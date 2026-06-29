from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import CurrentUser
from app.services import repository
from app.services.security import require_permission

router = APIRouter()


@router.get("")
def list_operation_logs(
    limit: int = Query(default=100, ge=1, le=500),
    action: str | None = Query(default=None, min_length=1, max_length=120),
    target_type: str | None = Query(default=None, min_length=1, max_length=120),
    target_id: str | None = Query(default=None, min_length=1, max_length=120),
    actor_id: str | None = Query(default=None, min_length=1, max_length=64),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("audit:read")),
) -> list[dict]:
    return repository.list_operation_logs(
        db,
        limit=limit,
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
        created_from=created_from,
        created_to=created_to,
    )
