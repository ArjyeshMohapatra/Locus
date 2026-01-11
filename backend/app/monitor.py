import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sqlalchemy.orm import Session
from app.database import models, crud
from app.database.models import SessionLocal
from app import storage


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


# Global map to track files currently being restored to avoid creating new versions.
# Using an expiry window makes this robust against multiple on_modified events.
PENDING_RESTORES: dict[str, float] = {}


def register_restore_start(file_path: str):
    """Mark a file path as being restored so the monitor ignores imminent changes."""
    # Restores often generate multiple modify events (truncate + write). Give it a window.
    PENDING_RESTORES[_norm_path(file_path)] = time.time() + 2.0


class LocusEventHandler(FileSystemEventHandler):
    """
    Handles file system events and logs them to the SQLite database.
    Also handles file versioning (Shadow Copies).
    """

    def __init__(self):
        super().__init__()

    def _backup_file(self, src_path: str):
        normalized_src_path = _norm_path(src_path)
        expires_at = PENDING_RESTORES.get(normalized_src_path)
        if expires_at is not None:
            if time.time() <= expires_at:
                print(
                    f"[Backup] Skipping version creation for restored file: {src_path}"
                )
                return
            PENDING_RESTORES.pop(normalized_src_path, None)

        if src_path.endswith((".tmp", ".crdownload", "~", ".swp")):
            return

        # CHANGED: Calculate hash EARLY to help with identity recovery
        current_hash = storage.calculate_file_hash(src_path)
        if not current_hash:
            return  # File gone

        db: Session = SessionLocal()
        try:
            # IDENTITY TRACKING: Pass hash to recover history if path changed manually
            file_record = crud.create_file_record(
                db, src_path, content_hash=current_hash
            )

            # Check existing versions to determine next version number
            # and to allow deduplication (hash check)
            # Note: get_file_versions uses the identity (file_record), so if we recovered it,
            # we get the old history here!
            versions = crud.get_file_versions(db, src_path)
            next_version = len(versions) + 1

            # Deduplication Check
            if versions:
                last_version = versions[0]  # Ordered desc
                if last_version.file_hash == current_hash:
                    # Content hasn't changed
                    return

            meta = storage.save_file_version(src_path)
            if meta:
                crud.create_file_version(
                    db,
                    src_path,
                    meta["storage_path"],
                    next_version,
                    meta["file_hash"],
                    meta["file_size"],
                    file_record_id=file_record.id if file_record else None,
                )
                print(f"[Backup] Saved version {next_version} of {src_path}")

        except Exception as e:
            print(f"[Error] Backup failed: {e}")
        finally:
            db.close()

    def _log_event(self, event_type: str, src_path: str, dest_path: str = None):
        if src_path.endswith((".tmp", ".crdownload", "~", ".swp")):
            return  # Ignore temp files

        # We need a new session per event because this runs in a separate thread
        db: Session = SessionLocal()
        try:
            event_data = {
                "event_type": event_type,
                "src_path": src_path,
                "dest_path": dest_path,
            }
            crud.create_file_event(db, event_data)
            print(f"[Monitor] {event_type}: {src_path}")
        except Exception as e:
            print(f"[Error] Failed to log event: {e}")
        finally:
            db.close()

    def on_created(self, event):
        if not event.is_directory:
            self._log_event("created", event.src_path)
            self._backup_file(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._log_event("deleted", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._log_event("modified", event.src_path)
            self._backup_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
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


class RootEventHandler(FileSystemEventHandler):
    """
    Watches the PARENT of a watched folder to detect if the watched folder itself is moved/renamed.
    """

    def __init__(self, target_folder_name: str, full_target_path: str, monitor_service):
        self.target_folder_name = target_folder_name
        self.full_target_path = full_target_path
        self.monitor_service = monitor_service

    def on_moved(self, event):
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
                print(f"[RootMonitor] Migrated {count} file records.")
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

    def on_deleted(self, event):
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


class FileMonitorService:
    def __init__(self):
        self.observer = Observer()
        self.active_watches = {}  # path -> watch_ref
        self.root_watches = {}  # path -> watch_ref (for parent watchers)

    def start(self):
        """Starts the observer thread."""
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()

    def handle_root_rename(self, old_path: str, new_path: str):
        """Called when a root folder is renamed."""
        print(f"[MonitorService] Handling root rename: {old_path} -> {new_path}")

        # 1. Stop watching the old path
        if old_path in self.active_watches:
            self.observer.unschedule(self.active_watches[old_path])
            del self.active_watches[old_path]

        # 2. Stop watching the OLD parent (we will re-add the new parent in sync)
        if old_path in self.root_watches:
            self.observer.unschedule(self.root_watches[old_path])
            del self.root_watches[old_path]

        # 3. Trigger sync to pick up new configuration from DB
        self.sync_watches()

    def handle_root_deletion(self, path: str):
        """Called when a root folder disappears (deleted or moved cross-volume)."""
        print(f"[MonitorService] Handling root deletion/loss: {path}")
        # Stop watching it.
        if path in self.active_watches:
            self.observer.unschedule(self.active_watches[path])
            del self.active_watches[path]

        # Stop watching parent (since we can't find the child anymore)
        if path in self.root_watches:
            self.observer.unschedule(self.root_watches[path])
            del self.root_watches[path]

        # Optional: Mark as 'inactive' in DB?
        # For now, we just stop monitoring it to avoid errors.
        # The user will see it red in UI if we added status checks (future feature).

    def sync_watches(self):
        """
        Reads watched paths from DB and updates local observers.
        Called on startup and when user configuration changes.
        """
        db = SessionLocal()
        try:
            paths = crud.get_watched_paths(db)
            current_db_paths = {p.path for p in paths}

            # 1. Remove stale watches (Standard)
            for path in list(self.active_watches.keys()):
                if path not in current_db_paths:
                    self.observer.unschedule(self.active_watches[path])
                    del self.active_watches[path]

                    # Also remove associated root watcher if it exists
                    if path in self.root_watches:
                        self.observer.unschedule(self.root_watches[path])
                        del self.root_watches[path]

                    print(f"[-] Stopped watching: {path}")

            # 2. Add new watches
            for p in paths:
                # We always try to setup monitors, even if one part fails.

                # A. Parent Watch (Non-recursive) - Setup this first or independently
                # We do this even if the child doesn't exist, so we can detect if it's moved BACK or created
                if p.path not in self.root_watches:
                    try:
                        parent_path = os.path.dirname(p.path)
                        folder_name = os.path.basename(p.path)

                        if os.path.exists(parent_path):
                            root_handler = RootEventHandler(folder_name, p.path, self)
                            parent_watch = self.observer.schedule(
                                root_handler, parent_path, recursive=False
                            )
                            self.root_watches[p.path] = parent_watch
                            print(f"[+] Root Monitor attached to parent: {parent_path}")
                        else:
                            print(f"[!] Parent path also missing: {parent_path}")
                    except Exception as e:
                        print(f"[!] Error attaching root monitor for {p.path}: {e}")

                # B. Normal Recursive Watch (The Child)
                if p.path not in self.active_watches:
                    try:
                        watch = self.observer.schedule(
                            LocusEventHandler(), p.path, recursive=True
                        )
                        self.active_watches[p.path] = watch
                        print(f"[+] Started watching: {p.path}")
                    except FileNotFoundError:
                        print(
                            f"[!] Warning: Path not found {p.path} (Waiting for it to appear...)"
                        )
                        # Note: If it appears later, the Parent Monitor might catch a "Created" or "Moved" event
                        # matching this path and we could trigger a sync?
                        # Actually RootEventHandler currently only handles on_moved.
                        # We might needed on_created too to auto-start watching?
                        # For now, at least we don't crash.
                    except Exception as e:
                        print(f"[!] Error watching {p.path}: {e}")

        finally:
            db.close()


# Global instance
monitor_service = FileMonitorService()
