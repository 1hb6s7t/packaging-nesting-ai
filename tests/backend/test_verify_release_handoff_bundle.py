from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SCRIPT_PATH = REPO_ROOT / "scripts" / "release_handoff_bundle.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_handoff_bundle.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_release_handoff_bundle_accepts_generated_manifest(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "passed"
    assert verification["summary"]["artifact_count"] == 8
    assert verification["summary"]["verified_count"] == 8
    assert verification["summary"]["failed_count"] == 0
    assert verification["manifest_errors"] == []


def test_verify_release_handoff_bundle_uses_base_dir_after_copy(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_copy_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_copy", VERIFY_SCRIPT_PATH)
    source_dir = tmp_path / "source"
    copied_dir = tmp_path / "copied"
    source_dir.mkdir()
    bundle_module.REPO_ROOT = source_dir
    paths = write_complete_handoff_inputs(source_dir)
    handoff_path = source_dir / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )
    bundle_module.write_json(handoff_path, report)
    shutil.copytree(source_dir, copied_dir)
    copied_handoff = copied_dir / "release-handoff-bundle.json"
    copied_manifest = json.loads(copied_handoff.read_text(encoding="utf-8"))
    copied_manifest["repo_root"] = str(tmp_path / "stale")
    for artifact in copied_manifest["artifacts"]:
        artifact["path"] = str(tmp_path / "stale" / Path(artifact["relative_path"]).name)
    copied_handoff.write_text(json.dumps(copied_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    verification = verify_module.verify_release_handoff_bundle(copied_handoff, base_dir=copied_dir)

    assert verification["status"] == "passed"
    assert all(item["path"] is None or str(copied_dir) in item["path"] for item in verification["checks"])


def test_verify_release_handoff_bundle_accepts_release_image_dependency_audit(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_release_image_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_release_image", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
    )
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "passed"
    assert verification["summary"]["artifact_count"] == 9
    assert verification["summary"]["verified_count"] == 9


def test_verify_release_handoff_bundle_detects_tampered_artifact(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_tamper_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_tamper", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )
    bundle_module.write_json(handoff_path, report)
    paths["preflight"].write_text(paths["preflight"].read_text(encoding="utf-8") + "\n{}", encoding="utf-8")

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert verification["summary"]["failed_artifacts"] == ["release_preflight_report"]
    failed_check = next(item for item in verification["checks"] if item["name"] == "release_preflight_report")
    assert "handoff artifact sha256 mismatch" in failed_check["errors"]


def test_verify_release_handoff_bundle_detects_missing_expanded_evidence_artifact(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_missing_evidence_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_missing_evidence", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )
    bundle_module.write_json(handoff_path, report)
    paths["deployment_compose_audit"].unlink()

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert verification["summary"]["failed_artifacts"] == ["release_evidence_artifact:deployment_compose_audit"]
    failed_check = next(
        item for item in verification["checks"] if item["name"] == "release_evidence_artifact:deployment_compose_audit"
    )
    assert "handoff artifact file is missing" in failed_check["errors"]


def test_verify_release_handoff_bundle_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    verify_module = load_module("verify_release_handoff_bundle_for_cli", VERIFY_SCRIPT_PATH)
    manifest_path = tmp_path / "release-handoff-bundle.json"
    output_path = tmp_path / "release-handoff-verification.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "status": "failed", "artifacts": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    exit_code = verify_module.main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["summary"]["manifest_error_count"] == 1


def write_complete_handoff_inputs(tmp_path: Path) -> dict[str, Path]:
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    dependency_inventory = evidence_dir / "dependency-inventory.json"
    dependency_review = evidence_dir / "dependency-review-audit.json"
    release_image_dependency_audit = evidence_dir / "release-image-dependency-audit.json"
    deployment_compose = evidence_dir / "deployment-compose-audit.json"
    repository_hygiene = evidence_dir / "repository-hygiene-audit.json"
    evidence_manifest = evidence_dir / "release-evidence-pack.json"
    evidence_verification = evidence_dir / "release-evidence-verification.json"
    preflight = tmp_path / "release-preflight.json"
    preflight_verification = tmp_path / "release-preflight-verification.json"

    write_json(
        dependency_inventory,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"dependency_count": 2, "review_required_count": 0},
            "dependencies": [],
        },
    )
    write_json(
        dependency_review,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"review_required_count": 0, "approved_count": 0},
        },
    )
    write_json(
        release_image_dependency_audit,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {
                "command_count": 2,
                "failed_command_count": 0,
                "missing_install_count": 1,
                "release_blocking_missing_install_count": 0,
                "review_required_count": 0,
                "dependency_review_status": "passed",
                "error_count": 0,
                "warning_count": 0,
            },
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        deployment_compose,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"check_count": 18, "error_count": 0, "warning_count": 1},
            "checks": [],
        },
    )
    write_json(
        repository_hygiene,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"required_pattern_count": 17, "missing_pattern_count": 0, "error_count": 0},
            "required_patterns": [],
            "observed_patterns": [],
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        evidence_manifest,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"artifact_count": 4, "failed_count": 0},
            "artifacts": [
                artifact_entry("deployment_compose_audit", deployment_compose),
                artifact_entry("repository_hygiene_audit", repository_hygiene),
                artifact_entry("dependency_inventory", dependency_inventory),
                artifact_entry("dependency_review_audit", dependency_review),
            ],
        },
    )
    write_json(
        evidence_verification,
        {"schema_version": 1, "status": "passed", "summary": {"artifact_count": 2, "failed_count": 0}},
    )
    write_json(
        preflight,
        {
            "schema_version": 1,
            "passed": True,
            "gates": [
                {
                    "name": "release evidence pack verification",
                    "status": "passed",
                    "payload": {
                        "manifest_path": str(evidence_manifest),
                        "verification_path": str(evidence_verification),
                    },
                }
            ],
            "cleanup": {"status": "passed"},
        },
    )
    write_json(
        preflight_verification,
        {"schema_version": 1, "status": "passed", "summary": {"error_count": 0, "warning_count": 0}},
    )
    return {
        "preflight": preflight,
        "preflight_verification": preflight_verification,
        "evidence_manifest": evidence_manifest,
        "evidence_verification": evidence_verification,
        "dependency_inventory": dependency_inventory,
        "dependency_review": dependency_review,
        "release_image_dependency_audit": release_image_dependency_audit,
        "deployment_compose_audit": deployment_compose,
        "repository_hygiene_audit": repository_hygiene,
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def artifact_entry(name: str, path: Path) -> dict:
    return {
        "name": name,
        "required": True,
        "status": "passed",
        "relative_path": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
