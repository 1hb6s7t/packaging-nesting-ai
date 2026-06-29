from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "conversion_supplier_audit.py"


def load_conversion_supplier_audit_module():
    spec = importlib.util.spec_from_file_location("conversion_supplier_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_conversion_supplier_audit_passes_submit_callback_error_and_sla_checks() -> None:
    module = load_conversion_supplier_audit_module()

    report = module.build_conversion_supplier_audit_report()

    assert report["status"] == "passed"
    assert report["summary"]["failed_count"] == 0
    assert report["policy_contract"]["status"] == "passed"
    assert report["policy_contract"]["failed_count"] == 0
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["submit"]["status"] == "submitted"
    assert report["submit"]["remote_status_code"] == 202
    assert report["submit"]["request_count"] == 2
    assert report["submit"]["endpoint_url_https"] is True
    assert report["submit"]["callback_url_https"] is True
    assert report["submit"]["authorization_header_present"] is True
    assert report["submit"]["multipart_contains_job_id"] is True
    assert report["submit"]["multipart_contains_source_bytes"] is True
    assert report["token_rotation"]["rotated"] is True
    assert report["token_rotation"]["history_tail"] == report["token_rotation"]["old_token_tail"]
    assert report["token_rotation"]["submit_attempt"] == 2
    assert report["token_rotation"]["hash_stored"] is True
    assert report["token_rotation"]["plaintext_stored"] is False
    assert report["callback"]["old_token_rejected"] is True
    assert report["callback"]["job_status"] == "completed"
    assert report["callback"]["polygon_count"] == 1
    assert report["callback"]["has_artwork_version"] is True
    assert report["callback"]["artwork_version_target_format"] == "svg"
    assert report["callback"]["normalized_storage_key"]
    assert report["vendor_error"]["job_status"] == "manual_required"
    assert report["vendor_error"]["code"] == "unsupported_format"
    assert report["vendor_error"]["mapped_status"] == "manual_required"
    assert report["sla"]["status"] == "overdue"
    assert report["sla"]["notification_count"] == 0
    assert report["sla"]["contains_sla_job"] is True
    assert all(check["status"] == "passed" for check in report["checks"])
    serialized = json.dumps(report, ensure_ascii=False)
    assert "audit-old-token-123456" not in serialized
    assert "audit-converter-secret" not in serialized


def test_conversion_supplier_policy_contract_fails_when_submit_is_not_authenticated() -> None:
    module = load_conversion_supplier_audit_module()
    report = module.build_conversion_supplier_audit_report()
    broken_report = copy.deepcopy(report)
    broken_report["submit"]["authorization_header_present"] = False

    policy = module.validate_conversion_supplier_policy_contract(broken_report)

    assert policy["status"] == "failed"
    failed_check = next(check for check in policy["failed_checks"] if check["code"] == "submit.authentication")
    assert failed_check["evidence"]["authorization_header_present"] is False


def test_conversion_supplier_policy_contract_fails_when_plaintext_token_is_stored() -> None:
    module = load_conversion_supplier_audit_module()
    report = module.build_conversion_supplier_audit_report()
    broken_report = copy.deepcopy(report)
    broken_report["token_rotation"]["plaintext_stored"] = True

    policy = module.validate_conversion_supplier_policy_contract(broken_report)

    assert policy["status"] == "failed"
    failed_check = next(check for check in policy["failed_checks"] if check["code"] == "token.storage")
    assert failed_check["evidence"]["plaintext_stored"] is True


def test_conversion_supplier_policy_contract_fails_when_normalized_artifact_is_missing() -> None:
    module = load_conversion_supplier_audit_module()
    report = module.build_conversion_supplier_audit_report()
    broken_report = copy.deepcopy(report)
    broken_report["callback"]["has_artwork_version"] = False
    broken_report["callback"]["normalized_storage_key"] = None

    policy = module.validate_conversion_supplier_policy_contract(broken_report)

    assert policy["status"] == "failed"
    failed_check = next(check for check in policy["failed_checks"] if check["code"] == "callback.normalized_artifact")
    assert failed_check["evidence"]["has_artwork_version"] is False
    assert failed_check["evidence"]["normalized_storage_key_present"] is False


def test_conversion_supplier_policy_contract_fails_when_sla_job_is_missing() -> None:
    module = load_conversion_supplier_audit_module()
    report = module.build_conversion_supplier_audit_report()
    broken_report = copy.deepcopy(report)
    broken_report["sla"]["contains_sla_job"] = False

    policy = module.validate_conversion_supplier_policy_contract(broken_report)

    assert policy["status"] == "failed"
    failed_check = next(check for check in policy["failed_checks"] if check["code"] == "sla.overdue_detection")
    assert failed_check["evidence"]["contains_sla_job"] is False


def test_conversion_supplier_audit_fails_when_submit_fails() -> None:
    module = load_conversion_supplier_audit_module()

    report = module.build_conversion_supplier_audit_report(simulate_submit_failure=True)

    assert report["status"] == "failed"
    assert report["summary"]["failed_count"] >= 1
    assert report["policy_contract"]["status"] == "failed"
    assert report["summary"]["policy_contract_failed_count"] >= 1
    assert report["submit"]["status"] == "failed"
    assert report["submit"]["remote_status_code"] is None
    assert "503" in report["submit"]["message"]
    assert report["submit"]["request_count"] == 1
    assert report["submit"]["authorization_header_present"] is True
    assert report["submit"]["multipart_contains_job_id"] is True
    assert report["submit"]["multipart_contains_source_bytes"] is True
    assert any(check["name"] == "supplier submit accepted" and check["status"] == "failed" for check in report["checks"])


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_conversion_supplier_audit_module()
    output_path = tmp_path / "audit.json"

    exit_code = module.main(["--simulate-submit-failure", "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["submit"]["status"] == "failed"
    assert written["submit"]["remote_status_code"] is None
    assert "503" in written["submit"]["message"]


def test_report_writer_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    module = load_conversion_supplier_audit_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report = {"schema_version": 1, "status": "passed"}

    output_path = module.write_report(Path("reports/conversion-supplier-audit.json"), report)

    assert output_path == tmp_path / "reports" / "conversion-supplier-audit.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
