from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import ApiMetricsRead, CurrentUser
from app.services import api_metrics
from app.services.health import build_readiness_report
from app.services.security import require_permission

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "packaging-nesting-api"}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> JSONResponse:
    report = build_readiness_report(db)
    return JSONResponse(status_code=200 if report["status"] == "ok" else 503, content=report)


@router.get("/metrics", response_model=ApiMetricsRead)
def metrics(current_user: CurrentUser = Depends(require_permission("audit:read"))) -> ApiMetricsRead:
    return ApiMetricsRead.model_validate(api_metrics.snapshot_api_metrics())
