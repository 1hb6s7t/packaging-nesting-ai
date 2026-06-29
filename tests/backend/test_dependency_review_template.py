from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "dependency_review_template.py"


def load_dependency_review_template_module():
    spec = importlib.util.spec_from_file_location("dependency_review_template", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dependency_review_template_builds_pending_entries_for_required_items() -> None:
    module = load_dependency_review_template_module()

    template = module.build_dependency_review_template(
        inventory=inventory_with_review_items(),
        inventory_path=Path("artifacts/dependency-inventory.json"),
        generated_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert template["schema_version"] == 1
    assert Path(template["inventory_path"]) == Path("artifacts/dependency-inventory.json")
    assert template["summary"] == {
        "review_required_count": 2,
        "entry_count": 2,
        "pending_count": 2,
    }
    assert [(entry["ecosystem"], entry["scope"], entry["name"]) for entry in template["entries"]] == [
        ("npm", "runtime", "copyleft-lib"),
        ("python", "runtime", "ortools"),
    ]
    assert template["entries"][0]["decision"] == "pending"
    assert template["entries"][0]["reason"] == ""
    assert template["entries"][0]["version"] == "1.0.0"
    assert template["entries"][0]["license"] == "GPL-3.0-only"
    assert template["entries"][0]["inventory_reason"] == "license marker requires review: GPL"


def test_dependency_review_template_pending_file_fails_required_audit(tmp_path: Path) -> None:
    module = load_dependency_review_template_module()
    review_file = tmp_path / "dependency-review-template.json"
    template = module.build_dependency_review_template(
        inventory=inventory_with_review_items(),
        reviewer="delivery-owner",
        reviewed_at="2026-06-29T10:00:00Z",
    )
    module.write_json(review_file, template)

    report = module.dependency_review_audit.build_dependency_review_audit(
        inventory=inventory_with_review_items(),
        review_file=review_file,
        require_review_file=True,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "failed"
    assert report["summary"]["not_approved_count"] == 2
    assert report["summary"]["invalid_ack_count"] == 2


def test_dependency_review_template_can_be_completed_and_pass_audit(tmp_path: Path) -> None:
    module = load_dependency_review_template_module()
    review_file = tmp_path / "dependency-review.json"
    template = module.build_dependency_review_template(
        inventory=inventory_with_review_items(),
        reviewer="delivery-owner",
        reviewed_at="2026-06-29T10:00:00Z",
    )
    for entry in template["entries"]:
        entry["decision"] = "approved"
        entry["reason"] = "release image dependency metadata reviewed by delivery owner"
        entry["ticket"] = "REL-2026-0629"
    module.write_json(review_file, template)

    report = module.dependency_review_audit.build_dependency_review_audit(
        inventory=inventory_with_review_items(),
        review_file=review_file,
        require_review_file=True,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "passed"
    assert report["summary"]["approved_count"] == 2
    assert report["errors"] == []


def test_dependency_review_template_cli_writes_template(tmp_path: Path) -> None:
    module = load_dependency_review_template_module()
    inventory_path = tmp_path / "dependency-inventory.json"
    output_path = tmp_path / "dependency-review-template.json"
    inventory_path.write_text(json.dumps(inventory_with_review_items(), ensure_ascii=False), encoding="utf-8")

    exit_code = module.main(
        [
            "--inventory",
            str(inventory_path),
            "--output",
            str(output_path),
            "--reviewer",
            "delivery-owner",
            "--reviewed-at",
            "2026-06-29T10:00:00Z",
        ]
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["reviewer"] == "delivery-owner"
    assert payload["reviewed_at"] == "2026-06-29T10:00:00Z"
    assert payload["summary"]["review_required_count"] == 2
    assert all(entry["decision"] == "pending" for entry in payload["entries"])


def inventory_with_review_items() -> dict:
    return {
        "schema_version": 1,
        "summary": {
            "dependency_count": 3,
            "review_required_count": 2,
            "review_required": [
                {
                    "ecosystem": "python",
                    "name": "ortools",
                    "scope": "runtime",
                    "installed": False,
                    "version": None,
                    "license": None,
                    "reason": "package is not installed in this environment; regenerate inventory in the release image",
                },
                {
                    "ecosystem": "npm",
                    "name": "copyleft-lib",
                    "scope": "runtime",
                    "installed": True,
                    "version": "1.0.0",
                    "license": "GPL-3.0-only",
                    "reason": "license marker requires review: GPL",
                },
            ],
        },
        "dependencies": [],
    }
