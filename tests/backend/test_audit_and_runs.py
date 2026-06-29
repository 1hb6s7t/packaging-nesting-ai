from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def test_solver_run_and_operation_log_are_persisted() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"AUDIT_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"AUDIT_SHEET_{suffix}",
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
    run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)
    assert run_response.status_code == 200
    solution = run_response.json()["solutions"][0]
    assert solution["exports"]["solver_run_id"].startswith("run_")

    runs = client.get(f"/api/nesting/jobs/{job_id}/runs", headers=headers)
    assert runs.status_code == 200
    run = runs.json()[0]
    assert run["status"] == "completed"
    assert run["solver_name"] == "RectpackSolver"

    logs = client.get(f"/api/nesting/runs/{run['id']}/logs", headers=headers)
    assert logs.status_code == 200
    messages = [item["message"] for item in logs.json()]
    assert "solver run started" in messages
    assert "solver run completed" in messages

    op_logs = client.get("/api/operation-logs", headers=headers)
    assert op_logs.status_code == 200
    actions = [item["action"] for item in op_logs.json()]
    assert "nesting_job.create" in actions
    assert "nesting_job.run" in actions
