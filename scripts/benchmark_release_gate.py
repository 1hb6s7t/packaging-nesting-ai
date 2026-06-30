from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.schemas import NestingItem, NestingJob, PlanningMode, PolygonAsset, SheetSpec  # noqa: E402
from app.services.batch_planning import plan_batch  # noqa: E402
from app.services.benchmarks import _current_rss_mb  # noqa: E402
from app.services.benchmark_importers import benchmark_case_from_mapping  # noqa: E402


DEFAULT_QUANTITY_LEVELS = [1000, 3000, 5000, 10000, 15000]


@dataclass(frozen=True)
class BenchmarkGateThresholds:
    min_quantity_fulfillment_rate: float = 1.0
    max_p95_runtime_ms: int = 2000
    max_total_runtime_ms: int = 15000
    max_peak_rss_mb: float | None = None


def build_787_sheet() -> SheetSpec:
    return SheetSpec(
        sheet_id="SHEET_787_1092_RELEASE_GATE",
        name="787x1092 release gate",
        width=787,
        height=1092,
        margin_top=10,
        margin_right=10,
        margin_bottom=10,
        margin_left=10,
        gripper_mm=20,
        material="white_card",
        thickness="350gsm",
    )


def build_case_item(quantity: int) -> NestingItem:
    return NestingItem(
        item_id="folding_carton_80x60",
        order_id=f"release_gate_qty_{quantity}",
        polygon=PolygonAsset(shape_id="folding_carton_80x60_shape", outer=[(0, 0), (80, 0), (80, 60), (0, 60)]),
        quantity=quantity,
        priority_score=1,
        allowed_rotations=[0, 90],
        min_gap_mm=2,
        bleed_mm=1,
    )


def benchmark_case_specs(quantity_levels: list[int] | None = None) -> list[tuple[str, PlanningMode, int]]:
    quantities = quantity_levels or DEFAULT_QUANTITY_LEVELS
    cases: list[tuple[str, PlanningMode, int]] = [
        (f"pattern_787x1092_qty_{quantity}", "pattern", quantity) for quantity in quantities
    ]
    cases.append(("expanded_787x1092_qty_1000", "expanded", 1000))
    return cases


def run_benchmark_gate(
    *,
    thresholds: BenchmarkGateThresholds | None = None,
    quantity_levels: list[int] | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or BenchmarkGateThresholds()
    cases = []
    runtimes: list[int] = []
    rss_values = [_current_rss_mb()]
    started = time.perf_counter()
    for case_id, planning_mode, quantity in benchmark_case_specs(quantity_levels):
        job = NestingJob(
            job_id=case_id,
            sheet=build_787_sheet(),
            candidate_items=[build_case_item(quantity)],
            constraints={"max_batch_candidates_per_sheet": 600},
            top_k=1,
        )
        result = plan_batch(job, planning_mode)
        rss_values.append(_current_rss_mb())
        runtimes.append(result.runtime_ms)
        cases.append(
            {
                "case_id": case_id,
                "source": "release_quantity_ladder",
                "planning_mode": planning_mode,
                "sheet": "787x1092",
                "sheet_width": job.sheet.width,
                "sheet_height": job.sheet.height,
                "min_item_quantity": quantity,
                "requested_units": result.requested_units,
                "produced_units": result.produced_units,
                "shortage_units": result.shortage_units,
                "overproduction_units": result.overproduction_units,
                "quantity_fulfillment_rate": result.quantity_fulfillment_rate,
                "units_per_sheet": result.units_per_sheet,
                "sheets_used": result.sheets_used,
                "utilization_rate": result.utilization_rate,
                "waste_rate": result.waste_rate,
                "runtime_ms": result.runtime_ms,
                "hard_rule_pass": result.hard_rule_pass,
                "export_ok": result.export_ok,
                "case_score": result.case_score,
                "failure_reason": result.failure_reason,
            }
        )
    or_case = build_or_dataset_release_case()
    or_job = NestingJob(
        job_id=or_case.case_id,
        sheet=or_case.sheet,
        candidate_items=or_case.items,
        constraints={"source": "or_dataset", "max_batch_candidates_per_sheet": 600},
        top_k=1,
    )
    or_result = plan_batch(or_job, or_case.planning_mode)
    rss_values.append(_current_rss_mb())
    runtimes.append(or_result.runtime_ms)
    cases.append(
        {
            "case_id": or_case.case_id,
            "source": "or_dataset",
            "planning_mode": or_case.planning_mode,
            "sheet": "787x1092",
            "sheet_width": or_case.sheet.width,
            "sheet_height": or_case.sheet.height,
            "min_item_quantity": min(item.quantity for item in or_case.items),
            "requested_units": or_result.requested_units,
            "produced_units": or_result.produced_units,
            "shortage_units": or_result.shortage_units,
            "overproduction_units": or_result.overproduction_units,
            "quantity_fulfillment_rate": or_result.quantity_fulfillment_rate,
            "units_per_sheet": or_result.units_per_sheet,
            "sheets_used": or_result.sheets_used,
            "utilization_rate": or_result.utilization_rate,
            "waste_rate": or_result.waste_rate,
            "runtime_ms": or_result.runtime_ms,
            "hard_rule_pass": or_result.hard_rule_pass,
            "export_ok": or_result.export_ok,
            "case_score": or_result.case_score,
            "failure_reason": or_result.failure_reason,
        }
    )

    p95_runtime_ms = _p95_int(runtimes)
    peak_rss_mb = _peak_rss(rss_values)
    coverage = build_coverage(cases, quantity_levels or DEFAULT_QUANTITY_LEVELS)
    errors = validate_gate_results(
        cases,
        p95_runtime_ms=p95_runtime_ms,
        peak_rss_mb=peak_rss_mb,
        thresholds=thresholds,
    )
    errors.extend(validate_coverage(coverage))
    status = "passed" if not errors else "failed"
    return {
        "schema_version": 1,
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "thresholds": asdict(thresholds),
        "summary": {
            "case_count": len(cases),
            "passed_case_count": sum(1 for case in cases if case["hard_rule_pass"]),
            "failed_case_count": sum(1 for case in cases if not case["hard_rule_pass"]),
            "quantity_levels": quantity_levels or DEFAULT_QUANTITY_LEVELS,
            "planning_modes": sorted({case["planning_mode"] for case in cases}),
            "min_quantity_fulfillment_rate": min(case["quantity_fulfillment_rate"] for case in cases),
            "p95_runtime_ms": p95_runtime_ms,
            "total_runtime_ms": sum(runtimes),
            "wall_time_ms": int((time.perf_counter() - started) * 1000),
            "peak_rss_mb": peak_rss_mb,
            "error_count": len(errors),
        },
        "coverage": coverage,
        "errors": errors,
        "cases": cases,
    }


def build_or_dataset_release_case():
    return benchmark_case_from_mapping(
        {
            "bin_width": 787,
            "bin_height": 1092,
            "rectangles": [
                {"id": "or_box_a", "width": 80, "height": 60, "demand": 1000},
            ],
        },
        case_id="or_dataset_787x1092_qty_1000",
        name="OR dataset 787x1092 quantity 1000",
        sheet_width=None,
        sheet_height=None,
        material="white_card",
        thickness="350gsm",
        planning_mode="pattern",
    )


def build_coverage(cases: list[dict[str, Any]], quantity_levels: list[int]) -> dict[str, Any]:
    return {
        "or_dataset": any(case.get("source") == "or_dataset" for case in cases),
        "sheet_787x1092": any(case.get("sheet_width") == 787 and case.get("sheet_height") == 1092 for case in cases),
        "moq_1000": any(case.get("min_item_quantity", 0) >= 1000 for case in cases),
        "quantity_levels": quantity_levels,
        "planning_modes": sorted({case["planning_mode"] for case in cases}),
        "case_sources": sorted({case.get("source", "unknown") for case in cases}),
    }


def validate_coverage(coverage: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if coverage.get("or_dataset") is not True:
        errors.append("benchmark release gate must include an OR-Datasets converted case")
    if coverage.get("sheet_787x1092") is not True:
        errors.append("benchmark release gate must cover 787x1092 sheet")
    if coverage.get("moq_1000") is not True:
        errors.append("benchmark release gate must cover MOQ 1000")
    return errors


def validate_gate_results(
    cases: list[dict[str, Any]],
    *,
    p95_runtime_ms: int,
    peak_rss_mb: float | None,
    thresholds: BenchmarkGateThresholds,
) -> list[str]:
    errors: list[str] = []
    for case in cases:
        case_id = case["case_id"]
        if case["hard_rule_pass"] is not True:
            errors.append(f"{case_id} hard_rule_pass must be true")
        if case["export_ok"] is not True:
            errors.append(f"{case_id} export_ok must be true")
        if case["quantity_fulfillment_rate"] < thresholds.min_quantity_fulfillment_rate:
            errors.append(
                f"{case_id} quantity_fulfillment_rate {case['quantity_fulfillment_rate']} is below "
                f"{thresholds.min_quantity_fulfillment_rate}"
            )
        if case["shortage_units"] != 0:
            errors.append(f"{case_id} shortage_units must be 0")
        if case["units_per_sheet"] <= 0:
            errors.append(f"{case_id} units_per_sheet must be positive")
        if case["sheets_used"] <= 0:
            errors.append(f"{case_id} sheets_used must be positive")
    if p95_runtime_ms > thresholds.max_p95_runtime_ms:
        errors.append(f"p95_runtime_ms {p95_runtime_ms} exceeds {thresholds.max_p95_runtime_ms}")
    total_runtime_ms = sum(case["runtime_ms"] for case in cases)
    if total_runtime_ms > thresholds.max_total_runtime_ms:
        errors.append(f"total_runtime_ms {total_runtime_ms} exceeds {thresholds.max_total_runtime_ms}")
    if thresholds.max_peak_rss_mb is not None and peak_rss_mb is not None and peak_rss_mb > thresholds.max_peak_rss_mb:
        errors.append(f"peak_rss_mb {peak_rss_mb} exceeds {thresholds.max_peak_rss_mb}")
    return errors


def _p95_int(values: list[int]) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[94])


def _peak_rss(values: list[float | None]) -> float | None:
    measured = [value for value in values if value is not None]
    return max(measured) if measured else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic enterprise benchmark release gates.")
    parser.add_argument("--output", type=Path, required=True, help="Benchmark gate report path.")
    parser.add_argument("--min-quantity-fulfillment-rate", type=float, default=1.0)
    parser.add_argument("--max-p95-runtime-ms", type=int, default=2000)
    parser.add_argument("--max-total-runtime-ms", type=int, default=15000)
    parser.add_argument("--max-peak-rss-mb", type=float)
    return parser.parse_args(argv)


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thresholds = BenchmarkGateThresholds(
        min_quantity_fulfillment_rate=args.min_quantity_fulfillment_rate,
        max_p95_runtime_ms=args.max_p95_runtime_ms,
        max_total_runtime_ms=args.max_total_runtime_ms,
        max_peak_rss_mb=args.max_peak_rss_mb,
    )
    report = run_benchmark_gate(thresholds=thresholds)
    output_path = write_json(args.output, report)
    summary = report["summary"]
    print(
        "benchmark release gate "
        f"{report['status']} "
        f"report={output_path} "
        f"cases={summary['case_count']} "
        f"p95_runtime_ms={summary['p95_runtime_ms']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
