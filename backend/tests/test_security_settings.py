import pytest

from app import storage


def test_security_settings_toggle(client, monkeypatch):
    monkeypatch.setattr(storage, "enable_admin_protection", lambda: (True, "ok"))
    monkeypatch.setattr(storage, "disable_admin_protection", lambda: (True, "ok"))
    monkeypatch.setattr(storage, "is_admin_user", lambda: True)

    resp = client.get("/settings/security")
    assert resp.status_code == 200
    data = resp.json()
    assert data["admin_protection_enabled"] is False
    assert data["is_admin"] is True

    resp2 = client.post("/settings/security", json={"enabled": True})
    assert resp2.status_code == 200
    assert resp2.json()["admin_protection_enabled"] is True

    resp3 = client.post("/settings/security", json={"enabled": False})
    assert resp3.status_code == 200
    assert resp3.json()["admin_protection_enabled"] is False


def test_security_settings_requires_admin(client, monkeypatch):
    monkeypatch.setattr(storage, "enable_admin_protection", lambda: (False, "no admin"))

    resp = client.post("/settings/security", json={"enabled": True})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "no admin"
