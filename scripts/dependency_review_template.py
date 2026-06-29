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

import dependency_review_audit
import release_inventory


PENDING_DECISION = "pending"


def build_dependency_review_template(
    *,
    inventory: dict[str, Any] | None = None,
    inventory_path: Path | None = None,
    reviewer: str | None = None,
    reviewed_at: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    current_time = dependency_review_audit.normalize_datetime(generated_at or datetime.now(UTC))
    inventory_payload = inventory if inventory is not None else release_inventory.build_dependency_inventory(REPO_ROOT)
    required_items = dependency_review_audit.review_required_items(inventory_payload)
    entries = [template_entry(item) for item in required_items]
    return {
        "schema_version": 1,
        "generated_at": current_time.isoformat(),
        "inventory_path": str(inventory_path) if inventory_path else None,
        "reviewer": reviewer or "",
        "reviewed_at": reviewed_at or "",
        "summary": {
            "review_required_count": len(required_items),
            "entry_count": len(entries),
            "pending_count": len(entries),
        },
        "entries": entries,
    }


def template_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ecosystem": item.get("ecosystem"),
        "name": item.get("name"),
        "scope": item.get("scope"),
        "version": item.get("version"),
        "license": item.get("license"),
        "installed": bool(item.get("installed", True)),
        "inventory_reason": item.get("reason"),
        "decision": PENDING_DECISION,
        "reason": "",
        "ticket": "",
        "expires_at": "",
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any], repo_root: Path = REPO_ROOT) -> Path:
    output_path = path if path.is_absolute() else repo_root / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a dependency review signoff template from release inventory.")
    parser.add_argument("--inventory", type=Path, help="Existing dependency inventory JSON. Defaults to generating one from the repo.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/dependency-review-template.json"))
    parser.add_argument("--reviewer", help="Optional top-level reviewer value to prefill.")
    parser.add_argument("--reviewed-at", help="Optional top-level ISO reviewed_at value to prefill.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inventory = read_json(args.inventory) if args.inventory else release_inventory.build_dependency_inventory(REPO_ROOT)
    template = build_dependency_review_template(
        inventory=inventory,
        inventory_path=args.inventory,
        reviewer=args.reviewer,
        reviewed_at=args.reviewed_at,
    )
    output_path = write_json(args.output, template)
    summary = template["summary"]
    print(
        "dependency review template written: "
        f"{output_path} "
        f"review_required={summary['review_required_count']} "
        f"pending={summary['pending_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
