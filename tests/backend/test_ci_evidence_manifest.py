from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ci_evidence_manifest.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_ci_evidence_manifest.py"
HANDOFF_SCRIPT_PATH = REPO_ROOT / "scripts" / "release_handoff_bundle.py"
HANDOFF_VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_handoff_bundle.py"


def load_ci_evidence_manifest_module():
    spec = importlib.util.spec_from_file_location("ci_evidence_manifest", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_verify_ci_evidence_manifest_module():
    spec = importlib.util.spec_from_file_location("verify_ci_evidence_manifest", VERIFY_SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ci_evidence_manifest_hashes_ci_reports_and_evidence_artifacts(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
        env={
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_SHA": "testsha",
        },
    )

    by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest["status"] == "passed"
    assert manifest["summary"]["failed_count"] == 0
    assert manifest["github"]["run_url"] == "https://github.com/owner/repo/actions/runs/123"
    assert manifest["frontend_gate_policy"]["preflight_skipped"] is True
    assert manifest["frontend_gate_policy"]["covered_by_ci_job"] == "Frontend build"
    assert manifest["frontend_gate_policy"]["frontend_artifact_name"] == "frontend-dist-testsha"
    assert by_name["release_preflight_report"]["sha256"] == sha256_file(paths["preflight_report"])
    assert by_name["release_evidence_artifact:deployment_compose_audit"]["sha256"] == sha256_file(
        paths["deployment_compose_audit"]
    )
    assert by_name["release_evidence_artifact:repository_hygiene_audit"]["status"] == "passed"


def test_ci_evidence_manifest_includes_release_handoff_bundle_when_provided(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    write_handoff_outputs(paths, tmp_path)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        handoff_manifest=paths["handoff_manifest"],
        handoff_verification=paths["handoff_verification"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest["status"] == "passed"
    assert by_name["release_handoff_manifest"]["sha256"] == sha256_file(paths["handoff_manifest"])
    assert by_name["release_handoff_verification"]["sha256"] == sha256_file(paths["handoff_verification"])
    assert by_name["release_handoff_manifest"]["summary"]["failed_count"] == 0


def test_ci_evidence_manifest_rejects_handoff_verification_for_different_manifest(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    write_handoff_outputs(paths, tmp_path)
    verification = json.loads(paths["handoff_verification"].read_text(encoding="utf-8"))
    verification["manifest_path"] = str((tmp_path / "other-handoff.json").resolve())
    write_json(paths["handoff_verification"], verification)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        handoff_manifest=paths["handoff_manifest"],
        handoff_verification=paths["handoff_verification"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    assert manifest["status"] == "failed"
    assert "release_handoff_verification" in manifest["summary"]["failed_artifacts"]
    assert any("release handoff verification manifest_path must match" in error for error in manifest["errors"])


def test_ci_evidence_manifest_rejects_preflight_verification_for_different_report(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    verification = json.loads(paths["preflight_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-release-preflight.json").resolve())
    write_json(paths["preflight_verification"], verification)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
    )

    assert manifest["status"] == "failed"
    assert "release_preflight_verification" in manifest["summary"]["failed_artifacts"]
    assert any("preflight verification report_path must match" in error for error in manifest["errors"])


def test_ci_evidence_manifest_rejects_tampered_evidence_file(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    paths["deployment_compose_audit"].write_text(
        paths["deployment_compose_audit"].read_text(encoding="utf-8") + "\n{}",
        encoding="utf-8",
    )

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
    )

    assert manifest["status"] == "failed"
    assert "release_evidence_manifest" in manifest["summary"]["failed_artifacts"]
    assert "release_evidence_artifact:deployment_compose_audit" in manifest["summary"]["failed_artifacts"]
    assert any("deployment_compose_audit" in error and "sha256 mismatch" in error for error in manifest["errors"])


def test_ci_evidence_manifest_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    verification = json.loads(paths["preflight_verification"].read_text(encoding="utf-8"))
    verification["status"] = "failed"
    write_json(paths["preflight_verification"], verification)
    output_path = tmp_path / "ci-evidence-manifest.json"

    exit_code = module.main(
        [
            "--preflight-report",
            str(paths["preflight_report"]),
            "--preflight-verification",
            str(paths["preflight_verification"]),
            "--dependency-inventory",
            str(paths["dependency_inventory"]),
            "--evidence-dir",
            str(paths["evidence_dir"]),
            "--output",
            str(output_path),
            "--allow-skipped-frontend",
            "--frontend-build-job",
            "Frontend build",
        ]
    )

    assert exit_code == 1
    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert "release_preflight_verification" in manifest["summary"]["failed_artifacts"]


def test_verify_ci_evidence_manifest_accepts_generated_manifest(tmp_path: Path) -> None:
    manifest_module = load_ci_evidence_manifest_module()
    verify_module = load_verify_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    output_path = tmp_path / "ci-evidence-manifest.json"
    manifest = manifest_module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=output_path,
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )
    write_json(output_path, manifest)

    verification = verify_module.verify_ci_evidence_manifest(output_path)

    assert verification["status"] == "passed"
    assert verification["summary"]["artifact_count"] == manifest["summary"]["artifact_count"]
    assert verification["summary"]["failed_count"] == 0
    assert verification["summary"]["manifest_error_count"] == 0


def test_verify_ci_evidence_manifest_detects_tampered_artifact(tmp_path: Path) -> None:
    manifest_module = load_ci_evidence_manifest_module()
    verify_module = load_verify_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    output_path = tmp_path / "ci-evidence-manifest.json"
    manifest = manifest_module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=output_path,
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )
    write_json(output_path, manifest)
    paths["preflight_report"].write_text(paths["preflight_report"].read_text(encoding="utf-8") + "\n{}", encoding="utf-8")

    verification = verify_module.verify_ci_evidence_manifest(output_path)

    assert verification["status"] == "failed"
    assert verification["summary"]["failed_artifacts"] == ["release_preflight_report"]
    failed_check = next(item for item in verification["checks"] if item["name"] == "release_preflight_report")
    assert "artifact size mismatch" in "\n".join(failed_check["errors"])
    assert "artifact sha256 mismatch" in failed_check["errors"]


def test_verify_ci_evidence_manifest_rejects_unsafe_relative_path(tmp_path: Path) -> None:
    manifest_module = load_ci_evidence_manifest_module()
    verify_module = load_verify_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    output_path = tmp_path / "ci-evidence-manifest.json"
    manifest = manifest_module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=output_path,
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )
    manifest["artifacts"][0]["relative_path"] = "../ci-release-preflight.json"
    write_json(output_path, manifest)

    verification = verify_module.verify_ci_evidence_manifest(output_path)

    assert verification["status"] == "failed"
    assert "release_preflight_report" in verification["summary"]["failed_artifacts"]
    failed_check = next(item for item in verification["checks"] if item["name"] == "release_preflight_report")
    assert "artifact relative_path is unsafe" in failed_check["errors"][0]


def test_verify_ci_evidence_manifest_rejects_skipped_frontend_without_covering_job(tmp_path: Path) -> None:
    manifest_module = load_ci_evidence_manifest_module()
    verify_module = load_verify_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    output_path = tmp_path / "ci-evidence-manifest.json"
    manifest = manifest_module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=output_path,
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )
    manifest["frontend_gate_policy"]["covered_by_ci_job"] = None
    write_json(output_path, manifest)

    verification = verify_module.verify_ci_evidence_manifest(output_path)

    assert verification["status"] == "failed"
    assert "frontend gate was skipped without a covering CI job" in verification["manifest_errors"]


def test_verify_ci_evidence_manifest_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    manifest_module = load_ci_evidence_manifest_module()
    verify_module = load_verify_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    output_path = tmp_path / "ci-evidence-manifest.json"
    verification_output = tmp_path / "ci-evidence-manifest-verification.json"
    manifest = manifest_module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        output_path=output_path,
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )
    manifest["status"] = "failed"
    write_json(output_path, manifest)

    exit_code = verify_module.main(["--manifest", str(output_path), "--output", str(verification_output)])

    assert exit_code == 1
    verification = json.loads(verification_output.read_text(encoding="utf-8"))
    assert verification["status"] == "failed"
    assert "manifest status must be passed, got failed" in verification["manifest_errors"]


def write_complete_ci_evidence(tmp_path: Path) -> dict[str, Path]:
    evidence_dir = tmp_path / "ci-release-evidence"
    evidence_dir.mkdir()
    dependency_inventory = tmp_path / "ci-dependency-inventory.json"
    evidence_dependency_inventory = evidence_dir / "dependency-inventory.json"
    dependency_review = evidence_dir / "dependency-review-audit.json"
    deployment_compose = evidence_dir / "deployment-compose-audit.json"
    repository_hygiene = evidence_dir / "repository-hygiene-audit.json"
    evidence_manifest = evidence_dir / "release-evidence-pack.json"
    evidence_verification = evidence_dir / "release-evidence-verification.json"
    preflight_report = tmp_path / "ci-release-preflight.json"
    preflight_verification = tmp_path / "ci-release-preflight-verification.json"
    handoff_manifest = tmp_path / "ci-release-handoff-bundle.json"
    handoff_verification = tmp_path / "ci-release-handoff-verification.json"

    inventory_payload = {
        "schema_version": 1,
        "status": "passed",
        "summary": {"dependency_count": 2, "review_required_count": 0},
        "dependencies": [{"name": "fastapi"}, {"name": "vue"}],
    }
    write_json(dependency_inventory, inventory_payload)
    write_json(evidence_dependency_inventory, inventory_payload)
    write_json(
        dependency_review,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"review_required_count": 0, "approved_count": 0},
        },
    )
    write_json(
        deployment_compose,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"check_count": 2, "error_count": 0, "warning_count": 0},
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        repository_hygiene,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"required_pattern_count": 5, "missing_pattern_count": 0},
            "errors": [],
            "warnings": [],
        },
    )

    evidence_artifacts = [
        artifact_entry("deployment_compose_audit", deployment_compose, required=True),
        artifact_entry("repository_hygiene_audit", repository_hygiene, required=True),
        artifact_entry("dependency_inventory", evidence_dependency_inventory, required=True),
        artifact_entry("dependency_review_audit", dependency_review, required=False),
    ]
    write_json(
        evidence_manifest,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {"artifact_count": len(evidence_artifacts), "failed_count": 0, "required_failed_count": 0},
            "artifacts": evidence_artifacts,
        },
    )
    write_json(
        evidence_verification,
        {
            "schema_version": 1,
            "status": "passed",
            "manifest_path": str(evidence_manifest.resolve()),
            "summary": {
                "artifact_count": len(evidence_artifacts),
                "verified_count": len(evidence_artifacts),
                "failed_count": 0,
                "skipped_count": 0,
                "manifest_error_count": 0,
                "failed_artifacts": [],
            },
            "manifest_errors": [],
            "checks": [],
        },
    )

    preflight_evidence_payload = {
        "output_dir": str(evidence_dir),
        "manifest_path": str(evidence_manifest),
        "verification_path": str(evidence_verification),
        "manifest_exists": True,
        "pack_status": "passed",
        "pack_summary": {"artifact_count": len(evidence_artifacts), "failed_count": 0, "required_failed_count": 0},
        "artifacts": evidence_artifacts,
        "verification_report_exists": True,
        "verification_status": "passed",
        "verification_summary": {
            "artifact_count": len(evidence_artifacts),
            "verified_count": len(evidence_artifacts),
            "failed_count": 0,
            "skipped_count": 0,
            "manifest_error_count": 0,
            "failed_artifacts": [],
        },
    }
    write_json(
        preflight_report,
        {
            "schema_version": 1,
            "passed": True,
            "options": {
                "skip_frontend": True,
                "skip_evidence_pack": False,
                "skip_smoke": False,
                "env_file": None,
                "require_production_env": False,
                "dependency_review_file": None,
                "require_dependency_review": False,
                "external_acceptance_file": None,
                "require_external_acceptance": False,
            },
            "gates": [
                command_gate("backend release gate tests"),
                command_gate("release evidence pack generation", payload=preflight_evidence_payload),
                command_gate("release evidence pack verification", payload=preflight_evidence_payload),
                {
                    "name": "API health smoke",
                    "kind": "smoke",
                    "status": "passed",
                    "duration_sec": 0.1,
                    "payload": {"port": 8123, "health": "{}", "ready": "{}"},
                },
            ],
            "cleanup": {
                "name": "cleanup pycache",
                "kind": "cleanup",
                "status": "passed",
                "duration_sec": 0.1,
                "payload": {"removed_count": 0},
            },
            "dependency_inventory_summary": {"dependency_count": 2, "review_required_count": 0},
        },
    )
    write_json(
        preflight_verification,
        {
            "schema_version": 1,
            "status": "passed",
            "report_path": str(preflight_report.resolve()),
            "summary": {"gate_count": 4, "error_count": 0, "warning_count": 0, "failed_gates": []},
            "errors": [],
            "warnings": [],
        },
    )
    return {
        "evidence_dir": evidence_dir,
        "dependency_inventory": dependency_inventory,
        "deployment_compose_audit": deployment_compose,
        "repository_hygiene_audit": repository_hygiene,
        "evidence_manifest": evidence_manifest,
        "evidence_verification": evidence_verification,
        "preflight_report": preflight_report,
        "preflight_verification": preflight_verification,
        "handoff_manifest": handoff_manifest,
        "handoff_verification": handoff_verification,
    }


def write_handoff_outputs(paths: dict[str, Path], tmp_path: Path) -> None:
    handoff_module = load_module("release_handoff_bundle_for_ci_evidence", HANDOFF_SCRIPT_PATH)
    verify_handoff_module = load_module("verify_release_handoff_bundle_for_ci_evidence", HANDOFF_VERIFY_SCRIPT_PATH)
    handoff = handoff_module.build_release_handoff_bundle(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
    )
    handoff_module.write_json(paths["handoff_manifest"], handoff)
    for source in paths["evidence_dir"].glob("*.json"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    verification = verify_handoff_module.verify_release_handoff_bundle(paths["handoff_manifest"], base_dir=tmp_path)
    verify_handoff_module.write_json(paths["handoff_verification"], verification)


def command_gate(name: str, payload: dict | None = None) -> dict:
    return {
        "name": name,
        "kind": "command",
        "status": "passed",
        "duration_sec": 0.1,
        "command": ["python"],
        "cwd": str(REPO_ROOT),
        "timeout_sec": 120,
        "exit_code": 0,
        "error": None,
        "payload": payload,
    }


def artifact_entry(name: str, path: Path, *, required: bool) -> dict:
    return {
        "name": name,
        "required": required,
        "status": "passed",
        "relative_path": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
