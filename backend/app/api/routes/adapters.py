from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db import models as dbm
from app.domain.schemas import (
    AdapterConfigCreate,
    AdapterConfigRead,
    AdapterConnectionTestResult,
    AdapterDictionarySignoffRequest,
    AdapterDictionarySignoffResult,
    AdapterFieldAcceptanceResult,
    AdapterReadinessReport,
    AdapterStatusRead,
    AdapterWritebackRequest,
    CurrentUser,
    DeliveryConfirmationRead,
    ExternalSystemCreate,
    ExternalSystemRead,
    ExternalSystemUpdate,
    InventorySnapshotRead,
    ProductionScheduleEntryRead,
    SyncTaskRead,
    WritebackLogRead,
)
from app.services import repository
from app.services.adapters import (
    build_adapter_readiness_report,
    evaluate_adapter_field_acceptance,
    signoff_adapter_dictionary,
    sync_crm_orders,
    sync_external_records,
    writeback_adapter_result,
)
from app.services.security import require_permission

router = APIRouter()


@router.get("/status", response_model=AdapterStatusRead)
def adapter_status(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterStatusRead:
    return repository.build_adapter_status(db)


@router.get("/readiness", response_model=AdapterReadinessReport)
def adapter_readiness(
    required_system_types: str = Query(default="crm,mes,erp", max_length=120),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterReadinessReport:
    required = [item.strip() for item in required_system_types.split(",") if item.strip()]
    report = build_adapter_readiness_report(db, required_system_types=required)
    repository.log_operation(
        db,
        action="adapters.readiness.check",
        target_type="adapter_readiness",
        target_id=",".join(report.required_system_types),
        actor_id=current_user.user_id,
        payload=report.model_dump(mode="json"),
    )
    return report


@router.get("/systems", response_model=list[ExternalSystemRead])
def list_external_systems(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[ExternalSystemRead]:
    return repository.list_external_systems(db)


@router.post("/systems", response_model=ExternalSystemRead)
def create_external_system(
    payload: ExternalSystemCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> ExternalSystemRead:
    system = repository.create_external_system(db, payload)
    repository.log_operation(
        db,
        action="adapters.external_system.create",
        target_type="external_system",
        target_id=system.id,
        actor_id=current_user.user_id,
        payload=system.model_dump(mode="json"),
    )
    return system


@router.patch("/systems/{external_system_id}", response_model=ExternalSystemRead)
def update_external_system(
    external_system_id: str,
    payload: ExternalSystemUpdate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> ExternalSystemRead:
    system = repository.update_external_system(db, external_system_id, payload)
    if system is None:
        raise HTTPException(status_code=404, detail="external system not found")
    repository.log_operation(
        db,
        action="adapters.external_system.update",
        target_type="external_system",
        target_id=system.id,
        actor_id=current_user.user_id,
        payload=system.model_dump(mode="json"),
    )
    return system


@router.get("/configs", response_model=list[AdapterConfigRead])
def list_adapter_configs(
    external_system_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[AdapterConfigRead]:
    return repository.list_adapter_configs(db, external_system_id=external_system_id)


@router.post("/systems/{external_system_id}/configs", response_model=AdapterConfigRead)
def create_adapter_config(
    external_system_id: str,
    payload: AdapterConfigCreate,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterConfigRead:
    try:
        config = repository.create_adapter_config(db, external_system_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="external system not found")
    repository.log_operation(
        db,
        action="adapters.config.create",
        target_type="adapter_config",
        target_id=config.id,
        actor_id=current_user.user_id,
        payload={
            "external_system_id": config.external_system_id,
            "adapter_type": config.adapter_type,
            "version": config.version,
            "is_active": config.is_active,
        },
    )
    return config


@router.post("/configs/{config_id}/activate", response_model=AdapterConfigRead)
def activate_adapter_config(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterConfigRead:
    try:
        config = repository.activate_adapter_config(db, config_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if config is None:
        raise HTTPException(status_code=404, detail="adapter config not found")
    repository.log_operation(
        db,
        action="adapters.config.activate",
        target_type="adapter_config",
        target_id=config.id,
        actor_id=current_user.user_id,
        payload={"external_system_id": config.external_system_id, "adapter_type": config.adapter_type, "version": config.version},
    )
    return config


@router.post("/configs/{config_id}/test", response_model=AdapterConnectionTestResult)
def test_adapter_connection(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterConnectionTestResult:
    result = repository.test_adapter_connection(db, config_id)
    if result is None:
        raise HTTPException(status_code=404, detail="adapter config not found")
    repository.log_operation(
        db,
        action="adapters.config.test",
        target_type="adapter_config",
        target_id=config_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.post("/configs/{config_id}/field-acceptance", response_model=AdapterFieldAcceptanceResult)
def evaluate_adapter_fields(
    config_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterFieldAcceptanceResult:
    config = db.get(dbm.AdapterConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="adapter config not found")
    system = db.get(dbm.ExternalSystem, config.external_system_id)
    if system is None:
        raise HTTPException(status_code=409, detail="external system not found")
    result = AdapterFieldAcceptanceResult.model_validate(
        evaluate_adapter_field_acceptance(db=db, system=system, config=config)
    )
    repository.log_operation(
        db,
        action="adapters.config.field_acceptance",
        target_type="adapter_config",
        target_id=config_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.post("/configs/{config_id}/dictionary-signoff", response_model=AdapterDictionarySignoffResult)
def signoff_adapter_config_dictionary(
    config_id: str,
    payload: AdapterDictionarySignoffRequest,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> AdapterDictionarySignoffResult:
    config = db.get(dbm.AdapterConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="adapter config not found")
    system = db.get(dbm.ExternalSystem, config.external_system_id)
    if system is None:
        raise HTTPException(status_code=409, detail="external system not found")
    try:
        result = signoff_adapter_dictionary(
            db,
            system=system,
            config=config,
            request=payload,
            actor_id=current_user.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="adapters.config.dictionary_signoff",
        target_type="adapter_config",
        target_id=config_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.get("/production-schedules", response_model=list[ProductionScheduleEntryRead])
def list_production_schedules(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[ProductionScheduleEntryRead]:
    return repository.list_production_schedule_entries(db, external_system_id=external_system_id, limit=limit)


@router.get("/inventory-snapshots", response_model=list[InventorySnapshotRead])
def list_inventory_snapshots(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[InventorySnapshotRead]:
    return repository.list_inventory_snapshots(db, external_system_id=external_system_id, limit=limit)


@router.get("/delivery-confirmations", response_model=list[DeliveryConfirmationRead])
def list_delivery_confirmations(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[DeliveryConfirmationRead]:
    return repository.list_delivery_confirmations(db, external_system_id=external_system_id, limit=limit)


@router.get("/sync-tasks", response_model=list[SyncTaskRead])
def list_sync_tasks(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[SyncTaskRead]:
    return repository.list_sync_tasks(db, external_system_id=external_system_id, limit=limit)


@router.get("/sync-tasks/retry-queue", response_model=list[SyncTaskRead])
def list_sync_retry_queue(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[SyncTaskRead]:
    return repository.list_sync_retry_queue(db, external_system_id=external_system_id, limit=limit)


@router.post("/sync-tasks/{task_id}/retry", response_model=SyncTaskRead)
def retry_sync_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> SyncTaskRead:
    source_task = repository.get_sync_task_row(db, task_id)
    if source_task is None:
        raise HTTPException(status_code=404, detail="sync task not found")
    if source_task.status != "failed":
        raise HTTPException(status_code=409, detail="only failed sync tasks can be retried")
    if source_task.task_type not in {"crm_sync", "mes_sync", "erp_sync"}:
        raise HTTPException(status_code=400, detail=f"retry is not supported for {source_task.task_type}")

    system = db.get(dbm.ExternalSystem, source_task.external_system_id)
    payload = source_task.payload or {}
    config_id = payload.get("adapter_config_id")
    config = db.get(dbm.AdapterConfig, str(config_id)) if config_id else None
    if system is None or config is None:
        raise HTTPException(status_code=409, detail="source system or adapter config is no longer available")

    dry_run = payload.get("dry_run") if isinstance(payload.get("dry_run"), bool) else None
    if source_task.task_type == "crm_sync":
        retry_payload = sync_crm_orders(db, system=system, config=config, dry_run_override=dry_run)
    else:
        retry_system_type = source_task.task_type.removesuffix("_sync")
        retry_payload = sync_external_records(
            db,
            system=system,
            config=config,
            system_type=retry_system_type,
            dry_run_override=dry_run,
        )
    root_task_id = payload.get("root_task_id") or payload.get("retry_of_task_id") or source_task.id
    retry_payload["retry_of_task_id"] = source_task.id
    retry_payload["root_task_id"] = root_task_id
    retry_payload["attempt"] = int(payload.get("attempt") or 1) + 1
    task = repository.create_sync_task(
        db,
        external_system_id=system.id,
        task_type=source_task.task_type,
        status=retry_payload["status"],
        payload=retry_payload,
    )
    repository.attach_domain_records_to_sync_task(db, task.id, retry_payload)
    repository.log_operation(
        db,
        action="adapters.sync_task.retry",
        target_type="sync_task",
        target_id=task.id,
        actor_id=current_user.user_id,
        payload=task.model_dump(mode="json"),
    )
    return task


@router.get("/writeback-logs", response_model=list[WritebackLogRead])
def list_writeback_logs(
    external_system_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> list[WritebackLogRead]:
    return repository.list_writeback_logs(db, external_system_id=external_system_id, limit=limit)


@router.post("/crm/sync")
def crm_sync(
    dry_run: bool | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> dict:
    active = repository.get_active_adapter_config_for_system_type(db, "crm")
    if active is None:
        return {"status": "not_configured", "message": "No enabled CRM system with an active adapter config"}
    system, config = active
    payload = sync_crm_orders(db, system=system, config=config, dry_run_override=dry_run)
    task = repository.create_sync_task(
        db,
        external_system_id=system.id,
        task_type="crm_sync",
        status=payload["status"],
        payload=payload,
    )
    repository.log_operation(
        db,
        action="adapters.crm.sync",
        target_type="sync_task",
        target_id=task.id,
        actor_id=current_user.user_id,
        payload=task.model_dump(mode="json"),
    )
    return task.model_dump(mode="json")


@router.post("/{system_type}/sync")
def adapter_sync(
    system_type: str,
    dry_run: bool | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> dict:
    if system_type not in {"mes", "erp"}:
        raise HTTPException(status_code=404, detail="adapter sync system type not found")
    active = repository.get_active_adapter_config_for_system_type(db, system_type)
    if active is None:
        return {"status": "not_configured", "message": f"No enabled {system_type.upper()} system with an active adapter config"}
    system, config = active
    payload = sync_external_records(db, system=system, config=config, system_type=system_type, dry_run_override=dry_run)
    task = repository.create_sync_task(
        db,
        external_system_id=system.id,
        task_type=f"{system_type}_sync",
        status=payload["status"],
        payload=payload,
    )
    repository.attach_domain_records_to_sync_task(db, task.id, payload)
    repository.log_operation(
        db,
        action=f"adapters.{system_type}.sync",
        target_type="sync_task",
        target_id=task.id,
        actor_id=current_user.user_id,
        payload=task.model_dump(mode="json"),
    )
    return task.model_dump(mode="json")


@router.post("/crm/writeback")
def crm_writeback(
    payload: AdapterWritebackRequest | None = None,
    target_id: str | None = None,
    dry_run: bool | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> dict:
    return _run_adapter_writeback("crm", payload, target_id, dry_run, db, current_user)


@router.post("/{system_type}/writeback")
def adapter_writeback(
    system_type: str,
    payload: AdapterWritebackRequest | None = None,
    target_id: str | None = None,
    dry_run: bool | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> dict:
    if system_type not in {"crm", "mes", "erp"}:
        raise HTTPException(status_code=404, detail="adapter writeback system type not found")
    return _run_adapter_writeback(system_type, payload, target_id, dry_run, db, current_user)


def _run_adapter_writeback(
    system_type: str,
    request: AdapterWritebackRequest | None,
    target_id: str | None,
    dry_run: bool | None,
    db: Session,
    current_user: CurrentUser,
) -> dict:
    active = repository.get_active_adapter_config_for_system_type(db, system_type)
    final_target_id = request.target_id if request and request.target_id is not None else target_id
    if active is None:
        log = repository.create_writeback_log(
            db,
            external_system_id=None,
            target_id=final_target_id,
            status="skipped",
            payload={"reason": f"No enabled {system_type.upper()} system with an active adapter config", "system_type": system_type},
        )
        return log.model_dump(mode="json")
    system, config = active
    dry_run_override = dry_run if dry_run is not None else (request.dry_run if request else None)
    writeback_payload = writeback_adapter_result(
        db,
        system=system,
        config=config,
        target_id=final_target_id,
        target_type=request.target_type if request else "solution",
        status=request.status if request else "completed",
        payload=request.payload if request else {},
        dry_run_override=dry_run_override,
    )
    log = repository.create_writeback_log(
        db,
        external_system_id=system.id,
        target_id=final_target_id,
        status=writeback_payload["status"],
        payload=writeback_payload,
    )
    repository.log_operation(
        db,
        action=f"adapters.{system_type}.writeback",
        target_type="writeback_log",
        target_id=log.id,
        actor_id=current_user.user_id,
        payload=log.model_dump(mode="json"),
    )
    return log.model_dump(mode="json")


@router.post("/mes/push-job")
def mes_push_job(
    job_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("integrations:write")),
) -> dict:
    active = repository.get_active_adapter_config_for_system_type(db, "mes")
    if active is None:
        return {"status": "not_configured", "message": "No enabled MES system with an active adapter config"}
    system, config = active
    task = repository.create_sync_task(
        db,
        external_system_id=system.id,
        task_type="mes_push_job",
        status="completed",
        payload={"job_id": job_id, "adapter_config_id": config.id, "adapter_type": config.adapter_type, "dry_run": True},
    )
    repository.log_operation(
        db,
        action="adapters.mes.push_job",
        target_type="sync_task",
        target_id=task.id,
        actor_id=current_user.user_id,
        payload=task.model_dump(mode="json"),
    )
    return task.model_dump(mode="json")
