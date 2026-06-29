from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def _order_payload(order_id: str, material: str, quantity: int) -> dict:
    return {
        "order_id": order_id,
        "product_name": f"Box {order_id}",
        "quantity": quantity,
        "material": material,
        "thickness": "350gsm",
    }


def _job_payload(job_id: str, first_order_id: str, second_order_id: str, material: str) -> dict:
    return {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"sheet_{job_id}",
            "width": 500,
            "height": 400,
            "margin_top": 5,
            "margin_right": 5,
            "margin_bottom": 5,
            "margin_left": 5,
            "gripper_mm": 10,
            "material": material,
            "thickness": "350gsm",
            "cost_per_sheet": 3.5,
        },
        "candidate_items": [
            {
                "item_id": f"{job_id}_item_1",
                "order_id": first_order_id,
                "polygon": {"shape_id": f"{job_id}_shape_1", "outer": [[0, 0], [100, 0], [100, 80], [0, 80]]},
            },
            {
                "item_id": f"{job_id}_item_2",
                "order_id": second_order_id,
                "polygon": {"shape_id": f"{job_id}_shape_2", "outer": [[0, 0], [80, 0], [80, 60], [0, 60]]},
            },
        ],
    }


def _sync_inventory(headers: dict[str, str], system_id: str, stock_id: str, material: str, available: int, reserved: int) -> None:
    config_response = client.post(
        f"/api/adapters/systems/{system_id}/configs",
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
                    "available_qty": "available",
                    "reserved_qty": "reserved",
                    "unit": "unit",
                },
                "status_dictionary": {"AVAILABLE": "available"},
                "sample_records": [
                    {
                        "stockId": stock_id,
                        "state": "AVAILABLE",
                        "material": {"code": material, "name": material},
                        "available": available,
                        "reserved": reserved,
                        "unit": "sheet",
                    }
                ],
            },
        },
    )
    assert config_response.status_code == 200
    sync_response = client.post("/api/adapters/erp/sync?dry_run=false", headers=headers)
    assert sync_response.status_code == 200
    assert sync_response.json()["payload"]["domain_persisted_count"] == 1


def test_nesting_material_readiness_uses_erp_inventory_snapshots() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    material = f"mat_{suffix}"
    first_order_id = f"MAT-{suffix}-001"
    second_order_id = f"MAT-{suffix}-002"
    job_id = f"job_mat_{suffix}"

    orders_response = client.post(
        "/api/orders/import",
        headers=headers,
        json={"orders": [_order_payload(first_order_id, material, 100), _order_payload(second_order_id, material, 50)]},
    )
    assert orders_response.status_code == 200

    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Material {suffix}", "system_type": "erp", "enabled": True},
    )
    assert system_response.status_code == 200
    system_id = system_response.json()["id"]
    stock_id = f"STOCK-MAT-{suffix}"

    _sync_inventory(headers, system_id, stock_id, material, available=220, reserved=20)

    create_job = client.post(
        "/api/nesting/jobs",
        headers=headers,
        json=_job_payload(job_id, first_order_id, second_order_id, material),
    )
    assert create_job.status_code == 200

    ready_response = client.get(f"/api/nesting/jobs/{job_id}/material-readiness", headers=headers)
    assert ready_response.status_code == 200
    ready = ready_response.json()
    assert ready["overall_status"] == "ready"
    assert ready["missing_order_ids"] == []
    assert ready["items"][0]["material"] == material
    assert ready["items"][0]["required_qty"] == 150
    assert ready["items"][0]["net_available_qty"] == 200
    assert ready["items"][0]["status"] == "ok"

    _sync_inventory(headers, system_id, stock_id, material, available=120, reserved=30)

    blocked_response = client.get(f"/api/nesting/jobs/{job_id}/material-readiness", headers=headers)
    assert blocked_response.status_code == 200
    blocked = blocked_response.json()
    assert blocked["overall_status"] == "blocked"
    assert blocked["items"][0]["net_available_qty"] == 90
    assert blocked["items"][0]["shortage_qty"] == 60
    assert blocked["items"][0]["status"] == "shortage"


def test_procurement_alert_check_creates_shortage_recommendation_and_dedupes_notifications() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    material = f"proc_mat_{suffix}"
    first_order_id = f"PROC-{suffix}-001"
    second_order_id = f"PROC-{suffix}-002"
    job_id = f"job_proc_{suffix}"

    orders_response = client.post(
        "/api/orders/import",
        headers=headers,
        json={"orders": [_order_payload(first_order_id, material, 100), _order_payload(second_order_id, material, 50)]},
    )
    assert orders_response.status_code == 200

    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Procurement {suffix}", "system_type": "erp", "enabled": True},
    )
    assert system_response.status_code == 200
    system_id = system_response.json()["id"]

    _sync_inventory(headers, system_id, f"STOCK-PROC-{suffix}", material, available=110, reserved=20)
    create_job = client.post(
        "/api/nesting/jobs",
        headers=headers,
        json=_job_payload(job_id, first_order_id, second_order_id, material),
    )
    assert create_job.status_code == 200

    alert_response = client.post(
        f"/api/nesting/jobs/{job_id}/procurement-alerts/check",
        headers=headers,
        json={"notify": True, "dedupe_minutes": 60, "safety_stock_rate": 0.1},
    )
    assert alert_response.status_code == 200
    payload = alert_response.json()
    assert payload["status"] == "alerting"
    assert payload["notification_count"] >= 1
    recommendation = payload["recommendations"][0]
    assert recommendation["material"] == material
    assert recommendation["shortage_qty"] == 60
    assert recommendation["recommended_purchase_qty"] == 66
    assert set(recommendation["order_ids"]) == {first_order_id, second_order_id}

    notifications = client.get("/api/notifications?unread_only=true&limit=300", headers=headers)
    assert notifications.status_code == 200
    assert any(
        item["event_type"] == "procurement.material_shortage" and item["target_id"] == job_id
        for item in notifications.json()
    )

    repeated_response = client.post(
        f"/api/nesting/jobs/{job_id}/procurement-alerts/check",
        headers=headers,
        json={"notify": True, "dedupe_minutes": 60, "safety_stock_rate": 0.1},
    )
    assert repeated_response.status_code == 200
    assert repeated_response.json()["status"] == "alerting"
    assert repeated_response.json()["notification_count"] == 0


def test_exception_writeback_creates_procurement_dry_run_for_material_shortage() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    material = f"wb_proc_mat_{suffix}"
    first_order_id = f"WB-PROC-{suffix}-001"
    second_order_id = f"WB-PROC-{suffix}-002"
    job_id = f"job_wb_proc_{suffix}"

    orders_response = client.post(
        "/api/orders/import",
        headers=headers,
        json={"orders": [_order_payload(first_order_id, material, 100), _order_payload(second_order_id, material, 50)]},
    )
    assert orders_response.status_code == 200
    system_response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": f"ERP Writeback Procurement {suffix}", "system_type": "erp", "enabled": True},
    )
    assert system_response.status_code == 200
    system_id = system_response.json()["id"]
    _sync_inventory(headers, system_id, f"STOCK-WB-PROC-{suffix}", material, available=110, reserved=20)
    writeback_config = client.post(
        f"/api/adapters/systems/{system_id}/configs",
        headers=headers,
        json={
            "adapter_type": "erp_writeback_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "writeback": {"dry_run": True},
                "writeback_status_dictionary": {"material_shortage": "MATERIAL_SHORTAGE"},
            },
        },
    )
    assert writeback_config.status_code == 200
    create_job = client.post(
        "/api/nesting/jobs",
        headers=headers,
        json=_job_payload(job_id, first_order_id, second_order_id, material),
    )
    assert create_job.status_code == 200

    response = client.post(
        f"/api/nesting/jobs/{job_id}/exception-writebacks/run",
        headers=headers,
        json={
            "dry_run": True,
            "include_procurement": True,
            "include_schedule": False,
            "include_delivery": False,
            "safety_stock_rate": 0.1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["action_count"] == 1
    assert payload["writeback_count"] == 1
    action = payload["actions"][0]
    assert action["system_type"] == "erp"
    assert action["target_type"] == "procurement_request"
    assert action["requested_status"] == "material_shortage"
    assert action["writeback_log"]["status"] == "completed"
    request_body = action["writeback_log"]["payload"]["request_body"]
    assert request_body["target_type"] == "procurement_request"
    assert request_body["status"] == "MATERIAL_SHORTAGE"
    assert request_body["payload"]["recommendations"][0]["recommended_purchase_qty"] == 66
