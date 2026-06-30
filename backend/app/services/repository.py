from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models as dbm
from app.domain import schemas
from app.services.storage import exists as storage_exists
from app.services.storage import inspect_object as storage_inspect_object
from app.services.storage import read_bytes as storage_read_bytes
from app.services.storage import read_text as storage_read_text


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


PERMISSION_DESCRIPTIONS = {
    "orders:write": "Import and manage production orders",
    "artworks:write": "Upload, preflight, and parse artwork files",
    "sheets:write": "Manage sheet specifications",
    "nesting:write": "Create and run nesting jobs",
    "solutions:write": "Validate solutions and request production approval",
    "solutions:export": "Generate and download approved production exports",
    "solutions:archive": "Manage solution export manifests, recovery drills, and archive lifecycle",
    "solutions:approve": "Approve or reject production solutions",
    "audit:read": "Read audit logs, solver runs, and task history",
    "tasks:manage": "Cancel and retry background tasks",
    "rules:manage": "Manage rule sets and rule execution logs",
    "integrations:write": "Configure external CRM/MES/ERP integrations",
    "notifications:manage": "Manage notification templates and message dispatch policies",
    "solvers:manage": "Manage solver registry, enablement, and license policies",
    "benchmark:write": "Create and run benchmark cases",
    "batch:write": "Manage batch artwork ingestion and batch layout planning",
    "ai:use": "Use AI assistant tools",
    "rbac:manage": "Manage users, roles, and permissions",
}
PERMISSION_CODES = list(PERMISSION_DESCRIPTIONS)
ROLE_TEMPLATES = {
    "admin": {
        "description": "Built-in administrator role with all permissions",
        "permission_codes": PERMISSION_CODES,
    },
    "print_planner": {
        "description": "Plans orders, artwork, sheets, and nesting jobs",
        "permission_codes": [
            "orders:write",
            "artworks:write",
            "batch:write",
            "sheets:write",
            "nesting:write",
            "rules:manage",
            "ai:use",
        ],
    },
    "production_operator": {
        "description": "Runs production tasks and exports approved solutions",
        "permission_codes": ["nesting:write", "solutions:write", "solutions:export", "tasks:manage", "audit:read"],
    },
    "solution_approver": {
        "description": "Reviews Validator reports and approves production release",
        "permission_codes": ["solutions:approve", "audit:read"],
    },
    "auditor": {
        "description": "Reads operation logs, solver runs, and task records",
        "permission_codes": ["audit:read"],
    },
    "operations_manager": {
        "description": "Runs operational maintenance, export retention, and recovery drills",
        "permission_codes": ["solutions:archive", "tasks:manage", "audit:read"],
    },
    "integration_manager": {
        "description": "Configures external system adapters",
        "permission_codes": ["integrations:write", "notifications:manage", "audit:read"],
    },
    "benchmark_engineer": {
        "description": "Runs solver benchmarks and monitors task execution",
        "permission_codes": [
            "benchmark:write",
            "batch:write",
            "solvers:manage",
            "nesting:write",
            "tasks:manage",
            "audit:read",
        ],
    },
}

TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
SENSITIVE_CONFIG_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "access_key",
    "private_key",
    "authorization",
)
SENSITIVE_VALUE_PLACEHOLDER = "***"
SENSITIVE_QUERY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "KEY",
    "AUTH",
    "SIGNATURE",
    "CREDENTIAL",
)
SENSITIVE_EXACT_KEYS = {
    "webhook_url",
    "webhook_endpoint_url",
}
SENSITIVE_KEY_EXEMPTIONS = {
    "api_key_header",
    "callback_token_history",
    "callback_token_rotated_at",
    "signature_header",
    "signature_timestamp_header",
    "token_rotation",
    "webhook_signature_header",
    "webhook_signature_timestamp_header",
}
SENSITIVE_KEY_SUFFIX_EXEMPTIONS = ("_tail", "_hash", "_fingerprint")
BLOCKED_INVENTORY_STATUSES = {
    "blocked",
    "disabled",
    "expired",
    "frozen",
    "hold",
    "inactive",
    "locked",
    "quality_hold",
    "quarantine",
    "scrapped",
    "unavailable",
}
BLOCKED_OPERATION_STATUSES = {
    "blocked",
    "cancelled",
    "canceled",
    "failed",
    "hold",
    "paused",
    "quality_hold",
    "rejected",
    "returned",
    "scrapped",
}
READY_SCHEDULE_STATUSES = {
    "planned",
    "pending_release",
    "queued",
    "ready",
    "ready_to_run",
    "released",
    "scheduled",
    "wait_release",
}
RUNNING_SCHEDULE_STATUSES = {"in_progress", "processing", "producing", "running", "started"}
COMPLETED_OPERATION_STATUSES = {"closed", "completed", "delivered", "done", "finished", "signed"}
DEFAULT_SOLVER_REGISTRY = [
    {"name": schemas.SolverName.rectpack.value, "version": "shelf-0.2.0", "enabled": True, "license_policy": "open_source"},
    {
        "name": schemas.SolverName.ortools.value,
        "version": "external-adapter-stub-0.1.0",
        "enabled": False,
        "license_policy": "review_required",
    },
    {
        "name": schemas.SolverName.packing_solver.value,
        "version": "external-adapter-stub-0.1.0",
        "enabled": False,
        "license_policy": "commercial",
    },
    {
        "name": schemas.SolverName.sparrow.value,
        "version": "external-adapter-stub-0.1.0",
        "enabled": False,
        "license_policy": "commercial",
    },
    {
        "name": schemas.SolverName.phoenix.value,
        "version": "external-adapter-stub-0.1.0",
        "enabled": False,
        "license_policy": "commercial",
    },
]
UNCONFIGURED_SOLVER_VERSION_PREFIX = "external-adapter-stub"


def seed_rbac(db: Session, hash_password_func) -> None:
    settings = get_settings()
    admin = get_user_by_email(db, settings.default_admin_email)
    if admin is None:
        admin = dbm.UserAccount(
            email=settings.default_admin_email,
            display_name="System Admin",
            hashed_password=hash_password_func(settings.default_admin_password),
            is_active=True,
        )
        db.add(admin)
        db.flush()

    permissions_by_code: dict[str, dbm.Permission] = {}
    for code in PERMISSION_CODES:
        permission = db.scalar(select(dbm.Permission).where(dbm.Permission.code == code))
        if permission is None:
            permission = dbm.Permission(code=code, description=PERMISSION_DESCRIPTIONS[code])
            db.add(permission)
            db.flush()
        elif not permission.description or permission.description == f"Permission {code}":
            permission.description = PERMISSION_DESCRIPTIONS[code]
        permissions_by_code[code] = permission

    seeded_roles: dict[str, dbm.Role] = {}
    for role_name, template in ROLE_TEMPLATES.items():
        role = db.scalar(select(dbm.Role).where(dbm.Role.name == role_name))
        created = False
        if role is None:
            role = dbm.Role(name=role_name, description=template["description"])
            db.add(role)
            db.flush()
            created = True
        elif role_name == "admin":
            role.description = template["description"]
        elif not role.description:
            role.description = template["description"]
        if created or role_name in {
            "admin",
            "benchmark_engineer",
            "integration_manager",
            "operations_manager",
            "print_planner",
            "production_operator",
        }:
            grant_role_permissions(db, role.id, template["permission_codes"], permissions_by_code)
        seeded_roles[role_name] = role

    admin_role = seeded_roles["admin"]
    existing_user_role = db.scalar(
        select(dbm.UserRole).where(dbm.UserRole.user_id == admin.id, dbm.UserRole.role_id == admin_role.id)
    )
    if existing_user_role is None:
        db.add(dbm.UserRole(user_id=admin.id, role_id=admin_role.id))
    db.commit()


def grant_role_permissions(
    db: Session,
    role_id: str,
    permission_codes: list[str],
    permissions_by_code: dict[str, dbm.Permission],
) -> None:
    existing_permission_ids = set(
        db.scalars(select(dbm.RolePermission.permission_id).where(dbm.RolePermission.role_id == role_id)).all()
    )
    for code in sorted(set(permission_codes)):
        permission = permissions_by_code[code]
        if permission.id not in existing_permission_ids:
            db.add(dbm.RolePermission(role_id=role_id, permission_id=permission.id))


def get_user_by_email(db: Session, email: str) -> dbm.UserAccount | None:
    return db.scalar(select(dbm.UserAccount).where(dbm.UserAccount.email == email))


def current_user_from_db(db: Session, user_id: str) -> schemas.CurrentUser:
    user = db.get(dbm.UserAccount, user_id)
    if user is None or not user.is_active:
        raise ValueError("user not found or inactive")
    role_rows = db.scalars(
        select(dbm.Role)
        .join(dbm.UserRole, dbm.UserRole.role_id == dbm.Role.id)
        .where(dbm.UserRole.user_id == user_id)
    ).all()
    role_ids = [role.id for role in role_rows]
    if role_ids:
        permission_rows = db.scalars(
            select(dbm.Permission)
            .join(dbm.RolePermission, dbm.RolePermission.permission_id == dbm.Permission.id)
            .where(dbm.RolePermission.role_id.in_(role_ids))
        ).all()
    else:
        permission_rows = []
    return schemas.CurrentUser(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        org_unit_code=user.org_unit_code,
        org_unit_name=user.org_unit_name,
        job_title=user.job_title,
        external_user_id=user.external_user_id,
        roles=[role.name for role in role_rows],
        permissions=sorted({permission.code for permission in permission_rows}),
    )


def list_permissions(db: Session) -> list[schemas.PermissionRead]:
    rows = db.scalars(select(dbm.Permission).order_by(dbm.Permission.code)).all()
    return [permission_from_row(row) for row in rows]


def list_roles(db: Session) -> list[schemas.RoleRead]:
    rows = db.scalars(select(dbm.Role).order_by(dbm.Role.name)).all()
    return [role_from_row(db, row) for row in rows]


def list_user_accounts(db: Session) -> list[schemas.UserAccountRead]:
    rows = db.scalars(select(dbm.UserAccount).order_by(dbm.UserAccount.email)).all()
    return [user_account_from_row(db, row) for row in rows]


def create_user_account(db: Session, payload: schemas.UserAccountCreate, hash_password_func) -> schemas.UserAccountRead:
    if get_user_by_email(db, payload.email) is not None:
        raise ValueError("email already exists")
    user = dbm.UserAccount(
        email=payload.email,
        display_name=payload.display_name,
        hashed_password=hash_password_func(payload.password),
        is_active=payload.is_active,
        org_unit_code=payload.org_unit_code,
        org_unit_name=payload.org_unit_name,
        job_title=payload.job_title,
        external_user_id=payload.external_user_id,
    )
    db.add(user)
    try:
        db.flush()
        replace_user_roles(db, user.id, payload.role_ids)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("email already exists") from exc
    except ValueError:
        db.rollback()
        raise
    return user_account_from_row(db, user)


def update_user_account(
    db: Session,
    user_id: str,
    payload: schemas.UserAccountUpdate,
    hash_password_func,
) -> schemas.UserAccountRead:
    user = db.get(dbm.UserAccount, user_id)
    if user is None:
        raise ValueError("user not found")
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.password is not None:
        user.hashed_password = hash_password_func(payload.password)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if "org_unit_code" in payload.model_fields_set:
        user.org_unit_code = payload.org_unit_code
    if "org_unit_name" in payload.model_fields_set:
        user.org_unit_name = payload.org_unit_name
    if "job_title" in payload.model_fields_set:
        user.job_title = payload.job_title
    if "external_user_id" in payload.model_fields_set:
        user.external_user_id = payload.external_user_id
    if payload.role_ids is not None:
        replace_user_roles(db, user.id, payload.role_ids)
    db.commit()
    return user_account_from_row(db, user)


def create_role(db: Session, payload: schemas.RoleCreate) -> schemas.RoleRead:
    existing = db.scalar(select(dbm.Role).where(dbm.Role.name == payload.name))
    if existing is not None:
        raise ValueError("role name already exists")
    role = dbm.Role(name=payload.name, description=payload.description)
    db.add(role)
    try:
        db.flush()
        replace_role_permissions(db, role.id, payload.permission_codes)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("role name already exists") from exc
    except ValueError:
        db.rollback()
        raise
    return role_from_row(db, role)


def update_role(db: Session, role_id: str, payload: schemas.RoleUpdate) -> schemas.RoleRead:
    role = db.get(dbm.Role, role_id)
    if role is None:
        raise ValueError("role not found")
    if payload.name is not None:
        role.name = payload.name
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_codes is not None:
        replace_role_permissions(db, role.id, payload.permission_codes)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("role name already exists") from exc
    return role_from_row(db, role)


def replace_user_roles(db: Session, user_id: str, role_ids: list[str]) -> None:
    unique_role_ids = sorted(set(role_ids))
    if unique_role_ids:
        existing_ids = set(db.scalars(select(dbm.Role.id).where(dbm.Role.id.in_(unique_role_ids))).all())
        missing = sorted(set(unique_role_ids) - existing_ids)
        if missing:
            raise ValueError(f"unknown role ids: {', '.join(missing)}")
    db.execute(delete(dbm.UserRole).where(dbm.UserRole.user_id == user_id))
    for role_id in unique_role_ids:
        db.add(dbm.UserRole(user_id=user_id, role_id=role_id))


def replace_role_permissions(db: Session, role_id: str, permission_codes: list[str]) -> None:
    unique_codes = sorted(set(permission_codes))
    if unique_codes:
        existing_rows = db.scalars(select(dbm.Permission).where(dbm.Permission.code.in_(unique_codes))).all()
        by_code = {row.code: row for row in existing_rows}
        missing = sorted(set(unique_codes) - set(by_code))
        if missing:
            raise ValueError(f"unknown permission codes: {', '.join(missing)}")
    else:
        by_code = {}
    db.execute(delete(dbm.RolePermission).where(dbm.RolePermission.role_id == role_id))
    for code in unique_codes:
        db.add(dbm.RolePermission(role_id=role_id, permission_id=by_code[code].id))


def permission_from_row(row: dbm.Permission) -> schemas.PermissionRead:
    return schemas.PermissionRead(id=row.id, code=row.code, description=row.description)


def role_from_row(db: Session, row: dbm.Role) -> schemas.RoleRead:
    permission_codes = list(
        db.scalars(
            select(dbm.Permission.code)
            .join(dbm.RolePermission, dbm.RolePermission.permission_id == dbm.Permission.id)
            .where(dbm.RolePermission.role_id == row.id)
            .order_by(dbm.Permission.code)
        ).all()
    )
    return schemas.RoleRead(
        id=row.id,
        name=row.name,
        description=row.description,
        permission_codes=permission_codes,
    )


def user_account_from_row(db: Session, row: dbm.UserAccount) -> schemas.UserAccountRead:
    role_rows = db.scalars(
        select(dbm.Role)
        .join(dbm.UserRole, dbm.UserRole.role_id == dbm.Role.id)
        .where(dbm.UserRole.user_id == row.id)
        .order_by(dbm.Role.name)
    ).all()
    role_reads = [role_from_row(db, role) for role in role_rows]
    permissions = sorted({code for role in role_reads for code in role.permission_codes})
    return schemas.UserAccountRead(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        is_active=row.is_active,
        org_unit_code=row.org_unit_code,
        org_unit_name=row.org_unit_name,
        job_title=row.job_title,
        external_user_id=row.external_user_id,
        roles=role_reads,
        permissions=permissions,
    )


def upsert_order(db: Session, order: schemas.ProductionOrder) -> schemas.ProductionOrder:
    row = db.get(dbm.ProductionOrder, order.order_id)
    values = {
        "id": order.order_id,
        "external_order_id": order.external_order_id,
        "customer_id": order.customer_id,
        "product_id": order.product_id,
        "customer_name": order.customer_name,
        "product_name": order.product_name,
        "category": order.category,
        "is_repeat_order": order.is_repeat_order,
        "quote_amount": order.quote_amount,
        "contacted": order.contacted,
        "due_date": order.due_date.isoformat() if order.due_date else None,
        "quantity": order.quantity,
        "material": order.material,
        "thickness": order.thickness,
        "print_side": order.print_side,
        "print_method": order.print_method,
        "color_count": order.color_count,
        "spot_color": order.spot_color,
        "surface_finish": order.surface_finish,
        "artwork_file_id": order.artwork_file_id,
        "allowed_rotations": order.allowed_rotations,
        "allow_mirror": order.allow_mirror,
        "min_gap_mm": order.min_gap_mm,
        "bleed_mm": order.bleed_mm,
        "priority_note": order.priority_note,
        "source_type": order.source_type,
    }
    if row is None:
        db.add(dbm.ProductionOrder(**values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return order


def list_orders(db: Session) -> list[schemas.ProductionOrder]:
    return [order_from_row(row) for row in db.scalars(select(dbm.ProductionOrder).order_by(dbm.ProductionOrder.created_at))]


def get_order(db: Session, order_id: str) -> schemas.ProductionOrder | None:
    row = db.get(dbm.ProductionOrder, order_id)
    return order_from_row(row) if row else None


def order_from_row(row: dbm.ProductionOrder) -> schemas.ProductionOrder:
    return schemas.ProductionOrder.model_validate(
        {
            "order_id": row.id,
            "external_order_id": row.external_order_id,
            "customer_id": row.customer_id,
            "customer_name": row.customer_name,
            "product_id": row.product_id,
            "product_name": row.product_name,
            "category": row.category,
            "is_repeat_order": row.is_repeat_order,
            "quote_amount": row.quote_amount,
            "contacted": row.contacted,
            "due_date": row.due_date,
            "quantity": row.quantity,
            "material": row.material,
            "thickness": row.thickness,
            "print_side": row.print_side,
            "print_method": row.print_method,
            "color_count": row.color_count,
            "spot_color": row.spot_color,
            "surface_finish": row.surface_finish,
            "artwork_file_id": row.artwork_file_id,
            "allowed_rotations": row.allowed_rotations,
            "allow_mirror": row.allow_mirror,
            "min_gap_mm": row.min_gap_mm,
            "bleed_mm": row.bleed_mm,
            "priority_note": row.priority_note,
            "source_type": row.source_type,
        }
    )


def ensure_default_rule_set(db: Session) -> schemas.RuleSetRead:
    from app.services.rules import DEFAULT_RULE_SET

    default_name = "Packaging Default"
    default_version = "v1"
    active_row = db.scalar(select(dbm.RuleSet).where(dbm.RuleSet.is_active.is_(True)))
    row = db.scalar(
        select(dbm.RuleSet).where(dbm.RuleSet.name == default_name, dbm.RuleSet.version == default_version)
    )
    created = False
    if row is None:
        row = dbm.RuleSet(
            name=default_name,
            version=default_version,
            is_active=active_row is None,
            definition=DEFAULT_RULE_SET.model_dump(mode="json"),
        )
        db.add(row)
        db.flush()
        _replace_rule_items(db, row.id, DEFAULT_RULE_SET)
        created = True
    elif active_row is None:
        row.is_active = True
    if created or active_row is None:
        db.commit()
    return rule_set_from_row(row)


def seed_solver_registry(db: Session) -> None:
    for template in DEFAULT_SOLVER_REGISTRY:
        row = db.scalar(select(dbm.SolverRegistry).where(dbm.SolverRegistry.name == template["name"]))
        if row is None:
            db.add(dbm.SolverRegistry(**template))
        else:
            if not row.version:
                row.version = template["version"]
            if not row.license_policy:
                row.license_policy = template["license_policy"]
    db.commit()


def list_solver_registry(db: Session) -> list[schemas.SolverRegistryRead]:
    rows = db.scalars(select(dbm.SolverRegistry).order_by(dbm.SolverRegistry.name)).all()
    if not rows:
        seed_solver_registry(db)
        rows = db.scalars(select(dbm.SolverRegistry).order_by(dbm.SolverRegistry.name)).all()
    return [solver_registry_from_row(row) for row in rows]


def get_solver_registry_entry(db: Session, solver_name: str) -> schemas.SolverRegistryRead | None:
    row = db.scalar(select(dbm.SolverRegistry).where(dbm.SolverRegistry.name == solver_name))
    return solver_registry_from_row(row) if row else None


def update_solver_registry_entry(
    db: Session,
    solver_name: str,
    payload: schemas.SolverRegistryUpdate,
) -> schemas.SolverRegistryRead | None:
    row = db.scalar(select(dbm.SolverRegistry).where(dbm.SolverRegistry.name == solver_name))
    if row is None:
        return None
    next_version = payload.version if payload.version is not None else row.version
    next_enabled = payload.enabled if payload.enabled is not None else row.enabled
    next_license_policy = payload.license_policy if payload.license_policy is not None else row.license_policy
    _validate_solver_registry_state(
        row.name,
        version=next_version,
        enabled=next_enabled,
        license_policy=next_license_policy,
    )
    if payload.version is not None:
        row.version = payload.version
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.license_policy is not None:
        row.license_policy = payload.license_policy
    db.commit()
    return solver_registry_from_row(row)


def ensure_solver_enabled(db: Session, solver_name: str) -> schemas.SolverRegistryRead:
    row = db.scalar(select(dbm.SolverRegistry).where(dbm.SolverRegistry.name == solver_name))
    if row is None:
        seed_solver_registry(db)
        row = db.scalar(select(dbm.SolverRegistry).where(dbm.SolverRegistry.name == solver_name))
    if row is None:
        raise ValueError(f"solver is not registered: {solver_name}")
    if not row.enabled:
        raise ValueError(f"solver is disabled: {solver_name}")
    _validate_solver_registry_state(
        row.name,
        version=row.version,
        enabled=row.enabled,
        license_policy=row.license_policy,
    )
    return solver_registry_from_row(row)


def _validate_solver_registry_state(
    solver_name: str,
    *,
    version: str,
    enabled: bool,
    license_policy: str,
) -> None:
    if not enabled:
        return
    if _is_unconfigured_solver_version(version):
        raise ValueError(f"solver adapter is not configured: {solver_name}")
    if license_policy == "disabled":
        raise ValueError(f"solver license policy is disabled: {solver_name}")


def _is_unconfigured_solver_version(version: str) -> bool:
    return version.strip().lower().startswith(UNCONFIGURED_SOLVER_VERSION_PREFIX)


def solver_registry_from_row(row: dbm.SolverRegistry) -> schemas.SolverRegistryRead:
    return schemas.SolverRegistryRead(
        id=row.id,
        name=row.name,
        version=row.version,
        enabled=row.enabled,
        license_policy=row.license_policy,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def list_rule_sets(db: Session) -> list[schemas.RuleSetRead]:
    rows = db.scalars(
        select(dbm.RuleSet).order_by(dbm.RuleSet.is_active.desc(), dbm.RuleSet.created_at.desc())
    ).all()
    if not rows:
        return [ensure_default_rule_set(db)]
    return [rule_set_from_row(row) for row in rows]


def get_active_rule_set(db: Session) -> schemas.RuleSetRead:
    row = db.scalar(select(dbm.RuleSet).where(dbm.RuleSet.is_active.is_(True)).order_by(dbm.RuleSet.updated_at.desc()))
    if row is None:
        return ensure_default_rule_set(db)
    return rule_set_from_row(row)


def create_rule_set(db: Session, payload: schemas.RuleSetCreate) -> schemas.RuleSetRead:
    existing = db.scalar(
        select(dbm.RuleSet).where(dbm.RuleSet.name == payload.name, dbm.RuleSet.version == payload.version)
    )
    if existing is not None:
        raise ValueError("rule set name/version already exists")
    if payload.is_active:
        for active_row in db.scalars(select(dbm.RuleSet).where(dbm.RuleSet.is_active.is_(True))).all():
            active_row.is_active = False
    row = dbm.RuleSet(
        name=payload.name,
        version=payload.version,
        is_active=payload.is_active,
        definition=payload.definition.model_dump(mode="json"),
    )
    db.add(row)
    try:
        db.flush()
        _replace_rule_items(db, row.id, payload.definition)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("rule set name/version already exists") from exc
    return rule_set_from_row(row)


def activate_rule_set(db: Session, rule_set_id: str) -> schemas.RuleSetRead | None:
    row = db.get(dbm.RuleSet, rule_set_id)
    if row is None:
        return None
    for active_row in db.scalars(select(dbm.RuleSet).where(dbm.RuleSet.is_active.is_(True))).all():
        active_row.is_active = False
    row.is_active = True
    db.commit()
    return rule_set_from_row(row)


def list_rule_execution_logs(
    db: Session,
    *,
    rule_set_id: str | None = None,
    order_id: str | None = None,
    limit: int = 100,
) -> list[schemas.RuleExecutionLogRead]:
    query = select(dbm.RuleExecutionLog).order_by(dbm.RuleExecutionLog.created_at.desc()).limit(limit)
    if rule_set_id:
        query = query.where(dbm.RuleExecutionLog.rule_set_id == rule_set_id)
    if order_id:
        query = query.where(dbm.RuleExecutionLog.order_id == order_id)
    return [rule_execution_log_from_row(row) for row in db.scalars(query).all()]


def log_rule_execution(
    db: Session,
    *,
    rule_set_id: str | None,
    order_id: str | None,
    result: dict[str, Any],
    commit: bool = True,
) -> schemas.RuleExecutionLogRead:
    row = dbm.RuleExecutionLog(rule_set_id=rule_set_id, order_id=order_id, result=result)
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return rule_execution_log_from_row(row)


def rule_set_from_row(row: dbm.RuleSet) -> schemas.RuleSetRead:
    return schemas.RuleSetRead(
        id=row.id,
        name=row.name,
        version=row.version,
        is_active=row.is_active,
        definition=schemas.RuleSet.model_validate(row.definition or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def rule_execution_log_from_row(row: dbm.RuleExecutionLog) -> schemas.RuleExecutionLogRead:
    return schemas.RuleExecutionLogRead(
        id=row.id,
        rule_set_id=row.rule_set_id,
        order_id=row.order_id,
        result=row.result,
        created_at=row.created_at.isoformat(),
    )


def _replace_rule_items(db: Session, rule_set_id: str, definition: schemas.RuleSet) -> None:
    db.execute(delete(dbm.RuleItem).where(dbm.RuleItem.rule_set_id == rule_set_id))
    for rule in definition.hard_constraints:
        db.add(
            dbm.RuleItem(
                rule_set_id=rule_set_id,
                name=rule.name,
                rule_type="hard_constraint",
                expression=rule.when,
                weight=None,
            )
        )
    for rule in definition.soft_scores:
        db.add(
            dbm.RuleItem(
                rule_set_id=rule_set_id,
                name=rule.name,
                rule_type="soft_score",
                expression=rule.expression,
                weight=rule.weight,
            )
        )


def upsert_sheet(db: Session, sheet: schemas.SheetSpec) -> schemas.SheetSpec:
    row = db.get(dbm.SheetSpec, sheet.sheet_id)
    values = {
        "id": sheet.sheet_id,
        "name": sheet.name or sheet.sheet_id,
        "width_mm": sheet.width,
        "height_mm": sheet.height,
        "margin_top_mm": sheet.margin_top,
        "margin_right_mm": sheet.margin_right,
        "margin_bottom_mm": sheet.margin_bottom,
        "margin_left_mm": sheet.margin_left,
        "gripper_mm": sheet.gripper_mm,
        "material": sheet.material,
        "thickness": sheet.thickness,
        "cost_per_sheet": sheet.cost_per_sheet,
    }
    if row is None:
        db.add(dbm.SheetSpec(**values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return sheet


def list_sheets(db: Session) -> list[schemas.SheetSpec]:
    return [sheet_from_row(row) for row in db.scalars(select(dbm.SheetSpec).order_by(dbm.SheetSpec.created_at))]


def get_sheet(db: Session, sheet_id: str) -> schemas.SheetSpec | None:
    row = db.get(dbm.SheetSpec, sheet_id)
    return sheet_from_row(row) if row else None


def sheet_from_row(row: dbm.SheetSpec) -> schemas.SheetSpec:
    return schemas.SheetSpec(
        sheet_id=row.id,
        name=row.name,
        width=row.width_mm,
        height=row.height_mm,
        margin_top=row.margin_top_mm,
        margin_right=row.margin_right_mm,
        margin_bottom=row.margin_bottom_mm,
        margin_left=row.margin_left_mm,
        gripper_mm=row.gripper_mm,
        material=row.material,
        thickness=row.thickness,
        cost_per_sheet=row.cost_per_sheet,
    )


def create_artwork(
    db: Session,
    *,
    artwork_id: str,
    filename: str,
    content_type: str,
    checksum: str,
    source_format: str,
    storage_key: str,
    preflight_report: schemas.PreflightReport,
) -> None:
    row = db.get(dbm.ArtworkFile, artwork_id)
    values = {
        "id": artwork_id,
        "filename": filename,
        "content_type": content_type,
        "checksum": checksum,
        "source_format": source_format,
        "storage_key": storage_key,
        "status": "uploaded",
    }
    if row is None:
        db.add(dbm.ArtworkFile(**values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()

    db.execute(delete(dbm.FilePreflightReport).where(dbm.FilePreflightReport.artwork_file_id == artwork_id))
    db.add(
        dbm.FilePreflightReport(
            artwork_file_id=artwork_id,
            can_parse_directly=preflight_report.can_parse_directly,
            requires_conversion=preflight_report.requires_conversion,
            requires_manual_review=preflight_report.requires_manual_review,
            report=preflight_report.model_dump(mode="json"),
        )
    )
    db.commit()


def list_artworks(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(dbm.ArtworkFile).order_by(dbm.ArtworkFile.created_at))
    return [artwork_meta_from_row(row) for row in rows]


def get_artwork_meta(db: Session, artwork_id: str) -> dict[str, Any] | None:
    row = db.get(dbm.ArtworkFile, artwork_id)
    return artwork_meta_from_row(row) if row else None


def artwork_meta_from_row(row: dbm.ArtworkFile) -> dict[str, Any]:
    return {
        "artwork_id": row.id,
        "filename": row.filename,
        "content_type": row.content_type,
        "checksum": row.checksum,
        "source_format": row.source_format,
        "storage_key": row.storage_key,
        "status": row.status,
    }


def get_preflight_report(db: Session, artwork_id: str) -> schemas.PreflightReport | None:
    row = db.scalar(select(dbm.FilePreflightReport).where(dbm.FilePreflightReport.artwork_file_id == artwork_id))
    return schemas.PreflightReport.model_validate(row.report) if row else None


def create_file_conversion_job(
    db: Session,
    *,
    artwork_id: str,
    source_format: str,
    target_format: str,
    status: str,
    log: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> schemas.FileConversionJobRead:
    row = dbm.FileConversionJob(
        artwork_file_id=artwork_id,
        source_format=source_format,
        target_format=target_format,
        status=status,
        log=log,
        metadata_json=metadata or {},
    )
    db.add(row)
    db.commit()
    return file_conversion_job_from_row(row)


def update_file_conversion_job(
    db: Session,
    job_id: str,
    payload: schemas.FileConversionJobUpdate,
) -> schemas.FileConversionJobRead | None:
    row = db.get(dbm.FileConversionJob, job_id)
    if row is None:
        return None
    row.status = payload.status
    row.log = payload.log
    db.commit()
    return file_conversion_job_from_row(row)


def set_file_conversion_job_status(
    db: Session,
    job_id: str,
    *,
    status: str,
    log: str | None = None,
    metadata_update: dict[str, Any] | None = None,
) -> schemas.FileConversionJobRead | None:
    row = db.get(dbm.FileConversionJob, job_id)
    if row is None:
        return None
    row.status = status
    row.log = log
    if metadata_update:
        metadata = dict(row.metadata_json or {})
        metadata.update(metadata_update)
        row.metadata_json = metadata
    db.commit()
    return file_conversion_job_from_row(row)


def update_file_conversion_job_metadata(
    db: Session,
    job_id: str,
    metadata_update: dict[str, Any],
) -> schemas.FileConversionJobRead | None:
    row = db.get(dbm.FileConversionJob, job_id)
    if row is None:
        return None
    metadata = dict(row.metadata_json or {})
    metadata.update(metadata_update)
    row.metadata_json = metadata
    db.commit()
    return file_conversion_job_from_row(row)


def list_overdue_file_conversion_job_rows(
    db: Session,
    *,
    now_iso: str,
    fallback_sla_minutes: int | None = None,
) -> list[dbm.FileConversionJob]:
    rows = db.scalars(
        select(dbm.FileConversionJob)
        .where(dbm.FileConversionJob.status == "queued")
        .order_by(dbm.FileConversionJob.created_at.asc())
    ).all()
    overdue_rows: list[dbm.FileConversionJob] = []
    for row in rows:
        metadata = row.metadata_json or {}
        sla_due_at = metadata.get("sla_due_at")
        if sla_due_at and str(sla_due_at) <= now_iso:
            overdue_rows.append(row)
            continue
        if fallback_sla_minutes is not None:
            fallback_due_at = row.updated_at + timedelta(minutes=max(1, fallback_sla_minutes))
            if fallback_due_at.isoformat() <= now_iso:
                overdue_rows.append(row)
    return overdue_rows


def list_file_conversion_jobs(
    db: Session,
    *,
    artwork_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[schemas.FileConversionJobRead]:
    query = select(dbm.FileConversionJob).order_by(dbm.FileConversionJob.created_at.desc()).limit(limit)
    if artwork_id:
        query = query.where(dbm.FileConversionJob.artwork_file_id == artwork_id)
    if status:
        query = query.where(dbm.FileConversionJob.status == status)
    return [file_conversion_job_from_row(row) for row in db.scalars(query).all()]


def get_file_conversion_job(db: Session, job_id: str) -> schemas.FileConversionJobRead | None:
    row = db.get(dbm.FileConversionJob, job_id)
    return file_conversion_job_from_row(row) if row else None


def get_file_conversion_job_row(db: Session, job_id: str) -> dbm.FileConversionJob | None:
    return db.get(dbm.FileConversionJob, job_id)


def file_conversion_job_from_row(row: dbm.FileConversionJob) -> schemas.FileConversionJobRead:
    return schemas.FileConversionJobRead(
        id=row.id,
        artwork_file_id=row.artwork_file_id,
        source_format=row.source_format,
        target_format=row.target_format,
        status=row.status,
        log=row.log,
        metadata=public_file_conversion_metadata(row.metadata_json or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def public_file_conversion_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive_payload(metadata)
    return redacted if isinstance(redacted, dict) else {}


def create_artwork_version(
    db: Session,
    *,
    artwork_id: str,
    normalized_storage_key: str,
    target_format: str,
    checksum: str,
    metadata: dict[str, Any] | None = None,
) -> schemas.ArtworkVersionRead:
    artwork = db.get(dbm.ArtworkFile, artwork_id)
    if artwork is None:
        raise ValueError("artwork not found")
    existing_versions = db.scalars(
        select(dbm.ArtworkVersion).where(dbm.ArtworkVersion.artwork_file_id == artwork_id)
    ).all()
    version = max((row.version for row in existing_versions), default=0) + 1
    metadata_json = {
        **(metadata or {}),
        "target_format": target_format,
        "checksum": checksum,
        "previous_source_format": artwork.source_format,
        "previous_storage_key": artwork.storage_key,
    }
    row = dbm.ArtworkVersion(
        artwork_file_id=artwork_id,
        version=version,
        normalized_storage_key=normalized_storage_key,
        metadata_json=metadata_json,
    )
    db.add(row)
    artwork.source_format = target_format
    artwork.storage_key = normalized_storage_key
    artwork.checksum = checksum
    artwork.status = "converted"
    db.commit()
    return artwork_version_from_row(row)


def list_artwork_versions(db: Session, artwork_id: str) -> list[schemas.ArtworkVersionRead]:
    rows = db.scalars(
        select(dbm.ArtworkVersion)
        .where(dbm.ArtworkVersion.artwork_file_id == artwork_id)
        .order_by(dbm.ArtworkVersion.version.desc())
    ).all()
    return [artwork_version_from_row(row) for row in rows]


def artwork_version_from_row(row: dbm.ArtworkVersion) -> schemas.ArtworkVersionRead:
    return schemas.ArtworkVersionRead(
        id=row.id,
        artwork_file_id=row.artwork_file_id,
        version=row.version,
        normalized_storage_key=row.normalized_storage_key,
        metadata=row.metadata_json,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def save_polygons(db: Session, artwork_id: str, polygons: list[schemas.PolygonAsset]) -> None:
    db.execute(delete(dbm.PolygonAsset).where(dbm.PolygonAsset.artwork_file_id == artwork_id))
    for polygon in polygons:
        bbox = polygon.bbox
        db.add(
            dbm.PolygonAsset(
                id=f"{artwork_id}:{polygon.shape_id}",
                artwork_file_id=artwork_id,
                unit=polygon.unit,
                polygon_json=polygon.model_dump(mode="json"),
                area=polygon.area or 0,
                bbox_width=bbox.width if bbox else 0,
                bbox_height=bbox.height if bbox else 0,
            )
        )
    artwork = db.get(dbm.ArtworkFile, artwork_id)
    if artwork:
        artwork.status = "parsed"
    db.commit()


def get_polygons(db: Session, artwork_id: str) -> list[schemas.PolygonAsset]:
    rows = db.scalars(select(dbm.PolygonAsset).where(dbm.PolygonAsset.artwork_file_id == artwork_id))
    return [schemas.PolygonAsset.model_validate(row.polygon_json) for row in rows]


def load_artwork_content(db: Session, artwork_id: str) -> str | None:
    row = db.get(dbm.ArtworkFile, artwork_id)
    if not row:
        return None
    if not storage_exists(row.storage_key):
        return None
    return storage_read_text(row.storage_key, encoding="utf-8", errors="ignore")


def upsert_job(db: Session, job: schemas.NestingJob) -> schemas.NestingJob:
    row = db.get(dbm.NestingJob, job.job_id)
    values = {
        "id": job.job_id,
        "status": "created",
        "sheet_spec_id": job.sheet.sheet_id,
        "input_json": job.model_dump(mode="json"),
        "objective": job.objective.model_dump(mode="json"),
        "top_k": job.top_k,
        "time_limit_sec": job.time_limit_sec,
    }
    if row is None:
        db.add(dbm.NestingJob(**values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.flush()
    db.execute(delete(dbm.NestingJobItem).where(dbm.NestingJobItem.nesting_job_id == job.job_id))
    for item in job.fixed_items:
        db.add(
            dbm.NestingJobItem(
                nesting_job_id=job.job_id,
                order_id=item.order_id,
                item_json=item.model_dump(mode="json"),
                role="fixed",
            )
        )
    for item in job.candidate_items:
        db.add(
            dbm.NestingJobItem(
                nesting_job_id=job.job_id,
                order_id=item.order_id,
                item_json=item.model_dump(mode="json"),
                role="candidate",
            )
        )
    db.commit()
    return job


def list_jobs(db: Session) -> list[schemas.NestingJob]:
    return [schemas.NestingJob.model_validate(row.input_json) for row in db.scalars(select(dbm.NestingJob).order_by(dbm.NestingJob.created_at))]


def get_job(db: Session, job_id: str) -> schemas.NestingJob | None:
    row = db.get(dbm.NestingJob, job_id)
    return schemas.NestingJob.model_validate(row.input_json) if row else None


def _material_key(value: str | None) -> str:
    if not value:
        return ""
    return "".join(char for char in value.casefold() if char.isalnum())


def _inventory_snapshot_material_keys(row: dbm.InventorySnapshot) -> set[str]:
    raw_values: list[Any] = [row.material_code, row.material_name]
    material_field = (row.fields or {}).get("material")
    if isinstance(material_field, dict):
        raw_values.extend([material_field.get("code"), material_field.get("name"), material_field.get("title")])
    else:
        raw_values.append(material_field)
    raw_values.extend(
        [
            (row.fields or {}).get("material_code"),
            (row.fields or {}).get("material_name"),
            (row.fields or {}).get("sku"),
            (row.fields or {}).get("item_code"),
            (row.fields or {}).get("item_name"),
        ]
    )
    return {key for value in raw_values if isinstance(value, str) and (key := _material_key(value))}


def _inventory_snapshot_matches_material(row: dbm.InventorySnapshot, material_key: str) -> bool:
    if not material_key:
        return False
    keys = _inventory_snapshot_material_keys(row)
    if material_key in keys:
        return True
    return any(
        (len(material_key) >= 4 and material_key in key) or (len(key) >= 4 and key in material_key)
        for key in keys
    )


def _inventory_snapshot_is_blocked(row: dbm.InventorySnapshot) -> bool:
    status = (row.status or "").casefold().replace("-", "_").replace(" ", "_")
    return status in BLOCKED_INVENTORY_STATUSES


def evaluate_job_material_availability(db: Session, job_id: str) -> schemas.MaterialAvailabilityCheckResult | None:
    job = get_job(db, job_id)
    if not job:
        return None

    demand_by_material: dict[str, dict[str, Any]] = {}
    missing_order_ids: set[str] = set()
    checked_order_ids: set[str] = set()
    warnings: list[str] = []
    for item in [*job.fixed_items, *job.candidate_items]:
        order_id = item.order_id
        order_row = db.get(dbm.ProductionOrder, order_id)
        if order_row is None:
            missing_order_ids.add(order_id)
            continue
        checked_order_ids.add(order_id)
        material = (order_row.material or "").strip()
        material_key = _material_key(material)
        if not material_key:
            warnings.append(f"order {order_id} has no material")
            continue
        demand = demand_by_material.setdefault(
            material_key,
            {"material": material, "required_qty": 0.0, "order_ids": set()},
        )
        demand["required_qty"] += float(order_row.quantity or 0) * float(item.quantity or 1)
        demand["order_ids"].add(order_id)

    inventory_rows = db.scalars(select(dbm.InventorySnapshot).order_by(dbm.InventorySnapshot.updated_at.desc())).all()
    inventory_source_ids: set[str] = set()
    items: list[schemas.MaterialAvailabilityItemRead] = []
    for material_key, demand in sorted(demand_by_material.items(), key=lambda entry: entry[1]["material"]):
        matched_rows = [row for row in inventory_rows if _inventory_snapshot_matches_material(row, material_key)]
        contributing_rows = [row for row in matched_rows if not _inventory_snapshot_is_blocked(row)]
        available_qty = sum(float(row.available_qty or 0) for row in contributing_rows)
        reserved_qty = sum(float(row.reserved_qty or 0) for row in contributing_rows)
        net_available_qty = max(available_qty - reserved_qty, 0.0)
        required_qty = float(demand["required_qty"])
        shortage_qty = max(required_qty - net_available_qty, 0.0)
        source_ids = [row.id for row in contributing_rows]
        inventory_source_ids.update(source_ids)
        if not matched_rows:
            status = "unknown"
        elif shortage_qty > 0:
            status = "shortage"
        else:
            status = "ok"
        items.append(
            schemas.MaterialAvailabilityItemRead(
                material=demand["material"],
                required_qty=required_qty,
                available_qty=available_qty,
                reserved_qty=reserved_qty,
                net_available_qty=net_available_qty,
                shortage_qty=shortage_qty,
                unit=next((row.unit for row in contributing_rows if row.unit), None),
                status=status,
                order_ids=sorted(demand["order_ids"]),
                inventory_snapshot_ids=source_ids,
                source_count=len(matched_rows),
            )
        )

    if missing_order_ids:
        warnings.append(f"{len(missing_order_ids)} job item order(s) were not found in production_order")
    if not items:
        warnings.append("no production order material demand could be derived for this job")

    item_statuses = {item.status for item in items}
    if "shortage" in item_statuses:
        overall_status = "blocked"
    elif missing_order_ids or "unknown" in item_statuses or not items:
        overall_status = "unknown"
    else:
        overall_status = "ready"

    return schemas.MaterialAvailabilityCheckResult(
        job_id=job_id,
        overall_status=overall_status,
        checked_at=utc_now().isoformat(),
        order_count=len(checked_order_ids),
        inventory_source_count=len(inventory_source_ids),
        missing_order_ids=sorted(missing_order_ids),
        items=items,
        warnings=warnings,
    )


def _operation_status_key(value: str | None) -> str:
    return (value or "").casefold().replace("-", "_").replace(" ", "_")


def _job_order_requirements(
    db: Session,
    job: schemas.NestingJob,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    requirements: dict[str, dict[str, Any]] = {}
    missing_order_ids: set[str] = set()
    for item in [*job.fixed_items, *job.candidate_items]:
        order_row = db.get(dbm.ProductionOrder, item.order_id)
        if order_row is None:
            missing_order_ids.add(item.order_id)
            continue
        requirement = requirements.setdefault(
            item.order_id,
            {
                "order_id": item.order_id,
                "required_qty": 0.0,
                "item_ids": set(),
            },
        )
        requirement["required_qty"] += float(order_row.quantity or 0) * float(item.quantity or 1)
        requirement["item_ids"].add(item.item_id)
    return requirements, missing_order_ids


def _classify_schedule_rows(rows: list[dbm.ProductionScheduleEntry]) -> str:
    if not rows:
        return "missing"
    statuses = {_operation_status_key(row.status) for row in rows if row.status}
    if statuses & BLOCKED_OPERATION_STATUSES:
        return "blocked"
    if statuses and statuses <= COMPLETED_OPERATION_STATUSES:
        return "completed"
    if statuses & RUNNING_SCHEDULE_STATUSES:
        return "in_progress"
    if statuses & READY_SCHEDULE_STATUSES or any(row.planned_start_at for row in rows):
        return "scheduled"
    return "unknown"


def _classify_delivery_rows(rows: list[dbm.DeliveryConfirmation], required_qty: float) -> tuple[str, float]:
    if not rows:
        return "missing", 0.0
    delivered_qty = 0.0
    blocked = False
    has_completion_signal = False
    for row in rows:
        status = _operation_status_key(row.status)
        if status in BLOCKED_OPERATION_STATUSES:
            blocked = True
            continue
        completed = status in COMPLETED_OPERATION_STATUSES or bool(row.delivered_at)
        has_completion_signal = has_completion_signal or completed
        if completed:
            delivered_qty += float(row.quantity or 0)
    if blocked and delivered_qty <= 0:
        return "blocked", delivered_qty
    if required_qty > 0 and delivered_qty >= required_qty:
        return "delivered", delivered_qty
    if has_completion_signal or delivered_qty > 0:
        return "partial", delivered_qty
    return "unknown", delivered_qty


def _rollup_schedule_status(items: list[schemas.ProductionScheduleReadinessItem]) -> str:
    statuses = {item.status for item in items}
    if not statuses:
        return "unknown"
    if "blocked" in statuses:
        return "blocked"
    if "missing" in statuses:
        return "missing"
    if "unknown" in statuses:
        return "unknown"
    if statuses == {"completed"}:
        return "completed"
    if "in_progress" in statuses:
        return "in_progress"
    return "scheduled"


def _rollup_delivery_status(items: list[schemas.DeliveryClosureItem]) -> str:
    statuses = {item.status for item in items}
    if not statuses:
        return "unknown"
    if "blocked" in statuses:
        return "blocked"
    if statuses == {"delivered"}:
        return "delivered"
    if statuses == {"missing"}:
        return "missing"
    if "unknown" in statuses and not (statuses & {"partial", "delivered"}):
        return "unknown"
    return "partial"


def evaluate_job_production_readiness(db: Session, job_id: str) -> schemas.JobProductionReadinessResult | None:
    job = get_job(db, job_id)
    if not job:
        return None

    material = evaluate_job_material_availability(db, job_id)
    requirements, missing_order_ids = _job_order_requirements(db, job)
    order_ids = sorted(requirements)
    schedule_conditions = [dbm.ProductionScheduleEntry.job_id == job_id]
    if order_ids:
        schedule_conditions.append(dbm.ProductionScheduleEntry.order_id.in_(order_ids))
    schedule_rows = db.scalars(
        select(dbm.ProductionScheduleEntry)
        .where(or_(*schedule_conditions))
        .order_by(dbm.ProductionScheduleEntry.updated_at.desc())
    ).all()
    delivery_rows = (
        db.scalars(
            select(dbm.DeliveryConfirmation)
            .where(dbm.DeliveryConfirmation.order_id.in_(order_ids))
            .order_by(dbm.DeliveryConfirmation.updated_at.desc())
        ).all()
        if order_ids
        else []
    )
    job_level_schedule_rows = [row for row in schedule_rows if row.job_id == job_id and not row.order_id]

    schedule_items: list[schemas.ProductionScheduleReadinessItem] = []
    delivery_items: list[schemas.DeliveryClosureItem] = []
    for order_id in order_ids:
        required_qty = float(requirements[order_id]["required_qty"])
        order_schedule_rows = [
            row for row in schedule_rows if row.order_id == order_id or row in job_level_schedule_rows
        ]
        schedule_status = _classify_schedule_rows(order_schedule_rows)
        latest_schedule = order_schedule_rows[0] if order_schedule_rows else None
        scheduled_qty = sum(
            float(row.quantity or 0)
            for row in order_schedule_rows
            if row.order_id == order_id and _operation_status_key(row.status) not in BLOCKED_OPERATION_STATUSES
        )
        schedule_items.append(
            schemas.ProductionScheduleReadinessItem(
                order_id=order_id,
                required_qty=required_qty,
                scheduled_qty=scheduled_qty,
                status=schedule_status,
                latest_status=latest_schedule.status if latest_schedule else None,
                schedule_entry_ids=[row.id for row in order_schedule_rows],
                planned_start_at=next((row.planned_start_at for row in order_schedule_rows if row.planned_start_at), None),
                planned_end_at=next((row.planned_end_at for row in order_schedule_rows if row.planned_end_at), None),
                line_code=next((row.line_code for row in order_schedule_rows if row.line_code), None),
                machine_code=next((row.machine_code for row in order_schedule_rows if row.machine_code), None),
            )
        )

        order_delivery_rows = [row for row in delivery_rows if row.order_id == order_id]
        delivery_status, delivered_qty = _classify_delivery_rows(order_delivery_rows, required_qty)
        latest_delivery = order_delivery_rows[0] if order_delivery_rows else None
        delivery_items.append(
            schemas.DeliveryClosureItem(
                order_id=order_id,
                required_qty=required_qty,
                delivered_qty=delivered_qty,
                status=delivery_status,
                latest_status=latest_delivery.status if latest_delivery else None,
                delivery_confirmation_ids=[row.id for row in order_delivery_rows],
                delivered_at=next((row.delivered_at for row in order_delivery_rows if row.delivered_at), None),
                shipment_no=next((row.shipment_no for row in order_delivery_rows if row.shipment_no), None),
                carrier=next((row.carrier for row in order_delivery_rows if row.carrier), None),
                tracking_no=next((row.tracking_no for row in order_delivery_rows if row.tracking_no), None),
            )
        )

    schedule_status = _rollup_schedule_status(schedule_items)
    delivery_status = _rollup_delivery_status(delivery_items)
    material_status = material.overall_status if material else "unknown"
    if material_status == "blocked" or schedule_status == "blocked":
        overall_status = "blocked"
    elif material_status != "ready" or schedule_status in {"missing", "unknown"}:
        overall_status = "unknown"
    else:
        overall_status = "ready"

    warnings: list[str] = []
    for warning in material.warnings if material else []:
        if warning not in warnings:
            warnings.append(warning)
    for warning in [
        f"{len(missing_order_ids)} job item order(s) were not found in production_order" if missing_order_ids else "",
        "no MES production schedule was found for one or more job orders" if schedule_status == "missing" else "",
        "no ERP delivery confirmation was found for job orders" if delivery_status == "missing" else "",
    ]:
        if warning and warning not in warnings:
            warnings.append(warning)

    return schemas.JobProductionReadinessResult(
        job_id=job_id,
        overall_status=overall_status,
        material_status=material_status,
        schedule_status=schedule_status,
        delivery_status=delivery_status,
        checked_at=utc_now().isoformat(),
        order_count=len(order_ids),
        schedule_source_count=len({row.id for row in schedule_rows}),
        delivery_source_count=len({row.id for row in delivery_rows}),
        material=material,
        schedule_items=schedule_items,
        delivery_items=delivery_items,
        warnings=warnings,
    )


def create_solver_run(db: Session, job_id: str, solver_name: str, solver_version: str, config: dict[str, Any]) -> str:
    run_id = f"run_{uuid4().hex[:16]}"
    db.add(
        dbm.SolverRun(
            id=run_id,
            nesting_job_id=job_id,
            solver_name=solver_name,
            solver_version=solver_version,
            status="running",
            runtime_ms=0,
            seed=config.get("seed"),
            config=config,
        )
    )
    job = db.get(dbm.NestingJob, job_id)
    if job:
        job.status = "running"
    db.commit()
    add_solver_run_log(db, run_id, "info", "solver run started", {"job_id": job_id, "solver": solver_name})
    return run_id


def complete_solver_run(db: Session, run_id: str, runtime_ms: int, payload: dict[str, Any] | None = None) -> None:
    row = db.get(dbm.SolverRun, run_id)
    if row:
        row.status = "completed"
        row.runtime_ms = runtime_ms
        add_solver_run_log(db, run_id, "info", "solver run completed", payload or {}, commit=False)
        db.commit()


def fail_solver_run(db: Session, run_id: str, message: str, payload: dict[str, Any] | None = None) -> None:
    row = db.get(dbm.SolverRun, run_id)
    if row:
        row.status = "failed"
        add_solver_run_log(db, run_id, "error", message, payload or {}, commit=False)
        db.commit()


def add_solver_run_log(
    db: Session,
    run_id: str,
    level: str,
    message: str,
    payload: dict[str, Any] | None = None,
    *,
    commit: bool = True,
) -> None:
    db.add(
        dbm.SolverRunLog(
            solver_run_id=run_id,
            level=level,
            message=message,
            payload=payload or {},
        )
    )
    if commit:
        db.commit()


def list_solver_runs(db: Session, job_id: str | None = None) -> list[dict[str, Any]]:
    query = select(dbm.SolverRun).order_by(dbm.SolverRun.created_at.desc())
    if job_id:
        query = query.where(dbm.SolverRun.nesting_job_id == job_id)
    return [solver_run_from_row(row) for row in db.scalars(query)]


def list_solver_run_logs(db: Session, run_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(dbm.SolverRunLog)
        .where(dbm.SolverRunLog.solver_run_id == run_id)
        .order_by(dbm.SolverRunLog.created_at)
    )
    return [
        {
            "id": row.id,
            "solver_run_id": row.solver_run_id,
            "level": row.level,
            "message": row.message,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def solver_run_from_row(row: dbm.SolverRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "nesting_job_id": row.nesting_job_id,
        "solver_name": row.solver_name,
        "solver_version": row.solver_version,
        "status": row.status,
        "runtime_ms": row.runtime_ms,
        "seed": row.seed,
        "config": row.config,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def log_operation(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    actor_id: str | None = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        dbm.OperationLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=redact_sensitive_payload(payload or {}),
        )
    )
    db.commit()


def list_operation_logs(
    db: Session,
    limit: int = 100,
    *,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    actor_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[dict[str, Any]]:
    query = select(dbm.OperationLog)
    if action:
        query = query.where(dbm.OperationLog.action == action)
    if target_type:
        query = query.where(dbm.OperationLog.target_type == target_type)
    if target_id:
        query = query.where(dbm.OperationLog.target_id == target_id)
    if actor_id:
        query = query.where(dbm.OperationLog.actor_id == actor_id)
    if created_from:
        query = query.where(dbm.OperationLog.created_at >= created_from)
    if created_to:
        query = query.where(dbm.OperationLog.created_at <= created_to)
    rows = db.scalars(query.order_by(dbm.OperationLog.created_at.desc()).limit(limit))
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def list_user_ids_by_permission(db: Session, permission_code: str) -> list[str]:
    rows = db.scalars(
        select(dbm.UserAccount.id)
        .join(dbm.UserRole, dbm.UserRole.user_id == dbm.UserAccount.id)
        .join(dbm.RolePermission, dbm.RolePermission.role_id == dbm.UserRole.role_id)
        .join(dbm.Permission, dbm.Permission.id == dbm.RolePermission.permission_id)
        .where(dbm.UserAccount.is_active.is_(True), dbm.Permission.code == permission_code)
        .order_by(dbm.UserAccount.email)
    ).all()
    return sorted(set(rows))


def list_user_ids_by_department_codes(db: Session, department_codes: list[str]) -> list[str]:
    codes = sorted({code.strip() for code in department_codes if code and code.strip()})
    if not codes:
        return []
    rows = db.scalars(
        select(dbm.UserAccount.id)
        .where(dbm.UserAccount.is_active.is_(True), dbm.UserAccount.org_unit_code.in_(codes))
        .order_by(dbm.UserAccount.email)
    ).all()
    return sorted(set(rows))


def list_user_ids_by_recipient_group(db: Session, group_id: str) -> list[str]:
    row = db.get(dbm.NotificationRecipientGroup, group_id)
    if row is None or not row.is_active:
        return []
    recipient_ids: set[str] = set()
    member_user_ids = sorted({user_id for user_id in row.member_user_ids or [] if user_id})
    if member_user_ids:
        direct_ids = db.scalars(
            select(dbm.UserAccount.id)
            .where(dbm.UserAccount.is_active.is_(True), dbm.UserAccount.id.in_(member_user_ids))
            .order_by(dbm.UserAccount.email)
        ).all()
        recipient_ids.update(direct_ids)
    for permission_code in row.permission_codes or []:
        recipient_ids.update(list_user_ids_by_permission(db, permission_code))
    recipient_ids.update(list_user_ids_by_department_codes(db, row.department_codes or []))
    return sorted(recipient_ids)


def list_active_user_emails_by_ids(db: Session, user_ids: list[str]) -> list[str]:
    ids = sorted({user_id for user_id in user_ids if user_id})
    if not ids:
        return []
    rows = db.scalars(
        select(dbm.UserAccount.email)
        .where(dbm.UserAccount.is_active.is_(True), dbm.UserAccount.id.in_(ids))
        .order_by(dbm.UserAccount.email)
    ).all()
    return sorted({email for email in rows if email})


def list_notification_recipient_groups(
    db: Session,
    *,
    active_only: bool = False,
    limit: int = 200,
) -> list[schemas.NotificationRecipientGroupRead]:
    query = select(dbm.NotificationRecipientGroup).order_by(dbm.NotificationRecipientGroup.name).limit(limit)
    if active_only:
        query = query.where(dbm.NotificationRecipientGroup.is_active.is_(True))
    return [notification_recipient_group_from_row(db, row) for row in db.scalars(query).all()]


def create_notification_recipient_group(
    db: Session,
    payload: schemas.NotificationRecipientGroupCreate,
) -> schemas.NotificationRecipientGroupRead:
    _validate_recipient_group_members(db, payload.member_user_ids, payload.permission_codes)
    row = dbm.NotificationRecipientGroup(
        name=payload.name,
        description=payload.description,
        member_user_ids=_clean_string_list(payload.member_user_ids),
        permission_codes=_clean_string_list(payload.permission_codes),
        department_codes=_clean_string_list(payload.department_codes),
        is_active=payload.is_active,
        metadata_json=payload.metadata,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("recipient group name already exists") from exc
    return notification_recipient_group_from_row(db, row)


def update_notification_recipient_group(
    db: Session,
    group_id: str,
    payload: schemas.NotificationRecipientGroupUpdate,
) -> schemas.NotificationRecipientGroupRead | None:
    row = db.get(dbm.NotificationRecipientGroup, group_id)
    if row is None:
        return None
    update_data = payload.model_dump(exclude_unset=True)
    member_user_ids = update_data.get("member_user_ids", row.member_user_ids or [])
    permission_codes = update_data.get("permission_codes", row.permission_codes or [])
    _validate_recipient_group_members(db, member_user_ids, permission_codes)
    for field_name, value in update_data.items():
        if field_name == "metadata":
            row.metadata_json = value or {}
        elif field_name in {"member_user_ids", "permission_codes", "department_codes"}:
            setattr(row, field_name, _clean_string_list(value or []))
        else:
            setattr(row, field_name, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("recipient group name already exists") from exc
    return notification_recipient_group_from_row(db, row)


def notification_recipient_group_from_row(
    db: Session,
    row: dbm.NotificationRecipientGroup,
) -> schemas.NotificationRecipientGroupRead:
    return schemas.NotificationRecipientGroupRead(
        id=row.id,
        name=row.name,
        description=row.description,
        member_user_ids=row.member_user_ids or [],
        permission_codes=row.permission_codes or [],
        department_codes=row.department_codes or [],
        is_active=row.is_active,
        metadata=mask_sensitive_config(row.metadata_json or {}),
        resolved_user_count=len(list_user_ids_by_recipient_group(db, row.id)),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _validate_recipient_group_members(db: Session, member_user_ids: list[str], permission_codes: list[str]) -> None:
    user_ids = _clean_string_list(member_user_ids)
    if user_ids:
        existing_user_ids = set(db.scalars(select(dbm.UserAccount.id).where(dbm.UserAccount.id.in_(user_ids))).all())
        missing_user_ids = sorted(set(user_ids) - existing_user_ids)
        if missing_user_ids:
            raise ValueError(f"unknown user ids: {', '.join(missing_user_ids)}")
    codes = _clean_string_list(permission_codes)
    if codes:
        existing_codes = set(db.scalars(select(dbm.Permission.code).where(dbm.Permission.code.in_(codes))).all())
        missing_codes = sorted(set(codes) - existing_codes)
        if missing_codes:
            raise ValueError(f"unknown permission codes: {', '.join(missing_codes)}")


def _clean_string_list(values: list[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def create_notification(
    db: Session,
    *,
    user_id: str,
    event_type: str,
    title: str,
    message: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
    commit: bool = True,
) -> schemas.NotificationRead:
    row = dbm.Notification(
        user_id=user_id,
        event_type=event_type,
        title=title,
        message=message,
        target_type=target_type,
        target_id=target_id,
        payload=payload or {},
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return notification_from_row(row)


def create_permission_notifications(
    db: Session,
    *,
    permission_code: str,
    event_type: str,
    title: str,
    message: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[schemas.NotificationRead]:
    return create_user_notifications(
        db,
        user_ids=list_user_ids_by_permission(db, permission_code),
        event_type=event_type,
        title=title,
        message=message,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )


def create_recipient_group_notifications(
    db: Session,
    *,
    group_id: str,
    event_type: str,
    title: str,
    message: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[schemas.NotificationRead]:
    return create_user_notifications(
        db,
        user_ids=list_user_ids_by_recipient_group(db, group_id),
        event_type=event_type,
        title=title,
        message=message,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )


def create_user_notifications(
    db: Session,
    *,
    user_ids: list[str],
    event_type: str,
    title: str,
    message: str,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> list[schemas.NotificationRead]:
    notifications = [
        create_notification(
            db,
            user_id=user_id,
            event_type=event_type,
            title=title,
            message=message,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            commit=False,
        )
        for user_id in sorted(set(user_ids))
    ]
    db.commit()
    return notifications


def list_notifications(
    db: Session,
    user_id: str,
    *,
    unread_only: bool = False,
    limit: int = 100,
) -> list[schemas.NotificationRead]:
    query = select(dbm.Notification).where(dbm.Notification.user_id == user_id)
    if unread_only:
        query = query.where(dbm.Notification.is_read.is_(False))
    query = query.order_by(dbm.Notification.created_at.desc()).limit(limit)
    return [notification_from_row(row) for row in db.scalars(query)]


def mark_notification_read(db: Session, notification_id: str, user_id: str) -> schemas.NotificationRead | None:
    row = db.get(dbm.Notification, notification_id)
    if row is None or row.user_id != user_id:
        return None
    if not row.is_read:
        row.is_read = True
        row.read_at = utc_now()
        db.commit()
    return notification_from_row(row)


def mark_all_notifications_read(db: Session, user_id: str) -> int:
    rows = db.scalars(
        select(dbm.Notification).where(dbm.Notification.user_id == user_id, dbm.Notification.is_read.is_(False))
    ).all()
    now = utc_now()
    for row in rows:
        row.is_read = True
        row.read_at = now
    db.commit()
    return len(rows)


def notification_from_row(row: dbm.Notification) -> schemas.NotificationRead:
    return schemas.NotificationRead(
        id=row.id,
        user_id=row.user_id,
        event_type=row.event_type,
        title=row.title,
        message=row.message,
        target_type=row.target_type,
        target_id=row.target_id,
        payload=row.payload,
        is_read=row.is_read,
        read_at=row.read_at.isoformat() if row.read_at else None,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def list_message_templates(
    db: Session,
    *,
    event_type: str | None = None,
    active_only: bool = False,
    limit: int = 200,
    redact_metadata: bool = True,
) -> list[schemas.MessageTemplateRead]:
    query = select(dbm.MessageTemplate).order_by(dbm.MessageTemplate.event_type, dbm.MessageTemplate.created_at.desc())
    if event_type:
        query = query.where(dbm.MessageTemplate.event_type == event_type)
    if active_only:
        query = query.where(dbm.MessageTemplate.is_active.is_(True))
    query = query.limit(limit)
    return [message_template_from_row(row, redact_metadata=redact_metadata) for row in db.scalars(query).all()]


def get_message_template(
    db: Session,
    template_id: str,
    *,
    redact_metadata: bool = True,
) -> schemas.MessageTemplateRead | None:
    row = db.get(dbm.MessageTemplate, template_id)
    return message_template_from_row(row, redact_metadata=redact_metadata) if row else None


def create_message_template(db: Session, payload: schemas.MessageTemplateCreate) -> schemas.MessageTemplateRead:
    _validate_recipient_group_ids(db, [payload.recipient_group_id, payload.escalation_group_id])
    row = dbm.MessageTemplate(
        name=payload.name,
        event_type=payload.event_type,
        channel=payload.channel,
        title_template=payload.title_template,
        message_template=payload.message_template,
        recipient_permission_code=payload.recipient_permission_code,
        recipient_group_id=payload.recipient_group_id,
        escalation_permission_code=payload.escalation_permission_code,
        escalation_group_id=payload.escalation_group_id,
        escalation_after_minutes=payload.escalation_after_minutes,
        is_active=payload.is_active,
        metadata_json=payload.metadata,
    )
    db.add(row)
    db.commit()
    return message_template_from_row(row)


def update_message_template(
    db: Session,
    template_id: str,
    payload: schemas.MessageTemplateUpdate,
) -> schemas.MessageTemplateRead | None:
    row = db.get(dbm.MessageTemplate, template_id)
    if row is None:
        return None
    update_data = payload.model_dump(exclude_unset=True)
    _validate_recipient_group_ids(
        db,
        [
            update_data.get("recipient_group_id", row.recipient_group_id),
            update_data.get("escalation_group_id", row.escalation_group_id),
        ],
    )
    for field_name, value in update_data.items():
        if field_name == "metadata":
            row.metadata_json = value or {}
        else:
            setattr(row, field_name, value)
    db.commit()
    return message_template_from_row(row)


def _validate_recipient_group_ids(db: Session, group_ids: list[str | None]) -> None:
    ids = sorted({group_id for group_id in group_ids if group_id})
    if not ids:
        return
    existing_ids = set(
        db.scalars(select(dbm.NotificationRecipientGroup.id).where(dbm.NotificationRecipientGroup.id.in_(ids))).all()
    )
    missing_ids = sorted(set(ids) - existing_ids)
    if missing_ids:
        raise ValueError(f"unknown recipient group ids: {', '.join(missing_ids)}")


def message_template_from_row(
    row: dbm.MessageTemplate,
    *,
    redact_metadata: bool = True,
) -> schemas.MessageTemplateRead:
    metadata = row.metadata_json or {}
    return schemas.MessageTemplateRead(
        id=row.id,
        name=row.name,
        event_type=row.event_type,
        channel=row.channel,
        title_template=row.title_template,
        message_template=row.message_template,
        recipient_permission_code=row.recipient_permission_code,
        recipient_group_id=row.recipient_group_id,
        escalation_permission_code=row.escalation_permission_code,
        escalation_group_id=row.escalation_group_id,
        escalation_after_minutes=row.escalation_after_minutes,
        is_active=row.is_active,
        metadata=mask_sensitive_config(metadata) if redact_metadata else metadata,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def unread_notification_exists(
    db: Session,
    *,
    event_type: str,
    target_type: str | None,
    target_id: str | None,
    older_than: datetime,
    dedupe_key: str | None = None,
) -> bool:
    query = (
        select(dbm.Notification)
        .where(
            dbm.Notification.event_type == event_type,
            dbm.Notification.is_read.is_(False),
            dbm.Notification.created_at <= older_than,
        )
        .order_by(dbm.Notification.created_at.desc())
        .limit(100)
    )
    if target_type is not None:
        query = query.where(dbm.Notification.target_type == target_type)
    if target_id is not None:
        query = query.where(dbm.Notification.target_id == target_id)
    rows = db.scalars(query).all()
    if dedupe_key is None:
        return bool(rows)
    return any((row.payload or {}).get("dedupe_key") == dedupe_key for row in rows)


def create_message_dispatch_log(
    db: Session,
    *,
    template_id: str | None,
    event_type: str,
    channel: str,
    target_type: str | None = None,
    target_id: str | None = None,
    status: str,
    recipient_count: int = 0,
    payload: dict[str, Any] | None = None,
    error: str | None = None,
    commit: bool = True,
) -> schemas.MessageDispatchLogRead:
    row = dbm.MessageDispatchLog(
        template_id=template_id,
        event_type=event_type,
        channel=channel,
        target_type=target_type,
        target_id=target_id,
        status=status,
        recipient_count=recipient_count,
        payload=payload or {},
        error=error,
    )
    db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    return message_dispatch_log_from_row(row)


def list_message_dispatch_logs(
    db: Session,
    *,
    event_type: str | None = None,
    limit: int = 200,
) -> list[schemas.MessageDispatchLogRead]:
    query = select(dbm.MessageDispatchLog).order_by(dbm.MessageDispatchLog.created_at.desc()).limit(limit)
    if event_type:
        query = query.where(dbm.MessageDispatchLog.event_type == event_type)
    return [message_dispatch_log_from_row(row) for row in db.scalars(query).all()]


def recent_message_dispatch_exists(
    db: Session,
    *,
    template_id: str | None,
    event_type: str,
    channel: str,
    dedupe_key: str,
    since: datetime,
    target_type: str | None = None,
    target_id: str | None = None,
    statuses: set[str] | None = None,
) -> bool:
    query = (
        select(dbm.MessageDispatchLog)
        .where(
            dbm.MessageDispatchLog.event_type == event_type,
            dbm.MessageDispatchLog.channel == channel,
            dbm.MessageDispatchLog.created_at >= since,
        )
        .order_by(dbm.MessageDispatchLog.created_at.desc())
        .limit(200)
    )
    if template_id is not None:
        query = query.where(dbm.MessageDispatchLog.template_id == template_id)
    if target_type is not None:
        query = query.where(dbm.MessageDispatchLog.target_type == target_type)
    if target_id is not None:
        query = query.where(dbm.MessageDispatchLog.target_id == target_id)
    if statuses:
        query = query.where(dbm.MessageDispatchLog.status.in_(statuses))
    return any((row.payload or {}).get("dedupe_key") == dedupe_key for row in db.scalars(query).all())


def message_dispatch_log_from_row(row: dbm.MessageDispatchLog) -> schemas.MessageDispatchLogRead:
    return schemas.MessageDispatchLogRead(
        id=row.id,
        template_id=row.template_id,
        event_type=row.event_type,
        channel=row.channel,
        target_type=row.target_type,
        target_id=row.target_id,
        status=row.status,
        recipient_count=row.recipient_count,
        payload=row.payload or {},
        error=row.error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def create_work_task(
    db: Session,
    *,
    task_type: str,
    target_type: str,
    target_id: str,
    actor_id: str | None = None,
    payload: dict[str, Any] | None = None,
    parent_task_id: str | None = None,
    attempt: int = 1,
    max_attempts: int = 3,
    timeout_sec: int | None = None,
) -> schemas.WorkTaskRead:
    row = dbm.WorkTask(
        parent_task_id=parent_task_id,
        task_type=task_type,
        status="queued",
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
        payload=payload or {},
        result={},
        attempt=attempt,
        max_attempts=max(1, max_attempts),
        timeout_sec=timeout_sec,
        cancel_requested=False,
        progress_percent=0,
    )
    db.add(row)
    db.commit()
    return work_task_from_row(row)


def start_work_task(db: Session, task_id: str) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.status in TERMINAL_TASK_STATUSES:
        return work_task_from_row(row)
    if row.cancel_requested:
        row.status = "cancelled"
        row.error = row.error or "task was cancelled before execution"
        row.completed_at = utc_now()
        db.commit()
        return work_task_from_row(row)
    if row.status != "queued":
        return work_task_from_row(row)
    row.status = "running"
    now = utc_now()
    row.started_at = now
    row.heartbeat_at = now
    row.progress_percent = max(row.progress_percent or 0, 5)
    row.error = None
    db.commit()
    return work_task_from_row(row)


def heartbeat_work_task(db: Session, task_id: str, progress_percent: int | None = None) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.status != "running":
        return work_task_from_row(row)
    row.heartbeat_at = utc_now()
    if progress_percent is not None:
        row.progress_percent = max(0, min(99, progress_percent))
    db.commit()
    return work_task_from_row(row)


def is_work_task_cancel_requested(db: Session, task_id: str) -> bool:
    row = db.get(dbm.WorkTask, task_id)
    return bool(row and row.cancel_requested)


def complete_work_task(db: Session, task_id: str, result: dict[str, Any] | None = None) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.cancel_requested:
        return cancel_work_task(db, task_id, "task was cancelled during execution", result=result)
    row.status = "completed"
    row.result = result or {}
    row.error = None
    now = utc_now()
    row.progress_percent = 100
    row.heartbeat_at = now
    row.completed_at = now
    db.commit()
    return work_task_from_row(row)


def fail_work_task(db: Session, task_id: str, error: str, result: dict[str, Any] | None = None) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.cancel_requested:
        return cancel_work_task(db, task_id, "task was cancelled during execution", result=result)
    row.status = "failed"
    row.error = error
    row.result = result or {}
    now = utc_now()
    row.heartbeat_at = now
    row.completed_at = now
    db.commit()
    return work_task_from_row(row)


def timeout_work_task(
    db: Session,
    task_id: str,
    elapsed_sec: float,
    result: dict[str, Any] | None = None,
) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    row.status = "timed_out"
    row.error = f"task exceeded timeout_sec={row.timeout_sec} after {elapsed_sec:.3f}s"
    row.result = result or {}
    now = utc_now()
    row.heartbeat_at = now
    row.completed_at = now
    db.commit()
    return work_task_from_row(row)


def request_cancel_work_task(db: Session, task_id: str, actor_id: str | None = None) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.status in TERMINAL_TASK_STATUSES:
        return work_task_from_row(row)
    row.cancel_requested = True
    if row.status == "queued":
        row.status = "cancelled"
        row.error = "task was cancelled before execution"
        row.completed_at = utc_now()
    else:
        row.error = "cancellation requested"
    log_operation(
        db,
        action="work_task.cancel_requested",
        target_type="work_task",
        target_id=task_id,
        actor_id=actor_id,
        payload={"status": row.status},
    )
    db.commit()
    return work_task_from_row(row)


def cancel_work_task(
    db: Session,
    task_id: str,
    reason: str,
    result: dict[str, Any] | None = None,
) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    row.status = "cancelled"
    row.cancel_requested = True
    row.error = reason
    row.result = result or {}
    now = utc_now()
    row.heartbeat_at = now
    row.completed_at = now
    db.commit()
    return work_task_from_row(row)


def retry_work_task(db: Session, task_id: str, actor_id: str | None = None) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    if row is None:
        return None
    if row.status not in {"failed", "cancelled", "timed_out"}:
        raise ValueError("only failed, cancelled, or timed_out tasks can be retried")
    existing_retry = db.scalar(select(dbm.WorkTask).where(dbm.WorkTask.parent_task_id == row.id))
    if existing_retry is not None:
        raise ValueError("task already has a retry child; retry the latest child task instead")
    next_attempt = row.attempt + 1
    if next_attempt > row.max_attempts:
        raise ValueError("task retry limit reached")
    retry = create_work_task(
        db,
        task_type=row.task_type,
        target_type=row.target_type,
        target_id=row.target_id,
        actor_id=actor_id or row.actor_id,
        payload={**(row.payload or {}), "retry_of": row.id},
        parent_task_id=row.id,
        attempt=next_attempt,
        max_attempts=row.max_attempts,
        timeout_sec=row.timeout_sec,
    )
    log_operation(
        db,
        action="work_task.retry_queued",
        target_type="work_task",
        target_id=retry.id,
        actor_id=actor_id,
        payload={"retry_of": row.id, "attempt": next_attempt, "max_attempts": row.max_attempts},
    )
    db.commit()
    return retry


def get_work_task(db: Session, task_id: str) -> schemas.WorkTaskRead | None:
    row = db.get(dbm.WorkTask, task_id)
    return work_task_from_row(row) if row else None


def list_work_tasks(db: Session, status: str | None = None, limit: int = 100) -> list[schemas.WorkTaskRead]:
    query = select(dbm.WorkTask).order_by(dbm.WorkTask.created_at.desc()).limit(limit)
    if status:
        query = select(dbm.WorkTask).where(dbm.WorkTask.status == status).order_by(dbm.WorkTask.created_at.desc()).limit(limit)
    return [work_task_from_row(row) for row in db.scalars(query)]


def get_work_task_metrics(db: Session, stale_after_sec: int) -> schemas.WorkTaskMetrics:
    rows = db.scalars(select(dbm.WorkTask)).all()
    counts = {status: 0 for status in ["queued", "running", "completed", "failed", "cancelled", "timed_out"]}
    stale_cutoff = utc_now() - timedelta(seconds=max(1, stale_after_sec))
    stale_running = 0
    oldest_queued_at = None
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        if row.status == "running" and (row.heartbeat_at is None or row.heartbeat_at < stale_cutoff):
            stale_running += 1
        if row.status == "queued" and (oldest_queued_at is None or row.created_at < oldest_queued_at):
            oldest_queued_at = row.created_at
    return schemas.WorkTaskMetrics(
        total=len(rows),
        queued=counts.get("queued", 0),
        running=counts.get("running", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
        timed_out=counts.get("timed_out", 0),
        active=counts.get("queued", 0) + counts.get("running", 0),
        stale_running=stale_running,
        stale_after_sec=stale_after_sec,
        oldest_queued_at=oldest_queued_at.isoformat() if oldest_queued_at else None,
    )


def work_task_from_row(row: dbm.WorkTask) -> schemas.WorkTaskRead:
    return schemas.WorkTaskRead(
        id=row.id,
        task_type=row.task_type,
        status=row.status,
        target_type=row.target_type,
        target_id=row.target_id,
        parent_task_id=row.parent_task_id,
        actor_id=row.actor_id,
        payload=row.payload,
        result=row.result,
        error=row.error,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        timeout_sec=row.timeout_sec,
        cancel_requested=row.cancel_requested,
        progress_percent=row.progress_percent,
        heartbeat_at=row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        started_at=row.started_at.isoformat() if row.started_at else None,
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def save_solutions(
    db: Session,
    job_id: str,
    solutions: list[schemas.NestingSolution],
    solver_run_id: str | None = None,
) -> None:
    existing_ids = list(
        db.scalars(select(dbm.NestingSolution.id).where(dbm.NestingSolution.nesting_job_id == job_id))
    )
    if existing_ids:
        db.execute(delete(dbm.SolutionApproval).where(dbm.SolutionApproval.solution_id.in_(existing_ids)))
        db.execute(delete(dbm.ValidationReport).where(dbm.ValidationReport.solution_id.in_(existing_ids)))
        db.execute(delete(dbm.SolutionPlacement).where(dbm.SolutionPlacement.solution_id.in_(existing_ids)))
    db.execute(delete(dbm.NestingSolution).where(dbm.NestingSolution.nesting_job_id == job_id))
    for solution in solutions:
        db.add(
            dbm.NestingSolution(
                id=solution.solution_id,
                nesting_job_id=solution.job_id,
                solver_run_id=solver_run_id,
                status=solution.status,
                rank=solution.rank,
                utilization_rate=solution.utilization_rate,
                waste_rate=solution.waste_rate,
                score=solution.score.total if solution.score else 0,
                solution_json=solution.model_dump(mode="json"),
            )
        )
        if solution.validation_report:
            db.add(
                dbm.ValidationReport(
                    solution_id=solution.solution_id,
                    is_valid=solution.validation_report.is_valid,
                    report=solution.validation_report.model_dump(mode="json"),
                )
            )
        for placement in solution.placed_items:
            db.add(
                dbm.SolutionPlacement(
                    solution_id=solution.solution_id,
                    item_id=placement.item_id,
                    order_id=placement.order_id,
                    x=placement.x,
                    y=placement.y,
                    rotation=placement.rotation,
                    mirrored=placement.mirrored,
                    placement_json=placement.model_dump(mode="json"),
                )
            )
    job = db.get(dbm.NestingJob, job_id)
    if job:
        job.status = "completed"
    db.commit()


def get_solution(db: Session, solution_id: str) -> schemas.NestingSolution | None:
    row = db.get(dbm.NestingSolution, solution_id)
    return schemas.NestingSolution.model_validate(row.solution_json) if row else None


def list_job_solutions(db: Session, job_id: str) -> list[schemas.NestingSolution]:
    rows = db.scalars(
        select(dbm.NestingSolution)
        .where(dbm.NestingSolution.nesting_job_id == job_id)
        .order_by(dbm.NestingSolution.rank)
    )
    return [schemas.NestingSolution.model_validate(row.solution_json) for row in rows]


def update_solution(db: Session, solution: schemas.NestingSolution) -> None:
    row = db.get(dbm.NestingSolution, solution.solution_id)
    if row:
        row.status = solution.status
        row.utilization_rate = solution.utilization_rate
        row.waste_rate = solution.waste_rate
        row.score = solution.score.total if solution.score else row.score
        row.solution_json = solution.model_dump(mode="json")
        db.execute(delete(dbm.ValidationReport).where(dbm.ValidationReport.solution_id == solution.solution_id))
        if solution.validation_report:
            db.add(
                dbm.ValidationReport(
                    solution_id=solution.solution_id,
                    is_valid=solution.validation_report.is_valid,
                    report=solution.validation_report.model_dump(mode="json"),
                )
            )
        db.commit()


def create_solution_approval_request(
    db: Session,
    solution: schemas.NestingSolution,
    requested_by: str,
    request_note: str | None = None,
) -> schemas.SolutionApprovalRead:
    pending = db.scalar(
        select(dbm.SolutionApproval)
        .where(dbm.SolutionApproval.solution_id == solution.solution_id, dbm.SolutionApproval.status == "pending")
        .order_by(dbm.SolutionApproval.created_at.desc())
    )
    if pending:
        return solution_approval_from_row(pending)

    row = dbm.SolutionApproval(
        solution_id=solution.solution_id,
        requested_by=requested_by,
        status="pending",
        request_note=request_note,
        snapshot={
            "solution_id": solution.solution_id,
            "job_id": solution.job_id,
            "solver": str(solution.solver),
            "rank": solution.rank,
            "status": solution.status,
            "utilization_rate": solution.utilization_rate,
            "waste_rate": solution.waste_rate,
            "score": solution.score.model_dump(mode="json") if solution.score else None,
            "validation_report": solution.validation_report.model_dump(mode="json") if solution.validation_report else None,
            "exports": solution.exports,
        },
    )
    db.add(row)
    db.commit()
    return solution_approval_from_row(row)


def decide_solution_approval(
    db: Session,
    solution_id: str,
    decision: str,
    decided_by: str,
    decision_note: str | None = None,
) -> schemas.SolutionApprovalRead:
    row = db.scalar(
        select(dbm.SolutionApproval)
        .where(dbm.SolutionApproval.solution_id == solution_id, dbm.SolutionApproval.status == "pending")
        .order_by(dbm.SolutionApproval.created_at.desc())
    )
    if row is None:
        raise ValueError("pending approval request not found")
    row.status = decision
    row.decided_by = decided_by
    row.decision_note = decision_note
    db.commit()
    return solution_approval_from_row(row)


def list_solution_approvals(db: Session, solution_id: str) -> list[schemas.SolutionApprovalRead]:
    rows = db.scalars(
        select(dbm.SolutionApproval)
        .where(dbm.SolutionApproval.solution_id == solution_id)
        .order_by(dbm.SolutionApproval.created_at.desc())
    )
    return [solution_approval_from_row(row) for row in rows]


def create_production_plan_approval_request(
    db: Session,
    plan: schemas.ProductionPlanRead,
    requested_by: str,
    request_note: str | None = None,
) -> schemas.ProductionPlanApprovalRead:
    pending = db.scalar(
        select(dbm.ProductionPlanApproval)
        .where(dbm.ProductionPlanApproval.plan_id == plan.plan_id, dbm.ProductionPlanApproval.status == "pending")
        .order_by(dbm.ProductionPlanApproval.created_at.desc())
    )
    if pending:
        return production_plan_approval_from_row(pending)
    row = dbm.ProductionPlanApproval(
        plan_id=plan.plan_id,
        requested_by=requested_by,
        status="pending",
        request_note=request_note,
        snapshot=plan.model_dump(mode="json"),
    )
    db.add(row)
    db.commit()
    return production_plan_approval_from_row(row)


def decide_production_plan_approval(
    db: Session,
    plan_id: str,
    decision: str,
    decided_by: str,
    decision_note: str | None = None,
) -> schemas.ProductionPlanApprovalRead:
    row = db.scalar(
        select(dbm.ProductionPlanApproval)
        .where(dbm.ProductionPlanApproval.plan_id == plan_id, dbm.ProductionPlanApproval.status == "pending")
        .order_by(dbm.ProductionPlanApproval.created_at.desc())
    )
    if row is None:
        raise ValueError("pending production plan approval request not found")
    row.status = decision
    row.decided_by = decided_by
    row.decision_note = decision_note
    db.commit()
    return production_plan_approval_from_row(row)


def list_production_plan_approvals(db: Session, plan_id: str) -> list[schemas.ProductionPlanApprovalRead]:
    rows = db.scalars(
        select(dbm.ProductionPlanApproval)
        .where(dbm.ProductionPlanApproval.plan_id == plan_id)
        .order_by(dbm.ProductionPlanApproval.created_at.desc())
    )
    return [production_plan_approval_from_row(row) for row in rows]


def set_production_plan_status(db: Session, plan_id: str, status: str) -> None:
    row = db.get(dbm.ProductionPlan, plan_id)
    if row is None:
        raise ValueError("production plan not found")
    row.status = status
    db.commit()


def create_production_plan_export_record(
    db: Session,
    *,
    export_id: str,
    plan_id: str,
    export_type: str,
    storage_key: str,
    checksum: str,
    storage_backend: str | None = None,
    storage_object_key: str | None = None,
    storage_version_id: str | None = None,
    storage_etag: str | None = None,
    storage_size_bytes: int | None = None,
) -> schemas.ProductionPlanExportRead:
    existing_exports = list(
        db.scalars(
            select(dbm.ProductionPlanExport).where(
                dbm.ProductionPlanExport.plan_id == plan_id,
                dbm.ProductionPlanExport.export_type == export_type,
            )
        )
    )
    version = max((row.version for row in existing_exports), default=0) + 1
    row = dbm.ProductionPlanExport(
        id=export_id,
        plan_id=plan_id,
        export_type=export_type,
        version=version,
        lifecycle_status="active",
        storage_key=storage_key,
        checksum=checksum,
        storage_backend=storage_backend,
        storage_object_key=storage_object_key,
        storage_version_id=storage_version_id,
        storage_etag=storage_etag,
        storage_size_bytes=storage_size_bytes,
    )
    for existing in existing_exports:
        if existing.lifecycle_status == "active":
            existing.lifecycle_status = "superseded"
    db.add(row)
    db.commit()
    return production_plan_export_from_row(row)


def list_production_plan_exports(db: Session, plan_id: str) -> list[schemas.ProductionPlanExportRead]:
    rows = db.scalars(
        select(dbm.ProductionPlanExport)
        .where(dbm.ProductionPlanExport.plan_id == plan_id)
        .order_by(
            dbm.ProductionPlanExport.export_type,
            dbm.ProductionPlanExport.version.desc(),
            dbm.ProductionPlanExport.created_at.desc(),
        )
    )
    return [production_plan_export_from_row(row) for row in rows]


def get_production_plan_export(db: Session, export_id: str) -> schemas.ProductionPlanExportRead | None:
    row = db.get(dbm.ProductionPlanExport, export_id)
    return production_plan_export_from_row(row) if row else None


def create_solution_export_record(
    db: Session,
    *,
    export_id: str,
    solution_id: str,
    export_type: str,
    storage_key: str,
    checksum: str,
    storage_backend: str | None = None,
    storage_object_key: str | None = None,
    storage_version_id: str | None = None,
    storage_etag: str | None = None,
    storage_size_bytes: int | None = None,
) -> schemas.SolutionExportRead:
    existing_exports = list(
        db.scalars(
            select(dbm.SolutionExport).where(
                dbm.SolutionExport.solution_id == solution_id,
                dbm.SolutionExport.export_type == export_type,
            )
        )
    )
    version = max((row.version for row in existing_exports), default=0) + 1
    retention_days = max(1, get_settings().export_retention_days)
    retention_until = utc_now() + timedelta(days=retention_days)
    row = dbm.SolutionExport(
        id=export_id,
        solution_id=solution_id,
        export_type=export_type,
        version=version,
        lifecycle_status="active",
        retention_until=retention_until,
        storage_key=storage_key,
        checksum=checksum,
        storage_backend=storage_backend,
        storage_object_key=storage_object_key,
        storage_version_id=storage_version_id,
        storage_etag=storage_etag,
        storage_size_bytes=storage_size_bytes,
    )
    for existing in existing_exports:
        if existing.lifecycle_status == "active":
            existing.lifecycle_status = "superseded"
            existing.superseded_by_export_id = export_id
    db.add(row)
    db.commit()
    return solution_export_from_row(row)


def list_solution_exports(db: Session, solution_id: str) -> list[schemas.SolutionExportRead]:
    rows = db.scalars(
        select(dbm.SolutionExport)
        .where(dbm.SolutionExport.solution_id == solution_id)
        .order_by(dbm.SolutionExport.export_type, dbm.SolutionExport.version.desc(), dbm.SolutionExport.created_at.desc())
    )
    return [solution_export_from_row(row) for row in rows]


def get_solution_export(db: Session, export_id: str) -> schemas.SolutionExportRead | None:
    row = db.get(dbm.SolutionExport, export_id)
    return solution_export_from_row(row) if row else None


def archive_expired_solution_exports(
    db: Session,
    *,
    solution_id: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> schemas.SolutionExportArchiveResult:
    cutoff = now or utc_now()
    query = select(dbm.SolutionExport).where(
        dbm.SolutionExport.retention_until.is_not(None),
        dbm.SolutionExport.retention_until <= cutoff,
        dbm.SolutionExport.lifecycle_status != "archived",
    )
    if solution_id:
        query = query.where(dbm.SolutionExport.solution_id == solution_id)
    rows = list(db.scalars(query.order_by(dbm.SolutionExport.retention_until, dbm.SolutionExport.created_at)).all())
    if not dry_run:
        for row in rows:
            row.lifecycle_status = "archived"
        if rows:
            db.commit()
    exports = [solution_export_from_row(row) for row in rows]
    return schemas.SolutionExportArchiveResult(
        status="dry_run" if dry_run else "completed",
        dry_run=dry_run,
        cutoff_at=cutoff.isoformat(),
        solution_id=solution_id,
        checked_count=len(rows),
        archived_count=0 if dry_run else len(rows),
        archived_exports=exports,
    )


def solution_export_from_row(row: dbm.SolutionExport) -> schemas.SolutionExportRead:
    return schemas.SolutionExportRead(
        id=row.id,
        solution_id=row.solution_id,
        export_type=row.export_type,
        version=row.version,
        lifecycle_status=row.lifecycle_status,
        retention_until=row.retention_until.isoformat() if row.retention_until else None,
        superseded_by_export_id=row.superseded_by_export_id,
        storage_key=row.storage_key,
        checksum=row.checksum,
        storage_backend=row.storage_backend,
        storage_object_key=row.storage_object_key,
        storage_version_id=row.storage_version_id,
        storage_etag=row.storage_etag,
        storage_size_bytes=row.storage_size_bytes,
        download_path=f"/solutions/exports/{row.id}/download",
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def build_solution_export_manifest(db: Session, solution_id: str) -> dict[str, Any]:
    exports = list_solution_exports(db, solution_id)
    generated_at = utc_now().isoformat()
    export_rows = []
    for export in exports:
        storage_info = storage_inspect_object(export.storage_key, version_id=export.storage_version_id)
        export_rows.append(
            {
                "id": export.id,
                "export_type": export.export_type,
                "version": export.version,
                "lifecycle_status": export.lifecycle_status,
                "retention_until": export.retention_until,
                "superseded_by_export_id": export.superseded_by_export_id,
                "storage_key": export.storage_key,
                "storage_backend": export.storage_backend or storage_info.backend,
                "storage_object_key": export.storage_object_key or storage_info.object_key,
                "storage_version_id": export.storage_version_id,
                "storage_etag": export.storage_etag,
                "storage_size_bytes": export.storage_size_bytes,
                "current_storage_version_id": storage_info.version_id,
                "current_storage_etag": storage_info.etag,
                "current_storage_size_bytes": storage_info.size,
                "storage_exists": storage_info.exists,
                "checksum": export.checksum,
                "download_path": export.download_path,
                "created_at": export.created_at,
                "updated_at": export.updated_at,
            }
        )
    return {
        "solution_id": solution_id,
        "generated_at": generated_at,
        "export_count": len(export_rows),
        "active_export_count": sum(1 for item in export_rows if item["lifecycle_status"] == "active"),
        "archived_export_count": sum(1 for item in export_rows if item["lifecycle_status"] == "archived"),
        "expired_export_count": sum(
            1
            for item in export_rows
            if item["retention_until"] is not None and item["retention_until"] <= generated_at
        ),
        "exports": export_rows,
    }


def build_solution_export_recovery_report(
    db: Session,
    solution_id: str,
    *,
    include_archive_dry_run: bool = True,
) -> schemas.SolutionExportRecoveryReport:
    generated_at = utc_now().isoformat()
    items = [_build_solution_export_recovery_item(export) for export in list_solution_exports(db, solution_id)]
    archive_dry_run = (
        archive_expired_solution_exports(db, solution_id=solution_id, dry_run=True) if include_archive_dry_run else None
    )
    checksum_mismatch_count = sum(1 for item in items if item.status == "checksum_mismatch")
    version_mismatch_count = sum(1 for item in items if item.status == "version_mismatch")
    missing_count = sum(1 for item in items if item.status == "missing")
    unreadable_count = sum(1 for item in items if item.status == "unreadable")
    ok_count = sum(1 for item in items if item.status == "ok")
    failed_count = checksum_mismatch_count + version_mismatch_count + missing_count + unreadable_count
    return schemas.SolutionExportRecoveryReport(
        solution_id=solution_id,
        generated_at=generated_at,
        status="failed" if failed_count else "passed",
        checked_count=len(items),
        ok_count=ok_count,
        missing_count=missing_count,
        unreadable_count=unreadable_count,
        checksum_mismatch_count=checksum_mismatch_count,
        version_mismatch_count=version_mismatch_count,
        archive_dry_run=archive_dry_run,
        items=items,
    )


def _build_solution_export_recovery_item(export: schemas.SolutionExportRead) -> schemas.SolutionExportRecoveryItem:
    storage_info = storage_inspect_object(export.storage_key, version_id=export.storage_version_id)
    storage_backend = export.storage_backend or storage_info.backend
    bucket = storage_info.bucket
    object_key = export.storage_object_key or storage_info.object_key
    storage_exists_value = storage_info.exists
    status: str = "ok"
    error = storage_info.error
    size_bytes = None
    actual_checksum = None
    if not storage_exists_value:
        status = "missing"
    else:
        try:
            payload = storage_read_bytes(export.storage_key, version_id=export.storage_version_id)
            size_bytes = len(payload)
            actual_checksum = hashlib.sha256(payload).hexdigest()
            if export.checksum and actual_checksum != export.checksum:
                status = "checksum_mismatch"
            elif export.storage_version_id and storage_info.version_id and storage_info.version_id != export.storage_version_id:
                status = "version_mismatch"
            elif export.storage_etag and storage_info.etag and storage_info.etag != export.storage_etag:
                status = "version_mismatch"
        except Exception as exc:
            status = "unreadable"
            error = str(exc)
    return schemas.SolutionExportRecoveryItem(
        export_id=export.id,
        export_type=export.export_type,
        version=export.version,
        lifecycle_status=export.lifecycle_status,
        storage_key=export.storage_key,
        storage_backend=storage_backend,
        bucket=bucket,
        object_key=object_key,
        expected_storage_version_id=export.storage_version_id,
        actual_storage_version_id=storage_info.version_id,
        expected_etag=export.storage_etag,
        actual_etag=storage_info.etag,
        storage_exists=storage_exists_value,
        size_bytes=size_bytes if size_bytes is not None else storage_info.size,
        expected_checksum=export.checksum,
        actual_checksum=actual_checksum,
        status=status,  # type: ignore[arg-type]
        error=error,
    )


def list_external_systems(db: Session) -> list[schemas.ExternalSystemRead]:
    rows = db.scalars(select(dbm.ExternalSystem).order_by(dbm.ExternalSystem.system_type, dbm.ExternalSystem.name)).all()
    return [external_system_from_row(row) for row in rows]


def get_external_system(db: Session, external_system_id: str) -> schemas.ExternalSystemRead | None:
    row = db.get(dbm.ExternalSystem, external_system_id)
    return external_system_from_row(row) if row else None


def create_external_system(db: Session, payload: schemas.ExternalSystemCreate) -> schemas.ExternalSystemRead:
    row = dbm.ExternalSystem(name=payload.name, system_type=payload.system_type, enabled=payload.enabled)
    db.add(row)
    db.commit()
    return external_system_from_row(row)


def update_external_system(
    db: Session,
    external_system_id: str,
    payload: schemas.ExternalSystemUpdate,
) -> schemas.ExternalSystemRead | None:
    row = db.get(dbm.ExternalSystem, external_system_id)
    if row is None:
        return None
    if payload.name is not None:
        row.name = payload.name
    if payload.system_type is not None:
        row.system_type = payload.system_type
    if payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    return external_system_from_row(row)


def list_adapter_configs(db: Session, external_system_id: str | None = None) -> list[schemas.AdapterConfigRead]:
    query = select(dbm.AdapterConfig).order_by(
        dbm.AdapterConfig.external_system_id,
        dbm.AdapterConfig.adapter_type,
        dbm.AdapterConfig.version.desc(),
        dbm.AdapterConfig.created_at.desc(),
    )
    if external_system_id:
        query = query.where(dbm.AdapterConfig.external_system_id == external_system_id)
    return [adapter_config_from_row(row) for row in db.scalars(query).all()]


def create_adapter_config(
    db: Session,
    external_system_id: str,
    payload: schemas.AdapterConfigCreate,
) -> schemas.AdapterConfigRead | None:
    system = db.get(dbm.ExternalSystem, external_system_id)
    if system is None:
        return None
    if payload.is_active and adapter_config_requires_dictionary_signoff(payload.config):
        raise ValueError("dictionary signoff is required before activating this adapter config")
    existing_rows = db.scalars(
        select(dbm.AdapterConfig).where(
            dbm.AdapterConfig.external_system_id == external_system_id,
            dbm.AdapterConfig.adapter_type == payload.adapter_type,
        )
    ).all()
    if payload.is_active:
        for row in existing_rows:
            row.is_active = False
    version = max((row.version for row in existing_rows), default=0) + 1
    row = dbm.AdapterConfig(
        external_system_id=external_system_id,
        adapter_type=payload.adapter_type,
        version=version,
        is_active=payload.is_active,
        validation_status="untested",
        config=payload.config,
    )
    db.add(row)
    db.commit()
    return adapter_config_from_row(row)


def get_adapter_config(db: Session, config_id: str) -> schemas.AdapterConfigRead | None:
    row = db.get(dbm.AdapterConfig, config_id)
    return adapter_config_from_row(row) if row else None


def update_adapter_config_runtime_state(
    db: Session,
    config_id: str,
    state_update: dict[str, Any],
) -> schemas.AdapterConfigRead | None:
    row = db.get(dbm.AdapterConfig, config_id)
    if row is None:
        return None
    config = dict(row.config or {})
    state = dict(config.get("state") or {})
    state.update(state_update)
    config["state"] = state
    row.config = config
    db.commit()
    return adapter_config_from_row(row)


def activate_adapter_config(db: Session, config_id: str) -> schemas.AdapterConfigRead | None:
    row = db.get(dbm.AdapterConfig, config_id)
    if row is None:
        return None
    if row.validation_status != "passed":
        raise ValueError("adapter config validation must pass before activation")
    if adapter_config_requires_dictionary_signoff(row.config or {}) and not adapter_config_dictionary_signed(row.config or {}):
        raise ValueError("dictionary signoff is required before activating this adapter config")
    siblings = db.scalars(
        select(dbm.AdapterConfig).where(
            dbm.AdapterConfig.external_system_id == row.external_system_id,
            dbm.AdapterConfig.adapter_type == row.adapter_type,
        )
    ).all()
    for sibling in siblings:
        sibling.is_active = False
    row.is_active = True
    db.commit()
    return adapter_config_from_row(row)


def update_adapter_config_dictionary_signoff(
    db: Session,
    config_id: str,
    signoff: dict[str, Any],
) -> schemas.AdapterConfigRead | None:
    row = db.get(dbm.AdapterConfig, config_id)
    if row is None:
        return None
    config = dict(row.config or {})
    config["dictionary_signoff"] = signoff
    row.config = config
    db.commit()
    return adapter_config_from_row(row)


def adapter_config_requires_dictionary_signoff(config: dict[str, Any]) -> bool:
    if not isinstance(config, dict):
        return False
    explicit_required = config.get("dictionary_signoff_required")
    if explicit_required is not None:
        return bool(explicit_required)
    has_dictionary = any(
        isinstance(config.get(key), dict)
        for key in (
            "status_dictionary",
            "status_mapping",
            "inbound_status_dictionary",
            "inbound_status_mapping",
            "writeback_status_dictionary",
            "writeback_status_mapping",
            "outbound_status_dictionary",
            "outbound_status_mapping",
        )
    )
    writeback_config = config.get("writeback") if isinstance(config.get("writeback"), dict) else {}
    if writeback_config:
        has_dictionary = has_dictionary or any(
            isinstance(writeback_config.get(key), dict)
            for key in ("status_dictionary", "status_mapping", "outbound_status_dictionary", "outbound_status_mapping")
        )
    organization_acceptance = {}
    for key in ("organization_acceptance", "org_acceptance", "organization_directory", "org_directory"):
        if isinstance(config.get(key), dict):
            organization_acceptance = config[key]
            break
    if organization_acceptance:
        has_dictionary = has_dictionary or any(
            organization_acceptance.get(key)
            for key in (
                "required_org_unit_codes",
                "org_unit_codes",
                "required_recipient_group_names",
                "recipient_group_names",
            )
        )
    has_dictionary = has_dictionary or any(
        config.get(key) for key in ("required_org_unit_codes", "required_recipient_group_names")
    )
    if not has_dictionary:
        return False
    source_mode = str(config.get("mode") or config.get("source") or "").lower()
    source_is_http = source_mode in {"http", "api", "remote"}
    source_dry_run = bool(config.get("dry_run", True))
    writeback_mode = str(
        (writeback_config or {}).get("mode")
        or (writeback_config or {}).get("source")
        or config.get("writeback_mode")
        or ""
    ).lower()
    writeback_is_http = writeback_mode in {"http", "api", "remote"}
    writeback_dry_run = bool((writeback_config or {}).get("dry_run", config.get("writeback_dry_run", True)))
    return (source_is_http and not source_dry_run) or (writeback_is_http and not writeback_dry_run)


def adapter_config_dictionary_signed(config: dict[str, Any]) -> bool:
    signoff = config.get("dictionary_signoff") if isinstance(config, dict) else None
    return isinstance(signoff, dict) and signoff.get("status") == "signed"


def test_adapter_connection(db: Session, config_id: str) -> schemas.AdapterConnectionTestResult | None:
    row = db.get(dbm.AdapterConfig, config_id)
    if row is None:
        return None
    missing_fields = adapter_config_missing_fields(row.adapter_type, row.config or {})
    row.validation_status = "failed" if missing_fields else "passed"
    db.commit()
    message = (
        "adapter configuration validation passed"
        if not missing_fields
        else f"missing required fields: {', '.join(missing_fields)}"
    )
    return schemas.AdapterConnectionTestResult(
        config_id=row.id,
        status=row.validation_status,
        missing_fields=missing_fields,
        message=message,
    )


def create_sync_task(
    db: Session,
    *,
    external_system_id: str,
    task_type: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> schemas.SyncTaskRead:
    row = dbm.SyncTask(
        external_system_id=external_system_id,
        task_type=task_type,
        status=status,
        payload=_redacted_payload_dict(payload or {}),
    )
    db.add(row)
    db.commit()
    return sync_task_from_row(row)


def get_sync_task_row(db: Session, task_id: str) -> dbm.SyncTask | None:
    return db.get(dbm.SyncTask, task_id)


def list_sync_tasks(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.SyncTaskRead]:
    query = select(dbm.SyncTask).order_by(dbm.SyncTask.created_at.desc()).limit(limit)
    if external_system_id:
        query = query.where(dbm.SyncTask.external_system_id == external_system_id)
    return [sync_task_from_row(row) for row in db.scalars(query).all()]


def list_sync_retry_queue(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.SyncTaskRead]:
    scan_limit = max(limit * 5, 500)
    query = select(dbm.SyncTask).order_by(dbm.SyncTask.created_at.desc()).limit(scan_limit)
    if external_system_id:
        query = query.where(dbm.SyncTask.external_system_id == external_system_id)
    rows = list(db.scalars(query).all())
    completed_retry_parent_ids: set[str] = set()
    for row in rows:
        if row.status != "completed" or not isinstance(row.payload, dict):
            continue
        for key in ("retry_of_task_id", "root_task_id"):
            parent_id = row.payload.get(key)
            if parent_id:
                completed_retry_parent_ids.add(str(parent_id))
    retryable_rows = [
        row
        for row in rows
        if row.status == "failed" and row.id not in completed_retry_parent_ids and (row.payload or {}).get("retryable", True)
    ]
    return [sync_task_from_row(row) for row in retryable_rows[:limit]]


def create_writeback_log(
    db: Session,
    *,
    external_system_id: str | None,
    target_id: str | None,
    status: str,
    payload: dict[str, Any] | None = None,
) -> schemas.WritebackLogRead:
    row = dbm.WritebackLog(
        external_system_id=external_system_id,
        target_id=target_id,
        status=status,
        payload=_redacted_payload_dict(payload or {}),
    )
    db.add(row)
    db.commit()
    return writeback_log_from_row(row)


def list_writeback_logs(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.WritebackLogRead]:
    query = select(dbm.WritebackLog).order_by(dbm.WritebackLog.created_at.desc()).limit(limit)
    if external_system_id:
        query = query.where(dbm.WritebackLog.external_system_id == external_system_id)
    return [writeback_log_from_row(row) for row in db.scalars(query).all()]


def upsert_production_schedule_entry(
    db: Session,
    *,
    external_system_id: str,
    record: dict[str, Any],
    sync_task_id: str | None = None,
) -> schemas.ProductionScheduleEntryRead:
    fields = _domain_record_fields(record)
    external_id = _required_domain_external_id(record)
    values = {
        "sync_task_id": sync_task_id,
        "order_id": _field_as_str(fields, "order_id", "orderId", "external_order_id"),
        "job_id": _field_as_str(fields, "job_id", "jobId", "work_order_id"),
        "line_code": _field_as_str(fields, "line_code", "line", "lineCode"),
        "machine_code": _field_as_str(fields, "machine_code", "machine", "machineCode"),
        "workstation": _field_as_str(fields, "workstation", "station", "work_center"),
        "planned_start_at": _field_as_str(fields, "planned_start_at", "planned_start", "start_at", "start"),
        "planned_end_at": _field_as_str(fields, "planned_end_at", "planned_end", "end_at", "end"),
        "status": _record_status(record),
        "quantity": _field_as_float(fields, "quantity", "qty", "planned_qty"),
        "fields": fields,
    }
    row = db.scalar(
        select(dbm.ProductionScheduleEntry).where(
            dbm.ProductionScheduleEntry.external_system_id == external_system_id,
            dbm.ProductionScheduleEntry.external_id == external_id,
        )
    )
    if row is None:
        row = dbm.ProductionScheduleEntry(external_system_id=external_system_id, external_id=external_id, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return production_schedule_entry_from_row(row)


def upsert_inventory_snapshot(
    db: Session,
    *,
    external_system_id: str,
    record: dict[str, Any],
    sync_task_id: str | None = None,
) -> schemas.InventorySnapshotRead:
    fields = _domain_record_fields(record)
    external_id = _required_domain_external_id(record)
    values = {
        "sync_task_id": sync_task_id,
        "material_code": _field_as_str(fields, "material_code", "material", "sku", "item_code"),
        "material_name": _field_as_str(fields, "material_name", "material_title", "item_name"),
        "batch_no": _field_as_str(fields, "batch_no", "batch", "lot_no"),
        "warehouse_code": _field_as_str(fields, "warehouse_code", "warehouse", "warehouse_id"),
        "status": _record_status(record),
        "available_qty": _field_as_float(fields, "available_qty", "available", "qty_available", "stock_qty"),
        "reserved_qty": _field_as_float(fields, "reserved_qty", "reserved", "qty_reserved"),
        "unit": _field_as_str(fields, "unit", "uom"),
        "fields": fields,
    }
    row = db.scalar(
        select(dbm.InventorySnapshot).where(
            dbm.InventorySnapshot.external_system_id == external_system_id,
            dbm.InventorySnapshot.external_id == external_id,
        )
    )
    if row is None:
        row = dbm.InventorySnapshot(external_system_id=external_system_id, external_id=external_id, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return inventory_snapshot_from_row(row)


def upsert_delivery_confirmation(
    db: Session,
    *,
    external_system_id: str,
    record: dict[str, Any],
    sync_task_id: str | None = None,
) -> schemas.DeliveryConfirmationRead:
    fields = _domain_record_fields(record)
    external_id = _required_domain_external_id(record)
    values = {
        "sync_task_id": sync_task_id,
        "order_id": _field_as_str(fields, "order_id", "orderId", "external_order_id"),
        "shipment_no": _field_as_str(fields, "shipment_no", "shipment", "shipment_id"),
        "carrier": _field_as_str(fields, "carrier", "carrier_name"),
        "tracking_no": _field_as_str(fields, "tracking_no", "tracking", "tracking_number"),
        "status": _record_status(record),
        "shipped_at": _field_as_str(fields, "shipped_at", "ship_at", "shipped_time"),
        "delivered_at": _field_as_str(fields, "delivered_at", "delivery_at", "confirmed_at"),
        "quantity": _field_as_float(fields, "quantity", "qty", "delivered_qty"),
        "fields": fields,
    }
    row = db.scalar(
        select(dbm.DeliveryConfirmation).where(
            dbm.DeliveryConfirmation.external_system_id == external_system_id,
            dbm.DeliveryConfirmation.external_id == external_id,
        )
    )
    if row is None:
        row = dbm.DeliveryConfirmation(external_system_id=external_system_id, external_id=external_id, **values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return delivery_confirmation_from_row(row)


def list_production_schedule_entries(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.ProductionScheduleEntryRead]:
    query = select(dbm.ProductionScheduleEntry).order_by(dbm.ProductionScheduleEntry.updated_at.desc()).limit(limit)
    if external_system_id:
        query = query.where(dbm.ProductionScheduleEntry.external_system_id == external_system_id)
    return [production_schedule_entry_from_row(row) for row in db.scalars(query).all()]


def list_inventory_snapshots(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.InventorySnapshotRead]:
    query = select(dbm.InventorySnapshot).order_by(dbm.InventorySnapshot.updated_at.desc()).limit(limit)
    if external_system_id:
        query = query.where(dbm.InventorySnapshot.external_system_id == external_system_id)
    return [inventory_snapshot_from_row(row) for row in db.scalars(query).all()]


def list_delivery_confirmations(
    db: Session,
    *,
    external_system_id: str | None = None,
    limit: int = 100,
) -> list[schemas.DeliveryConfirmationRead]:
    query = select(dbm.DeliveryConfirmation).order_by(dbm.DeliveryConfirmation.updated_at.desc()).limit(limit)
    if external_system_id:
        query = query.where(dbm.DeliveryConfirmation.external_system_id == external_system_id)
    return [delivery_confirmation_from_row(row) for row in db.scalars(query).all()]


def attach_domain_records_to_sync_task(db: Session, task_id: str, payload: dict[str, Any]) -> int:
    record_ids = [str(item) for item in payload.get("domain_record_ids") or [] if item]
    if not record_ids:
        return 0
    updated_count = 0
    for model in (dbm.ProductionScheduleEntry, dbm.InventorySnapshot, dbm.DeliveryConfirmation):
        rows = db.scalars(select(model).where(model.id.in_(record_ids))).all()
        for row in rows:
            row.sync_task_id = task_id
            updated_count += 1
    if updated_count:
        db.commit()
    return updated_count


def get_active_adapter_config_for_system_type(db: Session, system_type: str) -> tuple[dbm.ExternalSystem, dbm.AdapterConfig] | None:
    row = db.execute(
        select(dbm.ExternalSystem, dbm.AdapterConfig)
        .join(dbm.AdapterConfig, dbm.AdapterConfig.external_system_id == dbm.ExternalSystem.id)
        .where(
            dbm.ExternalSystem.system_type == system_type,
            dbm.ExternalSystem.enabled.is_(True),
            dbm.AdapterConfig.is_active.is_(True),
        )
        .order_by(dbm.AdapterConfig.created_at.desc(), dbm.AdapterConfig.version.desc())
    ).first()
    return (row[0], row[1]) if row else None


def build_adapter_status(db: Session) -> schemas.AdapterStatusRead:
    systems = list_external_systems(db)
    configs = list_adapter_configs(db)
    active_config_count = sum(1 for config in configs if config.is_active)
    configured_system_ids = {config.external_system_id for config in configs}
    return schemas.AdapterStatusRead(
        systems=systems,
        configs=configs,
        active_config_count=active_config_count,
        configured_system_count=len(configured_system_ids),
        enabled_system_count=sum(1 for system in systems if system.enabled),
    )


def adapter_config_missing_fields(adapter_type: str, config: dict[str, Any]) -> list[str]:
    mode = str(config.get("mode", "")).lower()
    if adapter_type.startswith("mock_") or mode in {"mock", "manual", "dry_run"}:
        return []
    missing: list[str] = []
    if not config.get("base_url"):
        missing.append("base_url")
    auth_type = str(config.get("auth_type", "")).lower()
    if auth_type in {"api_key", "bearer"} and not config.get("api_key"):
        missing.append("api_key")
    elif auth_type == "basic":
        if not config.get("username"):
            missing.append("username")
        if not config.get("password"):
            missing.append("password")
    return missing


def external_system_from_row(row: dbm.ExternalSystem) -> schemas.ExternalSystemRead:
    return schemas.ExternalSystemRead(
        id=row.id,
        name=row.name,
        system_type=row.system_type,
        enabled=row.enabled,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def adapter_config_from_row(row: dbm.AdapterConfig) -> schemas.AdapterConfigRead:
    return schemas.AdapterConfigRead(
        id=row.id,
        external_system_id=row.external_system_id,
        adapter_type=row.adapter_type,
        version=row.version,
        is_active=row.is_active,
        validation_status=row.validation_status,
        config=mask_sensitive_config(row.config or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def sync_task_from_row(row: dbm.SyncTask) -> schemas.SyncTaskRead:
    return schemas.SyncTaskRead(
        id=row.id,
        external_system_id=row.external_system_id,
        task_type=row.task_type,
        status=row.status,
        payload=_redacted_payload_dict(row.payload or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def writeback_log_from_row(row: dbm.WritebackLog) -> schemas.WritebackLogRead:
    return schemas.WritebackLogRead(
        id=row.id,
        external_system_id=row.external_system_id,
        target_id=row.target_id,
        status=row.status,
        payload=_redacted_payload_dict(row.payload or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def production_schedule_entry_from_row(row: dbm.ProductionScheduleEntry) -> schemas.ProductionScheduleEntryRead:
    return schemas.ProductionScheduleEntryRead(
        id=row.id,
        external_system_id=row.external_system_id,
        sync_task_id=row.sync_task_id,
        external_id=row.external_id,
        order_id=row.order_id,
        job_id=row.job_id,
        line_code=row.line_code,
        machine_code=row.machine_code,
        workstation=row.workstation,
        planned_start_at=row.planned_start_at,
        planned_end_at=row.planned_end_at,
        status=row.status,
        quantity=row.quantity,
        fields=_redacted_payload_dict(row.fields or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def inventory_snapshot_from_row(row: dbm.InventorySnapshot) -> schemas.InventorySnapshotRead:
    return schemas.InventorySnapshotRead(
        id=row.id,
        external_system_id=row.external_system_id,
        sync_task_id=row.sync_task_id,
        external_id=row.external_id,
        material_code=row.material_code,
        material_name=row.material_name,
        batch_no=row.batch_no,
        warehouse_code=row.warehouse_code,
        status=row.status,
        available_qty=row.available_qty,
        reserved_qty=row.reserved_qty,
        unit=row.unit,
        fields=_redacted_payload_dict(row.fields or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def delivery_confirmation_from_row(row: dbm.DeliveryConfirmation) -> schemas.DeliveryConfirmationRead:
    return schemas.DeliveryConfirmationRead(
        id=row.id,
        external_system_id=row.external_system_id,
        sync_task_id=row.sync_task_id,
        external_id=row.external_id,
        order_id=row.order_id,
        shipment_no=row.shipment_no,
        carrier=row.carrier,
        tracking_no=row.tracking_no,
        status=row.status,
        shipped_at=row.shipped_at,
        delivered_at=row.delivered_at,
        quantity=row.quantity,
        fields=_redacted_payload_dict(row.fields or {}),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def mask_sensitive_config(value: dict[str, Any]) -> dict[str, Any]:
    return _redacted_payload_dict(value)


def _redacted_payload_dict(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_sensitive_payload(value)
    return redacted if isinstance(redacted, dict) else {}


def redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_payload_key(key_text):
                redacted[key] = SENSITIVE_VALUE_PLACEHOLDER
            else:
                redacted[key] = redact_sensitive_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_url_sensitive_parts(value)
    return value


def _is_sensitive_payload_key(key: str) -> bool:
    key_lower = key.lower()
    if key_lower in SENSITIVE_EXACT_KEYS:
        return True
    if key_lower in SENSITIVE_KEY_EXEMPTIONS or key_lower.endswith(SENSITIVE_KEY_SUFFIX_EXEMPTIONS):
        return False
    return any(part in key_lower for part in SENSITIVE_CONFIG_KEY_PARTS)


def _redact_url_sensitive_parts(value: str) -> str:
    if not _is_url_like(value):
        return value
    return _redact_url_query_secrets(_redact_url_password(value))


def _redact_url_password(value: str) -> str:
    parts = urlsplit(value)
    if parts.password is None:
        return value
    username = parts.username or ""
    netloc = username
    if username:
        netloc += f":{SENSITIVE_VALUE_PLACEHOLDER}@"
    else:
        netloc = f"{SENSITIVE_VALUE_PLACEHOLDER}@"
    netloc += parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _redact_url_query_secrets(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    redacted_items = [
        (name, SENSITIVE_VALUE_PLACEHOLDER if _is_sensitive_query_name(name) else item_value)
        for name, item_value in query_items
    ]
    if redacted_items == query_items:
        return value
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(redacted_items, doseq=True, safe="*"),
            parts.fragment,
        )
    )


def _is_url_like(value: str) -> bool:
    return bool(urlsplit(value).scheme and "://" in value)


def _is_sensitive_query_name(name: str) -> bool:
    upper_name = name.upper()
    return any(marker in upper_name for marker in SENSITIVE_QUERY_MARKERS)


def _domain_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields")
    return dict(fields) if isinstance(fields, dict) else {}


def _required_domain_external_id(record: dict[str, Any]) -> str:
    external_id = record.get("external_id")
    if external_id is None or not str(external_id).strip():
        raise ValueError("domain persistence requires external_id")
    return str(external_id).strip()


def _record_status(record: dict[str, Any]) -> str | None:
    status = record.get("status")
    return str(status) if status is not None else None


def _field_as_str(fields: dict[str, Any], *paths: str) -> str | None:
    value = _first_field_value(fields, *paths)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field_as_float(fields: dict[str, Any], *paths: str) -> float | None:
    value = _first_field_value(fields, *paths)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_field_value(fields: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value = _nested_value(fields, path)
        if value is not None:
            return value
    return None


def _nested_value(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
        if value is None:
            return None
    return value


def upsert_benchmark_case(
    db: Session,
    case: schemas.BenchmarkCase,
    *,
    source: str = "manual",
) -> schemas.BenchmarkCaseRead:
    row = db.get(dbm.BenchmarkCase, case.case_id)
    values = {
        "id": case.case_id,
        "name": case.name,
        "source": source,
        "case_json": case.model_dump(mode="json"),
        "baseline_utilization_rate": case.baseline_utilization_rate,
    }
    if row is None:
        row = dbm.BenchmarkCase(**values)
        db.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)
    db.commit()
    return benchmark_case_from_row(row)


def list_benchmark_cases(db: Session) -> list[schemas.BenchmarkCaseRead]:
    rows = db.scalars(select(dbm.BenchmarkCase).order_by(dbm.BenchmarkCase.updated_at.desc())).all()
    return [benchmark_case_from_row(row) for row in rows]


def get_benchmark_case(db: Session, case_id: str) -> schemas.BenchmarkCaseRead | None:
    row = db.get(dbm.BenchmarkCase, case_id)
    return benchmark_case_from_row(row) if row else None


def create_benchmark_run_record(
    db: Session,
    *,
    case_id: str,
    solver_name: str,
    planning_mode: schemas.PlanningMode = "single_sheet",
    utilization_rate: float,
    waste_rate: float,
    runtime_ms: int,
    valid: bool,
    hard_rule_pass: bool | None = None,
    quantity_fulfillment_rate: float = 0,
    requested_units: int = 0,
    produced_units: int = 0,
    shortage_units: int = 0,
    overproduction_units: int = 0,
    units_per_sheet: int = 0,
    sheets_used: int = 0,
    peak_rss_mb: float | None = None,
    export_ok: bool = False,
    case_score: float = 0,
    baseline_delta_utilization_rate: float | None = None,
    p95_runtime_ms: int | None = None,
    metrics: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> schemas.BenchmarkRunResult:
    row = dbm.BenchmarkRun(
        benchmark_case_id=case_id,
        solver_name=solver_name,
        planning_mode=planning_mode,
        utilization_rate=utilization_rate,
        waste_rate=waste_rate,
        runtime_ms=runtime_ms,
        valid=valid,
        hard_rule_pass=valid if hard_rule_pass is None else hard_rule_pass,
        quantity_fulfillment_rate=quantity_fulfillment_rate,
        requested_units=requested_units,
        produced_units=produced_units,
        shortage_units=shortage_units,
        overproduction_units=overproduction_units,
        units_per_sheet=units_per_sheet,
        sheets_used=sheets_used,
        peak_rss_mb=peak_rss_mb,
        export_ok=export_ok,
        case_score=case_score,
        baseline_delta_utilization_rate=baseline_delta_utilization_rate,
        p95_runtime_ms=p95_runtime_ms,
        metrics_json=metrics or {},
        failure_reason=failure_reason,
    )
    db.add(row)
    db.commit()
    return benchmark_run_from_row(row)


def list_benchmark_runs(
    db: Session,
    *,
    case_id: str | None = None,
    limit: int = 100,
) -> list[schemas.BenchmarkRunResult]:
    query = select(dbm.BenchmarkRun).order_by(dbm.BenchmarkRun.created_at.desc()).limit(limit)
    if case_id:
        query = query.where(dbm.BenchmarkRun.benchmark_case_id == case_id)
    return [benchmark_run_from_row(row) for row in db.scalars(query).all()]


def benchmark_case_from_row(row: dbm.BenchmarkCase) -> schemas.BenchmarkCaseRead:
    payload = dict(row.case_json or {})
    payload["case_id"] = row.id
    payload["name"] = row.name
    payload["baseline_utilization_rate"] = row.baseline_utilization_rate
    payload["source"] = row.source
    payload["created_at"] = row.created_at.isoformat()
    payload["updated_at"] = row.updated_at.isoformat()
    return schemas.BenchmarkCaseRead.model_validate(payload)


def benchmark_run_from_row(row: dbm.BenchmarkRun) -> schemas.BenchmarkRunResult:
    return schemas.BenchmarkRunResult(
        run_id=row.id,
        case_id=row.benchmark_case_id,
        solver_name=row.solver_name,
        planning_mode=row.planning_mode,
        utilization_rate=row.utilization_rate,
        waste_rate=row.waste_rate,
        runtime_ms=row.runtime_ms,
        valid=row.valid,
        hard_rule_pass=row.hard_rule_pass,
        quantity_fulfillment_rate=row.quantity_fulfillment_rate,
        requested_units=row.requested_units,
        produced_units=row.produced_units,
        shortage_units=row.shortage_units,
        overproduction_units=row.overproduction_units,
        units_per_sheet=row.units_per_sheet,
        sheets_used=row.sheets_used,
        peak_rss_mb=row.peak_rss_mb,
        export_ok=row.export_ok,
        case_score=row.case_score,
        baseline_delta_utilization_rate=row.baseline_delta_utilization_rate,
        p95_runtime_ms=row.p95_runtime_ms,
        metrics=row.metrics_json or {},
        failure_reason=row.failure_reason,
        created_at=row.created_at.isoformat(),
    )


def production_plan_approval_from_row(row: dbm.ProductionPlanApproval) -> schemas.ProductionPlanApprovalRead:
    return schemas.ProductionPlanApprovalRead(
        id=row.id,
        plan_id=row.plan_id,
        requested_by=row.requested_by,
        decided_by=row.decided_by,
        status=row.status,
        request_note=row.request_note,
        decision_note=row.decision_note,
        snapshot=row.snapshot,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def production_plan_export_from_row(row: dbm.ProductionPlanExport) -> schemas.ProductionPlanExportRead:
    return schemas.ProductionPlanExportRead(
        id=row.id,
        plan_id=row.plan_id,
        export_type=row.export_type,
        version=row.version,
        lifecycle_status=row.lifecycle_status,
        storage_key=row.storage_key,
        checksum=row.checksum,
        storage_backend=row.storage_backend,
        storage_object_key=row.storage_object_key,
        storage_version_id=row.storage_version_id,
        storage_etag=row.storage_etag,
        storage_size_bytes=row.storage_size_bytes,
        download_path=f"/batch-layout/plans/exports/{row.id}/download",
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def solution_approval_from_row(row: dbm.SolutionApproval) -> schemas.SolutionApprovalRead:
    return schemas.SolutionApprovalRead(
        id=row.id,
        solution_id=row.solution_id,
        requested_by=row.requested_by,
        decided_by=row.decided_by,
        status=row.status,
        request_note=row.request_note,
        decision_note=row.decision_note,
        snapshot=row.snapshot,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )
