from pathlib import Path
import json
import pytest

from app import storage, event_stream
from app.database import crud


pytestmark = pytest.mark.slow


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["db"] == "connected"


def test_watched_paths_roundtrip(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()

    resp = client.post("/files/watched", json={"path": str(watched)})
    assert resp.status_code == 200

    resp2 = client.get("/files/watched")
    assert resp2.status_code == 200
    paths = [p["path"] for p in resp2.json()]
    assert str(watched) in paths


def test_activity_log_and_timeline(client):
    payload = {"type": "app_focus", "app": "code.exe", "details": "editing"}
    resp = client.post("/activity/log", json=payload)
    assert resp.status_code == 200

    resp2 = client.get("/activity/timeline")
    assert resp2.status_code == 200
    items = resp2.json()
    assert len(items) >= 1


def test_file_versions_and_current_version(client, db_session, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    file_path = watched / "doc.txt"
    file_path.write_text("hello")

    meta = storage.save_file_version(str(file_path))
    assert meta is not None

    record = crud.create_file_record(
        db_session, str(file_path), content_hash=meta["file_hash"]
    )
    crud.create_file_version(
        db_session,
        str(file_path),
        meta["storage_path"],
        1,
        meta["file_hash"],
        meta["file_size"],
        file_record_id=record.id,
    )

    resp = client.get("/files/versions", params={"path": str(file_path)})
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 1

    resp2 = client.get("/files/current-version", params={"path": str(file_path)})
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["matches_version"] is True
    assert data["version_number"] == 1


def test_version_content_endpoint(client, db_session, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    file_path = watched / "note.txt"
    file_path.write_text("content")

    meta = storage.save_file_version(str(file_path))
    record = crud.create_file_record(
        db_session, str(file_path), content_hash=meta["file_hash"]
    )
    version = crud.create_file_version(
        db_session,
        str(file_path),
        meta["storage_path"],
        1,
        meta["file_hash"],
        meta["file_size"],
        file_record_id=record.id,
    )

    resp = client.get(f"/files/versions/{version.id}/content")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "text"
    assert data["content"] == "content"


def test_events_endpoint(client, db_session, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    event = crud.create_file_event(
        db_session,
        {
            "event_type": "created",
            "src_path": str(watched / "a.txt"),
            "dest_path": None,
        },
    )
    assert event.id is not None

    resp = client.get("/files/events")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1


def test_events_stream_endpoint(client):
    payload = {"id": 1, "event_type": "created", "src_path": "x", "dest_path": None}

    with client.stream("GET", "/files/events/stream") as response:
        assert response.status_code == 200
        event_stream.publish(payload)

        data_line = None
        for line in response.iter_lines():
            if not line:
                continue
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if line.startswith("data: "):
                data_line = line
                break

        assert data_line is not None
        data = json.loads(data_line.replace("data: ", "", 1))
        assert data["event_type"] == "created"
