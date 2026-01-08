from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.database import models, crud
from app.monitor import monitor_service, register_restore_start
from app import storage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
import threading
import time
import uvicorn
import os
import gzip


# --- Database Setup ---
DATABASE_URL = "sqlite:///./locus.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    models.Base.metadata.create_all(bind=engine)


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


class FileRestore(BaseModel):
    version_id: int
    dest_path: str | None = None  # Optional, defaults to original path


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


@app.get("/")
def read_root():
    return {"status": "running", "service": "LOCUS Backend"}


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    # Simple check to ensure DB is reachable
    try:
        crud.get_watched_paths(db)
        return {"db": "connected", "background_service": "active"}
    except Exception as e:
        return {"db": "error", "error": str(e)}


# --- Watched Paths endpoints ---
@app.get("/files/watched")
def list_watched_paths(db: Session = Depends(get_db)):
    return crud.get_watched_paths(db)


@app.post("/files/watched")
def add_watched_path(path_data: PathCreate, db: Session = Depends(get_db)):
    try:
        path = crud.create_watched_path(db, path_data.path)
        # Trigger live update of the monitor service
        monitor_service.sync_watches()
        return path
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/files/events")
def get_file_events(limit: int = 50, db: Session = Depends(get_db)):
    events = crud.get_recent_file_events(db, limit)
    return events


# --- Activity Endpoints ---
@app.get("/activity/timeline")
def get_timeline(limit: int = 50, db: Session = Depends(get_db)):
    return crud.get_activity_timeline(db, limit)


# --- File Versioning & Restore ---
@app.get("/files/versions")
def list_file_versions(path: str, db: Session = Depends(get_db)):
    """List available versions for a specific file path"""
    return crud.get_file_versions(db, path)


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

    # Signal monitor to ignore the next update for this file
    register_restore_start(target_path)

    # 3. Restore
    # Security note: In a real app, validate target_path is within allowed dirs
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
def log_activity_manual(activity: ActivityCreate, db: Session = Depends(get_db)):
    return crud.log_activity(db, activity.type, activity.app, activity.details)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
