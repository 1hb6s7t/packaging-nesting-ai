from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT_PATH = REPO_ROOT / "scripts" / "dependency_review_audit.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_dependency_review_audit.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_dependency_review_audit_accepts_generated_passed_report(tmp_path: Path) -> None:
    audit_module = load_module("dependency_review_audit_for_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_dependency_review_audit", VERIFY_SCRIPT_PATH)
    report_path = write_passed_report(audit_module, tmp_path)

    verification = verify_module.verify_dependency_review_audit(report_path)

    assert verification["status"] == "passed"
    assert verification["report_status"] == "passed"
    assert verification["summary"]["review_required_count"] == 1
    assert verification["summary"]["approved_count"] == 1
    assert verification["summary"]["error_count"] == 0
    assert verification["errors"] == []


def test_verify_dependency_review_audit_rejects_summary_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("dependency_review_audit_for_summary_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_dependency_review_audit_for_summary", VERIFY_SCRIPT_PATH)
    report_path = write_passed_report(audit_module, tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["summary"]["missing_ack_count"] = 1
    write_json(report_path, report)

    verification = verify_module.verify_dependency_review_audit(report_path)

    assert verification["status"] == "failed"
    assert any("summary.missing_ack_count must be 0" in error for error in verification["errors"])
    assert "dependency review audit policy_contract does not match recomputed report policy" in verification["errors"]


def test_verify_dependency_review_audit_rejects_policy_contract_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("dependency_review_audit_for_policy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_dependency_review_audit_for_policy", VERIFY_SCRIPT_PATH)
    report_path = write_passed_report(audit_module, tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["policy_contract"]["status"] = "failed"
    write_json(report_path, report)

    verification = verify_module.verify_dependency_review_audit(report_path)

    assert verification["status"] == "failed"
    assert "dependency review audit summary.policy_contract_status does not match policy_contract status" in verification["errors"]
    assert "dependency review audit policy_contract does not match recomputed report policy" in verification["errors"]


def test_verify_dependency_review_audit_can_allow_structurally_valid_skipped_report(tmp_path: Path) -> None:
    audit_module = load_module("dependency_review_audit_for_skipped_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_dependency_review_audit_for_skipped", VERIFY_SCRIPT_PATH)
    report = audit_module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )
    report_path = tmp_path / "dependency-review-audit.json"
    write_json(report_path, report)

    failed = verify_module.verify_dependency_review_audit(report_path)
    allowed = verify_module.verify_dependency_review_audit(report_path, require_passed_report=False)

    assert failed["status"] == "failed"
    assert "dependency review audit status must be passed, got skipped" in failed["errors"]
    assert allowed["status"] == "passed"
    assert allowed["report_status"] == "skipped"
    assert allowed["summary"]["policy_contract_status"] == "skipped"


def test_verify_dependency_review_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    audit_module = load_module("dependency_review_audit_for_cli_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_dependency_review_audit_for_cli", VERIFY_SCRIPT_PATH)
    report = audit_module.build_dependency_review_audit(
        inventory=inventory_with_review_item(),
        now=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )
    report_path = tmp_path / "dependency-review-audit.json"
    output_path = tmp_path / "dependency-review-verification.json"
    write_json(report_path, report)

    exit_code = verify_module.main(["--report", str(report_path), "--output", str(output_path)])

    verification = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert verification["status"] == "failed"
    assert "dependency review audit status must be passed, got skipped" in verification["errors"]


def write_passed_report(module, tmp_path: Path) -> Path:
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
    report_path = tmp_path / "dependency-review-audit.json"
    write_json(report_path, report)
    return report_path


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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
