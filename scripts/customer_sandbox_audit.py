from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_PACK_PATH = REPO_ROOT / "samples" / "integrations" / "customer-sandbox" / "adapter-sandbox-pack.json"
DEFAULT_REQUIRED_SYSTEM_TYPES = ["crm", "mes", "erp"]

sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base
from app.db import models as dbm  # noqa: F401
from app.domain import schemas
from app.services import repository
from app.services.adapters import (
    DOMAIN_TARGET_ALIASES,
    REQUIRED_CRM_ORDER_FIELDS,
    REQUIRED_DOMAIN_FIELDS,
    build_adapter_readiness_report,
    evaluate_adapter_field_acceptance,
    signoff_adapter_dictionary,
)
from app.services.security import hash_password


PACK_SCHEMA_VERSION = 1
PACK_METADATA_KEYS = {"schema_version", "metadata", "organization_directory"}
REQUIRED_SAMPLE_KEYS_BY_SYSTEM_TYPE = {
    "crm": ("crm",),
    "mes": ("mes",),
    "erp": ("erp_inventory", "erp_delivery"),
}
EXPECTED_SAMPLE_CONTRACTS = {
    "crm": {"system_type": "crm"},
    "mes": {"system_type": "mes", "domain_target": "production_schedule"},
    "erp_inventory": {"system_type": "erp", "domain_target": "inventory_snapshot"},
    "erp_delivery": {"system_type": "erp", "domain_target": "delivery_confirmation"},
}


def load_pack(pack_path: Path) -> dict[str, Any]:
    return json.loads(pack_path.read_text(encoding="utf-8-sig"))


def discover_adapter_entries(pack: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key, value in pack.items():
        if key in PACK_METADATA_KEYS or not isinstance(value, dict):
            continue
        config = value.get("config")
        adapter_type = value.get("adapter_type")
        if not adapter_type or not isinstance(config, dict):
            continue
        entries.append(
            {
                "sample_key": key,
                "system_type": str(value.get("system_type") or infer_system_type(key, str(adapter_type))).lower(),
                "adapter_type": str(adapter_type),
                "config": config,
                "accepted_unmapped_statuses": value.get("accepted_unmapped_statuses")
                or config.get("accepted_unmapped_statuses")
                or [],
            }
        )
    return entries


def infer_system_type(sample_key: str, adapter_type: str) -> str:
    value = f"{sample_key} {adapter_type}".lower()
    for system_type in ("crm", "mes", "erp", "solver"):
        if system_type in value:
            return system_type
    return "other"


def build_customer_sandbox_audit_report(
    pack_path: Path = DEFAULT_PACK_PATH,
    *,
    required_system_types: list[str] | None = None,
) -> dict[str, Any]:
    resolved_pack_path = pack_path if pack_path.is_absolute() else REPO_ROOT / pack_path
    required_types = normalize_required_system_types(required_system_types)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "pack_path": str(resolved_pack_path),
        "status": "failed",
        "summary": {},
        "organization": {},
        "pack_contract": {},
        "adapters": [],
        "readiness": {},
        "errors": [],
    }
    if not resolved_pack_path.exists():
        report["errors"].append(f"pack file not found: {resolved_pack_path}")
        report["summary"] = build_summary(report, required_types)
        return report

    try:
        pack = load_pack(resolved_pack_path)
    except Exception as exc:
        report["errors"].append(f"pack file is not valid JSON: {exc}")
        report["summary"] = build_summary(report, required_types)
        return report

    if not isinstance(pack, dict):
        report["errors"].append("pack root must be a JSON object")
        report["summary"] = build_summary(report, required_types)
        return report

    report["pack_contract"] = validate_pack_contract(pack, required_system_types=required_types)
    if report["pack_contract"]["failed_count"]:
        report["summary"] = build_summary(report, required_types)
        return report

    entries = discover_adapter_entries(pack)
    if not entries:
        report["errors"].append("pack does not contain any adapter entries")
        report["summary"] = build_summary(report, required_types)
        return report

    with tempfile.TemporaryDirectory(prefix="customer-sandbox-audit-") as temp_dir:
        db_path = Path(temp_dir) / "audit.sqlite"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        try:
            with SessionLocal() as db:
                report["organization"] = seed_organization_directory(db, pack.get("organization_directory") or {})
                report["adapters"] = [audit_adapter_entry(db, entry) for entry in entries]
                readiness = build_adapter_readiness_report(db, required_system_types=required_types)
                report["readiness"] = summarize_readiness(readiness)
        finally:
            engine.dispose()

    report["summary"] = build_summary(report, required_types)
    report["status"] = "passed" if report["summary"]["failed_count"] == 0 else "failed"
    return report


def normalize_required_system_types(required_system_types: list[str] | None) -> list[str]:
    values = required_system_types or DEFAULT_REQUIRED_SYSTEM_TYPES
    normalized: list[str] = []
    for value in values:
        system_type = str(value).strip().lower()
        if system_type and system_type not in normalized:
            normalized.append(system_type)
    return normalized


def validate_pack_contract(pack: dict[str, Any], *, required_system_types: list[str]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    schema_version = pack.get("schema_version")
    checks.append(
        pack_contract_check(
            code="schema.version",
            status="passed" if schema_version == PACK_SCHEMA_VERSION else "failed",
            message=(
                f"schema_version {PACK_SCHEMA_VERSION} is declared"
                if schema_version == PACK_SCHEMA_VERSION
                else f"schema_version must be {PACK_SCHEMA_VERSION}"
            ),
            evidence={"schema_version": schema_version, "supported": [PACK_SCHEMA_VERSION]},
        )
    )

    directory_checks = validate_organization_directory_contract(pack.get("organization_directory"))
    checks.extend(directory_checks["checks"])
    org_unit_codes = directory_checks["org_unit_codes"]
    recipient_group_names = directory_checks["recipient_group_names"]

    required_keys = required_sample_keys(required_system_types)
    for sample_key in required_keys:
        checks.append(
            pack_contract_check(
                code="sample.required",
                status="passed" if sample_key in pack else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} sample is present"
                    if sample_key in pack
                    else f"{sample_key} sample is required for CRM/MES/ERP sandbox acceptance"
                ),
                evidence={"required_system_types": required_system_types},
            )
        )

    adapter_items = [
        (key, value)
        for key, value in pack.items()
        if key not in PACK_METADATA_KEYS and isinstance(value, dict) and ("adapter_type" in value or "config" in value)
    ]
    adapter_types = [str(value.get("adapter_type") or "").strip() for _, value in adapter_items]
    duplicate_adapter_types = sorted({value for value in adapter_types if value and adapter_types.count(value) > 1})
    checks.append(
        pack_contract_check(
            code="adapter_type.unique",
            status="passed" if not duplicate_adapter_types else "failed",
            message=(
                "adapter_type values are unique"
                if not duplicate_adapter_types
                else "adapter_type values must be unique within one sandbox pack"
            ),
            evidence={"duplicates": duplicate_adapter_types},
        )
    )

    for sample_key, entry in adapter_items:
        checks.extend(
            validate_adapter_entry_contract(
                sample_key=sample_key,
                entry=entry,
                required=sample_key in required_keys,
                org_unit_codes=org_unit_codes,
                recipient_group_names=recipient_group_names,
            )
        )

    failed_count = sum(1 for check in checks if check["status"] == "failed")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    passed_count = sum(1 for check in checks if check["status"] == "passed")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "supported_schema_version": PACK_SCHEMA_VERSION,
        "required_sample_keys": required_keys,
        "adapter_entry_count": len(adapter_items),
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in checks if check["status"] == "failed"],
        "warning_checks": [check for check in checks if check["status"] == "warning"],
        "checks": checks,
    }


def validate_organization_directory_contract(directory: Any) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    if not isinstance(directory, dict):
        checks.append(
            pack_contract_check(
                code="organization_directory.present",
                status="failed",
                message="organization_directory must be provided as an object",
            )
        )
        return {"checks": checks, "org_unit_codes": set(), "recipient_group_names": set()}

    org_units = directory.get("org_units") if isinstance(directory.get("org_units"), list) else []
    recipient_groups = directory.get("recipient_groups") if isinstance(directory.get("recipient_groups"), list) else []
    org_unit_codes = [str(item.get("code") or "").strip() for item in org_units if isinstance(item, dict)]
    org_unit_code_set = {code for code in org_unit_codes if code}
    duplicate_org_codes = sorted({code for code in org_unit_codes if code and org_unit_codes.count(code) > 1})
    group_names = [str(item.get("name") or "").strip() for item in recipient_groups if isinstance(item, dict)]
    group_name_set = {name for name in group_names if name}
    duplicate_group_names = sorted({name for name in group_names if name and group_names.count(name) > 1})

    checks.extend(
        [
            pack_contract_check(
                code="organization_directory.org_units",
                status="passed" if org_unit_code_set else "failed",
                message="organization_directory defines org units" if org_unit_code_set else "organization_directory.org_units must contain at least one coded org unit",
                evidence={"org_unit_count": len(org_unit_code_set)},
            ),
            pack_contract_check(
                code="organization_directory.recipient_groups",
                status="passed" if group_name_set else "failed",
                message=(
                    "organization_directory defines recipient groups"
                    if group_name_set
                    else "organization_directory.recipient_groups must contain at least one recipient group"
                ),
                evidence={"recipient_group_count": len(group_name_set)},
            ),
            pack_contract_check(
                code="organization_directory.org_unit.unique",
                status="passed" if not duplicate_org_codes else "failed",
                message="org unit codes are unique" if not duplicate_org_codes else "org unit codes must be unique",
                evidence={"duplicates": duplicate_org_codes},
            ),
            pack_contract_check(
                code="organization_directory.recipient_group.unique",
                status="passed" if not duplicate_group_names else "failed",
                message=(
                    "recipient group names are unique"
                    if not duplicate_group_names
                    else "recipient group names must be unique"
                ),
                evidence={"duplicates": duplicate_group_names},
            ),
        ]
    )

    for group in recipient_groups:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "").strip()
        department_codes = [str(code).strip() for code in group.get("department_codes") or [] if str(code).strip()]
        missing_departments = sorted(code for code in department_codes if code not in org_unit_code_set)
        checks.append(
            pack_contract_check(
                code="organization_directory.recipient_group.departments",
                status="passed" if department_codes and not missing_departments else "failed",
                sample_key=group_name or None,
                message=(
                    f"recipient group {group_name} resolves to known departments"
                    if department_codes and not missing_departments
                    else f"recipient group {group_name or '<unnamed>'} must reference existing department codes"
                ),
                evidence={"department_codes": department_codes, "missing_department_codes": missing_departments},
            )
        )

    return {"checks": checks, "org_unit_codes": org_unit_code_set, "recipient_group_names": group_name_set}


def validate_adapter_entry_contract(
    *,
    sample_key: str,
    entry: dict[str, Any],
    required: bool,
    org_unit_codes: set[str],
    recipient_group_names: set[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    expected = EXPECTED_SAMPLE_CONTRACTS.get(sample_key, {})
    adapter_type = str(entry.get("adapter_type") or "").strip()
    system_type = str(entry.get("system_type") or "").strip().lower()
    config = entry.get("config")
    config_dict = config if isinstance(config, dict) else {}
    records = configured_record_samples(config_dict)
    domain_target = normalize_domain_target(config_dict.get("domain_target") or config_dict.get("domain_entity") or config_dict.get("persist_to") or config_dict.get("domain_table"))
    expected_system_type = expected.get("system_type")
    expected_domain_target = expected.get("domain_target")

    checks.extend(
        [
            pack_contract_check(
                code="adapter.entry",
                status="passed" if isinstance(entry, dict) else "failed",
                sample_key=sample_key,
                message=f"{sample_key} adapter entry is an object",
            ),
            pack_contract_check(
                code="adapter.adapter_type",
                status="passed" if adapter_type else "failed",
                sample_key=sample_key,
                message=f"{sample_key} declares adapter_type" if adapter_type else f"{sample_key} must declare adapter_type",
                evidence={"adapter_type": adapter_type},
            ),
            pack_contract_check(
                code="adapter.system_type",
                status="passed" if system_type in {"crm", "mes", "erp"} else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} declares system_type {system_type}"
                    if system_type in {"crm", "mes", "erp"}
                    else f"{sample_key} must explicitly declare system_type crm, mes, or erp"
                ),
                evidence={"system_type": system_type},
            ),
            pack_contract_check(
                code="adapter.config",
                status="passed" if isinstance(config, dict) else "failed",
                sample_key=sample_key,
                message=f"{sample_key} config is an object" if isinstance(config, dict) else f"{sample_key} config must be an object",
            ),
            pack_contract_check(
                code="adapter.sample_records",
                status="passed" if records else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} includes local sample records"
                    if records
                    else f"{sample_key} must include pages, records, or sample_records for offline acceptance"
                ),
                evidence={"sample_count": len(records)},
            ),
        ]
    )

    if expected_system_type:
        checks.append(
            pack_contract_check(
                code="adapter.system_type.expected",
                status="passed" if system_type == expected_system_type else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} system_type matches expected {expected_system_type}"
                    if system_type == expected_system_type
                    else f"{sample_key} system_type must be {expected_system_type}"
                ),
                evidence={"system_type": system_type, "expected": expected_system_type},
            )
        )

    if config_dict:
        dry_run = config_dict.get("dry_run")
        checks.append(
            pack_contract_check(
                code="adapter.sandbox_mode",
                status="passed" if dry_run is True else "failed",
                sample_key=sample_key,
                message=f"{sample_key} is pinned to dry_run=true" if dry_run is True else f"{sample_key} sandbox config must set dry_run=true",
                evidence={"mode": config_dict.get("mode"), "source": config_dict.get("source"), "dry_run": dry_run},
            )
        )
        checks.extend(
            validate_field_mapping_contract(
                sample_key=sample_key,
                system_type=system_type,
                config=config_dict,
                records=records,
                domain_target=domain_target,
                expected_domain_target=expected_domain_target,
            )
        )
        checks.extend(
            validate_status_dictionary_contract(
                sample_key=sample_key,
                config=config_dict,
                records=records,
            )
        )
        if required:
            checks.extend(
                validate_organization_acceptance_contract(
                    sample_key=sample_key,
                    config=config_dict,
                    org_unit_codes=org_unit_codes,
                    recipient_group_names=recipient_group_names,
                )
            )
    return checks


def validate_field_mapping_contract(
    *,
    sample_key: str,
    system_type: str,
    config: dict[str, Any],
    records: list[dict[str, Any]],
    domain_target: str | None,
    expected_domain_target: str | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    mapping = config.get("field_mapping") if isinstance(config.get("field_mapping"), dict) else {}
    checks.append(
        pack_contract_check(
            code="adapter.field_mapping",
            status="passed" if mapping else "failed",
            sample_key=sample_key,
            message=f"{sample_key} declares field_mapping" if mapping else f"{sample_key} must declare field_mapping",
            evidence={"field_count": len(mapping)},
        )
    )
    if expected_domain_target:
        checks.append(
            pack_contract_check(
                code="adapter.domain_target.expected",
                status="passed" if domain_target == expected_domain_target else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} domain_target is {expected_domain_target}"
                    if domain_target == expected_domain_target
                    else f"{sample_key} domain_target must be {expected_domain_target}"
                ),
                evidence={"domain_target": domain_target, "expected": expected_domain_target},
            )
        )

    if system_type == "crm":
        for field_name in REQUIRED_CRM_ORDER_FIELDS:
            checks.append(field_path_contract_check(sample_key, field_name, mapping.get(field_name), records))
    elif system_type in {"mes", "erp"}:
        required_fields = REQUIRED_DOMAIN_FIELDS.get(domain_target or "")
        checks.append(
            pack_contract_check(
                code="adapter.domain_target.supported",
                status="passed" if required_fields else "failed",
                sample_key=sample_key,
                message=(
                    f"{sample_key} uses supported domain_target {domain_target}"
                    if required_fields
                    else f"{sample_key} must declare a supported domain_target"
                ),
                evidence={"domain_target": domain_target, "supported": sorted(REQUIRED_DOMAIN_FIELDS)},
            )
        )
        for field_name in required_fields or ():
            if field_name == "external_id":
                source_path = config.get("external_id_path") or config.get("id_path")
            elif field_name == "status":
                source_path = config.get("status_path") or "status"
            else:
                source_path = mapping.get(field_name)
            checks.append(field_path_contract_check(sample_key, field_name, source_path, records))
    return checks


def validate_status_dictionary_contract(
    *,
    sample_key: str,
    config: dict[str, Any],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    status_path = str(config.get("status_path") or "status")
    raw_statuses = sorted({str(value) for value in (get_path(record, status_path) for record in records) if present(value)})
    mapping = config.get("status_dictionary") or config.get("status_mapping") or config.get("inbound_status_dictionary") or config.get("inbound_status_mapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    missing_statuses = sorted(status for status in raw_statuses if status.lower() not in {str(key).lower() for key in mapping})
    return [
        pack_contract_check(
            code="adapter.status_path",
            status="passed" if raw_statuses else "failed",
            sample_key=sample_key,
            message=(
                f"{sample_key} status_path resolves sample statuses"
                if raw_statuses
                else f"{sample_key} status_path must resolve at least one sample status"
            ),
            evidence={"status_path": status_path, "sample_statuses": raw_statuses[:10]},
        ),
        pack_contract_check(
            code="adapter.status_dictionary",
            status="passed" if mapping and not missing_statuses else "failed",
            sample_key=sample_key,
            message=(
                f"{sample_key} status dictionary covers all sample statuses"
                if mapping and not missing_statuses
                else f"{sample_key} status dictionary must cover every sample status"
            ),
            evidence={"dictionary_count": len(mapping), "missing_statuses": missing_statuses},
        ),
    ]


def validate_organization_acceptance_contract(
    *,
    sample_key: str,
    config: dict[str, Any],
    org_unit_codes: set[str],
    recipient_group_names: set[str],
) -> list[dict[str, Any]]:
    if sample_key not in {"crm", "mes"}:
        return []
    acceptance = config.get("organization_acceptance")
    if not isinstance(acceptance, dict):
        return [
            pack_contract_check(
                code="adapter.organization_acceptance",
                status="failed",
                sample_key=sample_key,
                message=f"{sample_key} must declare organization_acceptance for go-live recipient governance",
            )
        ]
    required_org_codes = [str(code).strip() for code in acceptance.get("required_org_unit_codes") or [] if str(code).strip()]
    required_group_names = [
        str(name).strip() for name in acceptance.get("required_recipient_group_names") or [] if str(name).strip()
    ]
    missing_org_codes = sorted(code for code in required_org_codes if code not in org_unit_codes)
    missing_group_names = sorted(name for name in required_group_names if name not in recipient_group_names)
    return [
        pack_contract_check(
            code="adapter.organization_acceptance.org_units",
            status="passed" if required_org_codes and not missing_org_codes else "failed",
            sample_key=sample_key,
            message=(
                f"{sample_key} organization_acceptance org units exist"
                if required_org_codes and not missing_org_codes
                else f"{sample_key} organization_acceptance must reference existing org units"
            ),
            evidence={"required_org_unit_codes": required_org_codes, "missing_org_unit_codes": missing_org_codes},
        ),
        pack_contract_check(
            code="adapter.organization_acceptance.recipient_groups",
            status="passed" if required_group_names and not missing_group_names else "failed",
            sample_key=sample_key,
            message=(
                f"{sample_key} organization_acceptance recipient groups exist"
                if required_group_names and not missing_group_names
                else f"{sample_key} organization_acceptance must reference existing recipient groups"
            ),
            evidence={
                "required_recipient_group_names": required_group_names,
                "missing_recipient_group_names": missing_group_names,
            },
        ),
    ]


def field_path_contract_check(
    sample_key: str,
    field_name: str,
    source_path: Any,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    source = str(source_path or "").strip()
    observed = [get_path(record, source) for record in records] if source else []
    present_count = sum(1 for value in observed if present(value))
    return pack_contract_check(
        code="adapter.field_mapping.required",
        status="passed" if source and records and present_count == len(records) else "failed",
        sample_key=sample_key,
        message=(
            f"{sample_key}.{field_name} resolves for all sample records"
            if source and records and present_count == len(records)
            else f"{sample_key}.{field_name} must map to a populated sample path"
        ),
        evidence={"field": field_name, "source_path": source or None, "sample_count": len(records), "observed_count": present_count},
    )


def required_sample_keys(required_system_types: list[str]) -> list[str]:
    keys: list[str] = []
    for system_type in required_system_types:
        for sample_key in REQUIRED_SAMPLE_KEYS_BY_SYSTEM_TYPE.get(system_type, ()):
            if sample_key not in keys:
                keys.append(sample_key)
    return keys


def normalize_domain_target(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    if text in {"", "none", "false", "off"}:
        return None
    return DOMAIN_TARGET_ALIASES.get(text, text)


def configured_record_samples(config: dict[str, Any]) -> list[dict[str, Any]]:
    records_path = str(config.get("records_path") or "records")
    pages = config.get("pages")
    records: list[dict[str, Any]] = []
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, list):
                records.extend(record for record in page if isinstance(record, dict))
            elif isinstance(page, dict):
                page_records = get_path(page, records_path)
                if page_records is None and records_path == "records":
                    page_records = page.get("items")
                if isinstance(page_records, list):
                    records.extend(record for record in page_records if isinstance(record, dict))
        return records
    raw_records = config.get("sample_records", config.get("records", []))
    if isinstance(raw_records, list):
        records.extend(record for record in raw_records if isinstance(record, dict))
    return records


def get_path(record: dict[str, Any], path: str) -> Any:
    if not path:
        return None
    value: Any = record
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def pack_contract_check(
    *,
    code: str,
    status: str,
    message: str,
    sample_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "sample_key": sample_key,
        "status": status,
        "severity": "critical" if status == "failed" else "warning" if status == "warning" else "info",
        "message": message,
        "evidence": evidence or {},
    }


def seed_organization_directory(db, directory: dict[str, Any]) -> dict[str, Any]:
    org_units = directory.get("org_units") if isinstance(directory.get("org_units"), list) else []
    recipient_groups = directory.get("recipient_groups") if isinstance(directory.get("recipient_groups"), list) else []
    seeded_users = 0
    seeded_groups = 0
    group_summaries: list[dict[str, Any]] = []
    for org_unit in org_units:
        code = str(org_unit.get("code") or "").strip()
        if not code:
            continue
        repository.create_user_account(
            db,
            schemas.UserAccountCreate(
                email=f"{code}@customer-sandbox.audit",
                display_name=str(org_unit.get("name") or code),
                password="Strong123!45",
                org_unit_code=code,
                org_unit_name=str(org_unit.get("name") or code),
                job_title=", ".join(str(role) for role in org_unit.get("expected_roles") or []),
                role_ids=[],
            ),
            hash_password,
        )
        seeded_users += 1

    for group in recipient_groups:
        name = str(group.get("name") or "").strip()
        if not name:
            continue
        recipient_group = repository.create_notification_recipient_group(
            db,
            schemas.NotificationRecipientGroupCreate(
                name=name,
                description=group.get("purpose"),
                department_codes=[str(code) for code in group.get("department_codes") or []],
                metadata={"source": "customer_sandbox_audit"},
            ),
        )
        seeded_groups += 1
        group_summaries.append(
            {
                "name": recipient_group.name,
                "department_codes": recipient_group.department_codes,
                "resolved_user_count": recipient_group.resolved_user_count,
            }
        )
    return {
        "org_unit_count": len(org_units),
        "seeded_user_count": seeded_users,
        "recipient_group_count": len(recipient_groups),
        "seeded_recipient_group_count": seeded_groups,
        "recipient_groups": group_summaries,
    }


def audit_adapter_entry(db, entry: dict[str, Any]) -> dict[str, Any]:
    adapter_report: dict[str, Any] = {
        "sample_key": entry["sample_key"],
        "system_type": entry["system_type"],
        "adapter_type": entry["adapter_type"],
        "status": "failed",
        "external_system_id": None,
        "config_id": None,
        "validation": None,
        "field_acceptance": None,
        "dictionary_signoff": None,
        "activation": None,
        "errors": [],
    }
    try:
        system = repository.create_external_system(
            db,
            schemas.ExternalSystemCreate(
                name=f"Customer Sandbox {entry['sample_key']}",
                system_type=entry["system_type"],
                enabled=True,
            ),
        )
        config = repository.create_adapter_config(
            db,
            system.id,
            schemas.AdapterConfigCreate(
                adapter_type=entry["adapter_type"],
                config=entry["config"],
                is_active=False,
            ),
        )
        if config is None:
            raise RuntimeError("adapter config could not be created")
        adapter_report["external_system_id"] = system.id
        adapter_report["config_id"] = config.id

        validation = repository.test_adapter_connection(db, config.id)
        if validation is None:
            raise RuntimeError("adapter validation result is missing")
        adapter_report["validation"] = validation.model_dump(mode="json")

        system_row = db.get(dbm.ExternalSystem, system.id)
        config_row = db.get(dbm.AdapterConfig, config.id)
        if system_row is None or config_row is None:
            raise RuntimeError("adapter rows could not be reloaded")
        acceptance = schemas.AdapterFieldAcceptanceResult.model_validate(
            evaluate_adapter_field_acceptance(db=db, system=system_row, config=config_row)
        )
        adapter_report["field_acceptance"] = summarize_acceptance(acceptance)

        if validation.status == "passed" and acceptance.required_missing_count == 0:
            adapter_report["dictionary_signoff"] = attempt_dictionary_signoff(db, system_row, config_row, entry)
            config_row = db.get(dbm.AdapterConfig, config.id)
            if config_row is None:
                raise RuntimeError("adapter row disappeared after signoff")
            adapter_report["activation"] = attempt_activation(db, config.id)
        else:
            adapter_report["dictionary_signoff"] = {
                "status": "skipped",
                "reason": "validation or field acceptance has required gaps",
            }
            adapter_report["activation"] = {
                "status": "skipped",
                "reason": "validation or field acceptance has required gaps",
            }
    except Exception as exc:
        adapter_report["errors"].append(str(exc))

    adapter_report["status"] = adapter_status(adapter_report)
    return adapter_report


def attempt_dictionary_signoff(db, system_row, config_row, entry: dict[str, Any]) -> dict[str, Any]:
    try:
        signoff = signoff_adapter_dictionary(
            db,
            system=system_row,
            config=config_row,
            request=schemas.AdapterDictionarySignoffRequest(
                approver_name="Customer Sandbox Audit",
                note="Offline customer sandbox sample-pack audit",
                accepted_unmapped_statuses=[str(value) for value in entry.get("accepted_unmapped_statuses") or []],
                confirmation=f"SIGNOFF {config_row.id}",
            ),
            actor_id="customer-sandbox-audit",
        )
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    return {
        "status": signoff.status,
        "signed_at": signoff.signed_at,
        "dictionary_keys": signoff.dictionary_keys,
        "accepted_unmapped_statuses": signoff.accepted_unmapped_statuses,
    }


def attempt_activation(db, config_id: str) -> dict[str, Any]:
    try:
        activated = repository.activate_adapter_config(db, config_id)
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    if activated is None:
        return {"status": "failed", "error": "adapter config could not be activated"}
    return {"status": "activated", "is_active": activated.is_active}


def summarize_acceptance(acceptance: schemas.AdapterFieldAcceptanceResult) -> dict[str, Any]:
    return {
        "status": acceptance.status,
        "message": acceptance.message,
        "sample_count": acceptance.sample_count,
        "domain_target": acceptance.domain_target,
        "required_missing_count": acceptance.required_missing_count,
        "unresolved_mapping_count": acceptance.unresolved_mapping_count,
        "unmapped_status_count": acceptance.unmapped_status_count,
        "failed_checks": summarize_acceptance_checks(acceptance, "failed"),
        "warning_checks": summarize_acceptance_checks(acceptance, "warning"),
    }


def summarize_acceptance_checks(
    acceptance: schemas.AdapterFieldAcceptanceResult,
    status: str,
) -> list[dict[str, Any]]:
    return [
        {
            "scope": check.scope,
            "field": check.field,
            "required": check.required,
            "source_path": check.source_path,
            "missing_count": check.missing_count,
            "sample_values": check.sample_values,
            "message": check.message,
        }
        for check in acceptance.checks
        if check.status == status
    ]


def summarize_readiness(readiness: schemas.AdapterReadinessReport) -> dict[str, Any]:
    return {
        "status": readiness.status,
        "required_system_types": readiness.required_system_types,
        "passed_count": readiness.passed_count,
        "warning_count": readiness.warning_count,
        "failed_count": readiness.failed_count,
        "failed_checks": summarize_readiness_checks(readiness, "failed"),
        "warning_checks": summarize_readiness_checks(readiness, "warning"),
    }


def summarize_readiness_checks(
    readiness: schemas.AdapterReadinessReport,
    status: str,
) -> list[dict[str, Any]]:
    return [
        {
            "code": check.code,
            "scope": check.scope,
            "severity": check.severity,
            "message": check.message,
            "target_type": check.target_type,
            "target_id": check.target_id,
            "evidence": check.evidence,
        }
        for check in readiness.checks
        if check.status == status
    ]


def adapter_status(adapter_report: dict[str, Any]) -> str:
    validation = adapter_report.get("validation") or {}
    acceptance = adapter_report.get("field_acceptance") or {}
    signoff = adapter_report.get("dictionary_signoff") or {}
    activation = adapter_report.get("activation") or {}
    if adapter_report.get("errors"):
        return "failed"
    if validation.get("status") != "passed":
        return "failed"
    if acceptance.get("required_missing_count", 0) > 0 or acceptance.get("status") == "failed":
        return "failed"
    if signoff.get("status") != "signed":
        return "failed"
    if activation.get("status") != "activated":
        return "failed"
    return "passed"


def build_summary(report: dict[str, Any], required_system_types: list[str]) -> dict[str, Any]:
    adapters = report.get("adapters") or []
    readiness = report.get("readiness") or {}
    pack_contract = report.get("pack_contract") or {}
    adapter_failed_count = sum(1 for item in adapters if item.get("status") != "passed")
    pack_contract_failed_count = int(pack_contract.get("failed_count") or 0)
    pack_contract_warning_count = int(pack_contract.get("warning_count") or 0)
    error_count = len(report.get("errors") or [])
    readiness_failed_count = int(readiness.get("failed_count") or 0)
    failed_count = error_count + pack_contract_failed_count + adapter_failed_count + readiness_failed_count
    return {
        "required_system_types": required_system_types,
        "pack_contract_status": pack_contract.get("status"),
        "pack_contract_failed_count": pack_contract_failed_count,
        "pack_contract_warning_count": pack_contract_warning_count,
        "adapter_count": len(adapters),
        "adapter_passed_count": sum(1 for item in adapters if item.get("status") == "passed"),
        "adapter_failed_count": adapter_failed_count,
        "readiness_status": readiness.get("status"),
        "readiness_warning_count": int(readiness.get("warning_count") or 0),
        "readiness_failed_count": readiness_failed_count,
        "error_count": error_count,
        "failed_count": failed_count,
    }


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a customer CRM/MES/ERP adapter sandbox sample pack.")
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK_PATH, help="Customer adapter sandbox JSON pack.")
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    parser.add_argument(
        "--required-system-types",
        default=",".join(DEFAULT_REQUIRED_SYSTEM_TYPES),
        help="Comma-separated system types that must have active ready configs.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    required_types = [item.strip() for item in args.required_system_types.split(",") if item.strip()]
    report = build_customer_sandbox_audit_report(args.pack, required_system_types=required_types)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"customer sandbox audit report: {output_path}", flush=True)
    summary = report["summary"]
    print(
        "customer sandbox audit "
        f"{report['status']} "
        f"adapters={summary['adapter_count']} "
        f"failed={summary['failed_count']} "
        f"readiness={summary.get('readiness_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
