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

DATABASE_URL = "sqlite:///./locus.db"
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


# logs related to everything that happens to a file (created, modified, deleted) in real time
class FileEvent(Base):
    __tablename__ = "file_events"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String, nullable=False)  # created, modified, deleted, moved
    src_path = Column(String, nullable=False, index=True)
    dest_path = Column(String, nullable=True)  # For moves/renames
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    is_processed = Column(Boolean, default=False)


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


# setting's page configuration
class KeyValueStore(Base):
    """Simple settings store for user config like 'max_storage_size'"""

    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String)
