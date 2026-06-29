from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as dbm
from app.domain import schemas
from app.services.messaging import dispatch_message_event
from app.services import repository


def check_work_task_alerts(
    db: Session,
    *,
    settings: Settings,
    override: schemas.TaskAlertRuleOverride | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> schemas.TaskAlertCheckResult:
    metrics = repository.get_work_task_metrics(db, settings.task_stale_after_sec)
    alerts = evaluate_work_task_alerts(metrics, settings=settings, override=override)
    notification_count = 0
    external_push = None
    if alerts and (override is None or override.notify):
        notification_count = _create_task_alert_notifications(db, metrics, alerts, settings)
    if alerts and (override is None or override.push_external):
        external_push = _push_external_task_alert(metrics, alerts, settings, http_transport=http_transport)
    return schemas.TaskAlertCheckResult(
        status="alerting" if alerts else "ok",
        metrics=metrics,
        alerts=alerts,
        notification_count=notification_count,
        external_push=external_push,
    )


def evaluate_work_task_alerts(
    metrics: schemas.WorkTaskMetrics,
    *,
    settings: Settings,
    override: schemas.TaskAlertRuleOverride | None = None,
) -> list[schemas.TaskAlertRead]:
    active_threshold = _threshold(override.active_threshold if override else None, settings.task_alert_active_threshold)
    queued_threshold = _threshold(override.queued_threshold if override else None, settings.task_alert_queued_threshold)
    stale_threshold = _threshold(
        override.stale_running_threshold if override else None,
        settings.task_alert_stale_running_threshold,
    )
    failure_threshold = _threshold(override.failure_threshold if override else None, settings.task_alert_failure_threshold)
    alerts: list[schemas.TaskAlertRead] = []
    if metrics.active >= active_threshold:
        alerts.append(
            schemas.TaskAlertRead(
                code="work_task.active_high",
                severity="warning",
                message=f"active work tasks reached {metrics.active}",
                actual=metrics.active,
                threshold=active_threshold,
            )
        )
    if metrics.queued >= queued_threshold:
        alerts.append(
            schemas.TaskAlertRead(
                code="work_task.queued_high",
                severity="warning",
                message=f"queued work tasks reached {metrics.queued}",
                actual=metrics.queued,
                threshold=queued_threshold,
            )
        )
    if metrics.stale_running >= stale_threshold:
        alerts.append(
            schemas.TaskAlertRead(
                code="work_task.stale_running",
                severity="critical",
                message=f"stale running work tasks reached {metrics.stale_running}",
                actual=metrics.stale_running,
                threshold=stale_threshold,
            )
        )
    failed_or_timed_out = metrics.failed + metrics.timed_out
    if failed_or_timed_out >= failure_threshold:
        alerts.append(
            schemas.TaskAlertRead(
                code="work_task.failure_high",
                severity="critical",
                message=f"failed or timed-out work tasks reached {failed_or_timed_out}",
                actual=failed_or_timed_out,
                threshold=failure_threshold,
            )
        )
    return alerts


def _threshold(override_value: int | None, configured_value: int) -> int:
    return max(1, override_value if override_value is not None else configured_value)


def _create_task_alert_notifications(
    db: Session,
    metrics: schemas.WorkTaskMetrics,
    alerts: list[schemas.TaskAlertRead],
    settings: Settings,
) -> int:
    created_count = 0
    cooldown_after = repository.utc_now() - timedelta(minutes=max(1, settings.task_alert_dedupe_minutes))
    for alert in alerts:
        dedupe_key = f"{alert.code}:{alert.threshold}:{alert.actual}"
        if _recent_alert_notification_exists(db, dedupe_key, cooldown_after):
            continue
        dispatch = dispatch_message_event(
            db,
            event_type=alert.code,
            default_title="Work task queue alert",
            default_message=alert.message,
            target_type="work_task_metrics",
            target_id=alert.code,
            recipient_permission_code="audit:read",
            payload={
                "dedupe_key": dedupe_key,
                "alert": alert.model_dump(mode="json"),
                "metrics": metrics.model_dump(mode="json"),
            },
            channel_filter={"in_app"},
            settings=settings,
        )
        created_count += dispatch.notification_count
    return created_count


def _recent_alert_notification_exists(db: Session, dedupe_key: str, cooldown_after) -> bool:
    row = db.scalar(
        select(dbm.Notification)
        .where(
            dbm.Notification.target_type == "work_task_metrics",
            dbm.Notification.created_at >= cooldown_after,
        )
        .order_by(dbm.Notification.created_at.desc())
    )
    if row is not None and (row.payload or {}).get("dedupe_key") == dedupe_key:
        return True
    rows = db.scalars(
        select(dbm.Notification)
        .where(
            dbm.Notification.target_type == "work_task_metrics",
            dbm.Notification.created_at >= cooldown_after,
        )
        .order_by(dbm.Notification.created_at.desc())
        .limit(50)
    ).all()
    return any((row.payload or {}).get("dedupe_key") == dedupe_key for row in rows)


def _push_external_task_alert(
    metrics: schemas.WorkTaskMetrics,
    alerts: list[schemas.TaskAlertRead],
    settings: Settings,
    *,
    http_transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    if not settings.external_alert_webhook_url:
        return {"status": "skipped", "reason": "EXTERNAL_ALERT_WEBHOOK_URL not configured"}
    payload = {
        "event_type": "work_task.alert",
        "source": "packaging_nesting",
        "status": "alerting",
        "alerts": [alert.model_dump(mode="json") for alert in alerts],
        "metrics": metrics.model_dump(mode="json"),
    }
    try:
        with httpx.Client(timeout=max(1, settings.external_alert_webhook_timeout_sec), transport=http_transport) as client:
            response = client.post(settings.external_alert_webhook_url, json=payload)
            response.raise_for_status()
        return {"status": "sent", "http_status_code": response.status_code}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
