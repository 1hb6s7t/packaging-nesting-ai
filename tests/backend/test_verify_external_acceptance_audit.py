from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT_PATH = REPO_ROOT / "scripts" / "external_acceptance_audit.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_external_acceptance_audit.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_external_acceptance_audit_accepts_generated_report(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit", VERIFY_SCRIPT_PATH)
    paths = write_external_acceptance_outputs(audit_module, tmp_path)

    verification = verify_module.verify_external_acceptance_audit(paths["report"])

    assert verification["status"] == "passed"
    assert verification["report_status"] == "passed"
    assert verification["summary"]["passed_area_count"] == 5
    assert verification["summary"]["verified_evidence_file_count"] == 5
    assert verification["summary"]["failed_evidence_check_count"] == 0
    assert verification["errors"] == []


def test_verify_external_acceptance_audit_uses_base_dir_after_copy(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_copy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_copy", VERIFY_SCRIPT_PATH)
    source_dir = tmp_path / "source"
    copied_dir = tmp_path / "copied"
    paths = write_external_acceptance_outputs(audit_module, source_dir)
    shutil.copytree(source_dir, copied_dir)
    copied_report = copied_dir / paths["report"].name
    report = json.loads(copied_report.read_text(encoding="utf-8"))
    report["base_dir"] = str(tmp_path / "stale")
    for evidence in report["verified_evidence_files"]:
        evidence["path"] = str(tmp_path / "stale" / evidence["relative_path"])
    write_json(copied_report, report)

    verification = verify_module.verify_external_acceptance_audit(copied_report, base_dir=copied_dir)

    assert verification["status"] == "passed"
    assert all(str(copied_dir) in check["path"] for check in verification["evidence_checks"])


def test_verify_external_acceptance_audit_rejects_summary_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_summary_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_summary", VERIFY_SCRIPT_PATH)
    paths = write_external_acceptance_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["summary"]["verified_evidence_file_count"] = 99
    write_json(paths["report"], report)

    verification = verify_module.verify_external_acceptance_audit(paths["report"])

    assert verification["status"] == "failed"
    assert any("summary.verified_evidence_file_count must be 5" in error for error in verification["errors"])


def test_verify_external_acceptance_audit_rejects_policy_contract_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_policy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_policy", VERIFY_SCRIPT_PATH)
    paths = write_external_acceptance_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["policy_contract"]["status"] = "failed"
    report["policy_contract"]["failed_count"] = 1
    write_json(paths["report"], report)

    verification = verify_module.verify_external_acceptance_audit(paths["report"])

    assert verification["status"] == "failed"
    assert "external acceptance audit policy_contract does not match recomputed report policy" in verification["errors"]


def test_verify_external_acceptance_audit_rejects_verified_evidence_drift(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_evidence_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_evidence", VERIFY_SCRIPT_PATH)
    paths = write_external_acceptance_outputs(audit_module, tmp_path)
    (tmp_path / f"{audit_module.REQUIRED_ACCEPTANCE_AREAS[0]}.json").write_text("tampered", encoding="utf-8")

    verification = verify_module.verify_external_acceptance_audit(paths["report"])

    assert verification["status"] == "failed"
    assert verification["summary"]["failed_evidence_check_count"] == 1
    assert any("verified evidence file sha256 mismatch" in error for error in verification["errors"])


def test_verify_external_acceptance_audit_can_allow_structurally_valid_skipped_report(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_skipped_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_skipped", VERIFY_SCRIPT_PATH)
    report = audit_module.build_external_acceptance_audit()
    report_path = tmp_path / "external-acceptance-audit.json"
    write_json(report_path, report)

    failed = verify_module.verify_external_acceptance_audit(report_path)
    allowed = verify_module.verify_external_acceptance_audit(report_path, require_passed_report=False)

    assert failed["status"] == "failed"
    assert "external acceptance audit status must be passed, got skipped" in failed["errors"]
    assert allowed["status"] == "passed"
    assert allowed["report_status"] == "skipped"
    assert allowed["summary"]["policy_contract_status"] == "skipped"


def test_verify_external_acceptance_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    audit_module = load_module("external_acceptance_audit_for_cli_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_external_acceptance_audit_for_cli", VERIFY_SCRIPT_PATH)
    report = audit_module.build_external_acceptance_audit()
    report_path = tmp_path / "external-acceptance-audit.json"
    output_path = tmp_path / "external-acceptance-verification.json"
    write_json(report_path, report)

    exit_code = verify_module.main(["--report", str(report_path), "--output", str(output_path)])

    verification = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert verification["status"] == "failed"
    assert "external acceptance audit status must be passed, got skipped" in verification["errors"]


def write_external_acceptance_outputs(module, tmp_path: Path) -> dict[str, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(json.dumps(valid_manifest(module, tmp_path), ensure_ascii=False), encoding="utf-8")
    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )
    report_path = tmp_path / "external-acceptance-audit.json"
    write_json(report_path, report)
    return {"acceptance": acceptance_file, "report": report_path}


def valid_manifest(module, tmp_path: Path) -> dict:
    entries = []
    for area in module.REQUIRED_ACCEPTANCE_AREAS:
        evidence_path = tmp_path / f"{area}.json"
        evidence_path.write_text(json.dumps({"area": area, "status": "passed"}, ensure_ascii=False), encoding="utf-8")
        entries.append(
            {
                "area": area,
                "status": "passed",
                "summary": f"{area} accepted in customer sandbox",
                "ticket": "REL-EXT-1",
                "evidence_files": [
                    {
                        "path": evidence_path.name,
                        "size_bytes": evidence_path.stat().st_size,
                        "sha256": module.sha256_file(evidence_path),
                        "description": f"{area} evidence",
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "environment": "customer-production-2026-06-29",
        "reviewer": "delivery-owner",
        "reviewed_at": "2026-06-29T10:00:00Z",
        "entries": entries,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
