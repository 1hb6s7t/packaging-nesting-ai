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

from app.db import models as dbm  # noqa: E402, F401
from app.db.base import Base  # noqa: E402
from app.domain import schemas  # noqa: E402
from app.services import repository  # noqa: E402
from app.services.benchmarks import run_and_record_benchmark  # noqa: E402
from app.services.geometry import rectangle_asset  # noqa: E402
from app.services.solvers import SolverOrchestrator  # noqa: E402
from app.services.solvers.placeholders import UnsupportedExternalSolverAdapter  # noqa: E402


def build_solver_governance_audit_report(*, simulate_enabled_stub: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "summary": {},
        "policy_contract": {},
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
    report["policy_contract"] = validate_solver_policy_contract(report)
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
                "external_cli_adapter": adapter.__class__.__name__ in {"PackingSolverAdapter", "SparrowSolverAdapter"},
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
            "external_cli_adapter_names": sorted(row["name"] for row in adapter_rows if row["external_cli_adapter"]),
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
            "external adapters default safely",
            adapters.get("external_placeholder_names") == expected_placeholder_solver_names()
            and adapters.get("external_cli_adapter_names") == expected_external_cli_adapter_names(),
            f"placeholders={adapters.get('external_placeholder_names')} cli={adapters.get('external_cli_adapter_names')}",
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


def validate_solver_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    registry = report.get("registry") or {}
    rectpack_registry = registry.get("rectpack") or {}
    external_solvers = registry.get("external_solvers") if isinstance(registry.get("external_solvers"), list) else []
    guards = report.get("guards") or {}
    rectpack = report.get("rectpack") or {}
    benchmark = report.get("benchmark") or {}
    adapters = report.get("adapters") or {}
    expected_names = expected_solver_names()
    expected_external_names = expected_external_solver_names()
    registered_names = sorted(str(name) for name in registry.get("registered_names") or [])
    checks: list[dict[str, Any]] = [
        policy_check(
            code="schema.version",
            status="passed" if report.get("schema_version") == 1 else "failed",
            message="solver governance audit schema_version is 1"
            if report.get("schema_version") == 1
            else "solver governance audit schema_version must be 1",
            evidence={"schema_version": report.get("schema_version")},
        ),
        policy_check(
            code="registry.template",
            status="passed"
            if registry.get("solver_count") == registry.get("expected_solver_count")
            and registered_names == expected_names
            and sorted(registry.get("expected_names") or []) == expected_names
            else "failed",
            message="solver registry matches the governed template"
            if registry.get("solver_count") == registry.get("expected_solver_count")
            and registered_names == expected_names
            and sorted(registry.get("expected_names") or []) == expected_names
            else "solver registry must match the governed template",
            evidence={
                "solver_count": registry.get("solver_count"),
                "expected_solver_count": registry.get("expected_solver_count"),
                "registered_names": registered_names,
                "expected_names": expected_names,
            },
        ),
        policy_check(
            code="registry.rectpack.default",
            status="passed"
            if rectpack_registry.get("enabled") is True
            and rectpack_registry.get("license_policy") == "open_source"
            and not is_unconfigured_solver_version(rectpack_registry.get("version"))
            else "failed",
            message="Rectpack is the enabled open-source default solver"
            if rectpack_registry.get("enabled") is True
            and rectpack_registry.get("license_policy") == "open_source"
            and not is_unconfigured_solver_version(rectpack_registry.get("version"))
            else "Rectpack must be enabled, open_source, and configured",
            evidence={
                "enabled": rectpack_registry.get("enabled"),
                "license_policy": rectpack_registry.get("license_policy"),
                "version": rectpack_registry.get("version"),
            },
        ),
        policy_check(
            code="registry.external.disabled",
            status="passed"
            if registry.get("external_disabled_count") == len(external_solvers)
            and not registry.get("enabled_unconfigured_stubs")
            and all(not bool(item.get("enabled")) for item in external_solvers)
            else "failed",
            message="external solver stubs are disabled"
            if registry.get("external_disabled_count") == len(external_solvers)
            and not registry.get("enabled_unconfigured_stubs")
            and all(not bool(item.get("enabled")) for item in external_solvers)
            else "external solver stubs must remain disabled until configured",
            evidence={
                "external_disabled_count": registry.get("external_disabled_count"),
                "external_solver_count": len(external_solvers),
                "enabled_unconfigured_stubs": registry.get("enabled_unconfigured_stubs") or [],
                "enabled_external_names": sorted(item.get("name") for item in external_solvers if item.get("enabled")),
            },
        ),
        policy_check(
            code="registry.external.stub_versions",
            status="passed" if all(is_unconfigured_solver_version(item.get("version")) for item in external_solvers) else "failed",
            message="external solvers are explicit placeholder adapter versions"
            if all(is_unconfigured_solver_version(item.get("version")) for item in external_solvers)
            else "external solver placeholders must declare external-adapter-stub versions until configured",
            evidence={
                "versions": {str(item.get("name")): item.get("version") for item in external_solvers},
                "required_prefix": repository.UNCONFIGURED_SOLVER_VERSION_PREFIX,
            },
        ),
        policy_check(
            code="registry.external.license_policy",
            status="passed"
            if all(str(item.get("license_policy") or "") in {"commercial", "review_required"} for item in external_solvers)
            else "failed",
            message="external solver license policies require review or commercial approval"
            if all(str(item.get("license_policy") or "") in {"commercial", "review_required"} for item in external_solvers)
            else "external solver license policies must remain commercial or review_required before enablement",
            evidence={
                "policies": {str(item.get("name")): item.get("license_policy") for item in external_solvers},
            },
        ),
        policy_check(
            code="guards.enablement",
            status="passed"
            if bool(guards.get("stub_enable_rejected"))
            and bool(guards.get("disabled_license_rejected"))
            and bool(guards.get("runtime_enabled_stub_rejected"))
            else "failed",
            message="solver enablement guards reject unconfigured or disabled solvers"
            if bool(guards.get("stub_enable_rejected"))
            and bool(guards.get("disabled_license_rejected"))
            and bool(guards.get("runtime_enabled_stub_rejected"))
            else "solver enablement guards must reject unconfigured or disabled solvers",
            evidence={
                "stub_enable_rejected": bool(guards.get("stub_enable_rejected")),
                "disabled_license_rejected": bool(guards.get("disabled_license_rejected")),
                "runtime_enabled_stub_rejected": bool(guards.get("runtime_enabled_stub_rejected")),
            },
        ),
        policy_check(
            code="adapters.placeholder_boundary",
            status="passed"
            if sorted(adapters.get("registered_names") or []) == expected_names
            and sorted(adapters.get("external_placeholder_names") or []) == expected_placeholder_solver_names()
            and sorted(adapters.get("external_cli_adapter_names") or []) == expected_external_cli_adapter_names()
            else "failed",
            message="orchestrator registers all solvers while external adapters default to safe placeholder or CLI-contract mode"
            if sorted(adapters.get("registered_names") or []) == expected_names
            and sorted(adapters.get("external_placeholder_names") or []) == expected_placeholder_solver_names()
            and sorted(adapters.get("external_cli_adapter_names") or []) == expected_external_cli_adapter_names()
            else "orchestrator must register all solvers and keep external adapters safe by default",
            evidence={
                "registered_names": sorted(adapters.get("registered_names") or []),
                "external_placeholder_names": sorted(adapters.get("external_placeholder_names") or []),
                "external_cli_adapter_names": sorted(adapters.get("external_cli_adapter_names") or []),
                "expected_placeholder_names": expected_placeholder_solver_names(),
                "expected_external_cli_adapter_names": expected_external_cli_adapter_names(),
                "expected_external_names": expected_external_names,
            },
        ),
        policy_check(
            code="rectpack.validation",
            status="passed"
            if bool(rectpack.get("valid"))
            and rectpack.get("solution_status") == "valid"
            and rectpack.get("placed_count") == rectpack.get("candidate_count")
            and int(rectpack.get("unplaced_count") or 0) == 0
            and float(rectpack.get("utilization_rate") or 0) > 0
            else "failed",
            message="Rectpack audit solution is valid and places all sample items"
            if bool(rectpack.get("valid"))
            and rectpack.get("solution_status") == "valid"
            and rectpack.get("placed_count") == rectpack.get("candidate_count")
            and int(rectpack.get("unplaced_count") or 0) == 0
            and float(rectpack.get("utilization_rate") or 0) > 0
            else "Rectpack audit solution must be valid and place all sample items",
            evidence={
                "solution_status": rectpack.get("solution_status"),
                "valid": bool(rectpack.get("valid")),
                "placed_count": rectpack.get("placed_count"),
                "candidate_count": rectpack.get("candidate_count"),
                "unplaced_count": rectpack.get("unplaced_count"),
                "utilization_rate": rectpack.get("utilization_rate"),
            },
        ),
        policy_check(
            code="rectpack.determinism",
            status="passed" if bool(rectpack.get("deterministic_placement")) and bool(rectpack.get("placement_signature")) else "failed",
            message="Rectpack sample placement is deterministic"
            if bool(rectpack.get("deterministic_placement")) and bool(rectpack.get("placement_signature"))
            else "Rectpack sample placement must be deterministic",
            evidence={
                "deterministic_placement": bool(rectpack.get("deterministic_placement")),
                "placement_count": len(rectpack.get("placement_signature") or []),
            },
        ),
        policy_check(
            code="benchmark.persistence",
            status="passed"
            if bool(benchmark.get("valid"))
            and benchmark.get("solver_name") == schemas.SolverName.rectpack.value
            and int(benchmark.get("persisted_run_count") or 0) >= 1
            and present(benchmark.get("run_id"))
            else "failed",
            message="benchmark run is valid, persisted, and tied to Rectpack"
            if bool(benchmark.get("valid"))
            and benchmark.get("solver_name") == schemas.SolverName.rectpack.value
            and int(benchmark.get("persisted_run_count") or 0) >= 1
            and present(benchmark.get("run_id"))
            else "benchmark audit must persist a valid Rectpack run",
            evidence={
                "valid": bool(benchmark.get("valid")),
                "solver_name": benchmark.get("solver_name"),
                "persisted_run_count": benchmark.get("persisted_run_count"),
                "run_id_present": present(benchmark.get("run_id")),
            },
        ),
    ]
    failed_count = sum(1 for check in checks if check["status"] == "failed")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    passed_count = sum(1 for check in checks if check["status"] == "passed")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in checks if check["status"] == "failed"],
        "warning_checks": [check for check in checks if check["status"] == "warning"],
        "checks": checks,
    }


def policy_check(
    *,
    code: str,
    status: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "severity": "critical" if status == "failed" else "warning" if status == "warning" else "info",
        "message": message,
        "evidence": evidence or {},
    }


def expected_solver_names() -> list[str]:
    return sorted(str(item["name"]) for item in repository.DEFAULT_SOLVER_REGISTRY)


def expected_external_solver_names() -> list[str]:
    return sorted(str(item["name"]) for item in repository.DEFAULT_SOLVER_REGISTRY if item["name"] != schemas.SolverName.rectpack.value)


def expected_placeholder_solver_names() -> list[str]:
    return sorted([schemas.SolverName.ortools.value, schemas.SolverName.phoenix.value])


def expected_external_cli_adapter_names() -> list[str]:
    return sorted([schemas.SolverName.packing_solver.value, schemas.SolverName.sparrow.value])


def is_unconfigured_solver_version(value: Any) -> bool:
    return str(value or "").strip().lower().startswith(repository.UNCONFIGURED_SOLVER_VERSION_PREFIX)


def present(value: Any) -> bool:
    return bool(str(value or "").strip())


def check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") or []
    policy_contract = report.get("policy_contract") or {}
    policy_failed_count = int(policy_contract.get("failed_count") or 0)
    policy_warning_count = int(policy_contract.get("warning_count") or 0)
    registry = report.get("registry") or {}
    rectpack = report.get("rectpack") or {}
    benchmark = report.get("benchmark") or {}
    return {
        "check_count": len(checks),
        "failed_count": sum(1 for item in checks if item.get("status") != "passed")
        + policy_failed_count
        + len(report.get("errors") or []),
        "policy_contract_status": policy_contract.get("status"),
        "policy_contract_failed_count": policy_failed_count,
        "policy_contract_warning_count": policy_warning_count,
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
