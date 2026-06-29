import hashlib
import hmac
import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import SessionLocal, init_db
from app.domain import schemas
from app.main import app
from app.services import repository
from app.services.messaging import dispatch_message_event
from auth_helpers import auth_headers


client = TestClient(app)


def test_approval_notifications_can_be_read() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"NOTIFY_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"NOTIFY_SHEET_{suffix}",
            "width": 500,
            "height": 400,
            "margin_top": 5,
            "margin_right": 5,
            "margin_bottom": 5,
            "margin_left": 5,
            "gripper_mm": 10,
            "material": "white_card",
            "thickness": "350gsm",
        },
        "candidate_items": [
            {
                "item_id": f"item_{suffix}",
                "order_id": f"order_{suffix}",
                "polygon": {"shape_id": f"shape_{suffix}", "outer": [[0, 0], [100, 0], [100, 80], [0, 80]]},
                "priority_score": 0.9,
            }
        ],
    }
    assert client.post("/api/nesting/jobs", json=job, headers=headers).status_code == 200
    run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)
    assert run_response.status_code == 200
    solution_id = run_response.json()["solutions"][0]["solution_id"]

    request_response = client.post(
        f"/api/solutions/{solution_id}/approval/request",
        json={"note": "notify approver"},
        headers=headers,
    )
    assert request_response.status_code == 200

    unread = client.get("/api/notifications?unread_only=true&limit=100", headers=headers)
    assert unread.status_code == 200
    approval_notifications = [
        item
        for item in unread.json()
        if item["target_id"] == solution_id and item["event_type"] == "solution.approval.requested"
    ]
    assert approval_notifications
    notification = approval_notifications[0]
    assert notification["is_read"] is False

    read_response = client.post(f"/api/notifications/{notification['id']}/read", headers=headers)
    assert read_response.status_code == 200
    assert read_response.json()["is_read"] is True

    decision = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={"decision": "approved", "note": "notify requester", "confirmation": f"APPROVE {solution_id}"},
        headers=headers,
    )
    assert decision.status_code == 200
    unread_after_decision = client.get("/api/notifications?unread_only=true&limit=100", headers=headers)
    decision_notifications = [
        item
        for item in unread_after_decision.json()
        if item["target_id"] == solution_id and item["event_type"] == "solution.approval.approved"
    ]
    assert decision_notifications

    read_all = client.post("/api/notifications/read-all", headers=headers)
    assert read_all.status_code == 200
    assert read_all.json()["updated_count"] >= 1


def test_message_template_dispatch_renders_in_app_notification_and_logs() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    event_type = f"custom.material_alert.{suffix}"
    create_response = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={
            "name": f"物料告警 {suffix}",
            "event_type": event_type,
            "channel": "in_app",
            "title_template": "物料 {material.code} 短缺",
            "message_template": "缺口 {material.shortage}，任务 {job_id}",
            "recipient_permission_code": "audit:read",
            "escalation_permission_code": "tasks:manage",
            "escalation_after_minutes": 5,
            "metadata": {"owner": "qa"},
        },
    )
    assert create_response.status_code == 200
    template = create_response.json()
    assert template["event_type"] == event_type
    assert template["metadata"]["owner"] == "qa"

    dispatch_response = client.post(
        "/api/notifications/dispatch",
        headers=headers,
        json={
            "event_type": event_type,
            "context": {"job_id": f"JOB-{suffix}", "material": {"code": "PAPER-350", "shortage": 42}},
            "target_type": "nesting_job",
            "target_id": f"JOB-{suffix}",
            "default_title": "fallback",
            "default_message": "fallback",
            "recipient_permission_code": "audit:read",
            "payload": {"dedupe_key": f"dedupe-{suffix}"},
            "channel_filter": ["in_app"],
        },
    )
    assert dispatch_response.status_code == 200
    dispatch_payload = dispatch_response.json()
    assert dispatch_payload["status"] == "sent"
    assert dispatch_payload["notification_count"] >= 1

    notifications = client.get("/api/notifications?unread_only=true&limit=500", headers=headers)
    assert notifications.status_code == 200
    rendered = [
        item
        for item in notifications.json()
        if item["event_type"] == event_type and item["target_id"] == f"JOB-{suffix}"
    ]
    assert rendered
    assert rendered[0]["title"] == "物料 PAPER-350 短缺"
    assert "缺口 42" in rendered[0]["message"]
    assert rendered[0]["payload"]["message_template_id"] == template["id"]

    logs = client.get(f"/api/notifications/dispatch-logs?event_type={event_type}", headers=headers)
    assert logs.status_code == 200
    assert any(item["template_id"] == template["id"] and item["status"] == "sent" for item in logs.json())

    patch_response = client.patch(
        f"/api/notifications/templates/{template['id']}",
        headers=headers,
        json={"is_active": False},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["is_active"] is False


def test_message_template_api_redacts_webhook_secrets_but_dispatch_uses_raw_metadata() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    event_type = f"work_task.signed_api_redaction.{suffix}"
    webhook_url = f"https://alerts.example.test/hooks/{suffix}/secret-token"
    signature_secret = f"webhook-secret-{suffix}"

    create_response = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={
            "name": f"签名 Webhook {suffix}",
            "event_type": event_type,
            "channel": "webhook",
            "title_template": "签名告警 {job_id}",
            "message_template": "失败数 {metrics.failed}",
            "metadata": {
                "webhook_provider": "generic",
                "webhook_url": webhook_url,
                "signature_secret": signature_secret,
                "signature_header": "X-Notify-Signature",
                "signature_timestamp_header": "X-Notify-Timestamp",
            },
        },
    )
    assert create_response.status_code == 200
    public_template = create_response.json()
    assert public_template["metadata"]["webhook_provider"] == "generic"
    assert public_template["metadata"]["webhook_url"] == "***"
    assert public_template["metadata"]["signature_secret"] == "***"
    assert public_template["metadata"]["signature_header"] == "X-Notify-Signature"
    assert webhook_url not in create_response.text
    assert signature_secret not in create_response.text

    list_response = client.get(f"/api/notifications/templates?event_type={event_type}", headers=headers)
    assert list_response.status_code == 200
    assert list_response.json()[0]["metadata"]["webhook_url"] == "***"
    assert list_response.json()[0]["metadata"]["signature_secret"] == "***"
    assert webhook_url not in list_response.text
    assert signature_secret not in list_response.text

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == webhook_url
        body = request.read()
        timestamp = request.headers["X-Notify-Timestamp"]
        expected = hmac.new(
            signature_secret.encode("utf-8"),
            timestamp.encode("utf-8") + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-Notify-Signature"] == f"sha256={expected}"
        return httpx.Response(202, json={"accepted": True})

    with SessionLocal() as db:
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-API-REDACT", "metrics": {"failed": 2}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"api-redaction-{suffix}"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "sent"
    assert result.dispatches[0].payload["signature"]["enabled"] is True
    assert len(requests) == 1


def test_recipient_group_dispatch_routes_to_direct_department_and_permission_members() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    department_code = f"press_{suffix}"
    roles = client.get("/api/rbac/roles", headers=headers)
    assert roles.status_code == 200
    auditor_role = next(role for role in roles.json() if role["name"] == "auditor")

    direct_user = client.post(
        "/api/rbac/users",
        headers=headers,
        json={
            "email": f"notify_direct_{suffix}@example.com",
            "display_name": "Notify Direct",
            "password": "Strong123!45",
            "role_ids": [],
        },
    )
    assert direct_user.status_code == 200
    department_user = client.post(
        "/api/rbac/users",
        headers=headers,
        json={
            "email": f"notify_department_{suffix}@example.com",
            "display_name": "Notify Department",
            "password": "Strong123!45",
            "org_unit_code": department_code,
            "org_unit_name": "Press Line",
            "job_title": "Press Operator",
            "role_ids": [],
        },
    )
    assert department_user.status_code == 200
    permission_user = client.post(
        "/api/rbac/users",
        headers=headers,
        json={
            "email": f"notify_permission_{suffix}@example.com",
            "display_name": "Notify Permission",
            "password": "Strong123!45",
            "role_ids": [auditor_role["id"]],
        },
    )
    assert permission_user.status_code == 200

    group_response = client.post(
        "/api/notifications/recipient-groups",
        headers=headers,
        json={
            "name": f"上线收件组 {suffix}",
            "description": "Customer organization mapped recipients",
            "member_user_ids": [direct_user.json()["id"]],
            "permission_codes": ["audit:read"],
            "department_codes": [department_code],
            "metadata": {"owner": "ops"},
        },
    )
    assert group_response.status_code == 200
    group = group_response.json()
    assert group["resolved_user_count"] >= 3
    assert group["department_codes"] == [department_code]

    event_type = f"recipient.group.alert.{suffix}"
    template_response = client.post(
        "/api/notifications/templates",
        headers=headers,
        json={
            "name": f"收件组模板 {suffix}",
            "event_type": event_type,
            "channel": "in_app",
            "title_template": "收件组告警 {job_id}",
            "message_template": "部门 {department} 待处理",
            "recipient_group_id": group["id"],
        },
    )
    assert template_response.status_code == 200
    assert template_response.json()["recipient_group_id"] == group["id"]

    target_id = f"JOB-GROUP-{suffix}"
    dispatch_response = client.post(
        "/api/notifications/dispatch",
        headers=headers,
        json={
            "event_type": event_type,
            "context": {"job_id": target_id, "department": department_code},
            "target_type": "nesting_job",
            "target_id": target_id,
            "default_title": "fallback",
            "default_message": "fallback",
            "payload": {"dedupe_key": f"group-{suffix}"},
            "channel_filter": ["in_app"],
        },
    )
    assert dispatch_response.status_code == 200
    dispatch = dispatch_response.json()
    assert dispatch["status"] == "sent"
    assert dispatch["notification_count"] >= 3
    assert dispatch["dispatches"][0]["payload"]["recipient_group_id"] == group["id"]

    for email in [
        f"notify_direct_{suffix}@example.com",
        f"notify_department_{suffix}@example.com",
        f"notify_permission_{suffix}@example.com",
    ]:
        login = client.post("/api/auth/login", json={"email": email, "password": "Strong123!45"})
        assert login.status_code == 200
        user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        notifications = client.get("/api/notifications?unread_only=true&limit=100", headers=user_headers)
        assert notifications.status_code == 200
        assert any(item["event_type"] == event_type and item["target_id"] == target_id for item in notifications.json())

    patch_group = client.patch(
        f"/api/notifications/recipient-groups/{group['id']}",
        headers=headers,
        json={"is_active": False},
    )
    assert patch_group.status_code == 200
    assert patch_group.json()["is_active"] is False
    skipped = client.post(
        "/api/notifications/dispatch",
        headers=headers,
        json={
            "event_type": f"recipient.group.disabled.{suffix}",
            "target_type": "nesting_job",
            "target_id": f"JOB-DISABLED-{suffix}",
            "default_title": "disabled",
            "default_message": "disabled",
            "recipient_group_id": group["id"],
            "channel_filter": ["in_app"],
        },
    )
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert skipped.json()["notification_count"] == 0


def test_recipient_group_api_redacts_metadata_secrets() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    webhook_url = f"https://alerts.example.test/group/{suffix}/secret"
    api_key = f"group-api-key-{suffix}"
    group_response = client.post(
        "/api/notifications/recipient-groups",
        headers=headers,
        json={
            "name": f"收件组敏感元数据 {suffix}",
            "permission_codes": ["audit:read"],
            "metadata": {
                "owner": "ops",
                "webhook_url": webhook_url,
                "api_key": api_key,
            },
        },
    )

    assert group_response.status_code == 200
    metadata = group_response.json()["metadata"]
    assert metadata["owner"] == "ops"
    assert metadata["webhook_url"] == "***"
    assert metadata["api_key"] == "***"
    assert webhook_url not in group_response.text
    assert api_key not in group_response.text


def test_webhook_message_template_renders_and_posts_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = request.read().decode("utf-8")
        assert "外部告警 JOB-WEBHOOK" in body
        assert "work_task.failure_high" in body
        return httpx.Response(202, json={"accepted": True})

    suffix = uuid4().hex[:8]
    event_type = f"work_task.failure_high.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Webhook {suffix}",
                event_type=event_type,
                channel="webhook",
                title_template="外部告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                metadata={"webhook_url": "https://alerts.example.test/message"},
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-WEBHOOK", "metrics": {"failed": 3}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"webhook-{suffix}"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "sent"
    assert result.dispatches[0].external_push["status"] == "sent"
    assert result.dispatches[0].external_push["http_status_code"] == 202
    assert result.dispatches[0].external_push["attempt_count"] == 1
    assert len(requests) == 1


def test_feishu_webhook_template_uses_feishu_text_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read().decode("utf-8"))
        assert payload["msg_type"] == "text"
        assert "飞书告警 JOB-FEISHU" in payload["content"]["text"]
        assert "事件: work_task.feishu" in payload["content"]["text"]
        return httpx.Response(200, json={"StatusCode": 0})

    suffix = uuid4().hex[:8]
    event_type = f"work_task.feishu.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Feishu webhook {suffix}",
                event_type=event_type,
                channel="webhook",
                title_template="飞书告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                metadata={
                    "webhook_provider": "feishu",
                    "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                },
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-FEISHU", "metrics": {"failed": 4}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"feishu-{suffix}"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "sent"
    assert result.dispatches[0].payload["webhook_provider"] == "feishu"
    assert result.dispatches[0].external_push["webhook_provider"] == "feishu"
    assert len(requests) == 1


def test_wecom_webhook_template_uses_wecom_markdown_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.read().decode("utf-8"))
        assert payload["msgtype"] == "markdown"
        assert "**企微告警 JOB-WECOM**" in payload["markdown"]["content"]
        assert ">事件: work_task.wecom" in payload["markdown"]["content"]
        return httpx.Response(200, json={"errcode": 0})

    suffix = uuid4().hex[:8]
    event_type = f"work_task.wecom.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"WeCom webhook {suffix}",
                event_type=event_type,
                channel="webhook",
                title_template="企微告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                metadata={
                    "webhook_provider": "wecom",
                    "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                },
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-WECOM", "metrics": {"failed": 5}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"wecom-{suffix}"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "sent"
    assert result.dispatches[0].payload["webhook_provider"] == "wecom"
    assert result.dispatches[0].external_push["webhook_provider"] == "wecom"
    assert len(requests) == 1


def test_webhook_message_template_signs_and_retries_until_success() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = request.read()
        timestamp = request.headers["X-Notify-Timestamp"]
        expected = hmac.new(b"webhook-secret", timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
        assert request.headers["X-Notify-Signature"] == f"sha256={expected}"
        assert request.headers["X-Packaging-Event"].startswith("work_task.retry_signed")
        if len(requests) == 1:
            return httpx.Response(503, text="temporary unavailable")
        return httpx.Response(202, json={"accepted": True})

    suffix = uuid4().hex[:8]
    event_type = f"work_task.retry_signed.{suffix}"
    init_db()
    with SessionLocal() as db:
        template = repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Signed webhook {suffix}",
                event_type=event_type,
                channel="webhook",
                title_template="签名告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                metadata={
                    "webhook_url": "https://alerts.example.test/signed",
                    "signature_secret": "webhook-secret",
                    "signature_header": "X-Notify-Signature",
                    "signature_timestamp_header": "X-Notify-Timestamp",
                    "retry_count": 1,
                },
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-SIGNED", "metrics": {"failed": 2}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"signed-{suffix}"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )
        logs = repository.list_message_dispatch_logs(db, event_type=event_type, limit=5)

    assert result.status == "sent"
    assert result.dispatches[0].external_push["attempt_count"] == 2
    assert result.dispatches[0].payload["signature"]["enabled"] is True
    assert len(requests) == 2
    assert logs[0].template_id == template.id
    assert logs[0].payload["attempt_count"] == 2
    assert logs[0].payload["attempts"][0]["http_status_code"] == 503
    assert logs[0].payload["attempts"][1]["http_status_code"] == 202


def test_webhook_message_template_dedupes_noise_by_dedupe_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    suffix = uuid4().hex[:8]
    event_type = f"work_task.dedupe_webhook.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Dedupe webhook {suffix}",
                event_type=event_type,
                channel="webhook",
                title_template="重复告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                metadata={
                    "webhook_url": "https://alerts.example.test/dedupe",
                    "dedupe_minutes": 30,
                },
            ),
        )
        first = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-DEDUPE", "metrics": {"failed": 7}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": "same-alert"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )
        second = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-DEDUPE", "metrics": {"failed": 7}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": "same-alert"},
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=httpx.MockTransport(handler),
        )

    assert first.status == "sent"
    assert second.status == "skipped"
    assert second.dispatches[0].payload["reason"] == "dedupe_window"
    assert second.dispatches[0].external_push["reason"] == "dedupe_window"
    assert len(requests) == 1


def test_email_message_template_resolves_recipients_and_sends() -> None:
    sent_messages = []
    suffix = uuid4().hex[:8]
    event_type = f"work_task.email.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Email {suffix}",
                event_type=event_type,
                channel="email",
                title_template="邮件告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                recipient_permission_code="audit:read",
                metadata={"email_subject_prefix": "[OPS] "},
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-EMAIL", "metrics": {"failed": 9}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"email-{suffix}"},
            channel_filter={"email"},
            settings=Settings(SMTP_HOST="smtp.example.test", SMTP_FROM_EMAIL="alerts@example.test"),
            email_sender=lambda message, settings: sent_messages.append(message),
        )
        logs = repository.list_message_dispatch_logs(db, event_type=event_type, limit=5)

    assert result.status == "sent"
    assert result.dispatches[0].channel == "email"
    assert result.dispatches[0].recipient_count >= 1
    assert result.dispatches[0].external_push["status"] == "sent"
    assert result.dispatches[0].payload["subject"] == "[OPS] 邮件告警 JOB-EMAIL"
    assert len(sent_messages) == 1
    assert sent_messages[0]["From"] == "alerts@example.test"
    assert "admin@example.com" in sent_messages[0]["To"]
    assert sent_messages[0]["Subject"] == "[OPS] 邮件告警 JOB-EMAIL"
    assert "事件: work_task.email" in sent_messages[0].get_content()
    assert logs[0].status == "sent"
    assert logs[0].channel == "email"
    assert logs[0].payload["recipient_count"] >= 1


def test_email_message_template_skips_when_smtp_is_not_configured() -> None:
    suffix = uuid4().hex[:8]
    event_type = f"work_task.email_no_smtp.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Email no smtp {suffix}",
                event_type=event_type,
                channel="email",
                title_template="邮件告警 {job_id}",
                message_template="失败数 {metrics.failed}",
                recipient_permission_code="audit:read",
            ),
        )
        result = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-NO-SMTP", "metrics": {"failed": 1}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": f"email-no-smtp-{suffix}"},
            channel_filter={"email"},
            settings=Settings(),
        )

    assert result.status == "skipped"
    assert result.dispatches[0].payload["reason"] == "smtp not configured"
    assert result.dispatches[0].recipient_count >= 1


def test_email_message_template_dedupes_sent_messages() -> None:
    sent_messages = []
    suffix = uuid4().hex[:8]
    event_type = f"work_task.email_dedupe.{suffix}"
    init_db()
    with SessionLocal() as db:
        repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=f"Email dedupe {suffix}",
                event_type=event_type,
                channel="email",
                title_template="重复邮件 {job_id}",
                message_template="失败数 {metrics.failed}",
                recipient_permission_code="audit:read",
                metadata={"dedupe_minutes": 30},
            ),
        )
        first = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-EMAIL-DEDUPE", "metrics": {"failed": 2}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": "same-email-alert"},
            channel_filter={"email"},
            settings=Settings(SMTP_HOST="smtp.example.test", SMTP_FROM_EMAIL="alerts@example.test"),
            email_sender=lambda message, settings: sent_messages.append(message),
        )
        second = dispatch_message_event(
            db,
            event_type=event_type,
            context={"job_id": "JOB-EMAIL-DEDUPE", "metrics": {"failed": 2}},
            default_title="fallback",
            default_message="fallback",
            target_type="work_task_metrics",
            target_id=event_type,
            payload={"dedupe_key": "same-email-alert"},
            channel_filter={"email"},
            settings=Settings(SMTP_HOST="smtp.example.test", SMTP_FROM_EMAIL="alerts@example.test"),
            email_sender=lambda message, settings: sent_messages.append(message),
        )

    assert first.status == "sent"
    assert second.status == "skipped"
    assert second.dispatches[0].payload["reason"] == "dedupe_window"
    assert len(sent_messages) == 1
