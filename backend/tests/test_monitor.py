import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import models, crud
from app import monitor


def _make_sessionmaker(tmp_path: Path):
    db_path = tmp_path / "monitor_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_backup_file_enqueues_task(monkeypatch, tmp_path: Path):
    session_local = _make_sessionmaker(tmp_path)
    monkeypatch.setattr(monitor, "SessionLocal", session_local)
    monkeypatch.setattr(monitor, "LAST_BACKUP_TS", {})
    monkeypatch.setattr(monitor, "PENDING_RESTORES", {})

    handler = monitor.LocusEventHandler()
    file_path = tmp_path / "file.txt"
    file_path.write_text("hello")

    handler._backup_file(str(file_path))

    db = session_local()
    try:
        tasks = db.query(models.BackupTask).all()
        assert len(tasks) == 1
        assert tasks[0].src_path == str(file_path)
    finally:
        db.close()


def test_backup_file_debounce(monkeypatch, tmp_path: Path):
    session_local = _make_sessionmaker(tmp_path)
    monkeypatch.setattr(monitor, "SessionLocal", session_local)
    monkeypatch.setattr(monitor, "LAST_BACKUP_TS", {})
    monkeypatch.setattr(monitor, "PENDING_RESTORES", {})
    monkeypatch.setattr(monitor, "BACKUP_DEBOUNCE_SECONDS", 0.3)

    handler = monitor.LocusEventHandler()
    file_path = tmp_path / "file2.txt"
    file_path.write_text("hello")

    handler._backup_file(str(file_path))

    db = session_local()
    try:
        task = crud.get_next_backup_task(db)
        assert task is not None
        crud.mark_backup_task_done(db, task)
    finally:
        db.close()

    handler._backup_file(str(file_path))

    db = session_local()
    try:
        pending = (
            db.query(models.BackupTask)
            .filter(models.BackupTask.status == "pending")
            .all()
        )
        assert len(pending) == 0
    finally:
        db.close()

    time.sleep(0.35)
    handler._backup_file(str(file_path))

    db = session_local()
    try:
        pending = (
            db.query(models.BackupTask)
            .filter(models.BackupTask.status == "pending")
            .all()
        )
        assert len(pending) == 1
    finally:
        db.close()
