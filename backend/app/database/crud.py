from sqlalchemy.orm import Session
from . import models
from datetime import datetime


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
):
    db_version = models.FileVersion(
        original_path=original_path,
        storage_path=storage_path,
        version_number=version_number,
        file_hash=file_hash,
        file_size_bytes=file_size,
    )
    db.add(db_version)
    db.commit()
    db.refresh(db_version)
    return db_version


def get_file_versions(db: Session, original_path: str):
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
