import json
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.db import models as dbm
from app.db.session import SessionLocal
from app.domain.schemas import AdapterConfigCreate, ExternalSystemCreate
from app.main import app
from app.services import adapters, repository
from auth_helpers import auth_headers


client = TestClient(app)


def test_adapter_config_versioning_validation_and_events() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]

    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"CRM {suffix}", "system_type": "crm", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()

    first_config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={"adapter_type": "crm_api", "config": {"auth_type": "api_key"}, "is_active": True},
    )
    assert first_config_response.status_code == 200
    first_config = first_config_response.json()
    assert first_config["version"] == 1

    failed_test = client.post(f"/api/adapters/configs/{first_config['id']}/test", headers=headers)
    assert failed_test.status_code == 200
    assert failed_test.json()["status"] == "failed"
    assert set(failed_test.json()["missing_fields"]) == {"base_url", "api_key"}

    second_config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "crm_api",
            "config": {"base_url": "https://crm.example.test/api", "auth_type": "api_key", "api_key": "secret-value"},
            "is_active": True,
        },
    )
    assert second_config_response.status_code == 200
    second_config = second_config_response.json()
    assert second_config["version"] == 2
    assert second_config["is_active"] is True
    assert second_config["config"]["api_key"] == "***"

    passed_test = client.post(f"/api/adapters/configs/{second_config['id']}/test", headers=headers)
    assert passed_test.status_code == 200
    assert passed_test.json()["status"] == "passed"

    configs = client.get(f"/api/adapters/configs?external_system_id={system['id']}", headers=headers)
    assert configs.status_code == 200
    by_id = {item["id"]: item for item in configs.json()}
    assert by_id[first_config["id"]]["is_active"] is False
    assert by_id[second_config["id"]]["validation_status"] == "passed"

    status_response = client.get("/api/adapters/status", headers=headers)
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["active_config_count"] >= 1
    assert any(item["id"] == system["id"] for item in status["systems"])

    sync_response = client.post("/api/adapters/crm/sync", headers=headers)
    assert sync_response.status_code == 200
    assert sync_response.json()["status"] == "completed"
    assert sync_response.json()["external_system_id"] == system["id"]

    writeback_response = client.post(f"/api/adapters/crm/writeback?target_id=sol_{suffix}", headers=headers)
    assert writeback_response.status_code == 200
    assert writeback_response.json()["status"] == "completed"
    assert writeback_response.json()["target_id"] == f"sol_{suffix}"

    tasks = client.get(f"/api/adapters/sync-tasks?external_system_id={system['id']}", headers=headers)
    assert tasks.status_code == 200
    assert any(item["task_type"] == "crm_sync" for item in tasks.json())

    writeback_logs = client.get(f"/api/adapters/writeback-logs?external_system_id={system['id']}", headers=headers)
    assert writeback_logs.status_code == 200
    assert any(item["target_id"] == f"sol_{suffix}" for item in writeback_logs.json())


def test_adapter_field_acceptance_passes_for_customer_crm_mapping() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"CRM Acceptance {suffix}", "system_type": "crm", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "crm_acceptance",
            "is_active": True,
            "config": {
                "mode": "mock",
                "records_path": "data.orders",
                "status_path": "state",
                "status_dictionary": {"READY": "ready"},
                "field_mapping": {
                    "order_id": "crm_id",
                    "external_order_id": "crm_id",
                    "customer_name": "customer.name",
                    "product_name": "product.title",
                    "quantity": "qty",
                    "material": "spec.material",
                    "thickness": "spec.thickness",
                },
                "pages": [
                    {
                        "data": {
                            "orders": [
                                {
                                    "crm_id": f"CRM-ACC-{suffix}",
                                    "state": "READY",
                                    "customer": {"name": "Acme"},
                                    "product": {"title": "Gift Box"},
                                    "qty": 800,
                                    "spec": {"material": "white_card", "thickness": "350gsm"},
                                }
                            ]
                        }
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    acceptance = client.post(f"/api/adapters/configs/{config_response.json()['id']}/field-acceptance", headers=headers)
    assert acceptance.status_code == 200
    payload = acceptance.json()
    assert payload["status"] == "passed"
    assert payload["sample_count"] == 1
    assert payload["required_missing_count"] == 0
    assert any(item["field"] == "product_name" and item["status"] == "passed" for item in payload["checks"])


def test_adapter_field_acceptance_flags_inventory_mapping_gaps() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Acceptance {suffix}", "system_type": "erp", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "erp_inventory_acceptance",
            "is_active": True,
            "config": {
                "mode": "mock",
                "domain_target": "inventory_snapshot",
                "external_id_path": "stockId",
                "status_path": "state",
                "field_mapping": {
                    "material_code": "material.code",
                    "available_qty": "available",
                    "reserved_qty": "reserved",
                    "unit": "unit",
                },
                "status_dictionary": {"AVAILABLE": "available"},
                "sample_records": [
                    {
                        "stockId": f"STOCK-ACC-{suffix}",
                        "state": "QUALITY_HOLD",
                        "available": 100,
                        "reserved": 10,
                        "unit": "sheet",
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    acceptance = client.post(f"/api/adapters/configs/{config_response.json()['id']}/field-acceptance", headers=headers)
    assert acceptance.status_code == 200
    payload = acceptance.json()
    assert payload["status"] == "failed"
    assert payload["required_missing_count"] >= 1
    assert payload["unmapped_status_count"] >= 1
    assert any(item["field"] == "material_code" and item["status"] == "failed" for item in payload["checks"])
    assert any(item["scope"] == "status" and item["status"] == "warning" for item in payload["checks"])


def test_real_adapter_activation_requires_dictionary_signoff() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Signoff {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_http_signoff",
            "is_active": False,
            "config": {
                "mode": "http",
                "dry_run": False,
                "base_url": "https://mes.example.test/api",
                "auth_type": "api_key",
                "api_key": "secret-value",
                "records_path": "data.jobs",
                "domain_target": "production_schedule",
                "external_id_path": "workOrder",
                "status_path": "state",
                "status_dictionary": {"READY_TO_RUN": "scheduled"},
                "field_mapping": {"order_id": "orderId", "job_id": "jobId", "quantity": "qty"},
                "pages": [
                    {
                        "data": {
                            "jobs": [
                                {
                                    "workOrder": f"WO-SIGN-{suffix}",
                                    "orderId": f"ORD-SIGN-{suffix}",
                                    "jobId": f"JOB-SIGN-{suffix}",
                                    "state": "READY_TO_RUN",
                                    "qty": 1200,
                                }
                            ]
                        }
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    config = config_response.json()

    validation = client.post(f"/api/adapters/configs/{config['id']}/test", headers=headers)
    assert validation.status_code == 200
    assert validation.json()["status"] == "passed"

    rejected = client.post(f"/api/adapters/configs/{config['id']}/activate", headers=headers)
    assert rejected.status_code == 409
    assert "dictionary signoff" in rejected.json()["detail"]

    signoff = client.post(
        f"/api/adapters/configs/{config['id']}/dictionary-signoff",
        headers=headers,
        json={
            "approver_name": "MES Owner",
            "note": "READY_TO_RUN mapped to scheduled for go-live",
            "accepted_unmapped_statuses": [],
            "confirmation": f"SIGNOFF {config['id']}",
        },
    )
    assert signoff.status_code == 200
    signoff_payload = signoff.json()
    assert signoff_payload["status"] == "signed"
    assert signoff_payload["dictionary_keys"] == ["status_dictionary"]
    assert signoff_payload["field_acceptance"]["status"] == "passed"

    activated = client.post(f"/api/adapters/configs/{config['id']}/activate", headers=headers)
    assert activated.status_code == 200
    activated_payload = activated.json()
    assert activated_payload["is_active"] is True
    assert activated_payload["config"]["dictionary_signoff"]["status"] == "signed"
    assert activated_payload["config"]["dictionary_signoff"]["approver_name"] == "MES Owner"

    logs = client.get("/api/operation-logs?limit=200", headers=headers)
    assert logs.status_code == 200
    assert any(
        item["action"] == "adapters.config.dictionary_signoff" and item["target_id"] == config["id"]
        for item in logs.json()
    )


def test_adapter_field_acceptance_blocks_missing_customer_org_codes() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    org_code = f"press_missing_{suffix}"
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Org Missing {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_org_acceptance",
            "is_active": False,
            "config": {
                "mode": "http",
                "dry_run": False,
                "base_url": "https://mes.example.test/api",
                "auth_type": "api_key",
                "api_key": "secret-value",
                "retry_count": 1,
                "records_path": "data.jobs",
                "domain_target": "production_schedule",
                "external_id_path": "workOrder",
                "status_path": "state",
                "status_dictionary": {"READY_TO_RUN": "scheduled"},
                "field_mapping": {"order_id": "orderId", "job_id": "jobId", "quantity": "qty"},
                "organization_acceptance": {
                    "required_org_unit_codes": [org_code],
                    "required_recipient_group_names": [f"Missing Production Group {suffix}"],
                },
                "pages": [
                    {
                        "data": {
                            "jobs": [
                                {
                                    "workOrder": f"WO-ORG-MISSING-{suffix}",
                                    "orderId": f"ORD-ORG-MISSING-{suffix}",
                                    "jobId": f"JOB-ORG-MISSING-{suffix}",
                                    "state": "READY_TO_RUN",
                                    "qty": 800,
                                }
                            ]
                        }
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    config = config_response.json()

    acceptance = client.post(f"/api/adapters/configs/{config['id']}/field-acceptance", headers=headers)
    assert acceptance.status_code == 200
    payload = acceptance.json()
    assert payload["status"] == "failed"
    assert payload["required_missing_count"] >= 1
    assert any(
        item["scope"] == "organization" and item["field"] == "org_unit_code" and item["status"] == "failed"
        for item in payload["checks"]
    )
    assert any(
        item["scope"] == "organization"
        and item["field"] == "recipient_group.department_codes"
        and item["status"] == "failed"
        for item in payload["checks"]
    )

    signoff = client.post(
        f"/api/adapters/configs/{config['id']}/dictionary-signoff",
        headers=headers,
        json={"confirmation": f"SIGNOFF {config['id']}", "accepted_unmapped_statuses": []},
    )
    assert signoff.status_code == 409
    assert "field acceptance" in signoff.json()["detail"]


def test_adapter_org_acceptance_signoff_and_readiness_use_customer_directory_codes() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    org_code = f"press_live_{suffix}"
    org_user = client.post(
        "/api/rbac/users",
        headers=headers,
        json={
            "email": f"adapter_org_{suffix}@example.com",
            "display_name": "Adapter Org Owner",
            "password": "Strong123!45",
            "org_unit_code": org_code,
            "org_unit_name": "Press Line A",
            "job_title": "Line Owner",
            "role_ids": [],
        },
    )
    assert org_user.status_code == 200
    group_name = f"Production Exceptions {suffix}"
    group_response = client.post(
        "/api/notifications/recipient-groups",
        headers=headers,
        json={
            "name": group_name,
            "description": "MES sandbox org acceptance recipients",
            "member_user_ids": [],
            "permission_codes": [],
            "department_codes": [org_code],
            "metadata": {"source": "adapter_org_acceptance_test"},
        },
    )
    assert group_response.status_code == 200
    assert group_response.json()["resolved_user_count"] == 1

    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Org Accepted {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_org_acceptance",
            "is_active": False,
            "config": {
                "mode": "http",
                "dry_run": False,
                "base_url": "https://mes.example.test/api",
                "auth_type": "api_key",
                "api_key": "secret-value",
                "retry_count": 1,
                "records_path": "data.jobs",
                "domain_target": "production_schedule",
                "external_id_path": "workOrder",
                "status_path": "state",
                "status_dictionary": {"READY_TO_RUN": "scheduled"},
                "field_mapping": {"order_id": "orderId", "job_id": "jobId", "quantity": "qty"},
                "organization_acceptance": {
                    "required_org_unit_codes": [org_code],
                    "required_recipient_group_names": [group_name],
                    "require_users": True,
                    "require_recipient_groups": True,
                },
                "pages": [
                    {
                        "data": {
                            "jobs": [
                                {
                                    "workOrder": f"WO-ORG-READY-{suffix}",
                                    "orderId": f"ORD-ORG-READY-{suffix}",
                                    "jobId": f"JOB-ORG-READY-{suffix}",
                                    "state": "READY_TO_RUN",
                                    "qty": 800,
                                }
                            ]
                        }
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    config = config_response.json()
    validation = client.post(f"/api/adapters/configs/{config['id']}/test", headers=headers)
    assert validation.status_code == 200
    assert validation.json()["status"] == "passed"

    acceptance = client.post(f"/api/adapters/configs/{config['id']}/field-acceptance", headers=headers)
    assert acceptance.status_code == 200
    acceptance_payload = acceptance.json()
    assert acceptance_payload["status"] == "passed"
    assert any(
        item["scope"] == "organization" and item["field"] == "org_unit_code" and item["status"] == "passed"
        for item in acceptance_payload["checks"]
    )
    assert any(
        item["scope"] == "organization"
        and item["field"] == "recipient_group.department_codes"
        and item["status"] == "passed"
        for item in acceptance_payload["checks"]
    )

    signoff = client.post(
        f"/api/adapters/configs/{config['id']}/dictionary-signoff",
        headers=headers,
        json={
            "approver_name": "Customer MES Owner",
            "note": "Sandbox org code and recipient group accepted",
            "confirmation": f"SIGNOFF {config['id']}",
            "accepted_unmapped_statuses": [],
        },
    )
    assert signoff.status_code == 200
    signoff_payload = signoff.json()
    assert signoff_payload["field_acceptance"]["status"] == "passed"
    assert "organization_acceptance" in signoff_payload["dictionary_keys"]

    activated = client.post(f"/api/adapters/configs/{config['id']}/activate", headers=headers)
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True

    readiness = client.get("/api/adapters/readiness?required_system_types=mes", headers=headers)
    assert readiness.status_code == 200
    readiness_checks = [
        item for item in readiness.json()["checks"] if item["target_type"] == "adapter_config" and item["target_id"] == config["id"]
    ]
    assert any(item["code"] == "field_acceptance" and item["status"] == "passed" for item in readiness_checks)
    assert any(item["code"] == "dictionary_signoff" and item["status"] == "passed" for item in readiness_checks)


def test_adapter_readiness_report_blocks_missing_required_system_and_accepts_signed_config() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]

    missing = client.get(f"/api/adapters/readiness?required_system_types=missing_{suffix}", headers=headers)
    assert missing.status_code == 200
    missing_payload = missing.json()
    assert missing_payload["status"] == "blocked"
    assert missing_payload["failed_count"] >= 1
    assert any(item["code"] == "system.enabled" and item["status"] == "failed" for item in missing_payload["checks"])

    system_type = "other"
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"Readiness {suffix}", "system_type": system_type, "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_http_readiness",
            "is_active": False,
            "config": {
                "mode": "http",
                "dry_run": False,
                "base_url": "https://mes.example.test/api",
                "auth_type": "api_key",
                "api_key": "secret-value",
                "retry_count": 1,
                "records_path": "data.jobs",
                "domain_target": "production_schedule",
                "external_id_path": "workOrder",
                "status_path": "state",
                "status_dictionary": {"READY_TO_RUN": "scheduled"},
                "field_mapping": {"order_id": "orderId", "job_id": "jobId", "quantity": "qty"},
                "pages": [
                    {
                        "data": {
                            "jobs": [
                                {
                                    "workOrder": f"WO-READY-{suffix}",
                                    "orderId": f"ORD-READY-{suffix}",
                                    "jobId": f"JOB-READY-{suffix}",
                                    "state": "READY_TO_RUN",
                                    "qty": 800,
                                }
                            ]
                        }
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    config = config_response.json()
    assert client.post(f"/api/adapters/configs/{config['id']}/test", headers=headers).status_code == 200
    signoff = client.post(
        f"/api/adapters/configs/{config['id']}/dictionary-signoff",
        headers=headers,
        json={"confirmation": f"SIGNOFF {config['id']}", "accepted_unmapped_statuses": []},
    )
    assert signoff.status_code == 200
    assert client.post(f"/api/adapters/configs/{config['id']}/activate", headers=headers).status_code == 200

    readiness = client.get(f"/api/adapters/readiness?required_system_types={system_type}", headers=headers)
    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["status"] in {"ready", "warning"}
    assert payload["failed_count"] == 0
    checks = payload["checks"]
    assert any(item["code"] == "field_acceptance" and item["status"] != "failed" for item in checks)
    assert any(item["code"] == "dictionary_signoff" and item["status"] == "passed" for item in checks)
    assert any(item["code"] == "source.retry_policy" and item["status"] == "passed" for item in checks)


def test_crm_sync_maps_paginated_records_into_orders() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"CRM Import {suffix}", "system_type": "crm", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mock_crm_import",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": False,
                "records_path": "data.orders",
                "field_mapping": {
                    "order_id": "crm_id",
                    "external_order_id": "crm_id",
                    "customer_name": "customer.name",
                    "product_name": "product.title",
                    "quantity": "qty",
                    "material": "spec.material",
                    "thickness": "spec.thickness",
                    "due_date": "due",
                },
                "defaults": {"print_side": "single", "min_gap_mm": 4, "bleed_mm": 2},
                "pages": [
                    {
                        "data": {
                            "orders": [
                                {
                                    "crm_id": f"CRM-{suffix}-001",
                                    "customer": {"name": "Acme"},
                                    "product": {"title": "Gift Box"},
                                    "qty": 1200,
                                    "spec": {"material": "white_card", "thickness": "350gsm"},
                                    "due": "2026-07-01",
                                }
                            ]
                        }
                    },
                    {
                        "data": {
                            "orders": [
                                {
                                    "crm_id": f"CRM-{suffix}-002",
                                    "customer": {"name": "Beta"},
                                    "product": {"title": "Display Box"},
                                    "qty": 600,
                                    "spec": {"material": "kraft", "thickness": "300gsm"},
                                    "due": "2026/07/03",
                                }
                            ]
                        }
                    },
                ],
            },
        },
    )
    assert config_response.status_code == 200

    sync_response = client.post("/api/adapters/crm/sync", headers=headers)

    assert sync_response.status_code == 200
    task = sync_response.json()
    assert task["status"] == "completed"
    assert task["payload"]["dry_run"] is False
    assert task["payload"]["page_count"] == 2
    assert task["payload"]["mapped_count"] == 2
    assert task["payload"]["imported_count"] == 2
    assert task["payload"]["rejected_count"] == 0

    imported = client.get(f"/api/orders/CRM-{suffix}-001", headers=headers)
    assert imported.status_code == 200
    order = imported.json()
    assert order["external_order_id"] == f"CRM-{suffix}-001"
    assert order["customer_name"] == "Acme"
    assert order["source_type"] == "crm_sync"
    assert order["min_gap_mm"] == 4


def test_crm_sync_dry_run_does_not_import_orders() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"CRM Dry Run {suffix}", "system_type": "crm", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mock_crm_import",
            "is_active": True,
            "config": {
                "mode": "mock",
                "field_mapping": {
                    "order_id": "id",
                    "product_name": "name",
                    "material": "material",
                    "thickness": "thickness",
                },
                "sample_records": [
                    {"id": f"CRM-DRY-{suffix}", "name": "Dry Run Box", "material": "white_card", "thickness": "350gsm"}
                ],
            },
        },
    )
    assert config_response.status_code == 200

    sync_response = client.post("/api/adapters/crm/sync?dry_run=true", headers=headers)

    assert sync_response.status_code == 200
    task = sync_response.json()
    assert task["status"] == "completed"
    assert task["payload"]["dry_run"] is True
    assert task["payload"]["mapped_count"] == 1
    assert task["payload"]["imported_count"] == 0
    assert client.get(f"/api/orders/CRM-DRY-{suffix}", headers=headers).status_code == 404


def test_http_crm_sync_fetches_paginated_remote_records_with_retry() -> None:
    suffix = uuid4().hex[:8]
    order_id = f"CRM-HTTP-{suffix}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer crm-token"
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        page = request.url.params.get("page")
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "orders": [
                            {
                                "id": order_id,
                                "customer": {"name": "HTTP Customer"},
                                "product": {"name": "HTTP Box"},
                                "qty": 88,
                                "material": "white_card",
                                "thickness": "350gsm",
                            }
                        ]
                    },
                    "pagination": {"next_page": 2},
                },
            )
        return httpx.Response(200, json={"data": {"orders": []}, "pagination": {"next_page": None}})

    transport = httpx.MockTransport(handler)
    with SessionLocal() as db:
        system = repository.create_external_system(
            db,
            ExternalSystemCreate(name=f"HTTP CRM {suffix}", system_type="crm", enabled=True),
        )
        config = repository.create_adapter_config(
            db,
            system.id,
            AdapterConfigCreate(
                adapter_type="crm_api",
                is_active=True,
                config={
                    "mode": "http",
                    "base_url": "https://crm.example.test/api",
                    "endpoint": "/orders",
                    "auth_type": "bearer",
                    "api_key": "crm-token",
                    "records_path": "data.orders",
                    "next_page_path": "pagination.next_page",
                    "retry_count": 1,
                    "field_mapping": {
                        "order_id": "id",
                        "external_order_id": "id",
                        "customer_name": "customer.name",
                        "product_name": "product.name",
                        "quantity": "qty",
                        "material": "material",
                        "thickness": "thickness",
                    },
                },
            ),
        )
        assert config is not None
        system_row, config_row = repository.get_active_adapter_config_for_system_type(db, "crm")
        result = adapters.sync_crm_orders(
            db,
            system=system_row,
            config=config_row,
            dry_run_override=False,
            http_transport=transport,
        )
        imported = repository.get_order(db, order_id)

    assert result["status"] == "completed"
    assert result["source"] == "http"
    assert result["http_request_count"] == 2
    assert result["http_status_codes"] == [200, 200]
    assert result["mapped_count"] == 1
    assert result["imported_count"] == 1
    assert len(requests) == 3
    assert requests[0].url.params.get("page") == "1"
    assert requests[-1].url.params.get("page") == "2"
    assert imported is not None
    assert imported.customer_name == "HTTP Customer"
    assert imported.source_type == "crm_sync"


def test_http_crm_sync_persists_incremental_cursor_and_reuses_it() -> None:
    suffix = uuid4().hex[:8]
    order_id = f"CRM-CURSOR-{suffix}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert request.url.params.get("updated_after") is None
            return httpx.Response(
                200,
                json={
                    "data": {
                        "orders": [
                            {
                                "id": order_id,
                                "customer": {"name": "Cursor Customer"},
                                "product": {"name": "Cursor Box"},
                                "qty": 12,
                                "material": "white_card",
                                "thickness": "350gsm",
                                "updated_at": "2026-06-27T09:30:00Z",
                            }
                        ]
                    },
                    "sync": {"next_cursor": "2026-06-27T10:00:00Z"},
                },
            )
        if len(requests) == 2:
            return httpx.Response(200, json={"data": {"orders": []}, "sync": {"next_cursor": "2026-06-27T10:00:00Z"}})
        assert request.url.params.get("updated_after") == "2026-06-27T10:00:00Z"
        return httpx.Response(200, json={"data": {"orders": []}, "sync": {"next_cursor": "2026-06-27T10:00:00Z"}})

    transport = httpx.MockTransport(handler)
    with SessionLocal() as db:
        system = repository.create_external_system(
            db,
            ExternalSystemCreate(name=f"HTTP Cursor CRM {suffix}", system_type="crm", enabled=True),
        )
        config = repository.create_adapter_config(
            db,
            system.id,
            AdapterConfigCreate(
                adapter_type="crm_api",
                is_active=True,
                config={
                    "mode": "http",
                    "base_url": "https://crm.example.test/api",
                    "endpoint": "/orders",
                    "records_path": "data.orders",
                    "incremental": True,
                    "incremental_cursor_param": "updated_after",
                    "incremental_cursor_path": "sync.next_cursor",
                    "field_mapping": {
                        "order_id": "id",
                        "external_order_id": "id",
                        "customer_name": "customer.name",
                        "product_name": "product.name",
                        "quantity": "qty",
                        "material": "material",
                        "thickness": "thickness",
                    },
                },
            ),
        )
        assert config is not None
        system_row, config_row = repository.get_active_adapter_config_for_system_type(db, "crm")
        first_result = adapters.sync_crm_orders(
            db,
            system=system_row,
            config=config_row,
            dry_run_override=False,
            http_transport=transport,
        )
        persisted_config = db.get(dbm.AdapterConfig, config.id)
        assert persisted_config is not None
        assert persisted_config.config["state"]["cursor"] == "2026-06-27T10:00:00Z"

        second_result = adapters.sync_crm_orders(
            db,
            system=system_row,
            config=persisted_config,
            dry_run_override=False,
            http_transport=transport,
        )

    assert first_result["status"] == "completed"
    assert first_result["cursor_persisted"] is True
    assert first_result["next_cursor"] == "2026-06-27T10:00:00Z"
    assert second_result["cursor_in"] == "2026-06-27T10:00:00Z"
    assert requests[-1].url.params.get("updated_after") == "2026-06-27T10:00:00Z"


def test_failed_crm_sync_can_be_retried_from_retry_queue() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    order_id = f"CRM-RETRY-{suffix}"
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"CRM Retry {suffix}", "system_type": "crm", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mock_crm_import",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": False,
                "field_mapping": {"order_id": "id"},
                "sample_records": [{"id": order_id}],
            },
        },
    )
    assert config_response.status_code == 200

    failed_sync = client.post("/api/adapters/crm/sync", headers=headers)
    assert failed_sync.status_code == 200
    failed_task = failed_sync.json()
    assert failed_task["status"] == "failed"
    assert failed_task["payload"]["retryable"] is True

    queue_response = client.get(f"/api/adapters/sync-tasks/retry-queue?external_system_id={system['id']}", headers=headers)
    assert queue_response.status_code == 200
    assert any(item["id"] == failed_task["id"] for item in queue_response.json())

    with SessionLocal() as db:
        config_row = db.get(dbm.AdapterConfig, config_response.json()["id"])
        assert config_row is not None
        config_row.config = {
            "mode": "mock",
            "dry_run": False,
            "field_mapping": {
                "order_id": "id",
                "external_order_id": "id",
                "product_name": "name",
                "material": "material",
                "thickness": "thickness",
            },
            "sample_records": [
                {"id": order_id, "name": "Recovered Box", "material": "kraft", "thickness": "300gsm"}
            ],
        }
        db.commit()

    retry_response = client.post(f"/api/adapters/sync-tasks/{failed_task['id']}/retry", headers=headers)
    assert retry_response.status_code == 200
    retry_task = retry_response.json()
    assert retry_task["status"] == "completed"
    assert retry_task["payload"]["retry_of_task_id"] == failed_task["id"]
    assert retry_task["payload"]["attempt"] == 2
    assert retry_task["payload"]["imported_count"] == 1

    queue_after_retry = client.get(f"/api/adapters/sync-tasks/retry-queue?external_system_id={system['id']}", headers=headers)
    assert queue_after_retry.status_code == 200
    assert all(item["id"] != failed_task["id"] for item in queue_after_retry.json())

    imported = client.get(f"/api/orders/{order_id}", headers=headers)
    assert imported.status_code == 200
    assert imported.json()["product_name"] == "Recovered Box"


def test_http_writeback_posts_mapped_payload_and_confirms_remote_result() -> None:
    suffix = uuid4().hex[:8]
    target_id = f"sol-writeback-{suffix}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PATCH"
        assert request.headers["x-write-key"] == "write-token"
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        body = request.read().decode("utf-8")
        assert target_id in body
        assert "REL" in body
        assert "0.91" in body
        assert request.url.path.endswith(f"/api/releases/{target_id}")
        return httpx.Response(200, json={"ok": True, "confirmation": {"id": f"ack-{suffix}"}})

    transport = httpx.MockTransport(handler)
    with SessionLocal() as db:
        system = repository.create_external_system(
            db,
            ExternalSystemCreate(name=f"Writeback CRM {suffix}", system_type="crm", enabled=True),
        )
        config = repository.create_adapter_config(
            db,
            system.id,
            AdapterConfigCreate(
                adapter_type="crm_api",
                is_active=True,
                config={
                    "mode": "mock",
                    "writeback": {
                        "mode": "http",
                        "base_url": "https://crm.example.test/api",
                        "endpoint": "/releases/{target_id}",
                        "method": "PATCH",
                        "auth_type": "api_key",
                        "api_key_header": "X-Write-Key",
                        "api_key": "write-token",
                        "retry_count": 1,
                        "confirmation_path": "ok",
                        "status_dictionary": {"released": "REL"},
                        "payload": {"source": "nesting"},
                        "field_mapping": {
                            "external_id": "target_id",
                            "state": "status",
                            "metrics.score": "payload.score",
                            "confirmed_at": "confirmed_at",
                        },
                    },
                },
            ),
        )
        assert config is not None
        system_row = db.get(dbm.ExternalSystem, system.id)
        config_row = db.get(dbm.AdapterConfig, config.id)
        assert system_row is not None and config_row is not None
        result = adapters.writeback_adapter_result(
            db,
            system=system_row,
            config=config_row,
            target_id=target_id,
            target_type="solution",
            status="released",
            payload={"score": 0.91},
            dry_run_override=False,
            http_transport=transport,
        )

    assert result["status"] == "completed"
    assert result["dry_run"] is False
    assert result["confirmed"] is True
    assert result["confirmation_status"] == "confirmed"
    assert result["requested_status"] == "released"
    assert result["external_status"] == "REL"
    assert result["http_status_code"] == 200
    assert len(requests) == 2


def test_generic_mes_writeback_dry_run_records_confirmation_payload() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    target_id = f"job-mes-{suffix}"
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Writeback {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_api",
            "is_active": True,
            "config": {"mode": "mock", "writeback": {"dry_run": True}},
        },
    )
    assert config_response.status_code == 200

    response = client.post(
        "/api/adapters/mes/writeback",
        headers=headers,
        json={"target_id": target_id, "target_type": "nesting_job", "status": "scheduled", "payload": {"line": "A1"}},
    )

    assert response.status_code == 200
    log = response.json()
    assert log["status"] == "completed"
    assert log["target_id"] == target_id
    assert log["payload"]["system_type"] == "mes"
    assert log["payload"]["dry_run"] is True
    assert log["payload"]["confirmation_status"] == "dry_run"
    assert log["payload"]["request_body"]["target_type"] == "nesting_job"
    assert log["payload"]["request_body"]["status"] == "scheduled"


def test_adapter_sync_writeback_and_domain_reads_redact_sensitive_payloads() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    target_id = f"job-redact-{suffix}"
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Redaction {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_redaction_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "external_id_path": "jobNo",
                "status_path": "state",
                "entity_type": "work_order",
                "domain_target": "production_schedule",
                "field_mapping": {
                    "order_id": "orderNo",
                    "job_id": "jobNo",
                    "line_code": "line",
                    "operator_token": "operatorToken",
                    "connection_url": "connectionUrl",
                    "webhook_url": "webhookUrl",
                    "public_note": "note",
                },
                "status_dictionary": {"READY": "scheduled"},
                "sample_records": [
                    {
                        "jobNo": f"MES-RED-{suffix}",
                        "orderNo": f"ORD-RED-{suffix}",
                        "state": "READY",
                        "line": "L2",
                        "operatorToken": "plain-operator-token",
                        "connectionUrl": "postgresql://mes:plain-db-password@db:5432/mes",
                        "webhookUrl": "https://hooks.example.test/send?token=plain-hook-token",
                        "note": "visible-note",
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200

    sync_response = client.post("/api/adapters/mes/sync?dry_run=false", headers=headers)

    assert sync_response.status_code == 200
    task = sync_response.json()
    fields = task["payload"]["records"][0]["fields"]
    assert fields["operator_token"] == "***"
    assert fields["connection_url"] == "postgresql://mes:***@db:5432/mes"
    assert fields["webhook_url"] == "***"
    assert fields["public_note"] == "visible-note"
    serialized_task = json.dumps(task, ensure_ascii=False)
    assert "plain-operator-token" not in serialized_task
    assert "plain-db-password" not in serialized_task
    assert "plain-hook-token" not in serialized_task

    schedule_response = client.get(
        f"/api/adapters/production-schedules?external_system_id={system['id']}",
        headers=headers,
    )
    assert schedule_response.status_code == 200
    schedule = next(item for item in schedule_response.json() if item["external_id"] == f"MES-RED-{suffix}")
    assert schedule["fields"]["operator_token"] == "***"
    assert schedule["fields"]["connection_url"] == "postgresql://mes:***@db:5432/mes"
    assert schedule["fields"]["webhook_url"] == "***"
    assert schedule["fields"]["public_note"] == "visible-note"

    writeback_response = client.post(
        "/api/adapters/mes/writeback",
        headers=headers,
        json={
            "target_id": target_id,
            "target_type": "nesting_job",
            "status": "scheduled",
            "payload": {
                "operator_token": "plain-write-token",
                "callback_url": "https://callback.example.test/push?secret=raw-secret",
                "public_note": "visible-note",
            },
        },
    )
    assert writeback_response.status_code == 200
    log = writeback_response.json()
    request_payload = log["payload"]["request_body"]["payload"]
    assert request_payload["operator_token"] == "***"
    assert request_payload["callback_url"] == "https://callback.example.test/push?secret=***"
    assert request_payload["public_note"] == "visible-note"
    serialized_log = json.dumps(log, ensure_ascii=False)
    assert "plain-write-token" not in serialized_log
    assert "raw-secret" not in serialized_log

    with SessionLocal() as db:
        task_row = db.get(dbm.SyncTask, task["id"])
        schedule_row = db.get(dbm.ProductionScheduleEntry, schedule["id"])
        log_row = db.get(dbm.WritebackLog, log["id"])
        assert task_row is not None and schedule_row is not None and log_row is not None
        serialized_db_payload = json.dumps(
            {
                "task": task_row.payload,
                "schedule_fields": schedule_row.fields,
                "writeback": log_row.payload,
            },
            ensure_ascii=False,
        )
    assert "plain-operator-token" not in serialized_db_payload
    assert "plain-db-password" not in serialized_db_payload
    assert "plain-hook-token" not in serialized_db_payload
    assert "plain-write-token" not in serialized_db_payload
    assert "raw-secret" not in serialized_db_payload


def test_http_mes_sync_normalizes_status_and_persists_incremental_cursor() -> None:
    suffix = uuid4().hex[:8]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert request.url.params.get("since") is None
            return httpx.Response(
                200,
                json={
                    "data": {
                        "jobs": [
                            {
                                "jobNo": f"MES-JOB-{suffix}",
                                "state": "READY_TO_RUN",
                                "line": "A1",
                                "updatedAt": "2026-06-27T11:00:00Z",
                            }
                        ]
                    },
                    "sync": {"cursor": "2026-06-27T11:30:00Z"},
                },
            )
        assert request.url.params.get("since") == "2026-06-27T11:30:00Z"
        return httpx.Response(200, json={"data": {"jobs": []}, "sync": {"cursor": "2026-06-27T11:30:00Z"}})

    transport = httpx.MockTransport(handler)
    with SessionLocal() as db:
        system = repository.create_external_system(
            db,
            ExternalSystemCreate(name=f"HTTP MES {suffix}", system_type="mes", enabled=True),
        )
        config = repository.create_adapter_config(
            db,
            system.id,
            AdapterConfigCreate(
                adapter_type="mes_api",
                is_active=True,
                config={
                    "mode": "http",
                    "base_url": "https://mes.example.test/api",
                    "endpoint": "/jobs",
                    "max_pages": 1,
                    "records_path": "data.jobs",
                    "external_id_path": "jobNo",
                    "status_path": "state",
                    "entity_type": "work_order",
                    "field_mapping": {"line_code": "line", "updated_at": "updatedAt"},
                    "inbound_status_dictionary": {"READY_TO_RUN": "ready"},
                    "incremental": True,
                    "incremental_cursor_param": "since",
                    "incremental_cursor_path": "sync.cursor",
                },
            ),
        )
        assert config is not None
        system_row, config_row = repository.get_active_adapter_config_for_system_type(db, "mes")
        first = adapters.sync_external_records(
            db,
            system=system_row,
            config=config_row,
            system_type="mes",
            dry_run_override=False,
            http_transport=transport,
        )
        persisted_config = db.get(dbm.AdapterConfig, config.id)
        assert persisted_config is not None
        assert persisted_config.config["state"]["cursor"] == "2026-06-27T11:30:00Z"
        second = adapters.sync_external_records(
            db,
            system=system_row,
            config=persisted_config,
            system_type="mes",
            dry_run_override=False,
            http_transport=transport,
        )

    assert first["status"] == "completed"
    assert first["normalized_count"] == 1
    assert first["records"][0]["external_id"] == f"MES-JOB-{suffix}"
    assert first["records"][0]["raw_status"] == "READY_TO_RUN"
    assert first["records"][0]["status"] == "ready"
    assert first["records"][0]["fields"]["line_code"] == "A1"
    assert first["cursor_persisted"] is True
    assert second["cursor_in"] == "2026-06-27T11:30:00Z"
    assert requests[-1].url.params.get("since") == "2026-06-27T11:30:00Z"


def test_erp_sync_api_records_snapshot_and_retry_queue() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Sync {suffix}", "system_type": "erp", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "erp_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "records_path": "items",
                "external_id_path": "doc_id",
                "status_path": "state",
                "entity_type": "shipment",
                "field_mapping": {"customer_code": "customer", "planned_date": "plan_date"},
                "status_dictionary": {"WAIT_RELEASE": "pending_release"},
                "pages": [
                    {
                        "items": [
                            {
                                "doc_id": f"ERP-DOC-{suffix}",
                                "state": "WAIT_RELEASE",
                                "customer": "C-100",
                                "plan_date": "2026-07-02",
                            }
                        ]
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200

    sync_response = client.post("/api/adapters/erp/sync?dry_run=true", headers=headers)

    assert sync_response.status_code == 200
    task = sync_response.json()
    assert task["status"] == "completed"
    assert task["task_type"] == "erp_sync"
    assert task["payload"]["system_type"] == "erp"
    assert task["payload"]["record_count"] == 1
    assert task["payload"]["normalized_count"] == 1
    record = task["payload"]["records"][0]
    assert record["external_id"] == f"ERP-DOC-{suffix}"
    assert record["status"] == "pending_release"
    assert record["fields"]["customer_code"] == "C-100"

    queue_response = client.get(f"/api/adapters/sync-tasks/retry-queue?external_system_id={system['id']}", headers=headers)
    assert queue_response.status_code == 200
    assert all(item["id"] != task["id"] for item in queue_response.json())


def test_mes_sync_persists_production_schedule_domain_records() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Schedule {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "external_id_path": "jobNo",
                "status_path": "state",
                "entity_type": "work_order",
                "domain_target": "production_schedule",
                "field_mapping": {
                    "order_id": "orderNo",
                    "job_id": "jobNo",
                    "line_code": "line",
                    "machine_code": "press",
                    "planned_start_at": "plan.start",
                    "planned_end_at": "plan.end",
                    "quantity": "qty",
                },
                "inbound_status_dictionary": {"WAIT_RELEASE": "pending_release"},
                "sample_records": [
                    {
                        "jobNo": f"MES-JOB-{suffix}",
                        "orderNo": f"ORD-{suffix}",
                        "state": "WAIT_RELEASE",
                        "line": "L1",
                        "press": "PRESS-2",
                        "qty": 1200,
                        "plan": {"start": "2026-07-01T08:00:00+08:00", "end": "2026-07-01T12:00:00+08:00"},
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200

    sync_response = client.post("/api/adapters/mes/sync?dry_run=false", headers=headers)

    assert sync_response.status_code == 200
    task = sync_response.json()
    assert task["status"] == "completed"
    assert task["payload"]["domain_target"] == "production_schedule"
    assert task["payload"]["domain_persisted_count"] == 1
    assert task["payload"]["domain_targets"]["production_schedule"] == 1

    schedule_response = client.get(
        f"/api/adapters/production-schedules?external_system_id={system['id']}",
        headers=headers,
    )
    assert schedule_response.status_code == 200
    schedules = schedule_response.json()
    row = next(item for item in schedules if item["external_id"] == f"MES-JOB-{suffix}")
    assert row["sync_task_id"] == task["id"]
    assert row["order_id"] == f"ORD-{suffix}"
    assert row["line_code"] == "L1"
    assert row["machine_code"] == "PRESS-2"
    assert row["status"] == "pending_release"
    assert row["quantity"] == 1200


def test_erp_sync_persists_inventory_and_delivery_domain_records() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    inventory_system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Inventory {suffix}", "system_type": "erp", "enabled": True},
    )
    assert inventory_system_response.status_code == 200
    inventory_system = inventory_system_response.json()
    inventory_config = client.post(
        f"/api/adapters/systems/{inventory_system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "erp_inventory_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "external_id_path": "stockId",
                "status_path": "state",
                "domain_target": "inventory_snapshot",
                "field_mapping": {
                    "material_code": "material.code",
                    "material_name": "material.name",
                    "warehouse_code": "warehouse",
                    "available_qty": "available",
                    "reserved_qty": "reserved",
                    "unit": "unit",
                },
                "status_dictionary": {"AVAILABLE": "available"},
                "sample_records": [
                    {
                        "stockId": f"STOCK-{suffix}",
                        "state": "AVAILABLE",
                        "material": {"code": "WHITE-350", "name": "White card 350gsm"},
                        "warehouse": "WH-A",
                        "available": 9000,
                        "reserved": 1200,
                        "unit": "sheet",
                    }
                ],
            },
        },
    )
    assert inventory_config.status_code == 200

    inventory_sync = client.post("/api/adapters/erp/sync?dry_run=false", headers=headers)

    assert inventory_sync.status_code == 200
    inventory_task = inventory_sync.json()
    assert inventory_task["payload"]["domain_persisted_count"] == 1
    inventory_response = client.get(
        f"/api/adapters/inventory-snapshots?external_system_id={inventory_system['id']}",
        headers=headers,
    )
    assert inventory_response.status_code == 200
    inventory_row = next(item for item in inventory_response.json() if item["external_id"] == f"STOCK-{suffix}")
    assert inventory_row["sync_task_id"] == inventory_task["id"]
    assert inventory_row["material_code"] == "WHITE-350"
    assert inventory_row["available_qty"] == 9000
    assert inventory_row["reserved_qty"] == 1200
    assert inventory_row["status"] == "available"

    delivery_system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Delivery {suffix}", "system_type": "erp", "enabled": True},
    )
    assert delivery_system_response.status_code == 200
    delivery_system = delivery_system_response.json()
    delivery_config = client.post(
        f"/api/adapters/systems/{delivery_system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "erp_delivery_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "external_id_path": "deliveryId",
                "status_path": "state",
                "domain_target": "delivery_confirmation",
                "field_mapping": {
                    "order_id": "orderNo",
                    "shipment_no": "shipmentNo",
                    "carrier": "carrier",
                    "tracking_no": "tracking",
                    "delivered_at": "deliveredAt",
                    "quantity": "qty",
                },
                "status_dictionary": {"SIGNED": "delivered"},
                "sample_records": [
                    {
                        "deliveryId": f"DELIV-{suffix}",
                        "orderNo": f"ORD-{suffix}",
                        "shipmentNo": f"SHP-{suffix}",
                        "state": "SIGNED",
                        "carrier": "SF",
                        "tracking": "SF123456",
                        "deliveredAt": "2026-07-03T16:30:00+08:00",
                        "qty": 1200,
                    }
                ],
            },
        },
    )
    assert delivery_config.status_code == 200

    delivery_sync = client.post("/api/adapters/erp/sync?dry_run=false", headers=headers)

    assert delivery_sync.status_code == 200
    delivery_task = delivery_sync.json()
    assert delivery_task["payload"]["domain_persisted_count"] == 1
    delivery_response = client.get(
        f"/api/adapters/delivery-confirmations?external_system_id={delivery_system['id']}",
        headers=headers,
    )
    assert delivery_response.status_code == 200
    delivery_row = next(item for item in delivery_response.json() if item["external_id"] == f"DELIV-{suffix}")
    assert delivery_row["sync_task_id"] == delivery_task["id"]
    assert delivery_row["order_id"] == f"ORD-{suffix}"
    assert delivery_row["shipment_no"] == f"SHP-{suffix}"
    assert delivery_row["status"] == "delivered"
    assert delivery_row["quantity"] == 1200


def test_failed_mes_sync_can_be_retried_from_retry_queue() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"MES Retry {suffix}", "system_type": "mes", "enabled": True},
    )
    assert system_response.status_code == 200
    system = system_response.json()
    config_response = client.post(
        f"/api/adapters/systems/{system['id']}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_api",
            "is_active": True,
            "config": {"mode": "http", "dry_run": True, "endpoint": "/jobs"},
        },
    )
    assert config_response.status_code == 200

    failed_sync = client.post("/api/adapters/mes/sync?dry_run=true", headers=headers)
    assert failed_sync.status_code == 200
    failed_task = failed_sync.json()
    assert failed_task["status"] == "failed"
    assert failed_task["task_type"] == "mes_sync"

    queue_response = client.get(f"/api/adapters/sync-tasks/retry-queue?external_system_id={system['id']}", headers=headers)
    assert queue_response.status_code == 200
    assert any(item["id"] == failed_task["id"] for item in queue_response.json())

    with SessionLocal() as db:
        config_row = db.get(dbm.AdapterConfig, config_response.json()["id"])
        assert config_row is not None
        config_row.config = {
            "mode": "mock",
            "dry_run": True,
            "external_id_path": "job",
            "status_path": "state",
            "status_dictionary": {"READY": "ready"},
            "sample_records": [{"job": f"MES-RETRY-{suffix}", "state": "READY"}],
        }
        db.commit()

    retry_response = client.post(f"/api/adapters/sync-tasks/{failed_task['id']}/retry", headers=headers)
    assert retry_response.status_code == 200
    retry_task = retry_response.json()
    assert retry_task["status"] == "completed"
    assert retry_task["task_type"] == "mes_sync"
    assert retry_task["payload"]["retry_of_task_id"] == failed_task["id"]
    assert retry_task["payload"]["normalized_count"] == 1
    assert retry_task["payload"]["records"][0]["status"] == "ready"

    queue_after_retry = client.get(f"/api/adapters/sync-tasks/retry-queue?external_system_id={system['id']}", headers=headers)
    assert queue_after_retry.status_code == 200
    assert all(item["id"] != failed_task["id"] for item in queue_after_retry.json())
