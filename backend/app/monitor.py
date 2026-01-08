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

        db: Session = SessionLocal()
        try:
            # Check existing versions to determine next version number
            # and to allow deduplication (hash check)
            versions = crud.get_file_versions(db, src_path)
            next_version = len(versions) + 1

            # Calculate current hash before full copy to see if we need it
            # (Optimization: We'll let save_file_version handle the copy,
            # but ideally we'd hash first. specific implementation in storage handles copy)

            # For now, just try to save. Storage could be smarter, but let's do it here.
            # If we want to dedup, we have to hash first.
            current_hash = storage.calculate_file_hash(src_path)
            if not current_hash:
                return  # File might have been deleted quickly

            if versions:
                last_version = versions[0]  # Ordered desc
                if last_version.file_hash == current_hash:
                    # Content hasn't changed (false positive modify event or simple touch)
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
            # For moved, we should ideally treat it as a new file (created) at dest
            # We backup the dest path
            self._backup_file(event.dest_path)


class FileMonitorService:
    def __init__(self):
        self.observer = Observer()
        self.active_watches = {}  # path -> watch_ref

    def start(self):
        """Starts the observer thread."""
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()

    def sync_watches(self):
        """
        Reads watched paths from DB and updates local observers.
        Called on startup and when user configuration changes.
        """
        db = SessionLocal()
        try:
            paths = crud.get_watched_paths(db)
            current_db_paths = {p.path for p in paths}

            # 1. Remove stale watches
            for path in self.active_watches.keys():
                if path not in current_db_paths:
                    self.observer.unschedule(self.active_watches[path])
                    del self.active_watches[path]
                    print(f"[-] Stopped watching: {path}")

            # 2. Add new watches
            for p in paths:
                if p.path not in self.active_watches:
                    try:
                        watch = self.observer.schedule(
                            LocusEventHandler(), p.path, recursive=True
                        )
                        self.active_watches[p.path] = watch
                        print(f"[+] Started watching: {p.path}")
                    except FileNotFoundError:
                        print(f"[!] Warning: Path not found {p.path}")
                    except Exception as e:
                        print(f"[!] Error watching {p.path}: {e}")

        finally:
            db.close()


# Global instance
monitor_service = FileMonitorService()
