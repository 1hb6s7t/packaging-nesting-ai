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
PRODUCTION_ENV_ARTIFACT = "production_env_audit"
DEPLOYMENT_COMPOSE_ARTIFACT = "deployment_compose_audit"
DEPENDENCY_REVIEW_ARTIFACT = "dependency_review_audit"
EXTERNAL_ACCEPTANCE_ARTIFACT = "external_acceptance_audit"
FRONTEND_GATE = "frontend production build"
SMOKE_GATE = "API health smoke"
CLEANUP_NAME = "cleanup pycache"


def verify_release_preflight_report(
    report_path: Path,
    *,
    require_passed_report: bool = True,
    require_evidence_pack: bool = True,
    require_frontend: bool = True,
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

    gate_checks = [verify_gate_shape(item) for item in gates]
    gate_by_name = {item.get("name"): item for item in gates if isinstance(item, dict)}
    for check in gate_checks:
        if check["status"] == "failed":
            errors.extend(check["errors"])

    validate_backend_gate(gate_by_name, errors)
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
        if isinstance(gate, dict) and gate.get("status") not in {"passed"}
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


def verify_gate_shape(gate: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(gate, dict):
        return {"name": "<invalid>", "status": "failed", "errors": ["gate entry must be an object"]}
    name = str(gate.get("name") or "<unnamed>")
    status = gate.get("status")
    if status != "passed":
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
    if generation_gate is None:
        errors.append("release evidence pack generation gate is missing")
    else:
        validate_evidence_generation_payload(
            generation_gate.get("payload"),
            errors,
            require_production_env_artifact=require_production_env_artifact,
            require_dependency_review_artifact=require_dependency_review_artifact,
            require_external_acceptance_artifact=require_external_acceptance_artifact,
        )
    if verification_gate is None:
        errors.append("release evidence pack verification gate is missing")
    else:
        validate_evidence_verification_payload(
            verification_gate.get("payload"),
            errors,
            require_production_env_artifact=require_production_env_artifact,
            require_dependency_review_artifact=require_dependency_review_artifact,
            require_external_acceptance_artifact=require_external_acceptance_artifact,
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
