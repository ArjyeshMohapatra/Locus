import json
from pathlib import Path


def test_telemetry_event_persists_local_record(client, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCUS_DATA_DIR", str(tmp_path))

    response = client.post(
        "/telemetry/events",
        json={
            "source": "ui",
            "event_type": "unhandled_error",
            "severity": "error",
            "message": "frontend exploded",
            "stack": "Error: frontend exploded",
            "context": {
                "screen": "dashboard",
                "component": "App"
            },
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["event_id"]

    log_path = Path(tmp_path) / "diagnostics" / "stability-events.jsonl"
    assert log_path.exists()

    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert lines

    record = json.loads(lines[-1])
    assert record["source"] == "ui"
    assert record["event_type"] == "unhandled_error"
    assert record["severity"] == "error"
    assert record["message"] == "frontend exploded"
    assert record["context"]["screen"] == "dashboard"
