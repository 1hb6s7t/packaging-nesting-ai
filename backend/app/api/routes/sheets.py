from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import CurrentUser, SheetSpec
from app.services import repository
from app.services.security import get_current_user, require_permission
from app.services.store import store

router = APIRouter()


@router.post("", response_model=SheetSpec)
def create_sheet(
    sheet: SheetSpec,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("sheets:write")),
) -> SheetSpec:
    store.sheets[sheet.sheet_id] = sheet
    repository.upsert_sheet(db, sheet)
    repository.log_operation(
        db,
        action="sheet.create_or_update",
        target_type="sheet_spec",
        target_id=sheet.sheet_id,
        actor_id=current_user.user_id,
        payload=sheet.model_dump(mode="json"),
    )
    return sheet


@router.get("", response_model=list[SheetSpec])
def list_sheets(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[SheetSpec]:
    sheets = repository.list_sheets(db)
    if sheets:
        return sheets
    return list(store.sheets.values())


@router.get("/{sheet_id}", response_model=SheetSpec)
def get_sheet(
    sheet_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SheetSpec:
    sheet = repository.get_sheet(db, sheet_id) or store.sheets.get(sheet_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="sheet not found")
    return sheet


@router.put("/{sheet_id}", response_model=SheetSpec)
def update_sheet(
    sheet_id: str,
    sheet: SheetSpec,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("sheets:write")),
) -> SheetSpec:
    if sheet_id != sheet.sheet_id:
        raise HTTPException(status_code=400, detail="sheet_id mismatch")
    store.sheets[sheet_id] = sheet
    repository.upsert_sheet(db, sheet)
    repository.log_operation(
        db,
        action="sheet.update",
        target_type="sheet_spec",
        target_id=sheet.sheet_id,
        actor_id=current_user.user_id,
        payload=sheet.model_dump(mode="json"),
    )
    return sheet
