from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "storage_export_audit.py"


def load_storage_export_audit_module():
    spec = importlib.util.spec_from_file_location("storage_export_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_storage_export_audit_passes_manifest_recovery_and_drift_checks() -> None:
    module = load_storage_export_audit_module()

    report = module.build_storage_export_audit_report()

    assert report["status"] == "passed"
    assert report["summary"]["export_count"] == 3
    assert report["summary"]["failed_count"] == 0
    assert report["summary"]["storage_contract_status"] == "passed"
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["summary"]["storage_backend"] == "local"
    assert report["summary"]["recovery_status"] == "passed"
    assert report["summary"]["tamper_status"] == "checksum_mismatch"
    assert report["summary"]["version_drift_status"] == "version_mismatch"
    assert report["storage_contract"]["status"] == "passed"
    assert report["policy_contract"]["status"] == "passed"
    assert report["policy_contract"]["failed_count"] == 0
    assert report["storage_contract"]["probe"]["object_key"].endswith("/adapter-contract/probe.txt")
    assert report["manifest"]["active_export_count"] == 2
    assert report["manifest"]["expired_export_count"] == 1
    for item in report["manifest"]["exports"]:
        assert item["storage_backend"] == "local"
        assert item["storage_object_key"].startswith(f"exports/{report['solution_id']}/")
        assert item["storage_object_key"] != item["storage_key"]
        assert Path(item["storage_key"]).is_absolute()
    assert report["recovery"]["archive_dry_run"]["status"] == "dry_run"
    assert report["recovery"]["archive_dry_run"]["archived_count"] == 0
    assert all(check["status"] == "passed" for check in report["checks"])


def test_storage_policy_contract_fails_when_export_retention_is_missing() -> None:
    module = load_storage_export_audit_module()
    report = module.build_storage_export_audit_report()
    manifest = copy.deepcopy(report["manifest"])
    manifest["exports"][0]["retention_until"] = None

    policy = module.validate_storage_policy_contract(
        solution_id=report["solution_id"],
        storage_contract=report["storage_contract"],
        manifest=manifest,
        recovery=report["recovery"],
    )

    assert policy["status"] == "failed"
    assert any(
        check["code"] == "export.metadata.complete" and check["export_id"] == manifest["exports"][0]["id"]
        for check in policy["failed_checks"]
    )


def test_storage_policy_contract_fails_when_archive_dry_run_misses_expired_export() -> None:
    module = load_storage_export_audit_module()
    report = module.build_storage_export_audit_report()
    recovery = copy.deepcopy(report["recovery"])
    recovery["archive_dry_run"]["archived_exports"] = []

    policy = module.validate_storage_policy_contract(
        solution_id=report["solution_id"],
        storage_contract=report["storage_contract"],
        manifest=report["manifest"],
        recovery=recovery,
    )

    assert policy["status"] == "failed"
    failed_check = next(check for check in policy["failed_checks"] if check["code"] == "archive.expired_coverage")
    assert failed_check["evidence"]["expired_export_ids"] == ["audit_dxf_v1"]
    assert failed_check["evidence"]["archive_target_ids"] == []


def test_storage_policy_contract_fails_when_superseded_export_loses_successor_link() -> None:
    module = load_storage_export_audit_module()
    report = module.build_storage_export_audit_report()
    manifest = copy.deepcopy(report["manifest"])
    pdf_v1 = next(item for item in manifest["exports"] if item["id"] == "audit_pdf_v1")
    pdf_v1["superseded_by_export_id"] = None

    policy = module.validate_storage_policy_contract(
        solution_id=report["solution_id"],
        storage_contract=report["storage_contract"],
        manifest=manifest,
        recovery=report["recovery"],
    )

    assert policy["status"] == "failed"
    assert any(
        check["code"] == "version_chain.superseded_link" and check["export_id"] == "audit_pdf_v1"
        for check in policy["failed_checks"]
    )


def test_storage_export_audit_fails_when_adapter_contract_is_unsafe(monkeypatch) -> None:
    module = load_storage_export_audit_module()

    def unsafe_normalize(value: str) -> str:
        return value.replace("\\", "/").replace("../", "").strip("/")

    monkeypatch.setattr(module.storage, "normalize_object_key", unsafe_normalize)
    report = module.build_storage_export_audit_report()

    assert report["status"] == "failed"
    assert report["storage_contract"]["status"] == "failed"
    assert any(
        check["name"] == "unsafe object keys rejected" and check["status"] == "failed"
        for check in report["checks"]
    )


def test_storage_export_audit_fails_when_object_missing() -> None:
    module = load_storage_export_audit_module()

    report = module.build_storage_export_audit_report(simulate_missing=True)

    assert report["status"] == "failed"
    assert report["summary"]["failed_count"] >= 1
    assert report["recovery"]["status"] == "failed"
    assert report["recovery"]["missing_count"] == 1
    assert any(check["name"] == "recovery passed" and check["status"] == "failed" for check in report["checks"])


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_storage_export_audit_module()
    output_path = tmp_path / "audit.json"

    exit_code = module.main(["--simulate-missing", "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["recovery"]["missing_count"] == 1


def test_report_writer_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    module = load_storage_export_audit_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report: dict[str, Any] = {"schema_version": 1, "status": "passed"}

    output_path = module.write_report(Path("reports/storage-export-audit.json"), report)

    assert output_path == tmp_path / "reports" / "storage-export-audit.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
