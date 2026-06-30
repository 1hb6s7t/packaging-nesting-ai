import json
from pathlib import Path

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


def test_enterprise_batch_1500_endpoint_runs_real_batch_pipeline_stress() -> None:
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
    assert payload["job_id"]
    assert payload["metrics"]["synthetic"] is True
    assert payload["metrics"]["fixture_source"] == "generated_svg_dxf_pdf_placeholders"
    assert payload["metrics"]["pipeline"] == "batch_artwork_to_batch_layout"
    assert payload["metrics"]["sheet_parent"]["width"] == 787
    assert payload["metrics"]["sheet_parent"]["height"] == 1092
    assert payload["metrics"]["moq_per_item"] == 1000
    assert payload["metrics"]["top_k"] == 3
    assert payload["metrics"]["direct_parseable_count"] == 25
    assert payload["metrics"]["direct_parse_success_count"] == 25
    assert payload["metrics"]["direct_parse_success_rate"] == 1
    assert payload["metrics"]["plan_count"] == 3
    assert payload["metrics"]["legal_plan_count"] == 3
    assert payload["metrics"]["multi_solver_candidate_count"] >= 3
    assert payload["metrics"]["multi_solver_legal_candidate_count"] >= 1
    assert sum(payload["metrics"]["class_counts"].values()) == 25
    assert payload["metrics"]["status_counts"]["parsed"] == 25


def test_enterprise_batch_20000_endpoint_uses_explicit_generated_pipeline() -> None:
    headers = auth_headers(client)
    response = client.post(
        "/api/benchmarks/run/batch-20000",
        json={"file_count": 30},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["benchmark_type"] == "batch_20000"
    assert payload["status"] == "passed"
    assert payload["file_count"] == 30
    assert payload["metrics"]["synthetic"] is True
    assert payload["metrics"]["fixture_source"] == "generated_svg_dxf_pdf_placeholders"
    assert payload["metrics"]["file_count"] == 30
    assert payload["metrics"]["sheet_parent"]["width"] == 787
    assert payload["metrics"]["moq_per_item"] == 1000
    assert payload["metrics"]["top_k"] == 3
    assert payload["metrics"]["direct_parse_success_rate"] == 1
    assert payload["metrics"]["plan_count"] == 3


def test_enterprise_or_dataset_endpoint_imports_and_runs_case(tmp_path: Path) -> None:
    headers = auth_headers(client)
    dataset = tmp_path / "or-dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "bin_width": 787,
                "bin_height": 1092,
                "rectangles": [
                    {"id": "box_a", "width": 80, "height": 60, "demand": 1000},
                ],
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        "/api/benchmarks/run/or-dataset",
        json={"path": str(dataset), "case_id": "OR_API_CASE", "planning_mode": "pattern"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["benchmark_type"] == "or_dataset"
    assert payload["status"] == "passed"
    assert payload["file_count"] == 1
    assert payload["quantity_fulfillment_rate"] == 1
    assert payload["metrics"]["pipeline"] == "or_dataset_to_pattern_planner"
    assert payload["metrics"]["source"] == "or_dataset"
    assert payload["metrics"]["sheet_787x1092"] is True
    assert payload["metrics"]["moq_1000"] is True
    assert payload["metrics"]["shortage_units"] == 0
