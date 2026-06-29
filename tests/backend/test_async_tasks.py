import time
from pathlib import Path
from uuid import uuid4

import pytest
import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.config import get_settings
from app.db import models as dbm
from app.db.session import SessionLocal, init_db
from app.domain.schemas import TaskAlertRuleOverride
from app.main import app
from app.services import repository
from app.services import workflows
from app.services.alerts import check_work_task_alerts
from app.services.workflows import execute_work_task
from auth_helpers import auth_headers


client = TestClient(app)


def test_async_solver_and_export_tasks_complete() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"ASYNC_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"ASYNC_SHEET_{suffix}",
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

    queued = client.post(f"/api/nesting/jobs/{job_id}/run-async", headers=headers)
    assert queued.status_code == 200
    solve_task = wait_for_task(queued.json()["id"], headers)
    assert solve_task["status"] == "completed"
    assert solve_task["task_type"] == "nesting.solve"
    assert solve_task["progress_percent"] == 100
    assert solve_task["heartbeat_at"] is not None
    solution_id = solve_task["result"]["solution_ids"][0]

    assert client.post(f"/api/solutions/{solution_id}/approval/request", json={}, headers=headers).status_code == 200
    decision = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={
            "decision": "approved",
            "note": "async export approved",
            "confirmation": f"APPROVE {solution_id}",
        },
        headers=headers,
    )
    assert decision.status_code == 200

    export_queued = client.post(
        f"/api/solutions/{solution_id}/export/pdf/async",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=headers,
    )
    assert export_queued.status_code == 200
    export_task = wait_for_task(export_queued.json()["id"], headers)
    assert export_task["status"] == "completed"
    assert export_task["task_type"] == "solution.export"
    assert export_task["result"]["export_type"] == "pdf"
    assert export_task["progress_percent"] == 100
    assert export_task["heartbeat_at"] is not None
    assert Path(export_task["result"]["storage_key"]).exists()

    tasks = client.get("/api/tasks?limit=20", headers=headers)
    assert tasks.status_code == 200
    task_ids = {item["id"] for item in tasks.json()}
    assert solve_task["id"] in task_ids
    assert export_task["id"] in task_ids

    metrics = client.get("/api/tasks/metrics", headers=headers)
    assert metrics.status_code == 200
    metrics_payload = metrics.json()
    assert metrics_payload["total"] >= 2
    assert metrics_payload["completed"] >= 2
    assert metrics_payload["stale_after_sec"] >= 1


def test_task_cancel_and_retry_controls() -> None:
    headers = auth_headers(client)
    init_db()
    with SessionLocal() as db:
        task = repository.create_work_task(
            db,
            task_type="nesting.solve",
            target_type="nesting_job",
            target_id="missing_job_for_retry",
            actor_id="test",
            payload={"job_id": "missing_job_for_retry"},
            max_attempts=2,
            timeout_sec=5,
        )

    missing_cancel_confirmation = client.post(f"/api/tasks/{task.id}/cancel", headers=headers)
    assert missing_cancel_confirmation.status_code == 409

    cancelled = client.post(
        f"/api/tasks/{task.id}/cancel",
        json={"confirmation": f"CANCEL {task.id}"},
        headers=headers,
    )
    assert cancelled.status_code == 200
    cancelled_payload = cancelled.json()
    assert cancelled_payload["status"] == "cancelled"
    assert cancelled_payload["cancel_requested"] is True

    retry_response = client.post(
        f"/api/tasks/{task.id}/retry",
        json={"confirmation": f"RETRY {task.id}"},
        headers=headers,
    )
    assert retry_response.status_code == 200
    retry_payload = retry_response.json()
    assert retry_payload["parent_task_id"] == task.id
    assert retry_payload["attempt"] == 2
    assert retry_payload["max_attempts"] == 2

    retried = wait_for_task(retry_payload["id"], headers)
    assert retried["status"] == "failed"
    assert retried["error"] == "job not found"

    duplicate_retry = client.post(
        f"/api/tasks/{task.id}/retry",
        json={"confirmation": f"RETRY {task.id}"},
        headers=headers,
    )
    assert duplicate_retry.status_code == 409


def test_execute_work_task_marks_timeout_after_runtime() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"TIMEOUT_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"TIMEOUT_SHEET_{suffix}",
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
    with SessionLocal() as db:
        task = repository.create_work_task(
            db,
            task_type="nesting.solve",
            target_type="nesting_job",
            target_id=job_id,
            actor_id="test",
            payload={"job_id": job_id},
            timeout_sec=0,
        )

    result = execute_work_task(task.id)

    assert result.status == "timed_out"
    assert result.result["solution_count"] == 1
    assert "timeout_sec=0" in result.error


def test_execute_work_task_honors_cancel_before_solver_output_is_saved(monkeypatch) -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"CANCEL_DURING_SOLVE_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"CANCEL_SHEET_{suffix}",
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
    with SessionLocal() as db:
        task = repository.create_work_task(
            db,
            task_type="nesting.solve",
            target_type="nesting_job",
            target_id=job_id,
            actor_id="test",
            payload={"job_id": job_id},
            timeout_sec=30,
        )

    original_solve = workflows.orchestrator.solve

    def solve_then_cancel(job_payload):
        solutions = original_solve(job_payload)
        with SessionLocal() as db:
            repository.request_cancel_work_task(db, task.id, actor_id="test")
        return solutions

    monkeypatch.setattr(workflows.orchestrator, "solve", solve_then_cancel)

    result = execute_work_task(task.id)

    assert result.status == "cancelled"
    assert result.cancel_requested is True
    assert result.error == "task cancellation requested"
    with SessionLocal() as db:
        assert repository.list_job_solutions(db, job_id) == []


def test_task_metrics_marks_stale_running_tasks() -> None:
    headers = auth_headers(client)
    init_db()
    with SessionLocal() as db:
        task = repository.create_work_task(
            db,
            task_type="nesting.solve",
            target_type="nesting_job",
            target_id=f"stale_{uuid4().hex[:8]}",
            actor_id="test",
            payload={},
        )
        repository.start_work_task(db, task.id)
        row = db.get(dbm.WorkTask, task.id)
        assert row is not None
        row.heartbeat_at = repository.utc_now().replace(year=2000)
        db.commit()

    metrics = client.get("/api/tasks/metrics", headers=headers)

    assert metrics.status_code == 200
    assert metrics.json()["stale_running"] >= 1


def test_task_alert_check_creates_queue_waterline_notifications() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    init_db()
    with SessionLocal() as db:
        for index in range(2):
            repository.create_work_task(
                db,
                task_type="nesting.solve",
                target_type="nesting_job",
                target_id=f"alert_queue_{suffix}_{index}",
                actor_id="test",
                payload={},
            )

    response = client.post(
        "/api/tasks/alerts/check",
        headers=headers,
        json={
            "active_threshold": 2,
            "queued_threshold": 2,
            "stale_running_threshold": 9999,
            "failure_threshold": 9999,
            "push_external": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "alerting"
    alert_codes = {item["code"] for item in payload["alerts"]}
    assert "work_task.queued_high" in alert_codes
    assert payload["notification_count"] >= 1

    notifications = client.get("/api/notifications?unread_only=true&limit=200", headers=headers)
    assert notifications.status_code == 200
    assert any(item["event_type"] == "work_task.queued_high" for item in notifications.json())


def test_task_alert_check_pushes_external_webhook() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = request.read().decode("utf-8")
        assert "work_task.alert" in body
        assert "work_task.queued_high" in body
        return httpx.Response(202, json={"ok": True})

    settings = Settings(EXTERNAL_ALERT_WEBHOOK_URL="https://alerts.example.test/hook")
    with SessionLocal() as db:
        repository.create_work_task(
            db,
            task_type="nesting.solve",
            target_type="nesting_job",
            target_id=f"alert_webhook_{uuid4().hex[:8]}",
            actor_id="test",
            payload={},
        )
        result = check_work_task_alerts(
            db,
            settings=settings,
            override=TaskAlertRuleOverride(
                active_threshold=9999,
                queued_threshold=1,
                stale_running_threshold=9999,
                failure_threshold=9999,
                notify=False,
                push_external=True,
            ),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "alerting"
    assert result.external_push == {"status": "sent", "http_status_code": 202}
    assert len(requests) == 1


def test_celery_worker_uses_configured_time_limits() -> None:
    pytest.importorskip("celery")
    from app.workers.celery_app import celery_app

    settings = get_settings()

    assert celery_app.conf.task_time_limit == settings.task_hard_time_limit_sec
    assert celery_app.conf.task_soft_time_limit == min(
        settings.task_soft_time_limit_sec,
        settings.task_hard_time_limit_sec,
    )
    assert celery_app.conf.worker_prefetch_multiplier == max(1, settings.task_worker_prefetch_multiplier)


def wait_for_task(task_id: str, headers: dict[str, str]) -> dict:
    for _ in range(20):
        response = client.get(f"/api/tasks/{task_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload["status"] in {"completed", "failed", "cancelled", "timed_out"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish")
