from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import (
    CurrentUser,
    JobExceptionWritebackRequest,
    JobExceptionWritebackResult,
    JobProductionReadinessResult,
    MaterialAvailabilityCheckResult,
    NestingJob,
    ProcurementAlertCheckResult,
    ProcurementAlertRuleOverride,
    ProductionAlertCheckResult,
    ProductionAlertRuleOverride,
    SolutionList,
    WorkTaskRead,
)
from app.services import repository
from app.services.exception_writebacks import run_job_exception_writebacks
from app.services.procurement_alerts import check_job_procurement_alerts
from app.services.production_alerts import check_job_production_alerts
from app.services.security import get_current_user, require_permission
from app.services.store import store
from app.services.task_dispatch import dispatch_work_task
from app.services.workflows import run_nesting_job

router = APIRouter()


@router.post("/jobs", response_model=NestingJob)
def create_job(
    job: NestingJob,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> NestingJob:
    store.jobs[job.job_id] = job
    repository.upsert_job(db, job)
    repository.log_operation(
        db,
        action="nesting_job.create",
        target_type="nesting_job",
        target_id=job.job_id,
        actor_id=current_user.user_id,
        payload={"candidate_count": len(job.candidate_items), "top_k": job.top_k},
    )
    return job


@router.get("/jobs", response_model=list[NestingJob])
def list_jobs(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[NestingJob]:
    jobs = repository.list_jobs(db)
    if jobs:
        return jobs
    return list(store.jobs.values())


@router.get("/jobs/{job_id}", response_model=NestingJob)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NestingJob:
    job = repository.get_job(db, job_id) or store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/jobs/{job_id}/material-readiness", response_model=MaterialAvailabilityCheckResult)
def get_job_material_readiness(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> MaterialAvailabilityCheckResult:
    result = repository.evaluate_job_material_availability(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.material_readiness_check",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={
            "overall_status": result.overall_status,
            "item_count": len(result.items),
            "missing_order_count": len(result.missing_order_ids),
        },
    )
    return result


@router.get("/jobs/{job_id}/production-readiness", response_model=JobProductionReadinessResult)
def get_job_production_readiness(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> JobProductionReadinessResult:
    result = repository.evaluate_job_production_readiness(db, job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.production_readiness_check",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={
            "overall_status": result.overall_status,
            "material_status": result.material_status,
            "schedule_status": result.schedule_status,
            "delivery_status": result.delivery_status,
            "order_count": result.order_count,
        },
    )
    return result


@router.post("/jobs/{job_id}/procurement-alerts/check", response_model=ProcurementAlertCheckResult)
def check_job_procurement_alerts_route(
    job_id: str,
    payload: ProcurementAlertRuleOverride | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> ProcurementAlertCheckResult:
    result = check_job_procurement_alerts(db, job_id, settings=get_settings(), override=payload)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.procurement_alerts_check",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={
            "status": result.status,
            "recommendation_count": len(result.recommendations),
            "notification_count": result.notification_count,
            "materials": [item.material for item in result.recommendations],
        },
    )
    return result


@router.post("/jobs/{job_id}/exception-writebacks/run", response_model=JobExceptionWritebackResult)
def run_job_exception_writebacks_route(
    job_id: str,
    payload: JobExceptionWritebackRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> JobExceptionWritebackResult:
    request = payload or JobExceptionWritebackRequest()
    result = run_job_exception_writebacks(db, job_id, request)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.exception_writebacks_run",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={
            "status": result.status,
            "dry_run": result.dry_run,
            "action_count": result.action_count,
            "writeback_count": result.writeback_count,
            "skipped_count": result.skipped_count,
            "failed_count": result.failed_count,
            "requested_statuses": [action.requested_status for action in result.actions],
        },
    )
    return result


@router.post("/jobs/{job_id}/production-alerts/check", response_model=ProductionAlertCheckResult)
def check_job_production_alerts_route(
    job_id: str,
    payload: ProductionAlertRuleOverride | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> ProductionAlertCheckResult:
    result = check_job_production_alerts(db, job_id, settings=get_settings(), override=payload)
    if result is None:
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.production_alerts_check",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={
            "status": result.status,
            "alert_count": len(result.alerts),
            "notification_count": result.notification_count,
            "alert_codes": [alert.code for alert in result.alerts],
        },
    )
    return result


@router.post("/jobs/{job_id}/run", response_model=SolutionList)
def run_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> SolutionList:
    try:
        return run_nesting_job(db, job_id, actor_id=current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/run-async", response_model=WorkTaskRead)
def run_job_async(
    job_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> WorkTaskRead:
    job = repository.get_job(db, job_id) or store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    task = repository.create_work_task(
        db,
        task_type="nesting.solve",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={"job_id": job_id},
        timeout_sec=job.solver_config.time_limit_sec + 30,
    )
    repository.log_operation(
        db,
        action="nesting_job.run_queued",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload={"task_id": task.id},
    )
    dispatch_work_task(task.id, background_tasks)
    return task


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("nesting:write")),
) -> dict:
    if not (repository.get_job(db, job_id) or store.jobs.get(job_id)):
        raise HTTPException(status_code=404, detail="job not found")
    repository.log_operation(
        db,
        action="nesting_job.cancel",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=current_user.user_id,
    )
    return {"job_id": job_id, "status": "cancel_requested"}


@router.get("/jobs/{job_id}/solutions", response_model=SolutionList)
def get_job_solutions(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SolutionList:
    if not (repository.get_job(db, job_id) or store.jobs.get(job_id)):
        raise HTTPException(status_code=404, detail="job not found")
    solutions = repository.list_job_solutions(db, job_id) or [store.solutions[sid] for sid in store.job_solutions.get(job_id, [])]
    return SolutionList(job_id=job_id, solutions=solutions)


@router.get("/jobs/{job_id}/runs")
def get_job_solver_runs(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    if not (repository.get_job(db, job_id) or store.jobs.get(job_id)):
        raise HTTPException(status_code=404, detail="job not found")
    return repository.list_solver_runs(db, job_id=job_id)


@router.get("/runs")
def get_solver_runs(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    return repository.list_solver_runs(db)


@router.get("/runs/{run_id}/logs")
def get_solver_run_logs(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    logs = repository.list_solver_run_logs(db, run_id)
    if not logs and not any(row["id"] == run_id for row in repository.list_solver_runs(db)):
        raise HTTPException(status_code=404, detail="solver run not found")
    return logs
