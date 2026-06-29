import hashlib
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.session import SessionLocal
from app.domain import schemas
from app.main import app
from app.services import repository
from app.services.file_conversion import submit_external_conversion_job
from auth_helpers import auth_headers


client = TestClient(app)


def multipart_field(body: bytes, name: str) -> str:
    marker = f'name="{name}"'.encode("utf-8")
    marker_index = body.index(marker)
    value_start = body.index(b"\r\n\r\n", marker_index) + 4
    value_end = body.index(b"\r\n--", value_start)
    return body[value_start:value_end].decode("utf-8")


def test_unsupported_artwork_conversion_job_can_be_audited() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"design-{suffix}.ai", b"%!PS-Adobe-3.0", "application/postscript")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]

    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg", headers=headers)
    assert created.status_code == 200
    job = created.json()
    assert job["artwork_file_id"] == artwork_id
    assert job["source_format"] == "ai"
    assert job["target_format"] == "svg"
    assert job["status"] == "manual_required"
    assert "external conversion service" in job["log"]

    listed = client.get(f"/api/artworks/conversion-jobs?artwork_id={artwork_id}", headers=headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [job["id"]]

    fetched = client.get(f"/api/artworks/conversion-jobs/{job['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]

    updated = client.patch(
        f"/api/artworks/conversion-jobs/{job['id']}",
        json={"status": "failed", "log": "Vendor conversion rejected the source file."},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "failed"
    assert updated.json()["log"] == "Vendor conversion rejected the source file."


def test_legacy_plain_callback_token_is_redacted_but_still_accepted() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"legacy-token-{suffix}.pdf", b"%PDF-1.7", "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    legacy_token = f"legacy-token-{suffix}-123456"
    rotated_at = "2026-06-27T00:00:00+00:00"

    with SessionLocal() as db:
        repository.set_file_conversion_job_status(
            db,
            job["id"],
            status="queued",
            log="legacy token compatibility test",
            metadata_update={
                "callback_token": legacy_token,
                "callback_token_tail": legacy_token[-6:],
                "callback_token_rotated_at": rotated_at,
                "callback_token_history": [{"token_tail": "000000", "callback_token": "nested-secret-token"}],
                "webhook_secret": "legacy-webhook-secret",
            },
        )

    fetched = client.get(f"/api/artworks/conversion-jobs/{job['id']}", headers=headers)
    assert fetched.status_code == 200
    public_metadata = fetched.json()["metadata"]
    assert public_metadata["callback_token"] == "***"
    assert public_metadata["callback_token_tail"] == legacy_token[-6:]
    assert public_metadata["callback_token_rotated_at"] == rotated_at
    assert public_metadata["callback_token_history"][0]["callback_token"] == "***"
    assert public_metadata["webhook_secret"] == "***"
    assert legacy_token not in fetched.text

    listed = client.get(f"/api/artworks/conversion-jobs?artwork_id={artwork_id}", headers=headers)
    assert listed.status_code == 200
    assert legacy_token not in listed.text
    assert listed.json()[0]["metadata"]["callback_token"] == "***"

    accepted = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/callback",
        json={"status": "failed", "log": "legacy token accepted"},
        headers={"X-Conversion-Callback-Token": legacy_token},
    )
    assert accepted.status_code == 200
    assert accepted.json()["job"]["status"] == "failed"
    assert accepted.json()["job"]["metadata"]["callback_token"] == "***"
    assert legacy_token not in accepted.text


def test_direct_svg_conversion_is_recorded_as_skipped() -> None:
    headers = auth_headers(client)
    content = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="8"><rect width="10" height="8"/></svg>'
    upload = client.post("/api/artworks/upload", files={"file": ("direct.svg", content, "image/svg+xml")}, headers=headers)
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]

    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=dxf", headers=headers)
    assert created.status_code == 200
    job = created.json()
    assert job["artwork_file_id"] == artwork_id
    assert job["source_format"] == "svg"
    assert job["target_format"] == "dxf"
    assert job["status"] == "skipped"
    assert "directly parseable" in job["log"]


def test_external_conversion_job_can_be_submitted_to_service() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"submit-{suffix}.pdf", b"%PDF-1.7", "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = request.read()
        assert request.headers["authorization"] == "Bearer converter-secret"
        assert b'name="job_id"' in body
        assert job["id"].encode("utf-8") in body
        assert b'name="target_format"' in body
        assert b"svg" in body
        assert b'name="sla_minutes"' in body
        assert b"15" in body
        assert b"None" not in body
        assert b"%PDF-1.7" in body
        assert multipart_field(body, "callback_token")
        return httpx.Response(202, json={"remote_job_id": f"REMOTE-{suffix}"})

    with SessionLocal() as db:
        result = submit_external_conversion_job(
            db,
            job["id"],
            settings=Settings(
                EXTERNAL_CONVERSION_SERVICE_URL="https://converter.example.test/api",
                EXTERNAL_CONVERSION_SERVICE_API_KEY="converter-secret",
            ),
            request=schemas.FileConversionSubmitRequest(sla_minutes=15),
            http_transport=httpx.MockTransport(handler),
        )

    assert result.status == "submitted"
    assert result.remote_status_code == 202
    assert result.remote_response["remote_job_id"] == f"REMOTE-{suffix}"
    assert result.job.status == "queued"
    assert "callback_token" not in result.job.metadata
    assert result.job.metadata["callback_token_hash"]
    assert len(result.job.metadata["callback_token_tail"]) == 6
    assert result.job.metadata["callback_url"] == ""
    assert result.job.metadata["sla_minutes"] == 15
    assert result.job.metadata["sla_due_at"]
    assert result.job.metadata["submit_attempt"] == 1
    assert len(requests) == 1


def test_external_conversion_submit_can_rotate_callback_token() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"rotate-{suffix}.pdf", b"%PDF-1.7", "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    requests: list[httpx.Request] = []
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(request.read())
        return httpx.Response(202, json={"remote_job_id": f"ROTATE-{len(requests)}-{suffix}"})

    with SessionLocal() as db:
        first = submit_external_conversion_job(
            db,
            job["id"],
            settings=Settings(EXTERNAL_CONVERSION_SERVICE_URL="https://converter.example.test/api"),
            request=schemas.FileConversionSubmitRequest(callback_token="old-token-123456", sla_minutes=15),
            http_transport=httpx.MockTransport(handler),
        )
        second = submit_external_conversion_job(
            db,
            job["id"],
            settings=Settings(EXTERNAL_CONVERSION_SERVICE_URL="https://converter.example.test/api"),
            request=schemas.FileConversionSubmitRequest(rotate_callback_token=True, sla_minutes=15),
            http_transport=httpx.MockTransport(handler),
        )

    old_token = "old-token-123456"
    new_token = multipart_field(bodies[-1], "callback_token")
    assert first.status == "submitted"
    assert second.status == "submitted"
    assert new_token != old_token
    assert "callback_token" not in first.job.metadata
    assert "callback_token" not in second.job.metadata
    assert first.job.metadata["callback_token_hash"]
    assert second.job.metadata["callback_token_hash"]
    assert first.job.metadata["callback_token_tail"] == old_token[-6:]
    assert second.job.metadata["callback_token_tail"] == new_token[-6:]
    assert second.job.metadata["submit_attempt"] == 2
    assert second.job.metadata["callback_token_history"][-1]["token_tail"] == old_token[-6:]
    assert len(requests) == 2

    rejected = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/callback",
        json={"status": "failed", "log": "old token should be rejected"},
        headers={"X-Conversion-Callback-Token": old_token},
    )
    assert rejected.status_code == 401

    accepted = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/callback",
        json={"status": "failed", "log": "new token accepted"},
        headers={"X-Conversion-Callback-Token": new_token},
    )
    assert accepted.status_code == 200
    assert accepted.json()["job"]["status"] == "failed"


def test_external_conversion_callback_requires_token_and_applies_result() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"callback-{suffix}.pdf", b"%PDF-1.7", "application/pdf")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.read())
        return httpx.Response(202, json={"remote_job_id": f"CALLBACK-{suffix}"})

    with SessionLocal() as db:
        result = submit_external_conversion_job(
            db,
            job["id"],
            settings=Settings(EXTERNAL_CONVERSION_SERVICE_URL="https://converter.example.test/api"),
            http_transport=httpx.MockTransport(handler),
        )
    token = multipart_field(bodies[-1], "callback_token")
    assert "callback_token" not in result.job.metadata
    assert result.job.metadata["callback_token_hash"]
    assert result.job.metadata["callback_token_tail"] == token[-6:]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
        '<rect id="cut" width="20" height="10"/></svg>'
    )

    rejected = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/callback",
        json={"status": "completed", "target_format": "svg", "content": svg, "log": "Vendor callback ok"},
        headers={"X-Conversion-Callback-Token": "wrong-token"},
    )
    assert rejected.status_code == 401

    accepted = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/callback",
        json={"status": "completed", "target_format": "svg", "content": svg, "log": "Vendor callback ok"},
        headers={"X-Conversion-Callback-Token": token},
    )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["job"]["status"] == "completed"
    assert payload["job"]["metadata"]["last_callback_status"] == "completed"
    assert payload["artwork_version"]["metadata"]["conversion_job_id"] == job["id"]
    assert payload["polygon_count"] == 1


def test_conversion_failure_vendor_error_code_can_require_manual_handling() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"vendor-error-{suffix}.ai", b"%!PS-Adobe-3.0", "application/postscript")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()

    applied = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/result",
        headers=headers,
        json={
            "status": "failed",
            "log": "Vendor cannot open source",
            "metadata": {
                "vendor_error_code": "UNSUPPORTED-FORMAT",
                "vendor_error_message": "AI plug-in missing",
            },
        },
    )

    assert applied.status_code == 200
    payload = applied.json()
    assert payload["job"]["status"] == "manual_required"
    assert payload["job"]["metadata"]["vendor_error"]["code"] == "unsupported_format"
    assert payload["job"]["metadata"]["vendor_error"]["mapped_status"] == "manual_required"
    assert payload["job"]["metadata"]["vendor_error"]["message"] == "AI plug-in missing"


def test_conversion_sla_check_marks_overdue_jobs_and_notifies() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"sla-{suffix}.ai", b"%!PS-Adobe-3.0", "application/postscript")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    with SessionLocal() as db:
        repository.set_file_conversion_job_status(
            db,
            job["id"],
            status="queued",
            log="queued for SLA test",
            metadata_update={
                "sla_due_at": "2000-01-01T00:00:00",
                "callback_token_hash": hashlib.sha256(b"sla-test-token").hexdigest(),
                "callback_token_tail": "token",
            },
        )

    response = client.post("/api/artworks/conversion-jobs/sla/check", headers=headers, json={"notify": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "overdue"
    assert payload["overdue_count"] >= 1
    overdue = next(item for item in payload["overdue_jobs"] if item["id"] == job["id"])
    assert overdue["status"] == "overdue"
    assert overdue["metadata"]["overdue_at"]

    listed = client.get(f"/api/artworks/conversion-jobs/{job['id']}", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["status"] == "overdue"


def test_conversion_result_writes_normalized_version_and_polygons() -> None:
    headers = auth_headers(client)
    suffix = uuid4().hex[:8]
    upload = client.post(
        "/api/artworks/upload",
        files={"file": (f"design-{suffix}.ai", b"%!PS-Adobe-3.0", "application/postscript")},
        headers=headers,
    )
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    created = client.post(f"/api/artworks/{artwork_id}/convert?target_format=svg&submit_external=false", headers=headers)
    assert created.status_code == 200
    job = created.json()
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
        '<rect id="cut" width="20" height="10"/></svg>'
    )

    applied = client.post(
        f"/api/artworks/conversion-jobs/{job['id']}/result",
        headers=headers,
        json={"status": "completed", "target_format": "svg", "content": svg, "log": "Vendor conversion ok"},
    )

    assert applied.status_code == 200
    payload = applied.json()
    assert payload["job"]["status"] == "completed"
    assert payload["artwork_version"]["version"] == 1
    assert payload["artwork_version"]["metadata"]["conversion_job_id"] == job["id"]
    assert payload["polygon_count"] == 1
    assert payload["polygon_storage_key"]

    artwork = client.get(f"/api/artworks/{artwork_id}", headers=headers)
    assert artwork.status_code == 200
    assert artwork.json()["source_format"] == "svg"
    assert artwork.json()["status"] == "parsed"

    versions = client.get(f"/api/artworks/{artwork_id}/versions", headers=headers)
    assert versions.status_code == 200
    assert versions.json()[0]["metadata"]["target_format"] == "svg"

    preview = client.get(f"/api/artworks/{artwork_id}/preview", headers=headers)
    assert preview.status_code == 200
    assert "<polygon" in preview.text
