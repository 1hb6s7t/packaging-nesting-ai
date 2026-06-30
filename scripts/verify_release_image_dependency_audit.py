from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import release_image_dependency_audit  # noqa: E402


EXPECTED_COMMAND_NAMES = (
    "docker_build",
    "release_image_inventory",
    "release_image_dependency_review",
)
EXPECTED_SKIP_BUILD_COMMAND_NAMES = (
    "release_image_inventory",
    "release_image_dependency_review",
)


def verify_release_image_dependency_audit(
    report_path: Path,
    *,
    base_dir: Path | None = None,
    require_passed_report: bool = True,
) -> dict[str, Any]:
    resolved_report_path = report_path.resolve()
    report_errors: list[str] = []
    report = read_json_object(resolved_report_path, report_errors)
    report_dir = resolved_report_path.parent
    output_base_dir = base_dir.resolve() if base_dir else report_dir

    if report.get("schema_version") != 1:
        report_errors.append("release image dependency audit schema_version must be 1")
    report_status = str(report.get("status") or "")
    if require_passed_report and report_status != "passed":
        report_errors.append(
            f"release image dependency audit status must be passed, got {report_status or '<missing>'}"
        )

    source_errors = read_string_list(report, "errors", report_errors)
    source_warnings = read_string_list(report, "warnings", report_errors)
    commands = read_commands(report.get("commands"), report_errors)
    command_errors = validate_command_index(commands, skip_build=bool(report.get("skip_build")), report_status=report_status)
    report_errors.extend(command_errors)
    policy_contract = read_policy_contract(report.get("policy_contract"), report_errors)
    validate_summary(
        report,
        commands,
        source_errors=source_errors,
        source_warnings=source_warnings,
        policy_contract=policy_contract,
        errors=report_errors,
    )
    validate_policy_contract_matches_report(report, policy_contract, report_errors)

    output_checks = [
        verify_output_json(
            report,
            "inventory_output",
            label="release image dependency inventory",
            expected_summary=report.get("inventory_summary"),
            base_dir=output_base_dir,
            report_dir=report_dir,
        ),
        verify_output_json(
            report,
            "dependency_review_output",
            label="release image dependency review audit",
            expected_summary=report.get("dependency_review_summary"),
            base_dir=output_base_dir,
            report_dir=report_dir,
        ),
    ]
    for check in output_checks:
        if check["status"] == "failed":
            report_errors.extend(f"{check['name']}: {error}" for error in check["errors"])

    failed_output_checks = [check for check in output_checks if check["status"] == "failed"]
    failed_commands = [command for command in commands if command.get("exit_code") != 0]
    status = "passed" if not report_errors and not failed_output_checks else "failed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "report_path": str(resolved_report_path),
        "base_dir": str(output_base_dir),
        "report_status": report_status,
        "status": status,
        "summary": {
            "command_count": len(commands),
            "failed_command_count": len(failed_commands),
            "output_check_count": len(output_checks),
            "failed_output_check_count": len(failed_output_checks),
            "warning_count": len(source_warnings),
            "error_count": len(report_errors),
        },
        "errors": report_errors,
        "warnings": source_warnings,
        "output_checks": output_checks,
    }


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"release image dependency audit could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("release image dependency audit root must be an object")
        return {}
    return payload


def read_string_list(payload: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        errors.append(f"release image dependency audit {key} must be a list")
        return []
    items: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            items.append(item)
        else:
            errors.append(f"release image dependency audit {key}[{index}] must be a string")
    return items


def read_commands(value: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append("release image dependency audit commands must be a list")
        return []
    commands: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"release image dependency audit commands[{index}] must be an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"release image dependency audit commands[{index}].name is required")
        command = item.get("command")
        if not isinstance(command, list) or any(not isinstance(part, str) for part in command):
            errors.append(f"release image dependency audit commands[{index}].command must be a list of strings")
        if not isinstance(item.get("cwd"), str) or not item.get("cwd"):
            errors.append(f"release image dependency audit commands[{index}].cwd is required")
        if not isinstance(item.get("timeout_sec"), int) or item.get("timeout_sec") <= 0:
            errors.append(f"release image dependency audit commands[{index}].timeout_sec must be a positive integer")
        if not isinstance(item.get("exit_code"), int):
            errors.append(f"release image dependency audit commands[{index}].exit_code must be an integer")
        duration = item.get("duration_sec")
        if not isinstance(duration, int | float) or duration < 0:
            errors.append(f"release image dependency audit commands[{index}].duration_sec must be a non-negative number")
        commands.append(item)
    return commands


def validate_command_index(commands: list[dict[str, Any]], *, skip_build: bool, report_status: str) -> list[str]:
    errors: list[str] = []
    names = [str(command.get("name")) for command in commands if command.get("name")]
    duplicate_names = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicate_names:
        errors.append(f"release image dependency audit has duplicate command names: {', '.join(duplicate_names)}")

    expected_names = list(EXPECTED_SKIP_BUILD_COMMAND_NAMES if skip_build else EXPECTED_COMMAND_NAMES)
    if report_status == "passed" and names != expected_names:
        errors.append(
            "release image dependency audit commands must be "
            f"{', '.join(expected_names)} for a passed report, got {', '.join(names) or '<none>'}"
        )
    elif report_status != "passed" and names != expected_names[: len(names)]:
        errors.append(
            "release image dependency audit commands must preserve release command order, "
            f"got {', '.join(names) or '<none>'}"
        )
    return errors


def read_policy_contract(value: Any, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append("release image dependency audit policy_contract must be an object")
        return {}
    status = value.get("status")
    if status not in {"passed", "warning", "failed"}:
        errors.append(
            "release image dependency audit policy_contract.status must be passed, warning, or failed, "
            f"got {status or '<missing>'}"
        )
    checks = value.get("checks")
    if not isinstance(checks, list):
        errors.append("release image dependency audit policy_contract.checks must be a list")
        checks = []
    for key in ("passed_count", "warning_count", "failed_count"):
        if not isinstance(value.get(key), int) or value.get(key) < 0:
            errors.append(f"release image dependency audit policy_contract.{key} must be a non-negative integer")
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
            errors.append(f"release image dependency audit policy_contract.{key} must be {expected}, got {value.get(key)!r}")
    if policy_codes(value.get("failed_checks")) != policy_codes(failed_checks):
        errors.append("release image dependency audit policy_contract.failed_checks do not match failed checks")
    if policy_codes(value.get("warning_checks")) != policy_codes(warning_checks):
        errors.append("release image dependency audit policy_contract.warning_checks do not match warning checks")
    return value


def validate_summary(
    report: dict[str, Any],
    commands: list[dict[str, Any]],
    *,
    source_errors: list[str],
    source_warnings: list[str],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        errors.append("release image dependency audit summary must be an object")
        return
    failed_commands = [command for command in commands if command.get("exit_code") != 0]
    expected_counts = {
        "command_count": len(commands),
        "failed_command_count": len(failed_commands),
        "skip_build": bool(report.get("skip_build")),
        "error_count": len(source_errors) + len(failed_commands),
        "warning_count": len(source_warnings),
        "policy_contract_status": policy_contract.get("status"),
        "policy_contract_failed_count": policy_contract.get("failed_count"),
        "policy_contract_warning_count": policy_contract.get("warning_count"),
    }
    for key, expected in expected_counts.items():
        if summary.get(key) != expected:
            errors.append(f"release image dependency audit summary.{key} must be {expected!r}, got {summary.get(key)!r}")

    inventory_summary = report.get("inventory_summary") if isinstance(report.get("inventory_summary"), dict) else {}
    for key in ("missing_install_count", "release_blocking_missing_install_count", "review_required_count"):
        if summary.get(key) != inventory_summary.get(key):
            errors.append(
                f"release image dependency audit summary.{key} must match inventory_summary.{key}: "
                f"{summary.get(key)!r} != {inventory_summary.get(key)!r}"
            )

    review_summary = report.get("dependency_review_summary")
    if isinstance(review_summary, dict) and summary.get("dependency_review_status") == "passed":
        review_required_count = summary.get("review_required_count")
        approved_count = review_summary.get("approved_count")
        if isinstance(review_required_count, int) and approved_count != review_required_count:
            errors.append(
                "release image dependency audit dependency_review_summary.approved_count must match "
                f"summary.review_required_count: {approved_count!r} != {review_required_count!r}"
            )


def validate_policy_contract_matches_report(
    report: dict[str, Any],
    policy_contract: dict[str, Any],
    errors: list[str],
) -> None:
    if not policy_contract:
        return
    recomputed = release_image_dependency_audit.validate_release_image_dependency_policy_contract(report)
    embedded = policy_signature(policy_contract)
    expected = policy_signature(recomputed)
    if embedded != expected:
        errors.append("release image dependency audit policy_contract does not match recomputed report policy")
    if policy_contract.get("failed_count") not in {0, None} and report.get("status") == "passed":
        errors.append("release image dependency audit status must not be passed when policy_contract has failures")


def verify_output_json(
    report: dict[str, Any],
    field_name: str,
    *,
    label: str,
    expected_summary: Any,
    base_dir: Path,
    report_dir: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    path_value = report.get(field_name)
    path = resolve_reported_path(path_value, base_dir=base_dir, report_dir=report_dir)
    payload: dict[str, Any] = {}
    if path is None:
        errors.append(f"{label} path field {field_name} is missing or invalid")
    elif not path.is_file():
        errors.append(f"{label} file is missing: {path}")
    else:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append(f"{label} file could not be read: {exc}")
        else:
            if not isinstance(loaded, dict):
                errors.append(f"{label} root must be an object")
            else:
                payload = loaded
                if loaded.get("schema_version") != 1:
                    errors.append(f"{label} schema_version must be 1")
                actual_summary = loaded.get("summary")
                if not isinstance(actual_summary, dict):
                    errors.append(f"{label} summary must be an object")
                elif isinstance(expected_summary, dict):
                    for key, expected_value in expected_summary.items():
                        if actual_summary.get(key) != expected_value:
                            errors.append(
                                f"{label} summary.{key} must match report {field_name} summary: "
                                f"{actual_summary.get(key)!r} != {expected_value!r}"
                            )
    return {
        "name": field_name,
        "label": label,
        "status": "failed" if errors else "passed",
        "path": str(path) if path is not None else None,
        "report_status": payload.get("status") if payload else None,
        "errors": errors,
    }


def resolve_reported_path(value: Any, *, base_dir: Path, report_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        if path.exists():
            return path
        fallback = base_dir / path.name
        return fallback if fallback.exists() else path
    return (base_dir if base_dir else report_dir) / path


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
    parser = argparse.ArgumentParser(description="Verify a release image dependency audit report offline.")
    parser.add_argument("--report", type=Path, required=True, help="Path to release-image-dependency-audit.json.")
    parser.add_argument("--base-dir", type=Path, help="Base directory used to resolve copied output files.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-failed-report",
        action="store_true",
        help="Verify consistency even when the release image dependency audit did not pass.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    verification = verify_release_image_dependency_audit(
        args.report,
        base_dir=args.base_dir,
        require_passed_report=not args.allow_failed_report,
    )
    if args.output:
        write_json(args.output, verification)
    summary = verification["summary"]
    print(
        "release image dependency audit verification "
        f"{verification['status']} "
        f"report_status={verification['report_status'] or '<missing>'} "
        f"commands={summary['command_count']} "
        f"outputs_failed={summary['failed_output_check_count']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
