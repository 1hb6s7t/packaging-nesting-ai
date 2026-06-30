from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def _create_parsed_batch(headers: dict[str, str]) -> str:
    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="90" height="70">'
        b'<rect id="cut" x="0" y="0" width="90" height="70"/></svg>'
    )
    response = client.post(
        "/api/batch-artworks/upload",
        files=[("files", ("carton.svg", svg_content, "image/svg+xml"))],
        data={"source_name": "layout-api-batch"},
        headers=headers,
    )
    assert response.status_code == 200
    batch_id = response.json()["batch"]["batch_id"]
    assert client.post(f"/api/batch-artworks/{batch_id}/preflight", headers=headers).status_code == 200
    assert client.post(f"/api/batch-artworks/{batch_id}/parse", headers=headers).status_code == 200
    return batch_id


def test_batch_layout_job_run_outputs_top3_groups_plans_and_preview() -> None:
    headers = auth_headers(client)
    batch_id = _create_parsed_batch(headers)

    created = client.post(
        "/api/batch-layout/jobs",
        json={"batch_id": batch_id, "moq_per_item": 1000, "top_k": 3},
        headers=headers,
    )
    assert created.status_code == 200
    job = created.json()
    assert job["batch_id"] == batch_id
    assert len(job["cut_variants"]) >= 6
    fetched_job = client.get(f"/api/batch-layout/jobs/{job['job_id']}", headers=headers)
    assert fetched_job.status_code == 200
    assert fetched_job.json()["job_id"] == job["job_id"]
    assert fetched_job.json()["batch_id"] == batch_id

    run = client.post(f"/api/batch-layout/jobs/{job['job_id']}/run", headers=headers)
    assert run.status_code == 200
    result = run.json()
    assert result["summary"]["group_count"] == 1
    assert result["summary"]["plan_count"] == 3
    assert result["summary"]["multi_solver_candidate_count"] >= 3
    assert result["summary"]["multi_solver_legal_candidate_count"] >= 1
    assert result["plans"][0]["rank"] == 1
    assert result["plans"][0]["quantity_fulfillment_rate"] == 1
    candidate_pool = result["plans"][0]["audit_manifest"]["candidate_pool"]
    assert candidate_pool["orchestrator"] == "MultiSolverOrchestrator"
    assert candidate_pool["candidate_count"] == result["summary"]["multi_solver_candidate_count"]
    assert candidate_pool["legal_candidate_count"] == result["summary"]["multi_solver_legal_candidate_count"]
    assert "RectpackSolver" in candidate_pool["solver_names"]
    assert result["plans"][0]["validator_report"]["veto"]["multi_solver_candidate_pool_ok"] is True

    groups = client.get(f"/api/batch-layout/jobs/{job['job_id']}/groups", headers=headers)
    assert groups.status_code == 200
    assert len(groups.json()) == 1

    plans = client.get(f"/api/batch-layout/jobs/{job['job_id']}/plans", headers=headers)
    assert plans.status_code == 200
    assert len(plans.json()) == 3
    plan_id = plans.json()[0]["plan_id"]
    fetched_plan = client.get(f"/api/batch-layout/plans/{plan_id}", headers=headers)
    assert fetched_plan.status_code == 200
    assert fetched_plan.json()["plan_id"] == plan_id
    assert fetched_plan.json()["patterns"]

    preview = client.get(f"/api/batch-layout/plans/{plan_id}/preview", headers=headers)
    assert preview.status_code == 200
    assert "image/svg+xml" in preview.headers["content-type"]
    assert "Plan 1" in preview.text

    export = client.post(
        f"/api/batch-layout/plans/{plan_id}/export",
        json={"confirmation": f"EXPORT PLAN {plan_id}"},
        headers=headers,
    )
    assert export.status_code == 409
    assert export.json()["detail"] == "production plan must be approved before production export"

    request = client.post(
        f"/api/batch-layout/plans/{plan_id}/approval/request",
        json={"note": "Ready for production plan review"},
        headers=headers,
    )
    assert request.status_code == 200
    assert request.json()["status"] == "pending"
    assert request.json()["snapshot"]["plan_id"] == plan_id

    missing_confirmation = client.post(
        f"/api/batch-layout/plans/{plan_id}/approval/decision",
        json={"decision": "approved", "note": "missing phrase"},
        headers=headers,
    )
    assert missing_confirmation.status_code == 409

    decision = client.post(
        f"/api/batch-layout/plans/{plan_id}/approval/decision",
        json={"decision": "approved", "note": "Approved for JSON manifest", "confirmation": f"APPROVE PLAN {plan_id}"},
        headers=headers,
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"

    approved_export = client.post(
        f"/api/batch-layout/plans/{plan_id}/export",
        json={"confirmation": f"EXPORT PLAN {plan_id}"},
        headers=headers,
    )
    assert approved_export.status_code == 200
    export_payload = approved_export.json()
    assert export_payload["plan_id"] == plan_id
    assert export_payload["export_type"] == "json"
    assert export_payload["version"] == 1
    assert export_payload["checksum"]

    exports = client.get(f"/api/batch-layout/plans/{plan_id}/exports", headers=headers)
    assert exports.status_code == 200
    assert exports.json()[0]["id"] == export_payload["id"]

    download = client.get(f"/api/batch-layout/plans/exports/{export_payload['id']}/download", headers=headers)
    assert download.status_code == 200
    assert download.json()["plan"]["plan_id"] == plan_id

    rerun = client.post(f"/api/batch-layout/jobs/{job['job_id']}/run", headers=headers)
    assert rerun.status_code == 200
    blocked_after_rerun = client.post(
        f"/api/batch-layout/plans/{plan_id}/export",
        json={"confirmation": f"EXPORT PLAN {plan_id}"},
        headers=headers,
    )
    assert blocked_after_rerun.status_code == 409
    assert blocked_after_rerun.json()["detail"] == "production plan must be approved before production export"
