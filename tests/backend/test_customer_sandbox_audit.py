from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "customer_sandbox_audit.py"
SAMPLE_PACK_PATH = REPO_ROOT / "samples" / "integrations" / "customer-sandbox" / "adapter-sandbox-pack.json"


def load_customer_sandbox_audit_module():
    spec = importlib.util.spec_from_file_location("customer_sandbox_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sample_pack_audit_passes_with_no_readiness_blockers() -> None:
    module = load_customer_sandbox_audit_module()

    report = module.build_customer_sandbox_audit_report(SAMPLE_PACK_PATH)

    assert report["status"] == "passed"
    assert report["pack_contract"]["status"] == "passed"
    assert report["pack_contract"]["failed_count"] == 0
    assert report["business_flow_contract"]["status"] == "passed"
    assert report["business_flow_contract"]["failed_count"] == 0
    assert report["summary"]["pack_contract_status"] == "passed"
    assert report["summary"]["business_flow_status"] == "passed"
    assert report["summary"]["adapter_count"] == 4
    assert report["summary"]["adapter_passed_count"] == 4
    assert report["summary"]["failed_count"] == 0
    assert report["readiness"]["failed_count"] == 0
    assert report["readiness"]["status"] in {"ready", "warning"}
    assert all("config" not in adapter for adapter in report["adapters"])
    assert {adapter["system_type"] for adapter in report["adapters"]} == {"crm", "mes", "erp"}


def test_audit_fails_when_mes_order_does_not_link_to_crm(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack["mes"]["config"]["pages"][0]["data"]["jobs"][0]["orderId"] = "CRM-SBX-MISSING"
    pack_path = tmp_path / "broken-mes-link-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["business_flow_failed_count"] == 1
    assert report["summary"]["adapter_count"] == 0
    failed_check = report["business_flow_contract"]["failed_checks"][0]
    assert failed_check["code"] == "business_flow.mes_orders.link_crm"
    assert failed_check["evidence"]["missing_crm_order_ids"] == ["CRM-SBX-MISSING"]


def test_audit_fails_when_erp_delivery_exceeds_crm_quantity(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack["erp_delivery"]["config"]["sample_records"][0]["qty"] = 1201
    pack_path = tmp_path / "broken-delivery-quantity-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["business_flow_failed_count"] == 1
    assert any(
        check["code"] == "business_flow.delivery.quantity_within_order"
        and check["evidence"]["over_delivered"][0]["order_id"] == "CRM-SBX-1001"
        for check in report["business_flow_contract"]["failed_checks"]
    )


def test_audit_fails_when_erp_inventory_does_not_cover_crm_material(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack["erp_inventory"]["config"]["sample_records"][0]["material"]["code"] = "OTHER-MATERIAL"
    pack_path = tmp_path / "broken-inventory-material-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["business_flow_failed_count"] == 1
    assert any(
        check["code"] == "business_flow.inventory.material_coverage"
        and check["evidence"]["missing_material_codes"] == ["WHITE-350"]
        for check in report["business_flow_contract"]["failed_checks"]
    )


def test_audit_fails_when_required_sample_records_are_missing(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack["crm"]["config"]["pages"] = []
    pack_path = tmp_path / "broken-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["pack_contract_failed_count"] >= 1
    assert report["summary"]["adapter_count"] == 0
    assert any(
        check["code"] == "adapter.sample_records" and check["sample_key"] == "crm"
        for check in report["pack_contract"]["failed_checks"]
    )


def test_audit_fails_when_pack_schema_version_is_missing(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack.pop("schema_version")
    pack_path = tmp_path / "missing-schema-version-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["pack_contract_status"] == "failed"
    assert any(check["code"] == "schema.version" for check in report["pack_contract"]["failed_checks"])


def test_audit_fails_when_domain_sample_drifts_to_wrong_target(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    broken_pack["erp_inventory"]["config"]["domain_target"] = "delivery_confirmation"
    pack_path = tmp_path / "wrong-domain-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_customer_sandbox_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["summary"]["adapter_count"] == 0
    assert any(
        check["code"] == "adapter.domain_target.expected" and check["sample_key"] == "erp_inventory"
        for check in report["pack_contract"]["failed_checks"]
    )


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_customer_sandbox_audit_module()
    pack_path = tmp_path / "empty-pack.json"
    output_path = tmp_path / "audit.json"
    pack_path.write_text("{}", encoding="utf-8")

    exit_code = module.main(["--pack", str(pack_path), "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["summary"]["pack_contract_failed_count"] >= 1
    assert any(check["code"] == "schema.version" for check in written["pack_contract"]["failed_checks"])


def test_report_writer_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    module = load_customer_sandbox_audit_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report = {"schema_version": 1, "status": "passed"}

    output_path = module.write_report(Path("reports/customer-sandbox-audit.json"), report)

    assert output_path == tmp_path / "reports" / "customer-sandbox-audit.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
