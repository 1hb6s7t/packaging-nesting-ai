from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db import models as dbm
from app.domain import schemas
from app.services import repository


def check_job_procurement_alerts(
    db: Session,
    job_id: str,
    *,
    settings: Settings,
    override: schemas.ProcurementAlertRuleOverride | None = None,
) -> schemas.ProcurementAlertCheckResult | None:
    material_readiness = repository.evaluate_job_material_availability(db, job_id)
    if material_readiness is None:
        return None
    recommendations = build_procurement_recommendations(material_readiness, override=override)
    notification_count = 0
    if recommendations and (override is None or override.notify):
        dedupe_minutes = override.dedupe_minutes if override and override.dedupe_minutes else settings.task_alert_dedupe_minutes
        notification_count = _create_procurement_notifications(
            db,
            material_readiness,
            recommendations,
            max(1, dedupe_minutes),
        )
    return schemas.ProcurementAlertCheckResult(
        status="alerting" if recommendations else "ok",
        material_readiness=material_readiness,
        recommendations=recommendations,
        notification_count=notification_count,
    )


def build_procurement_recommendations(
    material_readiness: schemas.MaterialAvailabilityCheckResult,
    *,
    override: schemas.ProcurementAlertRuleOverride | None = None,
) -> list[schemas.ProcurementRecommendationRead]:
    safety_stock_rate = override.safety_stock_rate if override else 0
    min_purchase_qty = override.min_purchase_qty if override else 0
    recommendations: list[schemas.ProcurementRecommendationRead] = []
    for item in material_readiness.items:
        if item.status != "shortage" or item.shortage_qty <= 0:
            continue
        recommended_qty = item.shortage_qty * (1 + safety_stock_rate)
        if min_purchase_qty:
            recommended_qty = max(recommended_qty, min_purchase_qty)
        severity = "critical" if item.net_available_qty <= 0 else "warning"
        recommendations.append(
            schemas.ProcurementRecommendationRead(
                material=item.material,
                shortage_qty=item.shortage_qty,
                recommended_purchase_qty=round(recommended_qty, 3),
                unit=item.unit,
                severity=severity,
                order_ids=item.order_ids,
                inventory_snapshot_ids=item.inventory_snapshot_ids,
            )
        )
    return recommendations


def _create_procurement_notifications(
    db: Session,
    material_readiness: schemas.MaterialAvailabilityCheckResult,
    recommendations: list[schemas.ProcurementRecommendationRead],
    dedupe_minutes: int,
) -> int:
    created_count = 0
    cooldown_after = repository.utc_now() - timedelta(minutes=dedupe_minutes)
    user_ids = sorted(
        set(repository.list_user_ids_by_permission(db, "nesting:write"))
        | set(repository.list_user_ids_by_permission(db, "integrations:write"))
    )
    for recommendation in recommendations:
        dedupe_key = _procurement_dedupe_key(material_readiness.job_id, recommendation)
        if _recent_procurement_notification_exists(db, material_readiness.job_id, dedupe_key, cooldown_after):
            continue
        for user_id in user_ids:
            repository.create_notification(
                db,
                user_id=user_id,
                event_type="procurement.material_shortage",
                title="物料采购预警",
                message=(
                    f"Job {material_readiness.job_id} 物料 {recommendation.material} "
                    f"缺口 {recommendation.shortage_qty:g}"
                ),
                target_type="nesting_job",
                target_id=material_readiness.job_id,
                payload={
                    "dedupe_key": dedupe_key,
                    "recommendation": recommendation.model_dump(mode="json"),
                    "material_readiness": material_readiness.model_dump(mode="json"),
                },
                commit=False,
            )
            created_count += 1
        db.commit()
    return created_count


def _procurement_dedupe_key(job_id: str, recommendation: schemas.ProcurementRecommendationRead) -> str:
    order_key = ",".join(sorted(recommendation.order_ids))
    return f"{job_id}:{recommendation.material}:{recommendation.shortage_qty:g}:{order_key}"


def _recent_procurement_notification_exists(db: Session, job_id: str, dedupe_key: str, cooldown_after) -> bool:
    rows = db.scalars(
        select(dbm.Notification)
        .where(
            dbm.Notification.event_type == "procurement.material_shortage",
            dbm.Notification.target_type == "nesting_job",
            dbm.Notification.target_id == job_id,
            dbm.Notification.created_at >= cooldown_after,
        )
        .order_by(dbm.Notification.created_at.desc())
        .limit(100)
    ).all()
    return any((row.payload or {}).get("dedupe_key") == dedupe_key for row in rows)
