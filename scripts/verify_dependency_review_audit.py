from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import dependency_review_audit  # noqa: E402


VALID_REPORT_STATUSES = {"passed", "skipped", "failed"}
VALID_POLICY_STATUSES = {"passed", "warning", "skipped", "failed"}
DETAIL_COUNT_FIELDS = {
    "missing_ack_count": "missing",
    "not_approved_count": "not_approved",
    "stale_ack_count": "stale",
    "invalid_ack_count": "invalid",
    "expired_ack_count": "expired",
    "unmatched_ack_count": "unmatched",
}
SUMMARY_COUNT_FIELDS = (
    "review_required_count",
    "acknowledged_count",
    "approved_count",
    *DETAIL_COUNT_FIELDS.keys(),
    "policy_contract_failed_count",
    "policy_contract_warning_count",
)


def verify_dependency_review_audit(
    report_path: Path,
    *,
    require_passed_report: bool = True,
    require_review_file: bool | None = None,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    report_errors: list[str] = []
    report = read_json_object(resolved_report_path, report_errors)
    return verify_dependency_review_audit_payload(
        report,
        report_path=resolved_report_path,
        require_passed_report=require_passed_report,
        require_review_file=require_review_file,
        initial_errors=report_errors,
    )


def verify_dependency_review_audit_payload(
    report: dict[str, Any],
    *,
    report_path: Path | None = None,
    require_passed_report: bool = True,
    require_review_file: bool | None = None,
    initial_errors: list[str] | None = None,
) -> dict[str, Any]:
    report_errors = list(initial_errors or [])
    source_warnings = read_string_list(report, "warnings", report_errors)
    source_errors = read_string_list(report, "errors", report_errors)
    details = {field_name: read_object_list(report, field_name, report_errors) for field_name in DETAIL_COUNT_FIELDS.values()}
    summary = read_summary(report, report_errors)
    policy_contract = read_policy_contract(report.get("policy_contract"), report_errors)
    report_status = str(report.get("status") or "")
    resolved_require_review_file = resolve_require_review_file(
        report,
        override=require_review_file,
        errors=report_errors,
    )

    if report.get("schema_version") != 1:
        report_errors.append("dependency review audit schema_version must be 1")
    if not parse_report_datetime(report.get("generated_at")):
        report_errors.append("dependency review audit generated_at must be a timezone-aware ISO datetime")
    if report_status not in VALID_REPORT_STATUSES:
        report_errors.append(
            "dependency review audit status must be passed, skipped, or failed, "
            f"got {report_status or '<missing>'}"
        )
    if require_passed_report and report_status != "passed":
        report_errors.append(f"dependency review audit status must be passed, got {report_status or '<missing>'}")

    validate_path_field(report, "inventory_path", report_errors, allow_none=True)
    validate_path_field(report, "review_file", report_errors, allow_none=True)
    validate_summary_counts(
        report,
        summary,
        details,
        source_errors=source_errors,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_status_consistency(
        report,
        summary,
        source_errors=source_errors,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_policy_contract_matches_report(
        report,
        policy_contract,
        require_review_file=resolved_require_review_file,
        errors=report_errors,
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(report_path) if report_path is not None else None,
        "report_status": report_status,
        "status": "passed" if not report_errors else "failed",
        "summary": {
            "review_required_count": summary.get("review_required_count"),
            "acknowledged_count": summary.get("acknowledged_count"),
            "approved_count": summary.get("approved_count"),
            "source_error_count": len(source_errors),
            "source_warning_count": len(source_warnings),
            "policy_contract_status": policy_contract.get("status"),
            "policy_contract_failed_count": policy_contract.get("failed_count"),
            "policy_contract_warning_count": policy_contract.get("warning_count"),
            "error_count": len(report_errors),
        },
        "errors": report_errors,
        "warnings": source_warnings,
    }


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"dependency review audit could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("dependency review audit root must be an object")
        return {}
    return payload


def read_string_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"dependency review audit {key} must be a list")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append(item)
        else:
            errors.append(f"dependency review audit {key}[{index}] must be a string")
    return items


def read_object_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"dependency review audit {key} must be a list")
        return []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if isinstance(item, dict):
            items.append(item)
        else:
            errors.append(f"dependency review audit {key}[{index}] must be an object")
    return items


def read_summary(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("dependency review audit summary must be an object")
        return {}
    for field_name in SUMMARY_COUNT_FIELDS:
        value = summary.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"dependency review audit summary.{field_name} must be a non-negative integer")
    if summary.get("policy_contract_status") not in VALID_POLICY_STATUSES:
        errors.append(
            "dependency review audit summary.policy_contract_status must be passed, warning, skipped, or failed"
        )
    return summary


def read_policy_contract(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("dependency review audit policy_contract must be an object")
        return {}
    status = value.get("status")
    if status not in VALID_POLICY_STATUSES:
        errors.append(
            "dependency review audit policy_contract.status must be passed, warning, skipped, or failed, "
            f"got {status or '<missing>'}"
        )
    checks = value.get("checks")
    if not isinstance(checks, list):
        errors.append("dependency review audit policy_contract.checks must be a list")
        checks = []
    for key in ("passed_count", "warning_count", "failed_count"):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value.get(key) < 0:
            errors.append(f"dependency review audit policy_contract.{key} must be a non-negative integer")
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
            errors.append(f"dependency review audit policy_contract.{key} must be {expected}, got {value.get(key)!r}")
    if policy_codes(value.get("failed_checks")) != policy_codes(failed_checks):
        errors.append("dependency review audit policy_contract.failed_checks do not match failed checks")
    if policy_codes(value.get("warning_checks")) != policy_codes(warning_checks):
        errors.append("dependency review audit policy_contract.warning_checks do not match warning checks")
    return value


def validate_summary_counts(
    report: dict[str, Any],
    summary: dict[str, Any],
    details: dict[str, list[dict[str, Any]]],
    *,
    source_errors: list[str],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    for count_field, detail_field in DETAIL_COUNT_FIELDS.items():
        expected = len(details.get(detail_field, []))
        if summary.get(count_field) != expected:
            errors.append(f"dependency review audit summary.{count_field} must be {expected}, got {summary.get(count_field)!r}")

    review_required_count = summary.get("review_required_count")
    acknowledged_count = summary.get("acknowledged_count")
    missing_ack_count = summary.get("missing_ack_count")
    approved_count = summary.get("approved_count")
    if all(is_non_negative_int(value) for value in (review_required_count, acknowledged_count, missing_ack_count)):
        expected_acknowledged = review_required_count - missing_ack_count
        if acknowledged_count != expected_acknowledged:
            errors.append(
                "dependency review audit summary.acknowledged_count must equal "
                f"review_required_count - missing_ack_count: {acknowledged_count!r} != {expected_acknowledged!r}"
            )
    if is_non_negative_int(approved_count) and is_non_negative_int(acknowledged_count) and approved_count > acknowledged_count:
        errors.append("dependency review audit summary.approved_count must not exceed acknowledged_count")
    if summary.get("policy_contract_status") != policy_contract.get("status"):
        errors.append("dependency review audit summary.policy_contract_status does not match policy_contract status")
    if summary.get("policy_contract_failed_count") != policy_contract.get("failed_count"):
        errors.append("dependency review audit summary.policy_contract_failed_count does not match policy_contract")
    if summary.get("policy_contract_warning_count") != policy_contract.get("warning_count"):
        errors.append("dependency review audit summary.policy_contract_warning_count does not match policy_contract")
    if report.get("status") == "passed" and source_errors:
        errors.append("dependency review audit passed report must not contain source errors")


def validate_status_consistency(
    report: dict[str, Any],
    summary: dict[str, Any],
    *,
    source_errors: list[str],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    report_status = report.get("status")
    blocking_summary_count = sum(
        int(summary.get(field_name) or 0)
        for field_name in (
            "missing_ack_count",
            "not_approved_count",
            "stale_ack_count",
            "invalid_ack_count",
            "expired_ack_count",
        )
        if is_non_negative_int(summary.get(field_name))
    )
    policy_failed_count = policy_contract.get("failed_count")
    if report_status == "passed":
        if blocking_summary_count:
            errors.append("dependency review audit passed report must not contain blocking acknowledgement counts")
        if summary.get("approved_count") != summary.get("review_required_count"):
            errors.append("dependency review audit passed report approved_count must match review_required_count")
        if policy_failed_count not in {0, None}:
            errors.append("dependency review audit passed report must not contain failed policy checks")
    elif report_status == "skipped":
        if report.get("review_file"):
            errors.append("dependency review audit skipped report must not include review_file")
        if source_errors:
            errors.append("dependency review audit skipped report must not contain source errors")
        if summary.get("review_required_count") in {0, None}:
            errors.append("dependency review audit skipped report must have review-required items")
        if summary.get("missing_ack_count") != summary.get("review_required_count"):
            errors.append("dependency review audit skipped report missing_ack_count must match review_required_count")
        if policy_contract.get("status") != "skipped":
            errors.append("dependency review audit skipped report policy_contract must be skipped")
    elif report_status == "failed" and not source_errors and not blocking_summary_count and policy_failed_count in {0, None}:
        errors.append("dependency review audit failed report must include source errors, blocking counts, or failed policy checks")


def validate_policy_contract_matches_report(
    report: dict[str, Any],
    policy_contract: dict[str, Any],
    *,
    require_review_file: bool,
    errors: list[str],
) -> None:
    if not policy_contract:
        return
    recomputed = dependency_review_audit.validate_dependency_review_policy_contract(
        report,
        require_review_file=require_review_file,
    )
    if policy_signature(policy_contract) != policy_signature(recomputed):
        errors.append("dependency review audit policy_contract does not match recomputed report policy")
    if policy_contract.get("failed_count") not in {0, None} and report.get("status") == "passed":
        errors.append("dependency review audit status must not be passed when policy_contract has failures")


def resolve_require_review_file(
    report: dict[str, Any],
    *,
    override: bool | None,
    errors: list[str],
) -> bool:
    if override is not None:
        return override
    options = report.get("options")
    if options is None:
        return False
    if not isinstance(options, dict):
        errors.append("dependency review audit options must be an object")
        return False
    value = options.get("require_review_file")
    if not isinstance(value, bool):
        errors.append("dependency review audit options.require_review_file must be a boolean")
        return False
    return value


def validate_path_field(
    report: dict[str, Any],
    field_name: str,
    errors: list[str],
    *,
    allow_none: bool,
) -> None:
    value = report.get(field_name)
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value:
        errors.append(f"dependency review audit {field_name} must be a non-empty string or null")


def parse_report_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return dependency_review_audit.parse_datetime(value)


def is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
    parser = argparse.ArgumentParser(description="Verify a dependency review audit report offline.")
    parser.add_argument("--report", type=Path, required=True, help="Path to dependency-review-audit.json.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-non-passed-report",
        action="store_true",
        help="Verify consistency even when the dependency review audit is skipped or failed.",
    )
    parser.add_argument(
        "--require-review-file",
        action="store_true",
        help="Recompute the embedded policy contract using a required review file policy.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    verification = verify_dependency_review_audit(
        args.report,
        require_passed_report=not args.allow_non_passed_report,
        require_review_file=True if args.require_review_file else None,
    )
    if args.output:
        write_json(args.output, verification)
    summary = verification["summary"]
    print(
        "dependency review audit verification "
        f"{verification['status']} "
        f"report_status={verification['report_status'] or '<missing>'} "
        f"review_required={summary['review_required_count']} "
        f"approved={summary['approved_count']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
