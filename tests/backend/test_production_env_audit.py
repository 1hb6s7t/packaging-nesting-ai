from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "production_env_audit.py"
PRODUCTION_DATABASE_URL = "postgresql+psycopg://app:StrongDbPassword123!@db:5432/packaging"


def load_production_env_audit_module():
    spec = importlib.util.spec_from_file_location("production_env_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_valid_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                f"DATABASE_URL={PRODUCTION_DATABASE_URL}",
                "REDIS_URL=rediss://:StrongRedisPassword123!@redis.prod.internal:6379/0",
                "STORAGE_BACKEND=minio",
                "TASK_EXECUTION_BACKEND=celery",
                "AUTH_SECRET_KEY=prod-secret-key-with-at-least-32-chars",
                "DEFAULT_ADMIN_EMAIL=ops-admin@packaging-prod.internal",
                "DEFAULT_ADMIN_PASSWORD=StrongProduction123!",
                "MINIO_ENDPOINT=minio.prod.internal:9000",
                "MINIO_BUCKET=packaging-prod",
                "MINIO_ACCESS_KEY=prod-minio-access",
                "MINIO_SECRET_KEY=prod-minio-secret-123",
                "SECURITY_HEADERS_ENABLED=true",
                "SECURITY_HSTS_ENABLED=true",
                "EXTERNAL_ALERT_WEBHOOK_URL=https://hooks.prod.internal/services/very-secret-token",
                "SMTP_HOST=smtp.prod.internal",
                "SMTP_FROM_EMAIL=alerts@packaging-prod.internal",
                "SMTP_USERNAME=smtp-user",
                "SMTP_PASSWORD=StrongSmtp123!",
                "EXTERNAL_CONVERSION_SERVICE_URL=https://convert.prod.internal/jobs?api_key=secret&tenant=demo",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_env_parser_handles_comments_export_and_quotes(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text(
        """
# comment
export APP_ENV=production
DATABASE_URL="postgresql+psycopg://app:secret@db:5432/packaging" # comment
DEFAULT_ADMIN_EMAIL='ops-admin@example.com'
MINIO_BUCKET=packaging#prod
MINIO_ENDPOINT=minio:9000 # comment
""".strip(),
        encoding="utf-8",
    )

    result = module.parse_env_file(env_file)

    assert result.errors == []
    assert result.values["APP_ENV"] == "production"
    assert result.values["DATABASE_URL"] == "postgresql+psycopg://app:secret@db:5432/packaging"
    assert result.values["DEFAULT_ADMIN_EMAIL"] == "ops-admin@example.com"
    assert result.values["MINIO_BUCKET"] == "packaging#prod"
    assert result.values["MINIO_ENDPOINT"] == "minio:9000"


def test_env_parser_accepts_utf8_bom_files(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_bytes("\ufeffAPP_ENV=production\n".encode("utf-8"))

    result = module.parse_env_file(env_file)

    assert result.errors == []
    assert result.values["APP_ENV"] == "production"


def test_env_parser_reports_malformed_lines(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV\nBAD KEY=value\nAUTH_SECRET_KEY=\"unterminated\n", encoding="utf-8")

    result = module.parse_env_file(env_file)

    assert "line 1: expected KEY=value" in result.errors
    assert "line 2: invalid environment key 'BAD KEY'" in result.errors
    assert "line 3: unterminated quoted value" in result.errors


def test_env_parser_reports_duplicate_keys_case_insensitively(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\napp_env=development\n", encoding="utf-8")

    result = module.parse_env_file(env_file)

    assert "line 2: duplicate environment key 'app_env' (first defined on line 1)" in result.errors
    assert result.values["app_env"] == "development"

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "failed"
    assert any("duplicate environment key" in error for error in report["errors"])


def test_settings_builder_ignores_process_environment(monkeypatch) -> None:
    module = load_production_env_audit_module()
    monkeypatch.setenv("APP_ENV", "production")

    settings = module.build_settings_from_env_values({})

    assert settings.environment == "development"


def test_valid_production_env_passes_and_redacts_sensitive_values(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    write_valid_env(env_file)

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "passed"
    assert report["error_count"] == 0
    assert report["missing_recommended_keys"] == []
    assert report["redacted_settings"]["DATABASE_URL"] == (
        "postgresql+psycopg://app:***@db:5432/packaging"
    )
    assert report["redacted_settings"]["AUTH_SECRET_KEY"] == "***"
    assert report["redacted_settings"]["DEFAULT_ADMIN_PASSWORD"] == "***"
    assert report["redacted_settings"]["MINIO_SECRET_KEY"] == "***"
    assert report["redacted_settings"]["EXTERNAL_ALERT_WEBHOOK_URL"] == "***"
    assert report["redacted_settings"]["SMTP_PASSWORD"] == "***"
    assert report["redacted_settings"]["EXTERNAL_CONVERSION_SERVICE_URL"] == (
        "https://convert.prod.internal/jobs?api_key=***&tenant=demo"
    )


def test_production_env_example_has_required_keys_but_fails_until_placeholders_are_replaced() -> None:
    module = load_production_env_audit_module()
    env_file = REPO_ROOT / ".env.production.example"

    parse_result = module.parse_env_file(env_file)
    report = module.build_env_audit_report(env_file)

    assert parse_result.errors == []
    assert report["is_production"] is True
    assert report["missing_recommended_keys"] == []
    assert report["status"] == "failed"
    assert any("DATABASE_URL contains a placeholder value" in error for error in report["errors"])
    assert any("AUTH_SECRET_KEY contains a placeholder value" in error for error in report["errors"])
    assert any("MINIO_SECRET_KEY contains a placeholder value" in error for error in report["errors"])
    assert "packaging:packaging" not in env_file.read_text(encoding="utf-8")
    assert "minioadmin" not in env_file.read_text(encoding="utf-8")
    assert "Admin123!" not in env_file.read_text(encoding="utf-8")


def test_production_env_draft_generates_app_secrets_but_keeps_external_placeholders(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    draft_path = tmp_path / ".env.production"
    report_path = tmp_path / "production-env-draft-report.json"

    exit_code = module.main(["--write-draft", str(draft_path), "--output", str(report_path)])

    assert exit_code == 0
    assert draft_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    parse_result = module.parse_env_file(draft_path)
    assert parse_result.errors == []
    assert report["status"] == "pending"
    assert report["generated_secret_keys"] == ["AUTH_SECRET_KEY", "DEFAULT_ADMIN_PASSWORD"]
    assert parse_result.values["AUTH_SECRET_KEY"] != "<REPLACE_WITH_32_PLUS_RANDOM_CHARS>"
    assert len(parse_result.values["AUTH_SECRET_KEY"]) >= 32
    assert parse_result.values["DEFAULT_ADMIN_PASSWORD"] != "<REPLACE_WITH_INITIAL_ADMIN_PASSWORD_12_PLUS>"
    assert len(parse_result.values["DEFAULT_ADMIN_PASSWORD"]) >= 12
    assert "DATABASE_URL" in report["placeholder_keys"]
    assert "DEFAULT_ADMIN_EMAIL" in report["placeholder_keys"]
    assert "MINIO_ENDPOINT" in report["placeholder_keys"]
    assert "MINIO_SECRET_KEY" in report["placeholder_keys"]
    assert "CORS_ORIGINS" in report["placeholder_keys"]
    assert "AUTH_SECRET_KEY" not in report["placeholder_keys"]
    assert "DEFAULT_ADMIN_PASSWORD" not in report["placeholder_keys"]
    assert parse_result.values["AUTH_SECRET_KEY"] not in json.dumps(report, ensure_ascii=False)
    assert parse_result.values["DEFAULT_ADMIN_PASSWORD"] not in json.dumps(report, ensure_ascii=False)


def test_production_env_audit_rejects_placeholder_values(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    write_valid_env(env_file)
    text = env_file.read_text(encoding="utf-8").replace(
        "prod-secret-key-with-at-least-32-chars",
        "<REPLACE_WITH_32_PLUS_RANDOM_CHARS>",
    )
    env_file.write_text(text, encoding="utf-8")

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "failed"
    assert "AUTH_SECRET_KEY contains a placeholder value and must be replaced before production audit" in report["errors"]


def test_production_env_audit_rejects_example_template_domains(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    write_valid_env(env_file)
    text = env_file.read_text(encoding="utf-8").replace(
        PRODUCTION_DATABASE_URL,
        "postgresql+psycopg://app:StrongDbPassword123!@postgres.example.internal:5432/packaging",
    )
    text = text.replace("ops-admin@packaging-prod.internal", "ops-admin@customer.example")
    text = text.replace("minio.prod.internal:9000", "minio.example.internal:9000")
    env_file.write_text(text, encoding="utf-8")

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "failed"
    assert "DATABASE_URL contains an example/template domain and must be replaced before production audit" in report["errors"]
    assert "DEFAULT_ADMIN_EMAIL contains an example/template domain and must be replaced before production audit" in report["errors"]
    assert "MINIO_ENDPOINT contains an example/template domain and must be replaced before production audit" in report["errors"]


def test_unsafe_production_env_fails_with_existing_policy_errors(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "failed"
    assert "DATABASE_URL must not use SQLite in production" in report["errors"]
    assert "AUTH_SECRET_KEY must not use the development default in production" in report["errors"]
    assert "DEFAULT_ADMIN_PASSWORD must not use the development default in production" in report["errors"]
    assert "TASK_EXECUTION_BACKEND must be celery in production" in report["errors"]
    assert "REDIS_URL must not use the development default in production" in report["errors"]


def test_non_production_env_file_fails_audit(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=development\n", encoding="utf-8")

    report = module.build_env_audit_report(env_file)

    assert report["status"] == "failed"
    assert "APP_ENV must be prod or production for production environment audit" in report["errors"]


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_production_env_audit_module()
    env_file = tmp_path / ".env.production"
    output_path = tmp_path / "audit.json"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")

    exit_code = module.main(["--env-file", str(env_file), "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["error_count"] > 0
