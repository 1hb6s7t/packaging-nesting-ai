from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "repository_hygiene_audit.py"


def load_repository_hygiene_audit_module():
    spec = importlib.util.spec_from_file_location("repository_hygiene_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_hygiene_audit_accepts_current_gitignore() -> None:
    module = load_repository_hygiene_audit_module()

    report = module.build_repository_hygiene_audit()

    assert report["status"] == "passed"
    assert report["summary"]["missing_pattern_count"] == 0
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["policy_contract"]["status"] == "passed"
    assert ".env.*" in report["observed_patterns"]
    assert "!.env.production.example" in report["observed_patterns"]
    assert "artifacts/" in report["observed_patterns"]
    assert "tmp/" in report["observed_patterns"]


def test_repository_hygiene_audit_rejects_missing_secret_and_artifact_rules(tmp_path: Path) -> None:
    module = load_repository_hygiene_audit_module()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8")

    report = module.build_repository_hygiene_audit(gitignore_file=gitignore)

    assert report["status"] == "failed"
    failed_codes = {check["code"] for check in report["policy_contract"]["failed_checks"]}
    assert report["summary"]["policy_contract_status"] == "failed"
    assert "gitignore.required_patterns" in failed_codes
    assert "secrets.env_ignored" in failed_codes
    assert "release_artifacts.ignored" in failed_codes
    assert ".gitignore is missing required pattern: .env.*" in report["errors"]
    assert ".gitignore is missing required pattern: artifacts/" in report["errors"]
    assert ".gitignore is missing required pattern: tmp/" in report["errors"]


def test_repository_hygiene_audit_requires_env_template_unignore(tmp_path: Path) -> None:
    module = load_repository_hygiene_audit_module()
    gitignore = tmp_path / ".gitignore"
    patterns = [pattern for pattern in module.REQUIRED_IGNORE_PATTERNS if pattern != "!.env.production.example"]
    gitignore.write_text("\n".join(patterns) + "\n", encoding="utf-8")

    report = module.build_repository_hygiene_audit(gitignore_file=gitignore)

    failed_codes = {check["code"] for check in report["policy_contract"]["failed_checks"]}
    assert report["status"] == "failed"
    assert "secrets.env_ignored" in failed_codes
    assert ".gitignore must unignore .env.production.example when .env.* is ignored" in report["errors"]


def test_repository_hygiene_audit_cli_writes_report(tmp_path: Path) -> None:
    module = load_repository_hygiene_audit_module()
    output = tmp_path / "repository-hygiene-audit.json"

    exit_code = module.main(["--output", str(output)])

    written = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert written["status"] == "passed"
    assert written["summary"]["policy_contract_status"] == "passed"
    assert written["summary"]["required_pattern_count"] == len(module.REQUIRED_IGNORE_PATTERNS)
