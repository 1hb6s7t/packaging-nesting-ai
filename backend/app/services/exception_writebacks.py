from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.domain import schemas
from app.services import repository
from app.services.adapters import writeback_adapter_result
from app.services.procurement_alerts import build_procurement_recommendations


def run_job_exception_writebacks(
    db: Session,
    job_id: str,
    request: schemas.JobExceptionWritebackRequest,
) -> schemas.JobExceptionWritebackResult | None:
    readiness = repository.evaluate_job_production_readiness(db, job_id)
    if readiness is None:
        return None
    procurement_recommendations = build_procurement_recommendations(
        readiness.material or schemas.MaterialAvailabilityCheckResult(
            job_id=job_id,
            overall_status="unknown",
            checked_at=repository.utc_now().isoformat(),
        ),
        override=schemas.ProcurementAlertRuleOverride(
            notify=False,
            safety_stock_rate=request.safety_stock_rate,
            min_purchase_qty=request.min_purchase_qty,
        ),
    )
    actions: list[schemas.JobExceptionWritebackAction] = []
    if request.include_procurement and procurement_recommendations:
        actions.append(
            _run_writeback_action(
                db,
                system_type="erp",
                target_id=job_id,
                target_type="procurement_request",
                requested_status="material_shortage",
                reason="material_shortage",
                dry_run=request.dry_run,
                payload={
                    "job_id": job_id,
                    "recommendations": [item.model_dump(mode="json") for item in procurement_recommendations],
                    "material_readiness": readiness.material.model_dump(mode="json") if readiness.material else None,
                },
            )
        )
    if request.include_schedule and readiness.schedule_status in {"blocked", "missing", "unknown"}:
        actions.append(
            _run_writeback_action(
                db,
                system_type="mes",
                target_id=job_id,
                target_type="nesting_job",
                requested_status="schedule_blocked" if readiness.schedule_status == "blocked" else "schedule_exception",
                reason=f"schedule_{readiness.schedule_status}",
                dry_run=request.dry_run,
                payload={
                    "job_id": job_id,
                    "schedule_status": readiness.schedule_status,
                    "schedule_items": [item.model_dump(mode="json") for item in readiness.schedule_items],
                    "warnings": readiness.warnings,
                },
            )
        )
    if request.include_delivery and readiness.delivery_status in {"blocked", "partial", "missing", "unknown"}:
        actions.append(
            _run_writeback_action(
                db,
                system_type="erp",
                target_id=job_id,
                target_type="delivery_closure",
                requested_status="delivery_blocked" if readiness.delivery_status == "blocked" else "delivery_incomplete",
                reason=f"delivery_{readiness.delivery_status}",
                dry_run=request.dry_run,
                payload={
                    "job_id": job_id,
                    "delivery_status": readiness.delivery_status,
                    "delivery_items": [item.model_dump(mode="json") for item in readiness.delivery_items],
                    "warnings": readiness.warnings,
                },
            )
        )
    failed_count = sum(1 for action in actions if action.writeback_log.status == "failed")
    skipped_count = sum(1 for action in actions if action.writeback_log.status == "skipped")
    completed_count = sum(1 for action in actions if action.writeback_log.status == "completed")
    if not actions:
        status = "ok"
    elif failed_count:
        status = "failed" if failed_count == len(actions) else "partial"
    elif skipped_count:
        status = "skipped" if skipped_count == len(actions) else "partial"
    else:
        status = "completed"
    return schemas.JobExceptionWritebackResult(
        job_id=job_id,
        dry_run=request.dry_run,
        status=status,
        action_count=len(actions),
        writeback_count=completed_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        readiness=readiness,
        procurement_recommendations=procurement_recommendations,
        actions=actions,
    )


def _run_writeback_action(
    db: Session,
    *,
    system_type: str,
    target_id: str,
    target_type: str,
    requested_status: str,
    reason: str,
    dry_run: bool,
    payload: dict[str, Any],
) -> schemas.JobExceptionWritebackAction:
    active = repository.get_active_adapter_config_for_system_type(db, system_type)
    if active is None:
        log = repository.create_writeback_log(
            db,
            external_system_id=None,
            target_id=target_id,
            status="skipped",
            payload={
                "reason": f"No enabled {system_type.upper()} system with an active adapter config",
                "system_type": system_type,
                "target_type": target_type,
                "requested_status": requested_status,
                "dry_run": dry_run,
                "request_payload": payload,
            },
        )
    else:
        system, config = active
        writeback_payload = writeback_adapter_result(
            db,
            system=system,
            config=config,
            target_id=target_id,
            target_type=target_type,
            status=requested_status,
            payload=payload,
            dry_run_override=dry_run,
        )
        log = repository.create_writeback_log(
            db,
            external_system_id=system.id,
            target_id=target_id,
            status=writeback_payload["status"],
            payload=writeback_payload,
        )
    return schemas.JobExceptionWritebackAction(
        system_type=system_type,
        target_type=target_type,
        requested_status=requested_status,
        reason=reason,
        writeback_log=log,
    )
