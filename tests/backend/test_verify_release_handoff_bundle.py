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
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "passed"
    assert verification["summary"]["artifact_count"] == 18
    assert verification["summary"]["verified_count"] == 18
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
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
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
        release_image_dependency_verification=paths["release_image_dependency_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "passed"
    assert verification["summary"]["artifact_count"] == 20
    assert verification["summary"]["verified_count"] == 20


def test_verify_release_handoff_bundle_rejects_release_image_audit_without_verification(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_release_image_missing_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_release_image_missing", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        release_image_dependency_audit=paths["release_image_dependency_audit"],
        release_image_dependency_verification=paths["release_image_dependency_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["artifacts"] = [
        artifact for artifact in report["artifacts"] if artifact["name"] != "release_image_dependency_verification"
    ]
    report["summary"] = bundle_module.build_summary(report["artifacts"], report["errors"], report["warnings"])
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert (
        "handoff manifest artifacts are missing paired entry: release_image_dependency_verification"
        in verification["manifest_errors"]
    )


def test_verify_release_handoff_bundle_rejects_dependency_review_audit_without_verification(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_dependency_review_missing_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_dependency_review_missing", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["artifacts"] = [
        artifact for artifact in report["artifacts"] if artifact["name"] != "dependency_review_verification"
    ]
    report["summary"] = bundle_module.build_summary(report["artifacts"], report["errors"], report["warnings"])
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert (
        "handoff manifest artifacts are missing paired entry: dependency_review_verification"
        in verification["manifest_errors"]
    )


def test_verify_release_handoff_bundle_rejects_production_env_audit_without_verification(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_production_env_missing_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_production_env_missing", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["artifacts"] = [
        artifact for artifact in report["artifacts"] if artifact["name"] != "production_env_verification"
    ]
    report["summary"] = bundle_module.build_summary(report["artifacts"], report["errors"], report["warnings"])
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert (
        "handoff manifest artifacts are missing paired entry: production_env_verification"
        in verification["manifest_errors"]
    )


def test_verify_release_handoff_bundle_rejects_summary_mismatch(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_summary_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_summary", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["summary"]["artifact_count"] = 999
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert any("handoff manifest summary.artifact_count must be 18" in error for error in verification["manifest_errors"])


def test_verify_release_handoff_bundle_rejects_missing_required_manifest_entry(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_missing_entry_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_missing_entry", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["artifacts"] = [
        item for item in report["artifacts"] if item["name"] != "release_evidence_artifact:customer_sandbox_audit"
    ]
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert any(
        "handoff manifest artifacts are missing expected entries: release_evidence_artifact:customer_sandbox_audit"
        in error
        for error in verification["manifest_errors"]
    )


def test_verify_release_handoff_bundle_rejects_duplicate_artifact_names(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_duplicate_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_duplicate", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
    )
    report["artifacts"].append(dict(report["artifacts"][0]))
    bundle_module.write_json(handoff_path, report)

    verification = verify_module.verify_release_handoff_bundle(handoff_path, base_dir=tmp_path)

    assert verification["status"] == "failed"
    assert "handoff manifest has duplicate artifact names: release_preflight_report" in verification["manifest_errors"]


def test_verify_release_handoff_bundle_detects_tampered_artifact(tmp_path: Path) -> None:
    bundle_module = load_module("release_handoff_bundle_for_tamper_verify", BUNDLE_SCRIPT_PATH)
    verify_module = load_module("verify_release_handoff_bundle_for_tamper", VERIFY_SCRIPT_PATH)
    bundle_module.REPO_ROOT = tmp_path
    paths = write_complete_handoff_inputs(tmp_path)
    handoff_path = tmp_path / "release-handoff-bundle.json"
    report = bundle_module.build_release_handoff_bundle(
        preflight_report=paths["preflight"],
        preflight_verification=paths["preflight_verification"],
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
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
        production_env_verification=paths["production_env_verification"],
        external_acceptance_verification=paths["external_acceptance_verification"],
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
    assert "handoff manifest status must be passed, got failed" in report["manifest_errors"]


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
        artifact_entry("deployment_compose_audit", deployment_compose),
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
