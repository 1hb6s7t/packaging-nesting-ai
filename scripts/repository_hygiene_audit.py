from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GITIGNORE = REPO_ROOT / ".gitignore"

REQUIRED_IGNORE_PATTERNS = (
    "__pycache__/",
    ".pytest_cache/",
    ".venv/",
    "*.pyc",
    "*.pyo",
    "*.db",
    "storage/",
    "logs/",
    "*.log",
    ".codex-live-*.log",
    "artifacts/",
    "tmp/",
    "node_modules/",
    "dist/",
    ".env",
    ".env.*",
    "!.env.production.example",
)
PYTHON_CACHE_IGNORE_PATTERNS = ("__pycache__/", ".pytest_cache/", ".venv/", "*.pyc", "*.pyo")
RUNTIME_STATE_IGNORE_PATTERNS = ("*.db", "storage/", "logs/", "*.log", ".codex-live-*.log")
RELEASE_ARTIFACT_IGNORE_PATTERNS = ("artifacts/", "tmp/")
FRONTEND_BUILD_IGNORE_PATTERNS = ("node_modules/", "dist/")
SECRET_IGNORE_PATTERNS = (".env", ".env.*", "!.env.production.example")


def build_repository_hygiene_audit(*, gitignore_file: Path = DEFAULT_GITIGNORE) -> dict[str, Any]:
    resolved_gitignore = gitignore_file if gitignore_file.is_absolute() else REPO_ROOT / gitignore_file
    errors: list[str] = []
    warnings: list[str] = []
    patterns: list[str] = []

    if not resolved_gitignore.is_file():
        errors.append(f".gitignore does not exist: {resolved_gitignore}")
    else:
        patterns = parse_gitignore_patterns(resolved_gitignore)
        missing_patterns = [pattern for pattern in REQUIRED_IGNORE_PATTERNS if pattern not in patterns]
        errors.extend(f".gitignore is missing required pattern: {pattern}" for pattern in missing_patterns)
        if ".env.*" in patterns and "!.env.production.example" not in patterns:
            errors.append(".gitignore must unignore .env.production.example when .env.* is ignored")
        if "tmp/" not in patterns:
            warnings.append("tmp/ is not ignored; release preflight artifacts may be mixed into source review")
        if "artifacts/" not in patterns:
            warnings.append("artifacts/ is not ignored; go-live evidence may be mixed into source review")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not errors else "failed",
        "gitignore_file": str(resolved_gitignore),
        "summary": {
            "required_pattern_count": len(REQUIRED_IGNORE_PATTERNS),
            "observed_pattern_count": len(patterns),
            "missing_pattern_count": sum(1 for pattern in REQUIRED_IGNORE_PATTERNS if pattern not in patterns),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "required_patterns": list(REQUIRED_IGNORE_PATTERNS),
        "observed_patterns": patterns,
        "errors": errors,
        "warnings": warnings,
        "policy_contract": {},
    }
    return attach_policy_contract(report)


def attach_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    policy_contract = validate_repository_hygiene_policy_contract(report)
    report["policy_contract"] = policy_contract
    summary = report.get("summary")
    if isinstance(summary, dict):
        summary["policy_contract_status"] = policy_contract.get("status")
        summary["policy_contract_failed_count"] = int(policy_contract.get("failed_count") or 0)
        summary["policy_contract_warning_count"] = int(policy_contract.get("warning_count") or 0)
    if int(policy_contract.get("failed_count") or 0):
        report["status"] = "failed"
    return report


def validate_repository_hygiene_policy_contract(report: dict[str, Any]) -> dict[str, Any]:
    observed_patterns = report.get("observed_patterns")
    observed = set(observed_patterns if isinstance(observed_patterns, list) else [])
    errors = [str(error) for error in report.get("errors") or []]
    warnings = [str(warning) for warning in report.get("warnings") or []]
    missing_required = missing_patterns(observed, REQUIRED_IGNORE_PATTERNS)
    missing_secret_patterns = missing_patterns(observed, SECRET_IGNORE_PATTERNS)
    gitignore_exists = not any(error.startswith(".gitignore does not exist:") for error in errors)

    checks = [
        policy_check(
            code="schema.version",
            status="passed" if report.get("schema_version") == 1 else "failed",
            message="repository hygiene audit schema_version is 1"
            if report.get("schema_version") == 1
            else "repository hygiene audit schema_version must be 1",
            evidence={"schema_version": report.get("schema_version")},
        ),
        policy_check(
            code="gitignore.file.present",
            status="passed" if gitignore_exists else "failed",
            message=".gitignore exists for repository hygiene enforcement"
            if gitignore_exists
            else ".gitignore must exist for repository hygiene enforcement",
            evidence={"gitignore_file": report.get("gitignore_file")},
        ),
        policy_check(
            code="gitignore.required_patterns",
            status="passed" if not missing_required else "failed",
            message=".gitignore contains every required hygiene pattern"
            if not missing_required
            else ".gitignore must contain every required hygiene pattern",
            evidence={"missing_patterns": missing_required, "required_patterns": list(REQUIRED_IGNORE_PATTERNS)},
        ),
        policy_check(
            code="secrets.env_ignored",
            status="passed" if not missing_secret_patterns else "failed",
            message="local env files are ignored while the production template remains trackable"
            if not missing_secret_patterns
            else "local env files must be ignored and .env.production.example must remain trackable",
            evidence={"missing_patterns": missing_secret_patterns, "required_patterns": list(SECRET_IGNORE_PATTERNS)},
        ),
        policy_check(
            code="runtime_state.ignored",
            status="passed" if patterns_present(observed, RUNTIME_STATE_IGNORE_PATTERNS) else "failed",
            message="runtime databases, storage, and logs are ignored"
            if patterns_present(observed, RUNTIME_STATE_IGNORE_PATTERNS)
            else "runtime databases, storage, and logs must be ignored",
            evidence={
                "missing_patterns": missing_patterns(observed, RUNTIME_STATE_IGNORE_PATTERNS),
                "required_patterns": list(RUNTIME_STATE_IGNORE_PATTERNS),
            },
        ),
        policy_check(
            code="release_artifacts.ignored",
            status="passed" if patterns_present(observed, RELEASE_ARTIFACT_IGNORE_PATTERNS) else "failed",
            message="generated release artifacts are ignored"
            if patterns_present(observed, RELEASE_ARTIFACT_IGNORE_PATTERNS)
            else "generated release artifacts must be ignored",
            evidence={
                "missing_patterns": missing_patterns(observed, RELEASE_ARTIFACT_IGNORE_PATTERNS),
                "required_patterns": list(RELEASE_ARTIFACT_IGNORE_PATTERNS),
            },
        ),
        policy_check(
            code="python_cache.ignored",
            status="passed" if patterns_present(observed, PYTHON_CACHE_IGNORE_PATTERNS) else "failed",
            message="Python caches and virtual environments are ignored"
            if patterns_present(observed, PYTHON_CACHE_IGNORE_PATTERNS)
            else "Python caches and virtual environments must be ignored",
            evidence={
                "missing_patterns": missing_patterns(observed, PYTHON_CACHE_IGNORE_PATTERNS),
                "required_patterns": list(PYTHON_CACHE_IGNORE_PATTERNS),
            },
        ),
        policy_check(
            code="frontend_build.ignored",
            status="passed" if patterns_present(observed, FRONTEND_BUILD_IGNORE_PATTERNS) else "failed",
            message="frontend dependency and build outputs are ignored"
            if patterns_present(observed, FRONTEND_BUILD_IGNORE_PATTERNS)
            else "frontend dependency and build outputs must be ignored",
            evidence={
                "missing_patterns": missing_patterns(observed, FRONTEND_BUILD_IGNORE_PATTERNS),
                "required_patterns": list(FRONTEND_BUILD_IGNORE_PATTERNS),
            },
        ),
        policy_check(
            code="warnings.clear",
            status="warning" if warnings else "passed",
            message="repository hygiene audit has no warnings"
            if not warnings
            else "repository hygiene audit warnings should be reviewed before handoff",
            evidence={"warning_count": len(warnings), "warnings": warnings},
        ),
    ]
    failed_count = sum(1 for check in checks if check["status"] == "failed")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    passed_count = sum(1 for check in checks if check["status"] == "passed")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "passed_count": passed_count,
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in checks if check["status"] == "failed"],
        "warning_checks": [check for check in checks if check["status"] == "warning"],
        "checks": checks,
    }


def patterns_present(observed: set[str], required: tuple[str, ...]) -> bool:
    return not missing_patterns(observed, required)


def missing_patterns(observed: set[str], required: tuple[str, ...]) -> list[str]:
    return [pattern for pattern in required if pattern not in observed]


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


def parse_gitignore_patterns(path: Path) -> list[str]:
    patterns: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit repository ignore rules for generated artifacts and local secrets.")
    parser.add_argument("--gitignore-file", type=Path, default=DEFAULT_GITIGNORE)
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_repository_hygiene_audit(gitignore_file=args.gitignore_file)
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "repository hygiene audit "
        f"{report['status']} "
        f"required_patterns={summary['required_pattern_count']} "
        f"missing={summary['missing_pattern_count']} "
        f"errors={summary['error_count']} "
        f"policy={summary.get('policy_contract_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
