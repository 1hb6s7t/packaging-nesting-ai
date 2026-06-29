from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DEFAULT_BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
DEFAULT_FRONTEND_DOCKERFILE = REPO_ROOT / "frontend" / "Dockerfile"

REQUIRED_SERVICES = ("postgres", "redis", "minio", "api", "worker", "scheduler", "frontend")
BACKEND_RUNTIME_SERVICES = ("api", "worker", "scheduler")
BACKEND_REQUIRED_ENV_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "STORAGE_BACKEND",
    "TASK_EXECUTION_BACKEND",
)
PRODUCTION_ENV_VALUES = {"prod", "production"}


def build_deployment_compose_audit_report(
    *,
    compose_file: Path = DEFAULT_COMPOSE_PATH,
    backend_dockerfile: Path = DEFAULT_BACKEND_DOCKERFILE,
    frontend_dockerfile: Path = DEFAULT_FRONTEND_DOCKERFILE,
) -> dict[str, Any]:
    resolved_compose = resolve_repo_path(compose_file)
    resolved_backend_dockerfile = resolve_repo_path(backend_dockerfile)
    resolved_frontend_dockerfile = resolve_repo_path(frontend_dockerfile)
    checks: list[dict[str, Any]] = []

    compose, services = load_compose_services(resolved_compose, checks)
    if services is not None:
        check_required_services(services, checks)
        check_core_healthchecks(services, checks)
        check_service_dependencies(services, checks)
        check_external_image_tags(services, checks)
        check_backend_runtime_environment(services, checks)
        check_local_demo_defaults(services, checks)

    check_backend_dockerfile(resolved_backend_dockerfile, checks)
    check_frontend_dockerfile(resolved_frontend_dockerfile, checks)

    policy_contract = validate_deployment_compose_policy_contract(checks, services)
    summary = build_summary(checks, services, policy_contract)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if summary["error_count"] == 0 and summary["policy_contract_failed_count"] == 0 else "failed",
        "compose_path": str(resolved_compose),
        "backend_dockerfile": str(resolved_backend_dockerfile),
        "frontend_dockerfile": str(resolved_frontend_dockerfile),
        "summary": summary,
        "policy_contract": policy_contract,
        "checks": checks,
        "compose_metadata": {
            "top_level_keys": sorted(compose.keys()) if isinstance(compose, dict) else [],
            "service_names": sorted(services.keys()) if isinstance(services, dict) else [],
        },
    }


def load_compose_services(
    compose_file: Path,
    checks: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        import yaml
    except Exception as exc:
        add_check(
            checks,
            "compose.parser_available",
            False,
            severity="error",
            message=f"PyYAML is required to parse {compose_file}: {exc}",
        )
        return None, None

    if not compose_file.is_file():
        add_check(
            checks,
            "compose.file_exists",
            False,
            severity="error",
            message=f"compose file does not exist: {compose_file}",
        )
        return None, None

    try:
        payload = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except Exception as exc:
        add_check(
            checks,
            "compose.parses",
            False,
            severity="error",
            message=f"compose file could not be parsed: {exc}",
        )
        return None, None

    if not isinstance(payload, dict):
        add_check(checks, "compose.parses", False, severity="error", message="compose file must contain a mapping")
        return None, None
    services = payload.get("services")
    if not isinstance(services, dict):
        add_check(checks, "compose.services", False, severity="error", message="compose services must be a mapping")
        return payload, None

    add_check(
        checks,
        "compose.parses",
        True,
        severity="error",
        message="compose file parsed successfully",
        evidence={"service_count": len(services)},
    )
    return payload, services


def check_required_services(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    missing = [name for name in REQUIRED_SERVICES if name not in services]
    add_check(
        checks,
        "compose.required_services",
        not missing,
        severity="error",
        message="required compose services are present" if not missing else "required compose services are missing",
        evidence={"required_services": list(REQUIRED_SERVICES), "missing_services": missing},
    )


def check_core_healthchecks(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    postgres_healthcheck = healthcheck_test(services.get("postgres"))
    add_check(
        checks,
        "compose.healthcheck.postgres",
        "pg_isready" in flattened_text(postgres_healthcheck),
        severity="error",
        message="postgres healthcheck uses pg_isready",
        evidence={"healthcheck_test": postgres_healthcheck},
    )

    redis_healthcheck = healthcheck_test(services.get("redis"))
    add_check(
        checks,
        "compose.healthcheck.redis",
        redis_healthcheck == ["CMD", "redis-cli", "ping"],
        severity="error",
        message="redis healthcheck uses redis-cli ping",
        evidence={"healthcheck_test": redis_healthcheck},
    )

    api_healthcheck = healthcheck_test(services.get("api"))
    add_check(
        checks,
        "compose.healthcheck.api",
        "/api/health/ready" in flattened_text(api_healthcheck),
        severity="error",
        message="api healthcheck probes /api/health/ready",
        evidence={"healthcheck_test": api_healthcheck},
    )


def check_service_dependencies(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    expected = {
        "api": {"postgres": "service_healthy", "redis": "service_healthy", "minio": "service_started"},
        "worker": {"api": "service_healthy", "redis": "service_healthy"},
        "scheduler": {"api": "service_healthy", "redis": "service_healthy"},
        "frontend": {"api": "service_healthy"},
    }
    for service_name, dependencies in expected.items():
        mismatches: list[dict[str, str | None]] = []
        for dependency_name, expected_condition in dependencies.items():
            actual_condition = dependency_condition(services.get(service_name), dependency_name)
            if actual_condition != expected_condition:
                mismatches.append(
                    {
                        "dependency": dependency_name,
                        "expected_condition": expected_condition,
                        "actual_condition": actual_condition,
                    }
                )
        add_check(
            checks,
            f"compose.depends_on.{service_name}",
            not mismatches,
            severity="error",
            message=f"{service_name} waits for required dependencies",
            evidence={"mismatches": mismatches},
        )


def check_external_image_tags(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    for service_name in ("postgres", "redis", "minio"):
        service = service_mapping(services.get(service_name))
        image = service.get("image")
        has_tag = isinstance(image, str) and image_has_explicit_non_latest_tag(image)
        add_check(
            checks,
            f"compose.image_tag.{service_name}",
            has_tag,
            severity="error",
            message=f"{service_name} image uses an explicit non-latest tag",
            evidence={"image": image if isinstance(image, str) else None},
        )


def check_backend_runtime_environment(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    for service_name in BACKEND_RUNTIME_SERVICES:
        env = service_environment(services.get(service_name))
        missing = [key for key in BACKEND_REQUIRED_ENV_KEYS if key not in env]
        mismatched = []
        if env.get("STORAGE_BACKEND") != "minio":
            mismatched.append("STORAGE_BACKEND")
        if env.get("TASK_EXECUTION_BACKEND") != "celery":
            mismatched.append("TASK_EXECUTION_BACKEND")
        add_check(
            checks,
            f"compose.backend_environment.{service_name}",
            not missing and not mismatched,
            severity="error",
            message=f"{service_name} uses MinIO storage and Celery task runtime",
            evidence={
                "missing_variables": missing,
                "mismatched_variables": mismatched,
                "expected_storage_backend": "minio",
                "expected_task_execution_backend": "celery",
            },
        )


def check_local_demo_defaults(services: dict[str, Any], checks: list[dict[str, Any]]) -> None:
    markers = local_demo_default_markers(services)
    production_services = services_with_production_env(services)
    if markers and production_services:
        add_check(
            checks,
            "compose.local_demo_defaults",
            False,
            severity="error",
            message="production compose services must not use local demo defaults",
            evidence={
                "local_demo_value_count": len(markers),
                "markers": markers,
                "production_services": production_services,
            },
        )
        return
    if markers:
        add_check(
            checks,
            "compose.local_demo_defaults",
            False,
            severity="warning",
            message="local demo defaults are present and must be replaced before production",
            evidence={
                "local_demo_value_count": len(markers),
                "markers": markers,
                "production_services": production_services,
            },
        )
        return
    add_check(
        checks,
        "compose.local_demo_defaults",
        True,
        severity="warning",
        message="no local demo defaults were detected",
        evidence={"local_demo_value_count": 0, "markers": [], "production_services": production_services},
    )


def check_backend_dockerfile(path: Path, checks: list[dict[str, Any]]) -> None:
    text = read_text(path)
    if text is None:
        add_check(
            checks,
            "dockerfile.backend.exists",
            False,
            severity="error",
            message=f"backend Dockerfile does not exist: {path}",
        )
        return
    lines = text.splitlines()
    failures = []
    normalized_text = text.replace('"', "").replace("'", "")
    if not any(line.startswith("FROM python:") for line in lines):
        failures.append("python base image must use an explicit tag")
    if "pip install" not in text or "--no-cache-dir" not in text:
        failures.append("backend image must install Python dependencies without pip cache")
    if ".[optimization]" not in normalized_text:
        failures.append("backend image must install the packaged app with optimization extra")
    if "psycopg[binary]" not in normalized_text:
        failures.append("backend image must install the psycopg binary driver")
    if "uvicorn" not in text or "app.main:app" not in text:
        failures.append("backend image must start uvicorn app.main:app")
    add_check(
        checks,
        "dockerfile.backend.runtime",
        not failures,
        severity="error",
        message="backend Dockerfile keeps the API runtime reproducible",
        evidence={"failures": failures},
    )


def check_frontend_dockerfile(path: Path, checks: list[dict[str, Any]]) -> None:
    text = read_text(path)
    if text is None:
        add_check(
            checks,
            "dockerfile.frontend.exists",
            False,
            severity="error",
            message=f"frontend Dockerfile does not exist: {path}",
        )
        return
    lines = text.splitlines()
    failures = []
    copy_line = "COPY frontend/package.json frontend/package-lock.json /app/"
    if "frontend/package-lock.json" not in text:
        failures.append("frontend Dockerfile must copy package-lock.json")
    if "RUN npm ci" not in text:
        failures.append("frontend Dockerfile must install dependencies with npm ci")
    if "RUN npm install" in text:
        failures.append("frontend Dockerfile must not use npm install")
    if copy_line in lines and "RUN npm ci" in lines and lines.index(copy_line) > lines.index("RUN npm ci"):
        failures.append("frontend package manifest copy must happen before npm ci")
    add_check(
        checks,
        "dockerfile.frontend.lockfile_install",
        not failures,
        severity="error",
        message="frontend Dockerfile uses lockfile based installation",
        evidence={"failures": failures},
    )


def local_demo_default_markers(services: dict[str, Any]) -> list[dict[str, str]]:
    markers: list[dict[str, str]] = []
    postgres_env = service_environment(services.get("postgres"))
    if postgres_env.get("POSTGRES_USER") == "packaging" and postgres_env.get("POSTGRES_PASSWORD") == "packaging":
        markers.append({"service": "postgres", "marker": "postgres_demo_login"})

    minio_env = service_environment(services.get("minio"))
    if minio_env.get("MINIO_ROOT_USER") == "minioadmin" and minio_env.get("MINIO_ROOT_PASSWORD") == "minioadmin":
        markers.append({"service": "minio", "marker": "minio_demo_root_login"})

    for service_name in BACKEND_RUNTIME_SERVICES:
        env = service_environment(services.get(service_name))
        if "packaging:packaging@" in str(env.get("DATABASE_URL") or ""):
            markers.append({"service": service_name, "marker": "backend_demo_database_url"})
        if env.get("MINIO_ACCESS_KEY") == "minioadmin" and env.get("MINIO_SECRET_KEY") == "minioadmin":
            markers.append({"service": service_name, "marker": "backend_demo_minio_login"})
    return markers


def services_with_production_env(services: dict[str, Any]) -> list[str]:
    production_services = []
    for service_name, service in services.items():
        app_env = service_environment(service).get("APP_ENV")
        if isinstance(app_env, str) and app_env.strip().lower() in PRODUCTION_ENV_VALUES:
            production_services.append(str(service_name))
    return sorted(production_services)


def validate_deployment_compose_policy_contract(
    checks: list[dict[str, Any]],
    services: dict[str, Any] | None,
) -> dict[str, Any]:
    check_by_name = {str(item.get("name")): item for item in checks}
    local_demo_check = check_by_name.get("compose.local_demo_defaults", {})
    local_demo_status = str(local_demo_check.get("status") or "failed")
    checks_out = [
        policy_check(
            code="compose.required_services",
            status=aggregate_check_status(check_by_name, ["compose.required_services"]),
            message="Compose declares every required service"
            if aggregate_check_status(check_by_name, ["compose.required_services"]) == "passed"
            else "Compose must declare postgres, redis, minio, api, worker, scheduler, and frontend",
            evidence=required_services_evidence(check_by_name, services),
        ),
        policy_check(
            code="compose.healthchecks",
            status=aggregate_check_status(
                check_by_name,
                [
                    "compose.healthcheck.postgres",
                    "compose.healthcheck.redis",
                    "compose.healthcheck.api",
                ],
            ),
            message="Core service healthchecks are configured"
            if aggregate_check_status(
                check_by_name,
                [
                    "compose.healthcheck.postgres",
                    "compose.healthcheck.redis",
                    "compose.healthcheck.api",
                ],
            )
            == "passed"
            else "Postgres, Redis, and API healthchecks must be configured",
            evidence=checks_status_evidence(
                check_by_name,
                [
                    "compose.healthcheck.postgres",
                    "compose.healthcheck.redis",
                    "compose.healthcheck.api",
                ],
            ),
        ),
        policy_check(
            code="compose.dependencies",
            status=aggregate_check_status(
                check_by_name,
                [
                    "compose.depends_on.api",
                    "compose.depends_on.worker",
                    "compose.depends_on.scheduler",
                    "compose.depends_on.frontend",
                ],
            ),
            message="Runtime services wait for their required dependencies"
            if aggregate_check_status(
                check_by_name,
                [
                    "compose.depends_on.api",
                    "compose.depends_on.worker",
                    "compose.depends_on.scheduler",
                    "compose.depends_on.frontend",
                ],
            )
            == "passed"
            else "Runtime services must wait for healthy dependencies",
            evidence=checks_status_evidence(
                check_by_name,
                [
                    "compose.depends_on.api",
                    "compose.depends_on.worker",
                    "compose.depends_on.scheduler",
                    "compose.depends_on.frontend",
                ],
            ),
        ),
        policy_check(
            code="compose.image_tags",
            status=aggregate_check_status(
                check_by_name,
                [
                    "compose.image_tag.postgres",
                    "compose.image_tag.redis",
                    "compose.image_tag.minio",
                ],
            ),
            message="External service images use explicit non-latest tags"
            if aggregate_check_status(
                check_by_name,
                [
                    "compose.image_tag.postgres",
                    "compose.image_tag.redis",
                    "compose.image_tag.minio",
                ],
            )
            == "passed"
            else "External service images must use explicit non-latest tags",
            evidence=checks_status_evidence(
                check_by_name,
                [
                    "compose.image_tag.postgres",
                    "compose.image_tag.redis",
                    "compose.image_tag.minio",
                ],
            ),
        ),
        policy_check(
            code="compose.backend_environment",
            status=aggregate_check_status(
                check_by_name,
                [
                    "compose.backend_environment.api",
                    "compose.backend_environment.worker",
                    "compose.backend_environment.scheduler",
                ],
            ),
            message="Backend runtime services use MinIO and Celery environment"
            if aggregate_check_status(
                check_by_name,
                [
                    "compose.backend_environment.api",
                    "compose.backend_environment.worker",
                    "compose.backend_environment.scheduler",
                ],
            )
            == "passed"
            else "Backend runtime services must declare MinIO and Celery environment",
            evidence=checks_status_evidence(
                check_by_name,
                [
                    "compose.backend_environment.api",
                    "compose.backend_environment.worker",
                    "compose.backend_environment.scheduler",
                ],
            ),
        ),
        policy_check(
            code="compose.local_demo_defaults",
            status=local_demo_status if local_demo_status in {"passed", "warning"} else "failed",
            message="Local demo defaults are absent or explicitly limited to non-production"
            if local_demo_status in {"passed", "warning"}
            else "Production compose services must not use local demo defaults",
            evidence=dict(local_demo_check.get("evidence") or {}),
        ),
        policy_check(
            code="dockerfile.backend.runtime",
            status=aggregate_check_status(check_by_name, ["dockerfile.backend.runtime"]),
            message="Backend Dockerfile keeps release runtime reproducible"
            if aggregate_check_status(check_by_name, ["dockerfile.backend.runtime"]) == "passed"
            else "Backend Dockerfile must install the optimized app and production driver",
            evidence=checks_status_evidence(check_by_name, ["dockerfile.backend.runtime"]),
        ),
        policy_check(
            code="dockerfile.frontend.lockfile",
            status=aggregate_check_status(check_by_name, ["dockerfile.frontend.lockfile_install"]),
            message="Frontend Dockerfile uses lockfile based dependency installation"
            if aggregate_check_status(check_by_name, ["dockerfile.frontend.lockfile_install"]) == "passed"
            else "Frontend Dockerfile must use package-lock.json with npm ci",
            evidence=checks_status_evidence(check_by_name, ["dockerfile.frontend.lockfile_install"]),
        ),
    ]
    failed_count = sum(1 for check in checks_out if check["status"] == "failed")
    warning_count = sum(1 for check in checks_out if check["status"] == "warning")
    passed_count = sum(1 for check in checks_out if check["status"] == "passed")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in checks_out if check["status"] == "failed"],
        "warning_checks": [check for check in checks_out if check["status"] == "warning"],
        "checks": checks_out,
    }


def aggregate_check_status(check_by_name: dict[str, dict[str, Any]], names: list[str]) -> str:
    statuses = [str(check_by_name.get(name, {}).get("status") or "failed") for name in names]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "passed"


def checks_status_evidence(check_by_name: dict[str, dict[str, Any]], names: list[str]) -> dict[str, Any]:
    return {
        "checks": {
            name: str(check_by_name.get(name, {}).get("status") or "missing")
            for name in names
        },
        "failed_checks": [
            name
            for name in names
            if str(check_by_name.get(name, {}).get("status") or "failed") == "failed"
        ],
        "warning_checks": [
            name
            for name in names
            if str(check_by_name.get(name, {}).get("status") or "") == "warning"
        ],
    }


def required_services_evidence(
    check_by_name: dict[str, dict[str, Any]],
    services: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = dict(check_by_name.get("compose.required_services", {}).get("evidence") or {})
    evidence["service_count"] = len(services) if isinstance(services, dict) else 0
    return evidence


def policy_check(
    *,
    code: str,
    status: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "severity": "critical" if status == "failed" else "warning" if status == "warning" else "info",
        "message": message,
        "evidence": evidence or {},
    }


def build_summary(
    checks: list[dict[str, Any]],
    services: dict[str, Any] | None,
    policy_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failed = [item for item in checks if item["status"] == "failed"]
    warnings = [item for item in checks if item["status"] == "warning"]
    passed = [item for item in checks if item["status"] == "passed"]
    local_demo_check = next((item for item in checks if item["name"] == "compose.local_demo_defaults"), {})
    local_demo_evidence = local_demo_check.get("evidence") if isinstance(local_demo_check, dict) else {}
    policy = policy_contract or {}
    policy_failed_count = int(policy.get("failed_count") or 0)
    policy_warning_count = int(policy.get("warning_count") or 0)
    return {
        "service_count": len(services) if isinstance(services, dict) else 0,
        "check_count": len(checks),
        "passed_count": len(passed),
        "error_count": len(failed),
        "warning_count": len(warnings),
        "failed_checks": [str(item["name"]) for item in failed],
        "warning_checks": [str(item["name"]) for item in warnings],
        "local_demo_value_count": int(local_demo_evidence.get("local_demo_value_count") or 0)
        if isinstance(local_demo_evidence, dict)
        else 0,
        "production_mode_service_count": len(local_demo_evidence.get("production_services") or [])
        if isinstance(local_demo_evidence, dict)
        else 0,
        "policy_contract_status": policy.get("status"),
        "policy_contract_failed_count": policy_failed_count,
        "policy_contract_warning_count": policy_warning_count,
    }


def add_check(
    checks: list[dict[str, Any]],
    name: str,
    passed: bool,
    *,
    severity: str,
    message: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    if severity not in {"error", "warning"}:
        raise ValueError(f"unsupported severity: {severity}")
    status = "passed" if passed else ("warning" if severity == "warning" else "failed")
    checks.append(
        {
            "name": name,
            "status": status,
            "severity": severity,
            "message": message,
            "evidence": evidence or {},
        }
    )


def service_mapping(service: Any) -> dict[str, Any]:
    return service if isinstance(service, dict) else {}


def service_environment(service: Any) -> dict[str, str]:
    environment = service_mapping(service).get("environment")
    if isinstance(environment, dict):
        return {str(key): "" if value is None else str(value) for key, value in environment.items()}
    if isinstance(environment, list):
        normalized: dict[str, str] = {}
        for item in environment:
            text = str(item)
            if "=" in text:
                key, value = text.split("=", 1)
                normalized[key] = value
            else:
                normalized[text] = ""
        return normalized
    return {}


def healthcheck_test(service: Any) -> Any:
    healthcheck = service_mapping(service).get("healthcheck")
    if not isinstance(healthcheck, dict):
        return None
    return healthcheck.get("test")


def dependency_condition(service: Any, dependency_name: str) -> str | None:
    depends_on = service_mapping(service).get("depends_on")
    if isinstance(depends_on, dict):
        dependency = depends_on.get(dependency_name)
        if isinstance(dependency, dict):
            condition = dependency.get("condition")
            return str(condition) if condition is not None else None
        if dependency is not None:
            return "service_started"
    if isinstance(depends_on, list) and dependency_name in depends_on:
        return "service_started"
    return None


def flattened_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(flattened_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key} {flattened_text(item)}" for key, item in value.items())
    return "" if value is None else str(value)


def image_has_explicit_non_latest_tag(image: str) -> bool:
    if "@" in image:
        return True
    image_name = image.rsplit("/", 1)[-1]
    if ":" not in image_name:
        return False
    tag = image_name.rsplit(":", 1)[-1]
    return bool(tag) and tag != "latest"


def read_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = resolve_repo_path(output_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Docker Compose deployment handoff settings.")
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_PATH, help="Docker Compose file to audit.")
    parser.add_argument(
        "--backend-dockerfile",
        type=Path,
        default=DEFAULT_BACKEND_DOCKERFILE,
        help="Backend Dockerfile to audit.",
    )
    parser.add_argument(
        "--frontend-dockerfile",
        type=Path,
        default=DEFAULT_FRONTEND_DOCKERFILE,
        help="Frontend Dockerfile to audit.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_deployment_compose_audit_report(
        compose_file=args.compose_file,
        backend_dockerfile=args.backend_dockerfile,
        frontend_dockerfile=args.frontend_dockerfile,
    )
    if args.output:
        write_report(args.output, report)
    summary = report["summary"]
    print(
        "deployment compose audit "
        f"{report['status']} "
        f"checks={summary['check_count']} "
        f"errors={summary['error_count']} "
        f"warnings={summary['warning_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
