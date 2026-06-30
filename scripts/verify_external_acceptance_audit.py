from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import external_acceptance_audit  # noqa: E402


VALID_REPORT_STATUSES = {"passed", "skipped", "failed"}
VALID_POLICY_STATUSES = {"passed", "warning", "skipped", "failed"}


def verify_external_acceptance_audit(
    report_path: Path,
    *,
    base_dir: Path | None = None,
    require_passed_report: bool = True,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    report_errors: list[str] = []
    report = read_json_object(resolved_report_path, report_errors)
    report_dir = resolved_report_path.parent
    evidence_base_dir = resolve_evidence_base_dir(
        report,
        base_dir=base_dir,
        report_dir=report_dir,
        errors=report_errors,
    )
    source_errors = read_string_list(report, "errors", report_errors)
    source_warnings = read_string_list(report, "warnings", report_errors)
    required_areas = read_string_list(report, "required_areas", report_errors)
    missing_areas = read_string_list(report, "missing_areas", report_errors)
    invalid_areas = read_object_list(report, "invalid_areas", report_errors)
    unmatched_areas = read_object_list(report, "unmatched_areas", report_errors)
    verified_evidence_files = read_object_list(report, "verified_evidence_files", report_errors)
    failed_evidence_files = read_object_list(report, "failed_evidence_files", report_errors)
    summary = read_summary(report, report_errors)
    policy_contract = read_policy_contract(report.get("policy_contract"), report_errors)
    report_status = str(report.get("status") or "")

    if report.get("schema_version") != 1:
        report_errors.append("external acceptance audit schema_version must be 1")
    if not parse_report_datetime(report.get("generated_at")):
        report_errors.append("external acceptance audit generated_at must be a timezone-aware ISO datetime")
    if report_status not in VALID_REPORT_STATUSES:
        report_errors.append(
            "external acceptance audit status must be passed, skipped, or failed, "
            f"got {report_status or '<missing>'}"
        )
    if require_passed_report and report_status != "passed":
        report_errors.append(f"external acceptance audit status must be passed, got {report_status or '<missing>'}")

    validate_scalar_fields(report, report_errors)
    validate_detail_entries(invalid_areas, unmatched_areas, failed_evidence_files, report_errors)
    validate_summary_counts(
        summary,
        report_status=report_status,
        required_areas=required_areas,
        missing_areas=missing_areas,
        invalid_areas=invalid_areas,
        unmatched_areas=unmatched_areas,
        verified_evidence_files=verified_evidence_files,
        failed_evidence_files=failed_evidence_files,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_status_consistency(
        report,
        summary,
        source_errors=source_errors,
        source_warnings=source_warnings,
        missing_areas=missing_areas,
        invalid_areas=invalid_areas,
        verified_evidence_files=verified_evidence_files,
        failed_evidence_files=failed_evidence_files,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_policy_contract_matches_report(report, policy_contract, report_errors)
    evidence_checks = verify_evidence_files(
        verified_evidence_files,
        base_dir=evidence_base_dir,
    )
    failed_evidence_checks = [check for check in evidence_checks if check["status"] == "failed"]
    for check in failed_evidence_checks:
        report_errors.extend(f"{check['name']}: {error}" for error in check["errors"])

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(resolved_report_path),
        "base_dir": str(evidence_base_dir),
        "report_status": report_status,
        "status": "passed" if not report_errors else "failed",
        "summary": {
            "required_area_count": len(required_areas),
            "passed_area_count": summary.get("passed_area_count"),
            "verified_evidence_file_count": len(verified_evidence_files),
            "failed_evidence_file_count": len(failed_evidence_files),
            "evidence_check_count": len(evidence_checks),
            "failed_evidence_check_count": len(failed_evidence_checks),
            "source_error_count": len(source_errors),
            "source_warning_count": len(source_warnings),
            "policy_contract_status": policy_contract.get("status"),
            "policy_contract_failed_count": policy_contract.get("failed_count"),
            "policy_contract_warning_count": policy_contract.get("warning_count"),
            "error_count": len(report_errors),
        },
        "errors": report_errors,
        "warnings": source_warnings,
        "evidence_checks": evidence_checks,
    }


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"external acceptance audit could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("external acceptance audit root must be an object")
        return {}
    return payload


def read_string_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"external acceptance audit {key} must be a list")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append(item)
        else:
            errors.append(f"external acceptance audit {key}[{index}] must be a string")
    return items


def read_object_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"external acceptance audit {key} must be a list")
        return []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            items.append(item)
        else:
            errors.append(f"external acceptance audit {key}[{index}] must be an object")
    return items


def read_summary(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("external acceptance audit summary must be an object")
        return {}
    for field_name in (
        "required_area_count",
        "passed_area_count",
        "missing_area_count",
        "invalid_area_count",
        "unmatched_area_count",
        "evidence_file_count",
        "verified_evidence_file_count",
        "failed_evidence_file_count",
        "policy_contract_failed_count",
        "policy_contract_warning_count",
    ):
        value = summary.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"external acceptance audit summary.{field_name} must be a non-negative integer")
    if summary.get("policy_contract_status") not in VALID_POLICY_STATUSES:
        errors.append(
            "external acceptance audit summary.policy_contract_status must be passed, warning, skipped, or failed"
        )
    return summary


def read_policy_contract(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("external acceptance audit policy_contract must be an object")
        return {}
    status = value.get("status")
    if status not in VALID_POLICY_STATUSES:
        errors.append(
            "external acceptance audit policy_contract.status must be passed, warning, skipped, or failed, "
            f"got {status or '<missing>'}"
        )
    checks = value.get("checks")
    if not isinstance(checks, list):
        errors.append("external acceptance audit policy_contract.checks must be a list")
        checks = []
    for key in ("passed_count", "warning_count", "failed_count"):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value.get(key) < 0:
            errors.append(f"external acceptance audit policy_contract.{key} must be a non-negative integer")
    failed_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "failed"]
    warning_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "warning"]
    passed_checks = [check for check in checks if isinstance(check, dict) and check.get("status") == "passed"]
    expected_counts = {
        "passed_count": len(passed_checks),
        "warning_count": len(warning_checks),
        "failed_count": len(failed_checks),
    }
    for key, expected in expected_counts.items():
        if value.get(key) != expected:
            errors.append(f"external acceptance audit policy_contract.{key} must be {expected}, got {value.get(key)!r}")
    if policy_codes(value.get("failed_checks")) != policy_codes(failed_checks):
        errors.append("external acceptance audit policy_contract.failed_checks do not match failed checks")
    if policy_codes(value.get("warning_checks")) != policy_codes(warning_checks):
        errors.append("external acceptance audit policy_contract.warning_checks do not match warning checks")
    return value


def validate_scalar_fields(report: dict[str, Any], errors: list[str]) -> None:
    if report.get("acceptance_file") is not None and not non_empty_string(report.get("acceptance_file")):
        errors.append("external acceptance audit acceptance_file must be a non-empty string or null")
    if not isinstance(report.get("require_acceptance_file"), bool):
        errors.append("external acceptance audit require_acceptance_file must be a boolean")
    if not non_empty_string(report.get("base_dir")):
        errors.append("external acceptance audit base_dir must be a non-empty string")


def validate_detail_entries(
    invalid_areas: list[dict[str, Any]],
    unmatched_areas: list[dict[str, Any]],
    failed_evidence_files: list[dict[str, Any]],
    errors: list[str],
) -> None:
    for index, item in enumerate(invalid_areas):
        if not non_empty_string(item.get("area")):
            errors.append(f"external acceptance audit invalid_areas[{index}].area is required")
        if not isinstance(item.get("errors"), list) or any(not isinstance(error, str) for error in item.get("errors", [])):
            errors.append(f"external acceptance audit invalid_areas[{index}].errors must be a list of strings")
    for index, item in enumerate(unmatched_areas):
        if not non_empty_string(item.get("area")):
            errors.append(f"external acceptance audit unmatched_areas[{index}].area is required")
    for index, item in enumerate(failed_evidence_files):
        if item.get("status") != "failed":
            errors.append(f"external acceptance audit failed_evidence_files[{index}].status must be failed")
        if not isinstance(item.get("errors"), list) or any(not isinstance(error, str) for error in item.get("errors", [])):
            errors.append(f"external acceptance audit failed_evidence_files[{index}].errors must be a list of strings")


def validate_summary_counts(
    summary: dict[str, Any],
    *,
    report_status: str,
    required_areas: list[str],
    missing_areas: list[str],
    invalid_areas: list[dict[str, Any]],
    unmatched_areas: list[dict[str, Any]],
    verified_evidence_files: list[dict[str, Any]],
    failed_evidence_files: list[dict[str, Any]],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    expected_counts = {
        "required_area_count": len(required_areas),
        "missing_area_count": len(missing_areas),
        "invalid_area_count": len(invalid_areas),
        "unmatched_area_count": len(unmatched_areas),
        "evidence_file_count": len(verified_evidence_files) + len(failed_evidence_files),
        "verified_evidence_file_count": len(verified_evidence_files),
        "failed_evidence_file_count": len(failed_evidence_files),
        "policy_contract_status": policy_contract.get("status"),
        "policy_contract_failed_count": policy_contract.get("failed_count"),
        "policy_contract_warning_count": policy_contract.get("warning_count"),
    }
    for key, expected in expected_counts.items():
        if summary.get(key) != expected:
            errors.append(f"external acceptance audit summary.{key} must be {expected!r}, got {summary.get(key)!r}")
    if required_areas and report_status != "skipped":
        expected_passed = len(required_areas) - len(missing_areas) - len(invalid_areas)
        if summary.get("passed_area_count") != expected_passed:
            errors.append(
                "external acceptance audit summary.passed_area_count must equal "
                f"required - missing - invalid: {summary.get('passed_area_count')!r} != {expected_passed!r}"
            )


def validate_status_consistency(
    report: dict[str, Any],
    summary: dict[str, Any],
    *,
    source_errors: list[str],
    source_warnings: list[str],
    missing_areas: list[str],
    invalid_areas: list[dict[str, Any]],
    verified_evidence_files: list[dict[str, Any]],
    failed_evidence_files: list[dict[str, Any]],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    report_status = report.get("status")
    blocking_count = len(source_errors) + len(missing_areas) + len(invalid_areas) + len(failed_evidence_files)
    policy_failed_count = policy_contract.get("failed_count")
    if report_status == "passed":
        if blocking_count:
            errors.append("external acceptance audit passed report must not contain blocking details")
        if summary.get("passed_area_count") != summary.get("required_area_count"):
            errors.append("external acceptance audit passed report passed_area_count must match required_area_count")
        if len(verified_evidence_files) < len(external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS):
            errors.append("external acceptance audit passed report must verify every required area's evidence")
        if policy_failed_count not in {0, None}:
            errors.append("external acceptance audit passed report must not contain failed policy checks")
        if policy_contract.get("status") not in {"passed", "warning"}:
            errors.append("external acceptance audit passed report policy_contract must be passed or warning")
    elif report_status == "skipped":
        if report.get("acceptance_file"):
            errors.append("external acceptance audit skipped report must not include acceptance_file")
        if report.get("require_acceptance_file"):
            errors.append("external acceptance audit skipped report must not require acceptance_file")
        if source_errors:
            errors.append("external acceptance audit skipped report must not contain source errors")
        if missing_areas or invalid_areas or verified_evidence_files or failed_evidence_files:
            errors.append("external acceptance audit skipped report must not include acceptance details")
        if not source_warnings:
            errors.append("external acceptance audit skipped report should include a warning")
        if policy_contract.get("status") != "skipped":
            errors.append("external acceptance audit skipped report policy_contract must be skipped")
    elif report_status == "failed" and not blocking_count and policy_failed_count in {0, None}:
        errors.append("external acceptance audit failed report must include source errors, blocking details, or failed policy checks")


def validate_policy_contract_matches_report(
    report: dict[str, Any],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    if not policy_contract:
        return
    recomputed = external_acceptance_audit.validate_external_acceptance_policy_contract(report)
    if policy_signature(policy_contract) != policy_signature(recomputed):
        errors.append("external acceptance audit policy_contract does not match recomputed report policy")
    if policy_contract.get("failed_count") not in {0, None} and report.get("status") == "passed":
        errors.append("external acceptance audit status must not be passed when policy_contract has failures")


def verify_evidence_files(items: list[dict[str, Any]], *, base_dir: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        errors: list[str] = []
        relative_path = item.get("relative_path")
        path = resolve_relative_evidence_path(relative_path, base_dir, errors)
        if item.get("status") != "passed":
            errors.append("verified evidence file status must be passed")
        description = item.get("description")
        if not non_empty_string(description):
            errors.append("verified evidence file description is required")
        expected_size = item.get("size_bytes")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size <= 0:
            errors.append("verified evidence file size_bytes must be a positive integer")
        expected_sha = item.get("sha256")
        if not external_acceptance_audit.is_sha256_hex(expected_sha):
            errors.append("verified evidence file sha256 must be a 64-character hex digest")
        actual_size: int | None = None
        actual_sha: str | None = None
        if path is None:
            errors.append("verified evidence file relative_path is missing or invalid")
        elif not path.is_file():
            errors.append(f"verified evidence file is missing: {relative_path}")
        else:
            actual_size = path.stat().st_size
            actual_sha = external_acceptance_audit.sha256_file(path)
            if isinstance(expected_size, int) and actual_size != expected_size:
                errors.append(f"verified evidence file size mismatch: expected {expected_size}, got {actual_size}")
            if external_acceptance_audit.is_sha256_hex(expected_sha) and actual_sha != str(expected_sha).lower():
                errors.append("verified evidence file sha256 mismatch")
        checks.append(
            {
                "name": f"verified_evidence_files[{index}]",
                "area": item.get("area"),
                "status": "failed" if errors else "passed",
                "relative_path": relative_path,
                "path": str(path) if path is not None else None,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": actual_size,
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "errors": errors,
            }
        )
    return checks


def resolve_evidence_base_dir(
    report: dict[str, Any],
    *,
    base_dir: Path | None,
    report_dir: Path,
    errors: list[str],
) -> Path:
    if base_dir is not None:
        return base_dir.resolve()
    report_base_dir = report.get("base_dir")
    if isinstance(report_base_dir, str) and report_base_dir:
        path = Path(report_base_dir)
        return path.resolve() if path.is_absolute() else (report_dir / path).resolve()
    errors.append("external acceptance audit base_dir must be provided or present in report")
    return report_dir


def resolve_relative_evidence_path(value: Any, base_dir: Path, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(f"verified evidence file relative_path is unsafe: {value}")
        return None
    return base_dir / relative


def parse_report_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return external_acceptance_audit.parse_datetime(value)


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def policy_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item.get("code")) for item in value if isinstance(item, dict) and item.get("code")]


def policy_signature(policy_contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": policy_contract.get("status"),
        "passed_count": policy_contract.get("passed_count"),
        "warning_count": policy_contract.get("warning_count"),
        "failed_count": policy_contract.get("failed_count"),
        "failed_codes": policy_codes(policy_contract.get("failed_checks")),
        "warning_codes": policy_codes(policy_contract.get("warning_checks")),
        "check_codes": policy_codes(policy_contract.get("checks")),
    }


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify an external acceptance audit report offline.")
    parser.add_argument("--report", type=Path, required=True, help="Path to external-acceptance-audit.json.")
    parser.add_argument("--base-dir", type=Path, help="Base directory used to resolve verified evidence files.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-non-passed-report",
        action="store_true",
        help="Verify consistency even when the external acceptance audit is skipped or failed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    verification = verify_external_acceptance_audit(
        args.report,
        base_dir=args.base_dir,
        require_passed_report=not args.allow_non_passed_report,
    )
    if args.output:
        write_json(args.output, verification)
    summary = verification["summary"]
    print(
        "external acceptance audit verification "
        f"{verification['status']} "
        f"report_status={verification['report_status'] or '<missing>'} "
        f"passed_areas={summary['passed_area_count']} "
        f"verified_files={summary['verified_evidence_file_count']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
