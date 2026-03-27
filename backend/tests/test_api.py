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


def test_security_headers_present_on_api_responses(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert resp.headers["cross-origin-resource-policy"] == "same-origin"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" in resp.headers


def test_rejects_control_chars_in_user_input(client):
    bad_path = "C:/tracked/evil\u0000folder"
    resp = client.post("/files/watched", json={"path": bad_path})
    assert resp.status_code == 422

    bad_activity = {
        "type": "app_focus",
        "app": "code.exe",
        "details": "hello\u0007world",
    }
    resp2 = client.post("/activity/log", json=bad_activity)
    assert resp2.status_code == 422


def test_watched_paths_roundtrip(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()

    resp = client.post("/files/watched", json={"path": str(watched)})
    assert resp.status_code == 200

    resp2 = client.get("/files/watched")
    assert resp2.status_code == 200
    paths = [p["path"] for p in resp2.json()]
    assert str(watched) in paths


def test_add_watched_path_is_idempotent(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()

    first = client.post("/files/watched", json={"path": str(watched)})
    assert first.status_code == 200
    first_id = first.json()["id"]

    second = client.post("/files/watched", json={"path": str(watched)})
    assert second.status_code == 200
    second_id = second.json()["id"]
    assert second_id == first_id

    listed = client.get("/files/watched")
    assert listed.status_code == 200
    matches = [item for item in listed.json() if item["path"] == str(watched)]
    assert len(matches) == 1


def test_readd_soft_deleted_watched_path_reactivates(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()

    add_resp = client.post("/files/watched", json={"path": str(watched)})
    assert add_resp.status_code == 200
    watched_id = add_resp.json()["id"]

    remove_resp = client.delete(f"/files/watched/{watched_id}")
    assert remove_resp.status_code == 200

    readd_resp = client.post("/files/watched", json={"path": str(watched)})
    assert readd_resp.status_code == 200
    assert readd_resp.json()["id"] == watched_id

    listed = client.get("/files/watched")
    assert listed.status_code == 200
    matches = [item for item in listed.json() if item["path"] == str(watched)]
    assert len(matches) == 1


def test_watched_tree_includes_root_and_nested_files(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "root_file.txt").write_text("a")

    nested = watched / "src" / "storage"
    nested.mkdir(parents=True)
    (nested / "deep_file.txt").write_text("b")

    add_resp = client.post("/files/watched", json={"path": str(watched)})
    assert add_resp.status_code == 200

    tree_resp = client.get("/files/watched/tree")
    assert tree_resp.status_code == 200
    roots = tree_resp.json()

    root = next(item for item in roots if item["path"] == str(watched))
    assert root["exists"] is True
    assert root["tree"]["file_count"] == 2

    root_children = root["tree"]["children"]
    assert any(
        c["type"] == "file" and c["name"] == "root_file.txt" for c in root_children
    )

    src = next(c for c in root_children if c["type"] == "dir" and c["name"] == "src")
    storage = next(
        c for c in src["children"] if c["type"] == "dir" and c["name"] == "storage"
    )
    assert any(
        c["type"] == "file" and c["name"] == "deep_file.txt"
        for c in storage["children"]
    )


def test_remove_watched_path_purges_tracked_data(client, db_session, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    nested_dir = watched / "docs"
    nested_dir.mkdir()
    tracked_file = nested_dir / "a.txt"
    tracked_file.write_text("hello")

    add_resp = client.post("/files/watched", json={"path": str(watched)})
    assert add_resp.status_code == 200
    watched_id = add_resp.json()["id"]

    record = crud.create_file_record(db_session, str(tracked_file))
    crud.create_file_event(
        db_session,
        {
            "event_type": "created",
            "src_path": str(tracked_file),
            "dest_path": None,
        },
    )
    crud.enqueue_backup_task(db_session, str(tracked_file))
    crud.create_file_version(
        db_session,
        str(tracked_file),
        str(tmp_path / "dummy.gz"),
        1,
        "hash123",
        5,
        file_record_id=record.id,
    )

    remove_resp = client.delete(f"/files/watched/{watched_id}")
    assert remove_resp.status_code == 200

    payload = remove_resp.json()
    assert payload["status"] == "removed"
    assert payload["file_records_deleted"] >= 1
    assert payload["file_events_deleted"] >= 1
    assert payload["file_versions_deleted"] >= 1
    assert payload["backup_tasks_deleted"] >= 1

    watched_after = client.get("/files/watched").json()
    assert all(item["id"] != watched_id for item in watched_after)


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


def test_file_query_endpoints_reject_paths_outside_watched(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    outside = tmp_path.parent / "outside" / "doc.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("outside")

    resp1 = client.get("/files/versions", params={"path": str(outside)})
    assert resp1.status_code == 403

    resp2 = client.get("/files/current-version", params={"path": str(outside)})
    assert resp2.status_code == 403


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


def test_version_content_endpoint_missing_storage_returns_unavailable(
    client, db_session, tmp_path: Path
):
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

    Path(meta["storage_path"]).unlink(missing_ok=True)

    resp = client.get(f"/files/versions/{version.id}/content")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "unavailable"
    assert "no longer available" in data["content"]


def test_versions_endpoint_hides_missing_storage_versions(
    client, db_session, tmp_path: Path
):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    file_path = watched / "note.txt"
    file_path.write_text("content")

    meta = storage.save_file_version(str(file_path))
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

    Path(meta["storage_path"]).unlink(missing_ok=True)

    resp = client.get("/files/versions", params={"path": str(file_path)})
    assert resp.status_code == 200
    assert resp.json() == []


def test_current_content_endpoint(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    file_path = watched / "note.txt"
    file_path.write_text("live-content")

    resp = client.get("/files/current-content", params={"path": str(file_path)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "text"
    assert data["content"] == "live-content"


def test_current_content_endpoint_rejects_outside_watched(client, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    outside = tmp_path.parent / "outside" / "x.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("nope")

    resp = client.get("/files/current-content", params={"path": str(outside)})
    assert resp.status_code == 403


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


def test_events_endpoint_can_filter_by_path(client, db_session, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    client.post("/files/watched", json={"path": str(watched)})

    inside_a = watched / "a.txt"
    inside_b = watched / "b.txt"
    outside = tmp_path.parent / "outside" / "x.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)

    crud.create_file_event(
        db_session,
        {
            "event_type": "created",
            "src_path": str(inside_a),
            "dest_path": None,
        },
    )
    crud.create_file_event(
        db_session,
        {
            "event_type": "modified",
            "src_path": str(inside_b),
            "dest_path": None,
        },
    )

    filtered = client.get("/files/events", params={"path": str(inside_a), "limit": 500})
    assert filtered.status_code == 200
    filtered_items = filtered.json()
    assert len(filtered_items) >= 1
    assert all(
        item["src_path"] == str(inside_a) or item.get("dest_path") == str(inside_a)
        for item in filtered_items
    )

    forbidden = client.get("/files/events", params={"path": str(outside), "limit": 500})
    assert forbidden.status_code == 403


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
