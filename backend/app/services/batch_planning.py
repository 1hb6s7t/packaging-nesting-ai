from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.domain.schemas import NestingItem, NestingJob, NestingSolution, PlanningMode, SolverConfig
from app.services.geometry import calculate_bbox, enrich_polygon, printable_area_polygon, rotate_polygon
from app.services.solvers import SolverOrchestrator


DEFAULT_MAX_CANDIDATES_PER_SHEET = 600


@dataclass
class BatchPlanResult:
    planning_mode: PlanningMode
    requested_units: int
    produced_units: int
    shortage_units: int
    overproduction_units: int
    units_per_sheet: int
    sheets_used: int
    quantity_fulfillment_rate: float
    hard_rule_pass: bool
    utilization_rate: float
    waste_rate: float
    runtime_ms: int
    valid: bool
    export_ok: bool
    case_score: float
    failure_reason: str | None = None
    solutions: list[NestingSolution] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def plan_batch(job: NestingJob, mode: PlanningMode = "single_sheet", config: SolverConfig | None = None) -> BatchPlanResult:
    if mode == "pattern":
        return _plan_pattern(job, config)
    if mode == "expanded":
        return _plan_expanded(job, config)
    return _plan_single_sheet(job, config)


def _plan_single_sheet(job: NestingJob, config: SolverConfig | None) -> BatchPlanResult:
    solution = SolverOrchestrator().solve(job, config)[0]
    requested_by_source = _requested_units_by_source(job.candidate_items)
    placed_by_source = Counter(placement.item_id for placement in solution.placed_items)
    produced = sum(placed_by_source.values())
    return _build_result(
        planning_mode="single_sheet",
        requested_by_source=requested_by_source,
        produced_by_source=placed_by_source,
        sheets_used=1,
        units_per_sheet=produced,
        solutions=[solution],
    )


def _plan_pattern(job: NestingJob, config: SolverConfig | None) -> BatchPlanResult:
    requested_by_source = _requested_units_by_source(job.candidate_items)
    candidates, source_lookup = _expand_remaining_for_sheet(
        job,
        requested_by_source,
        sheet_index=1,
        max_candidates_per_sheet=_max_candidates_per_sheet(job),
    )
    pattern_job = _job_with_candidates(job, candidates)
    solution = SolverOrchestrator().solve(pattern_job, config)[0]
    units_by_source = _placed_units_by_source(solution, source_lookup)
    units_per_sheet = sum(units_by_source.values())
    sheets_used = _pattern_sheets_required(requested_by_source, units_by_source)
    produced_by_source = Counter(
        {source_id: units_by_source.get(source_id, 0) * sheets_used for source_id in requested_by_source}
    )
    return _build_result(
        planning_mode="pattern",
        requested_by_source=requested_by_source,
        produced_by_source=produced_by_source,
        sheets_used=sheets_used,
        units_per_sheet=units_per_sheet,
        solutions=[solution],
    )


def _plan_expanded(job: NestingJob, config: SolverConfig | None) -> BatchPlanResult:
    requested_by_source = _requested_units_by_source(job.candidate_items)
    remaining_by_source = Counter(requested_by_source)
    produced_by_source: Counter[str] = Counter()
    solutions: list[NestingSolution] = []
    units_by_sheet: list[int] = []
    max_sheets = _max_batch_sheets(job, sum(requested_by_source.values()))
    max_candidates_per_sheet = _max_candidates_per_sheet(job)

    for sheet_index in range(1, max_sheets + 1):
        if not any(quantity > 0 for quantity in remaining_by_source.values()):
            break
        candidates, source_lookup = _expand_remaining_for_sheet(
            job,
            remaining_by_source,
            sheet_index=sheet_index,
            max_candidates_per_sheet=max_candidates_per_sheet,
        )
        if not candidates:
            break
        sheet_job = _job_with_candidates(job, candidates)
        solution = SolverOrchestrator().solve(sheet_job, config)[0]
        sheet_units_by_source = _placed_units_by_source(solution, source_lookup)
        sheet_units = sum(sheet_units_by_source.values())
        if sheet_units == 0:
            solutions.append(solution)
            units_by_sheet.append(0)
            break
        for source_id, count in sheet_units_by_source.items():
            produced_by_source[source_id] += count
            remaining_by_source[source_id] = max(0, remaining_by_source[source_id] - count)
        solutions.append(solution)
        units_by_sheet.append(sheet_units)

    return _build_result(
        planning_mode="expanded",
        requested_by_source=requested_by_source,
        produced_by_source=produced_by_source,
        sheets_used=len(solutions),
        units_per_sheet=max(units_by_sheet, default=0),
        solutions=solutions,
        extra_metrics={"units_by_sheet": units_by_sheet},
    )


def _build_result(
    *,
    planning_mode: PlanningMode,
    requested_by_source: Counter[str],
    produced_by_source: Counter[str],
    sheets_used: int,
    units_per_sheet: int,
    solutions: list[NestingSolution],
    extra_metrics: dict[str, Any] | None = None,
) -> BatchPlanResult:
    requested_units = sum(requested_by_source.values())
    physical_produced_units = sum(produced_by_source.values())
    fulfilled_units = sum(
        min(produced_by_source.get(source_id, 0), requested)
        for source_id, requested in requested_by_source.items()
    )
    shortage_units = max(0, requested_units - fulfilled_units)
    overproduction_units = sum(
        max(0, produced_by_source.get(source_id, 0) - requested)
        for source_id, requested in requested_by_source.items()
    )
    quantity_fulfillment_rate = round(fulfilled_units / requested_units, 4) if requested_units else 1.0
    solver_outputs_valid = bool(solutions) and all(_solution_validator_passed(solution) for solution in solutions)
    hard_rule_pass = solver_outputs_valid and shortage_units == 0
    utilization_rate = _average_utilization(solutions)
    runtime_ms = sum(solution.runtime_ms for solution in solutions)
    case_score = _case_score(hard_rule_pass, utilization_rate, quantity_fulfillment_rate, runtime_ms)
    failure_reason = None
    if not solver_outputs_valid:
        failure_reason = "validator_failed"
    elif shortage_units:
        failure_reason = "quantity_shortage"

    metrics: dict[str, Any] = {
        "planning_mode": planning_mode,
        "requested_units_by_item": dict(requested_by_source),
        "produced_units_by_item": dict(produced_by_source),
        "fulfilled_units": fulfilled_units,
        "solution_count": len(solutions),
        "solution_statuses": [solution.status for solution in solutions],
    }
    if extra_metrics:
        metrics.update(extra_metrics)

    return BatchPlanResult(
        planning_mode=planning_mode,
        requested_units=requested_units,
        produced_units=physical_produced_units,
        shortage_units=shortage_units,
        overproduction_units=overproduction_units,
        units_per_sheet=units_per_sheet,
        sheets_used=sheets_used,
        quantity_fulfillment_rate=quantity_fulfillment_rate,
        hard_rule_pass=hard_rule_pass,
        utilization_rate=utilization_rate,
        waste_rate=round(1 - utilization_rate, 4),
        runtime_ms=runtime_ms,
        valid=hard_rule_pass,
        export_ok=hard_rule_pass,
        case_score=case_score,
        failure_reason=failure_reason,
        solutions=solutions,
        metrics=metrics,
    )


def _requested_units_by_source(items: list[NestingItem]) -> Counter[str]:
    return Counter({item.item_id: item.quantity for item in items})


def _placed_units_by_source(solution: NestingSolution, source_lookup: dict[str, str]) -> Counter[str]:
    return Counter(source_lookup.get(placement.item_id, placement.item_id) for placement in solution.placed_items)


def _expand_remaining_for_sheet(
    job: NestingJob,
    remaining_by_source: Counter[str],
    *,
    sheet_index: int,
    max_candidates_per_sheet: int,
) -> tuple[list[NestingItem], dict[str, str]]:
    source_items = {item.item_id: item for item in job.candidate_items}
    expanded: list[NestingItem] = []
    source_lookup: dict[str, str] = {}
    for source_id, remaining in sorted(
        remaining_by_source.items(),
        key=lambda item: source_items[item[0]].priority_score if item[0] in source_items else 0,
        reverse=True,
    ):
        if remaining <= 0 or source_id not in source_items:
            continue
        source_item = source_items[source_id]
        source_capacity = _estimate_item_capacity(job, source_item)
        copy_count = min(remaining, source_capacity, max_candidates_per_sheet - len(expanded))
        for index in range(1, copy_count + 1):
            copy_id = f"{source_id}__s{sheet_index:04d}_u{index:04d}"
            metadata = {
                **source_item.metadata,
                "source_item_id": source_id,
                "source_quantity": source_item.quantity,
                "batch_sheet_index": sheet_index,
                "batch_unit_index": index,
            }
            expanded.append(source_item.model_copy(update={"item_id": copy_id, "quantity": 1, "metadata": metadata}))
            source_lookup[copy_id] = source_id
        if len(expanded) >= max_candidates_per_sheet:
            break
    return expanded, source_lookup


def _estimate_item_capacity(job: NestingJob, item: NestingItem) -> int:
    printable = printable_area_polygon(job.sheet)
    bounds = printable.bbox or calculate_bbox(printable.outer)
    polygon = enrich_polygon(item.polygon)
    clearance = item.bleed_mm + item.min_gap_mm / 2
    best = 0
    for rotation in item.allowed_rotations or [0]:
        rotated = rotate_polygon(polygon, rotation)
        bbox = rotated.bbox or calculate_bbox(rotated.outer)
        padded_width = bbox.width + clearance * 2
        padded_height = bbox.height + clearance * 2
        if padded_width <= 0 or padded_height <= 0:
            continue
        best = max(best, math.floor(bounds.width / padded_width) * math.floor(bounds.height / padded_height))
    return max(1, best)


def _pattern_sheets_required(requested_by_source: Counter[str], units_by_source: Counter[str]) -> int:
    required = 0
    for source_id, requested in requested_by_source.items():
        units_per_sheet = units_by_source.get(source_id, 0)
        if units_per_sheet <= 0:
            return 0
        required = max(required, math.ceil(requested / units_per_sheet))
    return required


def _job_with_candidates(job: NestingJob, candidates: list[NestingItem]) -> NestingJob:
    return job.model_copy(update={"candidate_items": candidates, "top_k": 1})


def _solution_validator_passed(solution: NestingSolution) -> bool:
    return (
        solution.status not in {"failed", "invalid"}
        and solution.validation_report is not None
        and solution.validation_report.is_valid
    )


def _average_utilization(solutions: list[NestingSolution]) -> float:
    if not solutions:
        return 0.0
    return round(sum(solution.utilization_rate for solution in solutions) / len(solutions), 4)


def _case_score(
    hard_rule_pass: bool,
    utilization_rate: float,
    quantity_fulfillment_rate: float,
    runtime_ms: int,
) -> float:
    if not hard_rule_pass:
        return 0.0
    runtime_score = max(0.0, 1.0 - runtime_ms / 120_000)
    return round(100 * (0.45 * utilization_rate + 0.35 * quantity_fulfillment_rate + 0.20 * runtime_score), 2)


def _max_candidates_per_sheet(job: NestingJob) -> int:
    value = job.constraints.get("max_batch_candidates_per_sheet", DEFAULT_MAX_CANDIDATES_PER_SHEET)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CANDIDATES_PER_SHEET


def _max_batch_sheets(job: NestingJob, requested_units: int) -> int:
    value = job.constraints.get("max_batch_sheets", requested_units)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, requested_units)
