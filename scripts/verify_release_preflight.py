from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BACKEND_GATE_NAMES = {"backend release gate tests", "backend full test suite"}
EVIDENCE_GENERATION_GATE = "release evidence pack generation"
EVIDENCE_VERIFICATION_GATE = "release evidence pack verification"
PRODUCTION_ENV_VERIFICATION_GATE = "release evidence production env verification"
DEPENDENCY_REVIEW_VERIFICATION_GATE = "release evidence dependency review verification"
EXTERNAL_ACCEPTANCE_VERIFICATION_GATE = "release evidence external acceptance verification"
BENCHMARK_GATE = "benchmark release gate"
ENTERPRISE_BATCH_SLOW_GATE = "enterprise batch slow gates"
PRODUCTION_ENV_ARTIFACT = "production_env_audit"
DEPLOYMENT_COMPOSE_ARTIFACT = "deployment_compose_audit"
DEPENDENCY_REVIEW_ARTIFACT = "dependency_review_audit"
EXTERNAL_ACCEPTANCE_ARTIFACT = "external_acceptance_audit"
FRONTEND_GATE = "frontend production build"
SMOKE_GATE = "API health smoke"
CLEANUP_NAME = "cleanup pycache"
NESTED_CONTRACT_FIELDS = {
    "production_env_audit": ("policy_contract",),
    "deployment_compose_audit": ("policy_contract",),
    "repository_hygiene_audit": ("policy_contract",),
    "customer_sandbox_audit": ("pack_contract", "sync_strategy", "business_flow"),
    "notification_channel_audit": ("policy_contract",),
    "storage_export_audit": ("storage_contract", "policy_contract"),
    "conversion_supplier_audit": ("policy_contract",),
    "solver_governance_audit": ("policy_contract",),
    "external_acceptance_audit": ("policy_contract",),
    "dependency_review_audit": ("policy_contract",),
}


def verify_release_preflight_report(
    report_path: Path,
    *,
    require_passed_report: bool = True,
    require_evidence_pack: bool = True,
    require_frontend: bool = True,
    require_benchmark_gate: bool = True,
    require_smoke: bool = True,
    require_dependency_inventory: bool = True,
    require_cleanup: bool = True,
    fail_on_dependency_review: bool = False,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    report = read_json(resolved_report_path)
    errors: list[str] = []
    warnings: list[str] = []

    if report.get("schema_version") != 1:
        errors.append("report schema_version must be 1")
    if require_passed_report and report.get("passed") is not True:
        errors.append("release preflight report must have passed=true")

    options = report.get("options") if isinstance(report.get("options"), dict) else {}
    gates = report.get("gates")
    if not isinstance(gates, list):
        gates = []
        errors.append("report gates must be a list")

    allowed_skipped_gates = allowed_skipped_gate_names(
        require_evidence_pack=require_evidence_pack,
        require_frontend=require_frontend,
        require_benchmark_gate=require_benchmark_gate,
        require_smoke=require_smoke,
    )
    gate_checks = [verify_gate_shape(item, allowed_skipped_gates=allowed_skipped_gates) for item in gates]
    gate_by_name = {item.get("name"): item for item in gates if isinstance(item, dict)}
    for check in gate_checks:
        if check["status"] == "failed":
            errors.extend(check["errors"])

    validate_backend_gate(gate_by_name, errors)
    validate_benchmark_gate(gate_by_name, options, errors, require_benchmark_gate=require_benchmark_gate)
    validate_enterprise_batch_slow_gate(gate_by_name, options, errors)
    validate_frontend_gate(gate_by_name, options, errors, require_frontend=require_frontend)
    validate_smoke_gate(gate_by_name, options, errors, require_smoke=require_smoke)
    validate_evidence_gates(gate_by_name, options, errors, require_evidence_pack=require_evidence_pack)
    validate_cleanup(report.get("cleanup"), errors, require_cleanup=require_cleanup)
    validate_dependency_inventory(
        report.get("dependency_inventory_summary"),
        errors,
        warnings,
        require_dependency_inventory=require_dependency_inventory,
        fail_on_dependency_review=fail_on_dependency_review,
    )

    failed_gates = [
        str(gate.get("name") or "<unnamed>")
        for gate in gates
        if isinstance(gate, dict) and not gate_status_is_acceptable(gate, allowed_skipped_gates)
    ]
    status = "passed" if not errors else "failed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(resolved_report_path),
        "report_passed": bool(report.get("passed")),
        "status": status,
        "summary": {
            "gate_count": len(gates),
            "passed_gate_count": sum(1 for gate in gates if isinstance(gate, dict) and gate.get("status") == "passed"),
            "failed_gate_count": len(failed_gates),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "failed_gates": failed_gates,
        },
        "errors": errors,
        "warnings": warnings,
        "gate_checks": gate_checks,
    }


def allowed_skipped_gate_names(
    *,
    require_evidence_pack: bool,
    require_frontend: bool,
    require_benchmark_gate: bool,
    require_smoke: bool,
) -> set[str]:
    names: set[str] = set()
    if not require_evidence_pack:
        names.update(
            {
                EVIDENCE_GENERATION_GATE,
                EVIDENCE_VERIFICATION_GATE,
                PRODUCTION_ENV_VERIFICATION_GATE,
                DEPENDENCY_REVIEW_VERIFICATION_GATE,
                EXTERNAL_ACCEPTANCE_VERIFICATION_GATE,
            }
        )
    if not require_frontend:
        names.add(FRONTEND_GATE)
    if not require_benchmark_gate:
        names.add(BENCHMARK_GATE)
    if not require_smoke:
        names.add(SMOKE_GATE)
    return names


def gate_status_is_acceptable(gate: dict[str, Any], allowed_skipped_gates: set[str]) -> bool:
    status = gate.get("status")
    name = str(gate.get("name") or "<unnamed>")
    return status == "passed" or (status == "skipped" and name in allowed_skipped_gates)


def verify_gate_shape(gate: Any, *, allowed_skipped_gates: set[str] | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(gate, dict):
        return {"name": "<invalid>", "status": "failed", "errors": ["gate entry must be an object"]}
    allowed_skipped_gates = allowed_skipped_gates or set()
    name = str(gate.get("name") or "<unnamed>")
    status = gate.get("status")
    if not gate_status_is_acceptable(gate, allowed_skipped_gates):
        errors.append(f"{name} status must be passed, got {status or '<missing>'}")
    if not isinstance(gate.get("duration_sec"), (int, float)):
        errors.append(f"{name} duration_sec is missing or invalid")
    if gate.get("kind") == "command" and gate.get("exit_code") not in {0, None}:
        errors.append(f"{name} exit_code must be 0, got {gate.get('exit_code')}")
    return {"name": name, "status": "failed" if errors else "passed", "errors": errors}


def validate_backend_gate(gate_by_name: dict[Any, dict[str, Any]], errors: list[str]) -> None:
    if not any(name in gate_by_name for name in BACKEND_GATE_NAMES):
        errors.append("backend release gate is missing")


def validate_frontend_gate(
    gate_by_name: dict[Any, dict[str, Any]],
    options: dict[str, Any],
    errors: list[str],
    *,
    require_frontend: bool,
) -> None:
    if require_frontend and options.get("skip_frontend"):
        errors.append("frontend build was skipped")
    if require_frontend and FRONTEND_GATE not in gate_by_name:
        errors.append("frontend production build gate is missing")


def validate_benchmark_gate(
    gate_by_name: dict[Any, dict[str, Any]],
    options: dict[str, Any],
    errors: list[str],
    *,
    require_benchmark_gate: bool,
) -> None:
    if require_benchmark_gate and options.get("skip_benchmark_gate"):
        errors.append("benchmark release gate was skipped")
    gate = gate_by_name.get(BENCHMARK_GATE)
    if require_benchmark_gate and gate is None:
        errors.append("benchmark release gate is missing")
        return
    if gate is None:
        return
    payload = gate.get("payload")
    if not isinstance(payload, dict):
        errors.append("benchmark release gate payload is missing")
        return
    if payload.get("error"):
        errors.append(f"benchmark release gate report could not be read: {payload['error']}")
    if payload.get("exists") is not True:
        errors.append("benchmark release gate report was not recorded as existing")
    if payload.get("status") != "passed":
        errors.append(f"benchmark release gate status must be passed, got {payload.get('status') or '<missing>'}")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        errors.append("benchmark release gate thresholds are missing")
        thresholds = {}
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("benchmark release gate summary is missing")
        return
    case_count = summary.get("case_count", payload.get("case_count"))
    if not isinstance(case_count, int) or case_count < 6:
        errors.append("benchmark release gate must include at least 6 cases")
    if summary.get("failed_case_count") not in {0, None}:
        errors.append("benchmark release gate has failed cases")
    if summary.get("error_count") not in {0, None}:
        errors.append("benchmark release gate summary has errors")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("benchmark release gate coverage is missing")
        coverage = {}
    if coverage.get("or_dataset") is not True:
        errors.append("benchmark release gate must include OR-Datasets coverage")
    if coverage.get("sheet_787x1092") is not True:
        errors.append("benchmark release gate must include 787x1092 coverage")
    if coverage.get("moq_1000") is not True:
        errors.append("benchmark release gate must include MOQ 1000 coverage")
    modes = set(summary.get("planning_modes") or [])
    if not {"pattern", "expanded"}.issubset(modes):
        errors.append("benchmark release gate must cover pattern and expanded planning modes")
    quantity_levels = set(summary.get("quantity_levels") or [])
    if not {1000, 3000, 5000, 10000, 15000}.issubset(quantity_levels):
        errors.append("benchmark release gate must cover 1000/3000/5000/10000/15000 quantity levels")
    min_rate = summary.get("min_quantity_fulfillment_rate")
    required_rate = thresholds.get("min_quantity_fulfillment_rate", 1.0)
    if not isinstance(min_rate, (int, float)) or min_rate < required_rate:
        errors.append("benchmark release gate quantity fulfillment is below threshold")
    p95_runtime = summary.get("p95_runtime_ms")
    max_p95 = thresholds.get("max_p95_runtime_ms")
    if isinstance(max_p95, (int, float)) and (not isinstance(p95_runtime, (int, float)) or p95_runtime > max_p95):
        errors.append("benchmark release gate p95 runtime exceeds threshold")
    total_runtime = summary.get("total_runtime_ms")
    max_total = thresholds.get("max_total_runtime_ms")
    if isinstance(max_total, (int, float)) and (not isinstance(total_runtime, (int, float)) or total_runtime > max_total):
        errors.append("benchmark release gate total runtime exceeds threshold")
    max_peak_rss = thresholds.get("max_peak_rss_mb")
    peak_rss = summary.get("peak_rss_mb")
    if isinstance(max_peak_rss, (int, float)) and isinstance(peak_rss, (int, float)) and peak_rss > max_peak_rss:
        errors.append("benchmark release gate peak RSS exceeds threshold")


def validate_enterprise_batch_slow_gate(
    gate_by_name: dict[Any, dict[str, Any]],
    options: dict[str, Any],
    errors: list[str],
) -> None:
    include_slow_gates = options.get("include_slow_batch_gates") is True
    gate = gate_by_name.get(ENTERPRISE_BATCH_SLOW_GATE)
    if include_slow_gates and gate is None:
        errors.append("enterprise batch slow gates are missing")
        return
    if gate is None:
        return
    payload = gate.get("payload")
    if not isinstance(payload, dict):
        errors.append("enterprise batch slow gate payload is missing")
        return
    if payload.get("error"):
        errors.append(f"enterprise batch slow gate report could not be read: {payload['error']}")
    if payload.get("exists") is not True:
        errors.append("enterprise batch slow gate report was not recorded as existing")
    if payload.get("status") != "passed":
        errors.append(f"enterprise batch slow gate status must be passed, got {payload.get('status') or '<missing>'}")
    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        errors.append("enterprise batch slow gate thresholds are missing")
    dataset_labels = payload.get("dataset_labels")
    if not isinstance(dataset_labels, dict):
        errors.append("enterprise batch slow gate dataset labels are missing")
        dataset_labels = {}
    if dataset_labels.get("batch_1500") != "generated_synthetic_svg_dxf_pdf_placeholders":
        errors.append("enterprise batch slow gate must label batch_1500 as generated synthetic")
    if dataset_labels.get("batch_20000") != "generated_synthetic_svg_dxf_pdf_placeholders":
        errors.append("enterprise batch slow gate must label batch_20000 as generated synthetic")
    if dataset_labels.get("real_sample_classification") != "real_customer_sample_fixture_bbox":
        errors.append("enterprise batch slow gate must label real samples as fixture bbox evidence")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("enterprise batch slow gate coverage is missing")
        coverage = {}
    for key in (
        "batch_1500",
        "batch_20000",
        "real_sample_classification",
        "sheet_787x1092",
        "moq_1000",
        "top3",
        "synthetic_labels",
    ):
        if coverage.get(key) is not True:
            errors.append(f"enterprise batch slow gate must include {key} coverage")
    if options.get("real_sample_root") and coverage.get("real_sample_directory") is not True:
        errors.append("enterprise batch slow gate must include the configured real sample directory")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("enterprise batch slow gate summary is missing")
        return
    if summary.get("failed_gate_count") not in {0, None}:
        errors.append("enterprise batch slow gate has failed gates")
    if summary.get("error_count") not in {0, None}:
        errors.append("enterprise batch slow gate summary has errors")
    if not isinstance(summary.get("synthetic_file_count"), int) or summary["synthetic_file_count"] < 2:
        errors.append("enterprise batch slow gate synthetic file count is missing")
    if not isinstance(summary.get("real_sample_case_count"), int) or summary["real_sample_case_count"] < 1:
        errors.append("enterprise batch slow gate real sample case count is missing")
    if options.get("real_sample_root") and summary.get("real_sample_missing_file_count") not in {0, None}:
        errors.append("enterprise batch slow gate has missing real sample files")


def validate_smoke_gate(
    gate_by_name: dict[Any, dict[str, Any]],
    options: dict[str, Any],
    errors: list[str],
    *,
    require_smoke: bool,
) -> None:
    if require_smoke and options.get("skip_smoke"):
        errors.append("API health smoke was skipped")
    gate = gate_by_name.get(SMOKE_GATE)
    if require_smoke and gate is None:
        errors.append("API health smoke gate is missing")
        return
    if gate is None:
        return
    payload = gate.get("payload") if isinstance(gate.get("payload"), dict) else {}
    if require_smoke and not payload.get("port"):
        errors.append("API health smoke payload is missing effective port")
    if require_smoke and not payload.get("health"):
        errors.append("API health smoke payload is missing health response")
    if require_smoke and not payload.get("ready"):
        errors.append("API health smoke payload is missing readiness response")


def validate_evidence_gates(
    gate_by_name: dict[Any, dict[str, Any]],
    options: dict[str, Any],
    errors: list[str],
    *,
    require_evidence_pack: bool,
) -> None:
    if require_evidence_pack and options.get("skip_evidence_pack"):
        errors.append("release evidence pack gate was skipped")
    if not require_evidence_pack:
        return
    generation_gate = gate_by_name.get(EVIDENCE_GENERATION_GATE)
    verification_gate = gate_by_name.get(EVIDENCE_VERIFICATION_GATE)
    require_production_env_artifact = bool(options.get("env_file") or options.get("require_production_env"))
    require_dependency_review_artifact = bool(options.get("dependency_review_file") or options.get("require_dependency_review"))
    require_external_acceptance_artifact = bool(
        options.get("external_acceptance_file") or options.get("require_external_acceptance")
    )
    generation_payload = generation_gate.get("payload") if isinstance(generation_gate, dict) else None
    verification_payload = verification_gate.get("payload") if isinstance(verification_gate, dict) else None
    if generation_gate is None:
        errors.append("release evidence pack generation gate is missing")
    else:
        validate_evidence_generation_payload(
            generation_payload,
            errors,
            require_production_env_artifact=require_production_env_artifact,
            require_dependency_review_artifact=require_dependency_review_artifact,
            require_external_acceptance_artifact=require_external_acceptance_artifact,
        )
    if verification_gate is None:
        errors.append("release evidence pack verification gate is missing")
    else:
        validate_evidence_verification_payload(
            verification_payload,
            errors,
            require_production_env_artifact=require_production_env_artifact,
            require_dependency_review_artifact=require_dependency_review_artifact,
            require_external_acceptance_artifact=require_external_acceptance_artifact,
        )
    should_verify_production_env = require_production_env_artifact or evidence_artifact_has_passed(
        generation_payload,
        PRODUCTION_ENV_ARTIFACT,
    )
    should_verify_external_acceptance = require_external_acceptance_artifact or evidence_artifact_has_passed(
        generation_payload,
        EXTERNAL_ACCEPTANCE_ARTIFACT,
    )
    should_verify_dependency_review = require_dependency_review_artifact or evidence_artifact_has_passed(
        generation_payload,
        DEPENDENCY_REVIEW_ARTIFACT,
    )
    if should_verify_production_env:
        validate_evidence_file_verification_gate(
            gate_by_name.get(PRODUCTION_ENV_VERIFICATION_GATE),
            errors,
            display_name="production env",
            require_rebuilt_report_match=True,
        )
    if should_verify_dependency_review:
        validate_evidence_file_verification_gate(
            gate_by_name.get(DEPENDENCY_REVIEW_VERIFICATION_GATE),
            errors,
            display_name="dependency review",
        )
    if should_verify_external_acceptance:
        validate_evidence_file_verification_gate(
            gate_by_name.get(EXTERNAL_ACCEPTANCE_VERIFICATION_GATE),
            errors,
            display_name="external acceptance",
            require_no_failed_evidence_checks=True,
        )


def validate_evidence_generation_payload(
    payload: Any,
    errors: list[str],
    *,
    require_production_env_artifact: bool = False,
    require_dependency_review_artifact: bool = False,
    require_external_acceptance_artifact: bool = False,
) -> None:
    if not isinstance(payload, dict):
        errors.append("release evidence pack generation payload is missing")
        return
    if payload.get("manifest_exists") is not True:
        errors.append("release evidence pack manifest was not recorded as existing")
    if payload.get("pack_status") != "passed":
        errors.append(f"release evidence pack status must be passed, got {payload.get('pack_status') or '<missing>'}")
    summary = payload.get("pack_summary") if isinstance(payload.get("pack_summary"), dict) else {}
    if summary.get("required_failed_count") not in {0, None}:
        errors.append("release evidence pack has required failed artifacts")
    if summary.get("failed_count") not in {0, None}:
        errors.append("release evidence pack has failed artifacts")
    validate_pack_policy_summary(summary, errors)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("release evidence pack payload is missing artifact summaries")
        return
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("release evidence pack artifact summary must be an object")
            continue
        if artifact.get("required") and artifact.get("status") != "passed":
            errors.append(f"required evidence artifact {artifact.get('name') or '<unnamed>'} did not pass")
        if artifact.get("status") == "passed":
            if not artifact.get("relative_path"):
                errors.append(f"evidence artifact {artifact.get('name') or '<unnamed>'} is missing relative_path")
            if not isinstance(artifact.get("size_bytes"), int):
                errors.append(f"evidence artifact {artifact.get('name') or '<unnamed>'} is missing size_bytes")
            if not is_sha256_hex(artifact.get("sha256")):
                errors.append(f"evidence artifact {artifact.get('name') or '<unnamed>'} is missing valid sha256")
        validate_evidence_artifact_summary(artifact, errors)
    if require_production_env_artifact:
        validate_named_evidence_artifact(artifacts, PRODUCTION_ENV_ARTIFACT, "production env audit", errors)
    validate_named_evidence_artifact(artifacts, DEPLOYMENT_COMPOSE_ARTIFACT, "deployment compose audit", errors)
    if require_dependency_review_artifact:
        validate_named_evidence_artifact(artifacts, DEPENDENCY_REVIEW_ARTIFACT, "dependency review audit", errors)
    if require_external_acceptance_artifact:
        validate_named_evidence_artifact(artifacts, EXTERNAL_ACCEPTANCE_ARTIFACT, "external acceptance audit", errors)


def validate_evidence_verification_payload(
    payload: Any,
    errors: list[str],
    *,
    require_production_env_artifact: bool = False,
    require_dependency_review_artifact: bool = False,
    require_external_acceptance_artifact: bool = False,
) -> None:
    if not isinstance(payload, dict):
        errors.append("release evidence pack verification payload is missing")
        return
    validate_evidence_generation_payload(
        payload,
        errors,
        require_production_env_artifact=require_production_env_artifact,
        require_dependency_review_artifact=require_dependency_review_artifact,
        require_external_acceptance_artifact=require_external_acceptance_artifact,
    )
    if payload.get("verification_report_exists") is not True:
        errors.append("release evidence pack verification report was not recorded as existing")
    if payload.get("verification_status") != "passed":
        errors.append(
            f"release evidence verification status must be passed, got {payload.get('verification_status') or '<missing>'}"
        )
    summary = payload.get("verification_summary") if isinstance(payload.get("verification_summary"), dict) else {}
    if summary.get("failed_count") not in {0, None}:
        errors.append("release evidence verification has failed artifact checks")
    if summary.get("manifest_error_count") not in {0, None}:
        errors.append("release evidence verification has manifest errors")


def validate_pack_policy_summary(summary: dict[str, Any], errors: list[str]) -> None:
    status = summary.get("policy_contract_status")
    if status not in {"passed", "warning"}:
        errors.append(f"release evidence pack policy contract must be passed or warning, got {status or '<missing>'}")
    if summary.get("policy_contract_failed_count") not in {0, None}:
        errors.append("release evidence pack policy contract has failed checks")


def validate_evidence_artifact_summary(artifact: dict[str, Any], errors: list[str]) -> None:
    name = str(artifact.get("name") or "<unnamed>")
    status = artifact.get("status")
    summary = artifact.get("summary")
    if status == "passed" and not isinstance(summary, dict):
        errors.append(f"evidence artifact {name} is missing summary")
        return
    if not isinstance(summary, dict):
        return
    if status == "passed" and artifact.get("relative_path"):
        if summary.get("sensitive_scan_status") != "passed":
            errors.append(f"evidence artifact {name} sensitive scan must be passed")
        if summary.get("sensitive_scan_failed_count") not in {0, None}:
            errors.append(f"evidence artifact {name} sensitive scan has failed findings")

    contract_prefixes = NESTED_CONTRACT_FIELDS.get(name, ())
    for prefix in contract_prefixes:
        status_field = f"{prefix}_status"
        failed_count_field = f"{prefix}_failed_count"
        contract_status = summary.get(status_field)
        failed_count = summary.get(failed_count_field)
        if status == "passed":
            if contract_status not in {"passed", "warning"}:
                errors.append(
                    f"evidence artifact {name} {status_field} must be passed or warning, "
                    f"got {contract_status or '<missing>'}"
                )
            if failed_count not in {0, None}:
                errors.append(f"evidence artifact {name} {failed_count_field} must be 0")
        elif status == "skipped" and (status_field in summary or failed_count_field in summary):
            if contract_status not in {"skipped", "passed", "warning", None}:
                errors.append(
                    f"evidence artifact {name} {status_field} must be skipped, passed, or warning, "
                    f"got {contract_status or '<missing>'}"
                )
            if failed_count not in {0, None}:
                errors.append(f"evidence artifact {name} {failed_count_field} must be 0")


def validate_named_evidence_artifact(
    artifacts: list[Any],
    artifact_name: str,
    display_name: str,
    errors: list[str],
) -> None:
    artifact = next(
        (item for item in artifacts if isinstance(item, dict) and item.get("name") == artifact_name),
        None,
    )
    if artifact is None:
        errors.append(f"{display_name} artifact is missing")
        return
    if artifact.get("status") != "passed":
        errors.append(f"{display_name} artifact must be passed, got {artifact.get('status') or '<missing>'}")


def evidence_artifact_has_passed(payload: Any, artifact_name: str) -> bool:
    if not isinstance(payload, dict):
        return False
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return False
    return any(
        isinstance(artifact, dict) and artifact.get("name") == artifact_name and artifact.get("status") == "passed"
        for artifact in artifacts
    )


def validate_evidence_file_verification_gate(
    gate: Any,
    errors: list[str],
    *,
    display_name: str,
    require_rebuilt_report_match: bool = False,
    require_no_failed_evidence_checks: bool = False,
) -> None:
    if not isinstance(gate, dict):
        errors.append(f"{display_name} verification gate is missing")
        return
    payload = gate.get("payload")
    if not isinstance(payload, dict):
        errors.append(f"{display_name} verification payload is missing")
        return
    if payload.get("error"):
        errors.append(f"{display_name} verification report could not be read: {payload['error']}")
    if payload.get("exists") is not True:
        errors.append(f"{display_name} verification report was not recorded as existing")
    if payload.get("status") != "passed":
        errors.append(f"{display_name} verification status must be passed, got {payload.get('status') or '<missing>'}")
    if not payload.get("report_path"):
        errors.append(f"{display_name} verification report_path is required")
    if payload.get("report_status") != "passed":
        errors.append(f"{display_name} verification report_status must be passed")
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append(f"{display_name} verification summary is missing")
        return
    if summary.get("error_count") not in {0, None}:
        errors.append(f"{display_name} verification summary has errors")
    if require_rebuilt_report_match and summary.get("rebuilt_report_match") is not True:
        errors.append(f"{display_name} verification must match the supplied env file")
    if require_no_failed_evidence_checks and summary.get("failed_evidence_check_count") not in {0, None}:
        errors.append(f"{display_name} verification has failed evidence checks")


def validate_cleanup(cleanup: Any, errors: list[str], *, require_cleanup: bool) -> None:
    if not require_cleanup:
        return
    if not isinstance(cleanup, dict):
        errors.append("cleanup evidence is missing")
        return
    if cleanup.get("name") != CLEANUP_NAME:
        errors.append("cleanup result name is invalid")
    if cleanup.get("status") != "passed":
        errors.append(f"cleanup status must be passed, got {cleanup.get('status') or '<missing>'}")
    payload = cleanup.get("payload") if isinstance(cleanup.get("payload"), dict) else {}
    if not isinstance(payload.get("removed_count"), int):
        errors.append("cleanup payload removed_count is missing or invalid")


def validate_dependency_inventory(
    summary: Any,
    errors: list[str],
    warnings: list[str],
    *,
    require_dependency_inventory: bool,
    fail_on_dependency_review: bool,
) -> None:
    if not require_dependency_inventory:
        return
    if not isinstance(summary, dict):
        errors.append("dependency inventory summary is missing")
        return
    if not isinstance(summary.get("dependency_count"), int) or summary.get("dependency_count") <= 0:
        errors.append("dependency inventory dependency_count is missing or invalid")
    review_required_count = summary.get("review_required_count")
    if isinstance(review_required_count, int) and review_required_count > 0:
        message = dependency_inventory_review_message(summary)
        if fail_on_dependency_review:
            errors.append(message)
        else:
            warnings.append(message)


def dependency_inventory_review_message(summary: dict[str, Any]) -> str:
    review_required_count = summary.get("review_required_count")
    missing_count = summary.get("release_blocking_missing_install_count")
    review_required_items = summary.get("review_required")
    typed_review_required_items = (
        [item for item in review_required_items if isinstance(item, dict)]
        if isinstance(review_required_items, list)
        else []
    )
    if (
        isinstance(review_required_count, int)
        and review_required_count > 0
        and isinstance(missing_count, int)
        and missing_count > 0
        and isinstance(review_required_items, list)
        and len(typed_review_required_items) == len(review_required_items)
        and len(typed_review_required_items) == review_required_count
        and all(is_missing_install_review_item(item) for item in typed_review_required_items)
    ):
        return (
            f"dependency inventory has {review_required_count} review-required item(s) because "
            f"{missing_count} release-blocking package(s) are missing in this environment; "
            "regenerate and use the release image dependency inventory before go-live"
        )
    return f"dependency inventory has {review_required_count} review-required item(s)"


def is_missing_install_review_item(item: dict[str, Any]) -> bool:
    reason = str(item.get("reason") or "").lower()
    return item.get("installed") is False and "regenerate inventory in the release image" in reason


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a release preflight JSON report.")
    parser.add_argument("--report", type=Path, required=True, help="Path to release-preflight.json.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument("--allow-failed-report", action="store_true", help="Do not require release report passed=true.")
    parser.add_argument("--allow-skipped-evidence-pack", action="store_true", help="Do not require evidence pack gates.")
    parser.add_argument("--allow-skipped-frontend", action="store_true", help="Do not require frontend build gate.")
    parser.add_argument("--allow-skipped-benchmark-gate", action="store_true", help="Do not require benchmark gate.")
    parser.add_argument("--allow-skipped-smoke", action="store_true", help="Do not require API smoke gate.")
    parser.add_argument("--allow-missing-inventory", action="store_true", help="Do not require dependency inventory summary.")
    parser.add_argument("--allow-skipped-cleanup", action="store_true", help="Do not require cleanup evidence.")
    parser.add_argument("--fail-on-dependency-review", action="store_true", help="Fail when dependency inventory has review-required items.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = verify_release_preflight_report(
        args.report,
        require_passed_report=not args.allow_failed_report,
        require_evidence_pack=not args.allow_skipped_evidence_pack,
        require_frontend=not args.allow_skipped_frontend,
        require_benchmark_gate=not args.allow_skipped_benchmark_gate,
        require_smoke=not args.allow_skipped_smoke,
        require_dependency_inventory=not args.allow_missing_inventory,
        require_cleanup=not args.allow_skipped_cleanup,
        fail_on_dependency_review=args.fail_on_dependency_review,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "release preflight verification "
        f"{report['status']} "
        f"report={report['report_path']} "
        f"gates={summary['gate_count']} "
        f"errors={summary['error_count']} "
        f"warnings={summary['warning_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
