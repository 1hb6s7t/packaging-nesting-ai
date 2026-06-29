from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import (
    ArtworkVersionRead,
    CurrentUser,
    FileConversionJobRead,
    FileConversionJobUpdate,
    FileConversionResultApplyResult,
    FileConversionResultRequest,
    FileConversionSlaCheckRequest,
    FileConversionSlaCheckResult,
    FileConversionSubmitRequest,
    FileConversionSubmitResult,
    PreflightReport,
    PreflightRequest,
)
from app.services.artworks import (
    checksum_bytes,
    new_artwork_id,
    parse_vector_polygons,
    preflight_artwork,
    save_artwork_bytes,
    save_polygon_json,
)
from app.services import repository
from app.services.file_conversion import (
    CONVERSION_CALLBACK_TOKEN_HEADER,
    apply_authenticated_conversion_callback,
    apply_conversion_result,
    check_file_conversion_sla,
    submit_external_conversion_job,
)
from app.services.preview import _polygon_points
from app.services.security import get_current_user, require_permission
from app.services.store import store

router = APIRouter()

DIRECT_PARSE_FORMATS = {"svg", "dxf"}


@router.post("/upload")
async def upload_artwork(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> dict:
    data = await file.read()
    artwork_id = new_artwork_id()
    content = data.decode("utf-8", errors="ignore")
    report = preflight_artwork(file.filename or artwork_id, content, file.content_type)
    storage_key = save_artwork_bytes(artwork_id, file.filename or f"{artwork_id}.bin", data)
    store.artworks[artwork_id] = {
        "artwork_id": artwork_id,
        "filename": file.filename,
        "content_type": file.content_type,
        "checksum": checksum_bytes(data),
        "content": content,
        "source_format": report.source_format,
        "storage_key": storage_key,
    }
    store.preflight_reports[artwork_id] = report
    repository.create_artwork(
        db,
        artwork_id=artwork_id,
        filename=file.filename or f"{artwork_id}.bin",
        content_type=file.content_type or "application/octet-stream",
        checksum=checksum_bytes(data),
        source_format=report.source_format,
        storage_key=storage_key,
        preflight_report=report,
    )
    repository.log_operation(
        db,
        action="artwork.upload",
        target_type="artwork_file",
        target_id=artwork_id,
        actor_id=current_user.user_id,
        payload={"filename": file.filename, "source_format": report.source_format, "storage_key": storage_key},
    )
    return {"artwork_id": artwork_id, "preflight_report": report.model_dump(mode="json")}


@router.post("/preflight", response_model=PreflightReport)
def preflight(payload: PreflightRequest) -> PreflightReport:
    return preflight_artwork(payload.filename, payload.content, payload.content_type)


@router.get("")
def list_artworks(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict]:
    rows = repository.list_artworks(db)
    if rows:
        return rows
    return [{k: v for k, v in value.items() if k != "content"} for value in store.artworks.values()]


@router.get("/conversion-jobs", response_model=list[FileConversionJobRead])
def list_conversion_jobs(
    artwork_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> list[FileConversionJobRead]:
    return repository.list_file_conversion_jobs(db, artwork_id=artwork_id, status=status, limit=limit)


@router.post("/conversion-jobs/sla/check", response_model=FileConversionSlaCheckResult)
def check_conversion_sla(
    payload: FileConversionSlaCheckRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionSlaCheckResult:
    result = check_file_conversion_sla(db, request=payload)
    repository.log_operation(
        db,
        action="artwork.conversion_job.sla_check",
        target_type="file_conversion_job",
        target_id="sla_check",
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.get("/conversion-jobs/{job_id}", response_model=FileConversionJobRead)
def get_conversion_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionJobRead:
    job = repository.get_file_conversion_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    return job


@router.patch("/conversion-jobs/{job_id}", response_model=FileConversionJobRead)
def update_conversion_job(
    job_id: str,
    payload: FileConversionJobUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionJobRead:
    job = repository.update_file_conversion_job(db, job_id, payload)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    repository.log_operation(
        db,
        action="artwork.conversion_job.update",
        target_type="file_conversion_job",
        target_id=job.id,
        actor_id=current_user.user_id,
        payload=job.model_dump(mode="json"),
    )
    return job


@router.post("/conversion-jobs/{job_id}/submit", response_model=FileConversionSubmitResult)
def submit_conversion_job(
    job_id: str,
    payload: FileConversionSubmitRequest | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionSubmitResult:
    try:
        result = submit_external_conversion_job(db, job_id, settings=get_settings(), request=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="artwork.conversion_job.submit",
        target_type="file_conversion_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.post("/conversion-jobs/{job_id}/callback", response_model=FileConversionResultApplyResult)
def apply_conversion_job_callback(
    job_id: str,
    payload: FileConversionResultRequest,
    x_conversion_callback_token: str | None = Header(default=None, alias=CONVERSION_CALLBACK_TOKEN_HEADER),
    db: Session = Depends(get_db),
) -> FileConversionResultApplyResult:
    try:
        result = apply_authenticated_conversion_callback(
            db,
            job_id,
            payload,
            callback_token=x_conversion_callback_token,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="artwork.conversion_job.callback",
        target_type="file_conversion_job",
        target_id=job_id,
        actor_id="external_conversion_service",
        payload=result.model_dump(mode="json"),
    )
    return result


@router.post("/conversion-jobs/{job_id}/result", response_model=FileConversionResultApplyResult)
def apply_conversion_job_result(
    job_id: str,
    payload: FileConversionResultRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionResultApplyResult:
    try:
        result = apply_conversion_result(db, job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="artwork.conversion_job.result",
        target_type="file_conversion_job",
        target_id=job_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.get("/{artwork_id}")
def get_artwork(
    artwork_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    artwork = repository.get_artwork_meta(db, artwork_id) or store.artworks.get(artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail="artwork not found")
    return {k: v for k, v in artwork.items() if k != "content"}


@router.get("/{artwork_id}/versions", response_model=list[ArtworkVersionRead])
def list_artwork_versions(
    artwork_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> list[ArtworkVersionRead]:
    if repository.get_artwork_meta(db, artwork_id) is None:
        raise HTTPException(status_code=404, detail="artwork not found")
    return repository.list_artwork_versions(db, artwork_id)


@router.post("/{artwork_id}/preflight", response_model=PreflightReport)
def run_preflight(
    artwork_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> PreflightReport:
    report = repository.get_preflight_report(db, artwork_id) or store.preflight_reports.get(artwork_id)
    if not report:
        raise HTTPException(status_code=404, detail="artwork not found")
    return report


@router.post("/{artwork_id}/convert", response_model=FileConversionJobRead)
def convert_artwork(
    artwork_id: str,
    target_format: str = "svg",
    submit_external: bool = True,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> FileConversionJobRead:
    artwork = repository.get_artwork_meta(db, artwork_id) or store.artworks.get(artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail="artwork not found")
    source_format = str(artwork.get("source_format") or "unknown").lower()
    normalized_target = target_format.lower().strip() or "svg"
    if normalized_target not in DIRECT_PARSE_FORMATS:
        raise HTTPException(status_code=422, detail="target_format must be svg or dxf")
    if source_format in DIRECT_PARSE_FORMATS:
        status = "skipped"
        log = f"{source_format.upper()} is directly parseable; conversion to {normalized_target.upper()} was skipped."
    elif submit_external and get_settings().external_conversion_service_url:
        status = "queued"
        log = f"{source_format.upper()} queued for external conversion to {normalized_target.upper()}."
    else:
        status = "manual_required"
        log = (
            f"{source_format.upper()} requires an external conversion service or manual export to "
            f"{normalized_target.upper()}; original file is archived and not converted in core service."
        )
    job = repository.create_file_conversion_job(
        db,
        artwork_id=artwork_id,
        source_format=source_format,
        target_format=normalized_target,
        status=status,
        log=log,
    )
    repository.log_operation(
        db,
        action="artwork.conversion_job.create",
        target_type="file_conversion_job",
        target_id=job.id,
        actor_id=current_user.user_id,
        payload=job.model_dump(mode="json"),
    )
    if status == "queued":
        submit_result = submit_external_conversion_job(db, job.id, settings=get_settings(), request=None)
        repository.log_operation(
            db,
            action="artwork.conversion_job.submit",
            target_type="file_conversion_job",
            target_id=job.id,
            actor_id=current_user.user_id,
            payload=submit_result.model_dump(mode="json"),
        )
        job = submit_result.job
    return job


@router.post("/{artwork_id}/parse-polygon")
def parse_polygon(
    artwork_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("artworks:write")),
) -> dict:
    artwork = store.artworks.get(artwork_id) or repository.get_artwork_meta(db, artwork_id)
    if not artwork:
        raise HTTPException(status_code=404, detail="artwork not found")
    if artwork["source_format"] not in {"svg", "dxf"}:
        raise HTTPException(status_code=422, detail="MVP parser currently supports SVG/DXF geometry")
    try:
        content = artwork.get("content") or repository.load_artwork_content(db, artwork_id)
        if content is None:
            raise ValueError("original artwork content is missing from storage")
        polygons = parse_vector_polygons(content, artwork["source_format"], artwork_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.polygons[artwork_id] = polygons
    polygon_storage_key = save_polygon_json(artwork_id, polygons)
    repository.save_polygons(db, artwork_id, polygons)
    store.artworks.setdefault(artwork_id, {**artwork, "artwork_id": artwork_id})["polygon_storage_key"] = polygon_storage_key
    repository.log_operation(
        db,
        action="artwork.parse_polygon",
        target_type="artwork_file",
        target_id=artwork_id,
        actor_id=current_user.user_id,
        payload={"polygon_count": len(polygons), "polygon_storage_key": polygon_storage_key},
    )
    return {
        "artwork_id": artwork_id,
        "polygon_storage_key": polygon_storage_key,
        "polygons": [polygon.model_dump(mode="json") for polygon in polygons],
    }


@router.get("/{artwork_id}/preview")
def preview_artwork(
    artwork_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    polygons = store.polygons.get(artwork_id) or repository.get_polygons(db, artwork_id)
    if not polygons:
        raise HTTPException(status_code=404, detail="polygon asset not found; run parse-polygon first")
    max_x = max(poly.bbox.max_x for poly in polygons if poly.bbox)
    max_y = max(poly.bbox.max_y for poly in polygons if poly.bbox)
    body = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max_x + 10} {max_y + 10}">']
    for poly in polygons:
        body.append(f'<polygon points="{_polygon_points(poly.outer)}" fill="#dbeafe" stroke="#334155"/>')
    body.append("</svg>")
    return Response("\n".join(body), media_type="image/svg+xml")
