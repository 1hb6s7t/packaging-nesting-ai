from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import (
    ApprovalDecisionRequest,
    ApprovalRequestCreate,
    ConfirmationRequest,
    CurrentUser,
    NestingSolution,
    SolutionApprovalRead,
    SolutionExportArchiveRequest,
    SolutionExportArchiveResult,
    SolutionExportRecoveryDrillRequest,
    SolutionExportRecoveryReport,
    SolutionExportRead,
    WorkTaskRead,
)
from app.services import repository
from app.services.confirmations import approval_confirmation_phrase, check_confirmation, export_confirmation_phrase
from app.services.preview import generate_solution_svg
from app.services.reports import generate_solution_report
from app.services.storage import filename as storage_filename
from app.services.storage import local_path as storage_local_path
from app.services.storage import read_bytes as storage_read_bytes
from app.services.store import store
from app.services.security import get_current_user, require_any_permission, require_permission
from app.services.task_dispatch import dispatch_work_task
from app.services.validator import validate_solution
from app.services.workflows import archive_expired_solution_exports, export_solution

router = APIRouter()


def _get_solution_and_job(solution_id: str, db: Session):
    solution = repository.get_solution(db, solution_id) or store.solutions.get(solution_id)
    if not solution:
        raise HTTPException(status_code=404, detail="solution not found")
    job = repository.get_job(db, solution.job_id) or store.jobs.get(solution.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return solution, job


def _ensure_valid_solution(solution: NestingSolution) -> None:
    if not solution.validation_report or not solution.validation_report.is_valid:
        raise HTTPException(status_code=409, detail="solution must pass Validator first")


def _ensure_approved_solution(solution: NestingSolution) -> None:
    _ensure_valid_solution(solution)
    if solution.status != "approved":
        raise HTTPException(status_code=409, detail="solution must be approved before production export")


@router.get("/{solution_id}", response_model=NestingSolution)
def get_solution(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NestingSolution:
    solution, _ = _get_solution_and_job(solution_id, db)
    return solution


@router.post("/{solution_id}/validate")
def validate(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:write")),
) -> dict:
    solution, job = _get_solution_and_job(solution_id, db)
    report = validate_solution(job, solution)
    solution.validation_report = report
    solution.status = "valid" if report.is_valid else "invalid"
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action="solution.validate",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"is_valid": report.is_valid, "issue_count": len(report.issues)},
    )
    return report.model_dump()


@router.get("/{solution_id}/approval", response_model=list[SolutionApprovalRead])
def list_approvals(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[SolutionApprovalRead]:
    _get_solution_and_job(solution_id, db)
    return repository.list_solution_approvals(db, solution_id)


@router.post("/{solution_id}/approval/request", response_model=SolutionApprovalRead)
def request_approval(
    solution_id: str,
    payload: ApprovalRequestCreate | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:write")),
) -> SolutionApprovalRead:
    solution, _ = _get_solution_and_job(solution_id, db)
    _ensure_valid_solution(solution)
    approval = repository.create_solution_approval_request(
        db,
        solution,
        requested_by=current_user.user_id,
        request_note=payload.note if payload else None,
    )
    solution.status = "pending_approval"
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action="solution.approval.request",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"approval_id": approval.id, "note": approval.request_note},
    )
    repository.create_permission_notifications(
        db,
        permission_code="solutions:approve",
        event_type="solution.approval.requested",
        title="方案待审批",
        message=f"方案 {solution_id} 已提交审批",
        target_type="nesting_solution",
        target_id=solution_id,
        payload={"approval_id": approval.id, "requested_by": current_user.user_id, "note": approval.request_note},
    )
    return approval


@router.post("/{solution_id}/approval/decision", response_model=SolutionApprovalRead)
def decide_approval(
    solution_id: str,
    payload: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:approve")),
) -> SolutionApprovalRead:
    _require_confirmation(payload.confirmation, approval_confirmation_phrase(solution_id, payload.decision))
    solution, _ = _get_solution_and_job(solution_id, db)
    _ensure_valid_solution(solution)
    try:
        approval = repository.decide_solution_approval(
            db,
            solution_id,
            payload.decision,
            decided_by=current_user.user_id,
            decision_note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    solution.status = payload.decision
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action=f"solution.approval.{payload.decision}",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"approval_id": approval.id, "note": approval.decision_note},
    )
    repository.create_notification(
        db,
        user_id=approval.requested_by,
        event_type=f"solution.approval.{payload.decision}",
        title="方案审批通过" if payload.decision == "approved" else "方案审批驳回",
        message=f"方案 {solution_id} 审批结果：{payload.decision}",
        target_type="nesting_solution",
        target_id=solution_id,
        payload={"approval_id": approval.id, "decided_by": current_user.user_id, "note": approval.decision_note},
    )
    return approval


@router.get("/{solution_id}/preview.svg")
def preview(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    solution, job = _get_solution_and_job(solution_id, db)
    return Response(generate_solution_svg(job, solution), media_type="image/svg+xml")


@router.get("/{solution_id}/report")
def report(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    solution, job = _get_solution_and_job(solution_id, db)
    payload = generate_solution_report(job, solution)
    payload["approvals"] = [item.model_dump(mode="json") for item in repository.list_solution_approvals(db, solution_id)]
    payload["export_records"] = [item.model_dump(mode="json") for item in repository.list_solution_exports(db, solution_id)]
    return payload


@router.get("/{solution_id}/exports", response_model=list[SolutionExportRead])
def list_exports(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_any_permission("solutions:export", "solutions:archive")),
) -> list[SolutionExportRead]:
    _get_solution_and_job(solution_id, db)
    return repository.list_solution_exports(db, solution_id)


@router.get("/{solution_id}/exports/manifest")
def export_manifest(
    solution_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:archive")),
) -> dict:
    _get_solution_and_job(solution_id, db)
    return repository.build_solution_export_manifest(db, solution_id)


@router.post("/{solution_id}/exports/recovery-drill", response_model=SolutionExportRecoveryReport)
def export_recovery_drill(
    solution_id: str,
    payload: SolutionExportRecoveryDrillRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:archive")),
) -> SolutionExportRecoveryReport:
    _get_solution_and_job(solution_id, db)
    request = payload or SolutionExportRecoveryDrillRequest()
    report = repository.build_solution_export_recovery_report(
        db,
        solution_id,
        include_archive_dry_run=request.include_archive_dry_run,
    )
    repository.log_operation(
        db,
        action="solution.exports.recovery_drill",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload=report.model_dump(mode="json"),
    )
    return report


@router.post("/exports/archive-expired", response_model=SolutionExportArchiveResult)
def archive_expired_exports(
    payload: SolutionExportArchiveRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:archive")),
) -> SolutionExportArchiveResult:
    request = payload or SolutionExportArchiveRequest()
    if request.solution_id:
        _get_solution_and_job(request.solution_id, db)
    result = archive_expired_solution_exports(
        db,
        solution_id=request.solution_id,
        dry_run=request.dry_run,
        actor_id=current_user.user_id,
    )
    return SolutionExportArchiveResult.model_validate(result)


@router.post("/exports/archive-expired/async", response_model=WorkTaskRead)
def archive_expired_exports_async(
    background_tasks: BackgroundTasks,
    payload: SolutionExportArchiveRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:archive")),
) -> WorkTaskRead:
    request = payload or SolutionExportArchiveRequest()
    if request.solution_id:
        _get_solution_and_job(request.solution_id, db)
    target_id = request.solution_id or "all"
    task = repository.create_work_task(
        db,
        task_type="solution.export_archive_expired",
        target_type="solution_export",
        target_id=target_id,
        actor_id=current_user.user_id,
        payload={"solution_id": request.solution_id, "dry_run": request.dry_run},
        timeout_sec=120,
    )
    repository.log_operation(
        db,
        action="solution.exports.archive_expired_queued",
        target_type="solution_export",
        target_id=target_id,
        actor_id=current_user.user_id,
        payload={"task_id": task.id, "solution_id": request.solution_id, "dry_run": request.dry_run},
    )
    dispatch_work_task(task.id, background_tasks)
    return task


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> Response:
    export = repository.get_solution_export(db, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")
    media_type = "application/pdf" if export.export_type == "pdf" else "application/dxf"
    path = storage_local_path(export.storage_key)
    if path:
        return FileResponse(path, media_type=media_type, filename=path.name)
    try:
        payload = storage_read_bytes(export.storage_key, version_id=export.storage_version_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="export file missing from storage") from exc
    headers = {"Content-Disposition": f'attachment; filename="{storage_filename(export.storage_key)}"'}
    return Response(payload, media_type=media_type, headers=headers)


@router.post("/{solution_id}/export/pdf", response_model=SolutionExportRead)
def export_pdf(
    solution_id: str,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> SolutionExportRead:
    _require_confirmation(payload.confirmation if payload else None, export_confirmation_phrase(solution_id, "pdf"))
    try:
        return export_solution(db, solution_id, "pdf", actor_id=current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{solution_id}/export/pdf/async", response_model=WorkTaskRead)
def export_pdf_async(
    solution_id: str,
    background_tasks: BackgroundTasks,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> WorkTaskRead:
    _require_confirmation(payload.confirmation if payload else None, export_confirmation_phrase(solution_id, "pdf"))
    return _queue_export_task(solution_id, "pdf", background_tasks, db, current_user)


@router.post("/{solution_id}/export/dxf", response_model=SolutionExportRead)
def export_dxf(
    solution_id: str,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> SolutionExportRead:
    _require_confirmation(payload.confirmation if payload else None, export_confirmation_phrase(solution_id, "dxf"))
    try:
        return export_solution(db, solution_id, "dxf", actor_id=current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{solution_id}/export/dxf/async", response_model=WorkTaskRead)
def export_dxf_async(
    solution_id: str,
    background_tasks: BackgroundTasks,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> WorkTaskRead:
    _require_confirmation(payload.confirmation if payload else None, export_confirmation_phrase(solution_id, "dxf"))
    return _queue_export_task(solution_id, "dxf", background_tasks, db, current_user)


def _queue_export_task(
    solution_id: str,
    export_type: str,
    background_tasks: BackgroundTasks,
    db: Session,
    current_user: CurrentUser,
) -> WorkTaskRead:
    solution, _ = _get_solution_and_job(solution_id, db)
    _ensure_approved_solution(solution)
    task = repository.create_work_task(
        db,
        task_type="solution.export",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"solution_id": solution_id, "export_type": export_type},
        timeout_sec=120,
    )
    repository.log_operation(
        db,
        action=f"solution.export_{export_type}_queued",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"task_id": task.id},
    )
    dispatch_work_task(task.id, background_tasks)
    return task


@router.post("/{solution_id}/approve")
def approve(
    solution_id: str,
    payload: ApprovalDecisionRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:approve")),
) -> dict:
    _require_confirmation(
        payload.confirmation if payload else None,
        approval_confirmation_phrase(solution_id, "approved"),
    )
    solution, _ = _get_solution_and_job(solution_id, db)
    _ensure_valid_solution(solution)
    decision_note = payload.note if payload else None
    try:
        approval = repository.decide_solution_approval(
            db,
            solution_id,
            "approved",
            decided_by=current_user.user_id,
            decision_note=decision_note,
        )
    except ValueError as exc:
        if "pending approval request not found" not in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        repository.create_solution_approval_request(
            db,
            solution,
            requested_by=current_user.user_id,
            request_note="Direct approval",
        )
        approval = repository.decide_solution_approval(
            db,
            solution_id,
            "approved",
            decided_by=current_user.user_id,
            decision_note=decision_note,
        )
    solution.status = "approved"
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action="solution.approve",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=current_user.user_id,
        payload={"approval_id": approval.id, "note": decision_note},
    )
    return {"solution_id": solution_id, "status": "approved"}


def _require_confirmation(actual: str | None, expected: str) -> None:
    try:
        check_confirmation(actual, expected)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
