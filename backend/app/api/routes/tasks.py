from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import (
    ConfirmationRequest,
    CurrentUser,
    ScheduledMaintenanceRunRequest,
    ScheduledMaintenanceRunResult,
    TaskAlertCheckResult,
    TaskAlertRuleOverride,
    WorkTaskMetrics,
    WorkTaskRead,
)
from app.services import repository
from app.services.alerts import check_work_task_alerts
from app.services.confirmations import check_confirmation, task_confirmation_phrase
from app.services.maintenance import run_scheduled_maintenance
from app.services.security import require_permission
from app.services.task_dispatch import dispatch_work_task

router = APIRouter()


@router.get("", response_model=list[WorkTaskRead])
def list_tasks(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> list[WorkTaskRead]:
    return repository.list_work_tasks(db, status=status, limit=limit)


@router.get("/metrics", response_model=WorkTaskMetrics)
def get_task_metrics(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> WorkTaskMetrics:
    return repository.get_work_task_metrics(db, get_settings().task_stale_after_sec)


@router.post("/alerts/check", response_model=TaskAlertCheckResult)
def check_task_alerts(
    payload: TaskAlertRuleOverride | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> TaskAlertCheckResult:
    return check_work_task_alerts(db, settings=get_settings(), override=payload)


@router.get("/maintenance/schedule")
def get_maintenance_schedule(
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> dict:
    settings = get_settings()
    return {
        "enabled": settings.maintenance_scheduler_enabled,
        "interval_minutes": settings.maintenance_interval_minutes,
        "checks": {
            "archive_expired_exports": settings.maintenance_archive_expired_exports,
            "conversion_sla_check": settings.maintenance_conversion_sla_check,
            "task_alert_check": settings.maintenance_task_alert_check,
        },
    }


@router.post("/maintenance/run", response_model=ScheduledMaintenanceRunResult)
def run_maintenance(
    payload: ScheduledMaintenanceRunRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("tasks:manage")),
) -> ScheduledMaintenanceRunResult:
    return run_scheduled_maintenance(
        db,
        settings=get_settings(),
        request=payload or ScheduledMaintenanceRunRequest(),
        actor_id=current_user.user_id,
    )


@router.get("/{task_id}", response_model=WorkTaskRead)
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> WorkTaskRead:
    task = repository.get_work_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/{task_id}/cancel", response_model=WorkTaskRead)
def cancel_task(
    task_id: str,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("tasks:manage")),
) -> WorkTaskRead:
    _require_confirmation(payload.confirmation if payload else None, task_confirmation_phrase(task_id, "cancel"))
    task = repository.request_cancel_work_task(db, task_id, actor_id=current_user.user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.post("/{task_id}/retry", response_model=WorkTaskRead)
def retry_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("tasks:manage")),
) -> WorkTaskRead:
    _require_confirmation(payload.confirmation if payload else None, task_confirmation_phrase(task_id, "retry"))
    try:
        retry = repository.retry_work_task(db, task_id, actor_id=current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if retry is None:
        raise HTTPException(status_code=404, detail="task not found")
    dispatch_work_task(retry.id, background_tasks)
    return retry


def _require_confirmation(actual: str | None, expected: str) -> None:
    try:
        check_confirmation(actual, expected)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
