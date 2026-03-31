from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Boolean,
    Text,
    BigInteger,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.sql import func

import os as _os

# models.py is at backend/app/database/models.py → 3 dirname levels to reach backend/
_APP_DIR = _os.path.dirname(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
)
_DB_PATH = _os.path.join(_APP_DIR, "locus.db")
DATABASE_URL = f"sqlite:///{_DB_PATH}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# stores a list of folders locus should keep track of
class WatchedPath(Base):
    __tablename__ = "watched_paths"

    id = Column(Integer, primary_key=True, index=True)
    path = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# tracks initial snapshot progress for a watched path
class SnapshotJob(Base):
    __tablename__ = "snapshot_jobs"

    id = Column(Integer, primary_key=True, index=True)
    watched_path = Column(String, unique=True, nullable=False, index=True)
    storage_subdir = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    total_files = Column(Integer, nullable=False, default=0)
    processed_files = Column(Integer, nullable=False, default=0)
    skipped_files = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# logs related to everything that happens to a file (created, modified, deleted) in real time
class FileEvent(Base):
    __tablename__ = "file_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)  # created, modified, deleted, moved
    src_path = Column(String, nullable=False, index=True)
    dest_path = Column(String, nullable=True)  # For moves/renames
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    is_processed = Column(Boolean, default=False)


# durable queue for file backup work
class BackupTask(Base):
    __tablename__ = "backup_tasks"

    id = Column(Integer, primary_key=True, index=True)
    src_path = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# keeps track of identity of a file (e.g. if renamed)
class FileRecord(Base):
    __tablename__ = "file_records"

    id = Column(Integer, primary_key=True, index=True)
    # The current path on disk. If the file is renamed, this is updated.
    current_path = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now()
    )  # sets time when a row gets inserted
    last_seen_at = Column(
        DateTime(timezone=True), onupdate=func.now()
    )  # sets new value each time a row is updated


# stores backup or history of a file
class FileVersion(Base):
    __tablename__ = "file_versions"

    id = Column(Integer, primary_key=True, index=True)

    # Link to the consistent identity of the file
    file_record_id = Column(Integer, ForeignKey("file_records.id"), nullable=True)

    # Historical path at the time of backup (snapshot)
    original_path = Column(String, nullable=False, index=True)

    storage_path = Column(String, nullable=False)  # Path to the backed up file
    version_number = Column(Integer, nullable=False)
    file_hash = Column(String, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)

    # Relationship
    file_record = relationship("FileRecord")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CheckpointSession(Base):
    __tablename__ = "checkpoint_sessions"

    id = Column(Integer, primary_key=True, index=True)
    watched_path = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    scope = Column(String, nullable=False, index=True)
    item_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    items = relationship(
        "CheckpointSessionItem",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class CheckpointSessionItem(Base):
    __tablename__ = "checkpoint_session_items"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("checkpoint_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path = Column(String, nullable=False, index=True)
    file_record_id = Column(Integer, ForeignKey("file_records.id"), nullable=True)
    file_version_id = Column(Integer, ForeignKey("file_versions.id"), nullable=False)
    file_hash = Column(String, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("CheckpointSession", back_populates="items")
    file_record = relationship("FileRecord")
    file_version = relationship("FileVersion")


# tracks what a user is doing on their system
class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    activity_type = Column(
        String, nullable=False
    )  # app_focus, browser_visit, system_idle, system_shutdown
    app_name = Column(String, nullable=True)
    window_title = Column(String, nullable=True)
    details = Column(Text, nullable=True)  # JSON or descriptive text
    start_time = Column(DateTime(timezone=True), server_default=func.now())
    end_time = Column(DateTime(timezone=True), nullable=True)


# stores screenshots and context found in them
class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, index=True)
    image_path = Column(
        String, nullable=True
    )  # Can be null if we delete image after OCR
    ocr_text = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    app_context = Column(String, nullable=True)  # What app was open?


class ActivitySnapshotRecord(Base):
    __tablename__ = "activity_snapshot_records"

    id = Column(Integer, primary_key=True, index=True)
    captured_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    encrypted_payload = Column(Text, nullable=False)
    fingerprint = Column(String, nullable=False, index=True)


# setting's page configuration
class KeyValueStore(Base):
    """Simple settings store for user config like 'max_storage_size'"""

    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String)
