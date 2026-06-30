from uuid import uuid4
import time

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.services import benchmarks, repository
from app.services.workflows import execute_work_task
from auth_helpers import auth_headers


client = TestClient(app)


def _benchmark_case(case_id: str, *, planning_mode: str = "single_sheet", quantity: int = 1) -> dict:
    suffix = case_id[-8:]
    return {
        "case_id": case_id,
        "name": f"Benchmark {suffix}",
        "planning_mode": planning_mode,
        "sheet": {
            "sheet_id": f"BENCH_SHEET_{suffix}",
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
        "items": [
            {
                "item_id": f"item_{suffix}",
                "order_id": f"order_{suffix}",
                "polygon": {"shape_id": f"shape_{suffix}", "outer": [[0, 0], [100, 0], [100, 80], [0, 80]]},
                "quantity": quantity,
                "priority_score": 0.9,
            }
        ],
        "baseline_utilization_rate": 0.1,
    }


def test_benchmark_cases_and_runs_are_persisted() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    case_id = f"BENCH_{suffix}"
    payload = _benchmark_case(case_id)

    create_response = client.post("/api/benchmark/cases", headers=headers, json=payload)
    assert create_response.status_code == 200
    saved = create_response.json()
    assert saved["case_id"] == case_id
    assert saved["source"] == "manual"

    list_response = client.get("/api/benchmark/cases", headers=headers)
    assert list_response.status_code == 200
    assert any(row["case_id"] == case_id for row in list_response.json())

    run_response = client.post(f"/api/benchmark/cases/{case_id}/runs", headers=headers)
    assert run_response.status_code == 200
    run = run_response.json()
    assert run["run_id"].startswith("brun_")
    assert run["case_id"] == case_id
    assert run["solver_name"] == "RectpackSolver"
    assert run["planning_mode"] == "single_sheet"
    assert run["valid"] is True
    assert run["hard_rule_pass"] is True
    assert run["requested_units"] == 1
    assert run["produced_units"] == 1
    assert run["shortage_units"] == 0
    assert run["units_per_sheet"] == 1
    assert run["sheets_used"] == 1
    assert run["quantity_fulfillment_rate"] == 1
    assert run["export_ok"] is True
    assert run["case_score"] > 0
    assert run["baseline_delta_utilization_rate"] is not None
    assert run["metrics"]["solver_coordinates_source"] == "backend_solver"

    runs_response = client.get(f"/api/benchmark/runs?case_id={case_id}", headers=headers)
    assert runs_response.status_code == 200
    assert any(row["run_id"] == run["run_id"] for row in runs_response.json())

    ad_hoc_id = f"BENCH_ADHOC_{suffix}"
    ad_hoc_response = client.post("/api/benchmark/run", headers=headers, json=_benchmark_case(ad_hoc_id))
    assert ad_hoc_response.status_code == 200
    assert ad_hoc_response.json()["case_id"] == ad_hoc_id

    ad_hoc_case = client.get(f"/api/benchmark/cases/{ad_hoc_id}", headers=headers)
    assert ad_hoc_case.status_code == 200
    assert ad_hoc_case.json()["source"] == "ad_hoc"


def test_benchmark_case_can_run_as_background_task() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    case_id = f"BENCH_ASYNC_{suffix}"
    assert client.post("/api/benchmark/cases", headers=headers, json=_benchmark_case(case_id)).status_code == 200

    queued = client.post(f"/api/benchmark/cases/{case_id}/runs/async", headers=headers)
    assert queued.status_code == 200
    task = _wait_for_task(queued.json()["id"], headers)

    assert task["status"] == "completed"
    assert task["task_type"] == "benchmark.run"
    assert task["target_type"] == "benchmark_case"
    assert task["target_id"] == case_id
    assert task["result"]["case_id"] == case_id
    assert task["result"]["run_id"].startswith("brun_")
    assert task["result"]["valid"] is True
    assert task["result"]["hard_rule_pass"] is True
    assert task["result"]["quantity_fulfillment_rate"] == 1

    runs_response = client.get(f"/api/benchmark/runs?case_id={case_id}", headers=headers)
    assert runs_response.status_code == 200
    assert any(row["run_id"] == task["result"]["run_id"] for row in runs_response.json())


def test_benchmark_pattern_case_records_quantity_metrics() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    case_id = f"BENCH_PATTERN_{suffix}"
    payload = _benchmark_case(case_id, planning_mode="pattern", quantity=100)

    create_response = client.post("/api/benchmark/cases", headers=headers, json=payload)
    assert create_response.status_code == 200
    assert create_response.json()["planning_mode"] == "pattern"

    run_response = client.post(f"/api/benchmark/cases/{case_id}/runs", headers=headers)
    assert run_response.status_code == 200
    run = run_response.json()
    assert run["planning_mode"] == "pattern"
    assert run["requested_units"] == 100
    assert run["produced_units"] >= 100
    assert run["shortage_units"] == 0
    assert run["overproduction_units"] == run["produced_units"] - 100
    assert run["units_per_sheet"] > 1
    assert run["sheets_used"] > 1
    assert run["quantity_fulfillment_rate"] == 1
    assert run["hard_rule_pass"] is True
    assert run["export_ok"] is True
    assert run["p95_runtime_ms"] is not None
    assert run["metrics"]["requested_units_by_item"][f"item_{suffix}"] == 100


def test_cancelled_benchmark_task_does_not_write_run(monkeypatch) -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    case_id = f"BENCH_CANCEL_{suffix}"
    assert client.post("/api/benchmark/cases", headers=headers, json=_benchmark_case(case_id)).status_code == 200
    with SessionLocal() as db:
        task = repository.create_work_task(
            db,
            task_type="benchmark.run",
            target_type="benchmark_case",
            target_id=case_id,
            actor_id="test",
            payload={"case_id": case_id, "solver_name": "RectpackSolver"},
            timeout_sec=30,
        )

    original_plan_batch = benchmarks.plan_batch

    def cancelling_plan_batch(*args, **kwargs):
        result = original_plan_batch(*args, **kwargs)
        with SessionLocal() as db:
            repository.request_cancel_work_task(db, task.id, actor_id="test")
        return result

    monkeypatch.setattr(benchmarks, "plan_batch", cancelling_plan_batch)

    result = execute_work_task(task.id)

    assert result.status == "cancelled"
    assert result.error == "task cancellation requested"
    assert client.get(f"/api/benchmark/runs?case_id={case_id}", headers=headers).json() == []


def _wait_for_task(task_id: str, headers: dict[str, str]) -> dict:
    for _ in range(20):
        response = client.get(f"/api/tasks/{task_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "timed_out"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish")
