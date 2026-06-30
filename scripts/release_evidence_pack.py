from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))

import conversion_supplier_audit
import customer_sandbox_audit
import dependency_review_audit
import deployment_compose_audit
import external_acceptance_audit
import notification_channel_audit
import production_env_audit
import release_inventory
import repository_hygiene_audit
import solver_governance_audit
import storage_export_audit


ReportBuilder = Callable[[], dict[str, Any]]

SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "api_key",
    "access_key",
    "private_key",
    "authorization",
    "credential",
)
SENSITIVE_TOKEN_KEY_PARTS = ("token",)
SENSITIVE_EXACT_KEYS = {
    "webhook_endpoint_url",
    "webhook_url",
}
SENSITIVE_QUERY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "KEY",
    "AUTH",
    "SIGNATURE",
    "CREDENTIAL",
)
SENSITIVE_KEY_EXEMPTIONS = {
    "access_token_ttl_minutes",
    "api_key_header",
    "callback_token_history",
    "callback_token_rotated_at",
    "signature_header",
    "signature_timestamp_header",
    "token_rotation",
    "webhook_signature_header",
    "webhook_signature_timestamp_header",
}
SENSITIVE_KEY_SUFFIX_EXEMPTIONS = ("_hash", "_tail", "_fingerprint")
REDACTED_VALUE = "***"
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
FILELESS_OPTIONAL_ARTIFACTS = {"production_env_audit"}
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


def build_release_evidence_pack(
    *,
    output_dir: Path = Path("artifacts/release-evidence"),
    env_file: Path | None = None,
    require_production_env: bool = False,
    dependency_review_file: Path | None = None,
    require_dependency_review: bool = False,
    external_acceptance_file: Path | None = None,
    require_external_acceptance: bool = False,
    customer_pack: Path = customer_sandbox_audit.DEFAULT_PACK_PATH,
    notification_pack: Path = notification_channel_audit.DEFAULT_PACK_PATH,
    simulate_storage_missing: bool = False,
    simulate_conversion_submit_failure: bool = False,
    simulate_solver_enabled_stub: bool = False,
) -> dict[str, Any]:
    resolved_output_dir = resolve_output_dir(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, Any]] = []
    if env_file is not None:
        artifacts.append(
            run_report_artifact(
                name="production_env_audit",
                filename="production-env-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\production_env_audit.py",
                    "--env-file",
                    str(env_file),
                    "--output",
                    str(resolved_output_dir / "production-env-audit.json"),
                ],
                builder=lambda: production_env_audit.build_env_audit_report(env_file),
            )
        )
    else:
        artifacts.append(
            skipped_artifact(
                name="production_env_audit",
                required=require_production_env,
                reason="--env-file was not provided",
                command=[
                    "python",
                    "scripts\\production_env_audit.py",
                    "--env-file",
                    ".env.production",
                    "--output",
                    str(resolved_output_dir / "production-env-audit.json"),
                ],
            )
        )

    artifacts.extend(
        [
            run_report_artifact(
                name="deployment_compose_audit",
                filename="deployment-compose-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\deployment_compose_audit.py",
                    "--output",
                    str(resolved_output_dir / "deployment-compose-audit.json"),
                ],
                builder=lambda: deployment_compose_audit.build_deployment_compose_audit_report(),
            ),
            run_report_artifact(
                name="repository_hygiene_audit",
                filename="repository-hygiene-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\repository_hygiene_audit.py",
                    "--output",
                    str(resolved_output_dir / "repository-hygiene-audit.json"),
                ],
                builder=lambda: repository_hygiene_audit.build_repository_hygiene_audit(),
            ),
            run_report_artifact(
                name="customer_sandbox_audit",
                filename="customer-sandbox-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\customer_sandbox_audit.py",
                    "--pack",
                    str(customer_pack),
                    "--output",
                    str(resolved_output_dir / "customer-sandbox-audit.json"),
                ],
                builder=lambda: customer_sandbox_audit.build_customer_sandbox_audit_report(customer_pack),
            ),
            run_report_artifact(
                name="notification_channel_audit",
                filename="notification-channel-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\notification_channel_audit.py",
                    "--pack",
                    str(notification_pack),
                    "--output",
                    str(resolved_output_dir / "notification-channel-audit.json"),
                ],
                builder=lambda: notification_channel_audit.build_notification_channel_audit_report(notification_pack),
            ),
            run_report_artifact(
                name="storage_export_audit",
                filename="storage-export-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\storage_export_audit.py",
                    "--output",
                    str(resolved_output_dir / "storage-export-audit.json"),
                ],
                builder=lambda: storage_export_audit.build_storage_export_audit_report(simulate_missing=simulate_storage_missing),
            ),
            run_report_artifact(
                name="conversion_supplier_audit",
                filename="conversion-supplier-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\conversion_supplier_audit.py",
                    "--output",
                    str(resolved_output_dir / "conversion-supplier-audit.json"),
                ],
                builder=lambda: conversion_supplier_audit.build_conversion_supplier_audit_report(
                    simulate_submit_failure=simulate_conversion_submit_failure
                ),
            ),
            run_report_artifact(
                name="solver_governance_audit",
                filename="solver-governance-audit.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\solver_governance_audit.py",
                    "--output",
                    str(resolved_output_dir / "solver-governance-audit.json"),
                ],
                builder=lambda: solver_governance_audit.build_solver_governance_audit_report(
                    simulate_enabled_stub=simulate_solver_enabled_stub
                ),
            ),
            run_report_artifact(
                name="external_acceptance_audit",
                filename="external-acceptance-audit.json",
                output_dir=resolved_output_dir,
                command=external_acceptance_command(
                    resolved_output_dir / "external-acceptance-audit.json",
                    external_acceptance_file=external_acceptance_file,
                    require_external_acceptance=require_external_acceptance,
                ),
                builder=lambda: external_acceptance_audit.build_external_acceptance_audit(
                    acceptance_file=external_acceptance_file,
                    require_acceptance_file=require_external_acceptance,
                ),
                required=bool(external_acceptance_file or require_external_acceptance),
            ),
            run_report_artifact(
                name="dependency_inventory",
                filename="dependency-inventory.json",
                output_dir=resolved_output_dir,
                command=[
                    "python",
                    "scripts\\release_inventory.py",
                    "--output",
                    str(resolved_output_dir / "dependency-inventory.json"),
                ],
                builder=lambda: inventory_report(release_inventory.build_dependency_inventory(REPO_ROOT)),
            ),
            run_report_artifact(
                name="dependency_review_audit",
                filename="dependency-review-audit.json",
                output_dir=resolved_output_dir,
                command=dependency_review_command(
                    resolved_output_dir / "dependency-review-audit.json",
                    dependency_review_file=dependency_review_file,
                    require_dependency_review=require_dependency_review,
                ),
                builder=lambda: dependency_review_audit.build_dependency_review_audit(
                    inventory=release_inventory.build_dependency_inventory(REPO_ROOT),
                    review_file=dependency_review_file,
                    require_review_file=require_dependency_review,
                ),
                required=bool(dependency_review_file or require_dependency_review),
            ),
        ]
    )

    pack = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(REPO_ROOT),
        "output_dir": str(resolved_output_dir),
        "status": "failed",
        "summary": {},
        "artifacts": artifacts,
    }
    pack["summary"] = build_summary(artifacts)
    pack["status"] = "passed" if pack["summary"]["required_failed_count"] == 0 else "failed"
    pack = attach_policy_contract(pack)
    manifest_path = write_json(resolved_output_dir / "release-evidence-pack.json", pack)
    pack["manifest_path"] = str(manifest_path)
    manifest_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def run_report_artifact(
    *,
    name: str,
    filename: str,
    output_dir: Path,
    command: list[str],
    builder: ReportBuilder,
    required: bool = True,
) -> dict[str, Any]:
    output_path = output_dir / filename
    try:
        raw_report = builder()
        sensitive_scan = build_sensitive_scan(raw_report)
        report = redact_sensitive_evidence(raw_report)
        report["sensitive_scan"] = sensitive_scan
        write_json(output_path, report)
        status = str(report.get("status") or "passed")
        if sensitive_scan["failed_count"]:
            status = "failed"
        evidence = file_evidence(output_path, base_dir=output_dir)
        return {
            "name": name,
            "required": required,
            "status": status,
            **evidence,
            "command": command,
            "summary": artifact_summary(report),
        }
    except Exception as exc:
        return {
            "name": name,
            "required": required,
            "status": "failed",
            "path": str(output_path),
            "relative_path": None,
            "size_bytes": None,
            "sha256": None,
            "command": command,
            "summary": {"error": str(exc)},
        }


def skipped_artifact(*, name: str, required: bool, reason: str, command: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "required": required,
        "status": "failed" if required else "skipped",
        "path": None,
        "relative_path": None,
        "size_bytes": None,
        "sha256": None,
        "command": command,
        "summary": {"reason": reason},
    }


def inventory_report(inventory: dict[str, Any]) -> dict[str, Any]:
    return {**inventory, "status": "passed"}


def dependency_review_command(
    output_path: Path,
    *,
    dependency_review_file: Path | None,
    require_dependency_review: bool,
) -> list[str]:
    command = [
        "python",
        "scripts\\dependency_review_audit.py",
        "--output",
        str(output_path),
    ]
    if dependency_review_file is not None:
        command.extend(["--review-file", str(dependency_review_file)])
    if require_dependency_review:
        command.append("--require-review-file")
    return command


def external_acceptance_command(
    output_path: Path,
    *,
    external_acceptance_file: Path | None,
    require_external_acceptance: bool,
) -> list[str]:
    command = [
        "python",
        "scripts\\external_acceptance_audit.py",
        "--output",
        str(output_path),
    ]
    if external_acceptance_file is not None:
        command.extend(["--acceptance-file", str(external_acceptance_file)])
    if require_external_acceptance:
        command.append("--require-acceptance-file")
    return command


def artifact_summary(report: dict[str, Any]) -> dict[str, Any]:
    summary = dict(report.get("summary") or compact_report_summary(report))
    sensitive_scan = report.get("sensitive_scan") or {}
    if sensitive_scan:
        summary["sensitive_scan_status"] = sensitive_scan.get("status")
        summary["sensitive_scan_failed_count"] = sensitive_scan.get("failed_count", 0)
    return summary


def compact_report_summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = ("status", "error_count", "is_production")
    return {key: report[key] for key in keys if key in report}


def build_sensitive_scan(payload: Any) -> dict[str, Any]:
    findings = scan_sensitive_evidence(payload)
    return {
        "status": "failed" if findings else "passed",
        "failed_count": len(findings),
        "findings": findings,
    }


def scan_sensitive_evidence(payload: Any, path: str = "$") -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}"
            if is_sensitive_evidence_key(key_text):
                if contains_unredacted_sensitive_value(value):
                    findings.append(
                        {
                            "path": next_path,
                            "key": key_text,
                            "reason": "sensitive field is not redacted",
                        }
                    )
                continue
            findings.extend(scan_sensitive_evidence(value, next_path))
        return findings
    if isinstance(payload, list):
        for index, item in enumerate(payload):
            findings.extend(scan_sensitive_evidence(item, f"{path}[{index}]"))
        return findings
    if isinstance(payload, str) and url_contains_unredacted_secret(payload):
        findings.append(
            {
                "path": path,
                "key": "",
                "reason": "URL contains an unredacted password or sensitive query parameter",
            }
        )
    return findings


def contains_unredacted_sensitive_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, str):
        return value != REDACTED_VALUE
    if isinstance(value, (int, float, bool)):
        return False
    if isinstance(value, list):
        return any(contains_unredacted_sensitive_value(item) for item in value)
    if isinstance(value, dict):
        return any(contains_unredacted_sensitive_value(item) for item in value.values())
    return True


def redact_sensitive_evidence(payload: Any, key: str | None = None) -> Any:
    if key is not None and is_sensitive_evidence_key(key):
        return REDACTED_VALUE if contains_unredacted_sensitive_value(payload) else payload
    if isinstance(payload, dict):
        return {item_key: redact_sensitive_evidence(value, str(item_key)) for item_key, value in payload.items()}
    if isinstance(payload, list):
        return [redact_sensitive_evidence(item) for item in payload]
    if isinstance(payload, str):
        return redact_url_sensitive_parts(payload)
    return payload


def is_sensitive_evidence_key(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in SENSITIVE_KEY_EXEMPTIONS or key_lower.endswith(SENSITIVE_KEY_SUFFIX_EXEMPTIONS):
        return False
    if key_lower in SENSITIVE_EXACT_KEYS:
        return True
    if any(part in key_lower for part in SENSITIVE_KEY_PARTS):
        return True
    if any(part in key_lower for part in SENSITIVE_TOKEN_KEY_PARTS):
        return "ttl" not in key_lower
    return False


def redact_url_sensitive_parts(value: str) -> str:
    if not is_url_like(value):
        return value
    return redact_url_query_secrets(redact_url_password(value))


def redact_url_password(value: str) -> str:
    parts = urlsplit(value)
    if parts.password is None:
        return value
    username = parts.username or ""
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = username
    if username:
        netloc += f":{REDACTED_VALUE}@"
    else:
        netloc = f"{REDACTED_VALUE}@"
    netloc += host
    if parts.port is not None:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_url_query_secrets(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    redacted_items = [
        (name, REDACTED_VALUE if is_sensitive_query_name(name) else item_value)
        for name, item_value in query_items
    ]
    if redacted_items == query_items:
        return value
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(redacted_items, doseq=True, safe="*"),
            parts.fragment,
        )
    )


def url_contains_unredacted_secret(value: str) -> bool:
    if not is_url_like(value):
        return False
    parts = urlsplit(value)
    if parts.password not in {None, REDACTED_VALUE}:
        return True
    for name, item_value in parse_qsl(parts.query, keep_blank_values=True):
        if is_sensitive_query_name(name) and item_value != REDACTED_VALUE:
            return True
    return False


def is_url_like(value: str) -> bool:
    return bool(urlsplit(value).scheme and "://" in value)


def is_sensitive_query_name(name: str) -> bool:
    upper_name = name.upper()
    return any(marker in upper_name for marker in SENSITIVE_QUERY_MARKERS)


def build_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [item for item in artifacts if item.get("status") not in {"passed", "skipped"}]
    required_failed = [item for item in failed if item.get("required")]
    skipped = [item for item in artifacts if item.get("status") == "skipped"]
    passed = [item for item in artifacts if item.get("status") == "passed"]
    return {
        "artifact_count": len(artifacts),
        "required_count": sum(1 for item in artifacts if item.get("required")),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "required_failed_count": len(required_failed),
        "skipped_count": len(skipped),
        "failed_artifacts": [str(item.get("name")) for item in failed],
        "skipped_artifacts": [str(item.get("name")) for item in skipped],
    }


def attach_policy_contract(pack: dict[str, Any]) -> dict[str, Any]:
    policy_contract = validate_release_evidence_pack_policy_contract(pack)
    pack["policy_contract"] = policy_contract
    summary = pack.get("summary")
    if isinstance(summary, dict):
        summary["policy_contract_status"] = policy_contract.get("status")
        summary["policy_contract_failed_count"] = int(policy_contract.get("failed_count") or 0)
        summary["policy_contract_warning_count"] = int(policy_contract.get("warning_count") or 0)
    if int(policy_contract.get("failed_count") or 0):
        pack["status"] = "failed"
    return pack


def validate_release_evidence_pack_policy_contract(pack: dict[str, Any]) -> dict[str, Any]:
    artifacts = pack.get("artifacts") if isinstance(pack.get("artifacts"), list) else []
    summary = pack.get("summary") if isinstance(pack.get("summary"), dict) else {}
    artifact_names = [str(item.get("name")) for item in artifacts if isinstance(item, dict) and item.get("name")]
    missing_artifacts = [name for name in EXPECTED_ARTIFACT_NAMES if name not in artifact_names]
    unexpected_artifacts = sorted(name for name in artifact_names if name not in EXPECTED_ARTIFACT_NAMES)
    expected_status = "passed" if summary.get("required_failed_count") == 0 else "failed"

    checks = [
        policy_check(
            code="schema.version",
            status="passed" if pack.get("schema_version") == 1 else "failed",
            message="release evidence pack schema_version is 1"
            if pack.get("schema_version") == 1
            else "release evidence pack schema_version must be 1",
            evidence={"schema_version": pack.get("schema_version")},
        ),
        policy_check(
            code="manifest.summary",
            status="passed" if summary_matches_artifacts(summary, artifacts) else "failed",
            message="release evidence pack summary matches artifact statuses"
            if summary_matches_artifacts(summary, artifacts)
            else "release evidence pack summary must match artifact statuses",
            evidence=summary_consistency_evidence(summary, artifacts),
        ),
        policy_check(
            code="manifest.status",
            status="passed" if pack.get("status") == expected_status else "failed",
            message="release evidence pack status matches required artifact failures"
            if pack.get("status") == expected_status
            else "release evidence pack status must match required artifact failures",
            evidence={"status": pack.get("status"), "expected_status": expected_status},
        ),
        policy_check(
            code="artifacts.expected_set",
            status="failed" if missing_artifacts else "warning" if unexpected_artifacts else "passed",
            message="release evidence pack contains the expected artifact set"
            if not missing_artifacts and not unexpected_artifacts
            else "release evidence pack artifact set should match the delivery contract",
            evidence={"missing_artifacts": missing_artifacts, "unexpected_artifacts": unexpected_artifacts},
        ),
        policy_check(
            code="artifacts.required_passed",
            status="passed" if not required_artifact_failures(artifacts) else "failed",
            message="all required release evidence artifacts passed"
            if not required_artifact_failures(artifacts)
            else "all required release evidence artifacts must pass",
            evidence={"failed_required_artifacts": required_artifact_failures(artifacts)},
        ),
        policy_check(
            code="artifacts.optional_boundary",
            status="passed" if not optional_boundary_errors(artifacts) else "failed",
            message="optional skipped artifacts stay within the documented delivery boundary"
            if not optional_boundary_errors(artifacts)
            else "optional skipped artifacts must not be marked required or fail silently",
            evidence={"errors": optional_boundary_errors(artifacts)},
        ),
        policy_check(
            code="artifacts.integrity_fields",
            status="passed" if not artifact_integrity_field_errors(artifacts) else "failed",
            message="generated release evidence artifacts include relative path, size, and SHA-256"
            if not artifact_integrity_field_errors(artifacts)
            else "generated release evidence artifacts must include relative path, size, and SHA-256",
            evidence={"errors": artifact_integrity_field_errors(artifacts)},
        ),
        policy_check(
            code="artifacts.sensitive_scan",
            status="passed" if not sensitive_scan_errors(artifacts) else "failed",
            message="generated release evidence artifacts passed sensitive value scanning"
            if not sensitive_scan_errors(artifacts)
            else "generated release evidence artifacts must pass sensitive value scanning",
            evidence={"errors": sensitive_scan_errors(artifacts)},
        ),
        nested_contract_policy_check(artifacts),
        dependency_inventory_policy_check(artifacts),
        dependency_review_policy_check(artifacts),
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


def summary_matches_artifacts(summary: dict[str, Any], artifacts: list[Any]) -> bool:
    return not summary_consistency_errors(summary, artifacts)


def summary_consistency_evidence(summary: dict[str, Any], artifacts: list[Any]) -> dict[str, Any]:
    return {
        "errors": summary_consistency_errors(summary, artifacts),
        "observed": summary_consistency_values(artifacts),
        "summary": {key: summary.get(key) for key in summary_consistency_values(artifacts)},
    }


def summary_consistency_errors(summary: dict[str, Any], artifacts: list[Any]) -> list[str]:
    observed = summary_consistency_values(artifacts)
    errors: list[str] = []
    for key, value in observed.items():
        if summary.get(key) != value:
            errors.append(f"summary.{key} must be {value}, got {summary.get(key)!r}")
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


def required_artifact_failures(artifacts: list[Any]) -> list[str]:
    failures: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not artifact.get("required"):
            continue
        if artifact.get("status") != "passed":
            failures.append(str(artifact.get("name") or "<unnamed>"))
    return failures


def optional_boundary_errors(artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("artifact entry must be an object")
            continue
        name = str(artifact.get("name") or "<unnamed>")
        status = artifact.get("status")
        required = bool(artifact.get("required"))
        has_file = artifact_has_file_evidence(artifact)
        if status == "skipped" and required:
            errors.append(f"{name} is skipped but marked required")
        if status not in {"passed", "skipped"}:
            errors.append(f"{name} has unsupported status for evidence pack handoff: {status or '<missing>'}")
        if status == "skipped" and not has_file and name not in FILELESS_OPTIONAL_ARTIFACTS:
            errors.append(f"{name} is skipped without file evidence")
    return errors


def artifact_integrity_field_errors(artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or "<unnamed>")
        if artifact.get("status") == "skipped" and not artifact_has_file_evidence(artifact):
            continue
        relative_path = artifact.get("relative_path")
        size_bytes = artifact.get("size_bytes")
        sha256 = artifact.get("sha256")
        if not isinstance(relative_path, str) or not relative_path:
            errors.append(f"{name} relative_path is missing")
        if not isinstance(size_bytes, int) or size_bytes <= 0:
            errors.append(f"{name} size_bytes is missing or invalid")
        if not is_sha256_hex(sha256):
            errors.append(f"{name} sha256 is missing or invalid")
    return errors


def sensitive_scan_errors(artifacts: list[Any]) -> list[str]:
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not artifact_has_file_evidence(artifact):
            continue
        name = str(artifact.get("name") or "<unnamed>")
        summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
        if summary.get("sensitive_scan_status") != "passed":
            errors.append(f"{name} sensitive_scan_status must be passed")
        if summary.get("sensitive_scan_failed_count") not in {0, None}:
            errors.append(f"{name} sensitive_scan_failed_count must be 0")
    return errors


def nested_contract_policy_check(artifacts: list[Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        name = str(artifact.get("name") or "")
        contract_prefixes = NESTED_CONTRACT_FIELDS.get(name, ())
        if not contract_prefixes or (artifact.get("status") == "skipped" and not artifact_has_file_evidence(artifact)):
            continue
        summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
        for prefix in contract_prefixes:
            status = summary.get(f"{prefix}_status")
            failed_count = summary.get(f"{prefix}_failed_count")
            warning_count = summary.get(f"{prefix}_warning_count")
            allowed_statuses = {"passed", "warning"}
            if artifact.get("status") == "skipped" and not artifact.get("required"):
                allowed_statuses.add("skipped")
            if status not in allowed_statuses:
                errors.append(f"{name} {prefix}_status must be one of {sorted(allowed_statuses)}, got {status or '<missing>'}")
            if failed_count not in {0, None}:
                errors.append(f"{name} {prefix}_failed_count must be 0")
            if isinstance(warning_count, int) and warning_count > 0:
                warnings.append(f"{name} {prefix} has {warning_count} warning check(s)")
    return policy_check(
        code="artifacts.nested_contracts",
        status="failed" if errors else "warning" if warnings else "passed",
        message="nested artifact policy contracts passed without warnings"
        if not errors and not warnings
        else "nested artifact policy contracts must pass and warnings need release-owner review",
        evidence={"errors": errors, "warnings": warnings},
    )


def dependency_inventory_policy_check(artifacts: list[Any]) -> dict[str, Any]:
    inventory = artifact_by_name(artifacts).get("dependency_inventory") or {}
    summary = inventory.get("summary") if isinstance(inventory.get("summary"), dict) else {}
    missing_count = summary.get("release_blocking_missing_install_count")
    if not isinstance(missing_count, int):
        missing_count = summary.get("missing_install_count")
    warning = isinstance(missing_count, int) and missing_count > 0
    return policy_check(
        code="dependency_inventory.release_image_required",
        status="warning" if warning else "passed",
        message="dependency inventory has no release-blocking missing installed packages"
        if not warning
        else "dependency inventory has release-blocking packages missing from the local environment; regenerate in the release image before go-live",
        evidence={
            "release_blocking_missing_install_count": missing_count,
            "review_required_count": summary.get("review_required_count"),
        },
    )


def dependency_review_policy_check(artifacts: list[Any]) -> dict[str, Any]:
    by_name = artifact_by_name(artifacts)
    inventory_summary = by_name.get("dependency_inventory", {}).get("summary")
    review_summary = by_name.get("dependency_review_audit", {}).get("summary")
    review_artifact = by_name.get("dependency_review_audit") or {}
    inventory_summary = inventory_summary if isinstance(inventory_summary, dict) else {}
    review_summary = review_summary if isinstance(review_summary, dict) else {}
    review_required_count = inventory_summary.get("review_required_count")
    approved_count = review_summary.get("approved_count")
    errors: list[str] = []
    warnings: list[str] = []
    if isinstance(review_required_count, int) and review_required_count > 0:
        if review_artifact.get("status") == "skipped":
            warnings.append("dependency review audit is skipped while dependency inventory has review-required items")
        elif review_artifact.get("status") != "passed":
            errors.append("dependency review audit must pass when dependency inventory has review-required items")
        elif approved_count != review_required_count:
            errors.append("dependency review approved_count must match inventory review_required_count")
    return policy_check(
        code="dependency_review.signoff",
        status="failed" if errors else "warning" if warnings else "passed",
        message="dependency review signoff covers review-required inventory items"
        if not errors and not warnings
        else "dependency review signoff must cover review-required inventory items before go-live",
        evidence={
            "review_required_count": review_required_count,
            "approved_count": approved_count,
            "errors": errors,
            "warnings": warnings,
        },
    )


def artifact_by_name(artifacts: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in artifacts
        if isinstance(item, dict) and item.get("name")
    }


def artifact_has_file_evidence(artifact: dict[str, Any]) -> bool:
    return bool(
        artifact.get("relative_path")
        or artifact.get("path")
        or artifact.get("size_bytes") is not None
        or artifact.get("sha256")
    )


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


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


def file_evidence(path: Path, *, base_dir: Path | None = None) -> dict[str, Any]:
    relative_path = path.name
    if base_dir is not None:
        relative_path = path.relative_to(base_dir).as_posix()
    return {
        "path": str(path),
        "relative_path": relative_path,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_output_dir(output_dir: Path) -> Path:
    return output_dir if output_dir.is_absolute() else REPO_ROOT / output_dir


def write_json(output_path: Path, payload: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a consolidated local release evidence pack.")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/release-evidence"), help="Directory for JSON evidence artifacts.")
    parser.add_argument("--env-file", type=Path, help="Optional production env file to audit and include.")
    parser.add_argument("--require-production-env", action="store_true", help="Fail the evidence pack when --env-file is omitted.")
    parser.add_argument("--dependency-review-file", type=Path, help="Optional dependency review acknowledgement JSON.")
    parser.add_argument(
        "--require-dependency-review",
        action="store_true",
        help="Fail the evidence pack when review-required dependencies do not have approved acknowledgements.",
    )
    parser.add_argument("--external-acceptance-file", type=Path, help="Optional real external release acceptance manifest JSON.")
    parser.add_argument(
        "--require-external-acceptance",
        action="store_true",
        help="Fail the evidence pack unless real external release acceptance evidence passes.",
    )
    parser.add_argument("--customer-pack", type=Path, default=customer_sandbox_audit.DEFAULT_PACK_PATH)
    parser.add_argument("--notification-pack", type=Path, default=notification_channel_audit.DEFAULT_PACK_PATH)
    parser.add_argument("--simulate-storage-missing", action="store_true", help="Internal validation mode: make storage export audit fail.")
    parser.add_argument("--simulate-conversion-submit-failure", action="store_true", help="Internal validation mode: make conversion supplier audit fail.")
    parser.add_argument("--simulate-solver-enabled-stub", action="store_true", help="Internal validation mode: leave an enabled solver stub.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pack = build_release_evidence_pack(
        output_dir=args.output_dir,
        env_file=args.env_file,
        require_production_env=args.require_production_env,
        dependency_review_file=args.dependency_review_file,
        require_dependency_review=args.require_dependency_review,
        external_acceptance_file=args.external_acceptance_file,
        require_external_acceptance=args.require_external_acceptance,
        customer_pack=args.customer_pack,
        notification_pack=args.notification_pack,
        simulate_storage_missing=args.simulate_storage_missing,
        simulate_conversion_submit_failure=args.simulate_conversion_submit_failure,
        simulate_solver_enabled_stub=args.simulate_solver_enabled_stub,
    )
    summary = pack["summary"]
    print(
        "release evidence pack "
        f"{pack['status']} "
        f"manifest={pack['manifest_path']} "
        f"artifacts={summary['artifact_count']} "
        f"failed={summary['failed_count']} "
        f"skipped={summary['skipped_count']} "
        f"policy={summary.get('policy_contract_status')}",
        flush=True,
    )
    return 0 if pack["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
