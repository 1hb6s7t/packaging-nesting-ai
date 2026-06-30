from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.services.enterprise_benchmarks import EnterpriseBenchmarkRunner  # noqa: E402


REAL_SAMPLE_AUDIT_SCRIPT = REPO_ROOT / "scripts" / "audit_real_sample_classification.py"
DEFAULT_FIXTURE_PATH = REPO_ROOT / "samples" / "artworks" / "real-sample-classification-fixtures.json"


@dataclass(frozen=True)
class SlowGateThresholds:
    min_hard_rule_pass_rate: float = 1.0
    min_quantity_fulfillment_rate: float = 1.0
    min_topk_legal_rate: float = 0.99
    min_avg_case_score: float = 90.0
    max_batch_1500_p95_runtime_ms: int | None = None
    max_batch_20000_p95_runtime_ms: int | None = None


def run_slow_gates(
    *,
    batch_1500_count: int = 1500,
    batch_20000_count: int = 20000,
    real_sample_fixture: Path = DEFAULT_FIXTURE_PATH,
    real_sample_root: Path | None = None,
    require_real_sample_files: bool = True,
    hash_real_sample_files: bool = False,
    thresholds: SlowGateThresholds | None = None,
    include_pdf_fallback: bool = False,
) -> dict[str, Any]:
    thresholds = thresholds or SlowGateThresholds()
    started = time.perf_counter()
    gates: list[dict[str, Any]] = []
    errors: list[str] = []

    init_db()
    runner = EnterpriseBenchmarkRunner()
    with SessionLocal() as db:
        gates.append(
            run_generated_batch_gate(
                db,
                runner=runner,
                gate_name="batch-1500 generated pipeline",
                benchmark_type="batch_1500",
                file_count=batch_1500_count,
                p95_threshold_ms=thresholds.max_batch_1500_p95_runtime_ms,
                thresholds=thresholds,
                include_pdf_fallback=include_pdf_fallback,
            )
        )
        gates.append(
            run_generated_batch_gate(
                db,
                runner=runner,
                gate_name="batch-20000 generated pipeline",
                benchmark_type="batch_20000",
                file_count=batch_20000_count,
                p95_threshold_ms=thresholds.max_batch_20000_p95_runtime_ms,
                thresholds=thresholds,
                include_pdf_fallback=include_pdf_fallback,
            )
        )

    gates.append(
        run_real_sample_classification_gate(
            fixture_path=real_sample_fixture,
            sample_root=real_sample_root,
            require_files=require_real_sample_files,
            hash_files=hash_real_sample_files,
        )
    )

    for gate in gates:
        errors.extend(gate.get("errors") or [])
        if gate["status"] == "failed":
            errors.append(f"{gate['name']} failed")

    summary = build_summary(gates, errors, started)
    return {
        "schema_version": 1,
        "report_status": "passed" if not errors else "failed",
        "generated_at": datetime.now(UTC).isoformat(),
        "thresholds": asdict(thresholds),
        "dataset_labels": {
            "batch_1500": "generated_synthetic_svg_dxf_pdf_placeholders",
            "batch_20000": "generated_synthetic_svg_dxf_pdf_placeholders",
            "real_sample_classification": "real_customer_sample_fixture_bbox",
        },
        "coverage": build_coverage(gates),
        "summary": summary,
        "errors": errors,
        "gates": gates,
    }


def run_generated_batch_gate(
    db,
    *,
    runner: EnterpriseBenchmarkRunner,
    gate_name: str,
    benchmark_type: str,
    file_count: int,
    p95_threshold_ms: int | None,
    thresholds: SlowGateThresholds,
    include_pdf_fallback: bool,
) -> dict[str, Any]:
    if file_count < 1:
        return {
            "name": gate_name,
            "type": "generated_batch_pipeline",
            "dataset_label": "generated_synthetic_svg_dxf_pdf_placeholders",
            "benchmark_type": benchmark_type,
            "status": "failed",
            "requested_file_count": file_count,
            "file_count": 0,
            "synthetic": True,
            "errors": ["file_count must be >= 1"],
        }
    started = time.perf_counter()
    run = runner.run_batch_pipeline(
        db,
        file_count=file_count,
        include_pdf_fallback=include_pdf_fallback,
        benchmark_type=benchmark_type,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    errors = validate_generated_batch_run(
        run,
        expected_file_count=file_count,
        thresholds=thresholds,
        p95_threshold_ms=p95_threshold_ms,
    )
    status = "passed" if not errors else "failed"
    return {
        "name": gate_name,
        "type": "generated_batch_pipeline",
        "dataset_label": "generated_synthetic_svg_dxf_pdf_placeholders",
        "benchmark_type": benchmark_type,
        "status": status,
        "run_id": run.run_id,
        "job_id": run.job_id,
        "requested_file_count": file_count,
        "file_count": run.file_count,
        "synthetic": run.metrics.get("synthetic") is True,
        "fixture_source": run.metrics.get("fixture_source"),
        "sheet_parent": run.metrics.get("sheet_parent"),
        "moq_per_item": run.metrics.get("moq_per_item"),
        "top_k": run.metrics.get("top_k"),
        "hard_rule_pass_rate": run.hard_rule_pass_rate,
        "quantity_fulfillment_rate": run.quantity_fulfillment_rate,
        "topk_legal_rate": run.topk_legal_rate,
        "avg_case_score": run.avg_case_score,
        "p95_runtime_ms": run.p95_runtime_ms,
        "duration_ms": duration_ms,
        "stage_runtime_ms": run.metrics.get("stage_runtime_ms"),
        "direct_parse_success_rate": run.metrics.get("direct_parse_success_rate"),
        "conversion_required_count": run.metrics.get("conversion_required_count"),
        "manual_review_count": run.metrics.get("manual_review_count"),
        "plan_count": run.metrics.get("plan_count"),
        "legal_plan_count": run.metrics.get("legal_plan_count"),
        "multi_solver_candidate_count": run.metrics.get("multi_solver_candidate_count"),
        "multi_solver_legal_candidate_count": run.metrics.get("multi_solver_legal_candidate_count"),
        "errors": errors,
    }


def validate_generated_batch_run(
    run,
    *,
    expected_file_count: int,
    thresholds: SlowGateThresholds,
    p95_threshold_ms: int | None,
) -> list[str]:
    errors: list[str] = []
    metrics = run.metrics
    if run.status != "passed":
        errors.append(f"{run.benchmark_type} status must be passed, got {run.status}")
    if run.file_count != expected_file_count:
        errors.append(f"{run.benchmark_type} file_count must be {expected_file_count}, got {run.file_count}")
    if metrics.get("synthetic") is not True:
        errors.append(f"{run.benchmark_type} must be explicitly labeled synthetic")
    if metrics.get("fixture_source") != "generated_svg_dxf_pdf_placeholders":
        errors.append(f"{run.benchmark_type} fixture_source must be generated_svg_dxf_pdf_placeholders")
    sheet = metrics.get("sheet_parent") if isinstance(metrics.get("sheet_parent"), dict) else {}
    if sheet.get("width") != 787 or sheet.get("height") != 1092:
        errors.append(f"{run.benchmark_type} must use 787x1092 sheet evidence")
    if metrics.get("moq_per_item") != 1000:
        errors.append(f"{run.benchmark_type} must use MOQ 1000")
    if metrics.get("top_k") != 3:
        errors.append(f"{run.benchmark_type} must request Top3")
    if run.hard_rule_pass_rate < thresholds.min_hard_rule_pass_rate:
        errors.append(f"{run.benchmark_type} hard_rule_pass_rate is below threshold")
    if run.quantity_fulfillment_rate < thresholds.min_quantity_fulfillment_rate:
        errors.append(f"{run.benchmark_type} quantity_fulfillment_rate is below threshold")
    if run.topk_legal_rate < thresholds.min_topk_legal_rate:
        errors.append(f"{run.benchmark_type} topk_legal_rate is below threshold")
    if run.avg_case_score < thresholds.min_avg_case_score:
        errors.append(f"{run.benchmark_type} avg_case_score is below threshold")
    if p95_threshold_ms is not None:
        if run.p95_runtime_ms is None or run.p95_runtime_ms > p95_threshold_ms:
            errors.append(f"{run.benchmark_type} p95_runtime_ms exceeds threshold")
    return errors


def run_real_sample_classification_gate(
    *,
    fixture_path: Path,
    sample_root: Path | None,
    require_files: bool,
    hash_files: bool,
) -> dict[str, Any]:
    audit = load_real_sample_audit_module()
    fixture = audit.load_fixture(fixture_path)
    report = audit.build_report(
        fixture,
        sample_root=sample_root,
        require_files=require_files,
        hash_files=hash_files,
    )
    errors = list(report.get("errors") or [])
    status = "passed"
    if report["report_status"] == "failed":
        status = "failed"
        if require_files and report.get("sample_root_exists") is not True:
            errors.append("real sample directory is required for this slow gate")
    elif report["report_status"] == "skipped":
        status = "failed" if require_files else "skipped"
        if require_files:
            errors.append("real sample directory is required for this slow gate")
    summary = report.get("summary", {})
    return {
        "name": "real sample classification fixture",
        "type": "real_sample_classification",
        "dataset_label": "real_customer_sample_fixture_bbox",
        "status": status,
        "fixture_path": str(fixture_path),
        "sample_root": report.get("sample_root"),
        "sample_root_exists": report.get("sample_root_exists"),
        "require_files": require_files,
        "hash_files": hash_files,
        "case_count": summary.get("case_count", 0),
        "classification_match_count": summary.get("classification_match_count", 0),
        "missing_file_count": summary.get("missing_file_count", 0),
        "error_count": summary.get("error_count", 0),
        "errors": errors,
        "cases": report.get("cases", []),
    }


def load_real_sample_audit_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_real_sample_classification", REAL_SAMPLE_AUDIT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load real sample audit script: {REAL_SAMPLE_AUDIT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_summary(gates: list[dict[str, Any]], errors: list[str], started: float) -> dict[str, Any]:
    batch_gates = [gate for gate in gates if gate.get("type") == "generated_batch_pipeline"]
    real_sample_gate = next((gate for gate in gates if gate.get("type") == "real_sample_classification"), {})
    p95_values = [gate.get("p95_runtime_ms") for gate in batch_gates if isinstance(gate.get("p95_runtime_ms"), int)]
    return {
        "gate_count": len(gates),
        "passed_gate_count": sum(1 for gate in gates if gate["status"] == "passed"),
        "failed_gate_count": sum(1 for gate in gates if gate["status"] == "failed"),
        "skipped_gate_count": sum(1 for gate in gates if gate["status"] == "skipped"),
        "synthetic_file_count": sum(int(gate.get("file_count") or 0) for gate in batch_gates if gate.get("synthetic")),
        "real_sample_case_count": int(real_sample_gate.get("case_count") or 0),
        "real_sample_missing_file_count": int(real_sample_gate.get("missing_file_count") or 0),
        "min_hard_rule_pass_rate": min((gate.get("hard_rule_pass_rate", 0) for gate in batch_gates), default=0),
        "min_quantity_fulfillment_rate": min(
            (gate.get("quantity_fulfillment_rate", 0) for gate in batch_gates), default=0
        ),
        "min_topk_legal_rate": min((gate.get("topk_legal_rate", 0) for gate in batch_gates), default=0),
        "avg_case_score": round(
            statistics.fmean(gate.get("avg_case_score", 0) for gate in batch_gates), 4
        )
        if batch_gates
        else 0,
        "p95_runtime_ms": _p95_int(p95_values),
        "wall_time_ms": int((time.perf_counter() - started) * 1000),
        "error_count": len(errors),
    }


def build_coverage(gates: list[dict[str, Any]]) -> dict[str, Any]:
    by_type = {gate["type"]: gate for gate in gates}
    batch_gates = [gate for gate in gates if gate.get("type") == "generated_batch_pipeline"]
    return {
        "batch_1500": any(gate.get("benchmark_type") == "batch_1500" for gate in batch_gates),
        "batch_20000": any(gate.get("benchmark_type") == "batch_20000" for gate in batch_gates),
        "real_sample_classification": "real_sample_classification" in by_type,
        "real_sample_directory": bool(by_type.get("real_sample_classification", {}).get("sample_root_exists")),
        "sheet_787x1092": all(
            (gate.get("sheet_parent") or {}).get("width") == 787
            and (gate.get("sheet_parent") or {}).get("height") == 1092
            for gate in batch_gates
        ),
        "moq_1000": all(gate.get("moq_per_item") == 1000 for gate in batch_gates),
        "top3": all(gate.get("top_k") == 3 for gate in batch_gates),
        "synthetic_labels": all(gate.get("synthetic") is True for gate in batch_gates),
    }


def _p95_int(values: list[int]) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[94])


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run enterprise slow gates for generated batch and real samples.")
    parser.add_argument("--output", type=Path, required=True, help="Slow gate report path.")
    parser.add_argument("--batch-1500-count", type=int, default=1500)
    parser.add_argument("--batch-20000-count", type=int, default=20000)
    parser.add_argument("--real-sample-fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--real-sample-root", type=Path)
    parser.add_argument(
        "--allow-missing-real-samples",
        action="store_true",
        help="Mark missing local real-sample files as skipped instead of failed.",
    )
    parser.add_argument("--hash-real-sample-files", action="store_true")
    parser.add_argument("--include-pdf-fallback", action="store_true")
    parser.add_argument("--min-hard-rule-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-quantity-fulfillment-rate", type=float, default=1.0)
    parser.add_argument("--min-topk-legal-rate", type=float, default=0.99)
    parser.add_argument("--min-avg-case-score", type=float, default=90.0)
    parser.add_argument("--max-batch-1500-p95-runtime-ms", type=int)
    parser.add_argument("--max-batch-20000-p95-runtime-ms", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    thresholds = SlowGateThresholds(
        min_hard_rule_pass_rate=args.min_hard_rule_pass_rate,
        min_quantity_fulfillment_rate=args.min_quantity_fulfillment_rate,
        min_topk_legal_rate=args.min_topk_legal_rate,
        min_avg_case_score=args.min_avg_case_score,
        max_batch_1500_p95_runtime_ms=args.max_batch_1500_p95_runtime_ms,
        max_batch_20000_p95_runtime_ms=args.max_batch_20000_p95_runtime_ms,
    )
    report = run_slow_gates(
        batch_1500_count=args.batch_1500_count,
        batch_20000_count=args.batch_20000_count,
        real_sample_fixture=args.real_sample_fixture,
        real_sample_root=args.real_sample_root,
        require_real_sample_files=not args.allow_missing_real_samples,
        hash_real_sample_files=args.hash_real_sample_files,
        thresholds=thresholds,
        include_pdf_fallback=args.include_pdf_fallback,
    )
    output_path = write_json(args.output, report)
    summary = report["summary"]
    print(
        "enterprise batch slow gates "
        f"{report['report_status']} "
        f"report={output_path} "
        f"gates={summary['gate_count']} "
        f"synthetic_files={summary['synthetic_file_count']} "
        f"real_sample_cases={summary['real_sample_case_count']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if report["report_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
