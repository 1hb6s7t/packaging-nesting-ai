from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain import schemas
from app.services import repository
from app.services.artworks import checksum_bytes, parse_vector_polygons, save_polygon_json
from app.services.storage import read_bytes, read_text, write_bytes


NORMALIZED_CONTENT_TYPES = {
    "svg": "image/svg+xml; charset=utf-8",
    "dxf": "application/dxf; charset=utf-8",
}
CONVERSION_CALLBACK_TOKEN_HEADER = "X-Conversion-Callback-Token"
VENDOR_ERROR_STATUS_MAP = {
    "unsupported_format": "manual_required",
    "missing_dieline": "manual_required",
    "password_protected": "manual_required",
    "invalid_file": "manual_required",
    "corrupt_file": "manual_required",
    "font_missing": "manual_required",
    "license_required": "manual_required",
    "vector_export_required": "manual_required",
    "converter_crash": "failed",
    "quota_exceeded": "failed",
    "remote_service_unavailable": "failed",
    "timeout": "failed",
}
VENDOR_FAILURE_STATUSES = {"failed", "manual_required"}


def submit_external_conversion_job(
    db: Session,
    job_id: str,
    *,
    settings: Settings,
    request: schemas.FileConversionSubmitRequest | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> schemas.FileConversionSubmitResult:
    job_row = repository.get_file_conversion_job_row(db, job_id)
    if job_row is None:
        raise ValueError("conversion job not found")
    artwork = repository.get_artwork_meta(db, job_row.artwork_file_id)
    if artwork is None:
        raise ValueError("artwork not found")
    if not settings.external_conversion_service_url:
        raise ValueError("EXTERNAL_CONVERSION_SERVICE_URL is not configured")

    source_bytes = read_bytes(str(artwork["storage_key"]))
    endpoint = urljoin(settings.external_conversion_service_url.rstrip("/") + "/", "convert")
    now = repository.utc_now()
    existing_metadata = dict(job_row.metadata_json or {})
    metadata = dict(request.metadata if request else {})
    existing_callback_token = str(existing_metadata.get("callback_token") or "")
    existing_callback_hash = str(existing_metadata.get("callback_token_hash") or "")
    existing_callback_tail = str(existing_metadata.get("callback_token_tail") or "")
    has_existing_callback = bool(existing_callback_token or existing_callback_hash)
    explicit_callback_token = request.callback_token if request and request.callback_token else None
    rotate_callback_token = bool(request and request.rotate_callback_token)
    callback_token = (
        explicit_callback_token
        if explicit_callback_token
        else (
            secrets.token_urlsafe(24)
            if rotate_callback_token or not existing_callback_token
            else existing_callback_token
        )
    )
    if existing_callback_hash and not existing_callback_token and not explicit_callback_token:
        callback_token = secrets.token_urlsafe(24)
    previous_token_tail = existing_callback_token[-6:] if existing_callback_token else existing_callback_tail
    token_audit_metadata = _callback_token_audit_metadata(
        existing_metadata,
        previous_token_tail if has_existing_callback else "",
        callback_token,
        now,
    )
    callback_url = (
        request.callback_url
        if request and request.callback_url
        else str(existing_metadata.get("callback_url") or "")
    )
    sla_minutes = (
        request.sla_minutes
        if request and request.sla_minutes is not None
        else int(existing_metadata.get("sla_minutes") or settings.external_conversion_sla_minutes)
    )
    sla_due_at = now + timedelta(minutes=max(1, sla_minutes))
    submit_attempt = int(existing_metadata.get("submit_attempt") or 0) + 1
    data = {
        "job_id": job_row.id,
        "artwork_id": job_row.artwork_file_id,
        "source_format": job_row.source_format,
        "target_format": job_row.target_format,
        "callback_url": callback_url,
        "callback_token": callback_token,
        "sla_minutes": str(sla_minutes),
        "metadata": _jsonish(metadata),
    }
    headers = {}
    if settings.external_conversion_service_api_key:
        headers["Authorization"] = f"Bearer {settings.external_conversion_service_api_key}"

    try:
        with httpx.Client(timeout=max(1, settings.external_conversion_timeout_sec), transport=http_transport) as client:
            response = client.post(
                endpoint,
                data=data,
                files={
                    "file": (
                        str(artwork.get("filename") or Path(str(artwork["storage_key"])).name),
                        source_bytes,
                        str(artwork.get("content_type") or "application/octet-stream"),
                    )
                },
                headers=headers,
            )
            response.raise_for_status()
        remote_payload = _response_payload(response)
        job = repository.set_file_conversion_job_status(
            db,
            job_row.id,
            status="queued",
            log=f"Submitted to external conversion service; remote_status={response.status_code}",
            metadata_update={
                **token_audit_metadata,
                "callback_url": callback_url,
                "callback_token_hash": _callback_token_hash(callback_token),
                "callback_token_tail": callback_token[-6:],
                "sla_minutes": sla_minutes,
                "sla_due_at": sla_due_at.isoformat(),
                "last_submitted_at": now.isoformat(),
                "submit_attempt": submit_attempt,
                "last_remote_status_code": response.status_code,
                "last_remote_response": remote_payload,
                "vendor_metadata": metadata,
            },
        )
        assert job is not None
        job = _drop_plain_callback_token(db, job_row.id) or job
        return schemas.FileConversionSubmitResult(
            job=job,
            status="submitted",
            remote_status_code=response.status_code,
            remote_response=remote_payload,
            message="conversion job submitted",
        )
    except Exception as exc:
        job = repository.set_file_conversion_job_status(
            db,
            job_row.id,
            status="failed",
            log=f"External conversion submit failed: {exc}",
            metadata_update={
                **token_audit_metadata,
                "callback_url": callback_url,
                "callback_token_hash": _callback_token_hash(callback_token),
                "callback_token_tail": callback_token[-6:],
                "sla_minutes": sla_minutes,
                "sla_due_at": sla_due_at.isoformat(),
                "last_submitted_at": now.isoformat(),
                "submit_attempt": submit_attempt,
                "last_submit_error": str(exc),
                "vendor_metadata": metadata,
            },
        )
        assert job is not None
        job = _drop_plain_callback_token(db, job_row.id) or job
        return schemas.FileConversionSubmitResult(
            job=job,
            status="failed",
            remote_status_code=None,
            remote_response={},
            message=str(exc),
        )


def apply_conversion_result(
    db: Session,
    job_id: str,
    request: schemas.FileConversionResultRequest,
) -> schemas.FileConversionResultApplyResult:
    job_row = repository.get_file_conversion_job_row(db, job_id)
    if job_row is None:
        raise ValueError("conversion job not found")

    if request.status == "failed":
        now_iso = repository.utc_now().isoformat()
        failure_status, vendor_error = _classify_vendor_failure(request.metadata)
        metadata_update = {
            **request.metadata,
            "last_callback_at": now_iso,
            "last_callback_status": "failed",
        }
        if vendor_error:
            metadata_update["vendor_error"] = vendor_error
        job = repository.set_file_conversion_job_status(
            db,
            job_row.id,
            status=failure_status,
            log=request.log or _vendor_failure_log(vendor_error),
            metadata_update=metadata_update,
        )
        assert job is not None
        return schemas.FileConversionResultApplyResult(job=job, message="conversion failure recorded")

    target_format = (request.target_format or job_row.target_format).lower()
    if target_format not in NORMALIZED_CONTENT_TYPES:
        raise ValueError("converted target_format must be svg or dxf")
    artifact_bytes = _converted_artifact_bytes(request)
    if not artifact_bytes:
        raise ValueError("completed conversion requires content, content_base64, or storage_key")
    artifact_text = artifact_bytes.decode("utf-8", errors="ignore")
    checksum = checksum_bytes(artifact_bytes)
    normalized_storage_key = _write_normalized_artifact(job_row.artwork_file_id, job_row.id, target_format, artifact_bytes)

    polygons = []
    polygon_storage_key = None
    if request.parse_polygon:
        polygons = parse_vector_polygons(artifact_text, target_format, job_row.artwork_file_id)
        polygon_storage_key = save_polygon_json(job_row.artwork_file_id, polygons)

    version = repository.create_artwork_version(
        db,
        artwork_id=job_row.artwork_file_id,
        normalized_storage_key=normalized_storage_key,
        target_format=target_format,
        checksum=checksum,
        metadata={
            **request.metadata,
            "conversion_job_id": job_row.id,
            "source_format": job_row.source_format,
            "target_format": target_format,
            "polygon_count": len(polygons),
            "polygon_storage_key": polygon_storage_key,
        },
    )
    if polygons:
        repository.save_polygons(db, job_row.artwork_file_id, polygons)
    log_parts = [
        request.log or "External conversion completed",
        f"normalized_storage_key={normalized_storage_key}",
        f"artwork_version={version.version}",
    ]
    if polygon_storage_key:
        log_parts.append(f"polygon_storage_key={polygon_storage_key}")
    job = repository.set_file_conversion_job_status(
        db,
        job_row.id,
        status="completed",
        log="; ".join(log_parts),
        metadata_update={"last_callback_at": repository.utc_now().isoformat(), "last_callback_status": "completed"},
    )
    assert job is not None
    return schemas.FileConversionResultApplyResult(
        job=job,
        artwork_version=version,
        polygon_storage_key=polygon_storage_key,
        polygon_count=len(polygons),
        message="conversion result applied",
    )


def apply_authenticated_conversion_callback(
    db: Session,
    job_id: str,
    request: schemas.FileConversionResultRequest,
    *,
    callback_token: str | None,
) -> schemas.FileConversionResultApplyResult:
    job_row = repository.get_file_conversion_job_row(db, job_id)
    if job_row is None:
        raise ValueError("conversion job not found")
    metadata = job_row.metadata_json or {}
    expected_hash = str(metadata.get("callback_token_hash") or "")
    expected_token = str(metadata.get("callback_token") or "")
    if expected_hash:
        token_hash = _callback_token_hash(callback_token or "")
        if not callback_token or not secrets.compare_digest(token_hash, expected_hash):
            raise PermissionError("invalid conversion callback token")
        return apply_conversion_result(db, job_id, request)
    if not expected_token:
        raise PermissionError("conversion callback token is not configured")
    if not callback_token or not secrets.compare_digest(callback_token, expected_token):
        raise PermissionError("invalid conversion callback token")
    return apply_conversion_result(db, job_id, request)


def _callback_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _drop_plain_callback_token(db: Session, job_id: str) -> schemas.FileConversionJobRead | None:
    row = repository.get_file_conversion_job_row(db, job_id)
    if row is None or not row.metadata_json or "callback_token" not in row.metadata_json:
        return None
    metadata = dict(row.metadata_json)
    if not metadata.get("callback_token_hash"):
        return None
    metadata.pop("callback_token", None)
    row.metadata_json = metadata
    db.commit()
    return repository.file_conversion_job_from_row(row)


def check_file_conversion_sla(
    db: Session,
    *,
    request: schemas.FileConversionSlaCheckRequest | None = None,
) -> schemas.FileConversionSlaCheckResult:
    now = repository.utc_now()
    now_iso = now.isoformat()
    sla_minutes = (
        request.sla_minutes
        if request and request.sla_minutes is not None
        else get_settings().external_conversion_sla_minutes
    )
    overdue_rows = repository.list_overdue_file_conversion_job_rows(
        db,
        now_iso=now_iso,
        fallback_sla_minutes=sla_minutes,
    )
    overdue_jobs: list[schemas.FileConversionJobRead] = []
    notification_count = 0
    for row in overdue_rows:
        metadata = dict(row.metadata_json or {})
        sla_due_at = str(
            metadata.get("sla_due_at")
            or (row.updated_at + timedelta(minutes=max(1, sla_minutes))).isoformat()
        )
        job = repository.set_file_conversion_job_status(
            db,
            row.id,
            status="overdue",
            log=f"External conversion SLA overdue at {now_iso}",
            metadata_update={**metadata, "sla_due_at": sla_due_at, "overdue_at": now_iso},
        )
        assert job is not None
        overdue_jobs.append(job)
        if request is None or request.notify:
            notifications = repository.create_permission_notifications(
                db,
                permission_code="artworks:write",
                event_type="artwork.conversion.overdue",
                title="File conversion SLA overdue",
                message=f"Conversion job {row.id} exceeded SLA",
                target_type="file_conversion_job",
                target_id=row.id,
                payload={
                    "job": job.model_dump(mode="json"),
                    "sla_due_at": sla_due_at,
                    "checked_at": now_iso,
                },
            )
            notification_count += len(notifications)
    return schemas.FileConversionSlaCheckResult(
        status="overdue" if overdue_jobs else "ok",
        checked_count=len(overdue_rows),
        overdue_count=len(overdue_jobs),
        notification_count=notification_count,
        overdue_jobs=overdue_jobs,
    )


def _callback_token_audit_metadata(
    existing_metadata: dict[str, Any],
    previous_token_tail: str,
    current_token: str,
    now: datetime,
) -> dict[str, Any]:
    current_token_tail = current_token[-6:]
    if not previous_token_tail or previous_token_tail == current_token_tail:
        return {}
    history_value = existing_metadata.get("callback_token_history")
    history = list(history_value) if isinstance(history_value, list) else []
    history.append({"token_tail": previous_token_tail, "rotated_at": now.isoformat()})
    return {
        "callback_token_rotated_at": now.isoformat(),
        "callback_token_history": history[-10:],
    }


def _classify_vendor_failure(metadata: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    error_payload = metadata.get("vendor_error")
    vendor_error = dict(error_payload) if isinstance(error_payload, dict) else {}
    code = _normalized_vendor_error_code(
        metadata.get("vendor_error_code")
        or metadata.get("error_code")
        or vendor_error.get("code")
        or vendor_error.get("error_code")
    )
    message = metadata.get("vendor_error_message") or metadata.get("error_message") or vendor_error.get("message")
    category = metadata.get("vendor_error_category") or vendor_error.get("category")
    status = _mapped_vendor_error_status(code, metadata)
    if not code and not message and not category:
        return status, None
    detail: dict[str, Any] = {"mapped_status": status}
    if code:
        detail["code"] = code
    if message:
        detail["message"] = str(message)
    if category:
        detail["category"] = str(category)
    return status, detail


def _mapped_vendor_error_status(code: str | None, metadata: dict[str, Any]) -> str:
    status_map = dict(VENDOR_ERROR_STATUS_MAP)
    custom_map = metadata.get("vendor_error_map")
    if isinstance(custom_map, dict):
        for raw_code, raw_status in custom_map.items():
            normalized_code = _normalized_vendor_error_code(raw_code)
            status = str(raw_status)
            if normalized_code and status in VENDOR_FAILURE_STATUSES:
                status_map[normalized_code] = status
    return status_map.get(code, "failed") if code else "failed"


def _normalized_vendor_error_code(value: Any) -> str | None:
    if value is None:
        return None
    code = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return code or None


def _vendor_failure_log(vendor_error: dict[str, Any] | None) -> str:
    if not vendor_error:
        return "External conversion failed"
    code = vendor_error.get("code")
    message = vendor_error.get("message")
    category = vendor_error.get("category")
    if code and message:
        return f"External conversion failed ({code}: {message})"
    if code:
        return f"External conversion failed ({code})"
    if message:
        return f"External conversion failed ({message})"
    return f"External conversion failed ({category})"


def _converted_artifact_bytes(request: schemas.FileConversionResultRequest) -> bytes:
    if request.content is not None:
        return request.content.encode("utf-8")
    if request.content_base64 is not None:
        return base64.b64decode(request.content_base64)
    if request.storage_key is not None:
        return read_text(request.storage_key, encoding="utf-8", errors="ignore").encode("utf-8")
    return b""


def _write_normalized_artifact(artwork_id: str, job_id: str, target_format: str, data: bytes) -> str:
    stored = write_bytes(
        f"artworks/{artwork_id}/normalized/{job_id}.{target_format}",
        data,
        content_type=NORMALIZED_CONTENT_TYPES[target_format],
    )
    return stored.storage_key


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        value = response.json()
    except ValueError:
        return {"text": response.text}
    return value if isinstance(value, dict) else {"value": value}


def _jsonish(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
