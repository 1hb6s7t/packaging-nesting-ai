from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT_PATH = REPO_ROOT / "scripts" / "production_env_audit.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_production_env_audit.py"
PRODUCTION_DATABASE_URL = "postgresql+psycopg://app:StrongDbPassword123!@db:5432/packaging"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_production_env_audit_accepts_generated_report_with_env_file(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit", VERIFY_SCRIPT_PATH)
    paths = write_production_env_outputs(audit_module, tmp_path)

    verification = verify_module.verify_production_env_audit(paths["report"], env_file=paths["env"])

    assert verification["status"] == "passed"
    assert verification["report_status"] == "passed"
    assert verification["summary"]["rebuilt_report_match"] is True
    assert verification["summary"]["policy_contract_status"] == "passed"
    assert verification["errors"] == []


def test_verify_production_env_audit_rejects_summary_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_summary_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_summary", VERIFY_SCRIPT_PATH)
    paths = write_production_env_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["summary"]["error_count"] = 1
    write_json(paths["report"], report)

    verification = verify_module.verify_production_env_audit(paths["report"], env_file=paths["env"])

    assert verification["status"] == "failed"
    assert any("summary.error_count must be 0" in error for error in verification["errors"])
    assert "production env audit report does not match rebuilt audit from env_file" in verification["errors"]


def test_verify_production_env_audit_rejects_policy_contract_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_policy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_policy", VERIFY_SCRIPT_PATH)
    paths = write_production_env_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["policy_contract"]["status"] = "failed"
    report["policy_contract"]["failed_count"] = 1
    write_json(paths["report"], report)

    verification = verify_module.verify_production_env_audit(paths["report"], env_file=paths["env"])

    assert verification["status"] == "failed"
    assert "production env audit summary.policy_contract_status does not match policy_contract status" in verification["errors"]
    assert "production env audit report does not match rebuilt audit from env_file" in verification["errors"]


def test_verify_production_env_audit_rejects_unredacted_settings(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_redaction_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_redaction", VERIFY_SCRIPT_PATH)
    paths = write_production_env_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["redacted_settings"]["AUTH_SECRET_KEY"] = "prod-secret-key-with-at-least-32-chars"
    write_json(paths["report"], report)

    verification = verify_module.verify_production_env_audit(paths["report"], env_file=paths["env"])

    assert verification["status"] == "failed"
    assert "production env audit redacted_settings must redact sensitive values and URL secrets" in verification["errors"]
    assert "production env audit report does not match rebuilt audit from env_file" in verification["errors"]


def test_verify_production_env_audit_rejects_env_file_drift(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_drift_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_drift", VERIFY_SCRIPT_PATH)
    paths = write_production_env_outputs(audit_module, tmp_path)
    env_text = paths["env"].read_text(encoding="utf-8").replace("SECURITY_HSTS_ENABLED=true\n", "")
    paths["env"].write_text(env_text, encoding="utf-8")

    verification = verify_module.verify_production_env_audit(paths["report"], env_file=paths["env"])

    assert verification["status"] == "failed"
    assert "production env audit report does not match rebuilt audit from env_file" in verification["errors"]
    assert any("rebuilt mismatch for status" in error for error in verification["errors"])


def test_verify_production_env_audit_can_allow_matching_failed_report(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_failed_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_failed", VERIFY_SCRIPT_PATH)
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    report = audit_module.build_env_audit_report(env_file)
    report_path = tmp_path / "production-env-audit.json"
    write_json(report_path, report)

    failed = verify_module.verify_production_env_audit(report_path, env_file=env_file)
    allowed = verify_module.verify_production_env_audit(
        report_path,
        env_file=env_file,
        require_passed_report=False,
    )

    assert failed["status"] == "failed"
    assert "production env audit status must be passed, got failed" in failed["errors"]
    assert allowed["status"] == "passed"
    assert allowed["report_status"] == "failed"
    assert allowed["summary"]["rebuilt_report_match"] is True


def test_verify_production_env_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    audit_module = load_module("production_env_audit_for_cli_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_production_env_audit_for_cli", VERIFY_SCRIPT_PATH)
    env_file = tmp_path / ".env.production"
    env_file.write_text("APP_ENV=production\n", encoding="utf-8")
    report_path = tmp_path / "production-env-audit.json"
    output_path = tmp_path / "production-env-verification.json"
    write_json(report_path, audit_module.build_env_audit_report(env_file))

    exit_code = verify_module.main(
        ["--report", str(report_path), "--env-file", str(env_file), "--output", str(output_path)]
    )

    verification = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert verification["status"] == "failed"
    assert "production env audit status must be passed, got failed" in verification["errors"]


def write_production_env_outputs(module, tmp_path: Path) -> dict[str, Path]:
    env_file = tmp_path / ".env.production"
    report_path = tmp_path / "production-env-audit.json"
    write_valid_env(env_file)
    write_json(report_path, module.build_env_audit_report(env_file))
    return {"env": env_file, "report": report_path}


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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
