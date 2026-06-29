import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db import models as dbm
from app.db.session import SessionLocal
from app.main import app
from app.services import repository
from auth_helpers import auth_headers


client = TestClient(app)


def test_manual_scheduled_maintenance_archives_exports_and_marks_conversion_overdue() -> None:
    headers = auth_headers(client)
    solution_id, export_id = _create_approved_pdf_export(headers)
    conversion_job_id = _create_overdue_conversion_job(headers)

    with SessionLocal() as db:
        export = db.get(dbm.SolutionExport, export_id)
        assert export is not None
        export.retention_until = repository.utc_now() - timedelta(days=1)
        db.commit()

    response = client.post(
        "/api/tasks/maintenance/run",
        headers=headers,
        json={
            "archive_expired_exports": True,
            "archive_dry_run": False,
            "conversion_sla_check": True,
            "conversion_sla_notify": False,
            "task_alert_check": True,
            "task_alert_notify": False,
            "task_alert_push_external": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "attention"
    assert payload["export_archive"]["archived_count"] >= 1
    assert any(item["id"] == export_id for item in payload["export_archive"]["archived_exports"])
    assert payload["conversion_sla"]["status"] == "overdue"
    assert any(item["id"] == conversion_job_id for item in payload["conversion_sla"]["overdue_jobs"])
    assert payload["task_alerts"]["status"] in {"ok", "alerting"}

    with SessionLocal() as db:
        export = db.get(dbm.SolutionExport, export_id)
        conversion_job = db.get(dbm.FileConversionJob, conversion_job_id)
        assert export is not None
        assert conversion_job is not None
        assert export.lifecycle_status == "archived"
        assert conversion_job.status == "overdue"


def test_maintenance_schedule_endpoint_and_celery_beat_config() -> None:
    headers = auth_headers(client)

    schedule = client.get("/api/tasks/maintenance/schedule", headers=headers)

    assert schedule.status_code == 200
    assert {"enabled", "interval_minutes", "checks"} <= set(schedule.json())

    pytest.importorskip("celery")
    from app.workers.celery_app import build_maintenance_beat_schedule

    disabled = build_maintenance_beat_schedule(Settings(MAINTENANCE_SCHEDULER_ENABLED=False))
    enabled = build_maintenance_beat_schedule(
        Settings(MAINTENANCE_SCHEDULER_ENABLED=True, MAINTENANCE_INTERVAL_MINUTES=2)
    )
    assert disabled == {}
    assert enabled["packaging-nesting-scheduled-maintenance"]["task"] == "packaging_nesting.enqueue_scheduled_maintenance"
    assert enabled["packaging-nesting-scheduled-maintenance"]["schedule"] == 120


def _create_approved_pdf_export(headers: dict[str, str]) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    job_id = f"MAINT_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"MAINT_SHEET_{suffix}",
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
    solution_id = run_response.json()["solutions"][0]["solution_id"]
    request_approval = client.post(
        f"/api/solutions/{solution_id}/approval/request",
        json={"note": "ready for scheduled maintenance test"},
        headers=headers,
    )
    assert request_approval.status_code == 200
    approval = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={"decision": "approved", "note": "approved", "confirmation": f"APPROVE {solution_id}"},
        headers=headers,
    )
    assert approval.status_code == 200
    export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=headers,
    )
    assert export.status_code == 200
    storage_key = export.json()["storage_key"]
    assert Path(storage_key).exists()
    return solution_id, export.json()["id"]


def _create_overdue_conversion_job(headers: dict[str, str]) -> str:
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"maintenance-{suffix}.ai", b"%!PS-Adobe-3.0", "application/postscript")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job_id = created.json()["id"]
    with SessionLocal() as db:
        repository.set_file_conversion_job_status(
            db,
            job_id,
            status="queued",
            log="queued for scheduled maintenance test",
            metadata_update={
                "sla_due_at": "2000-01-01T00:00:00",
                "callback_token_hash": hashlib.sha256(b"maintenance-token").hexdigest(),
                "callback_token_tail": "token",
            },
        )
    return job_id
