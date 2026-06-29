from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "external_acceptance_audit.py"


def load_external_acceptance_audit_module():
    spec = importlib.util.spec_from_file_location("external_acceptance_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_external_acceptance_audit_accepts_complete_manifest(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    acceptance_file = write_acceptance_manifest(module, tmp_path)

    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )

    assert report["status"] == "passed"
    assert report["summary"]["required_area_count"] == 5
    assert report["summary"]["passed_area_count"] == 5
    assert report["summary"]["verified_evidence_file_count"] == 5
    assert report["errors"] == []
    assert report["missing_areas"] == []
    assert report["invalid_areas"] == []


def test_external_acceptance_audit_can_skip_or_fail_when_file_is_missing() -> None:
    module = load_external_acceptance_audit_module()

    skipped = module.build_external_acceptance_audit()
    failed = module.build_external_acceptance_audit(require_acceptance_file=True)

    assert skipped["status"] == "skipped"
    assert "external acceptance file was not provided" in skipped["warnings"]
    assert failed["status"] == "failed"
    assert "external acceptance file is required" in failed["errors"]
    assert failed["summary"]["missing_area_count"] == 5


def test_external_acceptance_audit_rejects_missing_area_pending_status_and_no_evidence(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    manifest["entries"] = manifest["entries"][:-1]
    manifest["entries"][0]["status"] = "pending"
    manifest["entries"][1]["evidence_files"] = []
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )

    assert report["status"] == "failed"
    assert report["summary"]["missing_area_count"] == 1
    assert report["summary"]["invalid_area_count"] == 2
    assert report["missing_areas"] == ["production_deployment"]
    invalid_by_area = {item["area"]: item for item in report["invalid_areas"]}
    assert "status must be passed" in invalid_by_area["customer_integration_sandbox"]["errors"]
    assert "evidence_files must contain at least one file" in invalid_by_area["notification_channel_sandbox"]["errors"]


def test_external_acceptance_audit_rejects_missing_ticket_and_evidence_description(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    manifest["entries"][0]["ticket"] = ""
    manifest["entries"][1]["evidence_files"][0].pop("description")
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )

    assert report["status"] == "failed"
    assert report["summary"]["invalid_area_count"] == 2
    invalid_by_area = {item["area"]: item for item in report["invalid_areas"]}
    assert "ticket is required" in invalid_by_area["customer_integration_sandbox"]["errors"]
    assert "one or more evidence files failed validation" in invalid_by_area["notification_channel_sandbox"]["errors"]
    failed_by_area = {item["area"]: item for item in report["failed_evidence_files"]}
    assert "evidence file description is required" in failed_by_area["notification_channel_sandbox"]["errors"]


def test_external_acceptance_audit_requires_timezone_aware_reviewed_at(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    date_only_file = tmp_path / "external-acceptance-date-only.json"
    date_only = {**manifest, "reviewed_at": "2026-06-29"}
    date_only_file.write_text(json.dumps(date_only, ensure_ascii=False), encoding="utf-8")
    naive_datetime_file = tmp_path / "external-acceptance-naive-datetime.json"
    naive_datetime = {**manifest, "reviewed_at": "2026-06-29T10:00:00"}
    naive_datetime_file.write_text(json.dumps(naive_datetime, ensure_ascii=False), encoding="utf-8")

    date_only_report = module.build_external_acceptance_audit(
        acceptance_file=date_only_file,
        require_acceptance_file=True,
    )
    naive_datetime_report = module.build_external_acceptance_audit(
        acceptance_file=naive_datetime_file,
        require_acceptance_file=True,
    )

    expected_error = "external acceptance reviewed_at must be a timezone-aware ISO datetime"
    assert date_only_report["status"] == "failed"
    assert expected_error in date_only_report["errors"]
    assert naive_datetime_report["status"] == "failed"
    assert expected_error in naive_datetime_report["errors"]


def test_external_acceptance_audit_detects_tampered_evidence_file(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    acceptance_file = write_acceptance_manifest(module, tmp_path)
    (tmp_path / "customer_integration_sandbox.json").write_text("tampered", encoding="utf-8")

    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )

    assert report["status"] == "failed"
    assert report["summary"]["failed_evidence_file_count"] == 1
    assert report["failed_evidence_files"][0]["area"] == "customer_integration_sandbox"
    assert "evidence file size_bytes mismatch" in report["failed_evidence_files"][0]["errors"]
    assert "evidence file sha256 mismatch" in report["failed_evidence_files"][0]["errors"]


def test_external_acceptance_audit_rejects_unsafe_placeholder_and_absolute_paths(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    manifest["reviewer"] = "<REPLACE_WITH_REVIEWER>"
    manifest["entries"][0]["evidence_files"][0]["path"] = str(tmp_path / "customer_integration_sandbox.json")
    manifest["entries"][1]["evidence_files"][0]["path"] = "../outside.json"
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = module.build_external_acceptance_audit(
        acceptance_file=acceptance_file,
        require_acceptance_file=True,
    )

    assert report["status"] == "failed"
    assert "$.reviewer contains a placeholder value" in report["errors"]
    failed_errors = "\n".join(error for item in report["failed_evidence_files"] for error in item["errors"])
    assert "evidence file path must be relative" in failed_errors
    assert "evidence file path must not escape" in failed_errors


def test_external_acceptance_audit_template_and_cli(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    template_path = tmp_path / "external-acceptance-template.json"
    audit_path = tmp_path / "external-acceptance-audit.json"

    template_exit_code = module.main(["--write-template", str(template_path)])
    audit_exit_code = module.main(
        [
            "--acceptance-file",
            str(template_path),
            "--require-acceptance-file",
            "--output",
            str(audit_path),
        ]
    )

    template = json.loads(template_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert template_exit_code == 0
    assert len(template["entries"]) == 5
    assert all(entry["status"] == "pending" for entry in template["entries"])
    assert all(len(entry["evidence_files"]) == 1 for entry in template["entries"])
    assert all(
        set(entry["evidence_files"][0]) == {"path", "size_bytes", "sha256", "description"}
        for entry in template["entries"]
    )
    assert all(
        entry["evidence_files"][0]["path"] == f"external-evidence-files/{entry['area']}.json"
        for entry in template["entries"]
    )
    assert audit_exit_code == 1
    assert audit["status"] == "failed"
    assert audit["summary"]["invalid_area_count"] == 5


def test_external_acceptance_audit_refreshes_evidence_metadata(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    for entry in manifest["entries"]:
        for evidence in entry["evidence_files"]:
            evidence["size_bytes"] = 1
            evidence["sha256"] = "0" * 64
    input_path = tmp_path / "external-acceptance-unrefreshed.json"
    output_path = tmp_path / "external-acceptance.json"
    refresh_report_path = tmp_path / "external-acceptance-refresh-report.json"
    audit_path = tmp_path / "external-acceptance-audit.json"
    input_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    refresh_exit_code = module.main(
        [
            "--refresh-evidence-metadata",
            str(input_path),
            "--refreshed-output",
            str(output_path),
            "--output",
            str(refresh_report_path),
        ]
    )
    audit_exit_code = module.main(
        [
            "--acceptance-file",
            str(output_path),
            "--require-acceptance-file",
            "--output",
            str(audit_path),
        ]
    )

    refreshed = json.loads(output_path.read_text(encoding="utf-8"))
    refresh_report = json.loads(refresh_report_path.read_text(encoding="utf-8"))
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert refresh_exit_code == 0
    assert refresh_report["status"] == "passed"
    assert refresh_report["summary"]["updated_evidence_file_count"] == 5
    assert audit_exit_code == 0
    assert audit["status"] == "passed"
    for entry in refreshed["entries"]:
        for evidence in entry["evidence_files"]:
            evidence_path = tmp_path / evidence["path"]
            assert evidence["size_bytes"] == evidence_path.stat().st_size
            assert evidence["sha256"] == module.sha256_file(evidence_path)


def test_external_acceptance_audit_refresh_rejects_unsafe_evidence_path(tmp_path: Path) -> None:
    module = load_external_acceptance_audit_module()
    manifest = valid_manifest(module, tmp_path)
    manifest["entries"][0]["evidence_files"][0]["path"] = "../outside.json"
    input_path = tmp_path / "external-acceptance-unrefreshed.json"
    output_path = tmp_path / "external-acceptance.json"
    input_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = module.refresh_external_acceptance_evidence_metadata(
        acceptance_file=input_path,
        output_file=output_path,
    )

    assert report["status"] == "failed"
    assert not output_path.exists()
    assert any("must not escape" in error for error in report["errors"])


def write_acceptance_manifest(module, tmp_path: Path) -> Path:
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(json.dumps(valid_manifest(module, tmp_path), ensure_ascii=False), encoding="utf-8")
    return acceptance_file


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
