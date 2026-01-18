from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import models, crud


def _make_session(tmp_path: Path):
    db_path = tmp_path / "queue_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    models.Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_backup_task_queue_lifecycle(tmp_path: Path):
    SessionLocal = _make_session(tmp_path)
    db = SessionLocal()
    try:
        task = crud.enqueue_backup_task(db, "C:/file.txt")
        assert task.status == "pending"
        assert task.attempts == 0

        assert crud.has_pending_backup_task(db, "C:/file.txt") is True

        next_task = crud.get_next_backup_task(db)
        assert next_task is not None
        assert next_task.id == task.id

        task = crud.mark_backup_task_processing(db, task)
        assert task.status == "processing"
        assert task.attempts == 1

        task = crud.mark_backup_task_done(db, task)
        assert task.status == "done"

        assert crud.has_pending_backup_task(db, "C:/file.txt") is False
    finally:
        db.close()


def test_backup_task_failed_records_error(tmp_path: Path):
    SessionLocal = _make_session(tmp_path)
    db = SessionLocal()
    try:
        task = crud.enqueue_backup_task(db, "C:/file2.txt")
        task = crud.mark_backup_task_processing(db, task)
        task = crud.mark_backup_task_failed(db, task, "boom")

        assert task.status == "failed"
        assert task.last_error == "boom"
        assert task.attempts == 1
    finally:
        db.close()
