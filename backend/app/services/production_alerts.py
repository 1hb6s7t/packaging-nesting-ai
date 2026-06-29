from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as dbm
from app.domain import schemas
from app.services import repository


def check_job_production_alerts(
    db: Session,
    job_id: str,
    *,
    settings: Settings,
    override: schemas.ProductionAlertRuleOverride | None = None,
) -> schemas.ProductionAlertCheckResult | None:
    readiness = repository.evaluate_job_production_readiness(db, job_id)
    if readiness is None:
        return None
    alerts = evaluate_job_production_alerts(readiness)
    notification_count = 0
    if alerts and (override is None or override.notify):
        dedupe_minutes = override.dedupe_minutes if override and override.dedupe_minutes else settings.task_alert_dedupe_minutes
        notification_count = _create_production_alert_notifications(db, readiness, alerts, max(1, dedupe_minutes))
    return schemas.ProductionAlertCheckResult(
        status="alerting" if alerts else "ok",
        readiness=readiness,
        alerts=alerts,
        notification_count=notification_count,
    )


def evaluate_job_production_alerts(readiness: schemas.JobProductionReadinessResult) -> list[schemas.ProductionAlertRead]:
    alerts: list[schemas.ProductionAlertRead] = []
    if readiness.material_status == "blocked":
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.material_blocked",
                severity="critical",
                message=f"job {readiness.job_id} has material shortage",
                status=readiness.material_status,
                affected_order_ids=_material_alert_order_ids(readiness, {"shortage"}),
            )
        )
    elif readiness.material_status == "unknown":
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.material_unknown",
                severity="warning",
                message=f"job {readiness.job_id} is missing material availability evidence",
                status=readiness.material_status,
                affected_order_ids=_material_alert_order_ids(readiness, {"unknown"}),
            )
        )

    if readiness.schedule_status == "blocked":
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.schedule_blocked",
                severity="critical",
                message=f"job {readiness.job_id} has blocked MES schedule entries",
                status=readiness.schedule_status,
                affected_order_ids=[item.order_id for item in readiness.schedule_items if item.status == "blocked"],
            )
        )
    elif readiness.schedule_status in {"missing", "unknown"}:
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.schedule_missing",
                severity="warning",
                message=f"job {readiness.job_id} is missing usable MES schedule evidence",
                status=readiness.schedule_status,
                affected_order_ids=[
                    item.order_id for item in readiness.schedule_items if item.status in {"missing", "unknown"}
                ],
            )
        )

    if readiness.delivery_status == "blocked":
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.delivery_blocked",
                severity="critical",
                message=f"job {readiness.job_id} has blocked delivery confirmations",
                status=readiness.delivery_status,
                affected_order_ids=[item.order_id for item in readiness.delivery_items if item.status == "blocked"],
            )
        )
    elif readiness.delivery_status in {"partial", "missing", "unknown"}:
        alerts.append(
            schemas.ProductionAlertRead(
                code="production.delivery_incomplete",
                severity="warning",
                message=f"job {readiness.job_id} delivery closure is incomplete",
                status=readiness.delivery_status,
                affected_order_ids=[
                    item.order_id for item in readiness.delivery_items if item.status in {"partial", "missing", "unknown"}
                ],
            )
        )
    return alerts


def _material_alert_order_ids(readiness: schemas.JobProductionReadinessResult, statuses: set[str]) -> list[str]:
    if readiness.material is None:
        return []
    order_ids: set[str] = set()
    for item in readiness.material.items:
        if item.status in statuses:
            order_ids.update(item.order_ids)
    order_ids.update(readiness.material.missing_order_ids)
    return sorted(order_ids)


def _create_production_alert_notifications(
    db: Session,
    readiness: schemas.JobProductionReadinessResult,
    alerts: list[schemas.ProductionAlertRead],
    dedupe_minutes: int,
) -> int:
    created_count = 0
    cooldown_after = repository.utc_now() - timedelta(minutes=dedupe_minutes)
    for alert in alerts:
        dedupe_key = _production_alert_dedupe_key(readiness.job_id, alert)
        if _recent_production_alert_notification_exists(db, readiness.job_id, dedupe_key, cooldown_after):
            continue
        notifications = repository.create_permission_notifications(
            db,
            permission_code="nesting:write",
            event_type=alert.code,
            title="生产异常告警",
            message=alert.message,
            target_type="nesting_job",
            target_id=readiness.job_id,
            payload={
                "dedupe_key": dedupe_key,
                "alert": alert.model_dump(mode="json"),
                "readiness": readiness.model_dump(mode="json"),
            },
        )
        created_count += len(notifications)
    return created_count


def _production_alert_dedupe_key(job_id: str, alert: schemas.ProductionAlertRead) -> str:
    order_key = ",".join(sorted(alert.affected_order_ids))
    return f"{job_id}:{alert.code}:{alert.status}:{order_key}"


def _recent_production_alert_notification_exists(db: Session, job_id: str, dedupe_key: str, cooldown_after) -> bool:
    rows = db.scalars(
        select(dbm.Notification)
        .where(
            dbm.Notification.target_type == "nesting_job",
            dbm.Notification.target_id == job_id,
            dbm.Notification.created_at >= cooldown_after,
        )
        .order_by(dbm.Notification.created_at.desc())
        .limit(100)
    ).all()
    return any((row.payload or {}).get("dedupe_key") == dedupe_key for row in rows)
