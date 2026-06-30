from __future__ import annotations

from pathlib import Path
import statistics
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import (
    BatchBenchmarkRunRead,
    BenchmarkCaseRead,
    CurrentUser,
    NestingItem,
    NestingJob,
    PolygonAsset,
    SheetSpec,
)
from app.services import repository
from app.services.batch_planning import plan_batch
from app.services.benchmark_importers import load_public_dataset_as_benchmark_case
from app.services.enterprise_benchmarks import (
    EnterpriseBenchmarkRunner,
    create_batch_benchmark_run,
)
from app.services.security import require_permission

router = APIRouter()
enterprise_runner = EnterpriseBenchmarkRunner()


@router.post("/import/or-datasets", response_model=BenchmarkCaseRead)
def import_or_dataset(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BenchmarkCaseRead:
    path_value = payload.get("path")
    if not path_value:
        raise HTTPException(status_code=422, detail="path is required")
    try:
        case = load_public_dataset_as_benchmark_case(
            Path(path_value),
            case_id=str(payload.get("case_id") or f"or_dataset_{int(time.time())}"),
            name=payload.get("name"),
            sheet_width=_maybe_float(payload.get("sheet_width")),
            sheet_height=_maybe_float(payload.get("sheet_height")),
            material=str(payload.get("material") or "dataset_material"),
            thickness=str(payload.get("thickness") or "dataset_thickness"),
            planning_mode=payload.get("planning_mode", "pattern"),
        )
        saved = repository.upsert_benchmark_case(db, case, source="or_dataset")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="benchmark.or_dataset.import",
        target_type="benchmark_case",
        target_id=saved.case_id,
        actor_id=current_user.user_id,
        payload={"path": str(path_value), "item_count": len(saved.items)},
    )
    return saved


@router.post("/run/stress-787", response_model=BatchBenchmarkRunRead)
def run_stress_787(
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BatchBenchmarkRunRead:
    quantity_levels = _quantity_levels(payload)
    started = time.perf_counter()
    results = [plan_batch(_stress_job(quantity), "pattern") for quantity in quantity_levels]
    runtime_ms = int((time.perf_counter() - started) * 1000)
    metrics = {
        "quantity_levels": quantity_levels,
        "runtime_ms": runtime_ms,
        "cases": [
            {
                "quantity": result.requested_units,
                "units_per_sheet": result.units_per_sheet,
                "sheets_used": result.sheets_used,
                "case_score": result.case_score,
                "hard_rule_pass": result.hard_rule_pass,
            }
            for result in results
        ],
    }
    run = create_batch_benchmark_run(
        db,
        benchmark_type="stress_787",
        status="passed" if all(result.hard_rule_pass for result in results) else "failed",
        file_count=len(results),
        p95_runtime_ms=_p95([result.runtime_ms for result in results]),
        hard_rule_pass_rate=_rate(result.hard_rule_pass for result in results),
        quantity_fulfillment_rate=min(result.quantity_fulfillment_rate for result in results),
        topk_legal_rate=1.0 if all(result.hard_rule_pass for result in results) else 0.0,
        avg_case_score=sum(result.case_score for result in results) / len(results),
        metrics=metrics,
    )
    repository.log_operation(
        db,
        action="benchmark.stress_787.run",
        target_type="batch_benchmark_run",
        target_id=run.run_id,
        actor_id=current_user.user_id,
        payload=run.metrics,
    )
    return run


@router.post("/run/batch-1500", response_model=BatchBenchmarkRunRead)
def run_batch_1500_stress(
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BatchBenchmarkRunRead:
    file_count = int((payload or {}).get("file_count", 1500))
    if file_count < 1:
        raise HTTPException(status_code=422, detail="file_count must be >= 1")
    try:
        run = enterprise_runner.run_batch_pipeline(
            db,
            file_count=file_count,
            include_pdf_fallback=bool((payload or {}).get("include_pdf_fallback", False)),
            moq_per_item=int((payload or {}).get("moq_per_item", 1000)),
            top_k=int((payload or {}).get("top_k", 3)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="benchmark.batch_1500.run",
        target_type="batch_benchmark_run",
        target_id=run.run_id,
        actor_id=current_user.user_id,
        payload=run.metrics,
    )
    return run


def _stress_job(quantity: int) -> NestingJob:
    return NestingJob(
        job_id=f"stress_787_{quantity}",
        sheet=SheetSpec(
            sheet_id="SHEET_787_1092_ENTERPRISE_API",
            name="787x1092 enterprise API",
            width=787,
            height=1092,
            margin_top=10,
            margin_right=10,
            margin_bottom=10,
            margin_left=10,
            gripper_mm=20,
            material="white_card",
            thickness="350gsm",
        ),
        candidate_items=[
            NestingItem(
                item_id="carton_80x60",
                order_id=f"stress_{quantity}",
                polygon=PolygonAsset(shape_id="carton_80x60_shape", outer=[(0, 0), (80, 0), (80, 60), (0, 60)]),
                quantity=quantity,
                allowed_rotations=[0, 90],
                min_gap_mm=2,
                bleed_mm=1,
            )
        ],
    )


def _quantity_levels(payload: dict[str, Any] | None) -> list[int]:
    value = (payload or {}).get("quantity_levels")
    if value is None:
        return [1000, 3000, 5000, 10000, 15000]
    if not isinstance(value, list) or not value:
        raise HTTPException(status_code=422, detail="quantity_levels must be a non-empty list")
    quantities = [int(item) for item in value]
    if any(item < 1 for item in quantities):
        raise HTTPException(status_code=422, detail="quantity levels must be >= 1")
    return quantities


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[94])


def _rate(values: Any) -> float:
    items = list(values)
    return round(sum(1 for item in items if item) / len(items), 4) if items else 0


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
