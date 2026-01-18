import os
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app, get_db
from backend.app import storage
from backend.app.database import crud


pytestmark = pytest.mark.slow


def test_restore_endpoint_rejects_outside_and_allows_inside(tmp_path, monkeypatch):
    # prepare storage file (source)
    stored = tmp_path / "stored.txt"
    stored.write_text("hello world")

    # watched folder inside tmp_path
    watched = tmp_path / "watched"
    watched.mkdir()

    # Fake version object
    version = SimpleNamespace(
        id=1,
        storage_path=str(stored),
        original_path=str(watched / "original.txt"),
        version_number=1,
        file_hash="deadbeef",
    )

    # Fake DB that can return versions or watched paths depending on model
    class VersionQuery:
        def __init__(self, version):
            self._version = version

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self._version

    class WatchedQuery:
        def __init__(self, watched_path):
            self._watched = watched_path

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [SimpleNamespace(path=self._watched)]

    class FakeDB:
        def __init__(self, version, watched_path):
            self._version = version
            self._watched = watched_path

        def query(self, model):
            name = getattr(model, "__name__", "")
            if name == "FileVersion":
                return VersionQuery(self._version)
            else:
                return WatchedQuery(self._watched)

    fake_db = FakeDB(version, str(watched))

    # Monkeypatch crud.get_watched_paths to return our watched path
    monkeypatch.setattr(
        crud, "get_watched_paths", lambda db: [SimpleNamespace(path=str(watched))]
    )

    # Override dependency
    def _fake_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = _fake_get_db

    client = TestClient(app)

    # Attempt to restore to an outside path -> should be forbidden
    outside = tmp_path.parent / "outside" / "hosts"
    outside.parent.mkdir(parents=True, exist_ok=True)

    resp = client.post(
        "/files/restore", json={"version_id": 1, "dest_path": str(outside)}
    )
    assert resp.status_code == 403

    # Now restore to an inside path -> should succeed
    inside = watched / "restored.txt"
    resp2 = client.post(
        "/files/restore", json={"version_id": 1, "dest_path": str(inside)}
    )
    assert resp2.status_code == 200
    assert inside.exists()
    assert inside.read_text() == "hello world"

    # Cleanup dependency override
    app.dependency_overrides.pop(get_db, None)
