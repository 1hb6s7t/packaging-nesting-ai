from fastapi.testclient import TestClient

from app.main import app
from auth_helpers import auth_headers


client = TestClient(app)


def test_enterprise_stress_787_endpoint_records_batch_benchmark_metrics() -> None:
    headers = auth_headers(client)
    response = client.post(
        "/api/benchmarks/run/stress-787",
        json={"quantity_levels": [1000, 3000]},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["benchmark_type"] == "stress_787"
    assert payload["status"] == "passed"
    assert payload["file_count"] == 2
    assert payload["hard_rule_pass_rate"] == 1
    assert payload["quantity_fulfillment_rate"] == 1
    assert payload["metrics"]["quantity_levels"] == [1000, 3000]


def test_enterprise_batch_1500_endpoint_runs_synthetic_feature_stress() -> None:
    headers = auth_headers(client)
    response = client.post(
        "/api/benchmarks/run/batch-1500",
        json={"file_count": 25},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["benchmark_type"] == "batch_1500"
    assert payload["status"] == "passed"
    assert payload["file_count"] == 25
    assert payload["topk_legal_rate"] == 1
    assert sum(payload["metrics"]["class_counts"].values()) == 25
