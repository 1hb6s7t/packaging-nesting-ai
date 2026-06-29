from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domain.schemas import CurrentUser, OrderImportRequest, OrderImportResult, ProductionOrder
from app.services.orders import parse_order_file
from app.services import repository
from app.services.rules import evaluate_order
from app.services.security import get_current_user, require_permission
from app.services.store import store

router = APIRouter()


@router.post("/import", response_model=OrderImportResult)
def import_orders(
    payload: OrderImportRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("orders:write")),
) -> OrderImportResult:
    imported: list[ProductionOrder] = []
    for item in payload.orders:
        order = ProductionOrder.model_validate(item.model_dump())
        store.orders[order.order_id] = order
        repository.upsert_order(db, order)
        imported.append(order)
    repository.log_operation(
        db,
        action="orders.import_json",
        target_type="production_order",
        actor_id=current_user.user_id,
        payload={"imported_count": len(imported), "order_ids": [order.order_id for order in imported]},
    )
    return OrderImportResult(imported_count=len(imported), orders=imported)


@router.post("/import-file", response_model=OrderImportResult)
async def import_order_file(
    file: UploadFile,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("orders:write")),
) -> OrderImportResult:
    data = await file.read()
    try:
        imported = parse_order_file(file.filename or "orders.csv", data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for order in imported:
        store.orders[order.order_id] = order
        repository.upsert_order(db, order)
    repository.log_operation(
        db,
        action="orders.import_file",
        target_type="production_order",
        actor_id=current_user.user_id,
        payload={"filename": file.filename, "imported_count": len(imported), "order_ids": [order.order_id for order in imported]},
    )
    return OrderImportResult(imported_count=len(imported), orders=imported)


@router.get("", response_model=list[ProductionOrder])
def list_orders(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> list[ProductionOrder]:
    orders = repository.list_orders(db)
    if orders:
        return orders
    return list(store.orders.values())


@router.get("/{order_id}", response_model=ProductionOrder)
def get_order(
    order_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ProductionOrder:
    return _get_order(order_id, db)


def _get_order(order_id: str, db: Session) -> ProductionOrder:
    order = repository.get_order(db, order_id) or store.orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@router.post("/filter-candidates", response_model=list[ProductionOrder])
def filter_candidate_orders(
    main_order_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("orders:write")),
) -> list[ProductionOrder]:
    main_order = _get_order(main_order_id, db)
    rule_set = repository.get_active_rule_set(db)
    orders = repository.list_orders(db) or list(store.orders.values())
    accepted: list[ProductionOrder] = []
    for order in orders:
        decision = evaluate_order(main_order, order, rule_set.definition)
        repository.log_rule_execution(
            db,
            rule_set_id=rule_set.id,
            order_id=order.order_id,
            result={
                "main_order_id": main_order.order_id,
                "candidate_order_id": order.order_id,
                "decision": decision.model_dump(mode="json"),
            },
        )
        if decision.accepted:
            accepted.append(order.model_copy(update={"priority_score": decision.priority_score}))
    return sorted(accepted, key=lambda item: item.priority_score, reverse=True)


@router.post("/{order_id}/score")
def score_order(
    order_id: str,
    main_order_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("orders:write")),
) -> dict:
    order = _get_order(order_id, db)
    main_order = _get_order(main_order_id, db)
    rule_set = repository.get_active_rule_set(db)
    decision = evaluate_order(main_order, order, rule_set.definition)
    result = decision.model_dump(mode="json")
    repository.log_rule_execution(
        db,
        rule_set_id=rule_set.id,
        order_id=order.order_id,
        result={"main_order_id": main_order.order_id, "candidate_order_id": order.order_id, "decision": result},
    )
    return result
