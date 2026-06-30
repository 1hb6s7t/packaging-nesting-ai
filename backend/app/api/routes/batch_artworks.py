from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import BatchArtworkRetryRequest, BatchArtworkSummary, CurrentUser
from app.services import repository
from app.services.artworks import checksum_bytes, new_artwork_id, preflight_artwork, save_artwork_bytes
from app.services.batch_artworks import BatchArtworkService
from app.services.security import get_current_user, require_permission
from app.services.store import store

router = APIRouter()
service = BatchArtworkService()


@router.post("/upload", response_model=BatchArtworkSummary)
async def upload_batch_artworks(
    files: list[UploadFile] = File(...),
    source_name: str | None = Form(default=None),
    metadata_json: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchArtworkSummary:
    if not files:
        raise HTTPException(status_code=422, detail="at least one artwork file is required")
    metadata = _parse_metadata_json(metadata_json)
    batch = service.create_batch(db, source_name=source_name, metadata=metadata)
    for file in files:
        data = await file.read()
        filename = file.filename or "artwork.bin"
        artwork_id = new_artwork_id()
        content = data.decode("utf-8", errors="ignore")
        report = preflight_artwork(filename, content, file.content_type)
        checksum = checksum_bytes(data)
        storage_key = save_artwork_bytes(artwork_id, filename, data)
        store.artworks[artwork_id] = {
            "artwork_id": artwork_id,
            "filename": filename,
            "content_type": file.content_type,
            "checksum": checksum,
            "content": content,
            "source_format": report.source_format,
            "storage_key": storage_key,
        }
        store.preflight_reports[artwork_id] = report
        repository.create_artwork(
            db,
            artwork_id=artwork_id,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            checksum=checksum,
            source_format=report.source_format,
            storage_key=storage_key,
            preflight_report=report,
        )
        service.create_item(
            db,
            batch_id=batch.batch_id,
            artwork_id=artwork_id,
            filename=filename,
            content_type=file.content_type,
            checksum=checksum,
            source_format=report.source_format,
            preflight_report=report,
            quantity=int(metadata.get("default_quantity", 1000)),
        )
    summary = service.summary(db, batch.batch_id)
    repository.log_operation(
        db,
        action="batch_artwork.upload",
        target_type="batch_upload",
        target_id=batch.batch_id,
        actor_id=current_user.user_id,
        payload={
            "source_name": source_name,
            "item_count": summary.batch.item_count,
            "format_counts": summary.format_counts,
        },
    )
    return summary


@router.post("/{batch_id}/preflight", response_model=BatchArtworkSummary)
def preflight_batch_artworks(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchArtworkSummary:
    try:
        summary = service.preflight_batch(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="batch_artwork.preflight",
        target_type="batch_upload",
        target_id=batch_id,
        actor_id=current_user.user_id,
        payload=summary.status_counts,
    )
    return summary


@router.post("/{batch_id}/parse", response_model=BatchArtworkSummary)
def parse_batch_artworks(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchArtworkSummary:
    try:
        summary = service.parse_batch(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="batch_artwork.parse",
        target_type="batch_upload",
        target_id=batch_id,
        actor_id=current_user.user_id,
        payload={
            "status_counts": summary.status_counts,
            "class_counts": summary.class_counts,
        },
    )
    return summary


@router.post("/{batch_id}/retry-failed", response_model=BatchArtworkSummary)
def retry_failed_batch_artworks(
    batch_id: str,
    request: BatchArtworkRetryRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("batch:write")),
) -> BatchArtworkSummary:
    try:
        summary = service.retry_failed_items(db, batch_id, item_ids=request.item_ids if request else None)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="batch_artwork.retry_failed",
        target_type="batch_upload",
        target_id=batch_id,
        actor_id=current_user.user_id,
        payload={
            "item_ids": request.item_ids if request else None,
            "status_counts": summary.status_counts,
            "class_counts": summary.class_counts,
        },
    )
    return summary


@router.get("/{batch_id}/summary", response_model=BatchArtworkSummary)
def get_batch_artwork_summary(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> BatchArtworkSummary:
    try:
        return service.summary(db, batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _parse_metadata_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"metadata_json is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="metadata_json must be a JSON object")
    return payload
