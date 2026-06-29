from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import alembic_head_state, metadata_schema_gaps, requires_explicit_migrations
from app.services import storage


def build_readiness_report(db: Session, require_migration_head: bool | None = None) -> dict[str, Any]:
    if require_migration_head is None:
        require_migration_head = requires_explicit_migrations(get_settings().environment)
    checks = [
        _database_check(db),
        _schema_check(db),
    ]
    if require_migration_head:
        checks.append(_migration_check(db))
    checks.append(_storage_check())
    status = "ok" if all(check["status"] == "ok" for check in checks) else "degraded"
    return {
        "status": status,
        "service": "packaging-nesting-api",
        "checks": checks,
    }


def _database_check(db: Session) -> dict[str, Any]:
    try:
        db.execute(text("SELECT 1"))
        return {"name": "database", "status": "ok"}
    except Exception as exc:
        return {"name": "database", "status": "failed", "error": str(exc)}


def _schema_check(db: Session) -> dict[str, Any]:
    try:
        missing_tables, missing_columns = metadata_schema_gaps(db.get_bind())
    except Exception as exc:
        return {"name": "schema", "status": "failed", "error": str(exc)}
    if missing_tables or missing_columns:
        return {
            "name": "schema",
            "status": "failed",
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
        }
    return {"name": "schema", "status": "ok"}


def _migration_check(db: Session) -> dict[str, Any]:
    try:
        state = alembic_head_state(db.get_bind())
    except Exception as exc:
        return {"name": "migration", "status": "failed", "error": str(exc)}
    if state["missing_heads"] or state["unexpected_heads"]:
        return {"name": "migration", "status": "failed", **state}
    return {"name": "migration", "status": "ok", **state}


def _storage_check() -> dict[str, Any]:
    try:
        payload = storage.readiness_check()
        return {"name": "storage", "status": "ok", **payload}
    except Exception as exc:
        return {"name": "storage", "status": "failed", "error": str(exc)}
