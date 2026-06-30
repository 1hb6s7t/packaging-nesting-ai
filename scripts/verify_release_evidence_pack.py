from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


EXPECTED_ARTIFACT_NAMES = (
    "production_env_audit",
    "deployment_compose_audit",
    "repository_hygiene_audit",
    "customer_sandbox_audit",
    "notification_channel_audit",
    "storage_export_audit",
    "conversion_supplier_audit",
    "solver_governance_audit",
    "external_acceptance_audit",
    "dependency_inventory",
    "dependency_review_audit",
)
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


def verify_release_evidence_pack(
    manifest_path: Path,
    *,
    require_passed_pack: bool = True,
) -> dict[str, Any]:
    resolved_manifest_path = manifest_path.resolve()
    manifest = read_json(resolved_manifest_path)
    manifest_dir = resolved_manifest_path.parent
    manifest_errors: list[str] = []

    if manifest.get("schema_version") != 1:
        manifest_errors.append("manifest schema_version must be 1")
    manifest_status = str(manifest.get("status") or "")
    if require_passed_pack and manifest_status != "passed":
        manifest_errors.append(f"manifest status must be passed, got {manifest_status or '<missing>'}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest_errors.append("manifest artifacts must be a list")

    checks = [verify_artifact(item, manifest_dir, require_passed_pack=require_passed_pack) for item in artifacts]
    manifest_errors.extend(
        validate_manifest_policy_contract(
            manifest,
            artifacts,
            require_passed_pack=require_passed_pack,
        )
    )
    failed_checks = [item for item in checks if item["status"] == "failed"]
    skipped_checks = [item for item in checks if item["status"] == "skipped"]
    verified_checks = [item for item in checks if item["status"] == "passed"]
    status = "passed" if not manifest_errors and not failed_checks else "failed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest_path": str(resolved_manifest_path),
        "manifest_status": manifest_status,
        "status": status,
        "summary": {
            "artifact_count": len(checks),
            "verified_count": len(verified_checks),
            "failed_count": len(failed_checks),
            "skipped_count": len(skipped_checks),
            "manifest_error_count": len(manifest_errors),
            "failed_artifacts": [str(item["name"]) for item in failed_checks],
        },
        "manifest_errors": manifest_errors,
        "checks": checks,
    }


def verify_artifact(
    artifact: Any,
    manifest_dir: Path,
    *,
    require_passed_pack: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return {
            "name": "<invalid>",
            "artifact_status": "<invalid>",
            "status": "failed",
            "path": None,
            "relative_path": None,
            "expected_size_bytes": None,
            "actual_size_bytes": None,
            "expected_sha256": None,
            "actual_sha256": None,
            "errors": ["artifact entry must be an object"],
        }

    name = str(artifact.get("name") or "<unnamed>")
    artifact_status = str(artifact.get("status") or "")
    relative_path = artifact.get("relative_path")
    expected_size = artifact.get("size_bytes")
    expected_sha256 = artifact.get("sha256")
    has_file_evidence = bool(relative_path or artifact.get("path") or expected_size is not None or expected_sha256)
    if artifact_status == "skipped" and not has_file_evidence:
        return {
            "name": name,
            "artifact_status": artifact_status,
            "status": "skipped",
            "path": None,
            "relative_path": relative_path,
            "expected_size_bytes": expected_size,
            "actual_size_bytes": None,
            "expected_sha256": expected_sha256,
            "actual_sha256": None,
            "errors": [],
        }

    if require_passed_pack and artifact_status not in {"passed", "skipped"}:
        errors.append(f"artifact status must be passed, got {artifact_status or '<missing>'}")

    artifact_path: Path | None = None
    actual_size: int | None = None
    actual_sha256: str | None = None

    try:
        artifact_path = resolve_artifact_path(artifact, manifest_dir)
    except ValueError as exc:
        errors.append(str(exc))

    if artifact_path is None:
        errors.append("artifact path is missing")
    elif not artifact_path.is_file():
        errors.append("artifact file is missing")
    else:
        actual_size = artifact_path.stat().st_size
        actual_sha256 = sha256_file(artifact_path)
        if not isinstance(expected_size, int):
            errors.append("artifact size_bytes is missing or invalid")
        elif actual_size != expected_size:
            errors.append(f"artifact size mismatch: expected {expected_size}, got {actual_size}")
        if not is_sha256_hex(expected_sha256):
            errors.append("artifact sha256 is missing or invalid")
        elif actual_sha256 != expected_sha256:
            errors.append("artifact sha256 mismatch")

    return {
        "name": name,
        "artifact_status": artifact_status,
        "status": "failed" if errors else "passed",
        "path": str(artifact_path) if artifact_path is not None else None,
        "relative_path": relative_path,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "errors": errors,
    }


def validate_manifest_policy_contract(
    manifest: dict[str, Any],
    artifacts: list[Any],
    *,
    require_passed_pack: bool,
) -> list[str]:
    errors: list[str] = []
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    policy_contract = manifest.get("policy_contract") if isinstance(manifest.get("policy_contract"), dict) else None
    artifact_names = [str(item.get("name")) for item in artifacts if isinstance(item, dict) and item.get("name")]
    missing_artifacts = [name for name in EXPECTED_ARTIFACT_NAMES if name not in artifact_names]

    if missing_artifacts:
        errors.append(f"manifest artifacts are missing expected entries: {', '.join(missing_artifacts)}")
    errors.extend(summary_consistency_errors(summary, artifacts))
    errors.extend(artifact_summary_policy_errors(artifacts))

    if policy_contract is None:
        errors.append("manifest policy_contract is missing or invalid")
        return errors

    policy_status = policy_contract.get("status")
    policy_failed_count = policy_contract.get("failed_count")
    policy_warning_count = policy_contract.get("warning_count")
    policy_checks = policy_contract.get("checks")
    if policy_status not in {"passed", "warning", "failed"}:
        errors.append(f"manifest policy_contract status is invalid: {policy_status or '<missing>'}")
    if not isinstance(policy_failed_count, int):
        errors.append("manifest policy_contract failed_count is missing or invalid")
    if not isinstance(policy_warning_count, int):
        errors.append("manifest policy_contract warning_count is missing or invalid")
    if not isinstance(policy_checks, list) or not policy_checks:
        errors.append("manifest policy_contract checks must be a non-empty list")
    if summary.get("policy_contract_status") != policy_status:
        errors.append("manifest summary policy_contract_status does not match policy_contract status")
    if summary.get("policy_contract_failed_count") != policy_failed_count:
        errors.append("manifest summary policy_contract_failed_count does not match policy_contract failed_count")
    if summary.get("policy_contract_warning_count") != policy_warning_count:
        errors.append("manifest summary policy_contract_warning_count does not match policy_contract warning_count")
    if require_passed_pack and policy_status not in {"passed", "warning"}:
        errors.append(f"manifest policy_contract must be passed or warning, got {policy_status or '<missing>'}")
    if require_passed_pack and policy_failed_count not in {0, None}:
        errors.append("manifest policy_contract has failed checks")
    return errors


def artifact_summary_policy_errors(artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or "<unnamed>")
        status = artifact.get("status")
        required = bool(artifact.get("required"))
        summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
        if required and status != "passed":
            errors.append(f"{name} required artifact status must be passed, got {status or '<missing>'}")
        if status == "skipped" and required:
            errors.append(f"{name} cannot be skipped when marked required")
        if artifact_has_file_evidence(artifact):
            if summary.get("sensitive_scan_status") != "passed":
                errors.append(f"{name} sensitive_scan_status must be passed")
            if summary.get("sensitive_scan_failed_count") not in {0, None}:
                errors.append(f"{name} sensitive_scan_failed_count must be 0")
        errors.extend(nested_contract_errors(name, status, required, summary, has_file=artifact_has_file_evidence(artifact)))
    return errors


def nested_contract_errors(
    name: str,
    status: Any,
    required: bool,
    summary: dict[str, Any],
    *,
    has_file: bool,
) -> list[str]:
    errors: list[str] = []
    if status == "skipped" and not has_file:
        return errors
    for prefix in NESTED_CONTRACT_FIELDS.get(name, ()):
        contract_status = summary.get(f"{prefix}_status")
        failed_count = summary.get(f"{prefix}_failed_count")
        allowed_statuses = {"passed", "warning"}
        if status == "skipped" and not required:
            allowed_statuses.add("skipped")
        if contract_status not in allowed_statuses:
            errors.append(f"{name} {prefix}_status must be one of {sorted(allowed_statuses)}, got {contract_status or '<missing>'}")
        if failed_count not in {0, None}:
            errors.append(f"{name} {prefix}_failed_count must be 0")
    return errors


def artifact_has_file_evidence(artifact: dict[str, Any]) -> bool:
    return bool(
        artifact.get("relative_path")
        or artifact.get("path")
        or artifact.get("size_bytes") is not None
        or artifact.get("sha256")
    )


def summary_consistency_errors(summary: dict[str, Any], artifacts: list[Any]) -> list[str]:
    observed = summary_consistency_values(artifacts)
    errors: list[str] = []
    for key, value in observed.items():
        if summary.get(key) != value:
            errors.append(f"manifest summary.{key} must be {value}, got {summary.get(key)!r}")
    return errors


def summary_consistency_values(artifacts: list[Any]) -> dict[str, Any]:
    artifact_items = [item for item in artifacts if isinstance(item, dict)]
    failed = [item for item in artifact_items if item.get("status") not in {"passed", "skipped"}]
    required_failed = [item for item in failed if item.get("required")]
    skipped = [item for item in artifact_items if item.get("status") == "skipped"]
    passed = [item for item in artifact_items if item.get("status") == "passed"]
    return {
        "artifact_count": len(artifact_items),
        "required_count": sum(1 for item in artifact_items if item.get("required")),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "required_failed_count": len(required_failed),
        "skipped_count": len(skipped),
        "failed_artifacts": [str(item.get("name")) for item in failed],
        "skipped_artifacts": [str(item.get("name")) for item in skipped],
    }


def resolve_artifact_path(artifact: dict[str, Any], manifest_dir: Path) -> Path | None:
    relative_path = artifact.get("relative_path")
    if relative_path:
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact relative_path is unsafe: {relative_path}")
        return manifest_dir / relative

    path_value = artifact.get("path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if path.is_absolute():
        if path.exists():
            return path
        return manifest_dir / path.name
    return manifest_dir / path


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify release evidence pack artifact size and SHA-256 integrity.")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to release-evidence-pack.json.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-failed-pack",
        action="store_true",
        help="Verify file integrity even when the manifest or an artifact status is failed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = verify_release_evidence_pack(args.manifest, require_passed_pack=not args.allow_failed_pack)
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "release evidence verification "
        f"{report['status']} "
        f"manifest={report['manifest_path']} "
        f"verified={summary['verified_count']} "
        f"failed={summary['failed_count']} "
        f"skipped={summary['skipped_count']} "
        f"manifest_errors={summary['manifest_error_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
