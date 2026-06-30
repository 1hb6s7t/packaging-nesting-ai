from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT_PATH = REPO_ROOT / "scripts" / "release_image_dependency_audit.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_image_dependency_audit.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_release_image_dependency_audit_accepts_generated_report(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)

    verification = verify_module.verify_release_image_dependency_audit(paths["report"])

    assert verification["status"] == "passed"
    assert verification["summary"]["command_count"] == 2
    assert verification["summary"]["failed_output_check_count"] == 0
    assert verification["errors"] == []


def test_verify_release_image_dependency_audit_uses_base_dir_after_copy(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_copy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_copy", VERIFY_SCRIPT_PATH)
    source_dir = repo_tmp_dir(tmp_path / "source")
    copied_dir = repo_tmp_dir(tmp_path / "copied")
    write_release_image_dependency_outputs(audit_module, source_dir)
    shutil.copytree(source_dir, copied_dir, dirs_exist_ok=True)
    copied_report = copied_dir / "release-image-dependency-audit.json"
    report = json.loads(copied_report.read_text(encoding="utf-8"))
    report["inventory_output"] = str(tmp_path / "stale" / "dependency-inventory-release-image.json")
    report["dependency_review_output"] = str(tmp_path / "stale" / "dependency-review-audit-release-image.json")
    write_json(copied_report, report)

    verification = verify_module.verify_release_image_dependency_audit(copied_report, base_dir=copied_dir)

    assert verification["status"] == "passed"
    assert all(str(copied_dir) in check["path"] for check in verification["output_checks"])


def test_verify_release_image_dependency_audit_rejects_summary_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_summary_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_summary", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["summary"]["command_count"] = 99
    write_json(paths["report"], report)

    verification = verify_module.verify_release_image_dependency_audit(paths["report"])

    assert verification["status"] == "failed"
    assert any("summary.command_count must be 2" in error for error in verification["errors"])


def test_verify_release_image_dependency_audit_rejects_policy_contract_mismatch(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_policy_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_policy", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["policy_contract"]["status"] = "failed"
    report["policy_contract"]["failed_count"] = 1
    write_json(paths["report"], report)

    verification = verify_module.verify_release_image_dependency_audit(paths["report"])

    assert verification["status"] == "failed"
    assert "release image dependency audit policy_contract does not match recomputed report policy" in verification["errors"]


def test_verify_release_image_dependency_audit_rejects_output_summary_drift(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_output_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_output", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)
    inventory_payload = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory_payload["summary"]["release_blocking_missing_install_count"] = 1
    write_json(paths["inventory"], inventory_payload)

    verification = verify_module.verify_release_image_dependency_audit(paths["report"])

    assert verification["status"] == "failed"
    assert any(
        "release image dependency inventory summary.release_blocking_missing_install_count must match" in error
        for error in verification["errors"]
    )


def test_verify_release_image_dependency_audit_rejects_duplicate_command_names(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_duplicate_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_duplicate", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["commands"].append(dict(report["commands"][0]))
    write_json(paths["report"], report)

    verification = verify_module.verify_release_image_dependency_audit(paths["report"])

    assert verification["status"] == "failed"
    assert "release image dependency audit has duplicate command names: release_image_inventory" in verification["errors"]


def test_verify_release_image_dependency_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    audit_module = load_module("release_image_dependency_audit_for_cli_verify", AUDIT_SCRIPT_PATH)
    verify_module = load_module("verify_release_image_dependency_audit_for_cli", VERIFY_SCRIPT_PATH)
    paths = write_release_image_dependency_outputs(audit_module, tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["status"] = "failed"
    write_json(paths["report"], report)
    output_path = paths["report"].parent / "release-image-dependency-verification.json"

    exit_code = verify_module.main(["--report", str(paths["report"]), "--output", str(output_path)])

    verification = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert verification["status"] == "failed"
    assert "release image dependency audit status must be passed, got failed" in verification["errors"]


def write_release_image_dependency_outputs(module, tmp_path: Path) -> dict[str, Path]:
    output_dir = repo_tmp_dir(tmp_path)
    inventory_output = output_dir / "dependency-inventory-release-image.json"
    review_output = output_dir / "dependency-review-audit-release-image.json"
    report_output = output_dir / "release-image-dependency-audit.json"

    def runner(name: str, command: list[str], cwd: Path, timeout_sec: int):
        if name == "release_image_inventory":
            write_json(inventory_output, inventory())
        if name == "release_image_dependency_review":
            write_json(review_output, review_audit())
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
    write_json(report_output, report)
    return {"report": report_output, "inventory": inventory_output, "review": review_output}


def inventory() -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "summary": {
            "dependency_count": 2,
            "installed_count": 2,
            "missing_install_count": 0,
            "release_blocking_missing_install_count": 0,
            "review_required_count": 0,
        },
        "dependencies": [],
    }


def review_audit() -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "summary": {
            "review_required_count": 0,
            "approved_count": 0,
            "missing_ack_count": 0,
            "policy_contract_status": "passed",
            "policy_contract_failed_count": 0,
            "policy_contract_warning_count": 0,
        },
        "policy_contract": {
            "status": "passed",
            "passed_count": 1,
            "warning_count": 0,
            "failed_count": 0,
            "failed_checks": [],
            "warning_checks": [],
            "checks": [{"code": "fixture.review", "status": "passed"}],
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repo_tmp_dir(tmp_path: Path) -> Path:
    path = REPO_ROOT / "tmp" / "pytest-verify-release-image-dependency" / tmp_path.name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path
