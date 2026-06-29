from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def _order(order_id: str, material: str, quantity: int) -> dict:
    return {
        "order_id": order_id,
        "product_name": f"Production Box {order_id}",
        "quantity": quantity,
        "material": material,
        "thickness": "350gsm",
    }


def _job(job_id: str, first_order_id: str, second_order_id: str, material: str) -> dict:
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
                "polygon": {"shape_id": f"{job_id}_shape_1", "outer": [[0, 0], [90, 0], [90, 70], [0, 70]]},
            },
            {
                "item_id": f"{job_id}_item_2",
                "order_id": second_order_id,
                "polygon": {"shape_id": f"{job_id}_shape_2", "outer": [[0, 0], [70, 0], [70, 50], [0, 50]]},
            },
        ],
    }


def _create_system(headers: dict[str, str], name: str, system_type: str) -> str:
    response = client.post(
        "/api/adapters/systems",
        headers=headers,
        json={"name": name, "system_type": system_type, "enabled": True},
    )
    assert response.status_code == 200
    return response.json()["id"]


def _sync_inventory(headers: dict[str, str], system_id: str, stock_id: str, material: str) -> None:
    response = client.post(
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
                        "available": 500,
                        "reserved": 0,
                        "unit": "sheet",
                    }
                ],
            },
        },
    )
    assert response.status_code == 200
    sync = client.post("/api/adapters/erp/sync?dry_run=false", headers=headers)
    assert sync.status_code == 200
    assert sync.json()["payload"]["domain_target"] == "inventory_snapshot"


def _sync_schedule(
    headers: dict[str, str],
    system_id: str,
    suffix: str,
    job_id: str,
    first_order_id: str,
    second_order_id: str,
    first_state: str = "READY_TO_RUN",
) -> None:
    response = client.post(
        f"/api/adapters/systems/{system_id}/configs",
        headers=headers,
        json={
            "adapter_type": "mes_schedule_api",
            "is_active": True,
            "config": {
                "mode": "mock",
                "dry_run": True,
                "external_id_path": "jobNo",
                "status_path": "state",
                "domain_target": "production_schedule",
                "field_mapping": {
                    "order_id": "orderNo",
                    "job_id": "jobId",
                    "line_code": "line",
                    "machine_code": "press",
                    "planned_start_at": "start",
                    "planned_end_at": "end",
                    "quantity": "qty",
                },
                "status_dictionary": {"READY_TO_RUN": "scheduled", "HOLD": "blocked"},
                "sample_records": [
                    {
                        "jobNo": f"MES-PROD-{suffix}-001",
                        "orderNo": first_order_id,
                        "jobId": job_id,
                        "state": first_state,
                        "line": "L1",
                        "press": "PRESS-1",
                        "start": "2026-07-01T08:00:00+08:00",
                        "end": "2026-07-01T10:00:00+08:00",
                        "qty": 100,
                    },
                    {
                        "jobNo": f"MES-PROD-{suffix}-002",
                        "orderNo": second_order_id,
                        "jobId": job_id,
                        "state": "READY_TO_RUN",
                        "line": "L1",
                        "press": "PRESS-1",
                        "start": "2026-07-01T10:00:00+08:00",
                        "end": "2026-07-01T12:00:00+08:00",
                        "qty": 50,
                    },
                ],
            },
        },
    )
    assert response.status_code == 200
    sync = client.post("/api/adapters/mes/sync?dry_run=false", headers=headers)
    assert sync.status_code == 200
    assert sync.json()["payload"]["domain_target"] == "production_schedule"


def _sync_partial_delivery(headers: dict[str, str], system_id: str, suffix: str, first_order_id: str) -> None:
    response = client.post(
        f"/api/adapters/systems/{system_id}/configs",
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
                        "deliveryId": f"DELIV-PROD-{suffix}-001",
                        "orderNo": first_order_id,
                        "shipmentNo": f"SHP-PROD-{suffix}",
                        "state": "SIGNED",
                        "carrier": "SF",
                        "tracking": "SF123456",
                        "deliveredAt": "2026-07-03T16:30:00+08:00",
                        "qty": 100,
                    }
                ],
            },
        },
    )
    assert response.status_code == 200
    sync = client.post("/api/adapters/erp/sync?dry_run=false", headers=headers)
    assert sync.status_code == 200
    assert sync.json()["payload"]["domain_target"] == "delivery_confirmation"


def test_job_production_readiness_combines_material_schedule_and_delivery() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    material = f"prod_mat_{suffix}"
    first_order_id = f"PROD-{suffix}-001"
    second_order_id = f"PROD-{suffix}-002"
    job_id = f"job_prod_{suffix}"

    orders = client.post(
        "/api/orders/import",
        headers=headers,
        json={"orders": [_order(first_order_id, material, 100), _order(second_order_id, material, 50)]},
    )
    assert orders.status_code == 200
    created_job = client.post("/api/nesting/jobs", headers=headers, json=_job(job_id, first_order_id, second_order_id, material))
    assert created_job.status_code == 200

    erp_system_id = _create_system(headers, f"ERP Production {suffix}", "erp")
    mes_system_id = _create_system(headers, f"MES Production {suffix}", "mes")
    _sync_inventory(headers, erp_system_id, f"STOCK-PROD-{suffix}", material)
    _sync_schedule(headers, mes_system_id, suffix, job_id, first_order_id, second_order_id)
    _sync_partial_delivery(headers, erp_system_id, suffix, first_order_id)

    ready_response = client.get(f"/api/nesting/jobs/{job_id}/production-readiness", headers=headers)
    assert ready_response.status_code == 200
    ready = ready_response.json()
    assert ready["overall_status"] == "ready"
    assert ready["material_status"] == "ready"
    assert ready["schedule_status"] == "scheduled"
    assert ready["delivery_status"] == "partial"
    assert ready["schedule_source_count"] == 2
    assert ready["delivery_source_count"] == 1
    assert {item["status"] for item in ready["schedule_items"]} == {"scheduled"}
    assert {item["status"] for item in ready["delivery_items"]} == {"delivered", "missing"}

    _sync_schedule(headers, mes_system_id, suffix, job_id, first_order_id, second_order_id, first_state="HOLD")

    blocked_response = client.get(f"/api/nesting/jobs/{job_id}/production-readiness", headers=headers)
    assert blocked_response.status_code == 200
    blocked = blocked_response.json()
    assert blocked["overall_status"] == "blocked"
    assert blocked["schedule_status"] == "blocked"
    assert any(item["order_id"] == first_order_id and item["status"] == "blocked" for item in blocked["schedule_items"])


def test_job_production_alert_check_creates_deduped_notifications() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    material = f"alert_prod_mat_{suffix}"
    first_order_id = f"ALERT-PROD-{suffix}-001"
    second_order_id = f"ALERT-PROD-{suffix}-002"
    job_id = f"job_alert_prod_{suffix}"

    orders = client.post(
        "/api/orders/import",
        headers=headers,
        json={"orders": [_order(first_order_id, material, 100), _order(second_order_id, material, 50)]},
    )
    assert orders.status_code == 200
    created_job = client.post("/api/nesting/jobs", headers=headers, json=_job(job_id, first_order_id, second_order_id, material))
    assert created_job.status_code == 200

    erp_system_id = _create_system(headers, f"ERP Alert Production {suffix}", "erp")
    mes_system_id = _create_system(headers, f"MES Alert Production {suffix}", "mes")
    _sync_inventory(headers, erp_system_id, f"STOCK-ALERT-PROD-{suffix}", material)
    _sync_schedule(headers, mes_system_id, suffix, job_id, first_order_id, second_order_id, first_state="HOLD")

    first_alert = client.post(
        f"/api/nesting/jobs/{job_id}/production-alerts/check",
        headers=headers,
        json={"notify": True, "dedupe_minutes": 60},
    )
    assert first_alert.status_code == 200
    first_payload = first_alert.json()
    assert first_payload["status"] == "alerting"
    assert first_payload["notification_count"] >= 1
    alert_codes = {item["code"] for item in first_payload["alerts"]}
    assert "production.schedule_blocked" in alert_codes
    assert "production.delivery_incomplete" in alert_codes

    notifications = client.get("/api/notifications?unread_only=true&limit=300", headers=headers)
    assert notifications.status_code == 200
    assert any(
        item["event_type"] == "production.schedule_blocked" and item["target_id"] == job_id
        for item in notifications.json()
    )

    second_alert = client.post(
        f"/api/nesting/jobs/{job_id}/production-alerts/check",
        headers=headers,
        json={"notify": True, "dedupe_minutes": 60},
    )
    assert second_alert.status_code == 200
    assert second_alert.json()["status"] == "alerting"
    assert second_alert.json()["notification_count"] == 0
