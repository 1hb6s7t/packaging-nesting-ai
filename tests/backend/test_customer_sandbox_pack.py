from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base
from app.db import models as dbm  # noqa: F401
from app.domain import schemas
from app.services import repository
from app.services.adapters import (
    build_adapter_readiness_report,
    evaluate_adapter_field_acceptance,
    signoff_adapter_dictionary,
)
from app.services.security import hash_password


SAMPLE_PACK_PATH = REPO_ROOT / "samples" / "integrations" / "customer-sandbox" / "adapter-sandbox-pack.json"
SAMPLE_ADAPTERS = (
    ("crm", "crm"),
    ("mes", "mes"),
    ("erp_inventory", "erp"),
    ("erp_delivery", "erp"),
)


def test_customer_sandbox_pack_passes_acceptance_and_has_no_readiness_blockers(tmp_path: Path) -> None:
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    assert pack["schema_version"] == 1
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'customer-sandbox.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        with SessionLocal() as db:
            _seed_sample_organization_directory(db, pack["organization_directory"])

            active_config_ids: list[str] = []
            for sample_key, system_type in SAMPLE_ADAPTERS:
                entry = pack[sample_key]
                assert entry["system_type"] == system_type
                system = repository.create_external_system(
                    db,
                    schemas.ExternalSystemCreate(
                        name=f"Customer Sandbox {sample_key}",
                        system_type=system_type,
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
                assert config is not None

                validation = repository.test_adapter_connection(db, config.id)
                assert validation is not None
                assert validation.status == "passed", validation.message

                system_row = db.get(dbm.ExternalSystem, system.id)
                config_row = db.get(dbm.AdapterConfig, config.id)
                assert system_row is not None
                assert config_row is not None
                acceptance = schemas.AdapterFieldAcceptanceResult.model_validate(
                    evaluate_adapter_field_acceptance(db=db, system=system_row, config=config_row)
                )
                assert acceptance.status == "passed", _acceptance_issue_summary(acceptance)
                assert acceptance.required_missing_count == 0
                assert acceptance.sample_count > 0

                signoff = signoff_adapter_dictionary(
                    db,
                    system=system_row,
                    config=config_row,
                    request=schemas.AdapterDictionarySignoffRequest(
                        approver_name="Sandbox Gate",
                        note="Sample pack regression signoff",
                        confirmation=f"SIGNOFF {config.id}",
                        accepted_unmapped_statuses=[],
                    ),
                    actor_id="customer-sandbox-regression",
                )
                assert signoff.field_acceptance.status == "passed"

                activated = repository.activate_adapter_config(db, config.id)
                assert activated is not None
                assert activated.is_active is True
                active_config_ids.append(config.id)

            readiness = build_adapter_readiness_report(db, required_system_types=["crm", "mes", "erp"])
            assert readiness.failed_count == 0, _readiness_failure_summary(readiness)
            assert readiness.status in {"ready", "warning"}
            for config_id in active_config_ids:
                config_checks = [
                    check
                    for check in readiness.checks
                    if check.target_type == "adapter_config" and check.target_id == config_id
                ]
                assert any(
                    check.code == "config.validation" and check.status == "passed" for check in config_checks
                )
                assert any(
                    check.code == "field_acceptance" and check.status == "passed" for check in config_checks
                )
                assert any(
                    check.code == "dictionary_signoff" and check.status == "passed" for check in config_checks
                )
    finally:
        engine.dispose()


def _seed_sample_organization_directory(db, directory: dict) -> None:
    org_units = directory["org_units"]
    for org_unit in org_units:
        repository.create_user_account(
            db,
            schemas.UserAccountCreate(
                email=f"{org_unit['code']}@customer-sandbox.test",
                display_name=org_unit["name"],
                password="Strong123!45",
                org_unit_code=org_unit["code"],
                org_unit_name=org_unit["name"],
                job_title=", ".join(org_unit.get("expected_roles") or []),
                role_ids=[],
            ),
            hash_password,
        )

    for group in directory["recipient_groups"]:
        recipient_group = repository.create_notification_recipient_group(
            db,
            schemas.NotificationRecipientGroupCreate(
                name=group["name"],
                description=group.get("purpose"),
                department_codes=group["department_codes"],
                metadata={"source": "customer-sandbox-pack"},
            ),
        )
        assert recipient_group.resolved_user_count == len(org_units)


def _acceptance_issue_summary(acceptance: schemas.AdapterFieldAcceptanceResult) -> list[dict[str, str]]:
    return [
        {
            "scope": check.scope,
            "field": check.field,
            "status": check.status,
            "message": check.message,
        }
        for check in acceptance.checks
        if check.status != "passed"
    ]


def _readiness_failure_summary(readiness: schemas.AdapterReadinessReport) -> list[dict[str, str | None]]:
    return [
        {
            "code": check.code,
            "scope": check.scope,
            "target_id": check.target_id,
            "message": check.message,
        }
        for check in readiness.checks
        if check.status == "failed"
    ]
