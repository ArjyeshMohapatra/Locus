from pathlib import Path

from app import storage, monitor
from app.database import models
from app import main as main_app


def test_initial_snapshot_creates_mirror_and_versions(
    client, db_session, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(main_app, "INITIAL_SNAPSHOT_ENABLED", True)
    monkeypatch.setattr(main_app, "INITIAL_SNAPSHOT_BLOCKING", True)
    monkeypatch.setattr(monitor, "SessionLocal", main_app.SessionLocal)

    watched = tmp_path / "MyTestFiles"
    watched.mkdir()
    file_path = watched / "a.txt"
    file_path.write_text("hello")

    resp = client.post("/files/watched", json={"path": str(watched)})
    assert resp.status_code == 200

    subdir = storage.storage_subdir_name(str(watched))
    mirrored = storage.STORAGE_ROOT / subdir / "a.txt"
    assert mirrored.exists()
    assert mirrored.read_text() == "hello"

    job = (
        db_session.query(models.SnapshotJob)
        .filter(models.SnapshotJob.watched_path == str(watched))
        .first()
    )
    assert job is not None
    assert job.status == "done"
    assert job.total_files == 1
    assert job.processed_files == 1

    versions = db_session.query(models.FileVersion).all()
    assert len(versions) == 1


def test_initial_snapshot_publishes_cancelled_complete_event(
    db_session,
    tmp_path: Path,
    monkeypatch,
):
    watched = tmp_path / "cancel-test"
    watched.mkdir()
    (watched / "a.txt").write_text("a")
    (watched / "b.txt").write_text("b")

    events: list[dict[str, object]] = []
    monkeypatch.setattr(main_app.event_stream, "publish", lambda payload: events.append(payload))

    processed_calls = {"count": 0}

    def _fake_process_snapshot_file(file_path: str, root_path: str, storage_subdir: str) -> None:
        _ = (file_path, root_path, storage_subdir)
        processed_calls["count"] += 1
        if processed_calls["count"] == 1:
            main_app._request_snapshot_cancel(str(watched))

    monkeypatch.setattr(main_app, "_process_snapshot_file", _fake_process_snapshot_file)

    main_app._run_initial_snapshot(str(watched))

    snapshot_complete = [
        payload for payload in events if payload.get("type") == "snapshot_complete"
    ]
    assert snapshot_complete
    latest = snapshot_complete[-1]
    assert latest.get("watched_path") == str(watched)
    assert latest.get("cancelled") is True
    assert latest.get("processed") == 1
