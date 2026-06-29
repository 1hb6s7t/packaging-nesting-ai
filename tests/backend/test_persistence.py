from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.store import store
from auth_helpers import auth_headers


client = TestClient(app)


def test_order_and_sheet_survive_memory_clear() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    order_id = f"PERSIST_ORDER_{suffix}"
    sheet_id = f"PERSIST_SHEET_{suffix}"
    order_payload = {
        "orders": [
            {
                "order_id": order_id,
                "product_name": "持久化测试彩盒",
                "category": "box",
                "material": "white_card",
                "thickness": "350gsm",
                "quantity": 100,
            }
        ]
    }
    sheet_payload = {
        "sheet_id": sheet_id,
        "width": 500,
        "height": 400,
        "material": "white_card",
        "thickness": "350gsm",
    }

    assert client.post("/api/orders/import", json=order_payload, headers=headers).status_code == 200
    assert client.post("/api/sheets", json=sheet_payload, headers=headers).status_code == 200
    store.orders.clear()
    store.sheets.clear()

    assert client.get(f"/api/orders/{order_id}", headers=headers).json()["order_id"] == order_id
    assert client.get(f"/api/sheets/{sheet_id}", headers=headers).json()["sheet_id"] == sheet_id


def test_artwork_polygon_preview_survives_memory_clear() -> None:
    headers = auth_headers(client)
    content = b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><path id="cut" d="M0 0 L120 0 L120 80 L0 80 Z"/></svg>'
    upload = client.post(
        "/api/artworks/upload",
        files={"file": ("persist-box.svg", content, "image/svg+xml")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    parsed = client.post(f"/api/artworks/{artwork_id}/parse-polygon", headers=headers)
    assert parsed.status_code == 200
    store.artworks.clear()
    store.preflight_reports.clear()
    store.polygons.clear()

    meta = client.get(f"/api/artworks/{artwork_id}", headers=headers)
    assert meta.status_code == 200
    preview = client.get(f"/api/artworks/{artwork_id}/preview", headers=headers)
    assert preview.status_code == 200
    assert "image/svg+xml" in preview.headers["content-type"]


def test_job_solution_report_survives_memory_clear() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"PERSIST_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"PERSIST_JOB_SHEET_{suffix}",
            "width": 500,
            "height": 400,
            "margin_top": 5,
            "margin_right": 5,
            "margin_bottom": 5,
            "margin_left": 5,
            "gripper_mm": 10,
            "material": "white_card",
            "thickness": "350gsm",
        },
        "candidate_items": [
            {
                "item_id": f"item_{suffix}",
                "order_id": f"order_{suffix}",
                "polygon": {"shape_id": f"shape_{suffix}", "outer": [[0, 0], [100, 0], [100, 80], [0, 80]]},
                "priority_score": 0.9,
            }
        ],
    }
    assert client.post("/api/nesting/jobs", json=job, headers=headers).status_code == 200
    run = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)
    assert run.status_code == 200
    solution_id = run.json()["solutions"][0]["solution_id"]
    store.jobs.clear()
    store.solutions.clear()
    store.job_solutions.clear()

    assert client.get(f"/api/nesting/jobs/{job_id}", headers=headers).json()["job_id"] == job_id
    report = client.get(f"/api/solutions/{solution_id}/report", headers=headers)
    assert report.status_code == 200
    assert report.json()["solution_id"] == solution_id
