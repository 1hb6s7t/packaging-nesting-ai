from fastapi.testclient import TestClient

from app.main import app
from app.services import batch_artworks as batch_artworks_module
from auth_helpers import auth_headers


client = TestClient(app)


def test_batch_artwork_upload_preflight_parse_and_summary() -> None:
    headers = auth_headers(client)
    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
        b'<path id="cut" d="M0 0 L120 0 L120 80 L0 80 Z"/></svg>'
    )
    pdf_content = b"%PDF-1.4\n% archived sample"

    upload = client.post(
        "/api/batch-artworks/upload",
        files=[
            ("files", ("box.svg", svg_content, "image/svg+xml")),
            ("files", ("manual.pdf", pdf_content, "application/pdf")),
        ],
        data={"source_name": "pytest-batch", "metadata_json": '{"default_quantity": 1000}'},
        headers=headers,
    )
    assert upload.status_code == 200
    batch_id = upload.json()["batch"]["batch_id"]
    assert upload.json()["batch"]["item_count"] == 2
    assert upload.json()["format_counts"]["svg"] == 1
    assert upload.json()["format_counts"]["pdf"] == 1

    preflight = client.post(f"/api/batch-artworks/{batch_id}/preflight", headers=headers)
    assert preflight.status_code == 200
    assert preflight.json()["batch"]["conversion_required_count"] == 1
    assert preflight.json()["batch"]["manual_review_count"] == 1

    parsed = client.post(f"/api/batch-artworks/{batch_id}/parse", headers=headers)
    assert parsed.status_code == 200
    payload = parsed.json()
    assert payload["batch"]["parsed_count"] == 1
    assert payload["status_counts"]["parsed"] == 1
    assert payload["status_counts"]["manual_review"] == 1
    parsed_item = next(item for item in payload["items"] if item["filename"] == "box.svg")
    pdf_item = next(item for item in payload["items"] if item["filename"] == "manual.pdf")
    assert parsed_item["feature"]["parse_confidence"] >= 0.9
    assert parsed_item["classification"] in {"FILLER", "ANCHOR"}
    assert pdf_item["parse_error"].startswith("Direct geometry parsing supports SVG/DXF")

    summary = client.get(f"/api/batch-artworks/{batch_id}/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json()["batch"]["batch_id"] == batch_id


def test_batch_artwork_retry_failed_items_reprocesses_only_failed(monkeypatch) -> None:
    headers = auth_headers(client)
    svg_content = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">'
        b'<path id="cut" d="M0 0 L120 0 L120 80 L0 80 Z"/></svg>'
    )
    upload = client.post(
        "/api/batch-artworks/upload",
        files=[("files", ("retry-box.svg", svg_content, "image/svg+xml"))],
        data={"source_name": "pytest-retry-batch", "metadata_json": '{"default_quantity": 1000}'},
        headers=headers,
    )
    assert upload.status_code == 200
    batch_id = upload.json()["batch"]["batch_id"]
    item_id = upload.json()["items"][0]["item_id"]

    real_parse_vector_polygons = batch_artworks_module.parse_vector_polygons
    calls = {"count": 0}

    def flaky_parse_vector_polygons(content: str, source_format: str, shape_id: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("simulated parser outage")
        return real_parse_vector_polygons(content, source_format, shape_id)

    monkeypatch.setattr(batch_artworks_module, "parse_vector_polygons", flaky_parse_vector_polygons)

    parsed = client.post(f"/api/batch-artworks/{batch_id}/parse", headers=headers)
    assert parsed.status_code == 200
    failed_payload = parsed.json()
    assert failed_payload["batch"]["failed_count"] == 1
    failed_item = failed_payload["items"][0]
    assert failed_item["item_id"] == item_id
    assert failed_item["status"] == "failed"
    assert failed_item["retry_count"] == 0
    assert "simulated parser outage" in failed_item["parse_error"]

    retry = client.post(
        f"/api/batch-artworks/{batch_id}/retry-failed",
        json={"item_ids": [item_id]},
        headers=headers,
    )
    assert retry.status_code == 200
    retry_payload = retry.json()
    assert retry_payload["batch"]["failed_count"] == 0
    assert retry_payload["batch"]["parsed_count"] == 1
    assert retry_payload["status_counts"]["parsed"] == 1
    retried_item = retry_payload["items"][0]
    assert retried_item["item_id"] == item_id
    assert retried_item["status"] == "parsed"
    assert retried_item["retry_count"] == 1
    assert retried_item["parse_error"] is None
    assert retried_item["classification"] in {"FILLER", "ANCHOR"}
