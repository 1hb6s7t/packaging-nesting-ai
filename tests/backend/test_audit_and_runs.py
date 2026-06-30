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


def test_candidate_pool_solver_attempts_are_persisted_with_replay_evidence() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"AUDIT_POOL_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "top_k": 3,
        "sheet": {
            "sheet_id": f"AUDIT_POOL_SHEET_{suffix}",
            "width": 500,
            "height": 400,
            "margin_top": 5,
            "margin_right": 5,
            "margin_bottom": 5,
            "margin_left": 5,
            "gripper_mm": 0,
            "material": "white_card",
            "thickness": "350gsm",
        },
        "solver_config": {
            "solver_name": "RectpackSolver",
            "time_limit_sec": 1,
            "options": {
                "candidate_pool_enabled": True,
                "candidate_pool_solvers": ["RectpackSolver", "PackingSolver"],
                "candidate_pool_seeds": [0, 17],
                "candidate_pool_rotation_policies": ["as_declared", "zero_only"],
            },
        },
        "candidate_items": [
            {
                "item_id": f"item_a_{suffix}",
                "order_id": f"order_a_{suffix}",
                "polygon": {"shape_id": f"shape_a_{suffix}", "outer": [[0, 0], [100, 0], [100, 80], [0, 80]]},
                "priority_score": 0.9,
                "min_gap_mm": 0,
                "bleed_mm": 0,
            },
            {
                "item_id": f"item_b_{suffix}",
                "order_id": f"order_b_{suffix}",
                "polygon": {"shape_id": f"shape_b_{suffix}", "outer": [[0, 0], [90, 0], [90, 70], [0, 70]]},
                "priority_score": 0.7,
                "min_gap_mm": 0,
                "bleed_mm": 0,
            },
        ],
    }
    assert client.post("/api/nesting/jobs", json=job, headers=headers).status_code == 200

    run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)

    assert run_response.status_code == 200
    solutions = run_response.json()["solutions"]
    assert len(solutions) == 3
    assert all(solution["exports"]["solver_run_id"].startswith("run_") for solution in solutions)
    assert all(solution["exports"]["certificate_json"] for solution in solutions)

    runs = client.get(f"/api/nesting/jobs/{job_id}/runs", headers=headers)
    assert runs.status_code == 200
    run_payload = runs.json()
    assert len(run_payload) == 8
    assert {run["solver_name"] for run in run_payload} == {"RectpackSolver", "PackingSolver"}
    assert {run["status"] for run in run_payload} >= {"completed", "failed"}
    assert all(run["config"]["candidate_pool_attempt"]["candidate_pool_enabled"] is True for run in run_payload)
    assert all(run["config"]["input_hash"] for run in run_payload)

    first_solution_run_id = solutions[0]["exports"]["solver_run_id"]
    logs = client.get(f"/api/nesting/runs/{first_solution_run_id}/logs", headers=headers)
    assert logs.status_code == 200
    evidence = next(item["payload"] for item in logs.json() if item["message"] == "solver attempt evidence")
    assert evidence["input_hash"]
    assert evidence["input_snapshot"]["job_id"] == job_id
    assert evidence["attempt_config"]["candidate_pool_enabled"] is True
    assert evidence["certificate"]["solution_id"] == solutions[0]["solution_id"]
    assert evidence["validator_report"]["is_valid"] is True
    assert "stdout" in evidence
    assert "stderr" in evidence
