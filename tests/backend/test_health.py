import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.db import models as dbm  # noqa: F401
from app.db.base import Base
from app.db.session import expected_alembic_heads
from app.main import app
from app.services.health import build_readiness_report


client = TestClient(app)


def test_readiness_endpoint_reports_database_schema_and_storage() -> None:
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["database"]["status"] == "ok"
    assert checks["schema"]["status"] == "ok"
    assert checks["storage"]["status"] == "ok"
    assert checks["storage"]["backend"] in {"local", "minio"}


def test_readiness_report_degrades_when_database_schema_is_missing(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'empty.sqlite').as_posix()}")
    SessionLocal = sessionmaker(bind=engine)
    try:
        with SessionLocal() as db:
            report = build_readiness_report(db)
    finally:
        engine.dispose()

    checks = {item["name"]: item for item in report["checks"]}
    assert report["status"] == "degraded"
    assert checks["database"]["status"] == "ok"
    assert checks["schema"]["status"] == "failed"
    assert "production_order" in checks["schema"]["missing_tables"]


def test_readiness_report_degrades_when_migration_head_is_missing(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'manual-schema.sqlite').as_posix()}")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    try:
        with SessionLocal() as db:
            report = build_readiness_report(db, require_migration_head=True)
    finally:
        engine.dispose()

    checks = {item["name"]: item for item in report["checks"]}
    assert report["status"] == "degraded"
    assert checks["database"]["status"] == "ok"
    assert checks["schema"]["status"] == "ok"
    assert checks["migration"]["status"] == "failed"
    assert checks["migration"]["database_heads"] == []
    assert checks["migration"]["missing_heads"] == sorted(expected_alembic_heads())
