from __future__ import annotations

import hashlib
import hmac
import json
import re
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from typing import Any, Callable

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain import schemas
from app.services import repository


PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")
WEBHOOK_SIGNATURE_HEADER = "X-Packaging-Signature"
WEBHOOK_SIGNATURE_TIMESTAMP_HEADER = "X-Packaging-Timestamp"
EmailSender = Callable[[EmailMessage, Settings], None]


def dispatch_message_event(
    db: Session,
    *,
    event_type: str,
    context: dict[str, Any] | None = None,
    default_title: str,
    default_message: str,
    target_type: str | None = None,
    target_id: str | None = None,
    recipient_permission_code: str | None = None,
    recipient_group_id: str | None = None,
    payload: dict[str, Any] | None = None,
    channel_filter: set[str] | None = None,
    settings: Settings | None = None,
    http_transport: httpx.BaseTransport | None = None,
    email_sender: EmailSender | None = None,
) -> schemas.MessageDispatchResult:
    settings = settings or get_settings()
    context_data = context or {}
    payload_data = payload or {}
    render_context = {
        "event_type": event_type,
        "target_type": target_type,
        "target_id": target_id,
        **context_data,
    }
    templates = repository.list_message_templates(db, event_type=event_type, active_only=True, redact_metadata=False)
    if channel_filter is not None:
        templates = [template for template in templates if template.channel in channel_filter]

    dispatches: list[schemas.MessageDispatchRecord] = []
    if not templates and (channel_filter is None or "in_app" in channel_filter):
        dispatches.append(
            _dispatch_fallback_in_app(
                db,
                event_type=event_type,
                title=default_title,
                message=default_message,
                target_type=target_type,
                target_id=target_id,
                recipient_permission_code=recipient_permission_code,
                recipient_group_id=recipient_group_id,
                payload=payload_data,
            )
        )
    for template in templates:
        if template.channel == "in_app":
            dispatches.append(
                _dispatch_in_app_template(
                    db,
                    template=template,
                    render_context=render_context,
                    target_type=target_type,
                    target_id=target_id,
                    fallback_permission_code=recipient_permission_code,
                    fallback_group_id=recipient_group_id,
                    payload=payload_data,
                )
            )
        elif template.channel == "webhook":
            dispatches.append(
                _dispatch_webhook_template(
                    db,
                    template=template,
                    render_context=render_context,
                    target_type=target_type,
                    target_id=target_id,
                    payload=payload_data,
                    settings=settings,
                    http_transport=http_transport,
                )
            )
        elif template.channel == "email":
            dispatches.append(
                _dispatch_email_template(
                    db,
                    template=template,
                    render_context=render_context,
                    target_type=target_type,
                    target_id=target_id,
                    fallback_permission_code=recipient_permission_code,
                    fallback_group_id=recipient_group_id,
                    payload=payload_data,
                    settings=settings,
                    email_sender=email_sender,
                )
            )

    notification_count = sum(item.notification_count for item in dispatches)
    if any(item.status == "sent" for item in dispatches):
        status = "sent"
    elif any(item.status == "failed" for item in dispatches):
        status = "failed"
    else:
        status = "skipped"
    return schemas.MessageDispatchResult(
        event_type=event_type,
        status=status,
        notification_count=notification_count,
        dispatches=dispatches,
    )


def render_message_template(template: str, context: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = _resolve_path(context, match.group(1))
        return "" if value is None else str(value)

    return PLACEHOLDER_PATTERN.sub(replace, template)


def _dispatch_fallback_in_app(
    db: Session,
    *,
    event_type: str,
    title: str,
    message: str,
    target_type: str | None,
    target_id: str | None,
    recipient_permission_code: str | None,
    recipient_group_id: str | None,
    payload: dict[str, Any],
) -> schemas.MessageDispatchRecord:
    if not recipient_permission_code and not recipient_group_id:
        return _record_dispatch(
            db,
            template_id=None,
            event_type=event_type,
            channel="in_app",
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=0,
            payload={"reason": "recipient routing not configured"},
        )
    recipient_ids: set[str] = set()
    if recipient_permission_code:
        recipient_ids.update(repository.list_user_ids_by_permission(db, recipient_permission_code))
    if recipient_group_id:
        recipient_ids.update(repository.list_user_ids_by_recipient_group(db, recipient_group_id))
    notifications = repository.create_user_notifications(
        db,
        user_ids=sorted(recipient_ids),
        event_type=event_type,
        title=title,
        message=message,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )
    return _record_dispatch(
        db,
        template_id=None,
        event_type=event_type,
        channel="in_app",
        target_type=target_type,
        target_id=target_id,
        status="sent" if notifications else "skipped",
        recipient_count=len(notifications),
        notification_count=len(notifications),
        payload={
            "fallback": True,
            "recipient_permission_code": recipient_permission_code,
            "recipient_group_id": recipient_group_id,
        },
    )


def _dispatch_in_app_template(
    db: Session,
    *,
    template: schemas.MessageTemplateRead,
    render_context: dict[str, Any],
    target_type: str | None,
    target_id: str | None,
    fallback_permission_code: str | None,
    fallback_group_id: str | None,
    payload: dict[str, Any],
) -> schemas.MessageDispatchRecord:
    permission_code = template.recipient_permission_code or fallback_permission_code
    group_id = template.recipient_group_id or fallback_group_id
    if not permission_code and not group_id:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=0,
            payload={"reason": "recipient routing not configured"},
        )

    title = render_message_template(template.title_template, render_context)
    message = render_message_template(template.message_template, render_context)
    dedupe_key = str(payload.get("dedupe_key") or "")
    escalated = _should_escalate(db, template=template, target_type=target_type, target_id=target_id, dedupe_key=dedupe_key)
    recipient_ids: set[str] = set()
    if permission_code:
        recipient_ids.update(repository.list_user_ids_by_permission(db, permission_code))
    if group_id:
        recipient_ids.update(repository.list_user_ids_by_recipient_group(db, group_id))
    if escalated and template.escalation_permission_code:
        recipient_ids.update(repository.list_user_ids_by_permission(db, template.escalation_permission_code))
    if escalated and template.escalation_group_id:
        recipient_ids.update(repository.list_user_ids_by_recipient_group(db, template.escalation_group_id))

    notification_payload = {
        **payload,
        "message_template_id": template.id,
        "message_channel": template.channel,
        "escalated": escalated,
    }
    for user_id in sorted(recipient_ids):
        repository.create_notification(
            db,
            user_id=user_id,
            event_type=template.event_type,
            title=title,
            message=message,
            target_type=target_type,
            target_id=target_id,
            payload=notification_payload,
            commit=False,
        )
    db.commit()

    return _record_dispatch(
        db,
        template_id=template.id,
        event_type=template.event_type,
        channel=template.channel,
        target_type=target_type,
        target_id=target_id,
        status="sent" if recipient_ids else "skipped",
        recipient_count=len(recipient_ids),
        notification_count=len(recipient_ids),
        payload={
            "title": title,
            "recipient_permission_code": permission_code,
            "recipient_group_id": group_id,
            "escalated": escalated,
            "escalation_permission_code": template.escalation_permission_code,
            "escalation_group_id": template.escalation_group_id,
        },
    )


def _dispatch_webhook_template(
    db: Session,
    *,
    template: schemas.MessageTemplateRead,
    render_context: dict[str, Any],
    target_type: str | None,
    target_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    http_transport: httpx.BaseTransport | None,
) -> schemas.MessageDispatchRecord:
    metadata = template.metadata or {}
    endpoint = metadata.get("webhook_url") or settings.external_alert_webhook_url
    title = render_message_template(template.title_template, render_context)
    message = render_message_template(template.message_template, render_context)
    dedupe_key = str(payload.get("dedupe_key") or "")
    dedupe_minutes = _int_metadata(metadata, "dedupe_minutes", default=0, minimum=0, maximum=1440)
    if dedupe_key and dedupe_minutes:
        cutoff = repository.utc_now() - timedelta(minutes=dedupe_minutes)
        if repository.recent_message_dispatch_exists(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            dedupe_key=dedupe_key,
            since=cutoff,
            target_type=target_type,
            target_id=target_id,
            statuses={"sent"},
        ):
            return _record_dispatch(
                db,
                template_id=template.id,
                event_type=template.event_type,
                channel=template.channel,
                target_type=target_type,
                target_id=target_id,
                status="skipped",
                recipient_count=0,
                payload={
                    "reason": "dedupe_window",
                    "dedupe_key": dedupe_key,
                    "dedupe_minutes": dedupe_minutes,
                    "title": title,
                    "webhook_provider": _webhook_provider(metadata),
                },
                external_push={
                    "status": "skipped",
                    "reason": "dedupe_window",
                    "dedupe_key": dedupe_key,
                    "webhook_provider": _webhook_provider(metadata),
                },
            )
    outbound_payload = _build_webhook_payload(
        template=template,
        metadata=metadata,
        title=title,
        message=message,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        render_context=render_context,
    )
    provider = _webhook_provider(metadata)
    if not endpoint:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=0,
            payload={
                "reason": "webhook endpoint not configured",
                "title": title,
                "dedupe_key": dedupe_key,
                "webhook_provider": provider,
            },
        )
    body = _json_bytes(outbound_payload)
    headers = _webhook_headers(template=template, metadata=metadata, body=body)
    retry_count = _int_metadata(metadata, "retry_count", default=0, minimum=0, maximum=5)
    attempts: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=max(1, settings.external_alert_webhook_timeout_sec), transport=http_transport) as client:
            for attempt in range(1, retry_count + 2):
                response = client.post(str(endpoint), content=body, headers=headers)
                if response.is_success:
                    attempts.append({"attempt": attempt, "status": "sent", "http_status_code": response.status_code})
                    return _record_dispatch(
                        db,
                        template_id=template.id,
                        event_type=template.event_type,
                        channel=template.channel,
                        target_type=target_type,
                        target_id=target_id,
                        status="sent",
                        recipient_count=1,
                        payload={
                            "http_status_code": response.status_code,
                            "title": title,
                            "dedupe_key": dedupe_key,
                            "webhook_provider": provider,
                            "attempt_count": attempt,
                            "attempts": attempts,
                            "signature": _signature_log(metadata),
                        },
                        external_push={
                            "status": "sent",
                            "http_status_code": response.status_code,
                            "attempt_count": attempt,
                            "webhook_provider": provider,
                        },
                    )
                attempts.append(
                    {
                        "attempt": attempt,
                        "status": "failed",
                        "http_status_code": response.status_code,
                        "error": response.text[:500],
                    }
                )
            last_error = attempts[-1]["error"] if attempts else "webhook delivery failed"
            raise RuntimeError(str(last_error))
    except Exception as exc:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="failed",
            recipient_count=1,
            payload={
                "title": title,
                "dedupe_key": dedupe_key,
                "webhook_provider": provider,
                "attempt_count": len(attempts) or 1,
                "attempts": attempts,
                "signature": _signature_log(metadata),
            },
            error=str(exc),
            external_push={
                "status": "failed",
                "error": str(exc),
                "attempt_count": len(attempts) or 1,
                "webhook_provider": provider,
            },
        )


def _dispatch_email_template(
    db: Session,
    *,
    template: schemas.MessageTemplateRead,
    render_context: dict[str, Any],
    target_type: str | None,
    target_id: str | None,
    fallback_permission_code: str | None,
    fallback_group_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
    email_sender: EmailSender | None,
) -> schemas.MessageDispatchRecord:
    metadata = template.metadata or {}
    permission_code = template.recipient_permission_code or fallback_permission_code
    group_id = template.recipient_group_id or fallback_group_id
    if not permission_code and not group_id:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=0,
            payload={"reason": "recipient routing not configured"},
        )

    title = render_message_template(template.title_template, render_context)
    message = render_message_template(template.message_template, render_context)
    dedupe_key = str(payload.get("dedupe_key") or "")
    dedupe_minutes = _int_metadata(metadata, "dedupe_minutes", default=0, minimum=0, maximum=1440)
    if dedupe_key and dedupe_minutes:
        cutoff = repository.utc_now() - timedelta(minutes=dedupe_minutes)
        if repository.recent_message_dispatch_exists(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            dedupe_key=dedupe_key,
            since=cutoff,
            target_type=target_type,
            target_id=target_id,
            statuses={"sent"},
        ):
            return _record_dispatch(
                db,
                template_id=template.id,
                event_type=template.event_type,
                channel=template.channel,
                target_type=target_type,
                target_id=target_id,
                status="skipped",
                recipient_count=0,
                payload={
                    "reason": "dedupe_window",
                    "dedupe_key": dedupe_key,
                    "dedupe_minutes": dedupe_minutes,
                    "title": title,
                },
                external_push={"status": "skipped", "reason": "dedupe_window", "dedupe_key": dedupe_key},
            )

    escalated = _should_escalate(db, template=template, target_type=target_type, target_id=target_id, dedupe_key=dedupe_key)
    recipient_ids: set[str] = set()
    if permission_code:
        recipient_ids.update(repository.list_user_ids_by_permission(db, permission_code))
    if group_id:
        recipient_ids.update(repository.list_user_ids_by_recipient_group(db, group_id))
    if escalated and template.escalation_permission_code:
        recipient_ids.update(repository.list_user_ids_by_permission(db, template.escalation_permission_code))
    if escalated and template.escalation_group_id:
        recipient_ids.update(repository.list_user_ids_by_recipient_group(db, template.escalation_group_id))
    recipient_emails = repository.list_active_user_emails_by_ids(db, sorted(recipient_ids))
    if not recipient_emails:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=0,
            payload={
                "reason": "no email recipients resolved",
                "recipient_permission_code": permission_code,
                "recipient_group_id": group_id,
                "title": title,
            },
        )
    if not _smtp_configured(settings):
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="skipped",
            recipient_count=len(recipient_emails),
            payload={
                "reason": "smtp not configured",
                "recipient_count": len(recipient_emails),
                "title": title,
                "dedupe_key": dedupe_key,
            },
            external_push={"status": "skipped", "reason": "smtp not configured", "recipient_count": len(recipient_emails)},
        )

    email_message = _build_email_message(
        template=template,
        metadata=metadata,
        title=title,
        message=message,
        target_id=target_id,
        recipients=recipient_emails,
        settings=settings,
    )
    sender = email_sender or _send_smtp_email
    try:
        sender(email_message, settings)
    except Exception as exc:
        return _record_dispatch(
            db,
            template_id=template.id,
            event_type=template.event_type,
            channel=template.channel,
            target_type=target_type,
            target_id=target_id,
            status="failed",
            recipient_count=len(recipient_emails),
            payload={
                "title": title,
                "dedupe_key": dedupe_key,
                "recipient_count": len(recipient_emails),
                "escalated": escalated,
                "smtp_host_configured": bool(settings.smtp_host),
            },
            error=str(exc),
            external_push={"status": "failed", "error": str(exc), "recipient_count": len(recipient_emails)},
        )
    return _record_dispatch(
        db,
        template_id=template.id,
        event_type=template.event_type,
        channel=template.channel,
        target_type=target_type,
        target_id=target_id,
        status="sent",
        recipient_count=len(recipient_emails),
        payload={
            "title": title,
            "dedupe_key": dedupe_key,
            "recipient_count": len(recipient_emails),
            "recipient_permission_code": permission_code,
            "recipient_group_id": group_id,
            "escalated": escalated,
            "smtp_host_configured": bool(settings.smtp_host),
            "subject": str(email_message["Subject"]),
        },
        external_push={"status": "sent", "recipient_count": len(recipient_emails), "subject": str(email_message["Subject"])},
    )


def _should_escalate(
    db: Session,
    *,
    template: schemas.MessageTemplateRead,
    target_type: str | None,
    target_id: str | None,
    dedupe_key: str,
) -> bool:
    if not template.escalation_after_minutes or not (template.escalation_permission_code or template.escalation_group_id):
        return False
    cutoff = repository.utc_now() - timedelta(minutes=template.escalation_after_minutes)
    return repository.unread_notification_exists(
        db,
        event_type=template.event_type,
        target_type=target_type,
        target_id=target_id,
        older_than=cutoff,
        dedupe_key=dedupe_key or None,
    )


def _record_dispatch(
    db: Session,
    *,
    template_id: str | None,
    event_type: str,
    channel: str,
    target_type: str | None,
    target_id: str | None,
    status: str,
    recipient_count: int,
    payload: dict[str, Any],
    notification_count: int = 0,
    error: str | None = None,
    external_push: dict[str, Any] | None = None,
) -> schemas.MessageDispatchRecord:
    repository.create_message_dispatch_log(
        db,
        template_id=template_id,
        event_type=event_type,
        channel=channel,
        target_type=target_type,
        target_id=target_id,
        status=status,
        recipient_count=recipient_count,
        payload={**payload, "notification_count": notification_count, "external_push": external_push},
        error=error,
    )
    return schemas.MessageDispatchRecord(
        template_id=template_id,
        event_type=event_type,
        channel=channel,
        status=status,
        recipient_count=recipient_count,
        notification_count=notification_count,
        external_push=external_push,
        error=error,
        payload=payload,
    )


def _resolve_path(context: dict[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if 0 <= index < len(value) else None
        else:
            return None
        if value is None:
            return None
    return value


def _build_webhook_payload(
    *,
    template: schemas.MessageTemplateRead,
    metadata: dict[str, Any],
    title: str,
    message: str,
    target_type: str | None,
    target_id: str | None,
    payload: dict[str, Any],
    render_context: dict[str, Any],
) -> dict[str, Any]:
    provider = _webhook_provider(metadata)
    if provider in {"feishu", "lark"}:
        return {
            "msg_type": "text",
            "content": {"text": _plain_notification_text(title, message, template.event_type, target_id)},
        }
    if provider in {"wecom", "wechat_work", "enterprise_wechat"}:
        message_type = str(metadata.get("webhook_message_type") or "markdown").lower()
        content = _plain_notification_text(title, message, template.event_type, target_id)
        if message_type == "text":
            return {"msgtype": "text", "text": {"content": content}}
        return {"msgtype": "markdown", "markdown": {"content": _markdown_notification_text(title, message, template.event_type, target_id)}}
    return {
        "event_type": template.event_type,
        "source": "packaging_nesting",
        "title": title,
        "message": message,
        "target_type": target_type,
        "target_id": target_id,
        "payload": payload,
        "context": render_context,
    }


def _webhook_provider(metadata: dict[str, Any]) -> str:
    provider = metadata.get("webhook_provider") or metadata.get("provider") or "generic"
    return str(provider).strip().lower()


def _plain_notification_text(title: str, message: str, event_type: str, target_id: str | None) -> str:
    lines = [title, message, f"事件: {event_type}"]
    if target_id:
        lines.append(f"目标: {target_id}")
    return "\n".join(line for line in lines if line)


def _markdown_notification_text(title: str, message: str, event_type: str, target_id: str | None) -> str:
    lines = [f"**{title}**", message, f">事件: {event_type}"]
    if target_id:
        lines.append(f">目标: {target_id}")
    return "\n".join(line for line in lines if line)


def _smtp_configured(settings: Settings) -> bool:
    return bool((settings.smtp_host or "").strip() and (settings.smtp_from_email or "").strip())


def _build_email_message(
    *,
    template: schemas.MessageTemplateRead,
    metadata: dict[str, Any],
    title: str,
    message: str,
    target_id: str | None,
    recipients: list[str],
    settings: Settings,
) -> EmailMessage:
    subject_prefix = str(metadata.get("email_subject_prefix") or "")
    email_message = EmailMessage()
    email_message["From"] = str(settings.smtp_from_email or "")
    email_message["To"] = ", ".join(recipients)
    email_message["Subject"] = f"{subject_prefix}{title}"
    email_message["X-Packaging-Event"] = template.event_type
    reply_to = metadata.get("email_reply_to")
    if reply_to:
        email_message["Reply-To"] = str(reply_to)
    email_message.set_content(_plain_notification_text(title, message, template.event_type, target_id))
    return email_message


def _send_smtp_email(message: EmailMessage, settings: Settings) -> None:
    host = str(settings.smtp_host or "").strip()
    with smtplib.SMTP(host, settings.smtp_port, timeout=max(1, settings.smtp_timeout_sec)) as client:
        if settings.smtp_use_tls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password or "")
        client.send_message(message)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _webhook_headers(
    *,
    template: schemas.MessageTemplateRead,
    metadata: dict[str, Any],
    body: bytes,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Packaging-Event": template.event_type,
    }
    secret = str(metadata.get("signature_secret") or metadata.get("webhook_secret") or "")
    if not secret:
        return headers
    algorithm = str(metadata.get("signature_algorithm") or "sha256").lower()
    if algorithm != "sha256":
        algorithm = "sha256"
    timestamp = repository.utc_now().isoformat()
    signed_body = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed_body, hashlib.sha256).hexdigest()
    signature_header = str(metadata.get("signature_header") or WEBHOOK_SIGNATURE_HEADER)
    timestamp_header = str(metadata.get("signature_timestamp_header") or WEBHOOK_SIGNATURE_TIMESTAMP_HEADER)
    headers[timestamp_header] = timestamp
    headers[signature_header] = f"{algorithm}={digest}"
    return headers


def _signature_log(metadata: dict[str, Any]) -> dict[str, Any]:
    secret = metadata.get("signature_secret") or metadata.get("webhook_secret")
    if not secret:
        return {"enabled": False}
    return {
        "enabled": True,
        "algorithm": "sha256",
        "signature_header": str(metadata.get("signature_header") or WEBHOOK_SIGNATURE_HEADER),
        "timestamp_header": str(metadata.get("signature_timestamp_header") or WEBHOOK_SIGNATURE_TIMESTAMP_HEADER),
    }


def _int_metadata(
    metadata: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(metadata.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)
