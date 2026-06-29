import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import get_settings
from auth_helpers import auth_headers


client = TestClient(app)


def test_default_admin_login_and_me() -> None:
    login = client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "Admin123!"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    payload = me.json()
    assert payload["email"] == "admin@example.com"
    assert "admin" in payload["roles"]
    assert "nesting:write" in payload["permissions"]
    assert "audit:read" in payload["permissions"]
    assert "rbac:manage" in payload["permissions"]


def test_protected_write_rejects_missing_token() -> None:
    response = client.post(
        "/api/sheets",
        json={
            "sheet_id": "AUTH_REQUIRED_SHEET",
            "width": 500,
            "height": 400,
            "material": "white_card",
            "thickness": "350gsm",
        },
    )
    assert response.status_code == 401


def test_business_reads_reject_missing_token() -> None:
    paths = [
        "/api/orders",
        "/api/sheets",
        "/api/artworks",
        "/api/nesting/jobs",
        "/api/nesting/runs",
        "/api/solutions/missing_solution_id/report",
    ]
    for path in paths:
        assert client.get(path).status_code == 401


def test_failed_login_attempt_is_audited() -> None:
    email = f"missing_{uuid4().hex[:8]}@example.com"
    response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    assert response.status_code == 401

    logs = client.get("/api/operation-logs", headers=auth_headers(client))
    assert logs.status_code == 200
    entries = [
        item
        for item in logs.json()
        if item["action"] == "auth.login_failed" and item["payload"].get("email_hash") == _email_hash(email)
    ]
    assert entries
    assert entries[0]["actor_id"] is None
    assert entries[0]["payload"]["failure_count"] == 1
    assert entries[0]["payload"]["client_host"]


def test_repeated_failed_logins_are_throttled_and_audited() -> None:
    email = f"throttle_{uuid4().hex[:8]}@example.com"
    settings = get_settings()
    for _ in range(settings.login_rate_limit_max_failures):
        response = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
        assert response.status_code == 401

    throttled = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    assert throttled.status_code == 429
    assert throttled.json()["detail"] == "too many failed login attempts; retry later"
    assert int(throttled.headers["Retry-After"]) > 0

    logs = client.get("/api/operation-logs", headers=auth_headers(client))
    assert logs.status_code == 200
    entries = [
        item
        for item in logs.json()
        if item["action"] == "auth.login_throttled" and item["payload"].get("email_hash") == _email_hash(email)
    ]
    assert entries
    assert entries[0]["payload"]["failure_count"] >= settings.login_rate_limit_max_failures
    assert entries[0]["payload"]["limit"] == settings.login_rate_limit_max_failures


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]
