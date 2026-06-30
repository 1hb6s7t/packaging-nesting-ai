from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def test_rbac_admin_manages_roles_and_users() -> None:
    admin_headers = auth_headers(client)
    suffix = uuid4().hex[:8]

    permissions = client.get("/api/rbac/permissions", headers=admin_headers)
    assert permissions.status_code == 200
    codes = {item["code"] for item in permissions.json()}
    assert "orders:write" in codes
    assert "rbac:manage" in codes

    role_response = client.post(
        "/api/rbac/roles",
        json={
            "name": f"order_operator_{suffix}",
            "description": "Can import production orders",
            "permission_codes": ["orders:write"],
        },
        headers=admin_headers,
    )
    assert role_response.status_code == 200
    role = role_response.json()
    assert role["permission_codes"] == ["orders:write"]

    email = f"operator_{suffix}@example.com"
    user_response = client.post(
        "/api/rbac/users",
        json={
            "email": email,
            "display_name": "Order Operator",
            "password": "Strong123!45",
            "org_unit_code": f"ops_{suffix}",
            "org_unit_name": "Production Ops",
            "job_title": "Import Operator",
            "external_user_id": f"ext_{suffix}",
            "role_ids": [role["id"]],
        },
        headers=admin_headers,
    )
    assert user_response.status_code == 200
    user = user_response.json()
    assert user["email"] == email
    assert user["org_unit_code"] == f"ops_{suffix}"
    assert user["org_unit_name"] == "Production Ops"
    assert user["job_title"] == "Import Operator"
    assert user["external_user_id"] == f"ext_{suffix}"
    assert user["permissions"] == ["orders:write"]

    login = client.post("/api/auth/login", json={"email": email, "password": "Strong123!45"})
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/api/auth/me", headers=user_headers)
    assert me.status_code == 200
    assert me.json()["permissions"] == ["orders:write"]

    assert client.get("/api/rbac/users", headers=user_headers).status_code == 403
    assert (
        client.post(
            "/api/sheets",
            json={
                "sheet_id": f"RBAC_FORBIDDEN_{suffix}",
                "width": 500,
                "height": 400,
                "material": "white_card",
                "thickness": "350gsm",
            },
            headers=user_headers,
        ).status_code
        == 403
    )

    updated_role = client.patch(
        f"/api/rbac/roles/{role['id']}",
        json={"permission_codes": ["orders:write", "sheets:write"]},
        headers=admin_headers,
    )
    assert updated_role.status_code == 200
    assert updated_role.json()["permission_codes"] == ["orders:write", "sheets:write"]

    allowed_sheet = client.post(
        "/api/sheets",
        json={
            "sheet_id": f"RBAC_ALLOWED_{suffix}",
            "width": 500,
            "height": 400,
            "material": "white_card",
            "thickness": "350gsm",
        },
        headers=user_headers,
    )
    assert allowed_sheet.status_code == 200

    disabled = client.patch(
        f"/api/rbac/users/{user['id']}",
        json={"is_active": False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert disabled.json()["org_unit_code"] == f"ops_{suffix}"
    assert client.get("/api/auth/me", headers=user_headers).status_code == 401


def test_rbac_user_password_policy_applies_to_create_and_update() -> None:
    admin_headers = auth_headers(client)
    suffix = uuid4().hex[:8]

    weak_create = client.post(
        "/api/rbac/users",
        json={
            "email": f"weak_{suffix}@example.com",
            "display_name": "Weak User",
            "password": "123456789012",
            "role_ids": [],
        },
        headers=admin_headers,
    )
    assert weak_create.status_code == 400
    assert "letter" in weak_create.json()["detail"]

    user_response = client.post(
        "/api/rbac/users",
        json={
            "email": f"policy_{suffix}@example.com",
            "display_name": "Policy User",
            "password": "Strong123!45",
            "role_ids": [],
        },
        headers=admin_headers,
    )
    assert user_response.status_code == 200

    weak_update = client.patch(
        f"/api/rbac/users/{user_response.json()['id']}",
        json={"password": "NoDigitsHere!"},
        headers=admin_headers,
    )
    assert weak_update.status_code == 400
    assert "digit" in weak_update.json()["detail"]


def test_rbac_seeded_enterprise_role_templates() -> None:
    admin_headers = auth_headers(client)

    permissions = client.get("/api/rbac/permissions", headers=admin_headers)
    assert permissions.status_code == 200
    codes = {item["code"] for item in permissions.json()}
    assert "tasks:manage" in codes
    assert "solvers:manage" in codes
    assert "solutions:export" in codes
    assert "solutions:archive" in codes
    assert "batch:write" in codes

    roles = client.get("/api/rbac/roles", headers=admin_headers)
    assert roles.status_code == 200
    by_name = {item["name"]: item for item in roles.json()}
    expected_roles = {
        "admin",
        "print_planner",
        "production_operator",
        "solution_approver",
        "auditor",
        "operations_manager",
        "integration_manager",
        "benchmark_engineer",
    }
    assert expected_roles.issubset(by_name)
    assert set(by_name["admin"]["permission_codes"]) == codes
    assert set(by_name["production_operator"]["permission_codes"]) >= {
        "solutions:write",
        "solutions:export",
        "tasks:manage",
    }
    assert set(by_name["operations_manager"]["permission_codes"]) >= {"solutions:archive", "tasks:manage", "audit:read"}
    assert "solvers:manage" in by_name["benchmark_engineer"]["permission_codes"]
    assert by_name["solution_approver"]["permission_codes"] == ["audit:read", "solutions:approve"]
    assert "batch:write" in by_name["print_planner"]["permission_codes"]
    assert "batch:write" in by_name["benchmark_engineer"]["permission_codes"]
