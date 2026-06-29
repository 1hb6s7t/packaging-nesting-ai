from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import CurrentUser, RuleExecutionLogRead, RuleSetCreate, RuleSetRead
from app.services import repository
from app.services.security import require_permission

router = APIRouter()


@router.get("/sets", response_model=list[RuleSetRead])
def list_rule_sets(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rules:manage")),
) -> list[RuleSetRead]:
    return repository.list_rule_sets(db)


@router.get("/sets/active", response_model=RuleSetRead)
def get_active_rule_set(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rules:manage")),
) -> RuleSetRead:
    return repository.get_active_rule_set(db)


@router.post("/sets", response_model=RuleSetRead)
def create_rule_set(
    payload: RuleSetCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rules:manage")),
) -> RuleSetRead:
    try:
        rule_set = repository.create_rule_set(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="rules.rule_set.create",
        target_type="rule_set",
        target_id=rule_set.id,
        actor_id=current_user.user_id,
        payload={"name": rule_set.name, "version": rule_set.version, "is_active": rule_set.is_active},
    )
    return rule_set


@router.post("/sets/{rule_set_id}/activate", response_model=RuleSetRead)
def activate_rule_set(
    rule_set_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rules:manage")),
) -> RuleSetRead:
    rule_set = repository.activate_rule_set(db, rule_set_id)
    if rule_set is None:
        raise HTTPException(status_code=404, detail="rule set not found")
    repository.log_operation(
        db,
        action="rules.rule_set.activate",
        target_type="rule_set",
        target_id=rule_set.id,
        actor_id=current_user.user_id,
        payload={"name": rule_set.name, "version": rule_set.version},
    )
    return rule_set


@router.get("/execution-logs", response_model=list[RuleExecutionLogRead])
def list_rule_execution_logs(
    rule_set_id: str | None = None,
    order_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rules:manage")),
) -> list[RuleExecutionLogRead]:
    return repository.list_rule_execution_logs(db, rule_set_id=rule_set_id, order_id=order_id, limit=limit)
