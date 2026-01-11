from sqlalchemy.orm import Session
from . import models
from datetime import datetime
import os


# --- Watched Paths ---
def create_watched_path(db: Session, path: str):
    db_path = models.WatchedPath(path=path)
    db.add(db_path)
    db.commit()
    db.refresh(db_path)
    return db_path


def get_watched_paths(db: Session):
    return (
        db.query(models.WatchedPath).filter(models.WatchedPath.is_active == True).all()
    )


def delete_watched_path(db: Session, path_id: int):
    db_path = (
        db.query(models.WatchedPath).filter(models.WatchedPath.id == path_id).first()
    )
    if db_path:
        db_path.is_active = False  # Soft delete
        db.commit()
    return db_path


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
def create_file_event(db: Session, event_data: dict):
    # event_data should match FileEvent columns: event_type, src_path, etc.
    db_event = models.FileEvent(**event_data)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event


def get_recent_file_events(db: Session, limit: int = 50):
    return (
        db.query(models.FileEvent)
        .order_by(models.FileEvent.timestamp.desc())
        .limit(limit)
        .all()
    )


# --- File Records (Identity Tracking) ---
def get_file_record(db: Session, path: str):
    return (
        db.query(models.FileRecord)
        .filter(models.FileRecord.current_path == path)
        .first()
    )


def create_file_record(db: Session, path: str, content_hash: str = None):
    # 1. Normal Check: Does this path already have a record?
    existing = get_file_record(db, path)
    if existing:
        return existing

    # 2. Recovery Check: Did we lose this file from another location?
    # Heuristic: Same Content Hash AND Same Filename AND Old Record is "Missing"
    if content_hash:
        # Find ANY file version with this hash
        # (This might be slow if DB is huge, but fine for local tool)
        candidate_versions = (
            db.query(models.FileVersion)
            .filter(models.FileVersion.file_hash == content_hash)
            .all()
        )

        current_filename = os.path.basename(path)

        for version in candidate_versions:
            if version.file_record:
                old_record = version.file_record
                old_path = old_record.current_path

                # Check 1: Filename match (avoids linking 'copy.txt' to 'original.txt' automatically)
                if os.path.basename(old_path) == current_filename:
                    # Check 2: Is the old path gone?
                    if not os.path.exists(old_path):
                        print(f"[Identity Recovery] Relinking {old_path} -> {path}")
                        old_record.current_path = path
                        db.commit()
                        db.refresh(old_record)
                        return old_record

    # 3. Create New Record
    db_record = models.FileRecord(current_path=path)
    db.add(db_record)
    db.commit()
    db.refresh(db_record)
    return db_record


def update_file_record_path(db: Session, old_path: str, new_path: str):
    """
    Updates the current_path of a file record.
    Used when a file is renamed/moved.
    """
    db_record = get_file_record(db, old_path)
    if db_record:
        db_record.current_path = new_path
        db.commit()
        db.refresh(db_record)
    return db_record


def update_directory_records(db: Session, old_dir: str, new_dir: str):
    """
    Updates current_path for all files inside a moved directory.
    """
    # Find all records starting with this path
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
            relative_suffix = record.current_path[
                len(old_dir) :
            ]  # Use original length to slice
            # Ensure we join correctly
            if relative_suffix.startswith(os.path.sep):
                new_full_path = new_dir + relative_suffix
            else:
                new_full_path = os.path.join(new_dir, relative_suffix)

            record.current_path = new_full_path
            count += 1

    db.commit()
    return count


# --- Activity Logs ---
def log_activity(db: Session, type: str, app: str, details: str = None):
    db_log = models.ActivityLog(activity_type=type, app_name=app, details=details)
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


def get_activity_timeline(db: Session, limit: int = 100):
    return (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.start_time.desc())
        .limit(limit)
        .all()
    )


# --- File Versions ---
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
