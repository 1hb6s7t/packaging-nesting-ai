from pathlib import Path

import pytest


yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _compose_services() -> dict:
    compose_path = REPO_ROOT / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))["services"]


def _dockerfile_lines(relative_path: str) -> list[str]:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines()


def test_compose_core_dependencies_have_healthchecks() -> None:
    services = _compose_services()

    assert "healthcheck" in services["postgres"]
    assert "pg_isready" in " ".join(services["postgres"]["healthcheck"]["test"])
    assert "healthcheck" in services["redis"]
    assert services["redis"]["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert "healthcheck" in services["api"]
    api_healthcheck = " ".join(services["api"]["healthcheck"]["test"])
    assert "/api/health/ready" in api_healthcheck


def test_compose_runtime_services_wait_for_healthy_api_and_redis() -> None:
    services = _compose_services()

    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["minio"]["condition"] == "service_started"
    assert services["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["scheduler"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["scheduler"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["frontend"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_frontend_dockerfile_uses_lockfile_install() -> None:
    lines = _dockerfile_lines("frontend/Dockerfile")
    dockerfile = "\n".join(lines)

    assert "frontend/package-lock.json" in dockerfile
    assert "RUN npm ci" in dockerfile
    assert "RUN npm install" not in dockerfile
    assert lines.index("COPY frontend/package.json frontend/package-lock.json /app/") < lines.index("RUN npm ci")
