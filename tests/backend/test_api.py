import logging
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main as main_module
from app.core.config import Settings
from app.main import app
from app.services import api_metrics
from auth_helpers import auth_headers


client = TestClient(app)


def test_health_and_ai_tools() -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    tools = client.get("/api/ai/tools").json()
    assert any(tool["name"] == "run_solver" for tool in tools)


def test_baseline_security_headers_are_applied() -> None:
    response = client.get("/api/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert "Strict-Transport-Security" not in response.headers


def test_request_id_header_is_generated_when_missing() -> None:
    response = client.get("/api/health")
    request_id = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)


def test_request_id_header_preserves_safe_gateway_value() -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "edge-req_123.456:abc"})
    assert response.headers["X-Request-ID"] == "edge-req_123.456:abc"


def test_request_id_header_rejects_unsafe_gateway_value() -> None:
    response = client.get("/api/health", headers={"X-Request-ID": "bad id with spaces"})
    request_id = response.headers["X-Request-ID"]
    assert request_id != "bad id with spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)


def test_request_access_log_includes_request_id(caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.request")
    response = client.get("/api/health", headers={"X-Request-ID": "ops-trace-001"})
    assert response.status_code == 200
    assert any(
        "request completed request_id=ops-trace-001 method=GET path=/api/health status_code=200" in record.message
        for record in caplog.records
    )


def test_http_error_response_includes_request_id() -> None:
    response = client.post("/api/sheets", json={}, headers={"X-Request-ID": "err-auth-001"})
    payload = response.json()
    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "err-auth-001"
    assert payload["request_id"] == "err-auth-001"
    assert payload["detail"] == "missing bearer token"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_validation_error_response_includes_request_id() -> None:
    response = client.post("/api/auth/login", json={"email": "bad@example.com"}, headers={"X-Request-ID": "err-422"})
    payload = response.json()
    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "err-422"
    assert payload["request_id"] == "err-422"
    assert isinstance(payload["detail"], list)


def test_unhandled_error_response_includes_safe_request_id() -> None:
    error_app = FastAPI()
    settings = Settings()
    main_module.add_request_context_middleware(error_app)
    main_module.add_security_headers_middleware(error_app, settings)
    main_module.add_error_handlers(error_app, settings)

    @error_app.get("/boom")
    def boom() -> None:
        raise RuntimeError("database password leaked in low-level message")

    response = TestClient(error_app, raise_server_exceptions=False).get("/boom", headers={"X-Request-ID": "err-500"})
    payload = response.json()
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "err-500"
    assert payload == {"detail": "internal server error", "request_id": "err-500"}
    assert response.headers["X-Frame-Options"] == "DENY"


def test_api_metrics_requires_audit_permission() -> None:
    api_metrics.reset_api_metrics()
    response = client.get("/api/metrics")
    assert response.status_code == 401
    assert response.json()["detail"] == "missing bearer token"


def test_api_metrics_reports_route_latency_and_status_classes() -> None:
    headers = auth_headers(client)
    api_metrics.reset_api_metrics()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/not-found").status_code == 404
    api_metrics.record_request_metric(method="POST", route="/api/synthetic", status_code=503, duration_ms=12.34)

    response = client.get("/api/metrics", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_requests"] == 3
    assert payload["error_count"] == 1
    assert payload["avg_duration_ms"] >= 0

    routes = {
        (item["method"], item["route"], item["status_class"]): item
        for item in payload["routes"]
    }
    health = routes[("GET", "/api/health", "2xx")]
    assert health["count"] == 1
    assert health["error_count"] == 0
    assert health["avg_duration_ms"] >= 0
    assert health["max_duration_ms"] >= 0
    assert routes[("GET", "/api/not-found", "4xx")]["count"] == 1
    synthetic = routes[("POST", "/api/synthetic", "5xx")]
    assert synthetic["count"] == 1
    assert synthetic["error_count"] == 1
    assert synthetic["total_duration_ms"] == 12.34


def test_hsts_security_header_can_be_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(SECURITY_HSTS_ENABLED=True, SECURITY_HSTS_MAX_AGE_SEC=63072000),
    )
    hsts_app = main_module.create_app()
    response = TestClient(hsts_app).get("/api/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=63072000"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_create_sheet_and_run_job() -> None:
    headers = auth_headers(client)
    sheet = {
        "sheet_id": "sheet_api",
        "width": 500,
        "height": 400,
        "margin_top": 5,
        "margin_right": 5,
        "margin_bottom": 5,
        "margin_left": 5,
        "gripper_mm": 10,
        "material": "white_card",
        "thickness": "350gsm",
        "cost_per_sheet": 3.5,
    }
    assert client.post("/api/sheets", json=sheet, headers=headers).status_code == 200
    job = {
        "job_id": "job_api",
        "sheet": sheet,
        "candidate_items": [
            {
                "item_id": "item_api_1",
                "order_id": "O-API-1",
                "polygon": {
                    "shape_id": "shape_api_1",
                    "outer": [[0, 0], [100, 0], [100, 80], [0, 80]],
                },
                "priority_score": 0.9,
            }
        ],
    }
    assert client.post("/api/nesting/jobs", json=job, headers=headers).status_code == 200
    response = client.post("/api/nesting/jobs/job_api/run", headers=headers)
    assert response.status_code == 200
    solution = response.json()["solutions"][0]
    assert solution["validation_report"]["is_valid"] is True
    assert client.get(f"/api/solutions/{solution['solution_id']}/preview.svg", headers=headers).status_code == 200
