from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]

PASSED_STATUS = "passed"
PENDING_STATUS = "pending"

REQUIRED_ACCEPTANCE_AREAS = (
    "customer_integration_sandbox",
    "notification_channel_sandbox",
    "conversion_supplier_sandbox",
    "storage_backend_cutover",
    "production_deployment",
)

PLACEHOLDER_MARKERS = (
    "<REPLACE",
    "REPLACE_WITH",
    "CHANGE_ME",
    "CHANGEME",
    "TODO",
)


def build_external_acceptance_template() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "environment": "",
        "reviewer": "",
        "reviewed_at": "",
        "entries": [
            {
                "area": area,
                "status": PENDING_STATUS,
                "summary": "",
                "ticket": "",
                "evidence_files": [
                    {
                        "path": f"external-evidence-files/{area}.json",
                        "size_bytes": 0,
                        "sha256": "",
                        "description": "",
                    }
                ],
            }
            for area in REQUIRED_ACCEPTANCE_AREAS
        ],
    }


def refresh_external_acceptance_evidence_metadata(
    *,
    acceptance_file: Path,
    output_file: Path,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    resolved_acceptance_file = resolve_repo_path(acceptance_file)
    resolved_base_dir = resolve_repo_path(base_dir) if base_dir else resolved_acceptance_file.parent
    resolved_output_file = resolve_repo_path(output_file)
    errors: list[str] = []
    updated_count = 0

    if not resolved_acceptance_file.is_file():
        errors.append(f"external acceptance file does not exist: {resolved_acceptance_file}")
        return refresh_report(
            acceptance_file=resolved_acceptance_file,
            output_file=resolved_output_file,
            base_dir=resolved_base_dir,
            errors=errors,
            updated_count=updated_count,
        )

    try:
        payload = read_json(resolved_acceptance_file)
    except Exception as exc:
        errors.append(f"external acceptance file could not be read: {exc}")
        return refresh_report(
            acceptance_file=resolved_acceptance_file,
            output_file=resolved_output_file,
            base_dir=resolved_base_dir,
            errors=errors,
            updated_count=updated_count,
        )

    if not isinstance(payload, dict):
        errors.append("external acceptance file must contain a JSON object")
        return refresh_report(
            acceptance_file=resolved_acceptance_file,
            output_file=resolved_output_file,
            base_dir=resolved_base_dir,
            errors=errors,
            updated_count=updated_count,
        )

    entries = payload.get("entries")
    if not isinstance(entries, list):
        errors.append("external acceptance entries must be a list")
        return refresh_report(
            acceptance_file=resolved_acceptance_file,
            output_file=resolved_output_file,
            base_dir=resolved_base_dir,
            errors=errors,
            updated_count=updated_count,
        )

    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entries[{entry_index}] must be an object")
            continue
        evidence_files = entry.get("evidence_files")
        if not isinstance(evidence_files, list):
            errors.append(f"entries[{entry_index}].evidence_files must be a list")
            continue
        for evidence_index, evidence in enumerate(evidence_files):
            location = f"entries[{entry_index}].evidence_files[{evidence_index}]"
            if not isinstance(evidence, dict):
                errors.append(f"{location} must be an object")
                continue
            raw_path = evidence.get("path")
            if not non_empty_string(raw_path):
                errors.append(f"{location}.path is required")
                continue
            resolved_path, path_error = resolve_evidence_path(str(raw_path), resolved_base_dir)
            if path_error:
                errors.append(f"{location}: {path_error}")
                continue
            if resolved_path is None or not resolved_path.is_file():
                errors.append(f"{location}: evidence file is missing: {raw_path}")
                continue
            evidence["size_bytes"] = resolved_path.stat().st_size
            evidence["sha256"] = sha256_file(resolved_path)
            updated_count += 1

    if not errors:
        write_json(resolved_output_file, payload)

    return refresh_report(
        acceptance_file=resolved_acceptance_file,
        output_file=resolved_output_file,
        base_dir=resolved_base_dir,
        errors=errors,
        updated_count=updated_count,
    )


def refresh_report(
    *,
    acceptance_file: Path,
    output_file: Path,
    base_dir: Path,
    errors: list[str],
    updated_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not errors else "failed",
        "acceptance_file": str(acceptance_file),
        "output_file": str(output_file),
        "base_dir": str(base_dir),
        "summary": {
            "updated_evidence_file_count": updated_count,
            "error_count": len(errors),
        },
        "errors": errors,
    }


def build_external_acceptance_audit(
    *,
    acceptance_file: Path | None = None,
    require_acceptance_file: bool = False,
    base_dir: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = normalize_datetime(now or datetime.now(UTC))
    resolved_acceptance_file = resolve_repo_path(acceptance_file) if acceptance_file else None
    resolved_base_dir = resolve_repo_path(base_dir) if base_dir else (
        resolved_acceptance_file.parent if resolved_acceptance_file else REPO_ROOT
    )
    report = {
        "schema_version": 1,
        "generated_at": current_time.isoformat(),
        "status": "failed",
        "acceptance_file": str(resolved_acceptance_file) if resolved_acceptance_file else None,
        "require_acceptance_file": require_acceptance_file,
        "base_dir": str(resolved_base_dir),
        "required_areas": list(REQUIRED_ACCEPTANCE_AREAS),
        "summary": empty_summary(),
        "policy_contract": {},
        "errors": [],
        "warnings": [],
        "missing_areas": [],
        "invalid_areas": [],
        "unmatched_areas": [],
        "verified_evidence_files": [],
        "failed_evidence_files": [],
    }

    if resolved_acceptance_file is None:
        if require_acceptance_file:
            report["errors"].append("external acceptance file is required")
            report["missing_areas"] = list(REQUIRED_ACCEPTANCE_AREAS)
            report["summary"]["missing_area_count"] = len(REQUIRED_ACCEPTANCE_AREAS)
            return attach_policy_contract(report)
        report["status"] = "skipped"
        report["warnings"].append("external acceptance file was not provided")
        return attach_policy_contract(report)

    if not resolved_acceptance_file.is_file():
        report["errors"].append(f"external acceptance file does not exist: {resolved_acceptance_file}")
        report["missing_areas"] = list(REQUIRED_ACCEPTANCE_AREAS)
        report["summary"]["missing_area_count"] = len(REQUIRED_ACCEPTANCE_AREAS)
        return attach_policy_contract(report)

    try:
        payload = read_json(resolved_acceptance_file)
    except Exception as exc:
        report["errors"].append(f"external acceptance file could not be read: {exc}")
        return attach_policy_contract(report)

    document_errors = validate_acceptance_document(payload)
    placeholder_errors = placeholder_value_errors(payload)
    report["errors"].extend(document_errors)
    report["errors"].extend(placeholder_errors)

    entries = acceptance_entries(payload)
    entries_by_area, duplicate_errors = index_entries(entries)
    report["errors"].extend(duplicate_errors)

    required_areas = set(REQUIRED_ACCEPTANCE_AREAS)
    for area in REQUIRED_ACCEPTANCE_AREAS:
        entry = entries_by_area.get(area)
        if entry is None:
            report["missing_areas"].append(area)
            continue
        area_result = validate_acceptance_area(
            area=area,
            entry=entry,
            base_dir=resolved_base_dir,
        )
        report["verified_evidence_files"].extend(area_result["verified_evidence_files"])
        report["failed_evidence_files"].extend(area_result["failed_evidence_files"])
        if area_result["errors"]:
            report["invalid_areas"].append(
                {
                    "area": area,
                    "errors": area_result["errors"],
                }
            )

    for entry in entries:
        area = normalize_area(entry.get("area"))
        if area and area not in required_areas:
            report["unmatched_areas"].append(compact_area_entry(entry))

    if report["unmatched_areas"]:
        report["warnings"].append(
            f"external acceptance file has {len(report['unmatched_areas'])} area(s) not required by this release gate"
        )

    report["summary"].update(
        {
            "passed_area_count": len(REQUIRED_ACCEPTANCE_AREAS)
            - len(report["missing_areas"])
            - len(report["invalid_areas"]),
            "missing_area_count": len(report["missing_areas"]),
            "invalid_area_count": len(report["invalid_areas"]),
            "unmatched_area_count": len(report["unmatched_areas"]),
            "evidence_file_count": len(report["verified_evidence_files"]) + len(report["failed_evidence_files"]),
            "verified_evidence_file_count": len(report["verified_evidence_files"]),
            "failed_evidence_file_count": len(report["failed_evidence_files"]),
        }
    )
    blocking_count = (
        len(report["errors"])
        + len(report["missing_areas"])
        + len(report["invalid_areas"])
        + len(report["failed_evidence_files"])
    )
    report["status"] = "passed" if blocking_count == 0 else "failed"
    report = attach_policy_contract(report)
    if int((report.get("policy_contract") or {}).get("failed_count") or 0):
        report["status"] = "failed"
    return report


def attach_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    policy_contract = validate_external_acceptance_policy_contract(report)
    report["policy_contract"] = policy_contract
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["policy_contract_status"] = policy_contract.get("status")
        summary["policy_contract_failed_count"] = int(policy_contract.get("failed_count") or 0)
        summary["policy_contract_warning_count"] = int(policy_contract.get("warning_count") or 0)
    return report


def validate_external_acceptance_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("status") == "skipped" and not report.get("require_acceptance_file"):
        skipped_check = policy_check(
            code="acceptance.optional",
            status="skipped",
            message="external acceptance file is optional and was not provided",
            evidence={"require_acceptance_file": bool(report.get("require_acceptance_file"))},
        )
        return {
            "status": "skipped",
            "passed_count": 0,
            "warning_count": 0,
            "failed_count": 0,
            "failed_checks": [],
            "warning_checks": [],
            "checks": [skipped_check],
        }

    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    required_areas = list(report.get("required_areas") or REQUIRED_ACCEPTANCE_AREAS)
    missing_areas = list(report.get("missing_areas") or [])
    invalid_areas = list(report.get("invalid_areas") or [])
    unmatched_areas = list(report.get("unmatched_areas") or [])
    verified_evidence_files = list(report.get("verified_evidence_files") or [])
    failed_evidence_files = list(report.get("failed_evidence_files") or [])
    checks = [
        policy_check(
            code="acceptance.file.present",
            status="passed" if report.get("acceptance_file") and not any("file is required" in error for error in report.get("errors") or []) else "failed",
            message="external acceptance manifest is present"
            if report.get("acceptance_file") and not any("file is required" in error for error in report.get("errors") or [])
            else "external acceptance manifest is required for this release gate",
            evidence={
                "acceptance_file": report.get("acceptance_file"),
                "require_acceptance_file": bool(report.get("require_acceptance_file")),
            },
        ),
        policy_check(
            code="acceptance.document",
            status="passed" if not report.get("errors") else "failed",
            message="external acceptance document fields are complete and placeholder-free"
            if not report.get("errors")
            else "external acceptance document fields must be complete, timezone-aware, and placeholder-free",
            evidence={"errors": report.get("errors") or []},
        ),
        policy_check(
            code="acceptance.area.coverage",
            status="passed"
            if summary.get("required_area_count") == len(REQUIRED_ACCEPTANCE_AREAS)
            and summary.get("passed_area_count") == len(REQUIRED_ACCEPTANCE_AREAS)
            and not missing_areas
            and not invalid_areas
            else "failed",
            message="all required external acceptance areas passed"
            if summary.get("required_area_count") == len(REQUIRED_ACCEPTANCE_AREAS)
            and summary.get("passed_area_count") == len(REQUIRED_ACCEPTANCE_AREAS)
            and not missing_areas
            and not invalid_areas
            else "all required external acceptance areas must pass",
            evidence={
                "required_areas": required_areas,
                "passed_area_count": summary.get("passed_area_count"),
                "missing_areas": missing_areas,
                "invalid_area_count": len(invalid_areas),
            },
        ),
        policy_check(
            code="acceptance.area.scope",
            status="warning" if unmatched_areas else "passed",
            message="external acceptance manifest only contains required areas"
            if not unmatched_areas
            else "external acceptance manifest contains non-required areas",
            evidence={"unmatched_area_count": len(unmatched_areas), "unmatched_areas": unmatched_areas},
        ),
        policy_check(
            code="evidence.integrity",
            status="passed"
            if summary.get("verified_evidence_file_count", 0) >= len(REQUIRED_ACCEPTANCE_AREAS)
            and not failed_evidence_files
            else "failed",
            message="external acceptance evidence files pass size and SHA-256 checks"
            if summary.get("verified_evidence_file_count", 0) >= len(REQUIRED_ACCEPTANCE_AREAS)
            and not failed_evidence_files
            else "external acceptance evidence files must pass size and SHA-256 checks",
            evidence={
                "verified_evidence_file_count": summary.get("verified_evidence_file_count"),
                "failed_evidence_file_count": summary.get("failed_evidence_file_count"),
                "failed_evidence_files": failed_evidence_files,
            },
        ),
        policy_check(
            code="evidence.metadata",
            status="passed" if verified_evidence_metadata_complete(verified_evidence_files) else "failed",
            message="external acceptance evidence metadata is complete"
            if verified_evidence_metadata_complete(verified_evidence_files)
            else "external acceptance evidence metadata must include relative path, size, SHA-256, and description",
            evidence={"verified_evidence_file_count": len(verified_evidence_files)},
        ),
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


def verified_evidence_metadata_complete(items: list[Any]) -> bool:
    if len(items) < len(REQUIRED_ACCEPTANCE_AREAS):
        return False
    return all(
        isinstance(item, dict)
        and non_empty_string(item.get("relative_path"))
        and isinstance(item.get("size_bytes"), int)
        and item.get("size_bytes", 0) > 0
        and is_sha256_hex(item.get("sha256"))
        and non_empty_string(item.get("description"))
        for item in items
    )


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


def empty_summary() -> dict[str, int]:
    return {
        "required_area_count": len(REQUIRED_ACCEPTANCE_AREAS),
        "passed_area_count": 0,
        "missing_area_count": 0,
        "invalid_area_count": 0,
        "unmatched_area_count": 0,
        "evidence_file_count": 0,
        "verified_evidence_file_count": 0,
        "failed_evidence_file_count": 0,
    }


def validate_acceptance_document(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["external acceptance file must contain a JSON object"]
    if document.get("schema_version") != 1:
        errors.append("external acceptance schema_version must be 1")
    if not non_empty_string(document.get("environment")):
        errors.append("external acceptance environment is required")
    if not non_empty_string(document.get("reviewer")):
        errors.append("external acceptance reviewer is required")
    reviewed_at = document.get("reviewed_at")
    if not isinstance(reviewed_at, str) or parse_datetime(reviewed_at) is None:
        errors.append("external acceptance reviewed_at must be a timezone-aware ISO datetime")
    if not isinstance(document.get("entries"), list):
        errors.append("external acceptance entries must be a list")
    return errors


def acceptance_entries(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        return []
    return [item for item in document["entries"] if isinstance(item, dict)]


def index_entries(entries: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    indexed: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for entry in entries:
        area = normalize_area(entry.get("area"))
        if not area:
            errors.append("external acceptance entry is missing area")
            continue
        if area in indexed:
            errors.append(f"external acceptance entry is duplicated: {area}")
            continue
        indexed[area] = entry
    return indexed, errors


def validate_acceptance_area(*, area: str, entry: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    verified_evidence_files: list[dict[str, Any]] = []
    failed_evidence_files: list[dict[str, Any]] = []
    if entry.get("status") != PASSED_STATUS:
        errors.append(f"status must be {PASSED_STATUS}")
    if not non_empty_string(entry.get("summary")):
        errors.append("summary is required")
    if not non_empty_string(entry.get("ticket")):
        errors.append("ticket is required")
    evidence_files = entry.get("evidence_files")
    if not isinstance(evidence_files, list) or not evidence_files:
        errors.append("evidence_files must contain at least one file")
        evidence_files = []
    for item in evidence_files:
        result = validate_evidence_file(area=area, evidence=item, base_dir=base_dir)
        if result["status"] == "passed":
            verified_evidence_files.append(result)
        else:
            failed_evidence_files.append(result)
    if failed_evidence_files:
        errors.append("one or more evidence files failed validation")
    return {
        "errors": errors,
        "verified_evidence_files": verified_evidence_files,
        "failed_evidence_files": failed_evidence_files,
    }


def validate_evidence_file(*, area: str, evidence: Any, base_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return {
            "area": area,
            "status": "failed",
            "path": None,
            "relative_path": None,
            "errors": ["evidence file entry must be an object"],
        }
    raw_path = evidence.get("path")
    relative_path: str | None = str(raw_path) if isinstance(raw_path, str) else None
    if not non_empty_string(raw_path):
        errors.append("evidence file path is required")
        resolved_path = None
    else:
        resolved_path, path_error = resolve_evidence_path(str(raw_path), base_dir)
        if path_error:
            errors.append(path_error)
    expected_size = evidence.get("size_bytes")
    expected_sha = evidence.get("sha256")
    description = evidence.get("description")
    if not non_empty_string(description):
        errors.append("evidence file description is required")
    if not isinstance(expected_size, int) or expected_size <= 0:
        errors.append("evidence file size_bytes must be a positive integer")
    if not is_sha256_hex(expected_sha):
        errors.append("evidence file sha256 must be a 64-character hex digest")
    actual_size: int | None = None
    actual_sha: str | None = None
    if resolved_path is not None:
        if not resolved_path.is_file():
            errors.append(f"evidence file is missing: {relative_path}")
        else:
            actual_size = resolved_path.stat().st_size
            actual_sha = sha256_file(resolved_path)
            if isinstance(expected_size, int) and actual_size != expected_size:
                errors.append("evidence file size_bytes mismatch")
            if is_sha256_hex(expected_sha) and actual_sha != str(expected_sha).lower():
                errors.append("evidence file sha256 mismatch")
    return {
        "area": area,
        "status": "failed" if errors else "passed",
        "path": str(resolved_path) if resolved_path else None,
        "relative_path": relative_path,
        "size_bytes": actual_size,
        "sha256": actual_sha,
        "description": description,
        "errors": errors,
    }


def resolve_evidence_path(raw_path: str, base_dir: Path) -> tuple[Path | None, str | None]:
    path = Path(raw_path)
    if path.is_absolute():
        return None, "evidence file path must be relative to the external acceptance manifest"
    if ".." in path.parts:
        return None, "evidence file path must not escape the external acceptance base directory"
    resolved_base = base_dir.resolve()
    resolved_path = (resolved_base / path).resolve()
    try:
        resolved_path.relative_to(resolved_base)
    except ValueError:
        return None, "evidence file path must stay within the external acceptance base directory"
    return resolved_path, None


def placeholder_value_errors(payload: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            errors.extend(placeholder_value_errors(value, f"{path}.{key}"))
        return errors
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            errors.extend(placeholder_value_errors(value, f"{path}[{index}]"))
        return errors
    if isinstance(payload, str) and is_placeholder_value(payload):
        errors.append(f"{path} contains a placeholder value")
    return errors


def is_placeholder_value(value: str) -> bool:
    upper_value = value.upper()
    return any(marker in upper_value for marker in PLACEHOLDER_MARKERS)


def compact_area_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "area": entry.get("area"),
        "status": entry.get("status"),
        "summary": entry.get("summary"),
        "ticket": entry.get("ticket"),
    }


def normalize_area(value: Any) -> str:
    return str(value or "").strip().lower()


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_datetime(value: str) -> datetime | None:
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
    return normalize_datetime(parsed)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    parser = argparse.ArgumentParser(description="Audit real external release acceptance evidence.")
    parser.add_argument("--acceptance-file", type=Path, help="External acceptance manifest JSON.")
    parser.add_argument("--require-acceptance-file", action="store_true", help="Fail when --acceptance-file is omitted.")
    parser.add_argument("--base-dir", type=Path, help="Base directory for relative evidence paths. Defaults to the manifest directory.")
    parser.add_argument("--write-template", type=Path, help="Write a pending acceptance manifest template and exit.")
    parser.add_argument(
        "--refresh-evidence-metadata",
        type=Path,
        help="Read an external acceptance manifest and refresh size_bytes/sha256 for each listed evidence file.",
    )
    parser.add_argument(
        "--refreshed-output",
        type=Path,
        help="Output manifest path for --refresh-evidence-metadata. Required when refreshing evidence metadata.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_template:
        output_path = write_json(args.write_template, build_external_acceptance_template())
        print(f"external acceptance template written: {output_path}", flush=True)
        return 0
    if args.refresh_evidence_metadata:
        if args.refreshed_output is None:
            raise SystemExit("--refreshed-output is required with --refresh-evidence-metadata")
        report = refresh_external_acceptance_evidence_metadata(
            acceptance_file=args.refresh_evidence_metadata,
            output_file=args.refreshed_output,
            base_dir=args.base_dir,
        )
        if args.output:
            write_json(args.output, report)
        summary = report["summary"]
        print(
            "external acceptance evidence metadata refresh "
            f"{report['status']} "
            f"updated_files={summary['updated_evidence_file_count']} "
            f"errors={summary['error_count']}",
            flush=True,
        )
        return 0 if report["status"] == "passed" else 1
    report = build_external_acceptance_audit(
        acceptance_file=args.acceptance_file,
        require_acceptance_file=args.require_acceptance_file,
        base_dir=args.base_dir,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "external acceptance audit "
        f"{report['status']} "
        f"required_areas={summary['required_area_count']} "
        f"passed_areas={summary['passed_area_count']} "
        f"verified_files={summary['verified_evidence_file_count']} "
        f"errors={len(report['errors'])}",
        flush=True,
    )
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
