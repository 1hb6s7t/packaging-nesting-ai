from datetime import timedelta
from uuid import uuid4
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import models as dbm
from app.db.session import SessionLocal
from app.main import app
from app.services import repository
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


def test_solution_approval_gates_production_export() -> None:
    admin_headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    job_id = f"APPROVAL_JOB_{suffix}"
    job = {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"APPROVAL_SHEET_{suffix}",
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
    assert client.post("/api/nesting/jobs", json=job, headers=admin_headers).status_code == 200
    run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=admin_headers)
    assert run_response.status_code == 200
    solution_id = run_response.json()["solutions"][0]["solution_id"]

    pre_export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=admin_headers,
    )
    assert pre_export.status_code == 409

    writer_headers = _headers_for_permissions(admin_headers, suffix, "solution_submitter", ["solutions:write"])

    request_response = client.post(
        f"/api/solutions/{solution_id}/approval/request",
        json={"note": "Ready for production review"},
        headers=writer_headers,
    )
    assert request_response.status_code == 200
    approval = request_response.json()
    assert approval["status"] == "pending"
    assert approval["request_note"] == "Ready for production review"

    writer_decision = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={"decision": "approved", "note": "not allowed", "confirmation": f"APPROVE {solution_id}"},
        headers=writer_headers,
    )
    assert writer_decision.status_code == 403

    missing_decision_confirmation = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={"decision": "approved", "note": "missing confirmation"},
        headers=admin_headers,
    )
    assert missing_decision_confirmation.status_code == 409

    decision_response = client.post(
        f"/api/solutions/{solution_id}/approval/decision",
        json={
            "decision": "approved",
            "note": "Approved for export",
            "confirmation": f"APPROVE {solution_id}",
        },
        headers=admin_headers,
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "approved"

    solution = client.get(f"/api/solutions/{solution_id}", headers=admin_headers)
    assert solution.status_code == 200
    assert solution.json()["status"] == "approved"

    writer_export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=writer_headers,
    )
    assert writer_export.status_code == 403
    assert client.get(f"/api/solutions/{solution_id}/exports", headers=writer_headers).status_code == 403
    assert client.get(f"/api/solutions/{solution_id}/exports/manifest", headers=writer_headers).status_code == 403
    assert (
        client.post(
            f"/api/solutions/{solution_id}/exports/recovery-drill",
            json={"include_archive_dry_run": True},
            headers=writer_headers,
        ).status_code
        == 403
    )

    export_headers = _headers_for_permissions(admin_headers, suffix, "solution_exporter", ["solutions:export"])
    archive_headers = _headers_for_permissions(admin_headers, suffix, "solution_archivist", ["solutions:archive"])
    assert client.get(f"/api/solutions/{solution_id}/exports", headers=export_headers).status_code == 200
    assert client.get(f"/api/solutions/{solution_id}/exports", headers=archive_headers).status_code == 200
    assert client.get(f"/api/solutions/{solution_id}/exports/manifest", headers=export_headers).status_code == 403
    archive_forbidden_export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=archive_headers,
    )
    assert archive_forbidden_export.status_code == 403

    missing_export_confirmation = client.post(f"/api/solutions/{solution_id}/export/pdf", headers=export_headers)
    assert missing_export_confirmation.status_code == 409

    pdf_export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=export_headers,
    )
    assert pdf_export.status_code == 200
    pdf_payload = pdf_export.json()
    assert pdf_payload["status"] == "ready"
    assert pdf_payload["export_type"] == "pdf"
    assert pdf_payload["version"] == 1
    assert pdf_payload["lifecycle_status"] == "active"
    assert pdf_payload["retention_until"]
    assert pdf_payload["superseded_by_export_id"] is None
    assert pdf_payload["checksum"]
    assert pdf_payload["storage_backend"] == "local"
    assert pdf_payload["storage_object_key"].endswith(f"{pdf_payload['id']}.pdf")
    assert pdf_payload["storage_version_id"]
    assert pdf_payload["storage_etag"] == pdf_payload["checksum"]
    assert pdf_payload["storage_size_bytes"] > 0
    assert Path(pdf_payload["storage_key"]).exists()
    assert Path(pdf_payload["storage_key"]).read_bytes().startswith(b"%PDF")

    second_pdf_export = client.post(
        f"/api/solutions/{solution_id}/export/pdf",
        json={"confirmation": f"EXPORT PDF {solution_id}"},
        headers=export_headers,
    )
    assert second_pdf_export.status_code == 200
    second_pdf_payload = second_pdf_export.json()
    assert second_pdf_payload["version"] == 2
    assert second_pdf_payload["lifecycle_status"] == "active"

    dxf_export = client.post(
        f"/api/solutions/{solution_id}/export/dxf",
        json={"confirmation": f"EXPORT DXF {solution_id}"},
        headers=export_headers,
    )
    assert dxf_export.status_code == 200
    dxf_payload = dxf_export.json()
    assert dxf_payload["status"] == "ready"
    assert dxf_payload["export_type"] == "dxf"
    assert "LWPOLYLINE" in Path(dxf_payload["storage_key"]).read_text(encoding="utf-8")

    exports = client.get(f"/api/solutions/{solution_id}/exports", headers=export_headers)
    assert exports.status_code == 200
    assert {item["export_type"] for item in exports.json()} >= {"pdf", "dxf"}
    by_id = {item["id"]: item for item in exports.json()}
    assert by_id[pdf_payload["id"]]["lifecycle_status"] == "superseded"
    assert by_id[pdf_payload["id"]]["superseded_by_export_id"] == second_pdf_payload["id"]
    assert by_id[second_pdf_payload["id"]]["version"] == 2
    assert by_id[second_pdf_payload["id"]]["storage_version_id"] == second_pdf_payload["storage_version_id"]
    assert by_id[second_pdf_payload["id"]]["storage_etag"] == second_pdf_payload["storage_etag"]

    manifest = client.get(f"/api/solutions/{solution_id}/exports/manifest", headers=archive_headers)
    assert manifest.status_code == 200
    manifest_payload = manifest.json()
    assert manifest_payload["solution_id"] == solution_id
    assert manifest_payload["export_count"] >= 3
    assert manifest_payload["active_export_count"] >= 2
    manifest_by_id = {item["id"]: item for item in manifest_payload["exports"]}
    assert manifest_by_id[second_pdf_payload["id"]]["storage_exists"] is True
    assert manifest_by_id[second_pdf_payload["id"]]["checksum"] == second_pdf_payload["checksum"]
    assert manifest_by_id[second_pdf_payload["id"]]["storage_version_id"] == second_pdf_payload["storage_version_id"]
    assert manifest_by_id[second_pdf_payload["id"]]["current_storage_version_id"] == second_pdf_payload["storage_version_id"]
    assert manifest_by_id[second_pdf_payload["id"]]["current_storage_etag"] == second_pdf_payload["storage_etag"]

    download = client.get(f"/api/solutions/exports/{second_pdf_payload['id']}/download", headers=export_headers)
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF")

    recovery = client.post(
        f"/api/solutions/{solution_id}/exports/recovery-drill",
        json={"include_archive_dry_run": True},
        headers=archive_headers,
    )
    assert recovery.status_code == 200
    recovery_payload = recovery.json()
    assert recovery_payload["status"] == "passed"
    assert recovery_payload["checked_count"] >= 3
    assert recovery_payload["checksum_mismatch_count"] == 0
    assert recovery_payload["version_mismatch_count"] == 0
    assert recovery_payload["archive_dry_run"]["status"] == "dry_run"
    recovery_item = next(item for item in recovery_payload["items"] if item["export_id"] == second_pdf_payload["id"])
    assert recovery_item["expected_storage_version_id"] == second_pdf_payload["storage_version_id"]
    assert recovery_item["actual_storage_version_id"] == second_pdf_payload["storage_version_id"]
    assert recovery_item["expected_etag"] == second_pdf_payload["storage_etag"]
    assert recovery_item["actual_etag"] == second_pdf_payload["storage_etag"]

    second_pdf_path = Path(second_pdf_payload["storage_key"])
    original_second_pdf = second_pdf_path.read_bytes()
    second_pdf_path.write_bytes(b"corrupted export payload")
    corrupt_recovery = client.post(
        f"/api/solutions/{solution_id}/exports/recovery-drill",
        json={"include_archive_dry_run": False},
        headers=archive_headers,
    )
    assert corrupt_recovery.status_code == 200
    corrupt_payload = corrupt_recovery.json()
    assert corrupt_payload["status"] == "failed"
    assert corrupt_payload["checksum_mismatch_count"] == 1
    corrupt_item = next(item for item in corrupt_payload["items"] if item["export_id"] == second_pdf_payload["id"])
    assert corrupt_item["status"] == "checksum_mismatch"
    assert corrupt_item["actual_checksum"] != corrupt_item["expected_checksum"]
    second_pdf_path.write_bytes(original_second_pdf)
    version_drift_recovery = client.post(
        f"/api/solutions/{solution_id}/exports/recovery-drill",
        json={"include_archive_dry_run": False},
        headers=archive_headers,
    )
    assert version_drift_recovery.status_code == 200
    version_drift_payload = version_drift_recovery.json()
    assert version_drift_payload["status"] == "failed"
    assert version_drift_payload["version_mismatch_count"] >= 1
    version_drift_item = next(
        item for item in version_drift_payload["items"] if item["export_id"] == second_pdf_payload["id"]
    )
    assert version_drift_item["status"] == "version_mismatch"
    assert version_drift_item["actual_checksum"] == version_drift_item["expected_checksum"]
    assert version_drift_item["actual_storage_version_id"] != version_drift_item["expected_storage_version_id"]

    report = client.get(f"/api/solutions/{solution_id}/report", headers=admin_headers)
    assert report.status_code == 200
    assert report.json()["approvals"][0]["decision_note"] == "Approved for export"
    assert report.json()["export_records"][0]["status"] == "ready"

    with SessionLocal() as db:
        row = db.get(dbm.SolutionExport, second_pdf_payload["id"])
        assert row is not None
        row.retention_until = repository.utc_now() - timedelta(days=1)
        db.commit()

    dry_run_archive = client.post(
        "/api/solutions/exports/archive-expired",
        json={"solution_id": solution_id, "dry_run": True},
        headers=archive_headers,
    )
    assert dry_run_archive.status_code == 200
    assert dry_run_archive.json()["status"] == "dry_run"
    assert dry_run_archive.json()["checked_count"] == 1
    assert dry_run_archive.json()["archived_count"] == 0
    exports_after_dry_run = client.get(f"/api/solutions/{solution_id}/exports", headers=archive_headers)
    assert exports_after_dry_run.status_code == 200
    assert {item["id"]: item for item in exports_after_dry_run.json()}[second_pdf_payload["id"]]["lifecycle_status"] == "active"

    archive_response = client.post(
        "/api/solutions/exports/archive-expired",
        json={"solution_id": solution_id, "dry_run": False},
        headers=archive_headers,
    )
    assert archive_response.status_code == 200
    archive_payload = archive_response.json()
    assert archive_payload["status"] == "completed"
    assert archive_payload["archived_count"] == 1
    assert archive_payload["archived_exports"][0]["id"] == second_pdf_payload["id"]
    exports_after_archive = client.get(f"/api/solutions/{solution_id}/exports", headers=archive_headers)
    assert exports_after_archive.status_code == 200
    assert {item["id"]: item for item in exports_after_archive.json()}[second_pdf_payload["id"]]["lifecycle_status"] == "archived"

    with SessionLocal() as db:
        row = db.get(dbm.SolutionExport, dxf_payload["id"])
        assert row is not None
        row.retention_until = repository.utc_now() - timedelta(days=1)
        db.commit()

    archive_task_response = client.post(
        "/api/solutions/exports/archive-expired/async",
        json={"solution_id": solution_id, "dry_run": False},
        headers=archive_headers,
    )
    assert archive_task_response.status_code == 200
    task_id = archive_task_response.json()["id"]
    task_response = client.get(f"/api/tasks/{task_id}", headers=admin_headers)
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "completed"
    assert task_response.json()["result"]["archived_count"] == 1

    manifest_after_archive = client.get(f"/api/solutions/{solution_id}/exports/manifest", headers=archive_headers)
    assert manifest_after_archive.status_code == 200
    assert manifest_after_archive.json()["archived_export_count"] >= 2
