from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal, init_db
from app.services import repository
from auth_helpers import auth_headers


client = TestClient(app)


def test_operation_log_redacts_sensitive_payload_fields() -> None:
    init_db()
    target_id = f"redaction_{uuid4().hex}"

    with SessionLocal() as db:
        repository.log_operation(
            db,
            action="security.redaction.test",
            target_type="operation_log",
            target_id=target_id,
            actor_id="test",
            payload={
                "password": "plain-password",
                "email_hash": "stable-email-hash",
                "callback_token": "full-callback-token",
                "callback_token_history": [
                    {"token_tail": "abc123", "rotated_at": "2026-06-27T00:00:00", "token": "old-full-token"}
                ],
                "token_rotation": {
                    "rotated": True,
                    "old_token_tail": "abc123",
                    "new_token_tail": "def456",
                },
                "database_url": "postgresql://app:plain-db-password@db:5432/packaging",
                "callback_url": "https://hooks.example.test/send?token=plain-token&event=ok",
                "adapter": {
                    "api_key": "secret-api-key",
                    "api_key_header": "X-API-Key",
                    "headers": {"Authorization": "Bearer secret-token"},
                },
                "attempts": [{"webhook_secret": "webhook-secret", "status": "failed"}],
            },
        )
        entries = repository.list_operation_logs(db, limit=50)

    entry = next(item for item in entries if item["target_id"] == target_id)
    payload = entry["payload"]
    assert payload["password"] == "***"
    assert payload["email_hash"] == "stable-email-hash"
    assert payload["callback_token"] == "***"
    assert payload["callback_token_history"][0]["token_tail"] == "abc123"
    assert payload["callback_token_history"][0]["token"] == "***"
    assert payload["token_rotation"]["old_token_tail"] == "abc123"
    assert payload["token_rotation"]["new_token_tail"] == "def456"
    assert payload["database_url"] == "postgresql://app:***@db:5432/packaging"
    assert payload["callback_url"] == "https://hooks.example.test/send?token=***&event=ok"
    assert payload["adapter"]["api_key"] == "***"
    assert payload["adapter"]["api_key_header"] == "X-API-Key"
    assert payload["adapter"]["headers"]["Authorization"] == "***"
    assert payload["attempts"][0]["webhook_secret"] == "***"
    assert payload["attempts"][0]["status"] == "failed"


def test_operation_log_filters_by_action_target_actor_and_time() -> None:
    init_db()
    suffix = uuid4().hex
    actor_id = f"actor_{suffix[:8]}"
    target_id = f"target_{suffix[:8]}"

    with SessionLocal() as db:
        repository.log_operation(
            db,
            action="audit.filter.match",
            target_type="filter_target",
            target_id=target_id,
            actor_id=actor_id,
            payload={"case": "match"},
        )
        repository.log_operation(
            db,
            action="audit.filter.other",
            target_type="filter_target",
            target_id=f"other_{suffix[:8]}",
            actor_id=actor_id,
            payload={"case": "other"},
        )
        matched = repository.list_operation_logs(
            db,
            limit=20,
            action="audit.filter.match",
            target_type="filter_target",
            target_id=target_id,
            actor_id=actor_id,
        )
        created_at = matched[0]["created_at"]

    assert len(matched) == 1
    assert matched[0]["target_id"] == target_id

    headers = auth_headers(client)
    response = client.get(
        "/api/operation-logs",
        headers=headers,
        params={
            "limit": 20,
            "action": "audit.filter.match",
            "target_type": "filter_target",
            "target_id": target_id,
            "actor_id": actor_id,
            "created_from": created_at,
            "created_to": created_at,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["action"] == "audit.filter.match"
    assert payload[0]["target_id"] == target_id
