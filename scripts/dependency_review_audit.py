from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))

import release_inventory


APPROVED_DECISION = "approved"


def build_dependency_review_audit(
    *,
    inventory: dict[str, Any] | None = None,
    inventory_path: Path | None = None,
    review_file: Path | None = None,
    require_review_file: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = normalize_datetime(now or datetime.now(UTC))
    inventory_payload = inventory if inventory is not None else release_inventory.build_dependency_inventory(REPO_ROOT)
    required_items = review_required_items(inventory_payload)
    base_report = {
        "schema_version": 1,
        "generated_at": current_time.isoformat(),
        "status": "failed",
        "inventory_path": str(inventory_path) if inventory_path else None,
        "review_file": str(review_file) if review_file else None,
        "summary": empty_summary(len(required_items)),
        "errors": [],
        "warnings": [],
        "missing": [],
        "not_approved": [],
        "stale": [],
        "invalid": [],
        "expired": [],
        "unmatched": [],
        "policy_contract": {},
    }

    if not required_items and review_file is None:
        base_report["status"] = "passed"
        return attach_policy_contract(base_report, require_review_file=require_review_file)

    if review_file is None:
        base_report["missing"] = required_items
        base_report["summary"]["missing_ack_count"] = len(required_items)
        if require_review_file and required_items:
            base_report["errors"].append("dependency review file is required because inventory has review-required items")
            return attach_policy_contract(base_report, require_review_file=require_review_file)
        base_report["status"] = "skipped" if required_items else "passed"
        if required_items:
            base_report["warnings"].append("dependency review file was not provided")
        return attach_policy_contract(base_report, require_review_file=require_review_file)

    if not review_file.is_file():
        base_report["missing"] = required_items
        base_report["summary"]["missing_ack_count"] = len(required_items)
        base_report["errors"].append(f"dependency review file does not exist: {review_file}")
        return attach_policy_contract(base_report, require_review_file=require_review_file)

    try:
        review_payload = read_json(review_file)
    except Exception as exc:
        base_report["errors"].append(f"dependency review file could not be read: {exc}")
        return attach_policy_contract(base_report, require_review_file=require_review_file)

    document_errors = validate_review_document(review_payload)
    base_report["errors"].extend(document_errors)
    entries = review_entries(review_payload)
    entries_by_key, duplicate_errors = index_review_entries(entries)
    base_report["errors"].extend(duplicate_errors)

    required_keys = {dependency_key(item) for item in required_items}
    approved_count = 0
    for item in required_items:
        key = dependency_key(item)
        entry = entries_by_key.get(key)
        if entry is None:
            base_report["missing"].append(item)
            continue
        validation = validate_review_entry(
            item=item,
            entry=entry,
            document=review_payload,
            now=current_time,
        )
        if validation["not_approved"]:
            base_report["not_approved"].append(validation["not_approved"])
        if validation["stale"]:
            base_report["stale"].append(validation["stale"])
        if validation["invalid"]:
            base_report["invalid"].append(validation["invalid"])
        if validation["expired"]:
            base_report["expired"].append(validation["expired"])
        if not any(validation.values()):
            approved_count += 1

    for entry in entries:
        if dependency_key(entry) not in required_keys:
            base_report["unmatched"].append(compact_review_entry(entry))

    base_report["summary"].update(
        {
            "acknowledged_count": len(required_items) - len(base_report["missing"]),
            "approved_count": approved_count,
            "missing_ack_count": len(base_report["missing"]),
            "not_approved_count": len(base_report["not_approved"]),
            "stale_ack_count": len(base_report["stale"]),
            "invalid_ack_count": len(base_report["invalid"]),
            "expired_ack_count": len(base_report["expired"]),
            "unmatched_ack_count": len(base_report["unmatched"]),
        }
    )
    if base_report["unmatched"]:
        base_report["warnings"].append(
            f"dependency review file has {len(base_report['unmatched'])} acknowledgement(s) not required by current inventory"
        )
    blocking_count = (
        len(base_report["missing"])
        + len(base_report["not_approved"])
        + len(base_report["stale"])
        + len(base_report["invalid"])
        + len(base_report["expired"])
        + len(document_errors)
        + len(duplicate_errors)
    )
    base_report["status"] = "passed" if blocking_count == 0 else "failed"
    return attach_policy_contract(base_report, require_review_file=require_review_file)


def attach_policy_contract(report: dict[str, Any], *, require_review_file: bool) -> dict[str, Any]:
    policy_contract = validate_dependency_review_policy_contract(
        report,
        require_review_file=require_review_file,
    )
    report["policy_contract"] = policy_contract
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["policy_contract_status"] = policy_contract.get("status")
        summary["policy_contract_failed_count"] = int(policy_contract.get("failed_count") or 0)
        summary["policy_contract_warning_count"] = int(policy_contract.get("warning_count") or 0)
    if int(policy_contract.get("failed_count") or 0):
        report["status"] = "failed"
    return report


def validate_dependency_review_policy_contract(
    report: dict[str, Any],
    *,
    require_review_file: bool,
) -> dict[str, Any]:
    if report.get("status") == "skipped" and not require_review_file:
        skipped_check = policy_check(
            code="review.optional",
            status="skipped",
            message="dependency review file is optional and was not provided",
            evidence={"require_review_file": require_review_file},
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
    review_required_count = int(summary.get("review_required_count") or 0)
    approved_count = int(summary.get("approved_count") or 0)
    missing_ack_count = int(summary.get("missing_ack_count") or 0)
    not_approved_count = int(summary.get("not_approved_count") or 0)
    stale_ack_count = int(summary.get("stale_ack_count") or 0)
    invalid_ack_count = int(summary.get("invalid_ack_count") or 0)
    expired_ack_count = int(summary.get("expired_ack_count") or 0)
    unmatched_ack_count = int(summary.get("unmatched_ack_count") or 0)
    errors = list(report.get("errors") or [])
    review_file_present = bool(report.get("review_file"))
    file_required = require_review_file and review_required_count > 0
    file_present_or_not_needed = review_required_count == 0 or review_file_present
    checks = [
        policy_check(
            code="schema.version",
            status="passed" if report.get("schema_version") == 1 else "failed",
            message="dependency review audit schema_version is 1"
            if report.get("schema_version") == 1
            else "dependency review audit schema_version must be 1",
            evidence={"schema_version": report.get("schema_version")},
        ),
        policy_check(
            code="inventory.review_required",
            status="passed" if isinstance(summary.get("review_required_count"), int) else "failed",
            message="dependency inventory review-required count is captured"
            if isinstance(summary.get("review_required_count"), int)
            else "dependency inventory review-required count must be captured",
            evidence={"review_required_count": summary.get("review_required_count")},
        ),
        policy_check(
            code="review.file.present",
            status="passed" if file_present_or_not_needed and not (file_required and not review_file_present) else "failed",
            message="dependency review file is present when required"
            if file_present_or_not_needed and not (file_required and not review_file_present)
            else "dependency review file must be present when review-required items need signoff",
            evidence={
                "review_file": report.get("review_file"),
                "require_review_file": require_review_file,
                "review_required_count": review_required_count,
            },
        ),
        policy_check(
            code="review.document",
            status="passed" if not errors else "failed",
            message="dependency review document parsed and passed document validation"
            if not errors
            else "dependency review document must parse and pass document validation",
            evidence={"error_count": len(errors)},
        ),
        policy_check(
            code="review.coverage",
            status="passed" if approved_count == review_required_count and missing_ack_count == 0 else "failed",
            message="every review-required dependency has an approved acknowledgement"
            if approved_count == review_required_count and missing_ack_count == 0
            else "every review-required dependency must have an approved acknowledgement",
            evidence={
                "review_required_count": review_required_count,
                "approved_count": approved_count,
                "missing_ack_count": missing_ack_count,
            },
        ),
        policy_check(
            code="review.decision",
            status="passed" if not_approved_count == 0 else "failed",
            message="all dependency review decisions are approved"
            if not_approved_count == 0
            else "dependency review decisions must be approved",
            evidence={"not_approved_count": not_approved_count},
        ),
        policy_check(
            code="review.current",
            status="passed" if stale_ack_count == 0 and expired_ack_count == 0 else "failed",
            message="dependency review acknowledgements match current inventory and are not expired"
            if stale_ack_count == 0 and expired_ack_count == 0
            else "dependency review acknowledgements must match current inventory and not be expired",
            evidence={"stale_ack_count": stale_ack_count, "expired_ack_count": expired_ack_count},
        ),
        policy_check(
            code="review.metadata",
            status="passed" if invalid_ack_count == 0 else "failed",
            message="dependency review metadata is complete and timezone-aware"
            if invalid_ack_count == 0
            else "dependency review metadata must include reviewer, timezone-aware reviewed_at, reason, version, and license",
            evidence={"invalid_ack_count": invalid_ack_count},
        ),
        policy_check(
            code="review.scope",
            status="warning" if unmatched_ack_count else "passed",
            message="dependency review file only contains acknowledgements required by current inventory"
            if unmatched_ack_count == 0
            else "dependency review file contains acknowledgements not required by current inventory",
            evidence={"unmatched_ack_count": unmatched_ack_count},
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


def empty_summary(review_required_count: int) -> dict[str, int]:
    return {
        "review_required_count": review_required_count,
        "acknowledged_count": 0,
        "approved_count": 0,
        "missing_ack_count": 0,
        "not_approved_count": 0,
        "stale_ack_count": 0,
        "invalid_ack_count": 0,
        "expired_ack_count": 0,
        "unmatched_ack_count": 0,
    }


def review_required_items(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    summary = inventory.get("summary") if isinstance(inventory, dict) else {}
    summary_items = summary.get("review_required") if isinstance(summary, dict) else None
    if isinstance(summary_items, list):
        return sorted(
            [normalize_dependency_item(item) for item in summary_items if isinstance(item, dict)],
            key=dependency_sort_key,
        )
    dependencies = inventory.get("dependencies") if isinstance(inventory, dict) else None
    if not isinstance(dependencies, list):
        return []
    return sorted(
        [normalize_dependency_item(item) for item in dependencies if isinstance(item, dict) and item.get("review_required")],
        key=dependency_sort_key,
    )


def normalize_dependency_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ecosystem": str(item.get("ecosystem") or ""),
        "name": str(item.get("name") or ""),
        "scope": str(item.get("scope") or ""),
        "installed": bool(item.get("installed", True)),
        "version": item.get("version"),
        "license": item.get("license"),
        "reason": item.get("reason") or item.get("review_reason"),
    }


def validate_review_document(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["dependency review file must contain a JSON object"]
    if document.get("schema_version") != 1:
        errors.append("dependency review schema_version must be 1")
    entries = document.get("entries")
    if not isinstance(entries, list):
        errors.append("dependency review entries must be a list")
    return errors


def review_entries(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        return []
    return [item for item in document["entries"] if isinstance(item, dict)]


def index_review_entries(entries: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str], dict[str, Any]], list[str]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    errors: list[str] = []
    for entry in entries:
        key = dependency_key(entry)
        if not all(key):
            errors.append("dependency review entry is missing ecosystem, name, or scope")
            continue
        if key in indexed:
            errors.append(f"dependency review entry is duplicated: {format_key(key)}")
            continue
        indexed[key] = entry
    return indexed, errors


def validate_review_entry(
    *,
    item: dict[str, Any],
    entry: dict[str, Any],
    document: dict[str, Any],
    now: datetime,
) -> dict[str, dict[str, Any] | None]:
    not_approved: dict[str, Any] | None = None
    stale: dict[str, Any] | None = None
    invalid: dict[str, Any] | None = None
    expired: dict[str, Any] | None = None

    decision = str(entry.get("decision") or "").strip().lower()
    if decision != APPROVED_DECISION:
        not_approved = {
            "item": item,
            "acknowledgement": compact_review_entry(entry),
            "reason": f"decision must be {APPROVED_DECISION}",
        }

    invalid_fields = missing_or_invalid_fields(entry, document)
    if invalid_fields:
        invalid = {
            "item": item,
            "acknowledgement": compact_review_entry(entry),
            "fields": invalid_fields,
        }

    stale_fields = stale_fields_for_entry(item, entry)
    if stale_fields:
        stale = {
            "item": item,
            "acknowledgement": compact_review_entry(entry),
            "fields": stale_fields,
        }

    expires_at = entry.get("expires_at")
    if expires_at:
        parsed_expiry = parse_datetime(str(expires_at))
        if parsed_expiry is None:
            invalid = merge_invalid_field(invalid, item, entry, "expires_at")
        elif parsed_expiry <= now:
            expired = {
                "item": item,
                "acknowledgement": compact_review_entry(entry),
                "expires_at": parsed_expiry.isoformat(),
            }

    return {
        "not_approved": not_approved,
        "stale": stale,
        "invalid": invalid,
        "expired": expired,
    }


def missing_or_invalid_fields(entry: dict[str, Any], document: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    reviewer = entry.get("reviewer") or document.get("reviewer")
    reviewed_at = entry.get("reviewed_at") or document.get("reviewed_at")
    reason = entry.get("reason") or entry.get("justification")
    if not isinstance(reviewer, str) or not reviewer.strip():
        fields.append("reviewer")
    if not isinstance(reviewed_at, str) or parse_datetime(reviewed_at) is None:
        fields.append("reviewed_at")
    if not isinstance(reason, str) or not reason.strip():
        fields.append("reason")
    if "version" not in entry:
        fields.append("version")
    if "license" not in entry:
        fields.append("license")
    return fields


def stale_fields_for_entry(item: dict[str, Any], entry: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for field in ("version", "license"):
        if field in entry and entry.get(field) != item.get(field):
            fields.append(field)
    return fields


def merge_invalid_field(
    invalid: dict[str, Any] | None,
    item: dict[str, Any],
    entry: dict[str, Any],
    field: str,
) -> dict[str, Any]:
    if invalid is None:
        return {
            "item": item,
            "acknowledgement": compact_review_entry(entry),
            "fields": [field],
        }
    fields = invalid.setdefault("fields", [])
    if field not in fields:
        fields.append(field)
    return invalid


def compact_review_entry(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ecosystem",
        "name",
        "scope",
        "version",
        "license",
        "decision",
        "reviewer",
        "reviewed_at",
        "expires_at",
        "reason",
        "ticket",
    )
    return {key: entry.get(key) for key in keys if key in entry}


def dependency_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("ecosystem") or "").lower(),
        str(item.get("name") or "").lower(),
        str(item.get("scope") or "").lower(),
    )


def dependency_sort_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return dependency_key(item)


def format_key(key: tuple[str, str, str]) -> str:
    return "/".join(key)


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit dependency review acknowledgements against the release inventory.")
    parser.add_argument("--inventory", type=Path, help="Existing dependency inventory JSON. Defaults to generating one from the repo.")
    parser.add_argument("--review-file", type=Path, help="Dependency review acknowledgement JSON.")
    parser.add_argument("--require-review-file", action="store_true", help="Fail when review-required items exist without a review file.")
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inventory = read_json(args.inventory) if args.inventory else release_inventory.build_dependency_inventory(REPO_ROOT)
    report = build_dependency_review_audit(
        inventory=inventory,
        inventory_path=args.inventory,
        review_file=args.review_file,
        require_review_file=args.require_review_file,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "dependency review audit "
        f"{report['status']} "
        f"review_required={summary['review_required_count']} "
        f"approved={summary['approved_count']} "
        f"missing={summary['missing_ack_count']} "
        f"stale={summary['stale_ack_count']} "
        f"invalid={summary['invalid_ack_count']}",
        flush=True,
    )
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
