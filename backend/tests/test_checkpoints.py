# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false

from pathlib import Path
from typing import Any
from fastapi.testclient import TestClient


def _add_watched_path(client: TestClient, path: Path) -> dict[str, Any]:
    response = client.post("/files/watched", json={"path": str(path)})
    assert response.status_code == 200
    return response.json()


def _create_checkpoint(
    client: TestClient,
    watched_path: Path,
    scope: str,
    name: str | None = None,
    file_paths: list[Path] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "watched_path": str(watched_path),
        "scope": scope,
    }
    if name is not None:
        payload["name"] = name
    if file_paths is not None:
        payload["file_paths"] = [str(path) for path in file_paths]

    response = client.post("/checkpoints/sessions", json=payload)
    assert response.status_code == 200
    return response.json()


def test_create_full_folder_checkpoint_with_default_name(
    client: TestClient, tmp_path: Path
):
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("one")
    (watched / "b.txt").write_text("two")

    _add_watched_path(client, watched)

    resp = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched),
            "scope": "full_folder",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "full_folder"
    assert body["item_count"] == 2
    assert body["captured_count"] == 2
    assert body["name"].startswith("Checkpoint ")

    detail = client.get(f"/checkpoints/sessions/{body['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["items"]) == 2


def test_single_file_scope_requires_exactly_one_path(client: TestClient, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    first = watched / "a.txt"
    second = watched / "b.txt"
    first.write_text("one")
    second.write_text("two")

    _add_watched_path(client, watched)

    resp = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched),
            "scope": "single_file",
            "file_paths": [str(first), str(second)],
        },
    )
    assert resp.status_code == 400
    assert "exactly one" in resp.json()["detail"]


def test_selected_files_checkpoint_allows_duplicate_names(client: TestClient, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "target.txt"
    target.write_text("hello")

    _add_watched_path(client, watched)

    first = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched),
            "scope": "selected_files",
            "name": "  stable   build  ",
            "file_paths": [str(target)],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched),
            "scope": "selected_files",
            "name": "stable build",
            "file_paths": [str(target)],
        },
    )
    assert second.status_code == 200

    first_body = first.json()
    second_body = second.json()
    assert first_body["name"] == "stable build"
    assert second_body["name"] == "stable build"
    assert first_body["id"] != second_body["id"]

    listed = client.get(
        "/checkpoints/sessions",
        params={"watched_path": str(watched)},
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) >= 2
    assert rows[0]["id"] == second_body["id"]


def test_selected_file_must_be_within_selected_watched_folder(
    client: TestClient, tmp_path: Path
):
    watched_a = tmp_path / "watched-a"
    watched_b = tmp_path / "watched-b"
    watched_a.mkdir()
    watched_b.mkdir()

    inside_b = watched_b / "b.txt"
    inside_b.write_text("from b")

    _add_watched_path(client, watched_a)
    _add_watched_path(client, watched_b)

    resp = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched_a),
            "scope": "selected_files",
            "file_paths": [str(inside_b)],
        },
    )
    assert resp.status_code == 403
    assert "selected watched folder" in resp.json()["detail"]


def test_remove_watched_path_also_removes_checkpoint_sessions(
    client: TestClient, tmp_path: Path
):
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "tracked.txt"
    target.write_text("persist")

    watched_row = _add_watched_path(client, watched)

    created = client.post(
        "/checkpoints/sessions",
        json={
            "watched_path": str(watched),
            "scope": "single_file",
            "file_paths": [str(target)],
        },
    )
    assert created.status_code == 200

    removed = client.delete(f"/files/watched/{watched_row['id']}")
    assert removed.status_code == 200
    payload = removed.json()
    assert payload["checkpoint_sessions_deleted"] >= 1


def test_rename_checkpoint_session_normalizes_name(client: TestClient, tmp_path: Path):
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "rename.txt"
    target.write_text("rename-me")

    _add_watched_path(client, watched)
    created = _create_checkpoint(
        client,
        watched_path=watched,
        scope="single_file",
        file_paths=[target],
    )

    renamed = client.patch(
        f"/checkpoints/sessions/{created['id']}",
        json={"name": "   release    candidate   "},
    )
    assert renamed.status_code == 200
    renamed_body = renamed.json()
    assert renamed_body["name"] == "release candidate"

    detail = client.get(f"/checkpoints/sessions/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "release candidate"


def test_diff_checkpoint_sessions_reports_added_removed_modified(
    client: TestClient, tmp_path: Path
):
    watched = tmp_path / "watched"
    watched.mkdir()

    file_a = watched / "a.txt"
    file_b = watched / "b.txt"
    file_a.write_text("v1-a")
    file_b.write_text("v1-b")

    _add_watched_path(client, watched)

    first = _create_checkpoint(
        client,
        watched_path=watched,
        scope="full_folder",
        name="initial",
    )

    file_a.write_text("v2-a")
    file_b.unlink()
    file_c = watched / "c.txt"
    file_c.write_text("v1-c")

    second = _create_checkpoint(
        client,
        watched_path=watched,
        scope="full_folder",
        name="after-change",
    )

    diff_resp = client.post(
        "/checkpoints/sessions/diff",
        json={
            "from_session_id": first["id"],
            "to_session_id": second["id"],
        },
    )
    assert diff_resp.status_code == 200

    body = diff_resp.json()
    assert body["summary"]["added"] == 1
    assert body["summary"]["removed"] == 1
    assert body["summary"]["modified"] == 1

    added_paths = {row["file_path"] for row in body["added"]}
    removed_paths = {row["file_path"] for row in body["removed"]}
    modified_paths = {row["file_path"] for row in body["modified"]}

    assert str(file_c) in added_paths
    assert str(file_b) in removed_paths
    assert str(file_a) in modified_paths

    assert body["summary"]["added_lines"] >= 1
    assert body["summary"]["removed_lines"] >= 1

    modified_entry = next(row for row in body["modified"] if row["file_path"] == str(file_a))
    assert modified_entry["added_lines"] == 1
    assert modified_entry["removed_lines"] == 1
    assert modified_entry["line_diff"]["available"] is True
    assert len(modified_entry["line_diff"]["hunks"]) >= 1

    first_hunk = modified_entry["line_diff"]["hunks"][0]
    assert "v1-a" in first_hunk["removed_preview"]
    assert "v2-a" in first_hunk["added_preview"]


def test_restore_checkpoint_session_dry_run_then_execute_with_rename(
    client: TestClient, tmp_path: Path
):
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "restore-target.txt"
    target.write_text("original-v1")

    _add_watched_path(client, watched)
    checkpoint = _create_checkpoint(
        client,
        watched_path=watched,
        scope="single_file",
        file_paths=[target],
    )

    target.write_text("live-v2")

    dry_run = client.post(
        f"/checkpoints/sessions/{checkpoint['id']}/restore",
        json={
            "dry_run": True,
            "conflict_strategy": "rename",
        },
    )
    assert dry_run.status_code == 200
    dry_body = dry_run.json()
    assert dry_body["dry_run"] is True
    assert dry_body["summary"]["conflicts"] == 1
    assert dry_body["summary"]["would_restore"] == 1
    assert len(dry_body["plan"]) == 1
    assert dry_body["plan"][0]["action"] == "rename"
    renamed_target_path = dry_body["plan"][0]["resolved_target_path"]

    restored = client.post(
        f"/checkpoints/sessions/{checkpoint['id']}/restore",
        json={
            "dry_run": False,
            "conflict_strategy": "rename",
        },
    )
    assert restored.status_code == 200
    restore_body = restored.json()
    assert restore_body["dry_run"] is False
    assert restore_body["summary"]["restored"] == 1
    assert restore_body["summary"]["failed"] == 0

    assert target.read_text() == "live-v2"
    restored_path = Path(restore_body["restored"][0]["target_path"])
    assert str(restored_path) == str(renamed_target_path)
    assert restored_path.exists()
    assert restored_path.read_text() == "original-v1"


def test_restore_checkpoint_rejects_destination_outside_checkpoint_root(
    client: TestClient, tmp_path: Path
):
    watched_a = tmp_path / "watched-a"
    watched_b = tmp_path / "watched-b"
    watched_a.mkdir()
    watched_b.mkdir()

    target = watched_a / "inside-a.txt"
    target.write_text("a")

    _add_watched_path(client, watched_a)
    _add_watched_path(client, watched_b)

    checkpoint = _create_checkpoint(
        client,
        watched_path=watched_a,
        scope="single_file",
        file_paths=[target],
    )

    resp = client.post(
        f"/checkpoints/sessions/{checkpoint['id']}/restore",
        json={
            "dry_run": True,
            "destination_root": str(watched_b),
            "conflict_strategy": "overwrite",
        },
    )
    assert resp.status_code == 403
    assert "within checkpoint watched folder" in resp.json()["detail"]
