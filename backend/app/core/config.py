from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEVELOPMENT_AUTH_SECRET_KEY = "dev-only-change-me"
DEVELOPMENT_POSTGRES_PASSWORD = "packaging"
DEVELOPMENT_POSTGRES_USER = "packaging"
DEVELOPMENT_DEFAULT_ADMIN_EMAIL = "admin@example.com"
DEVELOPMENT_DEFAULT_ADMIN_PASSWORD = "Admin123!"
DEVELOPMENT_MINIO_ACCESS_KEY = "minioadmin"
DEVELOPMENT_MINIO_SECRET_KEY = "minioadmin"
DEVELOPMENT_REDIS_URL = "redis://localhost:6379/0"
DEVELOPMENT_STORAGE_ROOT = Path("storage")
PRODUCTION_ENVIRONMENTS = {"prod", "production"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Packaging Nesting Optimization Platform"
    api_prefix: str = "/api"
    environment: str = Field(default="development", alias="APP_ENV")
    database_url: str = Field(default="sqlite:///./dev.db", alias="DATABASE_URL")
    redis_url: str = Field(default=DEVELOPMENT_REDIS_URL, alias="REDIS_URL")
    minio_endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    minio_bucket: str = Field(default="packaging-nesting", alias="MINIO_BUCKET")
    minio_access_key: str = Field(default=DEVELOPMENT_MINIO_ACCESS_KEY, alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default=DEVELOPMENT_MINIO_SECRET_KEY, alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, alias="MINIO_SECURE")
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    storage_root: Path = Field(default=Path("storage"), alias="STORAGE_ROOT")
    solver_time_limit_sec: int = 120
    default_top_k: int = 5
    auth_secret_key: str = Field(default=DEVELOPMENT_AUTH_SECRET_KEY, alias="AUTH_SECRET_KEY")
    access_token_ttl_minutes: int = Field(default=480, alias="ACCESS_TOKEN_TTL_MINUTES")
    default_admin_email: str = Field(default=DEVELOPMENT_DEFAULT_ADMIN_EMAIL, alias="DEFAULT_ADMIN_EMAIL")
    default_admin_password: str = Field(default=DEVELOPMENT_DEFAULT_ADMIN_PASSWORD, alias="DEFAULT_ADMIN_PASSWORD")
    login_rate_limit_max_failures: int = Field(default=5, ge=1, alias="LOGIN_RATE_LIMIT_MAX_FAILURES")
    login_rate_limit_window_sec: int = Field(default=300, ge=1, alias="LOGIN_RATE_LIMIT_WINDOW_SEC")
    task_execution_backend: str = Field(default="background", alias="TASK_EXECUTION_BACKEND")
    task_stale_after_sec: int = Field(default=300, alias="TASK_STALE_AFTER_SEC")
    task_soft_time_limit_sec: int = Field(default=1700, alias="TASK_SOFT_TIME_LIMIT_SEC")
    task_hard_time_limit_sec: int = Field(default=1800, alias="TASK_HARD_TIME_LIMIT_SEC")
    task_worker_prefetch_multiplier: int = Field(default=1, alias="TASK_WORKER_PREFETCH_MULTIPLIER")
    task_alert_active_threshold: int = Field(default=50, alias="TASK_ALERT_ACTIVE_THRESHOLD")
    task_alert_queued_threshold: int = Field(default=30, alias="TASK_ALERT_QUEUED_THRESHOLD")
    task_alert_stale_running_threshold: int = Field(default=1, alias="TASK_ALERT_STALE_RUNNING_THRESHOLD")
    task_alert_failure_threshold: int = Field(default=10, alias="TASK_ALERT_FAILURE_THRESHOLD")
    task_alert_dedupe_minutes: int = Field(default=30, alias="TASK_ALERT_DEDUPE_MINUTES")
    external_alert_webhook_url: str | None = Field(default=None, alias="EXTERNAL_ALERT_WEBHOOK_URL")
    external_alert_webhook_timeout_sec: int = Field(default=5, alias="EXTERNAL_ALERT_WEBHOOK_TIMEOUT_SEC")
    smtp_host: str | None = Field(default=None, alias="SMTP_HOST")
    smtp_port: int = Field(default=587, ge=1, le=65535, alias="SMTP_PORT")
    smtp_username: str | None = Field(default=None, alias="SMTP_USERNAME")
    smtp_password: str | None = Field(default=None, alias="SMTP_PASSWORD")
    smtp_from_email: str | None = Field(default=None, alias="SMTP_FROM_EMAIL")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    smtp_timeout_sec: int = Field(default=5, ge=1, alias="SMTP_TIMEOUT_SEC")
    external_conversion_service_url: str | None = Field(default=None, alias="EXTERNAL_CONVERSION_SERVICE_URL")
    external_conversion_service_api_key: str | None = Field(default=None, alias="EXTERNAL_CONVERSION_SERVICE_API_KEY")
    external_conversion_timeout_sec: int = Field(default=30, alias="EXTERNAL_CONVERSION_TIMEOUT_SEC")
    external_conversion_sla_minutes: int = Field(default=120, alias="EXTERNAL_CONVERSION_SLA_MINUTES")
    benchmark_task_timeout_sec: int = Field(default=300, alias="BENCHMARK_TASK_TIMEOUT_SEC")
    export_retention_days: int = Field(default=365, alias="EXPORT_RETENTION_DAYS")
    maintenance_scheduler_enabled: bool = Field(default=False, alias="MAINTENANCE_SCHEDULER_ENABLED")
    maintenance_interval_minutes: int = Field(default=60, alias="MAINTENANCE_INTERVAL_MINUTES")
    maintenance_archive_expired_exports: bool = Field(default=True, alias="MAINTENANCE_ARCHIVE_EXPIRED_EXPORTS")
    maintenance_conversion_sla_check: bool = Field(default=True, alias="MAINTENANCE_CONVERSION_SLA_CHECK")
    maintenance_task_alert_check: bool = Field(default=True, alias="MAINTENANCE_TASK_ALERT_CHECK")
    security_headers_enabled: bool = Field(default=True, alias="SECURITY_HEADERS_ENABLED")
    security_hsts_enabled: bool = Field(default=False, alias="SECURITY_HSTS_ENABLED")
    security_hsts_max_age_sec: int = Field(default=31536000, ge=0, alias="SECURITY_HSTS_MAX_AGE_SEC")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_production_environment(environment: str) -> bool:
    return environment.strip().lower() in PRODUCTION_ENVIRONMENTS


def production_security_setting_errors(settings: Settings) -> list[str]:
    if not is_production_environment(settings.environment):
        return []
    errors: list[str] = []
    raw_database_url = settings.database_url.strip()
    database_url = raw_database_url.lower()
    if database_url.startswith("sqlite"):
        errors.append("DATABASE_URL must not use SQLite in production")
    if database_url.startswith("postgresql"):
        db_parts = urlsplit(raw_database_url)
        db_user = unquote(db_parts.username or "")
        db_password = unquote(db_parts.password or "")
        if not db_password:
            errors.append("DATABASE_URL must include a database password in production")
        else:
            if db_user == DEVELOPMENT_POSTGRES_USER and db_password == DEVELOPMENT_POSTGRES_PASSWORD:
                errors.append("DATABASE_URL must not use the Docker demo database credentials in production")
            if len(db_password) < 12:
                errors.append("DATABASE_URL database password must be at least 12 characters in production")
    auth_secret = settings.auth_secret_key.strip()
    if auth_secret == DEVELOPMENT_AUTH_SECRET_KEY:
        errors.append("AUTH_SECRET_KEY must not use the development default in production")
    if len(auth_secret) < 32:
        errors.append("AUTH_SECRET_KEY must be at least 32 characters in production")
    if settings.default_admin_email.strip().lower() == DEVELOPMENT_DEFAULT_ADMIN_EMAIL:
        errors.append("DEFAULT_ADMIN_EMAIL must not use the development default in production")
    if settings.default_admin_password == DEVELOPMENT_DEFAULT_ADMIN_PASSWORD:
        errors.append("DEFAULT_ADMIN_PASSWORD must not use the development default in production")
    if len(settings.default_admin_password) < 12:
        errors.append("DEFAULT_ADMIN_PASSWORD must be at least 12 characters in production")
    storage_backend = settings.storage_backend.strip().lower()
    if storage_backend not in {"local", "minio"}:
        errors.append("STORAGE_BACKEND must be either local or minio in production")
    if storage_backend == "local":
        if settings.storage_root == DEVELOPMENT_STORAGE_ROOT or not settings.storage_root.is_absolute():
            errors.append("STORAGE_ROOT must be an absolute NAS or durable volume path in production local mode")
    if storage_backend == "minio":
        if settings.minio_access_key.strip() == DEVELOPMENT_MINIO_ACCESS_KEY:
            errors.append("MINIO_ACCESS_KEY must not use the development default in production")
        if settings.minio_secret_key == DEVELOPMENT_MINIO_SECRET_KEY:
            errors.append("MINIO_SECRET_KEY must not use the development default in production")
        if len(settings.minio_secret_key) < 12:
            errors.append("MINIO_SECRET_KEY must be at least 12 characters in production")
    if any(origin.strip() == "*" for origin in settings.cors_origins):
        errors.append("CORS_ORIGINS must not include '*' in production")
    if not settings.security_headers_enabled:
        errors.append("SECURITY_HEADERS_ENABLED must not be disabled in production")
    if settings.task_execution_backend.strip().lower() != "celery":
        errors.append("TASK_EXECUTION_BACKEND must be celery in production")
    redis_url = settings.redis_url.strip().lower()
    if redis_url == DEVELOPMENT_REDIS_URL:
        errors.append("REDIS_URL must not use the development default in production")
    if not (redis_url.startswith("redis://") or redis_url.startswith("rediss://")):
        errors.append("REDIS_URL must use redis:// or rediss:// in production")
    smtp_host = (settings.smtp_host or "").strip()
    smtp_from_email = (settings.smtp_from_email or "").strip()
    smtp_username = (settings.smtp_username or "").strip()
    smtp_password = settings.smtp_password or ""
    smtp_configured = bool(smtp_host or smtp_from_email or smtp_username or smtp_password)
    if smtp_configured:
        if not smtp_host:
            errors.append("SMTP_HOST must be set when SMTP notification delivery is configured")
        if not smtp_from_email:
            errors.append("SMTP_FROM_EMAIL must be set when SMTP notification delivery is configured")
        if smtp_username and not smtp_password:
            errors.append("SMTP_PASSWORD must be set when SMTP_USERNAME is configured")
        if smtp_password and not smtp_username:
            errors.append("SMTP_USERNAME must be set when SMTP_PASSWORD is configured")
        if smtp_password and len(smtp_password) < 12:
            errors.append("SMTP_PASSWORD must be at least 12 characters in production")
    return errors


def assert_production_security_settings(settings: Settings) -> None:
    errors = production_security_setting_errors(settings)
    if errors:
        raise RuntimeError("Production security settings are unsafe: " + "; ".join(errors))
