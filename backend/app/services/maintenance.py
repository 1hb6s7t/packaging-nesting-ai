from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain import schemas
from app.services import repository
from app.services.alerts import check_work_task_alerts
from app.services.file_conversion import check_file_conversion_sla
from app.services.workflows import archive_expired_solution_exports


def build_default_maintenance_request(settings: Settings | None = None) -> schemas.ScheduledMaintenanceRunRequest:
    config = settings or get_settings()
    return schemas.ScheduledMaintenanceRunRequest(
        archive_expired_exports=config.maintenance_archive_expired_exports,
        archive_dry_run=False,
        conversion_sla_check=config.maintenance_conversion_sla_check,
        conversion_sla_notify=True,
        task_alert_check=config.maintenance_task_alert_check,
        task_alert_notify=True,
        task_alert_push_external=bool(config.external_alert_webhook_url),
    )


def run_scheduled_maintenance(
    db: Session,
    *,
    settings: Settings | None = None,
    request: schemas.ScheduledMaintenanceRunRequest | None = None,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> schemas.ScheduledMaintenanceRunResult:
    config = settings or get_settings()
    run_request = request or build_default_maintenance_request(config)
    generated_at = repository.utc_now().isoformat()
    enabled_checks: list[str] = []
    export_archive = None
    conversion_sla = None
    task_alerts = None

    if run_request.archive_expired_exports:
        enabled_checks.append("export_archive")
        export_archive = schemas.SolutionExportArchiveResult.model_validate(
            archive_expired_solution_exports(
                db,
                dry_run=run_request.archive_dry_run,
                actor_id=actor_id,
                task_id=task_id,
            )
        )

    if run_request.conversion_sla_check:
        enabled_checks.append("conversion_sla")
        conversion_sla = check_file_conversion_sla(
            db,
            request=schemas.FileConversionSlaCheckRequest(notify=run_request.conversion_sla_notify),
        )

    if run_request.task_alert_check:
        enabled_checks.append("task_alerts")
        task_alerts = check_work_task_alerts(
            db,
            settings=config,
            override=schemas.TaskAlertRuleOverride(
                notify=run_request.task_alert_notify,
                push_external=run_request.task_alert_push_external,
            ),
        )

    attention = (conversion_sla is not None and conversion_sla.status == "overdue") or (
        task_alerts is not None and task_alerts.status == "alerting"
    )
    result = schemas.ScheduledMaintenanceRunResult(
        status="attention" if attention else "ok",
        generated_at=generated_at,
        enabled_checks=enabled_checks,
        export_archive=export_archive,
        conversion_sla=conversion_sla,
        task_alerts=task_alerts,
    )
    repository.log_operation(
        db,
        action="maintenance.run",
        target_type="maintenance",
        target_id=task_id or "manual",
        actor_id=actor_id,
        payload=result.model_dump(mode="json"),
    )
    return result
