from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "dependency_review_audit.py"


def load_dependency_review_audit_module():
    spec = importlib.util.spec_from_file_location("dependency_review_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_dependency_review_audit_accepts_current_approved_acknowledgements(tmp_path: Path) -> None:
    module = load_dependency_review_audit_module()
    review_file = tmp_path / "dependency-review.json"
    review_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29T10:00:00Z",
                "entries": [
                    {
                        "ecosystem": "python",
                        "name": "ortools",
                        "scope": "runtime",
                        "version": "9.12.4544",
                        "license": None,
                        "decision": "approved",
                        "reason": "release image metadata reviewed by owner",
                        "ticket": "REL-101",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8-sig",
    )

    report = module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        review_file=review_file,
        require_review_file=True,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "passed"
    assert report["summary"]["review_required_count"] == 1
    assert report["summary"]["approved_count"] == 1
    assert report["options"]["require_review_file"] is True
    assert report["policy_contract"]["status"] == "passed"
    assert report["policy_contract"]["failed_count"] == 0
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["errors"] == []


def test_dependency_review_audit_can_skip_or_fail_when_review_file_is_missing() -> None:
    module = load_dependency_review_audit_module()

    skipped = module.build_dependency_review_audit(inventory=inventory_with_review_item())
    failed = module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        require_review_file=True,
    )

    assert skipped["status"] == "skipped"
    assert skipped["options"]["require_review_file"] is False
    assert skipped["summary"]["missing_ack_count"] == 1
    assert skipped["policy_contract"]["status"] == "skipped"
    assert skipped["summary"]["policy_contract_status"] == "skipped"
    assert "dependency review file was not provided" in skipped["warnings"]
    assert failed["status"] == "failed"
    assert failed["options"]["require_review_file"] is True
    assert "dependency review file is required" in failed["errors"][0]
    assert failed["policy_contract"]["status"] == "failed"
    assert any(check["code"] == "review.file.present" for check in failed["policy_contract"]["failed_checks"])


def test_dependency_review_audit_rejects_stale_invalid_or_expired_acknowledgements(tmp_path: Path) -> None:
    module = load_dependency_review_audit_module()
    review_file = tmp_path / "dependency-review.json"
    review_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29",
                "entries": [
                    {
                        "ecosystem": "python",
                        "name": "ortools",
                        "scope": "runtime",
                        "version": "9.11.0",
                        "license": "MIT",
                        "decision": "approved",
                        "reason": "old review",
                        "expires_at": "2026-06-28T00:00:00Z",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        review_file=review_file,
        require_review_file=True,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "failed"
    assert report["summary"]["stale_ack_count"] == 1
    assert report["summary"]["invalid_ack_count"] == 1
    assert report["summary"]["expired_ack_count"] == 1
    assert report["stale"][0]["fields"] == ["version", "license"]
    assert report["invalid"][0]["fields"] == ["reviewed_at"]
    failed_codes = {check["code"] for check in report["policy_contract"]["failed_checks"]}
    assert "review.current" in failed_codes
    assert "review.metadata" in failed_codes


def test_dependency_review_audit_requires_timezone_aware_reviewed_and_expiry_times(tmp_path: Path) -> None:
    module = load_dependency_review_audit_module()
    review_file = tmp_path / "dependency-review.json"
    review_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29T10:00:00",
                "entries": [
                    {
                        "ecosystem": "python",
                        "name": "ortools",
                        "scope": "runtime",
                        "version": "9.12.4544",
                        "license": None,
                        "decision": "approved",
                        "reason": "release image metadata reviewed by owner",
                        "expires_at": "2026-07-29",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        review_file=review_file,
        require_review_file=True,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "failed"
    assert report["summary"]["invalid_ack_count"] == 1
    assert report["invalid"][0]["fields"] == ["reviewed_at", "expires_at"]
    assert any(check["code"] == "review.metadata" for check in report["policy_contract"]["failed_checks"])


def test_dependency_review_policy_contract_warns_for_unmatched_acknowledgements(tmp_path: Path) -> None:
    module = load_dependency_review_audit_module()
    review_file = tmp_path / "dependency-review.json"
    review_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29T10:00:00Z",
                "entries": [
                    {
                        "ecosystem": "python",
                        "name": "unused-lib",
                        "scope": "runtime",
                        "version": "1.0.0",
                        "license": "MIT",
                        "decision": "approved",
                        "reason": "not needed by current inventory",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = module.build_dependency_review_audit(
        inventory={"schema_version": 1, "summary": {"review_required_count": 0, "review_required": []}},
        review_file=review_file,
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )

    assert report["status"] == "passed"
    assert report["policy_contract"]["status"] == "warning"
    assert report["summary"]["policy_contract_warning_count"] == 1
    assert any(check["code"] == "review.scope" for check in report["policy_contract"]["warning_checks"])


def test_dependency_review_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    module = load_dependency_review_audit_module()
    inventory_path = tmp_path / "dependency-inventory.json"
    output_path = tmp_path / "dependency-review-audit.json"
    inventory_path.write_text(json.dumps(inventory_with_review_item(), ensure_ascii=False), encoding="utf-8")

    exit_code = module.main(
        [
            "--inventory",
            str(inventory_path),
            "--require-review-file",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["summary"]["missing_ack_count"] == 1
    assert report["summary"]["policy_contract_status"] == "failed"


def inventory_with_review_item() -> dict:
    return {
        "schema_version": 1,
        "summary": {
            "dependency_count": 1,
            "review_required_count": 1,
            "review_required": [
                {
                    "ecosystem": "python",
                    "name": "ortools",
                    "scope": "runtime",
                    "installed": True,
                    "version": "9.12.4544",
                    "license": None,
                    "reason": "missing license metadata",
                }
            ],
        },
        "dependencies": [],
    }
