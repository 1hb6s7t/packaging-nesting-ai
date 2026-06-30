from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import production_env_audit  # noqa: E402


VALID_REPORT_STATUSES = {"passed", "failed"}
VALID_POLICY_STATUSES = {"passed", "warning", "failed"}
COUNT_FIELDS = (
    "error_count",
    "parse_error_count",
    "settings_error_count",
    "production_mode_error_count",
    "security_error_count",
    "placeholder_error_count",
    "template_domain_error_count",
)
SUMMARY_FIELDS = (
    "status",
    "is_production",
    "error_count",
    "parse_error_count",
    "settings_error_count",
    "security_error_count",
    "placeholder_error_count",
    "template_domain_error_count",
    "missing_recommended_key_count",
    "policy_contract_status",
    "policy_contract_failed_count",
    "policy_contract_warning_count",
)


def verify_production_env_audit(
    report_path: Path,
    *,
    env_file: Path | None = None,
    require_passed_report: bool = True,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    report_errors: list[str] = []
    report = read_json_object(resolved_report_path, report_errors)
    report_status = str(report.get("status") or "")
    source_errors = read_string_list(report, "errors", report_errors)
    missing_recommended_keys = read_string_list(report, "missing_recommended_keys", report_errors)
    summary = read_summary(report, report_errors)
    redacted_settings = read_redacted_settings(report, report_errors)
    policy_contract = read_policy_contract(report.get("policy_contract"), report_errors)

    if report.get("schema_version") != 1:
        report_errors.append("production env audit schema_version must be 1")
    if not parse_report_datetime(report.get("generated_at")):
        report_errors.append("production env audit generated_at must be a timezone-aware ISO datetime")
    if report_status not in VALID_REPORT_STATUSES:
        report_errors.append(f"production env audit status must be passed or failed, got {report_status or '<missing>'}")
    if require_passed_report and report_status != "passed":
        report_errors.append(f"production env audit status must be passed, got {report_status or '<missing>'}")

    validate_scalar_fields(report, report_errors)
    validate_summary_counts(
        report,
        summary,
        source_errors=source_errors,
        missing_recommended_keys=missing_recommended_keys,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_status_consistency(
        report,
        source_errors=source_errors,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_redaction(redacted_settings, report_errors)

    resolved_env_file = resolve_env_file(env_file, report, errors=report_errors)
    rebuilt_match = None
    if resolved_env_file is not None:
        if not resolved_env_file.is_file():
            report_errors.append(f"production env audit env_file does not exist: {resolved_env_file}")
            rebuilt_match = False
        else:
            rebuilt = production_env_audit.build_env_audit_report(resolved_env_file)
            rebuilt_match = validate_rebuilt_report_matches(report, rebuilt, report_errors)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(resolved_report_path),
        "env_file": str(resolved_env_file) if resolved_env_file is not None else None,
        "report_status": report_status,
        "status": "passed" if not report_errors else "failed",
        "summary": {
            "is_production": report.get("is_production"),
            "source_error_count": len(source_errors),
            "missing_recommended_key_count": len(missing_recommended_keys),
            "redacted_setting_count": len(redacted_settings),
            "policy_contract_status": policy_contract.get("status"),
            "policy_contract_failed_count": policy_contract.get("failed_count"),
            "policy_contract_warning_count": policy_contract.get("warning_count"),
            "rebuilt_report_match": rebuilt_match,
            "error_count": len(report_errors),
        },
        "errors": report_errors,
        "warnings": [],
    }


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"production env audit could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("production env audit root must be an object")
        return {}
    return payload


def read_string_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"production env audit {key} must be a list")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append(item)
        else:
            errors.append(f"production env audit {key}[{index}] must be a string")
    return items


def read_summary(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        errors.append("production env audit summary must be an object")
        return {}
    for field_name in (
        "error_count",
        "parse_error_count",
        "settings_error_count",
        "security_error_count",
        "placeholder_error_count",
        "template_domain_error_count",
        "missing_recommended_key_count",
        "policy_contract_failed_count",
        "policy_contract_warning_count",
    ):
        value = summary.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"production env audit summary.{field_name} must be a non-negative integer")
    if summary.get("policy_contract_status") not in VALID_POLICY_STATUSES:
        errors.append("production env audit summary.policy_contract_status must be passed, warning, or failed")
    return summary


def read_redacted_settings(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    value = payload.get("redacted_settings")
    if not isinstance(value, dict):
        errors.append("production env audit redacted_settings must be an object")
        return {}
    return value


def read_policy_contract(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("production env audit policy_contract must be an object")
        return {}
    status = value.get("status")
    if status not in VALID_POLICY_STATUSES:
        errors.append(
            "production env audit policy_contract.status must be passed, warning, or failed, "
            f"got {status or '<missing>'}"
        )
    checks = value.get("checks")
    if not isinstance(checks, list):
        errors.append("production env audit policy_contract.checks must be a list")
        checks = []
    for key in ("passed_count", "warning_count", "failed_count"):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value.get(key) < 0:
            errors.append(f"production env audit policy_contract.{key} must be a non-negative integer")
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
            errors.append(f"production env audit policy_contract.{key} must be {expected}, got {value.get(key)!r}")
    if policy_codes(value.get("failed_checks")) != policy_codes(failed_checks):
        errors.append("production env audit policy_contract.failed_checks do not match failed checks")
    if policy_codes(value.get("warning_checks")) != policy_codes(warning_checks):
        errors.append("production env audit policy_contract.warning_checks do not match warning checks")
    return value


def validate_scalar_fields(report: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(report.get("env_file"), str) or not report.get("env_file"):
        errors.append("production env audit env_file must be a non-empty string")
    if not isinstance(report.get("is_production"), bool):
        errors.append("production env audit is_production must be a boolean")
    for field_name in COUNT_FIELDS:
        value = report.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"production env audit {field_name} must be a non-negative integer")


def validate_summary_counts(
    report: dict[str, Any],
    summary: dict[str, Any],
    *,
    source_errors: list[str],
    missing_recommended_keys: list[str],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    expected_summary = {
        "status": report.get("status"),
        "is_production": report.get("is_production"),
        "error_count": report.get("error_count", 0),
        "parse_error_count": report.get("parse_error_count", 0),
        "settings_error_count": report.get("settings_error_count", 0),
        "security_error_count": report.get("security_error_count", 0),
        "placeholder_error_count": report.get("placeholder_error_count", 0),
        "template_domain_error_count": report.get("template_domain_error_count", 0),
        "missing_recommended_key_count": len(report.get("missing_recommended_keys") or []),
        "policy_contract_status": policy_contract.get("status"),
        "policy_contract_failed_count": policy_contract.get("failed_count"),
        "policy_contract_warning_count": policy_contract.get("warning_count"),
    }
    for field_name in SUMMARY_FIELDS:
        expected = expected_summary.get(field_name)
        if summary.get(field_name) != expected:
            errors.append(f"production env audit summary.{field_name} must be {expected!r}, got {summary.get(field_name)!r}")
    if report.get("error_count") != len(source_errors):
        errors.append(f"production env audit error_count must be {len(source_errors)}, got {report.get('error_count')!r}")
    if summary.get("missing_recommended_key_count") != len(missing_recommended_keys):
        errors.append(
            "production env audit summary.missing_recommended_key_count must match missing_recommended_keys length: "
            f"{summary.get('missing_recommended_key_count')!r} != {len(missing_recommended_keys)!r}"
        )
    if summary.get("policy_contract_status") != policy_contract.get("status"):
        errors.append("production env audit summary.policy_contract_status does not match policy_contract status")
    if summary.get("policy_contract_failed_count") != policy_contract.get("failed_count"):
        errors.append("production env audit summary.policy_contract_failed_count does not match policy_contract")
    if summary.get("policy_contract_warning_count") != policy_contract.get("warning_count"):
        errors.append("production env audit summary.policy_contract_warning_count does not match policy_contract")


def validate_status_consistency(
    report: dict[str, Any],
    *,
    source_errors: list[str],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    policy_failed_count = policy_contract.get("failed_count")
    if report.get("status") == "passed":
        if source_errors:
            errors.append("production env audit passed report must not contain source errors")
        if report.get("is_production") is not True:
            errors.append("production env audit passed report must have is_production=true")
        if policy_failed_count not in {0, None}:
            errors.append("production env audit passed report must not contain failed policy checks")
    elif report.get("status") == "failed" and not source_errors and policy_failed_count in {0, None}:
        errors.append("production env audit failed report must include source errors or failed policy checks")


def validate_redaction(redacted_settings: dict[str, Any], errors: list[str]) -> None:
    if not production_env_audit.redacted_settings_policy_ok(redacted_settings):
        errors.append("production env audit redacted_settings must redact sensitive values and URL secrets")


def resolve_env_file(env_file: Path | None, report: dict[str, Any], *, errors: list[str]) -> Path | None:
    if env_file is not None:
        return env_file.resolve()
    value = report.get("env_file")
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = production_env_audit.REPO_ROOT / candidate
    resolved = candidate.resolve()
    if resolved.is_file():
        return resolved
    if env_file is not None:
        errors.append(f"production env audit env_file does not exist: {resolved}")
    return None


def validate_rebuilt_report_matches(
    report: dict[str, Any],
    rebuilt: dict[str, Any],
    errors: list[str],
) -> bool:
    actual = report_signature(report)
    expected = report_signature(rebuilt)
    if actual == expected:
        return True
    errors.append("production env audit report does not match rebuilt audit from env_file")
    for key, expected_value in expected.items():
        if actual.get(key) != expected_value:
            errors.append(
                f"production env audit rebuilt mismatch for {key}: "
                f"report={actual.get(key)!r} rebuilt={expected_value!r}"
            )
    return False


def report_signature(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "is_production": report.get("is_production"),
        "error_count": report.get("error_count"),
        "parse_error_count": report.get("parse_error_count"),
        "settings_error_count": report.get("settings_error_count"),
        "production_mode_error_count": report.get("production_mode_error_count"),
        "security_error_count": report.get("security_error_count"),
        "placeholder_error_count": report.get("placeholder_error_count"),
        "template_domain_error_count": report.get("template_domain_error_count"),
        "errors": report.get("errors"),
        "missing_recommended_keys": report.get("missing_recommended_keys"),
        "redacted_settings": report.get("redacted_settings"),
        "summary": {key: (report.get("summary") or {}).get(key) for key in SUMMARY_FIELDS},
        "policy_contract": policy_signature(report.get("policy_contract") or {}),
    }


def parse_report_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "T" not in text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


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
    parser = argparse.ArgumentParser(description="Verify a production env audit report offline.")
    parser.add_argument("--report", type=Path, required=True, help="Path to production-env-audit.json.")
    parser.add_argument("--env-file", type=Path, help="Optional production env file used to rebuild and compare the audit.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-failed-report",
        action="store_true",
        help="Verify consistency even when the production env audit did not pass.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    verification = verify_production_env_audit(
        args.report,
        env_file=args.env_file,
        require_passed_report=not args.allow_failed_report,
    )
    if args.output:
        write_json(args.output, verification)
    summary = verification["summary"]
    print(
        "production env audit verification "
        f"{verification['status']} "
        f"report_status={verification['report_status'] or '<missing>'} "
        f"is_production={summary['is_production']} "
        f"rebuilt_match={summary['rebuilt_report_match']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
