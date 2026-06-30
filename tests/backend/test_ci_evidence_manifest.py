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
PRODUCTION_ENV_BLOCKER = "production env audit artifact must be passed, got skipped"
EXTERNAL_ACCEPTANCE_BLOCKER = "external acceptance audit artifact must be passed, got skipped"


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


def test_ci_evidence_manifest_includes_release_image_dependency_artifacts_when_provided(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        dependency_review_audit=paths["dependency_review_audit"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
        evidence_dir=paths["evidence_dir"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest["status"] == "passed"
    assert by_name["dependency_review_audit"]["sha256"] == sha256_file(paths["dependency_review_audit"])
    assert by_name["release_image_dependency_audit"]["sha256"] == sha256_file(paths["release_image_dependency_audit"])
    assert by_name["release_image_dependency_audit"]["summary"]["release_blocking_missing_install_count"] == 0


def test_ci_evidence_manifest_rejects_failed_release_image_policy_contract(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    failed_report = json.loads(paths["release_image_dependency_audit"].read_text(encoding="utf-8"))
    failed_report["summary"]["policy_contract_status"] = "failed"
    failed_report["summary"]["policy_contract_failed_count"] = 1
    paths["release_image_dependency_audit"].write_text(json.dumps(failed_report, ensure_ascii=False), encoding="utf-8")

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        dependency_review_audit=paths["dependency_review_audit"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
        evidence_dir=paths["evidence_dir"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    artifact = next(item for item in manifest["artifacts"] if item["name"] == "release_image_dependency_audit")
    assert manifest["status"] == "failed"
    assert artifact["status"] == "failed"
    assert "release image dependency audit policy contract did not pass" in artifact["errors"]


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


def test_ci_evidence_manifest_includes_go_live_readiness_when_provided(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    write_handoff_outputs(paths, tmp_path)
    write_go_live_readiness_outputs(paths)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        handoff_manifest=paths["handoff_manifest"],
        handoff_verification=paths["handoff_verification"],
        go_live_readiness=paths["go_live_readiness"],
        go_live_readiness_verification=paths["go_live_readiness_verification"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest["status"] == "passed"
    assert by_name["go_live_readiness_report"]["sha256"] == sha256_file(paths["go_live_readiness"])
    assert by_name["go_live_readiness_report"]["summary"]["readiness_status"] == "failed"
    assert by_name["go_live_readiness_verification"]["sha256"] == sha256_file(paths["go_live_readiness_verification"])
    assert by_name["go_live_readiness_verification"]["summary"]["error_count"] == 0


def test_ci_evidence_manifest_rejects_go_live_verification_for_different_report(tmp_path: Path) -> None:
    module = load_ci_evidence_manifest_module()
    paths = write_complete_ci_evidence(tmp_path)
    write_handoff_outputs(paths, tmp_path)
    write_go_live_readiness_outputs(paths)
    verification = json.loads(paths["go_live_readiness_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-go-live-readiness.json").resolve())
    write_json(paths["go_live_readiness_verification"], verification)

    manifest = module.build_ci_evidence_manifest(
        preflight_report=paths["preflight_report"],
        preflight_verification=paths["preflight_verification"],
        dependency_inventory=paths["dependency_inventory"],
        evidence_dir=paths["evidence_dir"],
        handoff_manifest=paths["handoff_manifest"],
        handoff_verification=paths["handoff_verification"],
        go_live_readiness=paths["go_live_readiness"],
        go_live_readiness_verification=paths["go_live_readiness_verification"],
        output_path=tmp_path / "ci-evidence-manifest.json",
        allow_skipped_frontend=True,
        frontend_build_job="Frontend build",
        frontend_artifact_name="frontend-dist-testsha",
    )

    assert manifest["status"] == "failed"
    assert "go_live_readiness_verification" in manifest["summary"]["failed_artifacts"]
    assert any("go-live readiness verification report_path must match" in error for error in manifest["errors"])


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
    dependency_review_verification = evidence_dir / "dependency-review-verification.json"
    release_image_dependency_audit = tmp_path / "ci-release-image-dependency-audit.json"
    customer_sandbox = evidence_dir / "customer-sandbox-audit.json"
    notification_channel = evidence_dir / "notification-channel-audit.json"
    storage_export = evidence_dir / "storage-export-audit.json"
    conversion_supplier = evidence_dir / "conversion-supplier-audit.json"
    solver_governance = evidence_dir / "solver-governance-audit.json"
    external_acceptance = evidence_dir / "external-acceptance-audit.json"
    deployment_compose = evidence_dir / "deployment-compose-audit.json"
    repository_hygiene = evidence_dir / "repository-hygiene-audit.json"
    evidence_manifest = evidence_dir / "release-evidence-pack.json"
    evidence_verification = evidence_dir / "release-evidence-verification.json"
    preflight_report = tmp_path / "ci-release-preflight.json"
    preflight_verification = tmp_path / "ci-release-preflight-verification.json"
    handoff_manifest = tmp_path / "ci-release-handoff-bundle.json"
    handoff_verification = tmp_path / "ci-release-handoff-verification.json"
    go_live_readiness = tmp_path / "ci-go-live-readiness.json"
    go_live_readiness_verification = tmp_path / "ci-go-live-readiness-verification.json"

    inventory_payload = {
        "schema_version": 1,
        "status": "passed",
        "summary": {"dependency_count": 2, "review_required_count": 0},
        "dependencies": [{"name": "fastapi"}, {"name": "vue"}],
        "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
    }
    write_json(dependency_inventory, inventory_payload)
    write_json(evidence_dependency_inventory, inventory_payload)
    write_json(
        dependency_review,
        passed_dependency_review_audit(),
    )
    write_json(
        dependency_review_verification,
        {
            "schema_version": 1,
            "generated_at": "2026-06-29T00:00:00+00:00",
            "report_path": str(dependency_review.resolve()),
            "report_status": "passed",
            "status": "passed",
            "summary": {
                "review_required_count": 0,
                "acknowledged_count": 0,
                "approved_count": 0,
                "source_error_count": 0,
                "source_warning_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
                "policy_contract_warning_count": 0,
                "error_count": 0,
            },
            "errors": [],
            "warnings": [],
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
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
                "policy_contract_warning_count": 0,
            },
            "policy_contract": {"status": "passed", "failed_count": 0, "warning_count": 0, "checks": []},
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        deployment_compose,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {
                "check_count": 2,
                "error_count": 0,
                "warning_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
                "policy_contract_warning_count": 0,
            },
            "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
            "errors": [],
            "warnings": [],
        },
    )
    write_json(
        repository_hygiene,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {
                "required_pattern_count": 5,
                "missing_pattern_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
                "policy_contract_warning_count": 0,
            },
            "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
            "errors": [],
            "warnings": [],
        },
    )
    for path, status, summary in [
        (
            customer_sandbox,
            "passed",
            {
                "adapter_failed_count": 0,
                "pack_contract_status": "passed",
                "pack_contract_failed_count": 0,
                "sync_strategy_status": "passed",
                "sync_strategy_failed_count": 0,
                "business_flow_status": "passed",
                "business_flow_failed_count": 0,
            },
        ),
        (notification_channel, "passed", {"failed_count": 0, "policy_contract_status": "passed", "policy_contract_failed_count": 0}),
        (
            storage_export,
            "passed",
            {
                "failed_count": 0,
                "storage_contract_status": "passed",
                "storage_contract_failed_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
            },
        ),
        (conversion_supplier, "passed", {"failed_count": 0, "policy_contract_status": "passed", "policy_contract_failed_count": 0}),
        (solver_governance, "passed", {"failed_count": 0, "policy_contract_status": "passed", "policy_contract_failed_count": 0}),
        (external_acceptance, "skipped", {"required_area_count": 5, "passed_area_count": 0, "policy_contract_status": "skipped", "policy_contract_failed_count": 0}),
    ]:
        write_json(
            path,
            {
                "schema_version": 1,
                "status": status,
                "summary": summary,
                "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
            },
        )

    evidence_artifacts = [
        {
            "name": "production_env_audit",
            "required": False,
            "status": "skipped",
            "relative_path": None,
            "path": None,
            "size_bytes": None,
            "sha256": None,
            "summary": {"reason": "--env-file was not provided"},
        },
        artifact_entry("deployment_compose_audit", deployment_compose, required=True),
        artifact_entry("repository_hygiene_audit", repository_hygiene, required=True),
        artifact_entry("customer_sandbox_audit", customer_sandbox, required=True),
        artifact_entry("notification_channel_audit", notification_channel, required=True),
        artifact_entry("storage_export_audit", storage_export, required=True),
        artifact_entry("conversion_supplier_audit", conversion_supplier, required=True),
        artifact_entry("solver_governance_audit", solver_governance, required=True),
        {**artifact_entry("external_acceptance_audit", external_acceptance, required=False), "status": "skipped"},
        artifact_entry("dependency_inventory", evidence_dependency_inventory, required=True),
        artifact_entry("dependency_review_audit", dependency_review, required=False),
    ]
    evidence_summary = {
        "artifact_count": len(evidence_artifacts),
        "required_count": 8,
        "passed_count": 9,
        "failed_count": 0,
        "required_failed_count": 0,
        "skipped_count": 2,
        "failed_artifacts": [],
        "skipped_artifacts": ["production_env_audit", "external_acceptance_audit"],
        "policy_contract_status": "passed",
        "policy_contract_failed_count": 0,
        "policy_contract_warning_count": 0,
    }
    evidence_policy_contract = {
        "status": "passed",
        "passed_count": 1,
        "warning_count": 0,
        "failed_count": 0,
        "failed_checks": [],
        "warning_checks": [],
        "checks": [
            {
                "code": "fixture.delivery_contract",
                "status": "passed",
                "severity": "info",
                "message": "fixture release evidence manifest satisfies the delivery contract",
                "evidence": {},
            }
        ],
    }
    write_json(
        evidence_manifest,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": evidence_summary,
            "artifacts": evidence_artifacts,
            "policy_contract": evidence_policy_contract,
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
                "verified_count": 10,
                "failed_count": 0,
                "skipped_count": 1,
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
        "pack_summary": evidence_summary,
        "artifacts": evidence_artifacts,
        "verification_report_exists": True,
        "verification_status": "passed",
        "verification_summary": {
            "artifact_count": len(evidence_artifacts),
            "verified_count": 10,
            "failed_count": 0,
            "skipped_count": 1,
            "manifest_error_count": 0,
            "failed_artifacts": [],
        },
    }
    dependency_review_verification_payload = {
        "path": str(dependency_review_verification),
        "exists": True,
        "status": "passed",
        "report_status": "passed",
        "report_path": str(dependency_review.resolve()),
        "summary": {
            "review_required_count": 0,
            "acknowledged_count": 0,
            "approved_count": 0,
            "error_count": 0,
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
                "skip_benchmark_gate": False,
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
                command_gate("benchmark release gate", payload=benchmark_gate_payload()),
                command_gate("release evidence pack generation", payload=preflight_evidence_payload),
                command_gate("release evidence pack verification", payload=preflight_evidence_payload),
                command_gate(
                    "release evidence dependency review verification",
                    payload=dependency_review_verification_payload,
                ),
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
            "summary": {"gate_count": 6, "error_count": 0, "warning_count": 0, "failed_gates": []},
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
        "dependency_review_audit": dependency_review,
        "dependency_review_verification": dependency_review_verification,
        "release_image_dependency_audit": release_image_dependency_audit,
        "preflight_report": preflight_report,
        "preflight_verification": preflight_verification,
        "handoff_manifest": handoff_manifest,
        "handoff_verification": handoff_verification,
        "go_live_readiness": go_live_readiness,
        "go_live_readiness_verification": go_live_readiness_verification,
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


def write_go_live_readiness_outputs(paths: dict[str, Path]) -> None:
    blockers = [PRODUCTION_ENV_BLOCKER, EXTERNAL_ACCEPTANCE_BLOCKER]
    checks = [
        {"name": "handoff_manifest", "status": "passed", "summary": {}, "errors": []},
        {"name": "go_live_evidence", "status": "failed", "summary": {}, "errors": blockers},
    ]
    write_json(
        paths["go_live_readiness"],
        {
            "schema_version": 1,
            "generated_at": "2026-06-29T00:00:00+00:00",
            "status": "failed",
            "handoff_manifest": str(paths["handoff_manifest"].resolve()),
            "handoff_verification": str(paths["handoff_verification"].resolve()),
            "summary": {
                "check_count": len(checks),
                "passed_check_count": 1,
                "failed_check_count": 1,
                "blocker_count": len(blockers),
                "warning_count": 0,
            },
            "blockers": blockers,
            "warnings": [],
            "checks": checks,
        },
    )
    write_json(
        paths["go_live_readiness_verification"],
        {
            "schema_version": 1,
            "generated_at": "2026-06-29T00:00:00+00:00",
            "report_path": str(paths["go_live_readiness"].resolve()),
            "report_status": "failed",
            "status": "passed",
            "summary": {
                "check_count": len(checks),
                "blocker_count": len(blockers),
                "allowed_blocker_count": len(blockers),
                "unexpected_blocker_count": 0,
                "missing_allowed_blocker_count": 0,
                "warning_count": 0,
                "error_count": 0,
            },
            "allowed_blockers": blockers,
            "unexpected_blockers": [],
            "missing_allowed_blockers": [],
            "errors": [],
            "warnings": [],
        },
    )


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


def benchmark_gate_payload() -> dict:
    return {
        "report_path": "tmp/ci-release-evidence/benchmark-release-gate.json",
        "exists": True,
        "status": "passed",
        "thresholds": {
            "min_quantity_fulfillment_rate": 1.0,
            "max_p95_runtime_ms": 2000,
            "max_total_runtime_ms": 15000,
            "max_peak_rss_mb": None,
        },
        "case_count": 6,
        "coverage": {
            "or_dataset": True,
            "sheet_787x1092": True,
            "moq_1000": True,
            "quantity_levels": [1000, 3000, 5000, 10000, 15000],
            "planning_modes": ["expanded", "pattern"],
            "case_sources": ["or_dataset", "release_quantity_ladder"],
        },
        "summary": {
            "case_count": 6,
            "passed_case_count": 6,
            "failed_case_count": 0,
            "quantity_levels": [1000, 3000, 5000, 10000, 15000],
            "planning_modes": ["expanded", "pattern"],
            "min_quantity_fulfillment_rate": 1.0,
            "p95_runtime_ms": 50,
            "total_runtime_ms": 200,
            "wall_time_ms": 250,
            "peak_rss_mb": 200.0,
            "error_count": 0,
        },
    }


def artifact_entry(name: str, path: Path, *, required: bool) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = dict(payload.get("summary") or {})
    sensitive_scan = payload.get("sensitive_scan") if isinstance(payload.get("sensitive_scan"), dict) else {}
    if sensitive_scan:
        summary["sensitive_scan_status"] = sensitive_scan.get("status")
        summary["sensitive_scan_failed_count"] = sensitive_scan.get("failed_count", 0)
    return {
        "name": name,
        "required": required,
        "status": payload.get("status") or "passed",
        "relative_path": path.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "summary": summary,
    }


def passed_dependency_review_audit() -> dict:
    policy_codes = [
        "schema.version",
        "inventory.review_required",
        "review.file.present",
        "review.document",
        "review.coverage",
        "review.decision",
        "review.current",
        "review.metadata",
        "review.scope",
    ]
    return {
        "schema_version": 1,
        "generated_at": "2026-06-29T00:00:00+00:00",
        "status": "passed",
        "inventory_path": None,
        "review_file": None,
        "options": {"require_review_file": False},
        "summary": {
            "review_required_count": 0,
            "acknowledged_count": 0,
            "approved_count": 0,
            "missing_ack_count": 0,
            "not_approved_count": 0,
            "stale_ack_count": 0,
            "invalid_ack_count": 0,
            "expired_ack_count": 0,
            "unmatched_ack_count": 0,
            "policy_contract_status": "passed",
            "policy_contract_failed_count": 0,
            "policy_contract_warning_count": 0,
        },
        "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
        "errors": [],
        "warnings": [],
        "missing": [],
        "not_approved": [],
        "stale": [],
        "invalid": [],
        "expired": [],
        "unmatched": [],
        "policy_contract": {
            "status": "passed",
            "passed_count": len(policy_codes),
            "warning_count": 0,
            "failed_count": 0,
            "failed_checks": [],
            "warning_checks": [],
            "checks": [{"code": code, "status": "passed"} for code in policy_codes],
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
