import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db import models as dbm
from app.db.session import SessionLocal
from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def _sample_job(job_id: str, solver_name: str = "RectpackSolver") -> dict:
    suffix = job_id[-8:]
    return {
        "job_id": job_id,
        "sheet": {
            "sheet_id": f"SOLVER_SHEET_{suffix}",
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
        "solver_config": {"solver_name": solver_name, "time_limit_sec": 30, "options": {}},
    }


def test_solver_registry_seed_update_and_runtime_gate() -> None:
    headers = auth_headers(client)

    permissions = client.get("/api/rbac/permissions", headers=headers)
    assert permissions.status_code == 200
    assert "solvers:manage" in {item["code"] for item in permissions.json()}

    registry_response = client.get("/api/solvers/registry", headers=headers)
    assert registry_response.status_code == 200
    registry = registry_response.json()
    by_name = {item["name"]: item for item in registry}
    assert by_name["RectpackSolver"]["enabled"] is True
    assert by_name["RectpackSolver"]["license_policy"] == "open_source"
    assert by_name["PackingSolver"]["enabled"] is False

    suffix = uuid4().hex[:8]
    updated = client.patch(
        "/api/solvers/registry/PackingSolver",
        headers=headers,
        json={"enabled": True, "license_policy": "commercial", "version": f"contract-{suffix}"},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["version"] == f"contract-{suffix}"

    disabled = client.patch("/api/solvers/registry/PackingSolver", headers=headers, json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    job_id = f"SOLVER_DISABLED_{suffix}"
    assert client.post("/api/nesting/jobs", headers=headers, json=_sample_job(job_id, "PackingSolver")).status_code == 200
    run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)
    assert run_response.status_code == 404
    assert "solver is disabled" in run_response.json()["detail"]


def test_solver_registry_rejects_enabling_unconfigured_external_stub() -> None:
    headers = auth_headers(client)

    reset_response = client.patch(
        "/api/solvers/registry/SparrowSolver",
        headers=headers,
        json={"enabled": False, "version": "external-adapter-stub-0.1.0", "license_policy": "commercial"},
    )
    assert reset_response.status_code == 200

    enable_response = client.patch("/api/solvers/registry/SparrowSolver", headers=headers, json={"enabled": True})
    assert enable_response.status_code == 400
    assert "solver adapter is not configured: SparrowSolver" in enable_response.json()["detail"]


def test_solver_runtime_rejects_legacy_enabled_stub_registry_row() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        row = db.query(dbm.SolverRegistry).filter(dbm.SolverRegistry.name == "OrToolsSolver").one()
        original = {
            "enabled": row.enabled,
            "version": row.version,
            "license_policy": row.license_policy,
        }
        row.enabled = True
        row.version = "external-adapter-stub-0.1.0"
        row.license_policy = "review_required"
        db.commit()

    try:
        job_id = f"SOLVER_STUB_{suffix}"
        assert client.post("/api/nesting/jobs", headers=headers, json=_sample_job(job_id, "OrToolsSolver")).status_code == 200
        run_response = client.post(f"/api/nesting/jobs/{job_id}/run", headers=headers)
        assert run_response.status_code == 404
        assert "solver adapter is not configured: OrToolsSolver" in run_response.json()["detail"]
    finally:
        with SessionLocal() as db:
            row = db.query(dbm.SolverRegistry).filter(dbm.SolverRegistry.name == "OrToolsSolver").one()
            row.enabled = original["enabled"]
            row.version = original["version"]
            row.license_policy = original["license_policy"]
            db.commit()
