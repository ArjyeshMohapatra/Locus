# type: ignore

from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from app.database import models, crud
from app.monitor import monitor_service, register_restore_start, process_backup
from app import storage
from app import event_stream
from app.snapshot_service import snapshot_service
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from typing import Annotated, Any, Awaitable, Callable, Literal
import threading
import time
import uvicorn
import os
import gzip
import asyncio
import socket
import shutil  # Added shutil for file operations
import json
import re
import uuid
import difflib
from datetime import datetime, timezone


import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("locus")

# --- Database Setup ---
DATABASE_URL = models.DATABASE_URL
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,
    },
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Improve SQLite concurrency characteristics for read-heavy + write-burst workloads."""
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Initialize database tables (creates tables if missing).
def init_db():
    models.Base.metadata.create_all(bind=engine)
    _run_startup_migrations()
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
    return any(str(row.get("name")) == column_name for row in rows)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name = :table_name LIMIT 1"
        ),
        {"table_name": table_name},
    ).first()
    return row is not None


def _run_startup_migrations() -> None:
    """Apply additive SQLite migrations and remove legacy schema artifacts."""
    migrations: list[tuple[str, str, str]] = [
        (
            "file_versions",
            "file_record_id",
            "ALTER TABLE file_versions ADD COLUMN file_record_id INTEGER",
        ),
        (
            "file_versions",
            "file_hash",
            "ALTER TABLE file_versions ADD COLUMN file_hash VARCHAR",
        ),
        (
            "file_versions",
            "file_size_bytes",
            "ALTER TABLE file_versions ADD COLUMN file_size_bytes BIGINT",
        ),
        (
            "watched_paths",
            "is_active",
            "ALTER TABLE watched_paths ADD COLUMN is_active BOOLEAN DEFAULT 1",
        ),
        (
            "backup_tasks",
            "attempts",
            "ALTER TABLE backup_tasks ADD COLUMN attempts INTEGER DEFAULT 0",
        ),
        (
            "backup_tasks",
            "last_error",
            "ALTER TABLE backup_tasks ADD COLUMN last_error TEXT",
        ),
        (
            "snapshot_jobs",
            "status",
            "ALTER TABLE snapshot_jobs ADD COLUMN status VARCHAR DEFAULT 'pending'",
        ),
        (
            "snapshot_jobs",
            "total_files",
            "ALTER TABLE snapshot_jobs ADD COLUMN total_files INTEGER DEFAULT 0",
        ),
        (
            "snapshot_jobs",
            "processed_files",
            "ALTER TABLE snapshot_jobs ADD COLUMN processed_files INTEGER DEFAULT 0",
        ),
        (
            "snapshot_jobs",
            "skipped_files",
            "ALTER TABLE snapshot_jobs ADD COLUMN skipped_files INTEGER DEFAULT 0",
        ),
        (
            "snapshot_jobs",
            "error_count",
            "ALTER TABLE snapshot_jobs ADD COLUMN error_count INTEGER DEFAULT 0",
        ),
        (
            "snapshot_jobs",
            "last_error",
            "ALTER TABLE snapshot_jobs ADD COLUMN last_error TEXT",
        ),
        (
            "activity_snapshot_records",
            "fingerprint",
            "ALTER TABLE activity_snapshot_records ADD COLUMN fingerprint VARCHAR",
        ),
    ]

    with engine.begin() as conn:
        for table_name, column_name, sql in migrations:
            try:
                if not _column_exists(conn, table_name, column_name):
                    conn.execute(text(sql))
                    print(f"[DB] Applied migration: {table_name}.{column_name}")
            except Exception as exc:
                print(f"[DB] Migration skipped for {table_name}.{column_name}: {exc}")

        legacy_tables = [
            "assistant_messages",
            "assistant_conversations",
            "conversation_messages",
            "conversation_threads",
            "snapshots",
        ]
        for table_name in legacy_tables:
            try:
                if _table_exists(conn, table_name):
                    conn.execute(text(f"DROP TABLE {table_name}"))
                    print(f"[DB] Dropped legacy table: {table_name}")
            except Exception as exc:
                print(f"[DB] Legacy table cleanup skipped for {table_name}: {exc}")

        try:
            conn.execute(
                text("DELETE FROM settings WHERE key = 'snapshot_nlp_always_on'")
            )
        except Exception as exc:
            print(
                f"[DB] Legacy setting cleanup skipped for snapshot_nlp_always_on: {exc}"
            )


# FastAPI dependency that yields a scoped DB session.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]
MIN_SNAPSHOT_UNLOCK_COMPAT_LENGTH = 4
MIN_SNAPSHOT_PASSPHRASE_LENGTH = 12
FILE_PREVIEW_SNIFF_BYTES = 8192
FILE_PREVIEW_MAX_BYTES = 1024 * 1024
BINARY_PREVIEW_TEXT = "[Binary file - preview not available]"
DEFAULT_API_PORT = 8000
API_PORT_SEARCH_LIMIT = 20
CHECKPOINT_SCOPE_SINGLE_FILE = "single_file"
CHECKPOINT_SCOPE_SELECTED_FILES = "selected_files"
CHECKPOINT_SCOPE_FULL_FOLDER = "full_folder"
CHECKPOINT_NAME_MAX_LENGTH = 80
CHECKPOINT_SESSION_NOT_FOUND_DETAIL = "Checkpoint session not found"
CHECKPOINT_DIFF_MAX_BYTES = 1024 * 1024
CHECKPOINT_DIFF_PREVIEW_LINES = 6
CHECKPOINT_DIFF_MAX_HUNKS = 8


def _build_cors_origins() -> list[str]:
    """Secure-by-default CORS policy for desktop EXE, with explicit dev opt-in."""
    configured = os.getenv("LOCUS_CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]

    env = os.getenv("LOCUS_ENV", "development").strip().lower()
    strict = os.getenv("LOCUS_STRICT_CORS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    origins = [
        "tauri://localhost",
        "https://tauri.localhost",
    ]
    # Keep localhost UI working by default. For packaged EXE hardening, set
    # LOCUS_ENV=production and LOCUS_STRICT_CORS=true.
    if not strict or env in {"dev", "development", "local"}:
        origins.extend(["http://localhost:5173", "http://127.0.0.1:5173"])
    return origins


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _pick_api_port(host: str, preferred_port: int) -> int:
    if _is_port_available(host, preferred_port):
        return preferred_port

    for offset in range(1, API_PORT_SEARCH_LIMIT + 1):
        candidate = preferred_port + offset
        if _is_port_available(host, candidate):
            return candidate

    raise RuntimeError(
        f"No available port found in range {preferred_port}-{preferred_port + API_PORT_SEARCH_LIMIT}"
    )


# --- Pydantic Models (Validation) ---
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def _validate_text_input(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if _CONTROL_CHARS_RE.search(cleaned):
        raise ValueError(f"{field_name} contains invalid control characters")
    return cleaned


class PathCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_text_input(value, "path")


class ActivityCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    type: str = Field(min_length=1, max_length=64)  # 'app_focus', 'system', etc.
    app: str = Field(min_length=1, max_length=256)  # 'chrome.exe', 'code.exe'
    details: str | None = Field(default=None, max_length=4000)

    @field_validator("type", "app")
    @classmethod
    def validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _validate_text_input(value, info.field_name or "field")

    @field_validator("details")
    @classmethod
    def validate_optional_details(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_text_input(value, "details")


class PathInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_text_input(value, "path")


class PathRelink(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    old_path: str = Field(min_length=1, max_length=4096)
    new_path: str = Field(min_length=1, max_length=4096)
    move_files: bool | None = False  # New flag to request physical move

    @field_validator("old_path", "new_path")
    @classmethod
    def validate_paths(cls, value: str, info: ValidationInfo) -> str:
        return _validate_text_input(value, info.field_name or "path")


class CheckpointCreatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    watched_path: str = Field(min_length=1, max_length=4096)
    scope: Literal[
        "single_file",
        "selected_files",
        "full_folder",
    ] = CHECKPOINT_SCOPE_FULL_FOLDER
    name: str | None = Field(default=None, max_length=CHECKPOINT_NAME_MAX_LENGTH)
    file_paths: list[str] = Field(default_factory=list, max_length=5000)

    @field_validator("watched_path")
    @classmethod
    def validate_watched_path(cls, value: str) -> str:
        return _validate_text_input(value, "watched_path")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _validate_text_input(value, "name")
        normalized = " ".join(cleaned.split())
        return normalized or None

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, values: list[str]) -> list[str]:
        validated: list[str] = []
        for value in values:
            validated.append(_validate_text_input(value, "file_paths"))
        return validated


class CheckpointRenamePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=CHECKPOINT_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = _validate_text_input(value, "name")
        normalized = " ".join(cleaned.split())
        if not normalized:
            raise ValueError("name cannot be empty")
        return normalized


class CheckpointDiffPayload(BaseModel):
    from_session_id: int = Field(ge=1)
    to_session_id: int = Field(ge=1)
    include_unchanged: bool = False


class CheckpointRestorePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    destination_root: str | None = Field(default=None, max_length=4096)
    conflict_strategy: Literal["rename", "overwrite", "skip"] = "rename"
    dry_run: bool = False

    @field_validator("destination_root")
    @classmethod
    def validate_destination_root(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_text_input(value, "destination_root")


class FileRestore(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    version_id: int
    dest_path: str | None = Field(
        default=None, max_length=4096
    )  # Optional, defaults to original path

    @field_validator("dest_path")
    @classmethod
    def validate_dest_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_text_input(value, "dest_path")


class AdminProtectionToggle(BaseModel):
    enabled: bool


class TrackingExclusions(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    exclusions: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("exclusions")
    @classmethod
    def validate_exclusions(cls, exclusions: list[str]) -> list[str]:
        validated: list[str] = []
        for exclusion in exclusions:
            validated.append(_validate_text_input(exclusion, "exclusions"))
        return validated


class SnapshotFeatureToggle(BaseModel):
    enabled: bool
    passphrase: str | None = Field(
        default=None,
        min_length=MIN_SNAPSHOT_UNLOCK_COMPAT_LENGTH,
        max_length=256,
    )


class SnapshotUnlockPayload(BaseModel):
    passphrase: str = Field(
        min_length=MIN_SNAPSHOT_UNLOCK_COMPAT_LENGTH, max_length=256
    )


class SnapshotResetPayload(BaseModel):
    pass


class SnapshotSettingsUpdate(BaseModel):
    interval_seconds: int | None = Field(default=None, ge=5, le=300)
    retention_days: int | None = Field(default=None, ge=1, le=365)
    exclude_private_browsing: bool | None = None
    capture_on_window_change: bool | None = None
    allow_individual_delete: bool | None = None


class RuntimeSettingsUpdate(BaseModel):
    run_in_background_service: bool | None = None


class SnapshotHistoryQueryPayload(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    app_name: str | None = Field(default=None, max_length=256)
    start_time: str | None = Field(default=None, max_length=64)
    end_time: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=200, ge=1, le=1000)


class SnapshotActionPayload(BaseModel):
    action_type: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=4096)


# --- Helper Functions ---
def _normalize_path(path: str) -> str:
    """Normalize a filesystem path for consistent comparisons and DB lookups.
    This involves following symlinks and making the path absolute."""
    try:
        return os.path.abspath(os.path.realpath(path))
    except Exception as e:
        # Fallback to just abspath if realpath fails (e.g. permission error)
        logger.warning(f"realpath failed for {path}: {e}. Falling back to abspath.")
        return os.path.abspath(path)


def _is_within_watched_paths(target_path: str, watched_paths: list[str]) -> bool:
    target_norm = _normalize_path(target_path)
    for watched in watched_paths:
        if not watched:
            continue
        watched_norm = _normalize_path(watched)
        try:
            common = os.path.commonpath([target_norm, watched_norm])
            if common == watched_norm:
                return True
        except ValueError:
            # Different drive letters on Windows
            continue
    return False


# --- Background Service Placeholders ---
def background_monitor_task(stop_event: threading.Event):
    """
    Starts the persistent file monitor service.
    """
    logger.info("Background monitor service started...")
    monitor_service.start()

    # In a real app, we might want a periodic 'refresh' to check for new paths
    # or just let the API trigger updates.
    monitor_service.sync_watches()

    try:
        stop_event.wait()
    finally:
        monitor_service.stop()
        logger.info("Background monitor service stopped.")


def _run_gc_cycle() -> None:
    logger.info("[GC] Scheduled check running...")
    db = SessionLocal()
    try:
        # Resolve activity at deletion time from DB to avoid stale snapshots and
        # avoid loading the full file_versions table into memory.
        storage.run_garbage_collection(
            lambda filename: crud.storage_filename_exists(db, filename)
        )
    except Exception as e:
        logger.error(f"[GC] Error during garbage collection: {e}", exc_info=True)
    finally:
        db.close()


def background_gc_task(stop_event: threading.Event):
    """
    Runs Garbage Collection periodically to clean up unused storage files.
    """
    logger.info("GC Service Started...")
    while not stop_event.is_set():
        # Run GC every 5 minutes (300 seconds). The wait call also supports fast shutdown.
        if stop_event.wait(300):
            break

        try:
            _run_gc_cycle()
        except Exception as e:
            # Never allow an unhandled exception to kill the GC thread silently.
            logger.error(f"[GC] Error in background task: {e}", exc_info=True)
            if stop_event.wait(60):
                break
    logger.info("GC Service Stopped.")


def _ensure_snapshot_defaults(db: Session) -> None:
    defaults = {
        "snapshot_enabled": "false",
        "snapshot_interval_seconds": "10",
        "snapshot_retention_days": "10",
        "snapshot_exclude_private_browsing": "true",
        "snapshot_capture_on_window_change": "true",
        "snapshot_allow_delete": "false",
        "run_in_background_service": "true",
    }
    for key, default_value in defaults.items():
        if crud.get_setting(db, key, None) is None:
            crud.set_setting(db, key, default_value)


# --- Lifecycle Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB and start background threads
    logger.info("LOCUS System Starting...")
    startup_started = time.perf_counter()

    t0 = time.perf_counter()
    init_db()
    logger.info("[Startup] init_db completed in %.2fs", time.perf_counter() - t0)

    db = SessionLocal()
    try:
        t0 = time.perf_counter()
        _ensure_snapshot_defaults(db)
        logger.info(
            "[Startup] snapshot defaults ensured in %.2fs",
            time.perf_counter() - t0,
        )

        enabled = crud.get_setting(db, "admin_protection_enabled", "false")
        if enabled == "true":
            ok, msg = storage.enable_admin_protection()
            if not ok:
                logger.error(f"[Security] Admin protection failed: {msg}")

        raw_exclusions = crud.get_setting(db, "tracking_exclusions", "[]")
        try:
            parsed = json.loads(raw_exclusions or "[]")
            if isinstance(parsed, list):
                normalized_exclusions = [
                    str(item) for item in parsed if isinstance(item, str)
                ]
                storage.set_custom_exclusions(normalized_exclusions)
        except Exception as exc:
            logger.error(f"[Settings] Failed to load exclusions: {exc}", exc_info=True)
    finally:
        db.close()

    stop_event = threading.Event()

    # Start background service thread.
    monitor_thread = threading.Thread(
        target=background_monitor_task,
        args=(stop_event,),
        daemon=True,
    )
    monitor_thread.start()
    logger.info("[Startup] monitor thread started")

    # Start GC thread
    gc_thread = threading.Thread(
        target=background_gc_task,
        args=(stop_event,),
        daemon=True,
    )
    gc_thread.start()
    logger.info("[Startup] GC thread started")

    snapshot_service.start()
    logger.info("[Startup] snapshot thread started")
    logger.info(
        "[Startup] lifespan startup completed in %.2fs",
        time.perf_counter() - startup_started,
    )

    yield

    # Shutdown
    stop_event.set()
    snapshot_service.stop()
    monitor_service.stop()
    monitor_thread.join(timeout=3.0)
    gc_thread.join(timeout=3.0)
    logger.info("LOCUS System Shutting down...")


app = FastAPI(title="LOCUS API", version="0.1.0", lifespan=lifespan)

# CORS middleware to allow frontend requests


@app.middleware("http")
async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # AUTHENTICATION GATING (Option B: API Lock)
    exempt_paths = {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/health",
        "/auth/status",
        "/auth/setup",
        "/auth/unlock",
        "/auth/lock",
        "/auth/reset",
    }

    if request.url.path not in exempt_paths and os.getenv("LOCUS_SKIP_AUTH") != "true":
        try:
            from app.database.models import SessionLocal

            with SessionLocal() as db:
                verifier = crud.get_setting(db, "snapshot_key_verifier", None)
                if not verifier:
                    return Response(status_code=401, content="Setup required")
                if not snapshot_service.is_unlocked():
                    return Response(status_code=401, content="Locked")
        except Exception as e:
            logger.error(f"[Middleware] Auth check failed: {e}", exc_info=True)
            # If the DB fails, we should return a 500 or 503, not necessarily "Setup required"
            # though 401 is safer to avoid leaking info. We'll keep 401 but log the error.
            return Response(status_code=401, content="Locked")

    response = await call_next(request)

    # Security headers
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault("Cache-Control", "no-store")

    if request.url.path not in {"/docs", "/redoc"} and not request.url.path.startswith(
        "/openapi"
    ):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; "
            "frame-ancestors 'none'; base-uri 'none'",
        )

    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# --- Snapshot Settings ---
INITIAL_SNAPSHOT_ENABLED = True
INITIAL_SNAPSHOT_BLOCKING = False
INITIAL_SNAPSHOT_SKIP_SYMLINKS = True
INITIAL_SNAPSHOT_FAIL_ON_UNREADABLE = False
SNAPSHOT_BATCH_SIZE = 200
SNAPSHOT_VAULT_LOCKED_DETAIL = "Snapshot vault is locked. Unlock with passphrase first."


@app.get("/")
# Basic service status endpoint.
def read_root():
    return {"status": "running", "service": "LOCUS Backend"}


@app.get("/health")
# Healthcheck endpoint to verify DB connectivity and background services.
def health_check(db: DbSession):
    # Simple check to ensure DB is reachable
    try:
        db.execute(text("SELECT 1"))
        return {"db": "connected", "background_service": "active"}
    except Exception as e:
        logger.error(f"Health check DB error: {e}", exc_info=True)
        return {"db": "error", "error": str(e)}


# --- Watched Paths endpoints ---
@app.get("/files/watched")
# List all currently watched folders.
def list_watched_paths(db: DbSession):
    return crud.get_watched_paths(db)


def _safe_scandir(path: str) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(path) as entries:
            return list(entries)
    except Exception as e:
        logger.warning(f"Failed to scandir {path}: {e}")
        return []


def _build_watched_tree_node(path: str) -> dict[str, object]:
    node: dict[str, object] = {
        "type": "dir",
        "name": os.path.basename(path.rstrip("\\/")) or path,
        "path": path,
        "children": [],
        "file_count": 0,
    }

    entries = _safe_scandir(path)
    entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))

    total_files = 0
    for entry in entries:
        child_path = entry.path

        if storage.is_excluded_path(child_path):
            continue

        if entry.is_dir(follow_symlinks=False):
            child_node = _build_watched_tree_node(child_path)
            node["children"].append(child_node)
            total_files += child_node["file_count"]
        elif entry.is_file(follow_symlinks=False):
            node["children"].append(
                {
                    "type": "file",
                    "name": entry.name,
                    "path": child_path,
                }
            )
            total_files += 1

    node["file_count"] = total_files
    return node


def _default_checkpoint_name(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    return f"Checkpoint {stamp}"


def _normalize_checkpoint_name(name: str | None) -> str:
    if not name:
        return _default_checkpoint_name()
    return " ".join(name.strip().split())


def _serialize_checkpoint_session(session: models.CheckpointSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "watched_path": session.watched_path,
        "name": session.name,
        "scope": session.scope,
        "item_count": session.item_count,
        "created_at": session.created_at,
    }


def _serialize_checkpoint_item(item: models.CheckpointSessionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "file_path": item.file_path,
        "file_record_id": item.file_record_id,
        "file_version_id": item.file_version_id,
        "file_hash": item.file_hash,
        "file_size_bytes": item.file_size_bytes,
        "created_at": item.created_at,
    }


def _dict_path(item: dict[str, Any]) -> str:
    return str(item.get("file_path") or "")


def _dict_hash(item: dict[str, Any], field: str) -> str:
    return str(item.get(field) or "").strip()


def _index_added_items_by_hash(
    added: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for item in added:
        item_hash = _dict_hash(item, "to_hash")
        if not item_hash:
            continue
        indexed.setdefault(item_hash, []).append(item)
    return indexed


def _detect_renames(
    added: list[dict[str, Any]],
    removed: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    added_by_hash = _index_added_items_by_hash(added)

    renamed: list[dict[str, Any]] = []
    renamed_added_paths: set[str] = set()
    renamed_removed_paths: set[str] = set()

    for removed_item in removed:
        item_hash = _dict_hash(removed_item, "from_hash")
        if not item_hash:
            continue
        candidates = added_by_hash.get(item_hash)
        if not candidates:
            continue

        candidate = candidates.pop(0)
        candidate_path = _dict_path(candidate)
        removed_path = _dict_path(removed_item)

        renamed_added_paths.add(candidate_path)
        renamed_removed_paths.add(removed_path)
        renamed.append(
            {
                "from_path": removed_path,
                "to_path": candidate_path,
                "file_hash": item_hash,
                "from_file_version_id": removed_item.get("from_file_version_id"),
                "to_file_version_id": candidate.get("to_file_version_id"),
            }
        )

    filtered_added = [
        item for item in added if _dict_path(item) not in renamed_added_paths
    ]
    filtered_removed = [
        item
        for item in removed
        if _dict_path(item) not in renamed_removed_paths
    ]

    return filtered_added, filtered_removed, renamed


def _diff_checkpoint_session_items(
    from_items: list[models.CheckpointSessionItem],
    to_items: list[models.CheckpointSessionItem],
    include_unchanged: bool,
) -> dict[str, Any]:
    from_by_path = {item.file_path: item for item in from_items}
    to_by_path = {item.file_path: item for item in to_items}

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []

    for path, to_item in to_by_path.items():
        from_item = from_by_path.get(path)
        if not from_item:
            added.append(
                {
                    "file_path": path,
                    "to_file_version_id": to_item.file_version_id,
                    "to_hash": to_item.file_hash,
                    "to_size_bytes": to_item.file_size_bytes,
                }
            )
            continue

        if from_item.file_version_id != to_item.file_version_id:
            modified.append(
                {
                    "file_path": path,
                    "from_file_version_id": from_item.file_version_id,
                    "to_file_version_id": to_item.file_version_id,
                    "from_hash": from_item.file_hash,
                    "to_hash": to_item.file_hash,
                    "from_size_bytes": from_item.file_size_bytes,
                    "to_size_bytes": to_item.file_size_bytes,
                }
            )
        elif include_unchanged:
            unchanged.append(
                {
                    "file_path": path,
                    "file_version_id": to_item.file_version_id,
                    "file_hash": to_item.file_hash,
                    "file_size_bytes": to_item.file_size_bytes,
                }
            )

    for path, from_item in from_by_path.items():
        if path in to_by_path:
            continue
        removed.append(
            {
                "file_path": path,
                "from_file_version_id": from_item.file_version_id,
                "from_hash": from_item.file_hash,
                "from_size_bytes": from_item.file_size_bytes,
            }
        )

    added, removed, renamed = _detect_renames(added, removed)

    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "renamed": len(renamed),
            "unchanged": len(unchanged),
        },
        "added": sorted(added, key=lambda item: str(item.get("file_path") or "")),
        "removed": sorted(
            removed,
            key=lambda item: str(item.get("file_path") or ""),
        ),
        "modified": sorted(
            modified,
            key=lambda item: str(item.get("file_path") or ""),
        ),
        "renamed": sorted(
            renamed,
            key=lambda item: f"{item.get('from_path','')}->{item.get('to_path','')}",
        ),
        "unchanged": sorted(
            unchanged,
            key=lambda item: str(item.get("file_path") or ""),
        )
        if include_unchanged
        else [],
    }


def _read_checkpoint_version_text(
    version: models.FileVersion,
) -> tuple[str | None, str | None]:
    storage_path = str(version.storage_path)
    if not os.path.exists(storage_path):
        return None, "stored_version_unavailable"

    try:
        if storage_path.endswith(".gz"):
            with gzip.open(storage_path, "rb") as f:
                content_bytes = f.read(CHECKPOINT_DIFF_MAX_BYTES + 1)
        else:
            with open(storage_path, "rb") as f:
                content_bytes = f.read(CHECKPOINT_DIFF_MAX_BYTES + 1)
    except Exception:
        return None, "failed_to_read_stored_version"

    if len(content_bytes) > CHECKPOINT_DIFF_MAX_BYTES:
        return None, "file_too_large_for_line_diff"

    if b"\x00" in content_bytes[:FILE_PREVIEW_SNIFF_BYTES]:
        return None, "binary_file"

    try:
        return content_bytes.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "binary_file"


def _build_checkpoint_line_diff(
    from_version: models.FileVersion | None,
    to_version: models.FileVersion | None,
) -> dict[str, Any]:
    if not from_version or not to_version:
        return {
            "available": False,
            "reason": "missing_file_versions",
            "added_lines": 0,
            "removed_lines": 0,
            "hunks": [],
        }

    from_text, from_error = _read_checkpoint_version_text(from_version)
    if from_error:
        return {
            "available": False,
            "reason": from_error,
            "added_lines": 0,
            "removed_lines": 0,
            "hunks": [],
        }

    to_text, to_error = _read_checkpoint_version_text(to_version)
    if to_error:
        return {
            "available": False,
            "reason": to_error,
            "added_lines": 0,
            "removed_lines": 0,
            "hunks": [],
        }

    from_lines = str(from_text or "").splitlines()
    to_lines = str(to_text or "").splitlines()

    added_lines = 0
    removed_lines = 0
    hunks: list[dict[str, Any]] = []

    matcher = difflib.SequenceMatcher(a=from_lines, b=to_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        if tag in {"replace", "insert"}:
            added_lines += j2 - j1
        if tag in {"replace", "delete"}:
            removed_lines += i2 - i1

        if len(hunks) >= CHECKPOINT_DIFF_MAX_HUNKS:
            continue

        hunks.append(
            {
                "tag": tag,
                "from_start": i1 + 1,
                "from_count": i2 - i1,
                "to_start": j1 + 1,
                "to_count": j2 - j1,
                "removed_preview": from_lines[i1 : i1 + CHECKPOINT_DIFF_PREVIEW_LINES],
                "added_preview": to_lines[j1 : j1 + CHECKPOINT_DIFF_PREVIEW_LINES],
            }
        )

    return {
        "available": True,
        "reason": None,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
        "hunks": hunks,
        "truncated_hunks": len(hunks) >= CHECKPOINT_DIFF_MAX_HUNKS,
    }


def _attach_checkpoint_line_diffs(db: Session, modified: list[dict[str, Any]]) -> None:
    if not modified:
        return

    version_ids: set[int] = set()
    for item in modified:
        from_version_id = item.get("from_file_version_id")
        to_version_id = item.get("to_file_version_id")
        if isinstance(from_version_id, int):
            version_ids.add(from_version_id)
        if isinstance(to_version_id, int):
            version_ids.add(to_version_id)

    if not version_ids:
        return

    versions = (
        db.query(models.FileVersion)
        .filter(models.FileVersion.id.in_(list(version_ids)))
        .all()
    )
    version_map = {int(version.id): version for version in versions}

    for item in modified:
        from_version = version_map.get(int(item.get("from_file_version_id") or 0))
        to_version = version_map.get(int(item.get("to_file_version_id") or 0))
        line_diff = _build_checkpoint_line_diff(from_version, to_version)
        item["line_diff"] = line_diff
        item["added_lines"] = int(line_diff.get("added_lines") or 0)
        item["removed_lines"] = int(line_diff.get("removed_lines") or 0)


def _checkpoint_diff_line_totals(modified: list[dict[str, Any]]) -> dict[str, int]:
    added = 0
    removed = 0
    for item in modified:
        added += int(item.get("added_lines") or 0)
        removed += int(item.get("removed_lines") or 0)
    return {
        "added_lines": added,
        "removed_lines": removed,
    }


def _build_restore_conflict_path(target_path: str) -> str:
    base, ext = os.path.splitext(target_path)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = f"{base}.restored-{stamp}{ext}"
    suffix = 1
    while os.path.exists(candidate):
        candidate = f"{base}.restored-{stamp}-{suffix}{ext}"
        suffix += 1
    return candidate


def _resolve_restore_relative_path(
    original_path: str,
    watched_path: str,
) -> tuple[str | None, str | None]:
    try:
        relative_path = os.path.relpath(original_path, watched_path)
    except ValueError:
        return None, "Path could not be mapped under checkpoint watched folder"

    if relative_path in {".", ""}:
        return None, "Invalid file path in checkpoint manifest"

    if relative_path == os.pardir or relative_path.startswith(f"{os.pardir}{os.sep}"):
        return None, "Path escapes checkpoint watched folder"

    return relative_path, None


def _resolve_restore_target_path(
    relative_path: str,
    destination_root: str,
) -> tuple[str | None, str | None]:
    target_path = os.path.normpath(os.path.abspath(os.path.join(destination_root, relative_path)))
    if not _is_within_watched_paths(target_path, [destination_root]):
        return None, "Resolved restore path escapes destination root"
    return target_path, None


def _resolve_conflict_action(
    target_path: str,
    conflict_strategy: str,
) -> tuple[str, str, dict[str, Any] | None]:
    if not os.path.exists(target_path):
        return "restore", target_path, None

    if conflict_strategy == "skip":
        action = "skip"
        resolved_target_path = target_path
    elif conflict_strategy == "rename":
        action = "rename"
        resolved_target_path = _build_restore_conflict_path(target_path)
    else:
        action = "overwrite"
        resolved_target_path = target_path

    return (
        action,
        resolved_target_path,
        {
            "existing_target_path": target_path,
            "resolved_target_path": resolved_target_path,
            "action": action,
        },
    )


def _build_checkpoint_restore_plan(
    items: list[models.CheckpointSessionItem],
    watched_path: str,
    destination_root: str,
    conflict_strategy: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    plan: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in items:
        original_path = os.path.normpath(os.path.abspath(str(item.file_path)))
        relative_path, relative_error = _resolve_restore_relative_path(
            original_path,
            watched_path,
        )
        if relative_error or not relative_path:
            skipped.append(
                {
                    "file_path": original_path,
                    "reason": relative_error or "Invalid checkpoint restore path",
                }
            )
            continue

        target_path, target_error = _resolve_restore_target_path(
            relative_path,
            destination_root,
        )
        if target_error or not target_path:
            skipped.append(
                {
                    "file_path": original_path,
                    "reason": target_error or "Invalid restore target path",
                }
            )
            continue

        action, resolved_target_path, conflict_entry = _resolve_conflict_action(
            target_path,
            conflict_strategy,
        )
        if conflict_entry:
            conflicts.append({"file_path": original_path, **conflict_entry})

        plan.append(
            {
                "session_item_id": item.id,
                "file_path": original_path,
                "file_version_id": item.file_version_id,
                "target_path": target_path,
                "resolved_target_path": resolved_target_path,
                "action": action,
            }
        )

    return plan, conflicts, skipped


def _load_restore_versions(
    db: Session,
    plan: list[dict[str, Any]],
) -> dict[int, models.FileVersion]:
    version_ids = [
        int(entry["file_version_id"])
        for entry in plan
        if str(entry.get("action")) != "skip"
    ]
    if not version_ids:
        return {}

    versions = (
        db.query(models.FileVersion)
        .filter(models.FileVersion.id.in_(version_ids))
        .all()
    )
    return {int(version.id): version for version in versions}


def _build_restore_skip_result(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_path": str(entry.get("file_path") or ""),
        "target_path": str(entry.get("target_path") or ""),
        "reason": "Skipped due to conflict strategy",
    }


def _build_restore_failure_result(entry: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "file_path": str(entry.get("file_path") or ""),
        "target_path": str(entry.get("resolved_target_path") or ""),
        "reason": reason,
    }


def _execute_restore_plan_entry(
    entry: dict[str, Any],
    version_map: dict[int, models.FileVersion],
) -> tuple[str, dict[str, Any]]:
    action = str(entry.get("action") or "")
    if action == "skip":
        return "skipped", _build_restore_skip_result(entry)

    version_id = int(entry["file_version_id"])
    version = version_map.get(version_id)
    if not version:
        return "failed", _build_restore_failure_result(
            entry,
            f"Missing file version {version_id}",
        )

    resolved_target_path = str(entry.get("resolved_target_path") or "")
    target_dir = os.path.dirname(resolved_target_path) or "."

    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        return "failed", _build_restore_failure_result(
            entry,
            f"Failed to create target directory: {exc}",
        )

    register_restore_start(resolved_target_path)
    if not storage.restore_file_version(str(version.storage_path), resolved_target_path):
        return "failed", _build_restore_failure_result(
            entry,
            "Failed to restore file from storage",
        )

    return (
        "restored",
        {
            "file_path": str(entry.get("file_path") or ""),
            "file_version_id": version_id,
            "target_path": resolved_target_path,
            "action": action,
        },
    )


def _execute_checkpoint_restore_plan(
    db: Session,
    plan: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    restored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    version_map = _load_restore_versions(db, plan)

    for entry in plan:
        status, result = _execute_restore_plan_entry(entry, version_map)
        if status == "restored":
            restored.append(result)
        elif status == "skipped":
            skipped.append(result)
        else:
            failed.append(result)

    return restored, skipped, failed


def _collect_full_scope_files(watched_path: str) -> list[str]:
    files, _errors = _scan_snapshot_targets(watched_path)
    return files


def _normalize_checkpoint_file_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        current = os.path.normpath(os.path.abspath(raw))
        if current in seen:
            continue
        seen.add(current)
        normalized.append(current)
    return normalized


def _resolve_checkpoint_target_files(
    payload: CheckpointCreatePayload,
    watched_path: str,
    db: Session,
) -> list[str]:
    if payload.scope == CHECKPOINT_SCOPE_FULL_FOLDER:
        return _collect_full_scope_files(watched_path)

    normalized = _normalize_checkpoint_file_paths(payload.file_paths)
    if payload.scope == CHECKPOINT_SCOPE_SINGLE_FILE and len(normalized) != 1:
        raise HTTPException(
            status_code=400,
            detail="single_file scope requires exactly one file path",
        )
    if payload.scope == CHECKPOINT_SCOPE_SELECTED_FILES and not normalized:
        raise HTTPException(
            status_code=400,
            detail="selected_files scope requires at least one file path",
        )

    validated: list[str] = []
    for file_path in normalized:
        if not os.path.isabs(file_path):
            raise HTTPException(status_code=400, detail="File path must be absolute")
        _assert_path_allowed(file_path, db)
        if not _is_within_watched_paths(file_path, [watched_path]):
            raise HTTPException(
                status_code=403,
                detail="File path must be within selected watched folder",
            )
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            raise HTTPException(
                status_code=400,
                detail=f"File not found on disk: {file_path}",
            )
        if storage.is_excluded_path(file_path):
            raise HTTPException(
                status_code=400,
                detail=f"Excluded file cannot be checkpointed: {file_path}",
            )
        validated.append(file_path)

    return validated


def _ensure_file_has_version(
    db: Session,
    file_path: str,
) -> tuple[models.FileVersion | None, int | None]:
    current_hash = storage.calculate_file_hash(file_path)
    if not current_hash:
        return None, None

    file_record = crud.create_file_record(db, file_path, content_hash=current_hash)
    versions = crud.get_file_versions(db, file_path)
    for version in versions:
        if version.file_hash == current_hash and os.path.exists(str(version.storage_path)):
            return version, file_record.id if file_record else None

    meta = storage.save_file_version(file_path, known_hash=current_hash)
    if not meta:
        return None, file_record.id if file_record else None

    version = crud.create_file_version(
        db,
        file_path,
        str(meta["storage_path"]),
        len(versions) + 1,
        str(meta["file_hash"]),
        int(meta["file_size"]),
        file_record_id=(file_record.id if file_record else None),
    )
    return version, file_record.id if file_record else None


def _build_checkpoint_items(
    db: Session,
    files: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for file_path in files:
        version, file_record_id = _ensure_file_has_version(db, file_path)
        if not version:
            skipped.append(
                {
                    "file_path": file_path,
                    "reason": "No version could be captured for file",
                }
            )
            continue

        items.append(
            {
                "file_path": file_path,
                "file_record_id": file_record_id,
                "file_version_id": version.id,
                "file_hash": version.file_hash,
                "file_size_bytes": version.file_size_bytes,
            }
        )

    return items, skipped


@app.get("/files/watched/tree")
def get_watched_tree(db: DbSession):
    roots = []
    for watched in crud.get_watched_paths(db):
        root_path = str(watched.path)
        exists = os.path.isdir(root_path)
        if exists:
            tree = _build_watched_tree_node(root_path)
        else:
            tree = {
                "type": "dir",
                "name": os.path.basename(root_path.rstrip("\\/")) or root_path,
                "path": root_path,
                "children": [],
                "file_count": 0,
            }

        roots.append(
            {
                "id": watched.id,
                "path": root_path,
                "is_active": bool(watched.is_active),
                "exists": exists,
                "tree": tree,
            }
        )

    return roots


@app.post(
    "/files/watched",
    responses={
        400: {"description": "Invalid watched path"},
    },
)
# Add a new watched folder and refresh monitor watches.
def add_watched_path(path_data: PathCreate, db: DbSession):
    try:
        normalized_path = os.path.normpath(os.path.abspath(path_data.path))

        existing_active = (
            db.query(models.WatchedPath)
            .filter(
                models.WatchedPath.path == normalized_path,
                models.WatchedPath.is_active,
            )
            .first()
        )
        if existing_active:
            monitor_service.sync_watches()
            return existing_active

        path = crud.create_watched_path(db, normalized_path)
        # Trigger live update of the monitor service
        monitor_service.sync_watches()
        if INITIAL_SNAPSHOT_ENABLED:
            if INITIAL_SNAPSHOT_BLOCKING:
                _run_initial_snapshot(normalized_path)
            else:
                threading.Thread(
                    target=_run_initial_snapshot,
                    args=(normalized_path,),
                    daemon=True,
                ).start()
        return path
    except Exception as e:
        logger.error(f"[API] add_watched_path failed: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid watched path")


@app.post(
    "/checkpoints/sessions",
    responses={
        400: {"description": "Invalid checkpoint request"},
        403: {"description": "Path must be within a watched folder"},
        404: {"description": "Watched folder not found"},
    },
)
def create_checkpoint_session(payload: CheckpointCreatePayload, db: DbSession):
    watched_path = os.path.normpath(os.path.abspath(payload.watched_path))

    watched = (
        db.query(models.WatchedPath)
        .filter(
            models.WatchedPath.path == watched_path,
            models.WatchedPath.is_active,
        )
        .first()
    )
    if not watched:
        raise HTTPException(status_code=404, detail="Watched folder not found")

    target_files = _resolve_checkpoint_target_files(payload, watched_path, db)
    if not target_files:
        raise HTTPException(
            status_code=400,
            detail="No eligible files found for checkpoint scope",
        )

    items, skipped = _build_checkpoint_items(db, target_files)
    if not items:
        raise HTTPException(
            status_code=400,
            detail="Checkpoint captured no versions. Files may be excluded or oversized.",
        )

    session = crud.create_checkpoint_session(
        db,
        watched_path=watched_path,
        name=_normalize_checkpoint_name(payload.name),
        scope=payload.scope,
        items=items,
    )

    return {
        **_serialize_checkpoint_session(session),
        "captured_count": len(items),
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


@app.get(
    "/checkpoints/sessions",
    responses={
        422: {"description": "Invalid path input"},
    },
)
def list_checkpoint_sessions(
    db: DbSession,
    watched_path: Annotated[str | None, Query(max_length=4096)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    normalized_watched_path: str | None = None
    if watched_path is not None:
        try:
            normalized_watched_path = os.path.normpath(
                os.path.abspath(_validate_text_input(watched_path, "watched_path"))
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    sessions = crud.list_checkpoint_sessions(
        db,
        watched_path=normalized_watched_path,
        limit=limit,
    )
    return [_serialize_checkpoint_session(session) for session in sessions]


@app.get(
    "/checkpoints/sessions/{session_id}",
    responses={
        404: {"description": CHECKPOINT_SESSION_NOT_FOUND_DETAIL},
    },
)
def get_checkpoint_session_detail(session_id: int, db: DbSession):
    session = crud.get_checkpoint_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail=CHECKPOINT_SESSION_NOT_FOUND_DETAIL)

    items = crud.get_checkpoint_session_items(db, session_id)
    return {
        **_serialize_checkpoint_session(session),
        "items": [_serialize_checkpoint_item(item) for item in items],
    }


@app.patch(
    "/checkpoints/sessions/{session_id}",
    responses={
        404: {"description": CHECKPOINT_SESSION_NOT_FOUND_DETAIL},
    },
)
def rename_checkpoint_session(
    session_id: int,
    payload: CheckpointRenamePayload,
    db: DbSession,
):
    updated = crud.rename_checkpoint_session(
        db,
        session_id=session_id,
        new_name=_normalize_checkpoint_name(payload.name),
    )
    if not updated:
        raise HTTPException(status_code=404, detail=CHECKPOINT_SESSION_NOT_FOUND_DETAIL)

    return _serialize_checkpoint_session(updated)


@app.post(
    "/checkpoints/sessions/diff",
    responses={
        400: {"description": "Cannot diff sessions from different watched folders"},
        404: {"description": CHECKPOINT_SESSION_NOT_FOUND_DETAIL},
    },
)
def diff_checkpoint_sessions(payload: CheckpointDiffPayload, db: DbSession):
    from_session = crud.get_checkpoint_session(db, payload.from_session_id)
    if not from_session:
        raise HTTPException(status_code=404, detail="from_session not found")

    to_session = crud.get_checkpoint_session(db, payload.to_session_id)
    if not to_session:
        raise HTTPException(status_code=404, detail="to_session not found")

    if str(from_session.watched_path) != str(to_session.watched_path):
        raise HTTPException(
            status_code=400,
            detail="Checkpoint sessions must belong to the same watched folder",
        )

    from_items = crud.get_checkpoint_session_items(db, payload.from_session_id)
    to_items = crud.get_checkpoint_session_items(db, payload.to_session_id)

    diff = _diff_checkpoint_session_items(
        from_items,
        to_items,
        include_unchanged=payload.include_unchanged,
    )
    modified = diff.get("modified") or []
    if isinstance(modified, list):
        _attach_checkpoint_line_diffs(db, modified)
        if isinstance(diff.get("summary"), dict):
            diff["summary"] = {
                **diff["summary"],
                **_checkpoint_diff_line_totals(modified),
            }

    return {
        "from_session": _serialize_checkpoint_session(from_session),
        "to_session": _serialize_checkpoint_session(to_session),
        **diff,
    }


@app.post(
    "/checkpoints/sessions/{session_id}/restore",
    responses={
        400: {"description": "Invalid restore plan"},
        403: {"description": "Destination must be within checkpoint watched folder"},
        404: {"description": CHECKPOINT_SESSION_NOT_FOUND_DETAIL},
    },
)
def restore_checkpoint_session(
    session_id: int,
    payload: CheckpointRestorePayload,
    db: DbSession,
):
    session = crud.get_checkpoint_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail=CHECKPOINT_SESSION_NOT_FOUND_DETAIL)

    items = crud.get_checkpoint_session_items(db, session_id)
    if not items:
        raise HTTPException(status_code=400, detail="Checkpoint session has no items")

    watched_path = os.path.normpath(os.path.abspath(str(session.watched_path)))
    destination_root_raw = payload.destination_root or watched_path
    destination_root = os.path.normpath(os.path.abspath(destination_root_raw))

    _assert_path_allowed(destination_root, db)
    if not _is_within_watched_paths(destination_root, [watched_path]):
        raise HTTPException(
            status_code=403,
            detail="Destination must be within checkpoint watched folder",
        )

    plan, conflicts, pre_skipped = _build_checkpoint_restore_plan(
        items,
        watched_path=watched_path,
        destination_root=destination_root,
        conflict_strategy=payload.conflict_strategy,
    )

    if not plan and pre_skipped:
        raise HTTPException(
            status_code=400,
            detail="No restorable files found for this destination",
        )

    if payload.dry_run:
        would_restore = len([entry for entry in plan if str(entry.get("action")) != "skip"])
        return {
            "dry_run": True,
            "session": _serialize_checkpoint_session(session),
            "destination_root": destination_root,
            "conflict_strategy": payload.conflict_strategy,
            "summary": {
                "total_manifest_items": len(items),
                "planned": len(plan),
                "would_restore": would_restore,
                "conflicts": len(conflicts),
                "skipped": len(pre_skipped),
            },
            "plan": plan,
            "conflicts": conflicts,
            "skipped": pre_skipped,
        }

    restored, runtime_skipped, failed = _execute_checkpoint_restore_plan(db, plan)
    all_skipped = [*pre_skipped, *runtime_skipped]

    return {
        "dry_run": False,
        "session": _serialize_checkpoint_session(session),
        "destination_root": destination_root,
        "conflict_strategy": payload.conflict_strategy,
        "summary": {
            "total_manifest_items": len(items),
            "planned": len(plan),
            "restored": len(restored),
            "conflicts": len(conflicts),
            "skipped": len(all_skipped),
            "failed": len(failed),
        },
        "conflicts": conflicts,
        "restored": restored,
        "skipped": all_skipped,
        "failed": failed,
    }


@app.delete(
    "/files/watched/{path_id}",
    responses={
        404: {"description": "Watched path not found"},
        500: {"description": "Internal server error"},
    },
)
def remove_watched_path(path_id: int, db: DbSession):
    watched = (
        db.query(models.WatchedPath)
        .filter(models.WatchedPath.id == path_id, models.WatchedPath.is_active)
        .first()
    )
    if not watched:
        raise HTTPException(status_code=404, detail="Watched path not found")

    watched_path = str(watched.path)

    try:
        monitor_service.handle_root_deletion(watched_path)
        result: dict[str, int | str] | None = crud.remove_watched_path_and_tracked_data(
            db, path_id
        )
        if not result:
            raise HTTPException(status_code=404, detail="Watched path not found")
        monitor_service.sync_watches()
        return result
    except HTTPException:
        monitor_service.sync_watches()
        raise
    except Exception as e:
        monitor_service.sync_watches()
        logger.error(f"[API] remove_watched_path failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


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
    process_backup(file_path)


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
            logger.error(
                f"Snapshot file processing failed for {file_path}: {e}", exc_info=True
            )

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
        logger.error(
            f"Initial snapshot failed: Watched path does not exist: {root_path}"
        )
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
            logger.warning(f"Snapshot pre-scan error for {root_path}: {err}")

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
        logger.info(
            f"Initial snapshot for {root_path} completed. Processed: {processed}, Skipped: {skipped}, Errors: {error_count}"
        )
    except Exception as e:
        logger.error(
            f"Error during initial snapshot for {root_path}: {e}", exc_info=True
        )
        _publish_snapshot_error(root_path, f"Internal error during snapshot: {e}")
    finally:
        db.close()


def _assert_same_volume_move(old_path: str, new_path: str) -> None:
    old_drive = os.path.splitdrive(os.path.abspath(old_path))[0].lower()
    new_drive = os.path.splitdrive(os.path.abspath(new_path))[0].lower()
    if old_drive and new_drive and old_drive != new_drive:
        raise RuntimeError(
            "Cross-volume physical relink is not supported safely. Move files manually, then relink DB paths."
        )


def _prepare_staging_dir(old_path: str) -> str:
    staging_parent = os.path.dirname(old_path) or old_path
    staging_path = os.path.join(
        staging_parent, f".locus-relink-staging-{uuid.uuid4().hex}"
    )
    os.replace(old_path, staging_path)
    return staging_path


def _preflight_merge_conflicts(staging_path: str, new_path: str) -> None:
    for root, _dirs, files in os.walk(staging_path):
        rel_root = os.path.relpath(root, staging_path)
        destination_root = (
            new_path if rel_root == "." else os.path.join(new_path, rel_root)
        )
        if os.path.exists(destination_root) and not os.path.isdir(destination_root):
            raise FileExistsError(
                f"Destination path conflicts with file: {destination_root}"
            )
        for filename in files:
            destination_file = os.path.join(destination_root, filename)
            if os.path.exists(destination_file):
                raise FileExistsError(
                    f"Destination file already exists: {destination_file}"
                )


def _merge_staging_into_destination(
    staging_path: str, new_path: str
) -> list[tuple[str, str]]:
    moved_pairs: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(staging_path):
        rel_root = os.path.relpath(root, staging_path)
        destination_root = (
            new_path if rel_root == "." else os.path.join(new_path, rel_root)
        )
        os.makedirs(destination_root, exist_ok=True)

        for dirname in dirs:
            os.makedirs(os.path.join(destination_root, dirname), exist_ok=True)

        for filename in files:
            src_file = os.path.join(root, filename)
            dst_file = os.path.join(destination_root, filename)
            shutil.move(src_file, dst_file)
            moved_pairs.append((src_file, dst_file))
    return moved_pairs


def _rollback_merge(
    staging_path: str, old_path: str, moved_pairs: list[tuple[str, str]]
) -> None:
    for src_file, dst_file in reversed(moved_pairs):
        try:
            src_parent = os.path.dirname(src_file)
            os.makedirs(src_parent, exist_ok=True)
            if os.path.exists(dst_file):
                shutil.move(dst_file, src_file)
        except Exception as e:
            logger.error(f"Rollback file move failed: {e}", exc_info=True)

    try:
        if os.path.exists(staging_path):
            os.replace(staging_path, old_path)
    except Exception as e:
        logger.error(f"Rollback staging replace failed: {e}", exc_info=True)


def _perform_physical_move(old_path: str, new_path: str):
    """
    Helper to move folder contents. Handles case where destination already exists
    by merging contents instead of nesting.
    """
    logger.info(f"[Relink] Moving files from {old_path} to {new_path}")

    _assert_same_volume_move(old_path, new_path)

    # Case 1: Destination does not exist -> Standard Rename
    if not os.path.exists(new_path):
        parent_dir = os.path.dirname(new_path)
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        os.replace(old_path, new_path)
        return

    # Case 2: Destination exists -> transactional merge with rollback.
    staging_path = _prepare_staging_dir(old_path)
    moved_pairs: list[tuple[str, str]] = []
    try:
        _preflight_merge_conflicts(staging_path, new_path)
        moved_pairs = _merge_staging_into_destination(staging_path, new_path)

        shutil.rmtree(staging_path)
    except Exception:
        # Best-effort rollback to preserve pre-move state if merge fails mid-way.
        _rollback_merge(staging_path, old_path, moved_pairs)
        raise


@app.post(
    "/files/watched/relink",
    responses={
        400: {"description": "Invalid relink request"},
        404: {"description": "Old watched path not found in DB"},
        500: {"description": "Internal server error"},
    },
)
def relink_folder(data: PathRelink, db: DbSession):
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
                logger.error(f"[Relink] Physical move failed: {e}", exc_info=True)
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
    except HTTPException:
        monitor_service.sync_watches()  # safety
        raise
    except Exception as e:
        monitor_service.sync_watches()  # safety
        print(f"[API] relink_folder failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get(
    "/files/events",
    responses={
        400: {"description": "Path must be absolute"},
        403: {"description": "Path must be within a watched folder"},
        422: {"description": "Invalid path input"},
    },
)
# Return the most recent filesystem events recorded by the monitor.
def get_file_events(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    path: Annotated[str | None, Query(max_length=4096)] = None,
):
    safe_path = None
    if path is not None:
        try:
            safe_path = _validate_text_input(path, "path")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        _assert_path_allowed(safe_path, db)

    events = crud.get_recent_file_events(db, limit, safe_path)
    return events


@app.get("/files/events/stream")
async def stream_file_events(request: Request):
    async def event_generator():
        queue = event_stream.subscribe()
        try:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # Keep-alive ping and disconnect check cadence.
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            event_stream.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# --- Activity Endpoints ---
@app.get("/activity/timeline")
# Return recent user activity logs.
def get_timeline(
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
):
    return crud.get_activity_timeline(db, limit)


# --- File Versioning & Restore ---
@app.get(
    "/files/versions",
    responses={
        400: {"description": "Path must be absolute"},
        403: {"description": "Path must be within a watched folder"},
    },
)
def list_file_versions(path: str, db: DbSession):
    """List available versions for a specific file path"""
    _assert_path_allowed(path, db)

    versions = crud.get_file_versions(db, path)
    if not versions and os.path.exists(path):
        # Fallback/Recovery: If file exists but has no history,
        # try to link it using its current content (if valid).
        current_hash = storage.calculate_file_hash(path)
        if current_hash:
            crud.create_file_record(db, path, content_hash=current_hash)
            # Re-fetch
            versions = crud.get_file_versions(db, path)

    return [v for v in versions if os.path.exists(str(v.storage_path))]


@app.get(
    "/files/current-version",
    responses={
        400: {"description": "Path must be absolute"},
        403: {"description": "Path must be within a watched folder"},
        404: {"description": "File not found on disk"},
        500: {"description": "Failed to hash file"},
    },
)
def get_current_version(path: str, db: DbSession):
    """Return which saved version matches the file currently on disk.

    Note: restoring a version does not create a new snapshot; instead the file's
    current content may match an older version.
    """
    _assert_path_allowed(path, db)

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


@app.get(
    "/files/current-content",
    responses={
        400: {"description": "Path must be absolute"},
        403: {"description": "Path must be within a watched folder"},
        404: {"description": "File not found on disk"},
        500: {"description": "Failed to read file"},
    },
)
def get_current_file_content(path: str, db: DbSession):
    """Get the current on-disk content of a watched file path."""
    _assert_path_allowed(path, db)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    try:
        with open(path, "rb") as f:
            sample = f.read(FILE_PREVIEW_SNIFF_BYTES)

            if b"\x00" in sample:
                return {
                    "content": BINARY_PREVIEW_TEXT,
                    "type": "binary",
                }

            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "content": BINARY_PREVIEW_TEXT,
                    "type": "binary",
                }

            f.seek(0)
            content_bytes = f.read(FILE_PREVIEW_MAX_BYTES + 1)

        truncated = len(content_bytes) > FILE_PREVIEW_MAX_BYTES
        if truncated:
            content_bytes = content_bytes[:FILE_PREVIEW_MAX_BYTES]

        content = content_bytes.decode("utf-8")
        return {
            "content": content,
            "type": "text",
            "truncated": truncated,
            "max_bytes": FILE_PREVIEW_MAX_BYTES,
        }
    except UnicodeDecodeError:
        return {"content": BINARY_PREVIEW_TEXT, "type": "binary"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")


@app.get(
    "/files/versions/{version_id}/content",
    responses={
        404: {"description": "Version not found"},
        500: {"description": "Failed to read version content"},
    },
)
def get_version_content(version_id: int, db: DbSession):
    """Get the content of a specific file version"""
    version = (
        db.query(models.FileVersion).filter(models.FileVersion.id == version_id).first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    storage_path = str(version.storage_path)

    if not os.path.exists(storage_path):
        return {
            "content": "[Stored version content is no longer available on disk]",
            "type": "unavailable",
        }

    try:
        # Check if file is GZIP compressed (our new storage format)
        if storage_path.endswith(".gz"):
            # Decompress and read as text
            with gzip.open(storage_path, "rt", encoding="utf-8") as f:
                content = f.read()
        else:
            # Legacy format: read directly as text
            with open(storage_path, "r", encoding="utf-8") as f:
                content = f.read()
        return {"content": content, "type": "text"}
    except UnicodeDecodeError:
        # If it fails, it's likely binary
        return {"content": BINARY_PREVIEW_TEXT, "type": "binary"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")


def _normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.realpath(path)))


def _get_active_watched_paths(db: Session) -> list[str]:
    return [str(p.path) for p in crud.get_watched_paths(db)]


def _assert_path_allowed(target_path: str, db: Session) -> None:
    if not os.path.isabs(target_path):
        raise HTTPException(status_code=400, detail="Path must be absolute")

    watched_paths = _get_active_watched_paths(db)
    if not _is_within_watched_paths(target_path, watched_paths):
        raise HTTPException(
            status_code=403,
            detail="Path must be within a watched folder",
        )


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


@app.post(
    "/files/restore",
    responses={
        400: {"description": "Invalid restore destination"},
        403: {"description": "Path must be within a watched folder"},
        404: {"description": "Version not found"},
        500: {"description": "Failed to restore file from storage"},
    },
)
def restore_version(restore_data: FileRestore, db: DbSession):
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
    target_path = restore_data.dest_path or str(version.original_path)
    if not target_path:
        raise HTTPException(status_code=400, detail="Missing destination path")

    _assert_path_allowed(target_path, db)

    # Signal monitor to ignore the next update for this file
    register_restore_start(target_path)

    # 3. Restore
    success = storage.restore_file_version(str(version.storage_path), target_path)
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
def log_activity_manual(activity: ActivityCreate, db: DbSession):
    return crud.log_activity(db, activity.type, activity.app, activity.details)


# --- Security Settings ---
@app.get("/settings/security")
def get_security_settings(db: DbSession):
    enabled = crud.get_setting(db, "admin_protection_enabled", "false") == "true"
    return {
        "admin_protection_enabled": enabled,
        "is_admin": storage.is_admin_user(),
    }


@app.post(
    "/settings/security",
    responses={
        400: {"description": "Failed to apply security setting"},
    },
)
def set_security_settings(payload: AdminProtectionToggle, db: DbSession):
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
def set_tracking_exclusions(payload: TrackingExclusions, db: DbSession):
    storage.set_custom_exclusions(payload.exclusions)
    crud.set_setting(db, "tracking_exclusions", json.dumps(payload.exclusions))
    return {
        "custom_exclusions": sorted(storage.CUSTOM_EXCLUDED_DIRS),
    }


@app.get("/settings/snapshots")
def get_snapshot_settings(db: DbSession):
    return snapshot_service.get_settings(db)


@app.post(
    "/settings/snapshots",
    responses={
        400: {"description": "Invalid snapshot settings"},
    },
)
def update_snapshot_settings(payload: SnapshotSettingsUpdate, db: DbSession):
    updates: dict[str, object] = {}
    if payload.interval_seconds is not None:
        updates["interval_seconds"] = payload.interval_seconds
    if payload.retention_days is not None:
        updates["retention_days"] = payload.retention_days
    if payload.exclude_private_browsing is not None:
        updates["exclude_private_browsing"] = payload.exclude_private_browsing
    if payload.capture_on_window_change is not None:
        updates["capture_on_window_change"] = payload.capture_on_window_change
    if payload.allow_individual_delete is not None:
        updates["allow_individual_delete"] = payload.allow_individual_delete

    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    return snapshot_service.save_settings(db, updates)


@app.get("/settings/runtime")
def get_runtime_settings(db: DbSession):
    run_in_background_service = (
        crud.get_setting(db, "run_in_background_service", "true") == "true"
    )
    return {
        "run_in_background_service": run_in_background_service,
    }


@app.post(
    "/settings/runtime",
    responses={
        400: {"description": "Invalid runtime settings"},
    },
)
def update_runtime_settings(payload: RuntimeSettingsUpdate, db: DbSession):
    if payload.run_in_background_service is None:
        raise HTTPException(status_code=400, detail="No settings provided")

    crud.set_setting(
        db,
        "run_in_background_service",
        "true" if payload.run_in_background_service else "false",
    )
    return {
        "run_in_background_service": payload.run_in_background_service,
    }


@app.get("/auth/status")
def get_auth_status(db: DbSession):
    try:
        verifier = crud.get_setting(db, "snapshot_key_verifier", None)
    except Exception as e:
        logger.error(f"[Auth Status] Exception: {e}")
        # Tables might not exist yet (fresh DB or after manual wipe)
        return {"setup_required": True, "locked": False}
    setup_required = not bool(verifier)

    locked = False
    if not setup_required:
        locked = not snapshot_service.is_unlocked()

    return {"setup_required": setup_required, "locked": locked}


class SetupPayload(BaseModel):
    master_password: str


@app.post(
    "/auth/setup",
    responses={
        400: {"description": "Already setup or invalid master password"},
    },
)
def auth_setup(payload: SetupPayload, db: DbSession):
    verifier = crud.get_setting(db, "snapshot_key_verifier", None)
    if verifier:
        raise HTTPException(status_code=400, detail="Already setup")

    try:
        recovery_key = snapshot_service.setup_master_password(
            payload.master_password, db
        )
        return {"success": True, "recovery_key": recovery_key}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class UnlockPayload(BaseModel):
    passphrase: str


@app.post(
    "/auth/unlock",
    responses={
        401: {"description": "Invalid password or recovery key"},
    },
)
def auth_unlock(payload: UnlockPayload, db: DbSession):
    ok = snapshot_service.unlock(payload.passphrase, db)
    if not ok:
        raise HTTPException(status_code=401, detail="Invalid password or recovery key")
    return {"unlocked": True}


@app.post("/auth/lock")
def auth_lock(db: DbSession):
    snapshot_service.lock()
    return {"locked": True}


@app.get("/dashboard/summary")
def get_dashboard_summary(db: DbSession):
    from app.database import models
    import psutil
    import os

    from app.snapshot_service import SNAPSHOT_IMAGE_ROOT

    total_files = db.query(models.FileRecord).count()
    total_versions = db.query(models.FileVersion).count()

    # Calculate storage for both file versions and snapshot images
    storage_bytes = storage.get_total_storage_usage(storage.STORAGE_ROOT)
    snapshot_bytes = storage.get_total_storage_usage(SNAPSHOT_IMAGE_ROOT)
    total_storage_bytes = storage_bytes + snapshot_bytes

    # 1. RAM Usage
    process = psutil.Process(os.getpid())
    ram_usage_bytes = process.memory_info().rss

    # 2. DB Size
    try:
        db_size_bytes = os.path.getsize(models._DB_PATH)
    except Exception:
        db_size_bytes = 0

    # 3. Snapshot Engine Stats
    total_snapshots = db.query(models.ActivitySnapshotRecord).count()
    last_snap = (
        db.query(models.ActivitySnapshotRecord)
        .order_by(models.ActivitySnapshotRecord.captured_at.desc())
        .first()
    )
    last_snapshot_time = (
        last_snap.captured_at.replace(tzinfo=timezone.utc)
        if last_snap
        else None
    )

    return {
        "total_files": total_files,
        "total_versions": total_versions,
        "storage_bytes": total_storage_bytes,
        "ram_usage_bytes": ram_usage_bytes,
        "db_size_bytes": db_size_bytes,
        "total_snapshots": total_snapshots,
        "last_snapshot_time": last_snapshot_time,
    }


@app.post(
    "/auth/reset",
    responses={
        500: {"description": "Factory reset failed"},
    },
)
def auth_reset_factory(db: DbSession):
    """Factory reset: wipe all data and return to first-run setup."""
    import shutil
    from app.database.models import Base
    from app.storage import STORAGE_ROOT
    from app.snapshot_service import SNAPSHOT_IMAGE_ROOT

    try:
        # 1. Close the injected session so it releases its SQLite lock.
        db.close()

        # 2. Wipe all rows using the SAME engine the rest of the app uses
        #    (not models.py's separate engine). This avoids cross-engine deadlocks.
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys = OFF"))
            for tbl in reversed(Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
            conn.execute(text("PRAGMA foreign_keys = ON"))

        # 2b. Ensure tables exist (in case the DB file was externally deleted).
        Base.metadata.create_all(bind=engine)

        # 3. Only lock AFTER the DB wipe succeeds, so we never strand the user.
        snapshot_service.lock()

        # 4. Wipe file storage directories
        for dirpath in [str(STORAGE_ROOT), str(SNAPSHOT_IMAGE_ROOT)]:
            if os.path.exists(dirpath):
                shutil.rmtree(dirpath, ignore_errors=True)
            os.makedirs(dirpath, exist_ok=True)

        print("[RESET] Factory reset completed successfully.")
        return {"success": True}
    except Exception as e:
        print(f"[RESET] Factory reset FAILED: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post(
    "/snapshots/history",
    responses={
        400: {"description": "Invalid filter datetime format"},
        423: {"description": "Snapshot vault is locked"},
    },
)
def snapshot_history(payload: SnapshotHistoryQueryPayload, db: DbSession):
    if not snapshot_service.is_unlocked():
        raise HTTPException(
            status_code=423,
            detail=SNAPSHOT_VAULT_LOCKED_DETAIL,
        )

    def _parse_dt(raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            normalized = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid datetime format: {raw}. Use ISO-8601.",
            )

    start_dt = _parse_dt(payload.start_time)
    end_dt = _parse_dt(payload.end_time)

    return snapshot_service.history(
        db,
        text_query=payload.query,
        category=payload.category,
        app_name=payload.app_name,
        start_time=start_dt,
        end_time=end_dt,
        limit=payload.limit,
    )


@app.post(
    "/snapshots/execute-action",
    responses={
        400: {"description": "Invalid action request"},
        423: {"description": "Snapshot vault is locked"},
    },
)
def execute_snapshot_action(payload: SnapshotActionPayload, db: DbSession):
    if not snapshot_service.is_unlocked():
        raise HTTPException(
            status_code=423,
            detail=SNAPSHOT_VAULT_LOCKED_DETAIL,
        )

    result = snapshot_service.execute_action(payload.action_type, payload.value)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message") or "Action failed",
        )
    return result


@app.get(
    "/snapshots/{snapshot_id}/image",
    responses={
        404: {"description": "Snapshot image not found"},
        423: {"description": "Snapshot vault is locked"},
    },
)
def get_snapshot_image(snapshot_id: int, db: DbSession):
    if not snapshot_service.is_unlocked():
        raise HTTPException(status_code=423, detail=SNAPSHOT_VAULT_LOCKED_DETAIL)

    image_bytes = snapshot_service.get_snapshot_image_bytes(db, snapshot_id)
    if not image_bytes:
        raise HTTPException(status_code=404, detail="Snapshot image not found")

    return Response(content=image_bytes, media_type="image/jpeg")


@app.delete(
    "/snapshots/{snapshot_id}",
    responses={
        403: {"description": "Individual deletion is disabled"},
        404: {"description": "Snapshot not found"},
    },
)
def delete_snapshot(snapshot_id: int, db: DbSession):
    settings = snapshot_service.get_settings(db)
    if not settings.get("allow_individual_delete"):
        raise HTTPException(
            status_code=403,
            detail="Individual snapshot deletion is disabled in settings",
        )

    deleted = snapshot_service.delete_snapshot(db, snapshot_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"status": "deleted", "snapshot_id": snapshot_id}


if __name__ == "__main__":
    host = os.getenv("LOCUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    raw_port = os.getenv("LOCUS_PORT", str(DEFAULT_API_PORT)).strip()
    try:
        preferred_port = int(raw_port)
    except ValueError:
        preferred_port = DEFAULT_API_PORT

    selected_port = _pick_api_port(host, preferred_port)
    if selected_port != preferred_port:
        print(
            f"[Startup] Preferred port {preferred_port} unavailable, using {selected_port} instead"
        )

    uvicorn.run(app, host=host, port=selected_port)
