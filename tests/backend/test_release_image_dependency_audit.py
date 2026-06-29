from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_image_dependency_audit.py"


def load_release_image_dependency_audit_module():
    spec = importlib.util.spec_from_file_location("release_image_dependency_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_image_dependency_audit_runs_inventory_and_review_in_container(tmp_path: Path) -> None:
    module = load_release_image_dependency_audit_module()
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"
    calls: list[list[str]] = []

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        calls.append(command)
        if name == "release_image_inventory":
            write_json(inventory_output, inventory(missing_install_count=0, review_required_count=0))
        if name == "release_image_dependency_review":
            write_json(review_output, review_audit(status="passed", review_required_count=0))
        return module.CommandExecution(
            name=name,
            command=command,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            exit_code=0,
            duration_sec=0.01,
        )

    report = module.build_release_image_dependency_audit(
        image_tag="packaging:test",
        inventory_output=inventory_output,
        review_output=review_output,
        command_runner=runner,
    )

    assert report["status"] == "passed"
    assert report["summary"]["command_count"] == 3
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["policy_contract"]["status"] == "passed"
    assert report["summary"]["missing_install_count"] == 0
    assert report["summary"]["dependency_review_status"] == "passed"
    assert calls[0][:3] == ["docker", "build", "-f"]
    assert calls[1][0:2] == ["docker", "run"]
    assert "scripts/release_inventory.py" in calls[1]
    assert "scripts/dependency_review_audit.py" in calls[2]
    assert str(inventory_output) == report["inventory_output"]
    assert str(review_output) == report["dependency_review_output"]


def test_release_image_dependency_audit_fails_when_image_inventory_has_missing_installs(tmp_path: Path) -> None:
    module = load_release_image_dependency_audit_module()
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        if name == "release_image_inventory":
            write_json(inventory_output, inventory(missing_install_count=2, review_required_count=2))
        if name == "release_image_dependency_review":
            write_json(review_output, review_audit(status="skipped", review_required_count=2))
        return module.CommandExecution(
            name=name,
            command=command,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            exit_code=0,
            duration_sec=0.01,
        )

    report = module.build_release_image_dependency_audit(
        inventory_output=inventory_output,
        review_output=review_output,
        command_runner=runner,
    )

    assert report["status"] == "failed"
    failed_codes = {check["code"] for check in report["policy_contract"]["failed_checks"]}
    assert "inventory.release_blocking_installs" in failed_codes
    assert "review.status" in failed_codes
    assert "release image dependency inventory has 2 release-blocking missing installed package(s)" in report["errors"]
    assert "release image dependency review audit must be passed, got skipped" in report["errors"]


def test_release_image_dependency_audit_skip_build_reuses_image_tag(tmp_path: Path) -> None:
    module = load_release_image_dependency_audit_module()
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"
    command_names: list[str] = []

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        command_names.append(name)
        if name == "release_image_inventory":
            write_json(inventory_output, inventory(missing_install_count=0, review_required_count=0))
        if name == "release_image_dependency_review":
            write_json(review_output, review_audit(status="passed", review_required_count=0))
        return module.CommandExecution(
            name=name,
            command=command,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            exit_code=0,
            duration_sec=0.01,
        )

    report = module.build_release_image_dependency_audit(
        inventory_output=inventory_output,
        review_output=review_output,
        skip_build=True,
        command_runner=runner,
    )

    assert report["status"] == "passed"
    assert command_names == ["release_image_inventory", "release_image_dependency_review"]
    assert report["summary"]["skip_build"] is True
    assert report["summary"]["policy_contract_status"] == "passed"


def test_release_image_dependency_audit_allows_non_blocking_missing_test_extra(tmp_path: Path) -> None:
    module = load_release_image_dependency_audit_module()
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        if name == "release_image_inventory":
            write_json(
                inventory_output,
                inventory(
                    missing_install_count=1,
                    release_blocking_missing_install_count=0,
                    review_required_count=0,
                ),
            )
        if name == "release_image_dependency_review":
            write_json(review_output, review_audit(status="passed", review_required_count=0))
        return module.CommandExecution(
            name=name,
            command=command,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            exit_code=0,
            duration_sec=0.01,
        )

    report = module.build_release_image_dependency_audit(
        inventory_output=inventory_output,
        review_output=review_output,
        command_runner=runner,
    )

    assert report["status"] == "passed"
    assert report["summary"]["missing_install_count"] == 1
    assert report["summary"]["release_blocking_missing_install_count"] == 0
    assert report["summary"]["policy_contract_status"] == "passed"


def test_release_image_dependency_audit_rejects_failed_review_policy_contract(tmp_path: Path) -> None:
    module = load_release_image_dependency_audit_module()
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        if name == "release_image_inventory":
            write_json(inventory_output, inventory(missing_install_count=0, review_required_count=0))
        if name == "release_image_dependency_review":
            write_json(
                review_output,
                review_audit(status="passed", review_required_count=0, policy_contract_status="failed"),
            )
        return module.CommandExecution(
            name=name,
            command=command,
            cwd=str(cwd),
            timeout_sec=timeout_sec,
            exit_code=0,
            duration_sec=0.01,
        )

    report = module.build_release_image_dependency_audit(
        inventory_output=inventory_output,
        review_output=review_output,
        command_runner=runner,
    )

    failed_codes = {check["code"] for check in report["policy_contract"]["failed_checks"]}
    assert report["status"] == "failed"
    assert "review.policy_contract" in failed_codes


def inventory(
    *,
    missing_install_count: int,
    review_required_count: int,
    release_blocking_missing_install_count: int | None = None,
) -> dict:
    blocking_missing = (
        missing_install_count
        if release_blocking_missing_install_count is None
        else release_blocking_missing_install_count
    )
    return {
        "schema_version": 1,
        "status": "passed",
        "summary": {
            "dependency_count": 2,
            "installed_count": 2 - missing_install_count,
            "missing_install_count": missing_install_count,
            "release_blocking_missing_install_count": blocking_missing,
            "review_required_count": review_required_count,
        },
        "dependencies": [],
    }


def review_audit(*, status: str, review_required_count: int, policy_contract_status: str | None = None) -> dict:
    policy_status = policy_contract_status or ("passed" if status == "passed" else status)
    return {
        "schema_version": 1,
        "status": status,
        "summary": {
            "review_required_count": review_required_count,
            "approved_count": 0,
            "missing_ack_count": review_required_count,
            "policy_contract_status": policy_status,
            "policy_contract_failed_count": 0 if policy_status == "passed" else 1,
            "policy_contract_warning_count": 0,
        },
        "policy_contract": {
            "status": policy_status,
            "failed_count": 0 if policy_status == "passed" else 1,
            "warning_count": 0,
            "checks": [],
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_tmp_dir(tmp_path: Path) -> Path:
    path = REPO_ROOT / "tmp" / "pytest-release-image-dependency" / tmp_path.name
    path.mkdir(parents=True, exist_ok=True)
    return path
