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

    return {
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
    }


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
        f"warnings={summary['warning_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
