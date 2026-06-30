from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def _headers_for_permissions(
    admin_headers: dict[str, str],
    suffix: str,
    role_label: str,
    permission_codes: list[str],
) -> dict[str, str]:
    role_response = client.post(
        "/api/rbac/roles",
        json={
            "name": f"{role_label}_{suffix}",
            "description": f"Test role for {role_label}",
            "permission_codes": permission_codes,
        },
        headers=admin_headers,
    )
    assert role_response.status_code == 200
    role_id = role_response.json()["id"]

    email = f"{role_label}_{suffix}@example.com"
    user_response = client.post(
        "/api/rbac/users",
        json={
            "email": email,
            "display_name": role_label.replace("_", " ").title(),
            "password": "Strong123!45",
            "role_ids": [role_id],
        },
        headers=admin_headers,
    )
    assert user_response.status_code == 200
    login = client.post("/api/auth/login", json={"email": email, "password": "Strong123!45"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _create_parsed_ai_batch(headers: dict[str, str], suffix: str) -> str:
    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="70">'
        b'<rect id="cut" x="0" y="0" width="90" height="70"/></svg>'
    )
    upload = client.post(
        "/api/batch-artworks/upload",
        files=[("files", (f"ai-batch-{suffix}.svg", svg_content, "image/svg+xml"))],
        data={"source_name": f"ai-batch-tools-{suffix}"},
        headers=headers,
    )
    assert upload.status_code == 200
    batch_id = upload.json()["batch"]["batch_id"]
    assert client.post(f"/api/batch-artworks/{batch_id}/preflight", headers=headers).status_code == 200
    assert client.post(f"/api/batch-artworks/{batch_id}/parse", headers=headers).status_code == 200
    return batch_id


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


def test_ai_batch_tools_run_top3_pipeline_and_gate_write_permissions() -> None:
    suffix = uuid4().hex[:8]
    admin_headers = auth_headers(client)
    batch_id = _create_parsed_ai_batch(admin_headers, suffix)
    ai_only_headers = _headers_for_permissions(admin_headers, suffix, "ai_batch_reader", ["ai:use"])

    tools_response = client.get("/api/ai/tools", headers=ai_only_headers)
    assert tools_response.status_code == 200
    tools = {tool["name"]: tool for tool in tools_response.json()}
    assert tools["get_batch_summary"]["required_permissions"] == ["ai:use"]
    assert tools["get_batch_features"]["read_only"] is True
    assert tools["create_batch_layout_job"]["required_permissions"] == ["ai:use", "batch:write"]
    assert tools["create_batch_layout_job"]["mutates"] is True
    assert tools["run_batch_layout_job"]["required_permissions"] == ["ai:use", "batch:write"]
    assert tools["compare_batch_top3"]["read_only"] is True
    assert tools["generate_batch_report"]["read_only"] is True

    denied_create = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "create_batch_layout_job", "arguments": {"batch_id": batch_id}},
        headers=ai_only_headers,
    )
    assert denied_create.status_code == 200
    denied_payload = denied_create.json()
    assert denied_payload["status"] == "failed"
    assert "batch:write" in denied_payload["message"]

    summary_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "get_batch_summary", "arguments": {"batch_id": batch_id}},
        headers=ai_only_headers,
    ).json()
    assert summary_payload["status"] == "completed"
    assert summary_payload["result"]["item_count"] == 1
    assert summary_payload["result"]["status_counts"]["parsed"] == 1
    assert summary_payload["safety"]["ai_generated_coordinates"] is False
    assert summary_payload["safety"]["coordinates_source"] == "stored_features_only"

    features_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "get_batch_features", "arguments": {"batch_id": batch_id, "limit": 500}},
        headers=ai_only_headers,
    ).json()
    assert features_payload["status"] == "completed"
    assert features_payload["result"]["count"] == 1
    feature = features_payload["result"]["items"][0]["feature"]
    assert feature["bbox_width"] == 90
    assert feature["bbox_height"] == 70
    assert "bbox" not in feature

    create_payload = client.post(
        "/api/ai/tools/execute",
        json={
            "tool_name": "create_batch_layout_job",
            "arguments": {
                "batch_id": batch_id,
                "moq_per_item": 1000,
                "top_k": 3,
                "sheet_parent": {"parent_id": f"PARENT_AI_{suffix}", "width": 787, "height": 1092},
            },
        },
        headers=admin_headers,
    ).json()
    assert create_payload["status"] == "completed"
    assert create_payload["safety"]["coordinates_source"] == "none_job_contract_only"
    job_id = create_payload["result"]["job"]["job_id"]

    run_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "run_batch_layout_job", "arguments": {"job_id": job_id}},
        headers=admin_headers,
    ).json()
    assert run_payload["status"] == "completed"
    assert run_payload["result"]["summary"]["plan_count"] == 3
    assert run_payload["safety"]["coordinates_source"] == "backend_batch_layout_services"
    assert run_payload["safety"]["production_export_allowed"] is False
    assert all(plan["hard_rule_pass"] for plan in run_payload["result"]["plans"])

    compare_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "compare_batch_top3", "arguments": {"job_id": job_id}},
        headers=ai_only_headers,
    ).json()
    assert compare_payload["status"] == "completed"
    assert compare_payload["result"]["plan_count"] == 3
    assert {plan["intent"] for plan in compare_payload["result"]["plans"]} == {
        "highest_utilization",
        "balanced_risk",
        "fastest_production",
    }
    assert all(plan["diversity_score"] > 0 for plan in compare_payload["result"]["plans"])

    report_payload = client.post(
        "/api/ai/tools/execute",
        json={"tool_name": "generate_batch_report", "arguments": {"job_id": job_id}},
        headers=ai_only_headers,
    ).json()
    assert report_payload["status"] == "completed"
    report = report_payload["result"]["report"]
    assert report["plan_count"] == 3
    assert report["legal_plan_count"] == 3
    assert report["safety"]["requires_approval_before_export"] is True
    assert report["safety"]["production_export_allowed"] is False

    chat_payload = client.post(
        "/api/ai/chat",
        json={"message": "compare batch top3 report"},
        headers=ai_only_headers,
    ).json()
    planned_tools = {item["tool_name"] for item in chat_payload["recommended_tool_calls"]}
    assert "compare_batch_top3" in planned_tools
    assert "generate_batch_report" in planned_tools
    assert "get_batch_summary" in planned_tools
