from collections.abc import Generator
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings, is_production_environment
from app.db.base import Base


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
_initialized = False
BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_SCRIPT_LOCATION = BACKEND_DIR / "alembic"


def get_db() -> Generator[Session, None, None]:
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    # Development convenience. Production deployments should run Alembic migrations explicitly.
    from app.db import models  # noqa: F401
    from app.services import repository
    from app.services.security import hash_password

    if requires_explicit_migrations(settings.environment):
        assert_metadata_schema_ready(engine)
        assert_alembic_head_ready(engine)
    else:
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_schema_compat()
    with SessionLocal() as db:
        repository.seed_rbac(db, hash_password)
        repository.ensure_default_rule_set(db)
        repository.seed_solver_registry(db)
    _initialized = True


def requires_explicit_migrations(environment: str) -> bool:
    return is_production_environment(environment)


def metadata_schema_gaps(bind) -> tuple[list[str], dict[str, list[str]]]:
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    missing_tables = sorted(set(Base.metadata.tables) - table_names)
    missing_columns: dict[str, list[str]] = {}
    for table_name, table in Base.metadata.tables.items():
        if table_name not in table_names:
            continue
        db_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing = sorted(set(table.columns.keys()) - db_columns)
        if missing:
            missing_columns[table_name] = missing
    return missing_tables, missing_columns


def assert_metadata_schema_ready(bind) -> None:
    missing_tables, missing_columns = metadata_schema_gaps(bind)
    if not missing_tables and not missing_columns:
        return
    details: list[str] = []
    if missing_tables:
        details.append(f"missing tables: {', '.join(missing_tables[:12])}")
    if missing_columns:
        column_details = [
            f"{table_name}.{column_name}"
            for table_name, column_names in sorted(missing_columns.items())
            for column_name in column_names
        ]
        details.append(f"missing columns: {', '.join(column_details[:20])}")
    raise RuntimeError(
        "Database schema is not initialized for production. "
        "Run `alembic -c alembic.ini upgrade head` before starting the API. "
        + "; ".join(details)
    )


def expected_alembic_heads() -> set[str]:
    config = AlembicConfig()
    config.set_main_option("script_location", str(ALEMBIC_SCRIPT_LOCATION))
    script = ScriptDirectory.from_config(config)
    return set(script.get_heads())


def alembic_head_state(bind) -> dict[str, list[str]]:
    inspector = inspect(bind)
    expected_heads = expected_alembic_heads()
    if "alembic_version" not in set(inspector.get_table_names()):
        database_heads: set[str] = set()
    else:
        with bind.connect() as connection:
            database_heads = {row[0] for row in connection.execute(text("SELECT version_num FROM alembic_version"))}
    return {
        "expected_heads": sorted(expected_heads),
        "database_heads": sorted(database_heads),
        "missing_heads": sorted(expected_heads - database_heads),
        "unexpected_heads": sorted(database_heads - expected_heads),
    }


def assert_alembic_head_ready(bind) -> None:
    state = alembic_head_state(bind)
    if not state["missing_heads"] and not state["unexpected_heads"]:
        return
    details: list[str] = []
    if state["missing_heads"]:
        details.append(f"missing heads: {', '.join(state['missing_heads'])}")
    if state["unexpected_heads"]:
        details.append(f"database heads: {', '.join(state['unexpected_heads'])}")
    raise RuntimeError(
        "Database Alembic migration state is not at repository head. "
        "Run `alembic -c alembic.ini upgrade head` before starting the API. "
        + "; ".join(details)
    )


def ensure_sqlite_schema_compat() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    work_task_columns = {column["name"] for column in inspector.get_columns("work_task")} if "work_task" in table_names else set()
    work_task_ddl = {
        "parent_task_id": "ALTER TABLE work_task ADD COLUMN parent_task_id VARCHAR(64)",
        "attempt": "ALTER TABLE work_task ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
        "max_attempts": "ALTER TABLE work_task ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
        "timeout_sec": "ALTER TABLE work_task ADD COLUMN timeout_sec INTEGER",
        "cancel_requested": "ALTER TABLE work_task ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0",
        "progress_percent": "ALTER TABLE work_task ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0",
        "heartbeat_at": "ALTER TABLE work_task ADD COLUMN heartbeat_at DATETIME",
    }
    export_columns = (
        {column["name"] for column in inspector.get_columns("solution_export")} if "solution_export" in table_names else set()
    )
    export_ddl = {
        "version": "ALTER TABLE solution_export ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
        "lifecycle_status": "ALTER TABLE solution_export ADD COLUMN lifecycle_status VARCHAR(40) NOT NULL DEFAULT 'active'",
        "retention_until": "ALTER TABLE solution_export ADD COLUMN retention_until DATETIME",
        "superseded_by_export_id": "ALTER TABLE solution_export ADD COLUMN superseded_by_export_id VARCHAR(64)",
        "storage_backend": "ALTER TABLE solution_export ADD COLUMN storage_backend VARCHAR(40)",
        "storage_object_key": "ALTER TABLE solution_export ADD COLUMN storage_object_key VARCHAR(500)",
        "storage_version_id": "ALTER TABLE solution_export ADD COLUMN storage_version_id VARCHAR(255)",
        "storage_etag": "ALTER TABLE solution_export ADD COLUMN storage_etag VARCHAR(255)",
        "storage_size_bytes": "ALTER TABLE solution_export ADD COLUMN storage_size_bytes INTEGER",
    }
    adapter_config_columns = (
        {column["name"] for column in inspector.get_columns("adapter_config")} if "adapter_config" in table_names else set()
    )
    adapter_config_ddl = {
        "version": "ALTER TABLE adapter_config ADD COLUMN version INTEGER NOT NULL DEFAULT 1",
        "is_active": "ALTER TABLE adapter_config ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
        "validation_status": "ALTER TABLE adapter_config ADD COLUMN validation_status VARCHAR(40) NOT NULL DEFAULT 'untested'",
    }
    conversion_job_columns = (
        {column["name"] for column in inspector.get_columns("file_conversion_job")} if "file_conversion_job" in table_names else set()
    )
    conversion_job_ddl = {
        "metadata_json": "ALTER TABLE file_conversion_job ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'",
    }
    user_account_columns = (
        {column["name"] for column in inspector.get_columns("user_account")} if "user_account" in table_names else set()
    )
    user_account_ddl = {
        "org_unit_code": "ALTER TABLE user_account ADD COLUMN org_unit_code VARCHAR(120)",
        "org_unit_name": "ALTER TABLE user_account ADD COLUMN org_unit_name VARCHAR(120)",
        "job_title": "ALTER TABLE user_account ADD COLUMN job_title VARCHAR(120)",
        "external_user_id": "ALTER TABLE user_account ADD COLUMN external_user_id VARCHAR(120)",
    }
    message_template_columns = (
        {column["name"] for column in inspector.get_columns("message_template")} if "message_template" in table_names else set()
    )
    message_template_ddl = {
        "recipient_group_id": "ALTER TABLE message_template ADD COLUMN recipient_group_id VARCHAR(64)",
        "escalation_group_id": "ALTER TABLE message_template ADD COLUMN escalation_group_id VARCHAR(64)",
    }
    benchmark_run_columns = (
        {column["name"] for column in inspector.get_columns("benchmark_run")} if "benchmark_run" in table_names else set()
    )
    benchmark_run_ddl = {
        "planning_mode": "ALTER TABLE benchmark_run ADD COLUMN planning_mode VARCHAR(40) NOT NULL DEFAULT 'single_sheet'",
        "hard_rule_pass": "ALTER TABLE benchmark_run ADD COLUMN hard_rule_pass BOOLEAN NOT NULL DEFAULT 0",
        "quantity_fulfillment_rate": (
            "ALTER TABLE benchmark_run ADD COLUMN quantity_fulfillment_rate FLOAT NOT NULL DEFAULT 0"
        ),
        "requested_units": "ALTER TABLE benchmark_run ADD COLUMN requested_units INTEGER NOT NULL DEFAULT 0",
        "produced_units": "ALTER TABLE benchmark_run ADD COLUMN produced_units INTEGER NOT NULL DEFAULT 0",
        "shortage_units": "ALTER TABLE benchmark_run ADD COLUMN shortage_units INTEGER NOT NULL DEFAULT 0",
        "overproduction_units": "ALTER TABLE benchmark_run ADD COLUMN overproduction_units INTEGER NOT NULL DEFAULT 0",
        "units_per_sheet": "ALTER TABLE benchmark_run ADD COLUMN units_per_sheet INTEGER NOT NULL DEFAULT 0",
        "sheets_used": "ALTER TABLE benchmark_run ADD COLUMN sheets_used INTEGER NOT NULL DEFAULT 0",
        "peak_rss_mb": "ALTER TABLE benchmark_run ADD COLUMN peak_rss_mb FLOAT",
        "export_ok": "ALTER TABLE benchmark_run ADD COLUMN export_ok BOOLEAN NOT NULL DEFAULT 0",
        "case_score": "ALTER TABLE benchmark_run ADD COLUMN case_score FLOAT NOT NULL DEFAULT 0",
        "baseline_delta_utilization_rate": "ALTER TABLE benchmark_run ADD COLUMN baseline_delta_utilization_rate FLOAT",
        "p95_runtime_ms": "ALTER TABLE benchmark_run ADD COLUMN p95_runtime_ms INTEGER",
        "metrics_json": "ALTER TABLE benchmark_run ADD COLUMN metrics_json JSON NOT NULL DEFAULT '{}'",
    }
    production_pattern_columns = (
        {column["name"] for column in inspector.get_columns("production_pattern")}
        if "production_pattern" in table_names
        else set()
    )
    production_pattern_ddl = {
        "placement_json": "ALTER TABLE production_pattern ADD COLUMN placement_json JSON NOT NULL DEFAULT '{}'",
        "placement_svg": "ALTER TABLE production_pattern ADD COLUMN placement_svg TEXT NOT NULL DEFAULT ''",
        "placement_checksum": "ALTER TABLE production_pattern ADD COLUMN placement_checksum VARCHAR(128)",
        "placement_solver_json": "ALTER TABLE production_pattern ADD COLUMN placement_solver_json JSON NOT NULL DEFAULT '{}'",
    }
    with engine.begin() as connection:
        for column_name, ddl in work_task_ddl.items():
            if "work_task" in table_names and column_name not in work_task_columns:
                connection.execute(text(ddl))
        for column_name, ddl in export_ddl.items():
            if "solution_export" in table_names and column_name not in export_columns:
                connection.execute(text(ddl))
        for column_name, ddl in adapter_config_ddl.items():
            if "adapter_config" in table_names and column_name not in adapter_config_columns:
                connection.execute(text(ddl))
        for column_name, ddl in conversion_job_ddl.items():
            if "file_conversion_job" in table_names and column_name not in conversion_job_columns:
                connection.execute(text(ddl))
        for column_name, ddl in user_account_ddl.items():
            if "user_account" in table_names and column_name not in user_account_columns:
                connection.execute(text(ddl))
        for column_name, ddl in message_template_ddl.items():
            if "message_template" in table_names and column_name not in message_template_columns:
                connection.execute(text(ddl))
        for column_name, ddl in benchmark_run_ddl.items():
            if "benchmark_run" in table_names and column_name not in benchmark_run_columns:
                connection.execute(text(ddl))
        for column_name, ddl in production_pattern_ddl.items():
            if "production_pattern" in table_names and column_name not in production_pattern_columns:
                connection.execute(text(ddl))
        if "operation_log" in table_names:
            existing_indexes = {item["name"] for item in inspector.get_indexes("operation_log")}
            operation_log_indexes = {
                "ix_operation_log_created_at": "CREATE INDEX ix_operation_log_created_at ON operation_log (created_at)",
                "ix_operation_log_action_created_at": (
                    "CREATE INDEX ix_operation_log_action_created_at ON operation_log (action, created_at)"
                ),
                "ix_operation_log_target_created_at": (
                    "CREATE INDEX ix_operation_log_target_created_at ON operation_log (target_type, target_id, created_at)"
                ),
                "ix_operation_log_actor_created_at": (
                    "CREATE INDEX ix_operation_log_actor_created_at ON operation_log (actor_id, created_at)"
                ),
            }
            for index_name, ddl in operation_log_indexes.items():
                if index_name not in existing_indexes:
                    connection.execute(text(ddl))
