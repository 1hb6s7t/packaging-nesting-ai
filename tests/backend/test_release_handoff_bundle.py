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
    assert report["summary"]["artifact_count"] == 18
    assert report["summary"]["passed_count"] == 18
    assert by_name["release_preflight_report"]["sha256"] == module.sha256_file(paths["preflight"])
    assert by_name["release_evidence_manifest"]["path"] == str(paths["evidence_manifest"])
    assert by_name["release_evidence_artifact:production_env_audit"]["path"] == str(paths["production_env_audit"])
    assert by_name["production_env_verification"]["path"] == str(paths["production_env_verification"])
    assert by_name["external_acceptance_verification"]["path"] == str(paths["external_acceptance_verification"])
    assert by_name["release_evidence_artifact:deployment_compose_audit"]["path"] == str(
        paths["deployment_compose_audit"]
    )
    assert by_name["release_evidence_artifact:repository_hygiene_audit"]["path"] == str(
        paths["repository_hygiene_audit"]
    )
    assert by_name["release_evidence_artifact:deployment_compose_audit"]["summary"]["manifest_evidence_summary"][
        "policy_contract_status"
    ] == "passed"
    assert by_name["dependency_inventory"]["summary"]["dependency_count"] == 2
    assert by_name["dependency_review_verification"]["path"] == str(paths["dependency_review_verification"])
    assert by_name["dependency_review_verification"]["summary"]["report_status"] == "passed"
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
        release_image_dependency_verification=paths["release_image_dependency_verification"],
    )

    by_name = {item["name"]: item for item in report["artifacts"]}
    assert report["status"] == "passed"
    assert report["summary"]["artifact_count"] == 20
    assert report["summary"]["passed_count"] == 20
    assert by_name["release_image_dependency_audit"]["path"] == str(paths["release_image_dependency_audit"])
    assert by_name["release_image_dependency_audit"]["summary"]["release_blocking_missing_install_count"] == 0
    assert by_name["release_image_dependency_verification"]["path"] == str(
        paths["release_image_dependency_verification"]
    )
    assert by_name["release_image_dependency_verification"]["summary"]["report_status"] == "passed"


def test_release_handoff_bundle_includes_go_live_input_verifications_when_provided(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )

    by_name = {item["name"]: item for item in report["artifacts"]}
    assert report["status"] == "passed"
    assert report["summary"]["artifact_count"] == 18
    assert report["summary"]["passed_count"] == 18
    assert by_name["production_env_verification"]["summary"]["report_status"] == "passed"
    assert by_name["production_env_verification"]["summary"]["rebuilt_report_match"] is True
    assert by_name["external_acceptance_verification"]["summary"]["report_status"] == "passed"
    assert by_name["external_acceptance_verification"]["summary"]["failed_evidence_check_count"] == 0


def test_release_handoff_bundle_fails_when_passed_production_env_audit_lacks_verification(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    paths["production_env_verification"].unlink()

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert any("production_env_verification file does not exist" in error for error in report["errors"])


def test_release_handoff_bundle_fails_when_production_env_verification_points_elsewhere(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["production_env_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-production-env-audit.json").resolve())
    paths["production_env_verification"].write_text(json.dumps(verification, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
    )

    assert report["status"] == "failed"
    assert "production env verification report_path must match production env audit" in report["errors"]


def test_release_handoff_bundle_fails_when_external_acceptance_verification_has_failed_evidence(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["external_acceptance_verification"].read_text(encoding="utf-8"))
    verification["summary"]["failed_evidence_check_count"] = 1
    paths["external_acceptance_verification"].write_text(
        json.dumps(verification, ensure_ascii=False),
        encoding="utf-8",
    )

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )

    assert report["status"] == "failed"
    assert "external acceptance verification has failed evidence checks" in report["errors"]


def test_release_handoff_bundle_fails_when_release_image_dependency_verification_points_elsewhere(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["release_image_dependency_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-release-image-dependency-audit.json").resolve())
    paths["release_image_dependency_verification"].write_text(
        json.dumps(verification, ensure_ascii=False),
        encoding="utf-8",
    )

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
        release_image_dependency_verification=paths["release_image_dependency_verification"],
    )

    assert report["status"] == "failed"
    assert "release image dependency verification report_path must match release image dependency audit" in report["errors"]


def test_release_handoff_bundle_fails_when_release_image_dependency_verification_is_missing(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
    )

    assert report["status"] == "failed"
    assert "release_image_dependency_verification path is missing" in report["errors"]


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
    assert "release image dependency audit must be passed, got failed" in report["errors"]


def test_release_handoff_bundle_fails_when_passed_dependency_review_lacks_verification(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    paths["dependency_review_verification"].unlink()

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert any("dependency_review_verification file does not exist" in error for error in report["errors"])


def test_release_handoff_bundle_fails_when_dependency_review_verification_points_elsewhere(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["dependency_review_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-dependency-review-audit.json").resolve())
    paths["dependency_review_verification"].write_text(
        json.dumps(verification, ensure_ascii=False),
        encoding="utf-8",
    )

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "dependency review verification report_path must match dependency review audit" in report["errors"]


def test_release_handoff_bundle_fails_when_dependency_review_verification_status_drifts(
    tmp_path: Path,
) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    write_json(paths["dependency_review"], skipped_dependency_review_audit())

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "dependency review verification report_status must match dependency review audit" in report["errors"]


def test_release_handoff_bundle_fails_when_dependency_review_summary_drifted(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    review_report = json.loads(paths["dependency_review"].read_text(encoding="utf-8"))
    review_report["summary"]["missing_ack_count"] = 1
    paths["dependency_review"].write_text(json.dumps(review_report, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert any("dependency review audit summary.missing_ack_count must be 0" in error for error in report["errors"])


def test_release_handoff_bundle_allows_skipped_dependency_review_as_optional_warning(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    write_json(paths["dependency_review"], skipped_dependency_review_audit())
    write_json(
        paths["dependency_review_verification"],
        dependency_review_verification(paths["dependency_review"], report_status="skipped"),
    )

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    by_name = {item["name"]: item for item in report["artifacts"]}
    assert report["status"] == "passed"
    assert by_name["dependency_review_audit"]["status"] == "skipped"
    assert by_name["dependency_review_verification"]["status"] == "skipped"
    assert "dependency_review_audit is skipped" in report["warnings"]
    assert "dependency_review_verification is skipped" in report["warnings"]


def test_release_handoff_bundle_fails_when_release_image_policy_contract_failed(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    failed_report = json.loads(paths["release_image_dependency_audit"].read_text(encoding="utf-8"))
    failed_report["summary"]["policy_contract_status"] = "failed"
    failed_report["summary"]["policy_contract_failed_count"] = 1
    paths["release_image_dependency_audit"].write_text(json.dumps(failed_report, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
    )

    assert report["status"] == "failed"
    assert "release image dependency audit policy contract did not pass" in report["errors"]


def test_release_handoff_bundle_fails_when_preflight_verification_points_to_different_report(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["preflight_verification"].read_text(encoding="utf-8"))
    verification["report_path"] = str((tmp_path / "other-release-preflight.json").resolve())
    paths["preflight_verification"].write_text(json.dumps(verification, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "preflight verification report_path must match release preflight report" in report["errors"]


def test_release_handoff_bundle_fails_when_evidence_verification_points_to_different_manifest(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    verification = json.loads(paths["evidence_verification"].read_text(encoding="utf-8"))
    verification["manifest_path"] = str((tmp_path / "other-release-evidence-pack.json").resolve())
    paths["evidence_verification"].write_text(json.dumps(verification, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "release evidence verification manifest_path must match release evidence manifest" in report["errors"]


def test_release_handoff_bundle_fails_when_evidence_manifest_policy_failed(tmp_path: Path) -> None:
    module = load_release_handoff_bundle_module()
    paths = write_complete_handoff_inputs(tmp_path)
    manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    manifest["summary"]["policy_contract_status"] = "failed"
    manifest["summary"]["policy_contract_failed_count"] = 1
    manifest["policy_contract"]["status"] = "failed"
    manifest["policy_contract"]["failed_count"] = 1
    paths["evidence_manifest"].write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
    )

    assert report["status"] == "failed"
    assert "release evidence manifest policy_contract has failed checks" in report["errors"]


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
    dependency_review_verification_path = evidence_dir / "dependency-review-verification.json"
    release_image_dependency_audit = evidence_dir / "release-image-dependency-audit.json"
    release_image_dependency_verification = evidence_dir / "release-image-dependency-verification.json"
    production_env = evidence_dir / "production-env-audit.json"
    production_env_verification = evidence_dir / "production-env-verification.json"
    deployment_compose = evidence_dir / "deployment-compose-audit.json"
    repository_hygiene = evidence_dir / "repository-hygiene-audit.json"
    customer_sandbox = evidence_dir / "customer-sandbox-audit.json"
    notification_channel = evidence_dir / "notification-channel-audit.json"
    storage_export = evidence_dir / "storage-export-audit.json"
    conversion_supplier = evidence_dir / "conversion-supplier-audit.json"
    solver_governance = evidence_dir / "solver-governance-audit.json"
    external_acceptance = evidence_dir / "external-acceptance-audit.json"
    external_acceptance_verification = evidence_dir / "external-acceptance-verification.json"
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
        passed_dependency_review_audit(),
    )
    write_json(
        dependency_review_verification_path,
        dependency_review_verification(dependency_review),
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
        release_image_dependency_verification,
        {
            "schema_version": 1,
            "status": "passed",
            "report_path": str(release_image_dependency_audit.resolve()),
            "report_status": "passed",
            "summary": {
                "command_count": 2,
                "failed_command_count": 0,
                "output_check_count": 2,
                "failed_output_check_count": 0,
                "warning_count": 0,
                "error_count": 0,
            },
            "errors": [],
            "warnings": [],
        },
    )
    write_json(production_env, passed_status_audit({"policy_contract_status": "passed", "policy_contract_failed_count": 0}))
    write_json(
        production_env_verification,
        {
            "schema_version": 1,
            "status": "passed",
            "report_path": str(production_env.resolve()),
            "report_status": "passed",
            "summary": {
                "is_production": True,
                "rebuilt_report_match": True,
                "error_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
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
        customer_sandbox,
        passed_status_audit(
            {
                "pack_contract_status": "passed",
                "pack_contract_failed_count": 0,
                "sync_strategy_status": "passed",
                "sync_strategy_failed_count": 0,
                "business_flow_status": "passed",
                "business_flow_failed_count": 0,
            }
        ),
    )
    write_json(notification_channel, passed_status_audit())
    write_json(
        storage_export,
        passed_status_audit(
            {
                "storage_contract_status": "passed",
                "storage_contract_failed_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
            }
        ),
    )
    write_json(conversion_supplier, passed_status_audit())
    write_json(solver_governance, passed_status_audit())
    write_json(external_acceptance, passed_status_audit({"policy_contract_status": "passed", "policy_contract_failed_count": 0}))
    write_json(
        external_acceptance_verification,
        {
            "schema_version": 1,
            "status": "passed",
            "report_path": str(external_acceptance.resolve()),
            "report_status": "passed",
            "summary": {
                "required_area_count": 5,
                "passed_area_count": 5,
                "verified_evidence_file_count": 5,
                "failed_evidence_check_count": 0,
                "error_count": 0,
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
            },
            "errors": [],
            "warnings": [],
        },
    )

    evidence_artifacts = [
        artifact_entry("production_env_audit", production_env, required=False),
        {
            **artifact_entry("deployment_compose_audit", deployment_compose),
            "summary": {"policy_contract_status": "passed", "policy_contract_failed_count": 0},
        },
        artifact_entry("repository_hygiene_audit", repository_hygiene),
        artifact_entry("customer_sandbox_audit", customer_sandbox),
        artifact_entry("notification_channel_audit", notification_channel),
        artifact_entry("storage_export_audit", storage_export),
        artifact_entry("conversion_supplier_audit", conversion_supplier),
        artifact_entry("solver_governance_audit", solver_governance),
        artifact_entry("external_acceptance_audit", external_acceptance, required=False),
        artifact_entry("dependency_inventory", dependency_inventory),
        artifact_entry("dependency_review_audit", dependency_review, required=False),
    ]
    write_json(
        evidence_manifest,
        {
            "schema_version": 1,
            "status": "passed",
            "summary": {
                "artifact_count": len(evidence_artifacts),
                "required_count": sum(1 for artifact in evidence_artifacts if artifact["required"]),
                "passed_count": len(evidence_artifacts),
                "skipped_count": 0,
                "failed_count": 0,
                "required_failed_count": 0,
                "failed_artifacts": [],
                "skipped_artifacts": [],
                "policy_contract_status": "passed",
                "policy_contract_failed_count": 0,
                "policy_contract_warning_count": 0,
            },
            "artifacts": evidence_artifacts,
            "policy_contract": {"status": "passed", "failed_count": 0, "warning_count": 0, "checks": [{}]},
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
            "report_path": str(preflight.resolve()),
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
        "dependency_review_verification": dependency_review_verification_path,
        "release_image_dependency_audit": release_image_dependency_audit,
        "release_image_dependency_verification": release_image_dependency_verification,
        "production_env_audit": production_env,
        "production_env_verification": production_env_verification,
        "deployment_compose_audit": deployment_compose,
        "repository_hygiene_audit": repository_hygiene,
        "customer_sandbox_audit": customer_sandbox,
        "notification_channel_audit": notification_channel,
        "storage_export_audit": storage_export,
        "conversion_supplier_audit": conversion_supplier,
        "solver_governance_audit": solver_governance,
        "external_acceptance_audit": external_acceptance,
        "external_acceptance_verification": external_acceptance_verification,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def passed_status_audit(summary: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "summary": summary or {"policy_contract_status": "passed", "policy_contract_failed_count": 0},
        "sensitive_scan": {"status": "passed", "failed_count": 0, "findings": []},
        "errors": [],
        "warnings": [],
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


def dependency_review_verification(report_path: Path, *, report_status: str = "passed") -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "report_path": str(report_path.resolve()),
        "report_status": report_status,
        "summary": {
            "report_status": report_status,
            "error_count": 0,
            "warning_count": 0,
        },
        "errors": [],
        "warnings": [],
    }


def skipped_dependency_review_audit() -> dict:
    review_item = {
        "ecosystem": "python",
        "name": "ortools",
        "scope": "runtime",
        "installed": True,
        "version": "9.12.4544",
        "license": None,
        "reason": "missing license metadata",
    }
    return {
        "schema_version": 1,
        "generated_at": "2026-06-29T00:00:00+00:00",
        "status": "skipped",
        "inventory_path": None,
        "review_file": None,
        "options": {"require_review_file": False},
        "summary": {
            "review_required_count": 1,
            "acknowledged_count": 0,
            "approved_count": 0,
            "missing_ack_count": 1,
            "not_approved_count": 0,
            "stale_ack_count": 0,
            "invalid_ack_count": 0,
            "expired_ack_count": 0,
            "unmatched_ack_count": 0,
            "policy_contract_status": "skipped",
            "policy_contract_failed_count": 0,
            "policy_contract_warning_count": 0,
        },
        "errors": [],
        "warnings": ["dependency review file was not provided"],
        "missing": [review_item],
        "not_approved": [],
        "stale": [],
        "invalid": [],
        "expired": [],
        "unmatched": [],
        "policy_contract": {
            "status": "skipped",
            "passed_count": 0,
            "warning_count": 0,
            "failed_count": 0,
            "failed_checks": [],
            "warning_checks": [],
            "checks": [{"code": "review.optional", "status": "skipped"}],
        },
    }


def artifact_entry(name: str, path: Path, *, required: bool = True) -> dict:
    return {
        "name": name,
        "required": required,
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
