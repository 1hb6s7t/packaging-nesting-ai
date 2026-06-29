from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import CurrentUser, SolverRegistryRead, SolverRegistryUpdate
from app.services import repository
from app.services.security import require_permission

router = APIRouter()


@router.get("/registry", response_model=list[SolverRegistryRead])
def list_solver_registry(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solvers:manage")),
) -> list[SolverRegistryRead]:
    return repository.list_solver_registry(db)


@router.patch("/registry/{solver_name}", response_model=SolverRegistryRead)
def update_solver_registry(
    solver_name: str,
    payload: SolverRegistryUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("solvers:manage")),
) -> SolverRegistryRead:
    try:
        solver = repository.update_solver_registry_entry(db, solver_name, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if solver is None:
        raise HTTPException(status_code=404, detail="solver not found")
    repository.log_operation(
        db,
        action="solvers.registry.update",
        target_type="solver_registry",
        target_id=solver.id,
        actor_id=current_user.user_id,
        payload={
            "name": solver.name,
            "version": solver.version,
            "enabled": solver.enabled,
            "license_policy": solver.license_policy,
        },
    )
    return solver
