from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import (
    CurrentUser,
    PermissionRead,
    RoleCreate,
    RoleRead,
    RoleUpdate,
    UserAccountCreate,
    UserAccountRead,
    UserAccountUpdate,
)
from app.services import repository
from app.services.security import hash_password, require_permission, validate_password_policy

router = APIRouter()


@router.get("/permissions", response_model=list[PermissionRead])
def list_permissions(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> list[PermissionRead]:
    return repository.list_permissions(db)


@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> list[RoleRead]:
    return repository.list_roles(db)


@router.post("/roles", response_model=RoleRead)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> RoleRead:
    try:
        role = repository.create_role(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="rbac.role.create",
        target_type="role",
        target_id=role.id,
        actor_id=current_user.user_id,
        payload=role.model_dump(mode="json"),
    )
    return role


@router.patch("/roles/{role_id}", response_model=RoleRead)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> RoleRead:
    try:
        role = repository.update_role(db, role_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="rbac.role.update",
        target_type="role",
        target_id=role.id,
        actor_id=current_user.user_id,
        payload=role.model_dump(mode="json"),
    )
    return role


@router.get("/users", response_model=list[UserAccountRead])
def list_users(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> list[UserAccountRead]:
    return repository.list_user_accounts(db)


@router.post("/users", response_model=UserAccountRead)
def create_user(
    payload: UserAccountCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> UserAccountRead:
    try:
        validate_password_policy(payload.password)
        user = repository.create_user_account(db, payload, hash_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="rbac.user.create",
        target_type="user_account",
        target_id=user.id,
        actor_id=current_user.user_id,
        payload={"email": user.email, "roles": [role.name for role in user.roles], "is_active": user.is_active},
    )
    return user


@router.patch("/users/{user_id}", response_model=UserAccountRead)
def update_user(
    user_id: str,
    payload: UserAccountUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("rbac:manage")),
) -> UserAccountRead:
    try:
        if payload.password is not None:
            validate_password_policy(payload.password)
        user = repository.update_user_account(db, user_id, payload, hash_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="rbac.user.update",
        target_type="user_account",
        target_id=user.id,
        actor_id=current_user.user_id,
        payload={"email": user.email, "roles": [role.name for role in user.roles], "is_active": user.is_active},
    )
    return user
