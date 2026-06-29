import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app import main as main_module
from app.core.config import Settings, assert_production_security_settings, production_security_setting_errors


PRODUCTION_DATABASE_URL = "postgresql+psycopg://app:StrongDbPassword123!@db:5432/packaging"


def test_development_security_defaults_are_allowed() -> None:
    settings = Settings()
    assert production_security_setting_errors(settings) == []
    assert settings.login_rate_limit_max_failures == 5
    assert settings.login_rate_limit_window_sec == 300


def test_production_rejects_development_security_defaults() -> None:
    settings = Settings(APP_ENV="production")
    errors = production_security_setting_errors(settings)
    assert any("AUTH_SECRET_KEY" in error and "development default" in error for error in errors)
    assert any("AUTH_SECRET_KEY" in error and "32 characters" in error for error in errors)
    assert any("DEFAULT_ADMIN_EMAIL" in error for error in errors)
    assert any("DEFAULT_ADMIN_PASSWORD" in error and "development default" in error for error in errors)
    assert any("DEFAULT_ADMIN_PASSWORD" in error and "12 characters" in error for error in errors)
    with pytest.raises(RuntimeError, match="Production security settings are unsafe"):
        assert_production_security_settings(settings)


def test_production_accepts_rotated_security_settings() -> None:
    settings = Settings(
        APP_ENV="prod",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    assert production_security_setting_errors(settings) == []
    assert_production_security_settings(settings)


def test_production_accepts_complete_smtp_notification_settings() -> None:
    settings = Settings(
        APP_ENV="prod",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        SMTP_HOST="smtp.example.com",
        SMTP_FROM_EMAIL="alerts@example.com",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="StrongSmtp123!",
    )
    assert production_security_setting_errors(settings) == []


def test_production_rejects_partial_smtp_notification_settings() -> None:
    settings = Settings(
        APP_ENV="prod",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="short",
    )
    errors = production_security_setting_errors(settings)
    assert "SMTP_HOST must be set when SMTP notification delivery is configured" in errors
    assert "SMTP_FROM_EMAIL must be set when SMTP notification delivery is configured" in errors
    assert "SMTP_PASSWORD must be at least 12 characters in production" in errors


def test_production_rejects_default_minio_credentials_when_minio_storage_is_enabled() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
    )
    errors = production_security_setting_errors(settings)
    assert any("MINIO_ACCESS_KEY" in error and "development default" in error for error in errors)
    assert any("MINIO_SECRET_KEY" in error and "development default" in error for error in errors)
    assert any("MINIO_SECRET_KEY" in error and "12 characters" in error for error in errors)


def test_production_rejects_wildcard_cors_origin() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        cors_origins=["*"],
    )
    assert production_security_setting_errors(settings) == ["CORS_ORIGINS must not include '*' in production"]


def test_production_rejects_disabled_security_headers() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        SECURITY_HEADERS_ENABLED=False,
    )
    assert production_security_setting_errors(settings) == [
        "SECURITY_HEADERS_ENABLED must not be disabled in production"
    ]


def test_production_rejects_sqlite_database_url() -> None:
    settings = Settings(
        APP_ENV="production",
        REDIS_URL="redis://redis:6379/0",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        TASK_EXECUTION_BACKEND="celery",
    )
    assert "DATABASE_URL must not use SQLite in production" in production_security_setting_errors(settings)


def test_production_rejects_docker_demo_database_credentials() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://packaging:packaging@postgres:5432/packaging_nesting",
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    errors = production_security_setting_errors(settings)
    assert "DATABASE_URL must not use the Docker demo database credentials in production" in errors
    assert "DATABASE_URL database password must be at least 12 characters in production" in errors


def test_production_rejects_missing_database_password() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://app@db:5432/packaging",
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    assert production_security_setting_errors(settings) == [
        "DATABASE_URL must include a database password in production"
    ]


def test_production_rejects_short_database_password() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://app:secret@db:5432/packaging",
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    assert production_security_setting_errors(settings) == [
        "DATABASE_URL database password must be at least 12 characters in production"
    ]


def test_production_local_storage_requires_absolute_durable_path(tmp_path: Path) -> None:
    common = {
        "APP_ENV": "production",
        "DATABASE_URL": PRODUCTION_DATABASE_URL,
        "REDIS_URL": "redis://redis:6379/0",
        "AUTH_SECRET_KEY": "prod-secret-key-with-at-least-32-chars",
        "DEFAULT_ADMIN_EMAIL": "ops-admin@example.com",
        "DEFAULT_ADMIN_PASSWORD": "StrongProduction123!",
        "STORAGE_BACKEND": "local",
        "TASK_EXECUTION_BACKEND": "celery",
    }
    relative_settings = Settings(**common)
    assert (
        "STORAGE_ROOT must be an absolute NAS or durable volume path in production local mode"
        in production_security_setting_errors(relative_settings)
    )

    absolute_settings = Settings(**common, STORAGE_ROOT=tmp_path)
    assert production_security_setting_errors(absolute_settings) == []


def test_production_rejects_background_task_backend() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="redis://redis:6379/0",
        STORAGE_BACKEND="minio",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
        TASK_EXECUTION_BACKEND="background",
    )
    assert production_security_setting_errors(settings) == ["TASK_EXECUTION_BACKEND must be celery in production"]


def test_production_rejects_default_redis_url() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    assert production_security_setting_errors(settings) == [
        "REDIS_URL must not use the development default in production"
    ]


def test_production_rejects_non_redis_url() -> None:
    settings = Settings(
        APP_ENV="production",
        DATABASE_URL=PRODUCTION_DATABASE_URL,
        REDIS_URL="amqp://rabbitmq:5672",
        STORAGE_BACKEND="minio",
        TASK_EXECUTION_BACKEND="celery",
        AUTH_SECRET_KEY="prod-secret-key-with-at-least-32-chars",
        DEFAULT_ADMIN_EMAIL="ops-admin@example.com",
        DEFAULT_ADMIN_PASSWORD="StrongProduction123!",
        MINIO_ACCESS_KEY="prod-minio-access",
        MINIO_SECRET_KEY="prod-minio-secret-123",
    )
    assert production_security_setting_errors(settings) == [
        "REDIS_URL must use redis:// or rediss:// in production"
    ]


def test_create_app_fails_fast_with_unsafe_production_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module, "get_settings", lambda: Settings(APP_ENV="production"))
    with pytest.raises(RuntimeError, match="Production security settings are unsafe"):
        main_module.create_app()
