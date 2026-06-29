from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def verify_go_live_readiness_report(
    report_path: Path,
    *,
    allowed_blockers: list[str] | None = None,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    allowed = sorted(set(allowed_blockers or []))
    errors: list[str] = []
    report = read_json_object(resolved_report_path, errors)

    if report.get("schema_version") != 1:
        errors.append("go-live readiness report schema_version must be 1")

    report_status = str(report.get("status") or "")
    blockers = read_string_list(report, "blockers", errors)
    report_warnings = read_string_list(report, "warnings", errors)
    checks = read_checks(report.get("checks"), errors)
    validate_summary(report.get("summary"), blockers, report_warnings, checks, errors)

    duplicate_blockers = sorted({item for item in blockers if blockers.count(item) > 1})
    if duplicate_blockers:
        errors.append(f"go-live readiness report has duplicate blockers: {', '.join(duplicate_blockers)}")

    blocker_set = set(blockers)
    allowed_set = set(allowed)
    unexpected_blockers = sorted(blocker_set - allowed_set)
    missing_allowed_blockers = sorted(allowed_set - blocker_set)

    expected_status = "passed" if not blockers else "failed"
    if report_status != expected_status:
        errors.append(
            "go-live readiness report status does not match blockers: "
            f"expected {expected_status}, got {report_status or '<missing>'}"
        )

    if allowed:
        if report_status != "failed":
            errors.append(
                "go-live readiness report status must be failed when allowed blockers are configured, "
                f"got {report_status or '<missing>'}"
            )
        if unexpected_blockers:
            errors.append(f"go-live readiness report has unexpected blockers: {', '.join(unexpected_blockers)}")
        if missing_allowed_blockers:
            errors.append(
                f"go-live readiness report is missing allowed blockers: {', '.join(missing_allowed_blockers)}"
            )
    else:
        if report_status != "passed":
            errors.append(f"go-live readiness report status must be passed, got {report_status or '<missing>'}")
        if blockers:
            errors.append(f"go-live readiness report has blockers: {', '.join(blockers)}")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(resolved_report_path),
        "report_status": report_status,
        "status": "passed" if not errors else "failed",
        "summary": {
            "check_count": len(checks),
            "blocker_count": len(blockers),
            "allowed_blocker_count": len(allowed),
            "unexpected_blocker_count": len(unexpected_blockers),
            "missing_allowed_blocker_count": len(missing_allowed_blockers),
            "warning_count": len(report_warnings),
            "error_count": len(errors),
        },
        "allowed_blockers": allowed,
        "unexpected_blockers": unexpected_blockers,
        "missing_allowed_blockers": missing_allowed_blockers,
        "errors": errors,
        "warnings": report_warnings,
    }


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"go-live readiness report could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("go-live readiness report root must be an object")
        return {}
    return payload


def read_string_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"go-live readiness report {key} must be a list")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"go-live readiness report {key}[{index}] must be a string")
            continue
        items.append(item)
    return items


def read_checks(value: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append("go-live readiness report checks must be a list")
        return []
    checks: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"go-live readiness report checks[{index}] must be an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"go-live readiness report checks[{index}].name is required")
        status = item.get("status")
        if status not in {"passed", "failed"}:
            errors.append(
                f"go-live readiness report checks[{index}].status must be passed or failed, "
                f"got {status or '<missing>'}"
            )
        check_errors = item.get("errors")
        if not isinstance(check_errors, list):
            errors.append(f"go-live readiness report checks[{index}].errors must be a list")
        elif any(not isinstance(error, str) for error in check_errors):
            errors.append(f"go-live readiness report checks[{index}].errors must contain only strings")
        checks.append(item)
    return checks


def validate_summary(
    summary: Any,
    blockers: list[str],
    warnings: list[str],
    checks: list[dict[str, Any]],
    errors: list[str],
) -> None:
    if not isinstance(summary, dict):
        errors.append("go-live readiness report summary must be an object")
        return
    expected_counts = {
        "check_count": len(checks),
        "passed_check_count": sum(1 for check in checks if check.get("status") == "passed"),
        "failed_check_count": sum(1 for check in checks if check.get("status") == "failed"),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }
    for key, expected in expected_counts.items():
        if summary.get(key) != expected:
            errors.append(f"go-live readiness report summary {key} mismatch: expected {expected}, got {summary.get(key)}")


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a go-live readiness report and its accepted blockers.")
    parser.add_argument("--report", type=Path, required=True, help="Path to go-live-readiness.json.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-blocker",
        action="append",
        default=[],
        help="Allowed blocker text. When provided, the report must contain exactly these blockers and no others.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = verify_go_live_readiness_report(args.report, allowed_blockers=args.allow_blocker)
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "go-live readiness verification "
        f"{report['status']} "
        f"report_status={report['report_status'] or '<missing>'} "
        f"blockers={summary['blocker_count']} "
        f"unexpected={summary['unexpected_blocker_count']} "
        f"missing_allowed={summary['missing_allowed_blocker_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
