import pytest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import storage
from app.database import models
from app import main as main_app


@pytest.fixture
def test_sessionmaker(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    models.Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(main_app, "engine", engine)
    monkeypatch.setattr(main_app, "SessionLocal", SessionLocal)

    return SessionLocal


@pytest.fixture
def db_session(test_sessionmaker):
    db = test_sessionmaker()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(tmp_path, monkeypatch, test_sessionmaker):
    storage_root = tmp_path / ".storage"
    chunk_dir = storage_root / "chunks"
    monkeypatch.setattr(storage, "STORAGE_ROOT", storage_root)
    monkeypatch.setattr(storage, "CHUNK_DIR", chunk_dir)
    storage.init_storage()

    def _get_db():
        db = test_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    main_app.app.dependency_overrides[main_app.get_db] = _get_db

    monkeypatch.setattr(main_app, "background_monitor_task", lambda: None)
    monkeypatch.setattr(main_app, "background_gc_task", lambda: None)
    monkeypatch.setattr(main_app.monitor_service, "sync_watches", lambda: None)
    monkeypatch.setattr(
        main_app.monitor_service, "handle_root_deletion", lambda path: None
    )

    with TestClient(main_app.app) as test_client:
        yield test_client

    main_app.app.dependency_overrides.clear()
