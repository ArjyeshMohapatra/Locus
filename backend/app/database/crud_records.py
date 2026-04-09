# type: ignore

import os
from datetime import datetime
from typing import Any, cast

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import models


def _normalize_for_compare(path: str) -> str:
    normalized = os.path.abspath(os.path.normpath(path))
    if os.name == "nt":
        return os.path.normcase(normalized)
    return normalized


def _is_path_within_root(target_path: str | None, root_path: str) -> bool:
    if not target_path:
        return False
    try:
        normalized_target = _normalize_for_compare(target_path)
        normalized_root = _normalize_for_compare(root_path)
        return (
            os.path.commonpath([normalized_target, normalized_root]) == normalized_root
        )
    except (TypeError, ValueError):
        return False


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
def create_file_record(db: Session, path: str, content_hash: str | None = None):
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
    for record in records:
        if _is_path_within_root(record.current_path, old_dir):
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
        setattr(watched_path, "is_active", False)  # Deactivate old
    else:
        # Standard rename
        setattr(watched_path, "path", new_path)
        setattr(watched_path, "is_active", True)

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
        if event.src_path and _is_path_within_root(event.src_path, old_dir):
            event.src_path = swap_path_prefix(event.src_path, old_dir, new_dir)
            count += 1

        # Check dest_path
        if event.dest_path and _is_path_within_root(event.dest_path, old_dir):
            event.dest_path = swap_path_prefix(event.dest_path, old_dir, new_dir)
            count += 1

    return count


# --- Activity Logs ---
# logs user's activity
def log_activity(
    db: Session,
    activity_type: str,
    app: str,
    details: str | None = None,
):
    db_log = models.ActivityLog(
        activity_type=activity_type,
        app_name=app,
        details=details,
    )
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


# --- Settings ---
def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    setting = (
        db.query(models.KeyValueStore).filter(models.KeyValueStore.key == key).first()
    )
    if not setting:
        return default
    return setting.value


def set_setting(db: Session, key: str, value: str) -> models.KeyValueStore:
    setting = (
        db.query(models.KeyValueStore).filter(models.KeyValueStore.key == key).first()
    )
    if setting:
        setting.value = value
    else:
        setting = models.KeyValueStore(key=key, value=value)
        db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


# --- File Versions ---
# creates a version for a file in case file gets modified
def create_file_version(
    db: Session,
    original_path: str,
    storage_path: str,
    version_number: int,
    file_hash: str,
    file_size: int,
    file_record_id: int | None = None,
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


def get_latest_file_version(db: Session, original_path: str):
    versions = get_file_versions(db, original_path)
    if not versions:
        return None
    return versions[0]


def create_checkpoint_session(
    db: Session,
    watched_path: str,
    name: str,
    scope: str,
    items: list[dict[str, Any]],
):
    session = models.CheckpointSession(
        watched_path=watched_path,
        name=name,
        scope=scope,
        item_count=len(items),
    )
    db.add(session)
    db.flush()

    rows: list[models.CheckpointSessionItem] = []
    for item in items:
        row = models.CheckpointSessionItem(
            session_id=int(cast(Any, session).id),
            file_path=str(item["file_path"]),
            file_record_id=item.get("file_record_id"),
            file_version_id=int(item["file_version_id"]),
            file_hash=item.get("file_hash"),
            file_size_bytes=item.get("file_size_bytes"),
        )
        rows.append(row)

    if rows:
        db.add_all(rows)

    db.commit()
    db.refresh(session)
    return session


def list_checkpoint_sessions(
    db: Session,
    watched_path: str | None = None,
    limit: int = 100,
):
    query = db.query(models.CheckpointSession)
    if watched_path:
        query = query.filter(models.CheckpointSession.watched_path == watched_path)

    return (
        query.order_by(
            models.CheckpointSession.created_at.desc(),
            models.CheckpointSession.id.desc(),
        )
        .limit(limit)
        .all()
    )


def get_checkpoint_session(db: Session, session_id: int):
    return (
        db.query(models.CheckpointSession)
        .filter(models.CheckpointSession.id == session_id)
        .first()
    )


def rename_checkpoint_session(db: Session, session_id: int, new_name: str):
    session = get_checkpoint_session(db, session_id)
    if not session:
        return None

    session.name = new_name
    db.commit()
    db.refresh(session)
    return session


def get_checkpoint_session_items(db: Session, session_id: int):
    return (
        db.query(models.CheckpointSessionItem)
        .filter(models.CheckpointSessionItem.session_id == session_id)
        .order_by(models.CheckpointSessionItem.file_path.asc())
        .all()
    )


def get_all_storage_paths(db: Session):
    """Returns a list of all storage_path strings from file_versions table."""
    # We only need the storage_path column
    return [r[0] for r in db.query(models.FileVersion.storage_path).all()]


def storage_filename_exists(db: Session, storage_filename: str) -> bool:
    """Checks whether any file_version currently references a given storage filename."""
    if not storage_filename:
        return False

    normalized = storage_filename.strip()
    if not normalized:
        return False

    # Match either exact filename rows or absolute paths ending in the filename.
    return (
        db.query(models.FileVersion.id)
        .filter(
            or_(
                models.FileVersion.storage_path == normalized,
                models.FileVersion.storage_path.endswith(f"\\{normalized}"),
                models.FileVersion.storage_path.endswith(f"/{normalized}"),
            )
        )
        .first()
        is not None
    )


# --- Encrypted Activity Snapshots ---
def create_activity_snapshot(
    db: Session,
    encrypted_payload: str,
    fingerprint: str,
    captured_at: datetime | None = None,
):
    record = models.ActivitySnapshotRecord(
        encrypted_payload=encrypted_payload,
        fingerprint=fingerprint,
        captured_at=captured_at or datetime.now(),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_activity_snapshots(
    db: Session,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 1000,
):
    query = db.query(models.ActivitySnapshotRecord)
    if start_time is not None:
        query = query.filter(models.ActivitySnapshotRecord.captured_at >= start_time)
    if end_time is not None:
        query = query.filter(models.ActivitySnapshotRecord.captured_at <= end_time)
    return (
        query.order_by(models.ActivitySnapshotRecord.captured_at.desc())
        .limit(limit)
        .all()
    )


def delete_activity_snapshot(db: Session, snapshot_id: int) -> bool:
    deleted = (
        db.query(models.ActivitySnapshotRecord)
        .filter(models.ActivitySnapshotRecord.id == snapshot_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return bool(deleted)


def delete_activity_snapshots_before(db: Session, cutoff: datetime) -> int:
    deleted = (
        db.query(models.ActivitySnapshotRecord)
        .filter(models.ActivitySnapshotRecord.captured_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    return int(deleted)


def delete_all_activity_snapshots(db: Session) -> int:
    deleted = db.query(models.ActivitySnapshotRecord).delete(synchronize_session=False)
    db.commit()
    return int(deleted)


def get_activity_snapshot_by_id(db: Session, snapshot_id: int):
    return (
        db.query(models.ActivitySnapshotRecord)
        .filter(models.ActivitySnapshotRecord.id == snapshot_id)
        .first()
    )
