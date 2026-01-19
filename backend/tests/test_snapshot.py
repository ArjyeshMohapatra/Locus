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
