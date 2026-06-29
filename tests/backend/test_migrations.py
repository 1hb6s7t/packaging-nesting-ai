from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import (
    alembic_head_state,
    assert_alembic_head_ready,
    assert_metadata_schema_ready,
    expected_alembic_heads,
    metadata_schema_gaps,
    requires_explicit_migrations,
)


def test_alembic_upgrade_head_matches_model_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "migration-check.sqlite"
    database_url = f"sqlite:///{db_path.as_posix()}"
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "PYTHONPATH": str(BACKEND_DIR),
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        db_tables = set(inspector.get_table_names()) - {"alembic_version"}
        model_tables = set(Base.metadata.tables)
        assert sorted(model_tables - db_tables) == []
        assert sorted(db_tables - model_tables) == []

        mismatches: dict[str, dict[str, list[str]]] = {}
        for table_name, table in Base.metadata.tables.items():
            db_columns = {column["name"] for column in inspector.get_columns(table_name)}
            model_columns = set(table.columns.keys())
            missing_columns = sorted(model_columns - db_columns)
            extra_columns = sorted(db_columns - model_columns)
            if missing_columns or extra_columns:
                mismatches[table_name] = {"missing": missing_columns, "extra": extra_columns}
        assert mismatches == {}
        operation_log_indexes = {item["name"] for item in inspector.get_indexes("operation_log")}
        assert {
            "ix_operation_log_created_at",
            "ix_operation_log_action_created_at",
            "ix_operation_log_target_created_at",
            "ix_operation_log_actor_created_at",
        }.issubset(operation_log_indexes)
        assert_metadata_schema_ready(engine)
        expected_heads = sorted(expected_alembic_heads())
        migration_state = alembic_head_state(engine)
        assert migration_state["expected_heads"] == expected_heads
        assert migration_state["database_heads"] == expected_heads
        assert migration_state["missing_heads"] == []
        assert migration_state["unexpected_heads"] == []
        assert_alembic_head_ready(engine)
    finally:
        engine.dispose()


def test_production_startup_requires_migrated_schema(tmp_path: Path) -> None:
    assert requires_explicit_migrations("production") is True
    assert requires_explicit_migrations("development") is False
    engine = create_engine(f"sqlite:///{(tmp_path / 'empty.sqlite').as_posix()}")
    try:
        missing_tables, missing_columns = metadata_schema_gaps(engine)
        assert missing_tables == sorted(Base.metadata.tables)
        assert missing_columns == {}
        with pytest.raises(RuntimeError, match="alembic"):
            assert_metadata_schema_ready(engine)
    finally:
        engine.dispose()


def test_production_startup_rejects_schema_without_alembic_head(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'manual-schema.sqlite').as_posix()}")
    try:
        Base.metadata.create_all(bind=engine)
        assert_metadata_schema_ready(engine)
        expected_heads = sorted(expected_alembic_heads())
        migration_state = alembic_head_state(engine)
        assert migration_state["database_heads"] == []
        assert migration_state["missing_heads"] == expected_heads
        with pytest.raises(RuntimeError, match="Alembic migration state is not at repository head"):
            assert_alembic_head_ready(engine)
    finally:
        engine.dispose()
