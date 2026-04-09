# type: ignore
# pyright: reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedImport=false
# pylint: disable=unused-import

import os
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import models
from . import crud_records as _crud_records

SQLITE_IN_CLAUSE_CHUNK_SIZE = 5000


def _watched_path_active_filter() -> Any:
    return getattr(models.WatchedPath, "is_active").is_(True)


def _normalize_for_compare(path: str) -> str:
    normalized = os.path.abspath(os.path.normpath(path))
    if os.name == "nt":
        return os.path.normcase(normalized)
    return normalized


# ---Watched Path ---
# creates path's needed to be watched
def create_watched_path(db: Session, path: str):
    existing = cast(
        Any,
        db.query(models.WatchedPath).filter(models.WatchedPath.path == path).first(),
    )
    if existing:
        if not bool(getattr(existing, "is_active", False)):
            setattr(existing, "is_active", True)
            db.commit()
            db.refresh(existing)
        return existing

    db_path = models.WatchedPath(path=path)
    db.add(db_path)
    db.commit()
    db.refresh(
        db_path
    )  # don't use it if there's no need for accessing data immediately after saving
    return db_path


# --- Snapshot Jobs ---
def create_snapshot_job(db: Session, watched_path: str, storage_subdir: str):
    job = models.SnapshotJob(
        watched_path=watched_path,
        storage_subdir=storage_subdir,
        status="pending",
        total_files=0,
        processed_files=0,
        skipped_files=0,
        error_count=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_snapshot_job(db: Session, watched_path: str):
    return (
        db.query(models.SnapshotJob)
        .filter(models.SnapshotJob.watched_path == watched_path)
        .first()
    )


def mark_snapshot_job_started(db: Session, job: Any, total_files: int):
    job.status = "in_progress"
    job.total_files = total_files
    job.updated_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job


def update_snapshot_job_progress(
    db: Session,
    job: Any,
    processed: int,
    skipped: int = 0,
    error_count: int = 0,
    last_error: str | None = None,
):
    job.processed_files = processed
    job.skipped_files = skipped
    job.error_count = error_count
    if last_error:
        job.last_error = last_error
    job.updated_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job


def mark_snapshot_job_done(db: Session, job: Any):
    job.status = "done"
    job.updated_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job


def mark_snapshot_job_failed(db: Session, job: Any, error: str):
    job.status = "failed"
    job.last_error = error
    job.updated_at = datetime.now()
    db.commit()
    db.refresh(job)
    return job


def is_snapshot_in_progress(db: Session, target_path: str) -> bool:
    active = (
        db.query(models.SnapshotJob)
        .filter(models.SnapshotJob.status == "in_progress")
        .all()
    )
    if not active:
        return False
    for job in active:
        if _is_path_within_root(target_path, str(cast(Any, job).watched_path)):
            return True
    return False


# get's all actively watched paths
def get_watched_paths(db: Session):
    return db.query(models.WatchedPath).filter(_watched_path_active_filter()).all()


# set's is_active = False for watched path instead of hard deleting them in order to store previous history related to them
def delete_watched_path(db: Session, path_id: int):
    db_path = (
        db.query(models.WatchedPath).filter(models.WatchedPath.id == path_id).first()
    )
    if db_path:
        setattr(db_path, "is_active", False)  # Soft delete
        db.commit()
    return db_path


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


def _delete_by_ids_in_chunks(
    query, column, ids: list[int], chunk_size: int = SQLITE_IN_CLAUSE_CHUNK_SIZE
) -> int:
    """Delete rows in bounded chunks to avoid SQLite variable-limit failures."""
    total_deleted = 0
    if not ids:
        return 0

    for i in range(0, len(ids), chunk_size):
        batch = ids[i : i + chunk_size]
        if not batch:
            continue
        total_deleted += int(
            query.filter(column.in_(batch)).delete(synchronize_session=False)
        )
    return total_deleted


def _delete_if_ids(query, column, ids: list[int]) -> int:
    if not ids:
        return 0
    return _delete_by_ids_in_chunks(query, column, ids)


def _collect_ids_within_root(
    rows: list[Any],
    root_path: str,
    path_fields: tuple[str, ...],
) -> list[int]:
    collected: list[int] = []
    for row in rows:
        row_id = int(getattr(row, "id", 0) or 0)
        if row_id <= 0:
            continue
        for field in path_fields:
            if _is_path_within_root(str(getattr(row, field, "") or ""), root_path):
                collected.append(row_id)
                break
    return collected


def _delete_versions_under_root(
    db: Session,
    root_path: str,
    file_record_ids: list[int],
) -> int:
    linked_versions_deleted = _delete_if_ids(
        db.query(models.FileVersion),
        models.FileVersion.file_record_id,
        file_record_ids,
    )

    legacy_versions = (
        db.query(models.FileVersion)
        .filter(models.FileVersion.original_path.startswith(root_path))
        .all()
    )
    legacy_version_ids = _collect_ids_within_root(
        legacy_versions,
        root_path,
        ("original_path",),
    )
    legacy_versions_deleted = _delete_if_ids(
        db.query(models.FileVersion),
        models.FileVersion.id,
        legacy_version_ids,
    )
    return linked_versions_deleted + legacy_versions_deleted


def _delete_events_under_root(db: Session, root_path: str) -> int:
    events = (
        db.query(models.FileEvent)
        .filter(
            or_(
                models.FileEvent.src_path.startswith(root_path),
                models.FileEvent.dest_path.startswith(root_path),
            )
        )
        .all()
    )
    event_ids = _collect_ids_within_root(events, root_path, ("src_path", "dest_path"))
    return _delete_if_ids(
        db.query(models.FileEvent),
        models.FileEvent.id,
        event_ids,
    )


def _delete_backup_tasks_under_root(db: Session, root_path: str) -> int:
    backup_tasks = (
        db.query(models.BackupTask)
        .filter(models.BackupTask.src_path.startswith(root_path))
        .all()
    )
    backup_task_ids = _collect_ids_within_root(
        backup_tasks,
        root_path,
        ("src_path",),
    )
    return _delete_if_ids(
        db.query(models.BackupTask),
        models.BackupTask.id,
        backup_task_ids,
    )


def remove_watched_path_and_tracked_data(
    db: Session, path_id: int
) -> dict[str, int | str] | None:
    watched = (
        db.query(models.WatchedPath).filter(models.WatchedPath.id == path_id).first()
    )
    if not watched:
        return None

    root_path = str(getattr(watched, "path", "") or "")

    checkpoint_sessions_deleted = (
        db.query(models.CheckpointSession)
        .filter(models.CheckpointSession.watched_path == root_path)
        .delete(synchronize_session=False)
    )

    file_records = (
        db.query(models.FileRecord)
        .filter(models.FileRecord.current_path.startswith(root_path))
        .all()
    )
    file_record_ids = _collect_ids_within_root(
        file_records,
        root_path,
        ("current_path",),
    )

    versions_deleted = _delete_versions_under_root(db, root_path, file_record_ids)
    events_deleted = _delete_events_under_root(db, root_path)
    backup_tasks_deleted = _delete_backup_tasks_under_root(db, root_path)

    snapshot_jobs_deleted = (
        db.query(models.SnapshotJob)
        .filter(models.SnapshotJob.watched_path == root_path)
        .delete(synchronize_session=False)
    )

    file_records_deleted = _delete_if_ids(
        db.query(models.FileRecord),
        models.FileRecord.id,
        file_record_ids,
    )

    setattr(watched, "is_active", False)
    db.commit()

    return {
        "status": "removed",
        "path_id": int(getattr(watched, "id", 0) or 0),
        "path": root_path,
        "file_records_deleted": file_records_deleted,
        "file_versions_deleted": versions_deleted,
        "file_events_deleted": events_deleted,
        "backup_tasks_deleted": backup_tasks_deleted,
        "snapshot_jobs_deleted": snapshot_jobs_deleted,
        "checkpoint_sessions_deleted": checkpoint_sessions_deleted,
    }


# finds an active path an dchanges its path string into new one
def update_watched_path(db: Session, old_path: str, new_path: str):
    """Update a watched path entry (e.g., when root is renamed)."""
    db_path = (
        db.query(models.WatchedPath)
        .filter(
            models.WatchedPath.path == old_path,
            _watched_path_active_filter(),
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


def create_file_events_bulk(db: Session, events_data: list[dict[str, str | None]]):
    """Insert multiple file events in one transaction and return persisted rows."""
    if not events_data:
        return []

    rows = [models.FileEvent(**event_data) for event_data in events_data]
    db.add_all(rows)
    db.commit()

    for row in rows:
        db.refresh(row)

    return rows


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


def fail_stale_processing_backup_tasks(
    db: Session, stale_after_seconds: int = 120
) -> int:
    cutoff = datetime.now() - timedelta(seconds=stale_after_seconds)
    stale_tasks = (
        db.query(models.BackupTask)
        .filter(
            models.BackupTask.status == "processing",
            models.BackupTask.updated_at.isnot(None),
            models.BackupTask.updated_at < cutoff,
        )
        .all()
    )

    if not stale_tasks:
        return 0

    now = datetime.now()
    for task in stale_tasks:
        task.status = "failed"
        if not task.last_error:
            task.last_error = (
                "Task interrupted during processing and marked failed on startup"
            )
        task.updated_at = now

    db.commit()
    return len(stale_tasks)


# populates recent 50 file activities
def get_recent_file_events(db: Session, limit: int = 50, path: str | None = None):
    query = db.query(models.FileEvent)
    if path:
        query = query.filter(
            or_(models.FileEvent.src_path == path, models.FileEvent.dest_path == path)
        )
    return query.order_by(models.FileEvent.timestamp.desc()).limit(limit).all()


# Re-export APIs moved to crud_records for backward compatibility.

create_activity_snapshot = _crud_records.create_activity_snapshot
create_checkpoint_session = _crud_records.create_checkpoint_session
create_file_record = _crud_records.create_file_record
create_file_version = _crud_records.create_file_version
delete_activity_snapshot = _crud_records.delete_activity_snapshot
delete_activity_snapshots_before = _crud_records.delete_activity_snapshots_before
delete_all_activity_snapshots = _crud_records.delete_all_activity_snapshots
get_activity_snapshot_by_id = _crud_records.get_activity_snapshot_by_id
get_activity_snapshots = _crud_records.get_activity_snapshots
get_activity_timeline = _crud_records.get_activity_timeline
get_all_storage_paths = _crud_records.get_all_storage_paths
get_checkpoint_session = _crud_records.get_checkpoint_session
get_checkpoint_session_items = _crud_records.get_checkpoint_session_items
get_file_record = _crud_records.get_file_record
get_file_versions = _crud_records.get_file_versions
get_latest_file_version = _crud_records.get_latest_file_version
get_setting = _crud_records.get_setting
list_checkpoint_sessions = _crud_records.list_checkpoint_sessions
log_activity = _crud_records.log_activity
relink_watched_path = _crud_records.relink_watched_path
rename_checkpoint_session = _crud_records.rename_checkpoint_session
set_setting = _crud_records.set_setting
storage_filename_exists = _crud_records.storage_filename_exists
swap_path_prefix = _crud_records.swap_path_prefix
update_directory_events = _crud_records.update_directory_events
update_directory_records = _crud_records.update_directory_records
update_file_record_path = _crud_records.update_file_record_path
