from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def test_ai_tool_execution_requires_auth_and_searches_orders() -> None:
    suffix = uuid4().hex[:8]
    headers = auth_headers(client)
    order_id = f"O-AI-{suffix}"
    product_name = f"AI-PROD-{suffix}"
    order_payload = {
        "orders": [
            {
                "order_id": order_id,
                "customer_name": "AI Tool Customer",
                "product_name": product_name,
                "quantity": 120,
                "material": "white_card",
                "thickness": "350gsm",
            }
        ]
    }
    assert client.post("/api/orders/import", json=order_payload, headers=headers).status_code == 200

    missing_tools_auth = client.get("/api/ai/tools")
    assert missing_tools_auth.status_code == 401
    tools_response = client.get("/api/ai/tools", headers=headers)
    assert tools_response.status_code == 200
    tools = {tool["name"]: tool for tool in tools_response.json()}
    assert tools["search_orders"]["schema_version"] == 1
    assert tools["search_orders"]["required_permissions"] == ["ai:use"]
    assert tools["search_orders"]["read_only"] is True
    assert tools["run_solver"]["mutates"] is True
    assert tools["export_pdf"]["blocked_in_production"] is True
    assert tools["export_pdf"]["requires_human_approval"] is True

    missing_auth = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "search_orders", "arguments": {"query": product_name}},
    )
    assert missing_auth.status_code == 401

    response = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "search_orders", "arguments": {"query": product_name, "material": "white_card"}},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["safety"]["ai_generated_coordinates"] is False
    assert payload["result"]["orders"][0]["order_id"] == order_id

    logs = client.get("/api/operation-logs?limit=20", headers=headers).json()
    assert any(
        log["action"] == "ai.tool.execute"
        and log["target_id"] == "search_orders"
        and log["payload"]["arguments"]["query"] == product_name
        and log["payload"]["status"] == "completed"
        for log in logs
    )


def test_ai_solver_compare_report_and_blocked_export() -> None:
    suffix = uuid4().hex[:8]
    headers = auth_headers(client)
    sheet = {
        "sheet_id": f"sheet_ai_{suffix}",
        "width": 360,
        "height": 260,
        "margin_top": 5,
        "margin_right": 5,
        "margin_bottom": 5,
        "margin_left": 5,
        "gripper_mm": 8,
        "material": "white_card",
        "thickness": "350gsm",
        "cost_per_sheet": 4.2,
    }
    assert client.post("/api/sheets", json=sheet, headers=headers).status_code == 200
    job_id = f"job_ai_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": sheet,
        "candidate_items": [
            {
                "item_id": f"item_ai_{suffix}_1",
                "order_id": f"O-AI-JOB-{suffix}-1",
                "polygon": {
                    "shape_id": f"shape_ai_{suffix}_1",
                    "outer": [[0, 0], [80, 0], [80, 60], [0, 60]],
                },
                "priority_score": 0.8,
            },
            {
                "item_id": f"item_ai_{suffix}_2",
                "order_id": f"O-AI-JOB-{suffix}-2",
                "polygon": {
                    "shape_id": f"shape_ai_{suffix}_2",
                    "outer": [[0, 0], [70, 0], [70, 50], [0, 50]],
                },
                "priority_score": 0.6,
            },
        ],
    }
    assert client.post("/api/nesting/jobs", json=job, headers=headers).status_code == 200

    run_response = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "run_solver", "arguments": {"job_id": job_id}},
        headers=headers,
    )
    assert run_response.status_code == 200
    run_payload = run_response.json()
    assert run_payload["status"] == "completed"
    assert run_payload["safety"]["coordinates_source"] == "backend_solver"
    solution_id = run_payload["result"]["solutions"][0]["solution_id"]

    compare_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "compare_solutions", "arguments": {"job_id": job_id}},
        headers=headers,
    ).json()
    assert compare_payload["status"] == "completed"
    assert compare_payload["result"]["solution_count"] >= 1

    report_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "generate_report", "arguments": {"solution_id": solution_id}},
        headers=headers,
    ).json()
    assert report_payload["status"] == "completed"
    assert report_payload["result"]["report"]["solution_id"] == solution_id

    export_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "export_pdf", "arguments": {"solution_id": solution_id}},
        headers=headers,
    ).json()
    assert export_payload["status"] == "blocked"
    assert export_payload["safety"]["production_export_allowed"] is False
