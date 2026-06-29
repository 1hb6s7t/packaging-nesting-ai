from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "deployment_compose_audit.py"


def load_deployment_compose_audit_module():
    spec = importlib.util.spec_from_file_location("deployment_compose_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_deployment_compose_audit_passes_default_local_compose_with_demo_warning() -> None:
    module = load_deployment_compose_audit_module()

    report = module.build_deployment_compose_audit_report()

    assert report["status"] == "passed"
    assert report["summary"]["service_count"] == 7
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["warning_count"] == 1
    assert report["summary"]["local_demo_value_count"] > 0
    assert report["policy_contract"]["status"] == "warning"
    assert report["policy_contract"]["failed_count"] == 0
    assert report["summary"]["policy_contract_status"] == "warning"
    assert report["summary"]["policy_contract_warning_count"] == 1
    assert any(
        check["code"] == "compose.local_demo_defaults"
        for check in report["policy_contract"]["warning_checks"]
    )
    by_name = {item["name"]: item for item in report["checks"]}
    assert by_name["compose.required_services"]["status"] == "passed"
    assert by_name["compose.local_demo_defaults"]["status"] == "warning"
    assert by_name["dockerfile.frontend.lockfile_install"]["status"] == "passed"
    assert by_name["dockerfile.backend.runtime"]["status"] == "passed"


def test_deployment_compose_audit_fails_demo_defaults_when_service_declares_production(tmp_path: Path) -> None:
    module = load_deployment_compose_audit_module()
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose["services"]["api"]["environment"]["APP_ENV"] = "production"
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    report = module.build_deployment_compose_audit_report(compose_file=compose_path)

    by_name = {item["name"]: item for item in report["checks"]}
    assert report["status"] == "failed"
    assert by_name["compose.local_demo_defaults"]["status"] == "failed"
    assert by_name["compose.local_demo_defaults"]["evidence"]["production_services"] == ["api"]
    assert report["policy_contract"]["status"] == "failed"
    assert any(
        check["code"] == "compose.local_demo_defaults"
        for check in report["policy_contract"]["failed_checks"]
    )


def test_deployment_compose_audit_fails_frontend_dockerfile_without_lockfile_install(tmp_path: Path) -> None:
    module = load_deployment_compose_audit_module()
    frontend_dockerfile = tmp_path / "Dockerfile"
    frontend_dockerfile.write_text(
        "\n".join(
            [
                "FROM node:24-alpine AS build",
                "WORKDIR /app",
                "COPY frontend/package.json /app/",
                "RUN npm install",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = module.build_deployment_compose_audit_report(frontend_dockerfile=frontend_dockerfile)

    by_name = {item["name"]: item for item in report["checks"]}
    assert report["status"] == "failed"
    assert by_name["dockerfile.frontend.lockfile_install"]["status"] == "failed"
    assert len(by_name["dockerfile.frontend.lockfile_install"]["evidence"]["failures"]) >= 2
    assert any(
        check["code"] == "dockerfile.frontend.lockfile"
        for check in report["policy_contract"]["failed_checks"]
    )


def test_deployment_compose_audit_requires_backend_optimization_extra(tmp_path: Path) -> None:
    module = load_deployment_compose_audit_module()
    backend_dockerfile = tmp_path / "Dockerfile"
    backend_dockerfile.write_text(
        "\n".join(
            [
                "FROM python:3.12-slim",
                "WORKDIR /app",
                "COPY backend /app",
                "RUN pip install --no-cache-dir . psycopg[binary]",
                'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = module.build_deployment_compose_audit_report(backend_dockerfile=backend_dockerfile)

    by_name = {item["name"]: item for item in report["checks"]}
    assert report["status"] == "failed"
    assert by_name["dockerfile.backend.runtime"]["status"] == "failed"
    assert "backend image must install the packaged app with optimization extra" in by_name[
        "dockerfile.backend.runtime"
    ]["evidence"]["failures"]
    assert any(
        check["code"] == "dockerfile.backend.runtime"
        for check in report["policy_contract"]["failed_checks"]
    )


def test_deployment_compose_audit_cli_writes_report_and_returns_nonzero_for_production_demo_defaults(
    tmp_path: Path,
) -> None:
    module = load_deployment_compose_audit_module()
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    compose["services"]["worker"]["environment"]["APP_ENV"] = "production"
    compose_path = tmp_path / "docker-compose.yml"
    output_path = tmp_path / "deployment-compose-audit.json"
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    exit_code = module.main(["--compose-file", str(compose_path), "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert "compose.local_demo_defaults" in written["summary"]["failed_checks"]
