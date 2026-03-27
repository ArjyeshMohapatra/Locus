import pytest

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
    monkeypatch.setattr(models, "engine", engine)
    monkeypatch.setattr(models, "SessionLocal", SessionLocal)

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
    monkeypatch.setenv("LOCUS_SKIP_AUTH", "true")
    storage.init_storage()

    def _get_db():
        db = test_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    main_app.app.dependency_overrides[main_app.get_db] = _get_db

    monkeypatch.setattr(main_app, "background_monitor_task", lambda *a, **k: None)
    monkeypatch.setattr(main_app, "background_gc_task", lambda *a, **k: None)
    monkeypatch.setattr(main_app.monitor_service, "start", lambda *a, **k: None)
    monkeypatch.setattr(main_app.monitor_service, "stop", lambda *a, **k: None)
    monkeypatch.setattr(main_app.monitor_service, "sync_watches", lambda *a, **k: None)
    monkeypatch.setattr(
        main_app.monitor_service, "handle_root_deletion", lambda *a, **k: None
    )
    monkeypatch.setattr(main_app, "INITIAL_SNAPSHOT_ENABLED", False)

    # Bypass security middleware
    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda *a, **k: True)
    monkeypatch.setattr(main_app.snapshot_service, "start", lambda *a, **k: None)
    monkeypatch.setattr(main_app.snapshot_service, "stop", lambda *a, **k: None)

    # Ensure snapshot_key_verifier exists in test DB so middleware doesn't 401
    db = test_sessionmaker()
    from app.database import crud

    crud.set_setting(db, "snapshot_key_verifier", "test_verifier")
    db.close()

    with TestClient(main_app.app) as test_client:
        yield test_client

    main_app.app.dependency_overrides.clear()
