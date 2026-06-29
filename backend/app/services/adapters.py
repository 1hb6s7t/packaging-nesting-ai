from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as dbm
from app.domain import schemas
from app.domain.schemas import ProductionOrder, ProductionOrderIn
from app.services import repository
from app.services.confirmations import adapter_dictionary_signoff_confirmation, check_confirmation
from app.services.orders import rows_to_orders
from app.services.store import store


ORDER_FIELDS = set(ProductionOrderIn.model_fields)
HTTP_SOURCE_VALUES = {"http", "api", "remote"}
REQUIRED_INTEGRATION_NOTIFICATION_EVENTS = (
    "work_task.failure_high",
    "work_task.stale_running",
    "procurement.material_shortage",
    "production.schedule_blocked",
    "production.delivery_incomplete",
)
WRITEBACK_CONTEXT_FIELDS = {
    "target_id",
    "target_type",
    "status",
    "internal_status",
    "confirmed_at",
    "source",
    "adapter_config_id",
    "adapter_version",
    "payload",
}
DOMAIN_TARGET_ALIASES = {
    "production_schedule": "production_schedule",
    "production_schedule_entry": "production_schedule",
    "schedule": "production_schedule",
    "work_order": "production_schedule",
    "job_schedule": "production_schedule",
    "inventory": "inventory_snapshot",
    "inventory_snapshot": "inventory_snapshot",
    "stock": "inventory_snapshot",
    "stock_snapshot": "inventory_snapshot",
    "delivery": "delivery_confirmation",
    "delivery_confirmation": "delivery_confirmation",
    "shipment": "delivery_confirmation",
    "shipment_confirmation": "delivery_confirmation",
}
REQUIRED_CRM_ORDER_FIELDS = ("order_id", "product_name", "quantity", "material", "thickness")
REQUIRED_DOMAIN_FIELDS = {
    "production_schedule": ("external_id", "status"),
    "inventory_snapshot": ("external_id", "material_code", "available_qty"),
    "delivery_confirmation": ("external_id", "order_id", "status"),
}
RECOMMENDED_DOMAIN_FIELDS = {
    "production_schedule": ("order_id|job_id",),
    "inventory_snapshot": ("status", "unit"),
    "delivery_confirmation": ("quantity", "delivered_at"),
}


@dataclass
class RecordPageLoad:
    pages: list[list[dict[str, Any]]]
    source: str
    request_count: int = 0
    remote_status_codes: list[int] = field(default_factory=list)
    incremental: bool = False
    cursor_in: Any | None = None
    next_cursor: Any | None = None


def evaluate_adapter_field_acceptance(
    *,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
    db: Session | None = None,
) -> dict[str, Any]:
    raw_config = config.config or {}
    pages = _configured_record_pages(raw_config)
    records = [record for page in pages for record in page]
    checks: list[dict[str, Any]] = []
    if not records:
        checks.append(
            _acceptance_check(
                scope="sample",
                field="sample_records",
                status="failed",
                required=True,
                message="provide pages, records, or sample_records for field acceptance",
            )
        )

    domain_target = _configured_domain_target(raw_config)
    if system.system_type == "crm":
        checks.extend(_evaluate_crm_order_acceptance(raw_config, records))
    elif system.system_type in {"mes", "erp"}:
        checks.extend(_evaluate_domain_record_acceptance(raw_config, records, domain_target))
    else:
        checks.append(
            _acceptance_check(
                scope="sample",
                field="system_type",
                status="warning",
                message=f"field acceptance has no domain profile for {system.system_type}",
            )
        )
    checks.extend(_evaluate_mapping_paths(raw_config, records))
    checks.extend(_evaluate_status_dictionary(raw_config, records))
    checks.extend(_evaluate_writeback_acceptance(raw_config))
    checks.extend(_evaluate_organization_acceptance(db, raw_config))

    required_missing_count = sum(1 for check in checks if check["required"] and check["status"] == "failed")
    unresolved_mapping_count = sum(1 for check in checks if check["scope"] == "mapping" and check["status"] != "passed")
    unmapped_status_count = sum(1 for check in checks if check["scope"] == "status" and check["status"] != "passed")
    if required_missing_count:
        status = "failed"
    elif any(check["status"] == "warning" for check in checks):
        status = "warning"
    else:
        status = "passed"
    message = (
        "field acceptance passed"
        if status == "passed"
        else f"field acceptance {status}: {required_missing_count} required gaps, "
        f"{unresolved_mapping_count} mapping warnings, {unmapped_status_count} status warnings"
    )
    return {
        "config_id": config.id,
        "external_system_id": system.id,
        "system_type": system.system_type,
        "adapter_type": config.adapter_type,
        "adapter_version": config.version,
        "status": status,
        "domain_target": domain_target,
        "sample_count": len(records),
        "required_missing_count": required_missing_count,
        "unresolved_mapping_count": unresolved_mapping_count,
        "unmapped_status_count": unmapped_status_count,
        "checks": checks,
        "message": message,
    }


def signoff_adapter_dictionary(
    db: Session,
    *,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
    request: schemas.AdapterDictionarySignoffRequest,
    actor_id: str,
) -> schemas.AdapterDictionarySignoffResult:
    check_confirmation(request.confirmation, adapter_dictionary_signoff_confirmation(config.id))
    acceptance = schemas.AdapterFieldAcceptanceResult.model_validate(
        evaluate_adapter_field_acceptance(db=db, system=system, config=config)
    )
    if acceptance.required_missing_count:
        raise ValueError("field acceptance must have zero required gaps before dictionary signoff")
    unmapped_statuses = _unmapped_status_values(acceptance)
    accepted_statuses = sorted({str(value) for value in request.accepted_unmapped_statuses})
    accepted_lookup = {value.lower() for value in accepted_statuses}
    unaccepted = [value for value in unmapped_statuses if value.lower() not in accepted_lookup]
    if unaccepted:
        raise ValueError(f"unmapped statuses require explicit acceptance: {', '.join(unaccepted[:10])}")

    now_iso = repository.utc_now().isoformat()
    dictionary_keys = _dictionary_keys(config.config or {})
    signoff = {
        "status": "signed",
        "signed_by": actor_id,
        "signed_at": now_iso,
        "approver_name": request.approver_name,
        "note": request.note,
        "dictionary_keys": dictionary_keys,
        "accepted_unmapped_statuses": accepted_statuses,
        "field_acceptance_status": acceptance.status,
        "field_acceptance_message": acceptance.message,
    }
    repository.update_adapter_config_dictionary_signoff(db, config.id, signoff)
    return schemas.AdapterDictionarySignoffResult(
        config_id=config.id,
        external_system_id=system.id,
        status="signed",
        signed_by=actor_id,
        signed_at=now_iso,
        approver_name=request.approver_name,
        note=request.note,
        dictionary_keys=dictionary_keys,
        accepted_unmapped_statuses=accepted_statuses,
        field_acceptance=acceptance,
    )


def build_adapter_readiness_report(
    db: Session,
    *,
    required_system_types: list[str] | None = None,
) -> schemas.AdapterReadinessReport:
    required_types = _normalize_required_system_types(required_system_types)
    systems = list(db.scalars(select(dbm.ExternalSystem)).all())
    configs = list(db.scalars(select(dbm.AdapterConfig)).all())
    checks: list[schemas.AdapterReadinessCheck] = []

    for system_type in required_types:
        enabled_systems = [system for system in systems if system.system_type == system_type and system.enabled]
        if not enabled_systems:
            checks.append(
                _readiness_check(
                    code="system.enabled",
                    scope=system_type,
                    status="failed",
                    severity="critical",
                    message=f"{system_type.upper()} enabled external system is required",
                    target_type="external_system",
                    evidence={"system_type": system_type},
                )
            )
            continue
        for system in enabled_systems:
            checks.append(
                _readiness_check(
                    code="system.enabled",
                    scope=system_type,
                    status="passed",
                    message=f"{system.name} is enabled",
                    target_type="external_system",
                    target_id=system.id,
                    evidence={"system_type": system.system_type},
                )
            )
            active_configs = [
                config for config in configs if config.external_system_id == system.id and config.is_active
            ]
            if not active_configs:
                checks.append(
                    _readiness_check(
                        code="config.active",
                        scope=system_type,
                        status="failed",
                        severity="critical",
                        message=f"{system.name} has no active adapter config",
                        target_type="external_system",
                        target_id=system.id,
                    )
                )
                continue
            for config in active_configs:
                checks.extend(_adapter_config_readiness_checks(db=db, system=system, config=config))

    checks.extend(_integration_notification_template_checks(db))
    checks.extend(_integration_runtime_health_checks(db))
    failed_count = sum(1 for check in checks if check.status == "failed")
    warning_count = sum(1 for check in checks if check.status == "warning")
    passed_count = sum(1 for check in checks if check.status == "passed")
    if failed_count:
        status = "blocked"
    elif warning_count:
        status = "warning"
    else:
        status = "ready"
    return schemas.AdapterReadinessReport(
        status=status,
        generated_at=repository.utc_now().isoformat(),
        required_system_types=required_types,
        passed_count=passed_count,
        warning_count=warning_count,
        failed_count=failed_count,
        checks=checks,
    )


def _adapter_config_readiness_checks(
    *,
    db: Session,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
) -> list[schemas.AdapterReadinessCheck]:
    raw_config = config.config or {}
    checks = [
        _readiness_check(
            code="config.active",
            scope=system.system_type,
            status="passed",
            message=f"{config.adapter_type} v{config.version} is active",
            target_type="adapter_config",
            target_id=config.id,
            evidence={"adapter_type": config.adapter_type, "version": config.version},
        )
    ]
    checks.append(
        _readiness_check(
            code="config.validation",
            scope=system.system_type,
            status="passed" if config.validation_status == "passed" else "failed",
            severity="info" if config.validation_status == "passed" else "critical",
            message=(
                "adapter config validation passed"
                if config.validation_status == "passed"
                else f"adapter config validation is {config.validation_status}"
            ),
            target_type="adapter_config",
            target_id=config.id,
            evidence={"validation_status": config.validation_status},
        )
    )
    acceptance = schemas.AdapterFieldAcceptanceResult.model_validate(
        evaluate_adapter_field_acceptance(db=db, system=system, config=config)
    )
    if acceptance.required_missing_count:
        acceptance_status = "failed"
        acceptance_severity = "critical"
    elif acceptance.status == "warning":
        acceptance_status = "warning"
        acceptance_severity = "warning"
    else:
        acceptance_status = "passed"
        acceptance_severity = "info"
    checks.append(
        _readiness_check(
            code="field_acceptance",
            scope=system.system_type,
            status=acceptance_status,
            severity=acceptance_severity,
            message=acceptance.message,
            target_type="adapter_config",
            target_id=config.id,
            evidence={
                "sample_count": acceptance.sample_count,
                "required_missing_count": acceptance.required_missing_count,
                "unresolved_mapping_count": acceptance.unresolved_mapping_count,
                "unmapped_status_count": acceptance.unmapped_status_count,
            },
        )
    )
    requires_signoff = repository.adapter_config_requires_dictionary_signoff(raw_config)
    signed = repository.adapter_config_dictionary_signed(raw_config)
    checks.append(
        _readiness_check(
            code="dictionary_signoff",
            scope=system.system_type,
            status="passed" if not requires_signoff or signed else "failed",
            severity="info" if not requires_signoff or signed else "critical",
            message=(
                "dictionary signoff is recorded"
                if signed
                else "dictionary signoff is not required"
                if not requires_signoff
                else "dictionary signoff is required before go-live"
            ),
            target_type="adapter_config",
            target_id=config.id,
            evidence={
                "required": requires_signoff,
                "signed": signed,
                "signed_at": (raw_config.get("dictionary_signoff") or {}).get("signed_at")
                if isinstance(raw_config.get("dictionary_signoff"), dict)
                else None,
            },
        )
    )
    source_production = _uses_http_source(raw_config) and not bool(raw_config.get("dry_run", True))
    checks.append(
        _readiness_check(
            code="source.production_mode",
            scope=system.system_type,
            status="passed" if source_production else "warning",
            severity="info" if source_production else "warning",
            message=(
                "source sync is configured for real HTTP import"
                if source_production
                else "source sync is still mock, manual, or dry-run"
            ),
            target_type="adapter_config",
            target_id=config.id,
            evidence={
                "mode": raw_config.get("mode"),
                "source": raw_config.get("source"),
                "dry_run": raw_config.get("dry_run", True),
            },
        )
    )
    if _uses_http_source(raw_config):
        retry_count = _int_value(raw_config.get("retry_count"), default=0)
        checks.append(
            _readiness_check(
                code="source.retry_policy",
                scope=system.system_type,
                status="passed" if retry_count > 0 else "warning",
                severity="info" if retry_count > 0 else "warning",
                message=(
                    f"source HTTP retry_count is {retry_count}"
                    if retry_count > 0
                    else "source HTTP retry_count is not configured"
                ),
                target_type="adapter_config",
                target_id=config.id,
                evidence={"retry_count": retry_count},
            )
        )
    writeback_config = _writeback_config(raw_config)
    if writeback_config:
        writeback_real = _uses_http_writeback(raw_config, writeback_config) and not _writeback_dry_run(
            raw_config, writeback_config, None
        )
        checks.append(
            _readiness_check(
                code="writeback.production_mode",
                scope=system.system_type,
                status="passed" if writeback_real else "warning",
                severity="info" if writeback_real else "warning",
                message=(
                    "writeback is configured for real HTTP confirmation"
                    if writeback_real
                    else "writeback is still mock, manual, or dry-run"
                ),
                target_type="adapter_config",
                target_id=config.id,
                evidence={
                    "mode": writeback_config.get("mode"),
                    "source": writeback_config.get("source"),
                    "dry_run": _writeback_dry_run(raw_config, writeback_config, None),
                },
            )
        )
    return checks


def _integration_notification_template_checks(db: Session) -> list[schemas.AdapterReadinessCheck]:
    templates = repository.list_message_templates(db, active_only=True, limit=500)
    template_events = {template.event_type for template in templates}
    checks: list[schemas.AdapterReadinessCheck] = []
    for event_type in REQUIRED_INTEGRATION_NOTIFICATION_EVENTS:
        checks.append(
            _readiness_check(
                code="notification.template",
                scope="notifications",
                status="passed" if event_type in template_events else "warning",
                severity="info" if event_type in template_events else "warning",
                message=(
                    f"active notification template exists for {event_type}"
                    if event_type in template_events
                    else f"no active notification template configured for {event_type}; fallback delivery may apply"
                ),
                target_type="message_template",
                target_id=event_type,
                evidence={"event_type": event_type},
            )
        )
    return checks


def _integration_runtime_health_checks(db: Session) -> list[schemas.AdapterReadinessCheck]:
    retry_queue = repository.list_sync_retry_queue(db, limit=100)
    writeback_logs = repository.list_writeback_logs(db, limit=100)
    failed_writebacks = [log for log in writeback_logs if log.status == "failed"]
    return [
        _readiness_check(
            code="sync.retry_queue",
            scope="runtime",
            status="warning" if retry_queue else "passed",
            severity="warning" if retry_queue else "info",
            message=(
                f"{len(retry_queue)} failed sync tasks are waiting for retry"
                if retry_queue
                else "no failed sync tasks are waiting for retry"
            ),
            target_type="sync_task",
            evidence={"retry_count": len(retry_queue)},
        ),
        _readiness_check(
            code="writeback.failures",
            scope="runtime",
            status="warning" if failed_writebacks else "passed",
            severity="warning" if failed_writebacks else "info",
            message=(
                f"{len(failed_writebacks)} recent writebacks failed"
                if failed_writebacks
                else "no recent writeback failures found"
            ),
            target_type="writeback_log",
            evidence={"failed_count": len(failed_writebacks)},
        ),
    ]


def _readiness_check(
    *,
    code: str,
    scope: str,
    status: str,
    message: str,
    severity: str = "info",
    target_type: str | None = None,
    target_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> schemas.AdapterReadinessCheck:
    return schemas.AdapterReadinessCheck(
        code=code,
        scope=scope,
        status=status,
        severity=severity,
        message=message,
        target_type=target_type,
        target_id=target_id,
        evidence=evidence or {},
    )


def _normalize_required_system_types(required_system_types: list[str] | None) -> list[str]:
    values = required_system_types or ["crm", "mes", "erp"]
    normalized: list[str] = []
    for value in values:
        system_type = str(value).strip().lower()
        if system_type and system_type not in normalized:
            normalized.append(system_type)
    return normalized or ["crm", "mes", "erp"]


def _int_value(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sync_crm_orders(
    db: Session,
    *,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
    dry_run_override: bool | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    raw_config = config.config or {}
    dry_run = bool(raw_config.get("dry_run", True)) if dry_run_override is None else dry_run_override
    try:
        loaded = _load_record_pages(raw_config, http_transport=http_transport)
        pages = loaded.pages
        load_error = None
    except Exception as exc:
        loaded = RecordPageLoad(pages=[], source="http" if _uses_http_source(raw_config) else "configured")
        pages = []
        load_error = str(exc)
    mapped_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if load_error:
        errors.append({"page": None, "row": None, "external_id": None, "message": load_error})

    for page_index, records in enumerate(pages, start=1):
        for row_index, record in enumerate(records, start=1):
            try:
                mapped_rows.append(_map_external_record(record, raw_config))
            except Exception as exc:
                errors.append(
                    {
                        "page": page_index,
                        "row": row_index,
                        "external_id": _record_identifier(record),
                        "message": str(exc),
                    }
                )

    orders: list[ProductionOrder] = []
    if mapped_rows:
        try:
            orders = [order.model_copy(update={"source_type": "crm_sync"}) for order in rows_to_orders(mapped_rows)]
        except Exception as exc:
            errors.append({"page": None, "row": None, "external_id": None, "message": str(exc)})

    imported_ids: list[str] = []
    if not dry_run and not errors:
        for order in orders:
            repository.upsert_order(db, order)
            store.orders[order.order_id] = order
            imported_ids.append(order.order_id)

    status = "failed" if errors else "completed"
    cursor_persisted = _persist_incremental_state(
        db,
        config=config,
        loaded=loaded,
        dry_run=dry_run,
        status=status,
    )
    failure_stage = None
    if errors:
        failure_stage = "load" if load_error else "map_or_import"
    return {
        "adapter_config_id": config.id,
        "adapter_type": config.adapter_type,
        "adapter_version": config.version,
        "external_system_id": system.id,
        "source": loaded.source,
        "dry_run": dry_run,
        "incremental": loaded.incremental,
        "cursor_in": loaded.cursor_in,
        "next_cursor": loaded.next_cursor,
        "cursor_persisted": cursor_persisted,
        "page_count": len(pages),
        "http_request_count": loaded.request_count,
        "http_status_codes": loaded.remote_status_codes,
        "mapped_count": len(mapped_rows),
        "imported_count": len(imported_ids),
        "rejected_count": len(errors),
        "order_ids": imported_ids,
        "errors": errors,
        "retryable": status == "failed",
        "failure_stage": failure_stage,
        "status": status,
    }


def sync_external_records(
    db: Session,
    *,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
    system_type: str,
    dry_run_override: bool | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    raw_config = config.config or {}
    dry_run = bool(raw_config.get("dry_run", True)) if dry_run_override is None else dry_run_override
    try:
        loaded = _load_record_pages(raw_config, http_transport=http_transport)
        pages = loaded.pages
        load_error = None
    except Exception as exc:
        loaded = RecordPageLoad(pages=[], source="http" if _uses_http_source(raw_config) else "configured")
        pages = []
        load_error = str(exc)

    normalized_records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if load_error:
        errors.append({"page": None, "row": None, "external_id": None, "message": load_error})

    for page_index, records in enumerate(pages, start=1):
        for row_index, record in enumerate(records, start=1):
            try:
                normalized_records.append(_normalize_external_record(record, raw_config, page_index, row_index, system_type))
            except Exception as exc:
                errors.append(
                    {
                        "page": page_index,
                        "row": row_index,
                        "external_id": _record_identifier(record),
                        "message": str(exc),
                    }
                )

    domain_summary = (
        _persist_external_domain_records(
            db,
            system=system,
            raw_config=raw_config,
            records=normalized_records,
            dry_run=dry_run,
        )
        if not errors
        else _empty_domain_summary(raw_config, dry_run, skipped_reason="normalization_failed")
    )
    errors.extend(domain_summary["errors"])

    status = "failed" if errors else "completed"
    cursor_persisted = _persist_incremental_state(
        db,
        config=config,
        loaded=loaded,
        dry_run=dry_run,
        status=status,
    )
    audit_record_limit = max(0, int(raw_config.get("audit_record_limit") or 50))
    return {
        "adapter_config_id": config.id,
        "adapter_type": config.adapter_type,
        "adapter_version": config.version,
        "external_system_id": system.id,
        "system_type": system_type,
        "source": loaded.source,
        "dry_run": dry_run,
        "incremental": loaded.incremental,
        "cursor_in": loaded.cursor_in,
        "next_cursor": loaded.next_cursor,
        "cursor_persisted": cursor_persisted,
        "page_count": len(pages),
        "http_request_count": loaded.request_count,
        "http_status_codes": loaded.remote_status_codes,
        "record_count": sum(len(page) for page in pages),
        "normalized_count": len(normalized_records),
        "records": normalized_records[:audit_record_limit],
        "records_truncated": len(normalized_records) > audit_record_limit,
        "domain_target": domain_summary["target"],
        "domain_targets": domain_summary["target_counts"],
        "domain_persisted_count": domain_summary["persisted_count"],
        "domain_record_ids": domain_summary["record_ids"],
        "domain_skipped_reason": domain_summary["skipped_reason"],
        "domain_errors": domain_summary["errors"],
        "rejected_count": len(errors),
        "errors": errors,
        "retryable": status == "failed",
        "failure_stage": "load" if load_error else ("domain_persist" if domain_summary["errors"] else ("normalize" if errors else None)),
        "status": status,
    }


def _evaluate_crm_order_acceptance(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    mapped_rows = [_map_external_record(record, config) for record in records]
    mapping = config.get("field_mapping") if isinstance(config.get("field_mapping"), dict) else {}
    defaults = config.get("defaults") if isinstance(config.get("defaults"), dict) else {}
    for field_name in REQUIRED_CRM_ORDER_FIELDS:
        values = [row.get(field_name) for row in mapped_rows if _present(row.get(field_name))]
        missing_count = max(0, len(records) - len(values))
        source_path = str(mapping.get(field_name) or "") or (field_name if any(field_name in record for record in records) else None)
        if not source_path and field_name in defaults:
            source_path = f"defaults.{field_name}"
        checks.append(
            _acceptance_check(
                scope="record",
                field=field_name,
                required=True,
                status="passed" if records and missing_count == 0 else "failed",
                source_path=source_path,
                observed_count=len(values),
                missing_count=missing_count,
                sample_values=_sample_values(values),
                message=(
                    f"{field_name} resolved for all sample orders"
                    if records and missing_count == 0
                    else f"{field_name} is required for CRM order import"
                ),
            )
        )
    return checks


def _evaluate_domain_record_acceptance(
    config: dict[str, Any],
    records: list[dict[str, Any]],
    domain_target: str | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if domain_target is None:
        checks.append(
            _acceptance_check(
                scope="record",
                field="domain_target",
                required=True,
                status="failed",
                message="domain_target is required to persist MES/ERP records into production tables",
            )
        )
        return checks
    if domain_target == "auto":
        targets = {_domain_target_for_record("auto", _normalize_external_record(record, config, 1, index, "external")) for index, record in enumerate(records, start=1)}
        targets.discard(None)
        if len(targets) != 1:
            checks.append(
                _acceptance_check(
                    scope="record",
                    field="domain_target",
                    required=True,
                    status="failed",
                    message="auto domain_target requires sample records with a single recognizable entity_type",
                )
            )
            return checks
        target = next(iter(targets))
    else:
        target = DOMAIN_TARGET_ALIASES.get(domain_target, domain_target)
    required_fields = REQUIRED_DOMAIN_FIELDS.get(target)
    if not required_fields:
        checks.append(
            _acceptance_check(
                scope="record",
                field="domain_target",
                required=True,
                status="failed",
                message=f"unsupported domain_target: {domain_target}",
            )
        )
        return checks

    normalized_records = [
        _normalize_external_record(record, config, 1, index, "external")
        for index, record in enumerate(records, start=1)
    ]
    for field_name in required_fields:
        values = [_domain_field_value(record, field_name) for record in normalized_records]
        present_values = [value for value in values if _present(value)]
        missing_count = max(0, len(records) - len(present_values))
        source_path = _domain_source_path(config, field_name)
        checks.append(
            _acceptance_check(
                scope="record",
                field=field_name,
                required=True,
                status="passed" if records and missing_count == 0 else "failed",
                source_path=source_path,
                observed_count=len(present_values),
                missing_count=missing_count,
                sample_values=_sample_values(present_values),
                message=(
                    f"{field_name} resolved for all sample records"
                    if records and missing_count == 0
                    else f"{field_name} is required for {target}"
                ),
            )
        )

    for field_name in RECOMMENDED_DOMAIN_FIELDS.get(target, ()):
        if "|" in field_name:
            options = field_name.split("|")
            present = [
                any(_present(_domain_field_value(record, option)) for option in options)
                for record in normalized_records
            ]
            observed_count = sum(1 for item in present if item)
            missing_count = max(0, len(records) - observed_count)
            source_path = " or ".join(_domain_source_path(config, option) or option for option in options)
        else:
            values = [_domain_field_value(record, field_name) for record in normalized_records]
            observed_count = sum(1 for value in values if _present(value))
            missing_count = max(0, len(records) - observed_count)
            source_path = _domain_source_path(config, field_name)
        checks.append(
            _acceptance_check(
                scope="record",
                field=field_name,
                required=False,
                status="passed" if records and missing_count == 0 else "warning",
                source_path=source_path,
                observed_count=observed_count,
                missing_count=missing_count,
                message=(
                    f"{field_name} resolved for all sample records"
                    if records and missing_count == 0
                    else f"{field_name} is recommended for production checks and audit clarity"
                ),
            )
        )
    return checks


def _evaluate_mapping_paths(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = config.get("field_mapping") if isinstance(config.get("field_mapping"), dict) else {}
    checks: list[dict[str, Any]] = []
    for output_field, source_path in sorted(mapping.items()):
        source = str(source_path)
        values = [_get_path(record, source) for record in records]
        observed = [value for value in values if _present(value)]
        missing_count = max(0, len(records) - len(observed))
        checks.append(
            _acceptance_check(
                scope="mapping",
                field=str(output_field),
                required=False,
                status="passed" if records and observed else "warning",
                source_path=source,
                observed_count=len(observed),
                missing_count=missing_count,
                sample_values=_sample_values(observed),
                message=(
                    f"{source} resolved for {len(observed)} sample records"
                    if observed
                    else f"{source} did not resolve in the configured samples"
                ),
            )
        )
    return checks


def _evaluate_status_dictionary(config: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return []
    status_path = str(config.get("status_path") or "status")
    raw_statuses = [_get_path(record, status_path) for record in records if _present(_get_path(record, status_path))]
    if not raw_statuses:
        return [
            _acceptance_check(
                scope="status",
                field="status",
                status="warning",
                source_path=status_path,
                message="status_path did not resolve in the configured samples",
                missing_count=len(records),
            )
        ]
    mapping = _inbound_status_mapping(config)
    if not mapping:
        return [
            _acceptance_check(
                scope="status",
                field="status_dictionary",
                status="warning",
                source_path=status_path,
                observed_count=len(raw_statuses),
                sample_values=_sample_values(raw_statuses),
                message="status dictionary is not configured; customer status codes will pass through unchanged",
            )
        ]
    mapped_keys = {str(key).lower() for key in mapping}
    unmapped = sorted({str(status) for status in raw_statuses if str(status).lower() not in mapped_keys})
    return [
        _acceptance_check(
            scope="status",
            field="status_dictionary",
            status="passed" if not unmapped else "warning",
            source_path=status_path,
            observed_count=len(raw_statuses) - len(unmapped),
            missing_count=len(unmapped),
            sample_values=unmapped[:5],
            message="all sample statuses are mapped" if not unmapped else f"unmapped sample statuses: {', '.join(unmapped[:5])}",
        )
    ]


def _unmapped_status_values(acceptance: schemas.AdapterFieldAcceptanceResult) -> list[str]:
    values: set[str] = set()
    for check in acceptance.checks:
        if check.scope == "status" and check.status != "passed":
            values.update(str(value) for value in check.sample_values)
    return sorted(values)


def _dictionary_keys(config: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in (
        "status_dictionary",
        "status_mapping",
        "inbound_status_dictionary",
        "inbound_status_mapping",
        "writeback_status_dictionary",
        "writeback_status_mapping",
        "outbound_status_dictionary",
        "outbound_status_mapping",
    ):
        if isinstance(config.get(key), dict):
            keys.append(key)
    writeback_config = config.get("writeback") if isinstance(config.get("writeback"), dict) else {}
    for key in ("status_dictionary", "status_mapping", "outbound_status_dictionary", "outbound_status_mapping"):
        if isinstance(writeback_config.get(key), dict):
            keys.append(f"writeback.{key}")
    organization_config = _organization_acceptance_config(config)
    if any(
        organization_config.get(key)
        for key in (
            "required_org_unit_codes",
            "org_unit_codes",
            "required_recipient_group_names",
            "recipient_group_names",
        )
    ):
        keys.append("organization_acceptance")
    elif any(config.get(key) for key in ("required_org_unit_codes", "required_recipient_group_names")):
        keys.append("organization_acceptance")
    return keys


def _evaluate_writeback_acceptance(config: dict[str, Any]) -> list[dict[str, Any]]:
    writeback_config = _writeback_config(config)
    if not writeback_config:
        return []
    checks: list[dict[str, Any]] = []
    endpoint = writeback_config.get("endpoint") or config.get("writeback_endpoint")
    mode = str(writeback_config.get("mode") or writeback_config.get("source") or config.get("writeback_mode") or "").lower()
    dry_run = _writeback_dry_run(config, writeback_config, None)
    checks.append(
        _acceptance_check(
            scope="writeback",
            field="endpoint",
            required=not dry_run and mode in HTTP_SOURCE_VALUES,
            status="passed" if endpoint or dry_run or mode not in HTTP_SOURCE_VALUES else "failed",
            source_path="writeback.endpoint",
            observed_count=1 if endpoint else 0,
            message="writeback endpoint configured" if endpoint else "real HTTP writeback requires an endpoint",
        )
    )
    mapping = writeback_config.get("field_mapping")
    if mapping is None:
        mapping = config.get("writeback_field_mapping")
    if isinstance(mapping, dict):
        invalid_paths = [
            str(context_path)
            for context_path in mapping.values()
            if not _writeback_context_path_supported(str(context_path))
        ]
        checks.append(
            _acceptance_check(
                scope="writeback",
                field="field_mapping",
                required=False,
                status="passed" if not invalid_paths else "warning",
                observed_count=max(0, len(mapping) - len(invalid_paths)),
                missing_count=len(invalid_paths),
                sample_values=invalid_paths[:5],
                message="writeback context mappings are supported"
                if not invalid_paths
                else f"unsupported writeback context paths: {', '.join(invalid_paths[:5])}",
            )
        )
    return checks


def _evaluate_organization_acceptance(db: Session | None, config: dict[str, Any]) -> list[dict[str, Any]]:
    organization_config = _organization_acceptance_config(config)
    required_org_codes = _string_list(
        organization_config.get("required_org_unit_codes")
        or organization_config.get("org_unit_codes")
        or config.get("required_org_unit_codes")
    )
    required_group_names = _string_list(
        organization_config.get("required_recipient_group_names")
        or organization_config.get("recipient_group_names")
        or config.get("required_recipient_group_names")
    )
    if not required_org_codes and not required_group_names:
        return []
    if db is None:
        return [
            _acceptance_check(
                scope="organization",
                field="organization_acceptance",
                required=True,
                status="failed",
                message="organization acceptance requires database-backed user and recipient-group lookup",
            )
        ]

    require_users = bool(organization_config.get("require_users", True))
    require_groups = bool(organization_config.get("require_recipient_groups", True))
    active_users = db.scalars(select(dbm.UserAccount).where(dbm.UserAccount.is_active.is_(True))).all()
    active_groups = db.scalars(
        select(dbm.NotificationRecipientGroup).where(dbm.NotificationRecipientGroup.is_active.is_(True))
    ).all()
    users_by_org_code: dict[str, list[dbm.UserAccount]] = {}
    for user in active_users:
        code = str(user.org_unit_code or "").strip()
        if code:
            users_by_org_code.setdefault(code, []).append(user)
    groups_by_org_code: dict[str, list[dbm.NotificationRecipientGroup]] = {}
    groups_by_name = {row.name: row for row in active_groups}
    for group in active_groups:
        for code in _string_list(group.department_codes or []):
            groups_by_org_code.setdefault(code, []).append(group)

    checks: list[dict[str, Any]] = []
    if required_org_codes:
        user_missing_codes = [code for code in required_org_codes if not users_by_org_code.get(code)]
        checks.append(
            _acceptance_check(
                scope="organization",
                field="org_unit_code",
                required=require_users,
                status="passed" if not user_missing_codes else "failed" if require_users else "warning",
                source_path="user_account.org_unit_code",
                observed_count=len(required_org_codes) - len(user_missing_codes),
                missing_count=len(user_missing_codes),
                sample_values=user_missing_codes[:5],
                message=(
                    "all required organization unit codes resolve to active users"
                    if not user_missing_codes
                    else f"organization unit codes without active users: {', '.join(user_missing_codes[:5])}"
                ),
            )
        )
        group_missing_codes = [code for code in required_org_codes if not groups_by_org_code.get(code)]
        checks.append(
            _acceptance_check(
                scope="organization",
                field="recipient_group.department_codes",
                required=require_groups,
                status="passed" if not group_missing_codes else "failed" if require_groups else "warning",
                source_path="notification_recipient_group.department_codes",
                observed_count=len(required_org_codes) - len(group_missing_codes),
                missing_count=len(group_missing_codes),
                sample_values=group_missing_codes[:5],
                message=(
                    "all required organization unit codes are covered by active recipient groups"
                    if not group_missing_codes
                    else f"organization unit codes without active recipient groups: {', '.join(group_missing_codes[:5])}"
                ),
            )
        )
    if required_group_names:
        missing_names = [name for name in required_group_names if name not in groups_by_name]
        empty_names = [
            name
            for name in required_group_names
            if name in groups_by_name and not repository.list_user_ids_by_recipient_group(db, groups_by_name[name].id)
        ]
        failed_names = missing_names + empty_names
        checks.append(
            _acceptance_check(
                scope="organization",
                field="recipient_group.name",
                required=True,
                status="passed" if not failed_names else "failed",
                source_path="notification_recipient_group.name",
                observed_count=len(required_group_names) - len(failed_names),
                missing_count=len(failed_names),
                sample_values=failed_names[:5],
                message=(
                    "all required recipient groups exist and resolve to active users"
                    if not failed_names
                    else f"recipient groups missing or empty: {', '.join(failed_names[:5])}"
                ),
            )
        )
    return checks


def _organization_acceptance_config(config: dict[str, Any]) -> dict[str, Any]:
    for key in ("organization_acceptance", "org_acceptance", "organization_directory", "org_directory"):
        value = config.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.replace("\n", ",").split(",")
    elif isinstance(value, list | tuple | set):
        raw_values = list(value)
    else:
        raw_values = [value]
    return sorted({str(item).strip() for item in raw_values if str(item).strip()})


def _domain_field_value(record: dict[str, Any], field_name: str) -> Any:
    if field_name in {"external_id", "status"}:
        return record.get(field_name)
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    return fields.get(field_name)


def _domain_source_path(config: dict[str, Any], field_name: str) -> str | None:
    mapping = config.get("field_mapping") if isinstance(config.get("field_mapping"), dict) else {}
    if field_name == "external_id":
        return str(config.get("external_id_path") or config.get("id_path") or "") or None
    if field_name == "status":
        return str(config.get("status_path") or "status")
    return str(mapping.get(field_name) or "") or None


def _acceptance_check(
    *,
    scope: str,
    field: str,
    status: str,
    message: str,
    required: bool = False,
    source_path: str | None = None,
    observed_count: int = 0,
    missing_count: int = 0,
    sample_values: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "scope": scope,
        "field": field,
        "required": required,
        "status": status,
        "source_path": source_path,
        "observed_count": observed_count,
        "missing_count": missing_count,
        "sample_values": sample_values or [],
        "message": message,
    }


def _sample_values(values: list[Any], limit: int = 3) -> list[str]:
    samples: list[str] = []
    for value in values:
        text = str(value)
        if text not in samples:
            samples.append(text[:120])
        if len(samples) >= limit:
            break
    return samples


def _writeback_context_path_supported(path: str) -> bool:
    if path in WRITEBACK_CONTEXT_FIELDS:
        return True
    if path.startswith("payload."):
        return True
    return False


def _persist_external_domain_records(
    db: Session,
    *,
    system: dbm.ExternalSystem,
    raw_config: dict[str, Any],
    records: list[dict[str, Any]],
    dry_run: bool,
) -> dict[str, Any]:
    summary = _empty_domain_summary(raw_config, dry_run)
    if not records:
        summary["skipped_reason"] = "no_records"
        return summary
    configured_target = _configured_domain_target(raw_config)
    if configured_target is None:
        summary["skipped_reason"] = "domain_target not configured"
        return summary
    if dry_run:
        summary["skipped_reason"] = "dry_run"
        return summary

    for record in records:
        target = _domain_target_for_record(configured_target, record)
        if target is None:
            summary["errors"].append(
                {
                    "page": record.get("page"),
                    "row": record.get("row"),
                    "external_id": record.get("external_id"),
                    "message": f"unsupported domain_target: {configured_target}",
                }
            )
            continue
        try:
            if target == "production_schedule":
                row = repository.upsert_production_schedule_entry(
                    db,
                    external_system_id=system.id,
                    record=record,
                )
            elif target == "inventory_snapshot":
                row = repository.upsert_inventory_snapshot(
                    db,
                    external_system_id=system.id,
                    record=record,
                )
            elif target == "delivery_confirmation":
                row = repository.upsert_delivery_confirmation(
                    db,
                    external_system_id=system.id,
                    record=record,
                )
            else:
                raise ValueError(f"unsupported domain_target: {target}")
        except Exception as exc:
            summary["errors"].append(
                {
                    "page": record.get("page"),
                    "row": record.get("row"),
                    "external_id": record.get("external_id"),
                    "message": str(exc),
                }
            )
            continue
        summary["persisted_count"] += 1
        summary["record_ids"].append(row.id)
        summary["target_counts"][target] = summary["target_counts"].get(target, 0) + 1
    summary["skipped_reason"] = None if summary["persisted_count"] else summary["skipped_reason"]
    if summary["target"] == "auto":
        summary["target"] = "mixed" if len(summary["target_counts"]) > 1 else next(iter(summary["target_counts"]), "auto")
    return summary


def _empty_domain_summary(
    raw_config: dict[str, Any],
    dry_run: bool,
    *,
    skipped_reason: str | None = None,
) -> dict[str, Any]:
    target = _configured_domain_target(raw_config)
    return {
        "target": target,
        "dry_run": dry_run,
        "persisted_count": 0,
        "record_ids": [],
        "target_counts": {},
        "skipped_reason": skipped_reason,
        "errors": [],
    }


def _configured_domain_target(config: dict[str, Any]) -> str | None:
    value = (
        config.get("domain_target")
        or config.get("domain_entity")
        or config.get("persist_to")
        or config.get("domain_table")
    )
    if value is None and config.get("persist_domain_records"):
        value = "auto"
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    if text in {"", "none", "false", "off"}:
        return None
    if text == "auto":
        return "auto"
    return DOMAIN_TARGET_ALIASES.get(text, text)


def _domain_target_for_record(configured_target: str, record: dict[str, Any]) -> str | None:
    if configured_target != "auto":
        return DOMAIN_TARGET_ALIASES.get(configured_target, configured_target)
    entity_type = str(record.get("entity_type") or "").strip().lower().replace("-", "_")
    return DOMAIN_TARGET_ALIASES.get(entity_type)


def _load_record_pages(config: dict[str, Any], http_transport: httpx.BaseTransport | None = None) -> RecordPageLoad:
    if _uses_http_source(config):
        return _fetch_http_record_pages(config, transport=http_transport)
    pages = _configured_record_pages(config)
    return RecordPageLoad(
        pages=pages,
        source="configured",
        incremental=_incremental_enabled(config),
        cursor_in=_current_incremental_cursor(config),
        next_cursor=_derive_next_incremental_cursor(config, pages),
    )


def _uses_http_source(config: dict[str, Any]) -> bool:
    mode = str(config.get("mode", "")).lower()
    source = str(config.get("source", "")).lower()
    return mode in HTTP_SOURCE_VALUES or source in HTTP_SOURCE_VALUES


def writeback_adapter_result(
    db: Session,
    *,
    system: dbm.ExternalSystem,
    config: dbm.AdapterConfig,
    target_id: str | None,
    target_type: str = "solution",
    status: str = "completed",
    payload: dict[str, Any] | None = None,
    dry_run_override: bool | None = None,
    http_transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    raw_config = config.config or {}
    writeback_config = _writeback_config(raw_config)
    dry_run = _writeback_dry_run(raw_config, writeback_config, dry_run_override)
    confirmed_at = repository.utc_now().isoformat()
    outbound_status = _map_status_value(status, _outbound_status_mapping(raw_config, writeback_config))
    context = {
        "target_id": target_id,
        "target_type": target_type,
        "status": outbound_status,
        "internal_status": status,
        "confirmed_at": confirmed_at,
        "source": "packaging_nesting",
        "adapter_config_id": config.id,
        "adapter_version": config.version,
        "payload": payload or {},
    }
    request_body = _build_writeback_payload(context, writeback_config, raw_config)
    result = {
        "adapter_config_id": config.id,
        "adapter_type": config.adapter_type,
        "adapter_version": config.version,
        "external_system_id": system.id,
        "system_type": system.system_type,
        "target_id": target_id,
        "target_type": target_type,
        "requested_status": status,
        "external_status": outbound_status,
        "dry_run": dry_run,
        "confirmed": False,
        "confirmation_status": "dry_run" if dry_run else "pending",
        "request_body": request_body,
        "errors": [],
    }
    if dry_run:
        result["status"] = "completed"
        return result
    if not _uses_http_writeback(raw_config, writeback_config):
        result["status"] = "failed"
        result["confirmation_status"] = "not_configured"
        result["errors"] = [{"message": "real writeback requires writeback.mode=http or writeback.source=http"}]
        return result
    try:
        http_result = _send_http_writeback(writeback_config, raw_config, context, request_body, http_transport)
        result.update(http_result)
        result["status"] = "completed" if http_result["confirmed"] else "failed"
    except Exception as exc:
        result["status"] = "failed"
        result["confirmation_status"] = "failed"
        result["errors"] = [{"message": str(exc)}]
    return result


def _configured_record_pages(config: dict[str, Any]) -> list[list[dict[str, Any]]]:
    records_path = str(config.get("records_path") or "records")
    pages = config.get("pages")
    if isinstance(pages, list):
        normalized_pages: list[list[dict[str, Any]]] = []
        for page in pages:
            if isinstance(page, list):
                normalized_pages.append([record for record in page if isinstance(record, dict)])
            elif isinstance(page, dict):
                records = _get_path(page, records_path)
                if records is None and records_path == "records":
                    records = page.get("items")
                normalized_pages.append([record for record in records or [] if isinstance(record, dict)])
        return normalized_pages
    records = config.get("sample_records", config.get("records", []))
    return [[record for record in records if isinstance(record, dict)]] if isinstance(records, list) else [[]]


def _fetch_http_record_pages(config: dict[str, Any], transport: httpx.BaseTransport | None = None) -> RecordPageLoad:
    base_url = str(config.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("http CRM sync requires base_url")
    endpoint = str(config.get("orders_endpoint") or config.get("endpoint") or "/orders")
    records_path = str(config.get("records_path") or "records")
    method = str(config.get("method") or "GET").upper()
    if method != "GET":
        raise ValueError("http CRM sync currently supports GET only")
    timeout_sec = float(config.get("timeout_sec") or 10)
    max_pages = max(1, int(config.get("max_pages") or 10))
    retry_count = max(0, int(config.get("retry_count") or 0))
    retry_backoff_sec = max(0.0, float(config.get("retry_backoff_sec") or 0))
    page_param = str(config.get("page_param") or "page")
    page_size_param = str(config.get("page_size_param") or "page_size")
    page_size = config.get("page_size")
    query = dict(config.get("query") or {})
    headers = _http_headers(config)
    auth = _http_auth(config)
    next_url_path = str(config.get("next_url_path") or "")
    next_page_path = str(config.get("next_page_path") or "")
    next_cursor_path = str(config.get("next_cursor_path") or "")
    cursor_param = str(config.get("cursor_param") or "cursor")
    incremental = _incremental_enabled(config)
    incremental_cursor = _current_incremental_cursor(config) if incremental else None
    incremental_cursor_param = str(config.get("incremental_cursor_param") or config.get("since_param") or "updated_after")
    pages: list[list[dict[str, Any]]] = []
    status_codes: list[int] = []
    request_count = 0
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    cursor: Any = None
    latest_response_cursor: Any = None

    with httpx.Client(timeout=timeout_sec, transport=transport) as client:
        for page_index in range(1, max_pages + 1):
            params = dict(query)
            if page_size is not None:
                params[page_size_param] = page_size
            if cursor is None and incremental_cursor is not None and incremental_cursor_param not in params:
                params[incremental_cursor_param] = incremental_cursor
            if cursor is not None:
                params[cursor_param] = cursor
            if cursor is None and not next_url_path and page_param not in params:
                params[page_param] = int(config.get("start_page") or page_index)

            response = _request_with_retries(
                client,
                url,
                headers=headers,
                params=params,
                auth=auth,
                retry_count=retry_count,
                retry_backoff_sec=retry_backoff_sec,
            )
            request_count += 1
            status_codes.append(response.status_code)
            payload = response.json()
            records = _get_path(payload, records_path)
            if records is None and records_path == "records":
                records = payload.get("items")
            page_records = [record for record in records or [] if isinstance(record, dict)]
            pages.append(page_records)
            response_cursor = _extract_response_incremental_cursor(config, payload)
            if response_cursor is not None:
                latest_response_cursor = response_cursor

            next_url = _get_path(payload, next_url_path) if next_url_path else None
            if next_url:
                url = urljoin(base_url.rstrip("/") + "/", str(next_url))
                cursor = None
                continue
            cursor = _get_path(payload, next_cursor_path) if next_cursor_path else None
            if cursor:
                continue
            next_page = _get_path(payload, next_page_path) if next_page_path else None
            if next_page:
                query[page_param] = next_page
                continue
            if not page_records or page_index >= max_pages:
                break

    return RecordPageLoad(
        pages=pages,
        source="http",
        request_count=request_count,
        remote_status_codes=status_codes,
        incremental=incremental,
        cursor_in=incremental_cursor,
        next_cursor=_derive_next_incremental_cursor(config, pages, response_cursor=latest_response_cursor),
    )


def _persist_incremental_state(
    db: Session,
    *,
    config: dbm.AdapterConfig,
    loaded: RecordPageLoad,
    dry_run: bool,
    status: str,
) -> bool:
    if dry_run or status != "completed" or not loaded.incremental or loaded.next_cursor is None:
        return False
    repository.update_adapter_config_runtime_state(
        db,
        config.id,
        {
            "cursor": loaded.next_cursor,
            "last_success_at": repository.utc_now().isoformat(),
            "last_source": loaded.source,
            "last_http_status_codes": loaded.remote_status_codes[-10:],
        },
    )
    return True


def _writeback_config(config: dict[str, Any]) -> dict[str, Any]:
    nested = config.get("writeback")
    return dict(nested) if isinstance(nested, dict) else {}


def _writeback_dry_run(
    config: dict[str, Any],
    writeback_config: dict[str, Any],
    dry_run_override: bool | None,
) -> bool:
    if dry_run_override is not None:
        return dry_run_override
    if "dry_run" in writeback_config:
        return bool(writeback_config.get("dry_run"))
    if "writeback_dry_run" in config:
        return bool(config.get("writeback_dry_run"))
    return True


def _uses_http_writeback(config: dict[str, Any], writeback_config: dict[str, Any]) -> bool:
    mode = str(writeback_config.get("mode") or config.get("writeback_mode") or "").lower()
    source = str(writeback_config.get("source") or config.get("writeback_source") or "").lower()
    return mode in HTTP_SOURCE_VALUES or source in HTTP_SOURCE_VALUES


def _build_writeback_payload(
    context: dict[str, Any],
    writeback_config: dict[str, Any],
    root_config: dict[str, Any],
) -> dict[str, Any]:
    explicit_payload = writeback_config.get("payload")
    if explicit_payload is None:
        explicit_payload = root_config.get("writeback_payload")
    if isinstance(explicit_payload, dict):
        body = dict(explicit_payload)
    else:
        body = {
            "target_id": context.get("target_id"),
            "target_type": context.get("target_type"),
            "status": context.get("status"),
            "confirmed_at": context.get("confirmed_at"),
            "source": context.get("source"),
            "payload": context.get("payload") or {},
        }

    defaults = writeback_config.get("defaults")
    if defaults is None:
        defaults = root_config.get("writeback_defaults")
    if isinstance(defaults, dict):
        for key, value in defaults.items():
            body.setdefault(str(key), value)

    mapping = writeback_config.get("field_mapping")
    if mapping is None:
        mapping = root_config.get("writeback_field_mapping")
    if isinstance(mapping, dict):
        for output_path, context_path in mapping.items():
            value = _get_path(context, str(context_path))
            if value is not None:
                _set_path(body, str(output_path), value)
    return body


def _normalize_external_record(
    record: dict[str, Any],
    config: dict[str, Any],
    page_index: int,
    row_index: int,
    system_type: str,
) -> dict[str, Any]:
    external_id_path = str(config.get("external_id_path") or config.get("id_path") or "")
    external_id = _get_path(record, external_id_path) if external_id_path else _record_identifier(record)
    status_path = str(config.get("status_path") or "status")
    raw_status = _get_path(record, status_path) if status_path else None
    status = _map_status_value(raw_status, _inbound_status_mapping(config))
    entity_type = str(config.get("entity_type") or f"{system_type}_record")
    fields = _map_generic_fields(record, config)
    return {
        "external_id": str(external_id) if external_id is not None else None,
        "entity_type": entity_type,
        "status": status,
        "raw_status": raw_status,
        "fields": fields,
        "page": page_index,
        "row": row_index,
    }


def _map_generic_fields(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    mapping = config.get("field_mapping") or {}
    fields: dict[str, Any] = {}
    if isinstance(mapping, dict):
        for output_path, source_path in mapping.items():
            value = _get_path(record, str(source_path))
            if value is not None:
                _set_path(fields, str(output_path), value)
    include_raw_fields = config.get("include_raw_fields") or []
    if isinstance(include_raw_fields, list):
        for field_name in include_raw_fields:
            value = _get_path(record, str(field_name))
            if value is not None:
                fields[str(field_name)] = value
    redacted = repository.redact_sensitive_payload(fields)
    return redacted if isinstance(redacted, dict) else {}


def _inbound_status_mapping(config: dict[str, Any]) -> dict[str, Any]:
    return _status_mapping(config, "inbound_status_dictionary", "inbound_status_mapping", "status_dictionary", "status_mapping")


def _outbound_status_mapping(root_config: dict[str, Any], writeback_config: dict[str, Any]) -> dict[str, Any]:
    return _status_mapping(
        writeback_config,
        "status_dictionary",
        "status_mapping",
        "outbound_status_dictionary",
        "outbound_status_mapping",
    ) or _status_mapping(
        root_config,
        "writeback_status_dictionary",
        "writeback_status_mapping",
        "outbound_status_dictionary",
        "outbound_status_mapping",
    )


def _status_mapping(config: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        mapping = config.get(key)
        if isinstance(mapping, dict):
            return mapping
    return {}


def _map_status_value(status: Any, mapping: dict[str, Any]) -> Any:
    if status is None or not mapping:
        return status
    if status in mapping:
        return mapping[status]
    status_text = str(status)
    lower_mapping = {str(key).lower(): value for key, value in mapping.items()}
    return lower_mapping.get(status_text.lower(), status)


def _send_http_writeback(
    writeback_config: dict[str, Any],
    root_config: dict[str, Any],
    context: dict[str, Any],
    request_body: dict[str, Any],
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    base_url = str(writeback_config.get("base_url") or root_config.get("base_url") or "").strip()
    if not base_url:
        raise ValueError("http writeback requires base_url")
    endpoint = str(writeback_config.get("endpoint") or root_config.get("writeback_endpoint") or "/writebacks")
    method = str(writeback_config.get("method") or root_config.get("writeback_method") or "POST").upper()
    if method not in {"POST", "PUT", "PATCH"}:
        raise ValueError("http writeback supports POST, PUT, and PATCH only")
    timeout_sec = float(writeback_config.get("timeout_sec") or root_config.get("timeout_sec") or 10)
    retry_count = max(0, int(writeback_config.get("retry_count") or root_config.get("retry_count") or 0))
    retry_backoff_sec = max(0.0, float(writeback_config.get("retry_backoff_sec") or root_config.get("retry_backoff_sec") or 0))
    request_config = {**root_config, **writeback_config}
    headers = _http_headers(request_config)
    auth = _http_auth(request_config)
    url = urljoin(base_url.rstrip("/") + "/", _format_endpoint(endpoint, context).lstrip("/"))

    with httpx.Client(timeout=timeout_sec, transport=transport) as client:
        response = _send_request_with_retries(
            client,
            method,
            url,
            headers=headers,
            json_body=request_body,
            auth=auth,
            retry_count=retry_count,
            retry_backoff_sec=retry_backoff_sec,
        )

    response_payload = _response_payload(response)
    confirmation = _writeback_confirmation(writeback_config, response_payload)
    return {
        "source": "http",
        "method": method,
        "endpoint": endpoint,
        "http_status_code": response.status_code,
        "confirmed": confirmation["confirmed"],
        "confirmation_status": confirmation["confirmation_status"],
        "remote_confirmation": confirmation["remote_confirmation"],
        "remote_response": response_payload,
    }


def _send_request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any],
    auth: httpx.Auth | tuple[str, str] | None,
    retry_count: int,
    retry_backoff_sec: float,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            response = client.request(method, url, headers=headers, json=json_body, auth=auth)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt >= retry_count:
                break
            if retry_backoff_sec:
                import time

                time.sleep(retry_backoff_sec)
    raise ValueError(f"http writeback request failed: {last_exc}") from last_exc


def _format_endpoint(endpoint: str, context: dict[str, Any]) -> str:
    formatted = endpoint
    for key in WRITEBACK_CONTEXT_FIELDS:
        value = context.get(key)
        if value is not None and not isinstance(value, dict):
            formatted = formatted.replace("{" + key + "}", quote(str(value), safe=""))
    return formatted


def _response_payload(response: httpx.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"value": payload}
    except ValueError:
        return {"text": response.text}


def _writeback_confirmation(writeback_config: dict[str, Any], response_payload: dict[str, Any]) -> dict[str, Any]:
    confirmation_path = str(
        writeback_config.get("confirmation_path")
        or writeback_config.get("confirmation_status_path")
        or writeback_config.get("confirm_path")
        or ""
    )
    if not confirmation_path:
        return {"confirmed": True, "confirmation_status": "confirmed", "remote_confirmation": None}
    remote_confirmation = _get_path(response_payload, confirmation_path)
    success_values = writeback_config.get("confirmation_success_values")
    if isinstance(success_values, list) and success_values:
        success_texts = {str(value).lower() for value in success_values}
        confirmed = str(remote_confirmation).lower() in success_texts
    else:
        confirmed = _truthy_confirmation(remote_confirmation)
    return {
        "confirmed": confirmed,
        "confirmation_status": "confirmed" if confirmed else "unconfirmed",
        "remote_confirmation": remote_confirmation,
    }


def _truthy_confirmation(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "ok", "success", "completed", "confirmed", "accepted"}


def _request_with_retries(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
    auth: httpx.Auth | tuple[str, str] | None,
    retry_count: int,
    retry_backoff_sec: float,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            response = client.get(url, headers=headers, params=params, auth=auth)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_exc = exc
            if attempt >= retry_count:
                break
            if retry_backoff_sec:
                import time

                time.sleep(retry_backoff_sec)
    raise ValueError(f"http CRM sync request failed: {last_exc}") from last_exc


def _http_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items()}
    auth_type = str(config.get("auth_type") or "").lower()
    if auth_type == "api_key":
        header_name = str(config.get("api_key_header") or "X-API-Key")
        headers[header_name] = str(config.get("api_key") or "")
    elif auth_type == "bearer":
        headers["Authorization"] = f"Bearer {config.get('api_key') or config.get('token') or ''}"
    return headers


def _http_auth(config: dict[str, Any]) -> tuple[str, str] | None:
    if str(config.get("auth_type") or "").lower() != "basic":
        return None
    return str(config.get("username") or ""), str(config.get("password") or "")


def _incremental_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("incremental") or config.get("incremental_sync"))


def _current_incremental_cursor(config: dict[str, Any]) -> Any | None:
    if not _incremental_enabled(config):
        return None
    state = config.get("state") if isinstance(config.get("state"), dict) else {}
    for source in (state, config):
        for key in ("cursor", "last_cursor", "last_success_cursor", "initial_cursor"):
            value = source.get(key)
            if _present(value):
                return value
    return None


def _derive_next_incremental_cursor(
    config: dict[str, Any],
    pages: list[list[dict[str, Any]]],
    *,
    response_cursor: Any | None = None,
) -> Any | None:
    if not _incremental_enabled(config):
        return None
    if _present(response_cursor):
        return response_cursor
    record_cursor_path = str(config.get("record_cursor_path") or config.get("incremental_record_cursor_path") or "")
    if not record_cursor_path:
        return None
    values = []
    for records in pages:
        for record in records:
            value = _get_path(record, record_cursor_path)
            if _present(value):
                values.append(str(value))
    return max(values) if values else None


def _extract_response_incremental_cursor(config: dict[str, Any], payload: dict[str, Any]) -> Any | None:
    for key in ("incremental_cursor_path", "next_incremental_cursor_path", "sync_cursor_path"):
        path = str(config.get(key) or "")
        if not path:
            continue
        value = _get_path(payload, path)
        if _present(value):
            return value
    return None


def _map_external_record(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    mapping = config.get("field_mapping") or {}
    defaults = config.get("defaults") or {}
    mapped = {key: value for key, value in defaults.items() if key in ORDER_FIELDS}
    for field in ORDER_FIELDS:
        path = mapping.get(field)
        if path:
            value = _get_path(record, str(path))
        else:
            value = record.get(field)
        if value is not None:
            mapped[field] = value
    return mapped


def _get_path(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _set_path(record: dict[str, Any], path: str, value: Any) -> None:
    current = record
    parts = [part for part in path.split(".") if part]
    for part in parts[:-1]:
        existing = current.get(part)
        if not isinstance(existing, dict):
            existing = {}
            current[part] = existing
        current = existing
    if parts:
        current[parts[-1]] = value


def _record_identifier(record: dict[str, Any]) -> str | None:
    for key in ("order_id", "id", "external_id", "external_order_id"):
        if key in record and record[key] is not None:
            return str(record[key])
    return None


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""
