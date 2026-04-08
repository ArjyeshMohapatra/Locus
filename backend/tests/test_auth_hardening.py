# pyright: reportPrivateUsage=false

from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app import main as main_app
from app import snapshot_service as snapshot_module


def _reset_unlock_throttle_state() -> None:
    main_app._auth_attempt_history.clear()
    main_app._auth_lockout_deadline.clear()


def test_auth_setup_rejects_short_password(client: TestClient) -> None:
    password_field = "master_" + "password"
    resp = client.post("/auth/setup", json={password_field: "short"})
    assert resp.status_code == 422


def _always_fail_unlock(*_args: object, **_kwargs: object) -> bool:
    return False


def test_auth_unlock_rate_limited_after_repeated_failures(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    _reset_unlock_throttle_state()
    monkeypatch.setattr(main_app.snapshot_service, "unlock", _always_fail_unlock)

    payload = {"passphrase": "not-the-right-passphrase"}
    for _ in range(main_app.AUTH_MAX_ATTEMPTS):
        resp = client.post("/auth/unlock", json=payload)
        assert resp.status_code == 401

    locked_resp = client.post("/auth/unlock", json=payload)
    assert locked_resp.status_code == 429


def test_auth_reset_requires_intent_header(client: TestClient) -> None:
    resp = client.post(
        "/auth/reset",
        json={
            "confirmation": "DELETE MY LOCUS DATA COMPLETELY",
            "reset_nonce": "nonce-placeholder",
            "final_confirmed": True,
        },
    )
    assert resp.status_code == 400


def test_auth_reset_allows_without_passphrase_when_vault_locked(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _valid_reset_challenge(_nonce: str) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda: False)
    monkeypatch.setattr(
        snapshot_module, "SNAPSHOT_IMAGE_ROOT", tmp_path / ".snapshot_images"
    )
    monkeypatch.setattr(main_app, "_validate_reset_challenge", _valid_reset_challenge)

    request_resp = client.post(
        "/auth/reset/request",
        headers={"X-Locus-Reset-Intent": "confirm"},
    )
    assert request_resp.status_code == 200
    request_payload_raw = request_resp.json()
    assert isinstance(request_payload_raw, dict)
    request_payload = cast(dict[str, object], request_payload_raw)
    reset_nonce_obj = request_payload.get("reset_nonce")
    assert isinstance(reset_nonce_obj, str) and reset_nonce_obj
    reset_nonce = reset_nonce_obj

    resp = client.post(
        "/auth/reset",
        headers={"X-Locus-Reset-Intent": "confirm"},
        json={
            "confirmation": "DELETE MY LOCUS DATA COMPLETELY",
            "reset_nonce": reset_nonce,
            "final_confirmed": True,
        },
    )
    assert resp.status_code == 200
    response_payload_raw = resp.json()
    assert isinstance(response_payload_raw, dict)
    response_payload = cast(dict[str, object], response_payload_raw)
    assert response_payload.get("success") is True


def test_auth_reset_rejects_invalid_or_expired_challenge(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_app.snapshot_service, "is_unlocked", lambda: False)

    resp = client.post(
        "/auth/reset",
        headers={"X-Locus-Reset-Intent": "confirm"},
        json={
            "confirmation": "DELETE MY LOCUS DATA COMPLETELY",
            "reset_nonce": "invalid-challenge",
            "final_confirmed": True,
        },
    )
    assert resp.status_code == 400


def test_auth_reset_cors_preflight_allows_reset_intent_header(
    client: TestClient,
) -> None:
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
    assert (
        "x-locus-reset-intent"
        in resp.headers.get("access-control-allow-headers", "").lower()
    )
