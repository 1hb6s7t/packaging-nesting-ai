from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PASSED_ARTIFACTS = {
    "release_preflight_report": "release preflight report",
    "release_preflight_verification": "release preflight verification",
    "release_evidence_manifest": "release evidence manifest",
    "release_evidence_verification": "release evidence verification",
    "release_evidence_artifact:deployment_compose_audit": "deployment compose audit",
    "release_evidence_artifact:repository_hygiene_audit": "repository hygiene audit",
    "release_evidence_artifact:production_env_audit": "production env audit",
    "release_evidence_artifact:external_acceptance_audit": "external acceptance audit",
    "dependency_inventory": "dependency inventory",
    "dependency_review_audit": "dependency review audit",
    "release_image_dependency_audit": "release image dependency audit",
}
POLICY_CONTRACT_ARTIFACTS = {
    "release_evidence_artifact:deployment_compose_audit": "deployment compose audit",
    "release_evidence_artifact:repository_hygiene_audit": "repository hygiene audit",
    "release_evidence_artifact:production_env_audit": "production env audit",
    "release_evidence_artifact:external_acceptance_audit": "external acceptance audit",
    "dependency_review_audit": "dependency review audit",
    "release_image_dependency_audit": "release image dependency audit",
}


def build_go_live_readiness_audit(
    *,
    handoff_manifest: Path,
    handoff_verification: Path | None = None,
) -> dict[str, Any]:
    resolved_manifest = resolve_repo_path(handoff_manifest)
    resolved_verification = resolve_repo_path(handoff_verification) if handoff_verification else None
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    manifest_payload: dict[str, Any] | None = None
    try:
        manifest_payload = read_json(resolved_manifest)
    except Exception as exc:
        blockers.append(f"handoff manifest could not be read: {exc}")

    if manifest_payload is not None:
        checks.append(validate_handoff_manifest(manifest_payload))
        artifact_checks = validate_required_artifacts(manifest_payload)
        checks.extend(artifact_checks)
        artifact_policy_checks = validate_artifact_policy_contracts(manifest_payload)
        checks.extend(artifact_policy_checks)
        dependency_checks = validate_dependency_readiness(manifest_payload)
        checks.extend(dependency_checks)

    if resolved_verification is not None:
        try:
            verification_payload = read_json(resolved_verification)
        except Exception as exc:
            checks.append(failed_check("handoff_verification", f"handoff verification could not be read: {exc}"))
        else:
            checks.append(validate_handoff_verification(verification_payload, expected_manifest=resolved_manifest))
    else:
        checks.append(failed_check("handoff_verification", "handoff verification was not provided"))

    for check in checks:
        if check.get("status") == "failed":
            blockers.extend(str(error) for error in check.get("errors", []))

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not blockers else "failed",
        "handoff_manifest": str(resolved_manifest),
        "handoff_verification": str(resolved_verification) if resolved_verification else None,
        "summary": {
            "check_count": len(checks),
            "passed_check_count": sum(1 for check in checks if check.get("status") == "passed"),
            "failed_check_count": sum(1 for check in checks if check.get("status") == "failed"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
        },
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "policy_contract": {},
    }
    return attach_policy_contract(report)


def validate_handoff_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("handoff manifest schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"handoff manifest status must be passed, got {payload.get('status') or '<missing>'}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("handoff manifest artifacts must be a non-empty list")
    return check_result("handoff_manifest", errors, summary=dict(payload.get("summary") or {}))


def validate_handoff_verification(payload: dict[str, Any], *, expected_manifest: Path) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("handoff verification schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"handoff verification status must be passed, got {payload.get('status') or '<missing>'}")
    manifest_path = payload.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        errors.append("handoff verification manifest_path is required")
    else:
        try:
            verification_manifest = resolve_repo_path(Path(manifest_path)).resolve()
            expected_resolved = expected_manifest.resolve()
        except OSError as exc:
            errors.append(f"handoff verification manifest_path could not be resolved: {exc}")
        else:
            if verification_manifest != expected_resolved:
                errors.append(
                    "handoff verification manifest_path must match handoff manifest: "
                    f"{verification_manifest} != {expected_resolved}"
                )
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if summary.get("failed_count") not in {0, None}:
        errors.append("handoff verification has failed artifact checks")
    if summary.get("manifest_error_count") not in {0, None}:
        errors.append("handoff verification has manifest errors")
    return check_result("handoff_verification", errors, summary=dict(summary))


def validate_required_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = artifact_by_name(payload)
    checks: list[dict[str, Any]] = []
    for artifact_name, display_name in REQUIRED_PASSED_ARTIFACTS.items():
        artifact = artifacts.get(artifact_name)
        errors: list[str] = []
        if artifact is None:
            errors.append(f"{display_name} artifact is missing")
        elif artifact.get("status") != "passed":
            errors.append(f"{display_name} artifact must be passed, got {artifact.get('status') or '<missing>'}")
        checks.append(check_result(artifact_name, errors, summary=dict((artifact or {}).get("summary") or {})))
    return checks


def validate_dependency_readiness(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = artifact_by_name(payload)
    inventory = artifacts.get("dependency_inventory") or {}
    inventory_summary = inventory.get("summary") if isinstance(inventory.get("summary"), dict) else {}
    review = artifacts.get("dependency_review_audit") or {}
    review_summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    checks: list[dict[str, Any]] = []

    inventory_errors: list[str] = []
    missing_install_count = inventory_summary.get("missing_install_count")
    release_blocking_missing_install_count = inventory_summary.get("release_blocking_missing_install_count")
    blocking_missing_install_count = (
        release_blocking_missing_install_count
        if isinstance(release_blocking_missing_install_count, int)
        else missing_install_count
    )
    if blocking_missing_install_count not in {0, None}:
        inventory_errors.append(
            f"dependency inventory has {blocking_missing_install_count} release-blocking missing installed package(s); "
            "run scripts\\release_image_dependency_audit.py and pass its inventory to release_handoff_bundle.py"
        )
    checks.append(check_result("dependency_inventory_release_image", inventory_errors, summary=dict(inventory_summary)))

    review_errors: list[str] = []
    review_required_count = inventory_summary.get("review_required_count")
    approved_count = review_summary.get("approved_count")
    if isinstance(review_required_count, int) and review_required_count > 0:
        if review.get("status") != "passed":
            review_errors.append("dependency review audit must pass when inventory has review-required items")
        elif approved_count != review_required_count:
            review_errors.append(
                f"dependency review approved_count must match review_required_count: {approved_count} != {review_required_count}"
            )
    checks.append(check_result("dependency_review_signoff", review_errors, summary=dict(review_summary)))
    return checks


def validate_artifact_policy_contracts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = artifact_by_name(payload)
    checks: list[dict[str, Any]] = []
    for artifact_name, display_name in POLICY_CONTRACT_ARTIFACTS.items():
        artifact = artifacts.get(artifact_name)
        summary = artifact_policy_summary(artifact)
        policy_status = summary.get("policy_contract_status")
        policy_failed_count = summary.get("policy_contract_failed_count")
        errors: list[str] = []
        if artifact is None:
            checks.append(check_result(f"{artifact_name}:policy_contract", errors, summary=dict(summary)))
            continue
        if artifact.get("status") != "passed":
            checks.append(check_result(f"{artifact_name}:policy_contract", errors, summary=dict(summary)))
            continue
        if not summary:
            errors.append(f"{display_name} policy contract summary is missing")
        elif policy_status not in {"passed", "warning"}:
            errors.append(f"{display_name} policy contract must be passed or warning, got {policy_status or '<missing>'}")
        if policy_failed_count not in {0, None}:
            errors.append(f"{display_name} policy contract has failed checks")
        checks.append(check_result(f"{artifact_name}:policy_contract", errors, summary=dict(summary)))
    return checks


def artifact_policy_summary(artifact: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {}
    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        return {}
    evidence_summary = summary.get("evidence_summary")
    if isinstance(evidence_summary, dict):
        return dict(evidence_summary)
    return dict(summary)


def attach_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    policy_contract = validate_go_live_readiness_policy_contract(report)
    report["policy_contract"] = policy_contract
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["policy_contract_status"] = policy_contract.get("status")
        summary["policy_contract_failed_count"] = int(policy_contract.get("failed_count") or 0)
        summary["policy_contract_warning_count"] = int(policy_contract.get("warning_count") or 0)
    if int(policy_contract.get("failed_count") or 0):
        report["status"] = "failed"
    return report


def validate_go_live_readiness_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    check_by_name = {str(check.get("name")): check for check in checks if isinstance(check, dict) and check.get("name")}
    blockers = [str(blocker) for blocker in report.get("blockers") or []]
    warnings = [str(warning) for warning in report.get("warnings") or []]
    required_artifact_names = list(REQUIRED_PASSED_ARTIFACTS)
    artifact_policy_check_names = [f"{name}:policy_contract" for name in POLICY_CONTRACT_ARTIFACTS]
    policy_checks = [
        policy_check(
            code="schema.version",
            status="passed" if report.get("schema_version") == 1 else "failed",
            message="go-live readiness audit schema_version is 1"
            if report.get("schema_version") == 1
            else "go-live readiness audit schema_version must be 1",
            evidence={"schema_version": report.get("schema_version")},
        ),
        policy_check(
            code="handoff.manifest",
            status=aggregate_check_status(check_by_name, ["handoff_manifest"]),
            message="release handoff manifest is present and passed"
            if aggregate_check_status(check_by_name, ["handoff_manifest"]) == "passed"
            else "release handoff manifest must be present and passed",
            evidence=checks_status_evidence(check_by_name, ["handoff_manifest"]),
        ),
        policy_check(
            code="handoff.verification",
            status=aggregate_check_status(check_by_name, ["handoff_verification"]),
            message="release handoff verification is present, passed, and matches the manifest"
            if aggregate_check_status(check_by_name, ["handoff_verification"]) == "passed"
            else "release handoff verification must be present, passed, and match the manifest",
            evidence=checks_status_evidence(check_by_name, ["handoff_verification"]),
        ),
        policy_check(
            code="handoff.required_artifacts",
            status=aggregate_check_status(check_by_name, required_artifact_names),
            message="all go-live required handoff artifacts are present and passed"
            if aggregate_check_status(check_by_name, required_artifact_names) == "passed"
            else "all go-live required handoff artifacts must be present and passed",
            evidence=checks_status_evidence(check_by_name, required_artifact_names),
        ),
        policy_check(
            code="handoff.artifact_policy_contracts",
            status=aggregate_check_status(check_by_name, artifact_policy_check_names),
            message="go-live artifact policy contracts have no failed checks"
            if aggregate_check_status(check_by_name, artifact_policy_check_names) == "passed"
            else "go-live artifact policy contracts must have no failed checks",
            evidence=checks_status_evidence(check_by_name, artifact_policy_check_names),
        ),
        policy_check(
            code="dependency.release_image_inventory",
            status=aggregate_check_status(check_by_name, ["dependency_inventory_release_image"]),
            message="dependency inventory is backed by release image evidence with no release-blocking missing installs"
            if aggregate_check_status(check_by_name, ["dependency_inventory_release_image"]) == "passed"
            else "dependency inventory must be backed by release image evidence with no release-blocking missing installs",
            evidence=checks_status_evidence(check_by_name, ["dependency_inventory_release_image"]),
        ),
        policy_check(
            code="dependency.review_signoff",
            status=aggregate_check_status(check_by_name, ["dependency_review_signoff"]),
            message="dependency review signoff covers review-required items"
            if aggregate_check_status(check_by_name, ["dependency_review_signoff"]) == "passed"
            else "dependency review signoff must cover review-required items",
            evidence=checks_status_evidence(check_by_name, ["dependency_review_signoff"]),
        ),
        policy_check(
            code="blockers.clear",
            status="passed" if not blockers else "failed",
            message="go-live readiness has no blockers"
            if not blockers
            else "go-live readiness blockers must be cleared",
            evidence={"blocker_count": len(blockers), "blockers": blockers},
        ),
        policy_check(
            code="warnings.clear",
            status="warning" if warnings else "passed",
            message="go-live readiness has no warnings"
            if not warnings
            else "go-live readiness warnings should be reviewed before handoff",
            evidence={"warning_count": len(warnings), "warnings": warnings},
        ),
    ]
    failed_count = sum(1 for check in policy_checks if check["status"] == "failed")
    warning_count = sum(1 for check in policy_checks if check["status"] == "warning")
    passed_count = sum(1 for check in policy_checks if check["status"] == "passed")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in policy_checks if check["status"] == "failed"],
        "warning_checks": [check for check in policy_checks if check["status"] == "warning"],
        "checks": policy_checks,
    }


def aggregate_check_status(check_by_name: dict[str, dict[str, Any]], names: list[str]) -> str:
    statuses = [str(check_by_name.get(name, {}).get("status") or "failed") for name in names]
    return "failed" if any(status == "failed" for status in statuses) else "passed"


def checks_status_evidence(check_by_name: dict[str, dict[str, Any]], names: list[str]) -> dict[str, Any]:
    return {
        "checks": {name: str(check_by_name.get(name, {}).get("status") or "missing") for name in names},
        "failed_checks": [
            name for name in names if str(check_by_name.get(name, {}).get("status") or "failed") == "failed"
        ],
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


def artifact_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return {}
    return {str(item.get("name")): item for item in artifacts if isinstance(item, dict) and item.get("name")}


def check_result(name: str, errors: list[str], *, summary: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": "failed" if errors else "passed",
        "summary": summary or {},
        "errors": errors,
    }


def failed_check(name: str, error: str) -> dict[str, Any]:
    return check_result(name, [error])


def resolve_repo_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else REPO_ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit whether a release handoff bundle is sufficient for production go-live.")
    parser.add_argument("--handoff-manifest", type=Path, required=True, help="Path to release-handoff-bundle.json.")
    parser.add_argument(
        "--handoff-verification",
        type=Path,
        help="Path to release-handoff-verification.json. Missing verification fails readiness.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON go-live readiness report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_go_live_readiness_audit(
        handoff_manifest=args.handoff_manifest,
        handoff_verification=args.handoff_verification,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "go-live readiness audit "
        f"{report['status']} "
        f"checks={summary['check_count']} "
        f"blockers={summary['blocker_count']} "
        f"warnings={summary['warning_count']} "
        f"policy={summary.get('policy_contract_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
