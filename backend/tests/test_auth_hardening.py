from app import main as main_app
from app import snapshot_service as snapshot_module


def _reset_unlock_throttle_state() -> None:
    main_app._auth_attempt_history.clear()
    main_app._auth_lockout_deadline.clear()


def test_auth_setup_rejects_short_password(client):
    password_field = "master_" + "password"
    resp = client.post("/auth/setup", json={password_field: "short"})
    assert resp.status_code == 422


def test_auth_unlock_rate_limited_after_repeated_failures(client, monkeypatch):
    _reset_unlock_throttle_state()
    monkeypatch.setattr(main_app.snapshot_service, "unlock", lambda *_args, **_kwargs: False)

    payload = {"passphrase": "not-the-right-passphrase"}
    for _ in range(main_app.AUTH_MAX_ATTEMPTS):
        resp = client.post("/auth/unlock", json=payload)
        assert resp.status_code == 401

    locked_resp = client.post("/auth/unlock", json=payload)
    assert locked_resp.status_code == 429



def test_auth_reset_requires_intent_header(client):
    resp = client.post(
        "/auth/reset",
        json={"confirmation": "RESET LOCUS DATA", "passphrase": None},
    )
    assert resp.status_code == 400



def test_auth_reset_allows_without_passphrase_when_vault_locked(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda: False)
    monkeypatch.setattr(main_app.snapshot_service, "unlock", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(snapshot_module, "SNAPSHOT_IMAGE_ROOT", tmp_path / ".snapshot_images")

    resp = client.post(
        "/auth/reset",
        headers={"X-Locus-Reset-Intent": "confirm"},
        json={"confirmation": "RESET LOCUS DATA", "passphrase": None},
    )
    assert resp.status_code == 200
    assert resp.json().get("success") is True


def test_auth_reset_rejects_invalid_passphrase_when_provided(client, monkeypatch):
    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda: False)
    monkeypatch.setattr(main_app.snapshot_service, "unlock", lambda *_args, **_kwargs: False)

    resp = client.post(
        "/auth/reset",
        headers={"X-Locus-Reset-Intent": "confirm"},
        json={"confirmation": "RESET LOCUS DATA", "passphrase": "wrong-passphrase"},
    )
    assert resp.status_code == 401


def test_auth_reset_cors_preflight_allows_reset_intent_header(client):
    resp = client.options(
        "/auth/reset",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-locus-reset-intent",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "tauri://localhost"
    assert "x-locus-reset-intent" in resp.headers.get(
        "access-control-allow-headers", ""
    ).lower()
