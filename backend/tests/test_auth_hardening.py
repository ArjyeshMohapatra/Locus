from app import main as main_app


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



def test_auth_reset_requires_passphrase_when_vault_locked(client, monkeypatch):
    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda: False)
    monkeypatch.setattr(main_app.snapshot_service, "unlock", lambda *_args, **_kwargs: False)

    resp = client.post(
        "/auth/reset",
        headers={"X-Locus-Reset-Intent": "confirm"},
        json={"confirmation": "RESET LOCUS DATA", "passphrase": None},
    )
    assert resp.status_code == 401
