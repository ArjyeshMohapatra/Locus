from sqlalchemy.orm import Session
from . import models
from datetime import datetime
import os


# ---Watched Path ---
# creates path's needed to be watched
def create_watched_path(db: Session, path: str):
    db_path = models.WatchedPath(path=path)
    db.add(db_path)
    db.commit()
    db.refresh(
        db_path
    )  # don't use it if there's no need for accessing data immediately after saving
    return db_path


# get's all actively watched paths
def get_watched_paths(db: Session):
    return (
        db.query(models.WatchedPath).filter(models.WatchedPath.is_active == True).all()
    )


# set's is_active = False for watched path instead of hard deleting them in order to store previous history related to them
def delete_watched_path(db: Session, path_id: int):
    db_path = (
        db.query(models.WatchedPath).filter(models.WatchedPath.id == path_id).first()
    )
    if db_path:
        db_path.is_active = False  # Soft delete
        db.commit()
    return db_path


# finds an active path an dchanges its path string into new one
def update_watched_path(db: Session, old_path: str, new_path: str):
    """Update a watched path entry (e.g., when root is renamed)."""
    db_path = (
        db.query(models.WatchedPath)
        .filter(
            models.WatchedPath.path == old_path, models.WatchedPath.is_active == True
        )
        .first()
    )
    if db_path:
        db_path.path = new_path
        db.commit()
        db.refresh(db_path)
    return db_path


# --- File Events ---
# receives a dictionary of data and saves it to file events table
def create_file_event(db: Session, event_data: dict):
    # event_data should match FileEvent columns: event_type, src_path, etc.
    db_event = models.FileEvent(**event_data)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


# --- Backup Tasks ---
def enqueue_backup_task(db: Session, src_path: str):
    task = models.BackupTask(src_path=src_path, status="pending", attempts=0)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def has_pending_backup_task(db: Session, src_path: str) -> bool:
    return (
        db.query(models.BackupTask)
        .filter(
            models.BackupTask.src_path == src_path,
            models.BackupTask.status.in_(["pending", "processing"]),
        )
        .first()
        is not None
    )


def get_next_backup_task(db: Session):
    return (
        db.query(models.BackupTask)
        .filter(models.BackupTask.status == "pending")
        .order_by(models.BackupTask.created_at.asc())
        .first()
    )


def mark_backup_task_processing(db: Session, task: models.BackupTask):
    task.status = "processing"
    task.attempts += 1
    task.updated_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


def mark_backup_task_done(db: Session, task: models.BackupTask):
    task.status = "done"
    task.updated_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


def mark_backup_task_failed(db: Session, task: models.BackupTask, error: str):
    task.status = "failed"
    task.last_error = error
    task.updated_at = datetime.now()
    db.commit()
    db.refresh(task)
    return task


# populates recent 50 file activities
def get_recent_file_events(db: Session, limit: int = 50):
    return (
        db.query(models.FileEvent)
        .order_by(models.FileEvent.timestamp.desc())
        .limit(limit)
        .all()
    )


# --- File Records (Identity Tracking) ---
# get's a file's record from file_record table
def get_file_record(db: Session, path: str):
    return (
        db.query(models.FileRecord)
        .filter(models.FileRecord.current_path == path)
        .first()
    )


# tries to recover a record when a file appears in a new location
def _try_recover_file_record(db: Session, path: str, content_hash: str):
    # Heuristic: Same Content Hash AND Same Filename AND Old Record is "Missing"
    candidate_versions = (
        db.query(models.FileVersion)
        .filter(models.FileVersion.file_hash == content_hash)
        .all()
    )

    current_filename = os.path.basename(path)

    for version in candidate_versions:
        if not version.file_record:
            continue

        old_record = version.file_record
        old_path = old_record.current_path

        # Check 1: Filename match (avoids linking 'copy.txt' to 'original.txt' automatically)
        if os.path.basename(old_path) != current_filename:
            continue

        # Check 2: Is the old path gone?
        if os.path.exists(old_path):
            continue

        print(f"[Identity Recovery] Relinking {old_path} -> {path}")
        old_record.current_path = path
        db.commit()
        db.refresh(old_record)
        return old_record

    return None


# checks existance of a file and update the file_record table accordingly
def create_file_record(db: Session, path: str, content_hash: str = None):
    # 1. Normal Check: Does this path already have a record?
    existing = get_file_record(db, path)
    if existing:
        return existing

    # 2. Recovery Check: Did we lose this file from another location?
    if content_hash:
        recovered = _try_recover_file_record(db, path, content_hash)
        if recovered:
            return recovered

    # 3. Create New Record
    db_record = models.FileRecord(current_path=path)
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record


# updates a file's current path if it gets renamed or moved
def update_file_record_path(db: Session, old_path: str, new_path: str):
    db_record = get_file_record(db, old_path)
    if db_record:
        db_record.current_path = new_path
        db.commit()
        db.refresh(db_record)
    return db_record


# helper function to safely replace beginning of a path
def swap_path_prefix(current_path: str, old_prefix: str, new_prefix: str) -> str:
    relative_suffix = current_path[len(old_prefix) :]
    if relative_suffix.startswith(os.path.sep):
        return new_prefix + relative_suffix
    return os.path.join(new_prefix, relative_suffix)


# updates current path for all the files under a moved directory
def update_directory_records(db: Session, old_dir: str, new_dir: str):
    # Find all records starting with this watched directory's old_path
    records = (
        db.query(models.FileRecord)
        .filter(models.FileRecord.current_path.startswith(old_dir))
        .all()
    )

    count = 0
    # Ensure directory path ends with separator to avoid prefix matching false positives
    # e.g., "C:\Test" matching "C:\Testing"
    norm_old_dir = old_dir if old_dir.endswith(os.path.sep) else old_dir + os.path.sep

    for record in records:
        # Replace the prefix
        if record.current_path.startswith(norm_old_dir):
            record.current_path = swap_path_prefix(
                record.current_path, old_dir, new_dir
            )
            count += 1

    db.commit()
    return count


def relink_watched_path(db: Session, old_path: str, new_path: str):
    """
    Finds the old watched path, updates it, and bulk-updates all file records.
    Used for manual relinking (e.g. C: -> E: moves).
    """
    # 1. Update the Watched Folder Entry
    watched_path = (
        db.query(models.WatchedPath).filter(models.WatchedPath.path == old_path).first()
    )
    if not watched_path:
        return None  # Path not found

    # Check if new path is already watched (prevent duplicates)
    existing_new = (
        db.query(models.WatchedPath).filter(models.WatchedPath.path == new_path).first()
    )
    if existing_new:
        # Merge scenario: The user manually added E:\Test, so we have C:\Test (old) and E:\Test (new).
        # We want to move history from C: to E:, then delete C:.
        watched_path.is_active = False  # Deactivate old
    else:
        # Standard rename
        watched_path.path = new_path
        watched_path.is_active = True

    # 2. Bulk Update Every File Record Inside
    # Leverage existing logic but ensure correct prefix handling
    count_records = update_directory_records(db, old_path, new_path)

    # 3. Bulk Update History Logs (FileEvent)
    # This prevents UI from 404ing when querying historical items
    count_events = update_directory_events(db, old_path, new_path)

    db.commit()
    return {
        "status": "relinked",
        "files_updated": count_records,
        "events_updated": count_events,
    }


def update_directory_events(db: Session, old_dir: str, new_dir: str):
    """
    Updates historical file events to reflect a moved root directory.
    Replaces old_dir prefix with new_dir in src_path and dest_path.
    """
    count = 0

    # Get all events where EITHER src or dest starts with the old_dir
    events = (
        db.query(models.FileEvent)
        .filter(
            (models.FileEvent.src_path.startswith(old_dir))
            | (models.FileEvent.dest_path.startswith(old_dir))
        )
        .all()
    )

    for event in events:
        # Check src_path
        if event.src_path and event.src_path.startswith(old_dir):
            event.src_path = swap_path_prefix(event.src_path, old_dir, new_dir)
            count += 1

        # Check dest_path
        if event.dest_path and event.dest_path.startswith(old_dir):
            event.dest_path = swap_path_prefix(event.dest_path, old_dir, new_dir)
            count += 1

    return count


# --- Activity Logs ---
# logs user's activity
def log_activity(db: Session, type: str, app: str, details: str = None):
    db_log = models.ActivityLog(activity_type=type, app_name=app, details=details)
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


# fetches 100 of user's activity
def get_activity_timeline(db: Session, limit: int = 100):
    return (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.start_time.desc())
        .limit(limit)
        .all()
    )


# --- File Versions ---
# creates a version for a file in case file gets modified
def create_file_version(
    db: Session,
    original_path: str,
    storage_path: str,
    version_number: int,
    file_hash: str,
    file_size: int,
    file_record_id: int = None,
):
    db_version = models.FileVersion(
        original_path=original_path,
        storage_path=storage_path,
        version_number=version_number,
        file_hash=file_hash,
        file_size_bytes=file_size,
        file_record_id=file_record_id,
    )
    db.add(db_version)
    db.commit()
    db.refresh(db_version)
    return db_version


# fetches file version from db in order to be displayed
def get_file_versions(db: Session, original_path: str):
    # 1. Try to find the FileRecord for this path
    record = get_file_record(db, original_path)
    if record:
        # If we have a record, get all versions linked to this identity
        return (
            db.query(models.FileVersion)
            .filter(models.FileVersion.file_record_id == record.id)
            .order_by(models.FileVersion.version_number.desc())
            .all()
        )

    # 2. Fallback: Query by plain string path (for legacy data)
    return (
        db.query(models.FileVersion)
        .filter(models.FileVersion.original_path == original_path)
        .order_by(models.FileVersion.version_number.desc())
        .all()
    )


def get_all_storage_paths(db: Session):
    """Returns a list of all storage_path strings from file_versions table."""
    # We only need the storage_path column
    return [r[0] for r in db.query(models.FileVersion.storage_path).all()]
