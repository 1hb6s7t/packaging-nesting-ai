from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import (
    ApprovalDecisionRequest,
    ApprovalRequestCreate,
    BatchLayoutJobCreate,
    BatchLayoutJobRead,
    BatchLayoutGroupRead,
    BatchLayoutRunResult,
    ConfirmationRequest,
    CurrentUser,
    ProductionPlanApprovalRead,
    ProductionPlanExportRead,
    ProductionPlanRead,
)
from app.services import repository
from app.services.batch_layout import BatchLayoutService
from app.services.confirmations import check_confirmation
from app.services.security import get_current_user, require_permission
from app.services.storage import filename as storage_filename
from app.services.storage import read_bytes as storage_read_bytes
from app.services.workflows import ensure_valid_production_plan, export_production_plan

router = APIRouter()
service = BatchLayoutService()


@router.post("/jobs", response_model=BatchLayoutJobRead)
def create_batch_layout_job(
    payload: BatchLayoutJobCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchLayoutJobRead:
    try:
        job = service.create_job(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="batch_layout.job.create",
        target_type="batch_layout_job",
        target_id=job.job_id,
        actor_id=current_user.user_id,
        payload={"batch_id": job.batch_id, "top_k": job.top_k, "variant_count": len(job.cut_variants)},
    )
    return job


@router.post("/jobs/{job_id}/run", response_model=BatchLayoutRunResult)
def run_batch_layout_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchLayoutRunResult:
    try:
        result = service.run_job(db, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="batch_layout.job.run",
        target_type="batch_layout_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload=result.summary,
    )
    return result


@router.get("/jobs/{job_id}", response_model=BatchLayoutJobRead)
def get_batch_layout_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BatchLayoutJobRead:
    job = service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="batch layout job not found")
    return job


@router.get("/jobs/{job_id}/groups", response_model=list[BatchLayoutGroupRead])
def list_batch_layout_groups(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[BatchLayoutGroupRead]:
    if service.get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="batch layout job not found")
    return service.list_groups(db, job_id)


@router.get("/jobs/{job_id}/plans", response_model=list[ProductionPlanRead])
def list_batch_layout_plans(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ProductionPlanRead]:
    if service.get_job(db, job_id) is None:
        raise HTTPException(status_code=404, detail="batch layout job not found")
    return service.list_plans(db, job_id)


@router.get("/plans/{plan_id}", response_model=ProductionPlanRead)
def get_batch_layout_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProductionPlanRead:
    plan = service.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    return plan


@router.get("/plans/{plan_id}/preview")
def preview_batch_layout_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    plan = service.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    return Response(_plan_preview_svg(plan), media_type="image/svg+xml")


@router.get("/plans/{plan_id}/approval", response_model=list[ProductionPlanApprovalRead])
def list_production_plan_approvals(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ProductionPlanApprovalRead]:
    if service.get_plan(db, plan_id) is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    return repository.list_production_plan_approvals(db, plan_id)


@router.post("/plans/{plan_id}/approval/request", response_model=ProductionPlanApprovalRead)
def request_production_plan_approval(
    plan_id: str,
    payload: ApprovalRequestCreate | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> ProductionPlanApprovalRead:
    plan = service.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    try:
        ensure_valid_production_plan(plan)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    approval = repository.create_production_plan_approval_request(
        db,
        plan,
        requested_by=current_user.user_id,
        request_note=payload.note if payload else None,
    )
    repository.set_production_plan_status(db, plan_id, "pending_approval")
    repository.log_operation(
        db,
        action="production_plan.approval.request",
        target_type="production_plan",
        target_id=plan_id,
        actor_id=current_user.user_id,
        payload={"approval_id": approval.id, "note": approval.request_note},
    )
    return approval


@router.post("/plans/{plan_id}/approval/decision", response_model=ProductionPlanApprovalRead)
def decide_production_plan_approval(
    plan_id: str,
    payload: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:approve")),
) -> ProductionPlanApprovalRead:
    plan = service.get_plan(db, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    try:
        _require_confirmation(payload.confirmation, _plan_approval_phrase(plan_id, payload.decision))
        ensure_valid_production_plan(plan)
        approval = repository.decide_production_plan_approval(
            db,
            plan_id,
            payload.decision,
            decided_by=current_user.user_id,
            decision_note=payload.note,
        )
        repository.set_production_plan_status(db, plan_id, payload.decision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action=f"production_plan.approval.{payload.decision}",
        target_type="production_plan",
        target_id=plan_id,
        actor_id=current_user.user_id,
        payload={"approval_id": approval.id, "note": approval.decision_note},
    )
    return approval


@router.get("/plans/{plan_id}/exports", response_model=list[ProductionPlanExportRead])
def list_production_plan_exports(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> list[ProductionPlanExportRead]:
    if service.get_plan(db, plan_id) is None:
        raise HTTPException(status_code=404, detail="production plan not found")
    return repository.list_production_plan_exports(db, plan_id)


@router.get("/plans/exports/{export_id}/download")
def download_production_plan_export(
    export_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> Response:
    export = repository.get_production_plan_export(db, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="production plan export not found")
    payload = storage_read_bytes(export.storage_key, version_id=export.storage_version_id)
    headers = {"Content-Disposition": f'attachment; filename="{storage_filename(export.storage_key)}"'}
    return Response(payload, media_type="application/json", headers=headers)


@router.post("/plans/{plan_id}/export", response_model=ProductionPlanExportRead)
def export_batch_layout_plan(
    plan_id: str,
    payload: ConfirmationRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solutions:export")),
) -> ProductionPlanExportRead:
    _require_confirmation(payload.confirmation if payload else None, _plan_export_phrase(plan_id))
    try:
        return export_production_plan(db, plan_id, actor_id=current_user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _plan_preview_svg(plan: ProductionPlanRead) -> str:
    width = 900
    row_height = 52
    height = 150 + max(1, len(plan.patterns)) * row_height
    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="24" y="36" font-family="Arial" font-size="20" fill="#0f172a">Plan {plan.rank}: {plan.intent}</text>',
        f'<text x="24" y="64" font-family="Arial" font-size="13" fill="#334155">utilization={plan.utilization_rate:.4f} sheets={plan.total_sheets_used} fulfillment={plan.quantity_fulfillment_rate:.4f}</text>',
    ]
    y = 104
    for pattern in plan.patterns:
        bar_width = max(4, int(500 * min(1, pattern.utilization_rate)))
        rows.extend(
            [
                f'<rect x="24" y="{y}" width="820" height="38" fill="#e2e8f0" stroke="#94a3b8"/>',
                f'<rect x="24" y="{y}" width="{bar_width}" height="38" fill="#2563eb" opacity="0.75"/>',
                (
                    f'<text x="36" y="{y + 24}" font-family="Arial" font-size="13" fill="#0f172a">'
                    f'{pattern.pattern_type} variant={pattern.cut_variant_id} units/sheet={pattern.units_per_sheet} '
                    f'sheets={pattern.required_sheets} hard_rule={str(pattern.hard_rule_pass).lower()}'
                    "</text>"
                ),
            ]
        )
        y += row_height
    rows.append("</svg>")
    return "\n".join(rows)


def _plan_approval_phrase(plan_id: str, decision: str) -> str:
    return f"{'APPROVE' if decision == 'approved' else 'REJECT'} PLAN {plan_id}"


def _plan_export_phrase(plan_id: str) -> str:
    return f"EXPORT PLAN {plan_id}"


def _require_confirmation(actual: str | None, expected: str) -> None:
    try:
        check_confirmation(actual, expected)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
