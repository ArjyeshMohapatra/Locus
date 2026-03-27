# type: ignore

# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false

import time
import os
import threading
import queue
from watchdog.observers import Observer
from typing import Any, Protocol, cast
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.api import ObservedWatch
from sqlalchemy.orm import Session
from app.database import crud
from app.database.models import SessionLocal
from app import storage
from app import event_stream


import logging

logger = logging.getLogger("locus.monitor")


# Normalize a filesystem path for consistent comparisons and DB lookups.
def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


# Global map to track files currently being restored to avoid creating new versions.
# Using an expiry window makes this robust against multiple on_modified events.
PENDING_RESTORES: dict[str, float] = {}

IGNORED_SUFFIXES = (".tmp", ".crdownload", "~", ".swp")

# Debounce window to coalesce rapid successive events per file path.
BACKUP_DEBOUNCE_SECONDS = 0.4
LAST_BACKUP_TS: dict[str, float] = {}
_DEBOUNCE_STATE_LOCK = threading.RLock()
_DEBOUNCE_PURGE_INTERVAL_SECONDS = 60.0
_LAST_BACKUP_TTL_SECONDS = 60.0 * 60.0
_last_debounce_purge_ts = 0.0
EVENT_LOG_QUEUE_MAXSIZE = 10000
EVENT_LOG_BATCH_SIZE = 128
EVENT_LOG_FLUSH_SECONDS = 0.15


def _purge_debounce_state(now: float | None = None) -> None:
    """Bound growth of debounce maps by removing stale entries periodically."""
    global _last_debounce_purge_ts
    current = now if now is not None else time.time()

    # Avoid scanning maps on every event.
    if (current - _last_debounce_purge_ts) < _DEBOUNCE_PURGE_INTERVAL_SECONDS:
        return

    _last_debounce_purge_ts = current

    # Keep pending restores only while their restore window is active.
    expired_restore_keys = [
        path for path, expires_at in PENDING_RESTORES.items() if expires_at < current
    ]
    for path in expired_restore_keys:
        PENDING_RESTORES.pop(path, None)

    # Keep debounce timestamps for a bounded TTL to avoid unbounded dict growth.
    stale_backup_keys = [
        path
        for path, last_seen in LAST_BACKUP_TS.items()
        if (current - last_seen) > _LAST_BACKUP_TTL_SECONDS
    ]
    for path in stale_backup_keys:
        LAST_BACKUP_TS.pop(path, None)


def process_backup(src_path: str) -> None:
    if storage.is_excluded_path(src_path):
        return
    if src_path.endswith(IGNORED_SUFFIXES):
        return
    if not storage.should_backup_file(src_path):
        return

    # CHANGED: Calculate hash EARLY to help with identity recovery
    current_hash = storage.calculate_file_hash(src_path)
    if not current_hash:
        return  # File gone

    db: Session = SessionLocal()
    try:
        # IDENTITY TRACKING: Pass hash to recover history if path changed manually
        file_record = crud.create_file_record(db, src_path, content_hash=current_hash)

        # Check existing versions to determine next version number
        # and to allow deduplication (hash check)
        versions = crud.get_file_versions(db, src_path)

        # Deduplication Check: if hash matches any prior version AND the file still physically exists, skip
        if any(
            v.file_hash == current_hash and os.path.exists(str(v.storage_path))
            for v in versions
        ):
            return

        next_version = len(versions) + 1

        meta = storage.save_file_version(src_path, known_hash=current_hash)
        if meta:
            crud.create_file_version(
                db,
                src_path,
                meta["storage_path"],
                next_version,
                meta["file_hash"],
                meta["file_size"],
                file_record_id=(
                    int(cast(Any, file_record).id) if file_record is not None else None
                ),
            )
            print(f"[Backup] Saved version {next_version} of {src_path}")

    except Exception as e:
        print(f"[Error] Backup failed: {e}")
    finally:
        db.close()


def register_restore_start(file_path: str):
    """Mark a file path as being restored so the monitor ignores imminent changes."""
    # Restores often generate multiple modify events (truncate + write). Give it a window.
    now = time.time()
    with _DEBOUNCE_STATE_LOCK:
        _purge_debounce_state(now)
        PENDING_RESTORES[_norm_path(file_path)] = now + 2.0


class LocusEventHandler(FileSystemEventHandler):
    """
    Handles file system events and logs them to the SQLite database.
    Also handles file versioning (Shadow Copies).
    """

    # Initialize the handler; watchdog calls the event methods.
    def __init__(self):
        super().__init__()

    # Create a versioned backup of a file when its content changes.
    def _backup_file(self, src_path: str):
        normalized_src_path = _norm_path(src_path)
        now = time.time()

        with _DEBOUNCE_STATE_LOCK:
            _purge_debounce_state(now)

            last_seen = LAST_BACKUP_TS.get(normalized_src_path)
            if last_seen is not None and (now - last_seen) < BACKUP_DEBOUNCE_SECONDS:
                return
            LAST_BACKUP_TS[normalized_src_path] = now

            expires_at = PENDING_RESTORES.get(normalized_src_path)
            if expires_at is not None:
                if now <= expires_at:
                    print(
                        f"[Backup] Skipping version creation for restored file: {src_path}"
                    )
                    return
                PENDING_RESTORES.pop(normalized_src_path, None)

        if src_path.endswith(IGNORED_SUFFIXES):
            return
        db: Session = SessionLocal()
        try:
            if crud.is_snapshot_in_progress(db, src_path):
                return
            if not crud.has_pending_backup_task(db, src_path):
                crud.enqueue_backup_task(db, src_path)
        finally:
            db.close()

    # Persist a filesystem event (create/modify/delete/move) to the DB.
    def _log_event(
        self, event_type: str, src_path: str, dest_path: str | None = None
    ) -> None:
        if storage.is_excluded_path(src_path) or (
            dest_path and storage.is_excluded_path(dest_path)
        ):
            return
        if src_path.endswith(IGNORED_SUFFIXES):
            return  # Ignore temp files

        monitor_service.enqueue_fs_event(
            {
                "event_type": event_type,
                "src_path": src_path,
                "dest_path": dest_path,
            }
        )

    # Watchdog callback: file created.
    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._log_event("created", event.src_path)
            self._backup_file(event.src_path)
            return

        # Folder copied/moved into watched tree may not emit per-file move callbacks reliably.
        if event.src_path and os.path.exists(event.src_path):
            monitor_service.enqueue_directory_rescan(event.src_path)

    # Watchdog callback: file deleted.
    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._log_event("deleted", event.src_path)

    # Watchdog callback: file modified.
    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._log_event("modified", event.src_path)
            self._backup_file(event.src_path)

    # Watchdog callback: file or directory moved/renamed.
    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            if storage.is_excluded_path(event.src_path) or (
                event.dest_path and storage.is_excluded_path(event.dest_path)
            ):
                return
            self._log_event("moved", event.src_path, event.dest_path)

            # Update the identity record so history follows the file
            db = SessionLocal()
            try:
                crud.update_file_record_path(db, event.src_path, event.dest_path)
            except Exception as e:
                print(f"[Error] Failed to update file record on move: {e}")
            finally:
                db.close()

            # We backup the dest path
            self._backup_file(event.dest_path)
        else:
            # Handle directory move: Update paths for all contained files
            self._log_event("moved_dir", event.src_path, event.dest_path)
            print(f"[Monitor] Directory moved: {event.src_path} -> {event.dest_path}")

            db = SessionLocal()
            try:
                count = crud.update_directory_records(
                    db, event.src_path, event.dest_path
                )
                print(f"[Monitor] Updated {count} file records for directory move.")
            except Exception as e:
                print(f"[Error] Failed to update directory records: {e}")
            finally:
                db.close()

            if event.dest_path and os.path.exists(event.dest_path):
                monitor_service.enqueue_directory_rescan(event.dest_path)


class RootEventHandler(FileSystemEventHandler):
    """
    Watches the PARENT of a watched folder to detect if the watched folder itself is moved/renamed.
    """

    # Track the watched root so we can detect when the root itself moves/renames.
    def __init__(
        self,
        target_folder_name: str,
        full_target_path: str,
        monitor_service: "MonitorServiceProtocol",
    ):
        self.target_folder_name = target_folder_name
        self.full_target_path = full_target_path
        self.monitor_service = monitor_service

    # Watchdog callback: detect when the watched root folder is moved/renamed.
    def on_moved(self, event: FileSystemEvent) -> None:
        # DEBUG: Print all moves seen by root monitor
        if event.is_directory:
            print(
                f"[RootMonitor DEBUG] Moved: {event.src_path} -> {event.dest_path} | Target: {self.full_target_path}"
            )

        # We only care if the directory we are watching (src_path) matches our target
        # event.src_path is absolute.
        if event.is_directory and _norm_path(event.src_path) == _norm_path(
            self.full_target_path
        ):
            print(
                f"[RootMonitor] DETECTED ROOT RENAME: {event.src_path} -> {event.dest_path}"
            )

            # 1. Update DB (WatchedPath and FileRecords)
            db = SessionLocal()
            try:
                # Update the config
                crud.update_watched_path(db, self.full_target_path, event.dest_path)
                # Update all files inside
                count = crud.update_directory_records(
                    db, self.full_target_path, event.dest_path
                )
                events_count = crud.update_directory_events(
                    db, self.full_target_path, event.dest_path
                )
                db.commit()
                print(
                    f"[RootMonitor] Migrated {count} file records and {events_count} file events."
                )
            except Exception as e:
                print(f"[RootMonitor] Error updating DB: {e}")
            finally:
                db.close()

            # 2. Trigger reload in MonitorService
            # We run this in a seemingly async way or directly?
            # Direct call is fine, watchdog runs in a thread.
            self.monitor_service.handle_root_rename(
                self.full_target_path, event.dest_path
            )

    # Watchdog callback: detect when the watched root folder disappears.
    def on_deleted(self, event: FileSystemEvent) -> None:
        # If the root folder is deleted (or moved to another drive), we lose track.
        if event.is_directory and _norm_path(event.src_path) == _norm_path(
            self.full_target_path
        ):
            print(
                f"[RootMonitor] CRITICAL: Watched root folder disappeared: {event.src_path}"
            )
            # We can't auto-heal this because we don't know where it went (e.g. Moved to E:\ drive).
            # But we should probably mark it as 'missing' or stop the watcher to avoid errors.
            self.monitor_service.handle_root_deletion(self.full_target_path)


class MonitorServiceProtocol(Protocol):
    def handle_root_rename(self, old_path: str, new_path: str) -> None: ...

    def handle_root_deletion(self, path: str) -> None: ...


class FileMonitorService:
    # Manage watchdog observers and keep them in sync with DB configuration.
    def __init__(self):
        # Create the Observer only when starting, to ensure it is created
        # and started on the same thread (Windows thread handle safety).
        self.observer: Any = None
        self.active_watches: dict[str, ObservedWatch] = {}
        self.root_watches: dict[str, ObservedWatch] = {}

        # Ensure all schedule/unschedule calls happen on the monitor thread.
        self._cmd_queue: "queue.Queue[tuple[str, object | None]]" = queue.Queue()
        self._event_queue: "queue.Queue[dict[str, str | None]]" = queue.Queue(
            maxsize=EVENT_LOG_QUEUE_MAXSIZE
        )
        self._rescan_queue: "queue.Queue[str]" = queue.Queue()
        self._monitor_thread: threading.Thread | None = None
        self._queue_thread: threading.Thread | None = None
        self._event_thread: threading.Thread | None = None
        self._running = False
        self._state_lock = threading.RLock()

    def _enqueue_command(self, cmd: str, payload: object | None = None):
        """Queue a command for the monitor thread, starting it if needed."""
        with self._state_lock:
            needs_start = (
                not self._monitor_thread or not self._monitor_thread.is_alive()
            )
        if needs_start:
            self.start()
        self._cmd_queue.put((cmd, payload))

    def _monitor_loop(self):
        """Monitor thread: owns the Observer and processes commands."""
        try:
            self.observer = Observer()
            self.observer.start()

            while self._running:
                try:
                    cmd, payload = self._cmd_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                try:
                    should_continue = self._dispatch_command(cmd, payload)
                    if not should_continue:
                        break
                except Exception as e:
                    print(f"[MonitorThread] {cmd} error: {e}")
        finally:
            if self.observer:
                try:
                    self.observer.stop()
                    self.observer.join()
                except Exception as e:
                    logger.error(f"Observer stop error: {e}")
            self.observer = None
            with self._state_lock:
                self.active_watches.clear()
                self.root_watches.clear()

    def start(self):
        """Starts the observer thread."""
        with self._state_lock:
            if self._monitor_thread and self._monitor_thread.is_alive():
                return
            self._running = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self._monitor_thread.start()

            if not self._queue_thread or not self._queue_thread.is_alive():
                self._queue_thread = threading.Thread(
                    target=self._backup_queue_loop, daemon=True
                )
                self._queue_thread.start()

            if not self._event_thread or not self._event_thread.is_alive():
                self._event_thread = threading.Thread(
                    target=self._event_db_loop, daemon=True
                )
                self._event_thread.start()

    def enqueue_fs_event(self, event_data: dict[str, str | None]) -> None:
        if not self._running:
            self.start()
        try:
            self._event_queue.put_nowait(event_data)
        except queue.Full:
            # Drop oldest to keep ingestion non-blocking under burst load.
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._event_queue.put_nowait(event_data)
            except queue.Full:
                pass

    def enqueue_directory_rescan(self, directory_path: str) -> None:
        if not directory_path:
            return
        if not self._running:
            self.start()
        self._rescan_queue.put(directory_path)

    def _collect_event_batch(self) -> list[dict[str, str | None]]:
        pending: list[dict[str, str | None]] = []
        try:
            pending.append(self._event_queue.get(timeout=EVENT_LOG_FLUSH_SECONDS))
        except queue.Empty:
            return pending

        while len(pending) < EVENT_LOG_BATCH_SIZE:
            try:
                pending.append(self._event_queue.get_nowait())
            except queue.Empty:
                break

        return pending

    def _persist_event_batch(self, pending: list[dict[str, str | None]]) -> None:
        db: Session = SessionLocal()
        try:
            rows = crud.create_file_events_bulk(db, pending)
            for row in rows:
                row_ts = cast(Any, row).timestamp
                event_stream.publish(
                    {
                        "id": cast(Any, row).id,
                        "event_type": cast(Any, row).event_type,
                        "src_path": cast(Any, row).src_path,
                        "dest_path": cast(Any, row).dest_path,
                        "timestamp": row_ts.isoformat() if row_ts is not None else None,
                    }
                )
        except Exception as e:
            print(f"[Error] Failed to persist file events batch: {e}")
        finally:
            db.close()

    def _event_db_loop(self) -> None:
        while self._running:
            pending = self._collect_event_batch()
            if not pending:
                continue
            self._persist_event_batch(pending)

    def _enqueue_directory_tree_for_backup(self, directory_path: str) -> None:
        if not os.path.isdir(directory_path):
            return

        db: Session = SessionLocal()
        try:
            for root, _dirs, files in os.walk(directory_path):
                for filename in files:
                    src_path = os.path.join(root, filename)
                    if storage.is_excluded_path(src_path):
                        continue
                    if src_path.endswith(IGNORED_SUFFIXES):
                        continue
                    if not crud.has_pending_backup_task(db, src_path):
                        crud.enqueue_backup_task(db, src_path)
        finally:
            db.close()

    def _backup_queue_loop(self):
        while self._running:
            try:
                try:
                    rescan_path = self._rescan_queue.get_nowait()
                    self._enqueue_directory_tree_for_backup(rescan_path)
                except queue.Empty:
                    pass

                db = SessionLocal()
                had_task = False
                try:
                    task = crud.get_next_backup_task(db)
                    if task:
                        had_task = True
                        crud.mark_backup_task_processing(db, task)
                        try:
                            process_backup(str(cast(Any, task).src_path))
                            crud.mark_backup_task_done(db, task)
                        except Exception as e:
                            crud.mark_backup_task_failed(db, task, str(e))
                finally:
                    db.close()

                if not had_task:
                    time.sleep(0.2)
            except Exception as e:
                print(f"[BackupQueue] Loop error: {e}")
                time.sleep(0.5)

    def _dispatch_command(self, cmd: str, payload: object | None) -> bool:
        """Handle a queued command. Returns False to stop the loop."""
        if cmd == "sync":
            self._do_sync_watches()
            return True
        if cmd == "rename":
            if not isinstance(payload, tuple) or len(payload) != 2:
                return True
            old_path, _new_path = cast(tuple[str, str], payload)
            self._stop_watching_path(old_path)
            self._do_sync_watches()
            return True
        if cmd == "delete":
            if not isinstance(payload, str):
                return True
            path = payload
            self._stop_watching_path(path)
            self._do_sync_watches()
            return True
        if cmd == "stop":
            return False
        return True

    # Stop the observer thread and wait for it to exit.
    def stop(self):
        with self._state_lock:
            monitor_thread = self._monitor_thread
            queue_thread = self._queue_thread
            event_thread = self._event_thread
            if not monitor_thread:
                return
            self._running = False
        try:
            self._cmd_queue.put(("stop", None))
            monitor_thread.join(timeout=2.0)
            if queue_thread:
                queue_thread.join(timeout=2.0)
            if event_thread:
                event_thread.join(timeout=2.0)
        finally:
            with self._state_lock:
                self._monitor_thread = None
                self._queue_thread = None
                self._event_thread = None

    def handle_root_rename(self, old_path: str, new_path: str):
        """Called when a root folder is renamed."""
        print(f"[MonitorService] Handling root rename: {old_path} -> {new_path}")

        # Remove old watches and resync on the monitor thread
        self._enqueue_command("rename", (old_path, new_path))

    def handle_root_deletion(self, path: str):
        """Called when a root folder disappears (deleted or moved cross-volume)."""
        print(f"[MonitorService] Handling root deletion/loss: {path}")

        # Remove watches and resync on the monitor thread.
        self._enqueue_command("delete", path)

    def _unschedule_watch(self, watch_map: dict[str, ObservedWatch], key: str) -> None:
        """Safely unschedule a watchdog watch stored in a dict by key."""
        with self._state_lock:
            watch = watch_map.get(key)
        if not watch:
            return
        try:
            if self.observer:
                self.observer.unschedule(watch)
        finally:
            # Ensure we don't keep stale references even if unschedule errors
            with self._state_lock:
                watch_map.pop(key, None)

    def _stop_watching_path(self, path: str):
        """Stop both the recursive watch and its parent/root watch for a path."""
        self._unschedule_watch(self.active_watches, path)
        self._unschedule_watch(self.root_watches, path)
        print(f"[-] Stopped watching: {path}")

    def _ensure_root_watch(self, watched_path: str):
        """Ensure a non-recursive parent watch exists for a watched root."""
        with self._state_lock:
            if watched_path in self.root_watches:
                return

        parent_path = os.path.dirname(watched_path)
        folder_name = os.path.basename(watched_path)

        if not os.path.exists(parent_path):
            print(f"[!] Parent path also missing: {parent_path}")
            return

        if not self.observer:
            raise RuntimeError("Observer not started")
        root_handler = RootEventHandler(folder_name, watched_path, self)
        parent_watch = self.observer.schedule(
            root_handler, parent_path, recursive=False
        )
        with self._state_lock:
            self.root_watches[watched_path] = parent_watch
        print(f"[+] Root Monitor attached to parent: {parent_path}")

    def _ensure_recursive_watch(self, watched_path: str):
        """Ensure a recursive watch exists for the watched folder itself."""
        with self._state_lock:
            if watched_path in self.active_watches:
                return

        if not self.observer:
            raise RuntimeError("Observer not started")
        watch = self.observer.schedule(
            LocusEventHandler(), watched_path, recursive=True
        )
        with self._state_lock:
            self.active_watches[watched_path] = watch
        print(f"[+] Started watching: {watched_path}")

    def _do_sync_watches(self):
        """Internal sync logic; must run on monitor thread."""
        db = SessionLocal()
        try:
            paths = crud.get_watched_paths(db)
            current_db_paths = {str(cast(Any, p).path) for p in paths}

            # 1. Remove stale watches
            with self._state_lock:
                active_paths = tuple(self.active_watches.keys())
            for path in active_paths:
                if path not in current_db_paths:
                    self._stop_watching_path(path)

            # 2. Ensure watches exist for all configured paths
            for p in paths:
                watch_path = str(cast(Any, p).path)
                try:
                    self._ensure_root_watch(watch_path)
                except Exception as e:
                    print(f"[!] Error attaching root monitor for {watch_path}: {e}")

                try:
                    self._ensure_recursive_watch(watch_path)
                except FileNotFoundError:
                    print(
                        f"[!] Warning: Path not found {watch_path} (Waiting for it to appear...)"
                    )
                except Exception as e:
                    print(f"[!] Error watching {watch_path}: {e}")
        finally:
            db.close()

    def sync_watches(self):
        """Request a sync on the monitor thread."""
        self._enqueue_command("sync", None)


# Global instance
monitor_service = FileMonitorService()
