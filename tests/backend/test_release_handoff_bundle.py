from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_handoff_bundle.py"


def load_release_handoff_bundle_module():
    spec = importlib.util.spec_from_file_location("release_handoff_bundle", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_handoff_bundle_derives_evidence_artifacts_and_hashes(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    by_name = {item["name"]: item for item in report["artifacts"]}
    assert report["status"] == "passed"
    assert report["summary"]["artifact_count"] == 8
    assert report["summary"]["passed_count"] == 8
    assert by_name["release_preflight_report"]["sha256"] == module.sha256_file(paths["preflight"])
    assert by_name["release_evidence_manifest"]["path"] == str(paths["evidence_manifest"])
    assert by_name["release_evidence_artifact:deployment_compose_audit"]["path"] == str(
        paths["deployment_compose_audit"]
    )
    assert by_name["release_evidence_artifact:repository_hygiene_audit"]["path"] == str(
        paths["repository_hygiene_audit"]
    )
    assert by_name["dependency_inventory"]["summary"]["dependency_count"] == 2
    assert report["warnings"] == []


def test_release_handoff_bundle_fails_when_evidence_artifact_does_not_match_manifest(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    paths["deployment_compose_audit"].write_text(
        paths["deployment_compose_audit"].read_text(encoding="utf-8") + "\n{}",
        encoding="utf-8",
    )

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert any("deployment_compose_audit sha256 mismatch" in error for error in report["errors"])


def test_release_handoff_bundle_includes_release_image_dependency_audit_when_provided(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
    )

    by_name = {item["name"]: item for item in report["artifacts"]}
    assert report["status"] == "passed"
    assert report["summary"]["artifact_count"] == 9
    assert report["summary"]["passed_count"] == 9
    assert by_name["release_image_dependency_audit"]["path"] == str(paths["release_image_dependency_audit"])
    assert by_name["release_image_dependency_audit"]["summary"]["release_blocking_missing_install_count"] == 0


def test_release_handoff_bundle_fails_when_release_image_dependency_audit_failed(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    failed_report = json.loads(paths["release_image_dependency_audit"].read_text(encoding="utf-8"))
    failed_report["status"] = "failed"
    paths["release_image_dependency_audit"].write_text(json.dumps(failed_report, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
    )

    assert report["status"] == "failed"
    assert "status report must be passed, got failed" in report["errors"]


def test_release_handoff_bundle_fails_when_required_report_failed(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    failed_preflight = json.loads(paths["preflight"].read_text(encoding="utf-8"))
    failed_preflight["passed"] = False
    paths["preflight"].write_text(json.dumps(failed_preflight, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "release preflight report must have passed=true" in report["errors"]


def test_release_handoff_bundle_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    output_path = tmp_path / "release-handoff-bundle.json"
    failed_verification = json.loads(paths["preflight_verification"].read_text(encoding="utf-8"))
    failed_verification["status"] = "failed"
    paths["preflight_verification"].write_text(
        json.dumps(failed_verification, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--preflight-report",
            str(paths["preflight"]),
            "--preflight-verification",
            str(paths["preflight_verification"]),
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["summary"]["error_count"] == 1


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
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"artifact_count": 2, "failed_count": 0},
        },
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
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"error_count": 0, "warning_count": 0},
        },
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
