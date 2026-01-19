from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from app.database import models, crud
from app.monitor import monitor_service, register_restore_start, _process_backup
from app import storage
from app import event_stream
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
import threading
import time
import uvicorn
import os
import gzip
import shutil  # Added shutil for file operations
import json


# --- Database Setup ---
DATABASE_URL = "sqlite:///./locus.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Initialize database tables (creates tables if missing).
def init_db():
    models.Base.metadata.create_all(bind=engine)


# FastAPI dependency that yields a scoped DB session.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Pydantic Models (Validation) ---
class PathCreate(BaseModel):
    path: str


class ActivityCreate(BaseModel):
    type: str  # 'app_focus', 'system', etc.
    app: str  # 'chrome.exe', 'code.exe'
    details: str | None = None


class PathInput(BaseModel):
    path: str


class PathRelink(BaseModel):
    old_path: str
    new_path: str
    move_files: bool | None = False  # New flag to request physical move


class FileRestore(BaseModel):
    version_id: int
    dest_path: str | None = None  # Optional, defaults to original path


class AdminProtectionToggle(BaseModel):
    enabled: bool


class TrackingExclusions(BaseModel):
    exclusions: list[str]


# --- Background Service Placeholders ---
def background_monitor_task():
    """
    Starts the persistent file monitor service.
    """
    print("Background service started...")
    monitor_service.start()

    # In a real app, we might want a periodic 'refresh' to check for new paths
    # or just let the API trigger updates.
    monitor_service.sync_watches()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor_service.stop()


def background_gc_task():
    """
    Runs Garbage Collection periodically to clean up unused storage files.
    """
    print("GC Service Started...")
    while True:
        try:
            # Run GC every 5 minutes (300 seconds) for demonstration
            # In production, this might be hourly or daily.
            time.sleep(300)

            print("[GC] Scheduled check running...")
            db = SessionLocal()
            try:
                # Get all 'active' storage paths from DB
                paths = crud.get_all_storage_paths(db)

                # We need just the filenames (e.g., "hash.gz") to match what's on disk
                active_filenames = {os.path.basename(p) for p in paths}

                # Run the cleanup
                storage.run_garbage_collection(active_filenames)
            finally:
                db.close()

        except Exception as e:
            print(f"[GC] Error in background task: {e}")
            time.sleep(60)  # Backoff on error


# --- Lifecycle Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB and start background threads
    print("LOCUS System Starting...")
    init_db()

    db = SessionLocal()
    try:
        enabled = crud.get_setting(db, "admin_protection_enabled", "false")
        if enabled == "true":
            ok, msg = storage.enable_admin_protection()
            if not ok:
                print(f"[Security] Admin protection failed: {msg}")

        raw_exclusions = crud.get_setting(db, "tracking_exclusions", "[]")
        try:
            parsed = json.loads(raw_exclusions or "[]")
            if isinstance(parsed, list):
                storage.set_custom_exclusions(parsed)
        except Exception as exc:
            print(f"[Settings] Failed to load exclusions: {exc}")
    finally:
        db.close()

    # Start background service in a separate thread (daemon=True so it dies with main process)
    monitor_thread = threading.Thread(target=background_monitor_task, daemon=True)
    monitor_thread.start()

    # Start GC thread
    gc_thread = threading.Thread(target=background_gc_task, daemon=True)
    gc_thread.start()

    yield

    # Shutdown
    print("LOCUS System Shutting down...")


app = FastAPI(title="LOCUS API", version="0.1.0", lifespan=lifespan)

# CORS middleware to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Snapshot Settings ---
INITIAL_SNAPSHOT_ENABLED = True
INITIAL_SNAPSHOT_BLOCKING = False
INITIAL_SNAPSHOT_SKIP_SYMLINKS = True
INITIAL_SNAPSHOT_FAIL_ON_UNREADABLE = False
SNAPSHOT_BATCH_SIZE = 200


@app.get("/")
# Basic service status endpoint.
def read_root():
    return {"status": "running", "service": "LOCUS Backend"}


@app.get("/health")
# Healthcheck endpoint to verify DB connectivity and background services.
def health_check(db: Session = Depends(get_db)):
    # Simple check to ensure DB is reachable
    try:
        db.execute(text("SELECT 1"))
        return {"db": "connected", "background_service": "active"}
    except Exception as e:
        return {"db": "error", "error": str(e)}


# --- Watched Paths endpoints ---
@app.get("/files/watched")
# List all currently watched folders.
def list_watched_paths(db: Session = Depends(get_db)):
    return crud.get_watched_paths(db)


@app.post("/files/watched")
# Add a new watched folder and refresh monitor watches.
def add_watched_path(path_data: PathCreate, db: Session = Depends(get_db)):
    try:
        path = crud.create_watched_path(db, path_data.path)
        # Trigger live update of the monitor service
        monitor_service.sync_watches()
        if INITIAL_SNAPSHOT_ENABLED:
            if INITIAL_SNAPSHOT_BLOCKING:
                _run_initial_snapshot(path_data.path)
            else:
                threading.Thread(
                    target=_run_initial_snapshot,
                    args=(path_data.path,),
                    daemon=True,
                ).start()
        return path
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _check_snapshot_file(file_path: str) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        if storage.is_excluded_path(file_path):
            return False, []
        if INITIAL_SNAPSHOT_SKIP_SYMLINKS and os.path.islink(file_path):
            errors.append(f"Skipped symlink: {file_path}")
            return False, errors
        if not os.access(file_path, os.R_OK):
            errors.append(f"Unreadable file: {file_path}")
            return False, errors
        return True, errors
    except Exception as e:
        errors.append(f"Error accessing {file_path}: {e}")
        return (not INITIAL_SNAPSHOT_FAIL_ON_UNREADABLE), errors


def _scan_snapshot_targets(root_path: str) -> tuple[list[str], list[str]]:
    files: list[str] = []
    errors: list[str] = []

    excluded_dirs = storage.get_all_excluded_dirs()

    for current_root, dirs, filenames in os.walk(root_path):
        dirs[:] = [d for d in dirs if d not in excluded_dirs]
        for name in filenames:
            full_path = os.path.join(current_root, name)
            if storage.is_excluded_path(full_path):
                continue
            include, errs = _check_snapshot_file(full_path)
            if errs:
                errors.extend(errs)
            if include:
                files.append(full_path)
    return files, errors


def _publish_snapshot_error(watched_path: str, message: str) -> None:
    event_stream.publish(
        {
            "type": "snapshot_error",
            "watched_path": watched_path,
            "message": message,
        }
    )


def _publish_snapshot_start(watched_path: str) -> None:
    event_stream.publish(
        {
            "type": "snapshot_start",
            "watched_path": watched_path,
        }
    )


def _publish_snapshot_progress(
    watched_path: str,
    total: int,
    processed: int,
    skipped: int,
    error_count: int,
    eta_seconds: int | None,
) -> None:
    event_stream.publish(
        {
            "type": "snapshot_progress",
            "watched_path": watched_path,
            "total": total,
            "processed": processed,
            "skipped": skipped,
            "error_count": error_count,
            "eta_seconds": eta_seconds,
        }
    )


def _process_snapshot_file(
    file_path: str,
    root_path: str,
    storage_subdir: str,
) -> None:
    storage.mirror_copy_file(file_path, root_path, storage_subdir)
    _process_backup(file_path)


def _process_snapshot_files(
    db: Session,
    job: models.SnapshotJob,
    root_path: str,
    storage_subdir: str,
    files: list[str],
    initial_errors: list[str],
) -> tuple[int, int, int, str | None]:
    start = time.time()
    processed = 0
    skipped = 0
    error_count = len(initial_errors)
    last_error = initial_errors[-1] if initial_errors else None

    for idx, file_path in enumerate(files, start=1):
        try:
            _process_snapshot_file(file_path, root_path, storage_subdir)
            processed += 1
        except Exception as e:
            skipped += 1
            error_count += 1
            last_error = str(e)
            _publish_snapshot_error(root_path, f"Failed {file_path}: {e}")

        if idx % SNAPSHOT_BATCH_SIZE == 0 or idx == len(files):
            elapsed = max(time.time() - start, 0.001)
            rate = processed / elapsed if processed else 0
            remaining = len(files) - processed
            eta = int(remaining / rate) if rate > 0 else None
            crud.update_snapshot_job_progress(
                db,
                job,
                processed=processed,
                skipped=skipped,
                error_count=error_count,
                last_error=last_error,
            )
            _publish_snapshot_progress(
                root_path,
                total=len(files),
                processed=processed,
                skipped=skipped,
                error_count=error_count,
                eta_seconds=eta,
            )

    return processed, skipped, error_count, last_error


def _run_initial_snapshot(root_path: str) -> None:
    if not os.path.exists(root_path):
        raise HTTPException(status_code=400, detail="Watched path does not exist")

    _publish_snapshot_start(root_path)
    storage_subdir = storage.storage_subdir_name(root_path)
    storage.ensure_snapshot_dir(storage_subdir)

    db = SessionLocal()
    try:
        job = crud.get_snapshot_job(db, root_path)
        if not job:
            job = crud.create_snapshot_job(db, root_path, storage_subdir)

        files, errors = _scan_snapshot_targets(root_path)
        crud.mark_snapshot_job_started(db, job, total_files=len(files))

        for err in errors:
            _publish_snapshot_error(root_path, err)

        processed, skipped, error_count, _last_error = _process_snapshot_files(
            db,
            job,
            root_path,
            storage_subdir,
            files,
            errors,
        )

        crud.mark_snapshot_job_done(db, job)
        event_stream.publish(
            {
                "type": "snapshot_complete",
                "watched_path": root_path,
                "total": len(files),
                "processed": processed,
                "skipped": skipped,
                "error_count": error_count,
            }
        )
    finally:
        db.close()


def _perform_physical_move(old_path: str, new_path: str):
    """
    Helper to move folder contents. Handles case where destination already exists
    by merging contents instead of nesting.
    """
    print(f"[Relink] Moving files from {old_path} to {new_path}")

    # Case 1: Destination does not exist -> Standard Rename
    if not os.path.exists(new_path):
        parent_dir = os.path.dirname(new_path)
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        shutil.move(old_path, new_path)
        return

    # Case 2: Destination exists -> Merge/Move Contents
    # User likely created the empty target folder already.
    # We should move CONTENTS of old_path into new_path
    # instead of moving old_path INTO new_path (creating a nested folder).
    for item in os.listdir(old_path):
        src = os.path.join(old_path, item)
        dst = os.path.join(new_path, item)
        if os.path.exists(dst):
            raise FileExistsError(
                f"Destination file {item} already exists in {new_path}"
            )
        shutil.move(src, dst)

    # Remove the now-empty source directory
    os.rmdir(old_path)


@app.post("/files/watched/relink")
def relink_folder(data: PathRelink, db: Session = Depends(get_db)):
    """
    Manually moves history from one path to another.
    Can also physically move files if move_files=True.
    """

    # 1. Validation Logic
    if data.move_files:
        if not os.path.exists(data.old_path):
            raise HTTPException(
                status_code=400, detail="Source folder not found! Cannot move files."
            )
    elif not os.path.exists(data.new_path):
        # If NOT moving (just updating DB), the new path MUST exist already.
        raise HTTPException(
            status_code=400,
            detail="New path does not exist on disk! Did you move the files yet?",
        )

    try:
        # A. Stop Watching
        monitor_service.handle_root_deletion(data.old_path)

        # B. Perform Physical Move (Optional)
        if data.move_files:
            try:
                _perform_physical_move(data.old_path, data.new_path)
            except Exception as e:
                # If move fails, we must abort DB update
                print(f"[Relink] Physical move failed: {e}")
                monitor_service.sync_watches()  # Restart watch on old path
                raise HTTPException(
                    status_code=500, detail=f"Failed to move files: {str(e)}"
                )

        # C. Update the Database
        result = crud.relink_watched_path(db, data.old_path, data.new_path)
        if not result:
            # This is weird if we just moved it... but maybe DB sync issue?
            raise HTTPException(
                status_code=404, detail="Old watched path not found in DB"
            )

        # D. Start watching the NEW location
        monitor_service.sync_watches()

        return result
    except Exception as e:
        monitor_service.sync_watches()  # safety
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/events")
# Return the most recent filesystem events recorded by the monitor.
def get_file_events(limit: int = 50, db: Session = Depends(get_db)):
    events = crud.get_recent_file_events(db, limit)
    return events


@app.get("/files/events/stream")
async def stream_file_events():
    async def event_generator():
        queue = event_stream.subscribe()
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            event_stream.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Activity Endpoints ---
@app.get("/activity/timeline")
# Return recent user activity logs.
def get_timeline(limit: int = 50, db: Session = Depends(get_db)):
    return crud.get_activity_timeline(db, limit)


# --- File Versioning & Restore ---
@app.get("/files/versions")
def list_file_versions(path: str, db: Session = Depends(get_db)):
    """List available versions for a specific file path"""
    versions = crud.get_file_versions(db, path)
    if not versions and os.path.exists(path):
        # Fallback/Recovery: If file exists but has no history,
        # try to link it using its current content (if valid).
        current_hash = storage.calculate_file_hash(path)
        if current_hash:
            crud.create_file_record(db, path, content_hash=current_hash)
            # Re-fetch
            versions = crud.get_file_versions(db, path)

    return versions


@app.get("/files/current-version")
def get_current_version(path: str, db: Session = Depends(get_db)):
    """Return which saved version matches the file currently on disk.

    Note: restoring a version does not create a new snapshot; instead the file's
    current content may match an older version.
    """
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    current_hash = storage.calculate_file_hash(path)
    if not current_hash:
        raise HTTPException(status_code=500, detail="Failed to hash file")

    # 1. Try to fetch versions via Identity (FileRecord) first
    # This ensures that if the file was moved/renamed, we still find its history
    versions = crud.get_file_versions(db, path)

    # 2. If no versions found via Identity, maybe the FileRecord is missing/stale?
    # Try to trigger a "check" or "recovery" by creating the record now?
    # If fetch failed, it means get_file_versions returned empty.
    # But wait, get_file_versions ALREADY looks up by FileRecord.
    # So if it returns empty, it means we have no history linked to this current path OR identity.

    if not versions:
        # Attempt active recovery: Create/Link the record now using the hash we just calculated
        # This fixes the "User updated path manually in UI but DB doesn't know this file is that old file" gap.
        crud.create_file_record(db, path, content_hash=current_hash)
        # Re-fetch
        versions = crud.get_file_versions(db, path)

    match = next((v for v in versions if v.file_hash == current_hash), None)
    return {
        "file_hash": current_hash,
        "matches_version": bool(match),
        "version_id": match.id if match else None,
        "version_number": match.version_number if match else None,
    }


@app.get("/files/versions/{version_id}/content")
def get_version_content(version_id: int, db: Session = Depends(get_db)):
    """Get the content of a specific file version"""
    version = (
        db.query(models.FileVersion).filter(models.FileVersion.id == version_id).first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    if not os.path.exists(version.storage_path):
        raise HTTPException(status_code=404, detail="Version file not found on disk")

    try:
        # Check if file is GZIP compressed (our new storage format)
        if str(version.storage_path).endswith(".gz"):
            # Decompress and read as text
            with gzip.open(version.storage_path, "rt", encoding="utf-8") as f:
                content = f.read()
        else:
            # Legacy format: read directly as text
            with open(version.storage_path, "r", encoding="utf-8") as f:
                content = f.read()
        return {"content": content, "type": "text"}
    except UnicodeDecodeError:
        # If it fails, it's likely binary
        return {"content": "[Binary file - preview not available]", "type": "binary"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.realpath(path)))


def _is_within_watched_paths(target_path: str, watched_paths: list[str]) -> bool:
    target_norm = _normalize_path(target_path)
    for watched in watched_paths:
        if not watched:
            continue
        watched_norm = _normalize_path(watched)
        try:
            common = os.path.commonpath([target_norm, watched_norm])
        except ValueError:
            # Different drive letters on Windows
            continue
        if common == watched_norm:
            return True
    return False


@app.post("/files/restore")
def restore_version(restore_data: FileRestore, db: Session = Depends(get_db)):
    """Restore a specific version of a file"""
    # 1. Get version info from DB
    version = (
        db.query(models.FileVersion)
        .filter(models.FileVersion.id == restore_data.version_id)
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # 2. Determine destination
    target_path = restore_data.dest_path or version.original_path
    if not target_path:
        raise HTTPException(status_code=400, detail="Missing destination path")

    if not os.path.isabs(target_path):
        raise HTTPException(status_code=400, detail="Destination path must be absolute")

    watched_paths = [p.path for p in crud.get_watched_paths(db)]
    if not _is_within_watched_paths(target_path, watched_paths):
        raise HTTPException(
            status_code=403,
            detail="Restore destination must be within a watched path",
        )

    # Signal monitor to ignore the next update for this file
    register_restore_start(target_path)

    # 3. Restore
    success = storage.restore_file_version(version.storage_path, target_path)
    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to restore file from storage"
        )

    return {
        "status": "restored",
        "path": target_path,
        "version": version.version_number,
    }


@app.post("/activity/log")
# Create an activity log entry (manual client-side logging).
def log_activity_manual(activity: ActivityCreate, db: Session = Depends(get_db)):
    return crud.log_activity(db, activity.type, activity.app, activity.details)


# --- Security Settings ---
@app.get("/settings/security")
def get_security_settings(db: Session = Depends(get_db)):
    enabled = crud.get_setting(db, "admin_protection_enabled", "false") == "true"
    return {
        "admin_protection_enabled": enabled,
        "is_admin": storage.is_admin_user(),
    }


@app.post("/settings/security")
def set_security_settings(
    payload: AdminProtectionToggle, db: Session = Depends(get_db)
):
    if payload.enabled:
        ok, msg = storage.enable_admin_protection()
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        crud.set_setting(db, "admin_protection_enabled", "true")
    else:
        ok, msg = storage.disable_admin_protection()
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        crud.set_setting(db, "admin_protection_enabled", "false")

    return {
        "admin_protection_enabled": payload.enabled,
        "message": "updated",
    }


@app.get("/settings/exclusions")
def get_tracking_exclusions():
    return {
        "excluded_directories": sorted(storage.DEFAULT_EXCLUDED_DIRS),
        "custom_exclusions": sorted(storage.CUSTOM_EXCLUDED_DIRS),
    }


@app.post("/settings/exclusions")
def set_tracking_exclusions(payload: TrackingExclusions, db: Session = Depends(get_db)):
    storage.set_custom_exclusions(payload.exclusions)
    crud.set_setting(db, "tracking_exclusions", json.dumps(payload.exclusions))
    return {
        "custom_exclusions": sorted(storage.CUSTOM_EXCLUDED_DIRS),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
