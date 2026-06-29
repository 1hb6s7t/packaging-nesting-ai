from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))

from app.db import models as dbm  # noqa: F401
from app.db.base import Base
from app.domain import schemas
from app.services import repository
from app.services.benchmarks import run_and_record_benchmark
from app.services.geometry import rectangle_asset
from app.services.solvers import SolverOrchestrator
from app.services.solvers.placeholders import UnsupportedExternalSolverAdapter


def build_solver_governance_audit_report(*, simulate_enabled_stub: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "summary": {},
        "checks": [],
        "registry": {},
        "guards": {},
        "rectpack": {},
        "benchmark": {},
        "adapters": {},
        "errors": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="solver-governance-audit-") as temp_dir:
            db_path = Path(temp_dir) / "audit.sqlite"
            engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            try:
                with SessionLocal() as db:
                    workflow = run_solver_governance_workflow(db, simulate_enabled_stub=simulate_enabled_stub)
            finally:
                engine.dispose()
    except Exception as exc:
        report["errors"].append(str(exc))
        report["summary"] = build_summary(report)
        return report

    report.update(workflow)
    report["checks"] = validate_workflow(report)
    report["summary"] = build_summary(report)
    report["status"] = "passed" if report["summary"]["failed_count"] == 0 else "failed"
    return report


def run_solver_governance_workflow(db: Session, *, simulate_enabled_stub: bool) -> dict[str, Any]:
    repository.seed_solver_registry(db)
    expected_names = {item["name"] for item in repository.DEFAULT_SOLVER_REGISTRY}
    sample_job = build_sample_job()

    guards = exercise_solver_guards(db, simulate_enabled_stub=simulate_enabled_stub)
    final_registry = repository.list_solver_registry(db)
    registry_by_name = {item.name: item for item in final_registry}
    external_entries = [item for item in final_registry if item.name != schemas.SolverName.rectpack.value]
    enabled_unconfigured_stubs = [
        item.name
        for item in final_registry
        if item.enabled and item.version.lower().startswith(repository.UNCONFIGURED_SOLVER_VERSION_PREFIX)
    ]

    orchestrator = SolverOrchestrator()
    first_solution = orchestrator.solve(sample_job)[0]
    second_solution = orchestrator.solve(sample_job)[0]
    first_signature = placement_signature(first_solution)
    second_signature = placement_signature(second_solution)

    case = repository.upsert_benchmark_case(
        db,
        schemas.BenchmarkCase(
            case_id="solver_governance_audit_case",
            name="Solver Governance Audit",
            sheet=sample_job.sheet,
            items=sample_job.candidate_items,
            baseline_utilization_rate=0.1,
        ),
        source="audit",
    )
    benchmark_result = run_and_record_benchmark(db, case, schemas.SolverName.rectpack)
    persisted_runs = repository.list_benchmark_runs(db, case_id=case.case_id)

    adapter_rows = []
    for name, adapter in orchestrator.adapters.items():
        adapter_rows.append(
            {
                "name": name.value,
                "class": adapter.__class__.__name__,
                "version": getattr(adapter, "version", ""),
                "external_placeholder": isinstance(adapter, UnsupportedExternalSolverAdapter),
                "supports_sample": adapter.supports(sample_job),
            }
        )

    rectpack = registry_by_name.get(schemas.SolverName.rectpack.value)
    return {
        "registry": {
            "solver_count": len(final_registry),
            "expected_solver_count": len(expected_names),
            "expected_names": sorted(expected_names),
            "registered_names": sorted(registry_by_name),
            "rectpack": rectpack.model_dump(mode="json") if rectpack else None,
            "external_solvers": [item.model_dump(mode="json") for item in external_entries],
            "enabled_unconfigured_stubs": enabled_unconfigured_stubs,
            "external_disabled_count": sum(1 for item in external_entries if not item.enabled),
        },
        "guards": guards,
        "rectpack": {
            "solution_status": first_solution.status,
            "valid": bool(first_solution.validation_report and first_solution.validation_report.is_valid),
            "placed_count": len(first_solution.placed_items),
            "unplaced_count": len(first_solution.unplaced_items),
            "candidate_count": len(sample_job.candidate_items),
            "deterministic_placement": first_signature == second_signature,
            "placement_signature": first_signature,
            "utilization_rate": first_solution.utilization_rate,
            "waste_rate": first_solution.waste_rate,
            "score_total": first_solution.score.total if first_solution.score else None,
        },
        "benchmark": {
            "case_id": case.case_id,
            "run_id": benchmark_result.run_id,
            "solver_name": benchmark_result.solver_name,
            "valid": benchmark_result.valid,
            "failure_reason": benchmark_result.failure_reason,
            "persisted_run_count": len(persisted_runs),
            "utilization_rate": benchmark_result.utilization_rate,
            "waste_rate": benchmark_result.waste_rate,
        },
        "adapters": {
            "adapter_count": len(adapter_rows),
            "registered_names": sorted(row["name"] for row in adapter_rows),
            "external_placeholder_names": sorted(row["name"] for row in adapter_rows if row["external_placeholder"]),
            "rows": sorted(adapter_rows, key=lambda row: row["name"]),
        },
    }


def exercise_solver_guards(db: Session, *, simulate_enabled_stub: bool) -> dict[str, Any]:
    stub_enable_rejected = False
    stub_enable_error = ""
    try:
        repository.update_solver_registry_entry(
            db,
            schemas.SolverName.sparrow.value,
            schemas.SolverRegistryUpdate(enabled=True),
        )
    except ValueError as exc:
        stub_enable_rejected = True
        stub_enable_error = str(exc)

    disabled_license_rejected = False
    disabled_license_error = ""
    try:
        repository.update_solver_registry_entry(
            db,
            schemas.SolverName.rectpack.value,
            schemas.SolverRegistryUpdate(enabled=True, license_policy="disabled"),
        )
    except ValueError as exc:
        disabled_license_rejected = True
        disabled_license_error = str(exc)

    row = db.query(dbm.SolverRegistry).filter(dbm.SolverRegistry.name == schemas.SolverName.ortools.value).one()
    original = {"enabled": row.enabled, "version": row.version, "license_policy": row.license_policy}
    row.enabled = True
    row.version = "external-adapter-stub-0.1.0"
    row.license_policy = "review_required"
    db.commit()
    runtime_enabled_stub_rejected = False
    runtime_enabled_stub_error = ""
    try:
        repository.ensure_solver_enabled(db, schemas.SolverName.ortools.value)
    except ValueError as exc:
        runtime_enabled_stub_rejected = True
        runtime_enabled_stub_error = str(exc)
    if not simulate_enabled_stub:
        row = db.query(dbm.SolverRegistry).filter(dbm.SolverRegistry.name == schemas.SolverName.ortools.value).one()
        row.enabled = original["enabled"]
        row.version = original["version"]
        row.license_policy = original["license_policy"]
        db.commit()

    return {
        "stub_enable_rejected": stub_enable_rejected,
        "stub_enable_error": stub_enable_error,
        "disabled_license_rejected": disabled_license_rejected,
        "disabled_license_error": disabled_license_error,
        "runtime_enabled_stub_rejected": runtime_enabled_stub_rejected,
        "runtime_enabled_stub_error": runtime_enabled_stub_error,
        "simulate_enabled_stub": simulate_enabled_stub,
    }


def build_sample_job() -> schemas.NestingJob:
    return schemas.NestingJob(
        job_id="solver_governance_audit_job",
        sheet=schemas.SheetSpec(
            sheet_id="solver_governance_audit_sheet",
            width=500,
            height=400,
            margin_top=5,
            margin_right=5,
            margin_bottom=5,
            margin_left=5,
            gripper_mm=10,
            material="white_card",
            thickness="350gsm",
        ),
        candidate_items=[
            schemas.NestingItem(
                item_id="audit_item_1",
                order_id="audit_order_1",
                polygon=rectangle_asset("audit_shape_1", 100, 80),
                priority_score=0.9,
            ),
            schemas.NestingItem(
                item_id="audit_item_2",
                order_id="audit_order_2",
                polygon=rectangle_asset("audit_shape_2", 80, 60),
                priority_score=0.8,
            ),
        ],
        top_k=1,
    )


def placement_signature(solution: schemas.NestingSolution) -> list[dict[str, Any]]:
    return [
        {
            "item_id": item.item_id,
            "order_id": item.order_id,
            "x": item.x,
            "y": item.y,
            "rotation": item.rotation,
            "width": item.width,
            "height": item.height,
        }
        for item in solution.placed_items
    ]


def validate_workflow(report: dict[str, Any]) -> list[dict[str, Any]]:
    registry = report.get("registry") or {}
    rectpack_registry = registry.get("rectpack") or {}
    guards = report.get("guards") or {}
    rectpack = report.get("rectpack") or {}
    benchmark = report.get("benchmark") or {}
    adapters = report.get("adapters") or {}
    return [
        check_result(
            "registry has expected solver entries",
            registry.get("solver_count") == registry.get("expected_solver_count"),
            f"count={registry.get('solver_count')} expected={registry.get('expected_solver_count')}",
        ),
        check_result(
            "registry names match expected templates",
            registry.get("registered_names") == registry.get("expected_names"),
            f"registered={registry.get('registered_names')}",
        ),
        check_result("rectpack enabled", rectpack_registry.get("enabled") is True, f"enabled={rectpack_registry.get('enabled')}"),
        check_result(
            "rectpack open source policy",
            rectpack_registry.get("license_policy") == "open_source",
            f"policy={rectpack_registry.get('license_policy')}",
        ),
        check_result(
            "rectpack uses configured version",
            not str(rectpack_registry.get("version") or "").lower().startswith(repository.UNCONFIGURED_SOLVER_VERSION_PREFIX),
            f"version={rectpack_registry.get('version')}",
        ),
        check_result(
            "external solver stubs disabled",
            registry.get("external_disabled_count") == len(registry.get("external_solvers") or []),
            f"disabled={registry.get('external_disabled_count')} total={len(registry.get('external_solvers') or [])}",
        ),
        check_result(
            "no enabled unconfigured solver stubs",
            not registry.get("enabled_unconfigured_stubs"),
            f"enabled_stubs={registry.get('enabled_unconfigured_stubs')}",
        ),
        check_result(
            "enabling unconfigured stub rejected",
            bool(guards.get("stub_enable_rejected")),
            str(guards.get("stub_enable_error") or ""),
        ),
        check_result(
            "disabled license policy rejected",
            bool(guards.get("disabled_license_rejected")),
            str(guards.get("disabled_license_error") or ""),
        ),
        check_result(
            "legacy enabled stub rejected at runtime",
            bool(guards.get("runtime_enabled_stub_rejected")),
            str(guards.get("runtime_enabled_stub_error") or ""),
        ),
        check_result(
            "orchestrator registered all solvers",
            adapters.get("registered_names") == registry.get("expected_names"),
            f"adapters={adapters.get('registered_names')}",
        ),
        check_result(
            "external adapters remain placeholders",
            adapters.get("external_placeholder_names") == sorted(item["name"] for item in repository.DEFAULT_SOLVER_REGISTRY if item["name"] != schemas.SolverName.rectpack.value),
            f"placeholders={adapters.get('external_placeholder_names')}",
        ),
        check_result("rectpack solution valid", bool(rectpack.get("valid")), f"status={rectpack.get('solution_status')}"),
        check_result(
            "rectpack places all audit items",
            rectpack.get("placed_count") == rectpack.get("candidate_count") and rectpack.get("unplaced_count") == 0,
            f"placed={rectpack.get('placed_count')} unplaced={rectpack.get('unplaced_count')}",
        ),
        check_result(
            "rectpack placement deterministic",
            bool(rectpack.get("deterministic_placement")),
            f"signature={rectpack.get('placement_signature')}",
        ),
        check_result(
            "rectpack utilization positive",
            (rectpack.get("utilization_rate") or 0) > 0,
            f"utilization={rectpack.get('utilization_rate')}",
        ),
        check_result("benchmark run valid", bool(benchmark.get("valid")), f"failure={benchmark.get('failure_reason')}"),
        check_result(
            "benchmark persisted run",
            benchmark.get("persisted_run_count", 0) >= 1 and bool(benchmark.get("run_id")),
            f"runs={benchmark.get('persisted_run_count')} run_id={benchmark.get('run_id')}",
        ),
        check_result(
            "benchmark uses rectpack solver",
            benchmark.get("solver_name") == schemas.SolverName.rectpack.value,
            f"solver={benchmark.get('solver_name')}",
        ),
    ]


def check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") or []
    registry = report.get("registry") or {}
    rectpack = report.get("rectpack") or {}
    benchmark = report.get("benchmark") or {}
    return {
        "check_count": len(checks),
        "failed_count": sum(1 for item in checks if item.get("status") != "passed") + len(report.get("errors") or []),
        "solver_count": registry.get("solver_count"),
        "enabled_unconfigured_stub_count": len(registry.get("enabled_unconfigured_stubs") or []),
        "rectpack_valid": rectpack.get("valid"),
        "benchmark_valid": benchmark.get("valid"),
    }


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit solver registry governance, runtime guards, Rectpack output, and Benchmark persistence.")
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    parser.add_argument("--simulate-enabled-stub", action="store_true", help="Internal validation mode: leave an enabled external stub in the temp registry.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_solver_governance_audit_report(simulate_enabled_stub=args.simulate_enabled_stub)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"solver governance audit report: {output_path}", flush=True)
    summary = report["summary"]
    print(
        "solver governance audit "
        f"{report['status']} "
        f"failed={summary['failed_count']} "
        f"solvers={summary.get('solver_count')} "
        f"enabled_stubs={summary.get('enabled_unconfigured_stub_count')} "
        f"rectpack_valid={summary.get('rectpack_valid')} "
        f"benchmark_valid={summary.get('benchmark_valid')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
