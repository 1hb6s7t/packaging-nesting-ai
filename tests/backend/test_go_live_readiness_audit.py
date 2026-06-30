from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "go_live_readiness_audit.py"


def load_go_live_readiness_audit_module():
    spec = importlib.util.spec_from_file_location("go_live_readiness_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_go_live_readiness_audit_accepts_complete_handoff(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    write_json(handoff_path, complete_handoff_manifest())
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "passed"
    assert report["summary"]["blocker_count"] == 0
    assert report["summary"]["failed_check_count"] == 0
    assert report["summary"]["policy_contract_status"] == "passed"
    assert report["summary"]["policy_contract_failed_count"] == 0
    assert report["policy_contract"]["status"] == "passed"
    assert report["warnings"] == []


def test_go_live_readiness_audit_rejects_local_handoff_with_skipped_go_live_evidence(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["release_evidence_artifact:production_env_audit"]["status"] = "skipped"
    by_name["release_evidence_artifact:external_acceptance_audit"]["status"] = "skipped"
    by_name["dependency_review_audit"]["status"] = "skipped"
    by_name["dependency_inventory"]["summary"]["missing_install_count"] = 4
    by_name["dependency_inventory"]["summary"]["review_required_count"] = 4
    by_name["dependency_review_audit"]["summary"] = {"review_required_count": 4, "approved_count": 0}
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert report["summary"]["policy_contract_status"] == "failed"
    assert "production env audit artifact must be passed, got skipped" in report["blockers"]
    assert "external acceptance audit artifact must be passed, got skipped" in report["blockers"]
    assert "dependency review audit artifact must be passed, got skipped" in report["blockers"]
    assert any("missing installed package" in blocker for blocker in report["blockers"])
    assert report["warnings"] == []


def test_go_live_readiness_audit_requires_handoff_verification(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    write_json(handoff_path, complete_handoff_manifest())

    report = module.build_go_live_readiness_audit(handoff_manifest=handoff_path)

    assert report["status"] == "failed"
    assert "handoff verification was not provided" in report["blockers"]
    assert report["warnings"] == []


def test_go_live_readiness_audit_rejects_failed_handoff_verification(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    write_json(handoff_path, complete_handoff_manifest())
    write_json(
        verification_path,
        {
            "schema_version": 1,
            "status": "failed",
            "manifest_path": str(handoff_path.resolve()),
            "summary": {"failed_count": 1, "manifest_error_count": 0},
        },
    )

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "handoff verification status must be passed, got failed" in report["blockers"]
    assert "handoff verification has failed artifact checks" in report["blockers"]


def test_go_live_readiness_audit_rejects_handoff_verification_for_different_manifest(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    other_handoff_path = tmp_path / "other-release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    write_json(handoff_path, complete_handoff_manifest())
    write_json(other_handoff_path, complete_handoff_manifest())
    write_json(verification_path, handoff_verification(other_handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert any("handoff verification manifest_path must match handoff manifest" in item for item in report["blockers"])


def test_go_live_readiness_audit_requires_repository_hygiene_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [
        item for item in manifest["artifacts"] if item["name"] != "release_evidence_artifact:repository_hygiene_audit"
    ]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "repository hygiene audit artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_requires_customer_sandbox_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [
        item for item in manifest["artifacts"] if item["name"] != "release_evidence_artifact:customer_sandbox_audit"
    ]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "customer sandbox audit artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_requires_release_image_dependency_audit_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "release_image_dependency_audit"]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "release image dependency audit artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_requires_release_image_dependency_verification_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [
        item for item in manifest["artifacts"] if item["name"] != "release_image_dependency_verification"
    ]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "release image dependency verification artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_requires_dependency_review_verification_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "dependency_review_verification"]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "dependency review verification artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_requires_production_env_verification_artifact(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    manifest["artifacts"] = [item for item in manifest["artifacts"] if item["name"] != "production_env_verification"]
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "production env verification artifact is missing" in report["blockers"]


def test_go_live_readiness_audit_rejects_production_env_verification_without_rebuild_match(
    tmp_path: Path,
) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["production_env_verification"]["summary"]["rebuilt_report_match"] = False
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "production env verification must match the supplied env file" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_external_acceptance_verification_summary(
    tmp_path: Path,
) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["external_acceptance_verification"]["summary"]["failed_evidence_check_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "external acceptance verification has failed evidence checks" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_release_image_dependency_verification_summary(
    tmp_path: Path,
) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["release_image_dependency_verification"]["summary"]["failed_output_check_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "release image dependency verification has failed output checks" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_dependency_review_verification_summary(
    tmp_path: Path,
) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["dependency_review_verification"]["summary"]["error_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "dependency review verification summary has errors" in report["blockers"]


def test_go_live_readiness_audit_rejects_skipped_dependency_review_verification_summary(
    tmp_path: Path,
) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["dependency_review_verification"]["summary"]["report_status"] = "skipped"
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "dependency review verification report_status must be passed" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_release_image_policy_contract(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["release_image_dependency_audit"]["summary"]["policy_contract_status"] = "failed"
    by_name["release_image_dependency_audit"]["summary"]["policy_contract_failed_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "release image dependency audit policy contract has failed checks" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_nested_artifact_contract(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    summary = by_name["release_evidence_artifact:customer_sandbox_audit"]["summary"]["manifest_evidence_summary"]
    summary["sync_strategy_status"] = "failed"
    summary["sync_strategy_failed_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "customer sandbox audit sync_strategy has failed checks" in report["blockers"]


def test_go_live_readiness_audit_rejects_failed_sensitive_scan_summary(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    summary = by_name["release_evidence_artifact:storage_export_audit"]["summary"]["manifest_evidence_summary"]
    summary["sensitive_scan_status"] = "failed"
    summary["sensitive_scan_failed_count"] = 1
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "failed"
    assert "storage export audit sensitive scan has failed findings" in report["blockers"]


def test_go_live_readiness_audit_allows_non_blocking_missing_test_extra(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["dependency_inventory"]["summary"]["missing_install_count"] = 1
    by_name["dependency_inventory"]["summary"]["release_blocking_missing_install_count"] = 0
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    report = module.build_go_live_readiness_audit(
        handoff_manifest=handoff_path,
        handoff_verification=verification_path,
    )

    assert report["status"] == "passed"
    assert not any("missing installed package" in blocker for blocker in report["blockers"])


def test_go_live_readiness_audit_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    module = load_go_live_readiness_audit_module()
    handoff_path = tmp_path / "release-handoff-bundle.json"
    verification_path = tmp_path / "release-handoff-verification.json"
    output_path = tmp_path / "go-live-readiness.json"
    manifest = complete_handoff_manifest()
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    by_name["release_evidence_artifact:external_acceptance_audit"]["status"] = "skipped"
    write_json(handoff_path, manifest)
    write_json(verification_path, handoff_verification(handoff_path))

    exit_code = module.main(
        [
            "--handoff-manifest",
            str(handoff_path),
            "--handoff-verification",
            str(verification_path),
            "--output",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["summary"]["policy_contract_status"] == "failed"
    assert any("external acceptance audit" in blocker for blocker in report["blockers"])


def complete_handoff_manifest() -> dict:
    artifacts = [
        artifact("release_preflight_report", "passed"),
        artifact("release_preflight_verification", "passed"),
        artifact("release_evidence_manifest", "passed"),
        artifact("release_evidence_verification", "passed"),
        artifact(
            "release_evidence_artifact:deployment_compose_audit",
            "passed",
            summary=evidence_summary("deployment_compose_audit", {**policy_summary("warning", warnings=1), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:repository_hygiene_audit",
            "passed",
            summary=evidence_summary("repository_hygiene_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:customer_sandbox_audit",
            "passed",
            summary=evidence_summary(
                "customer_sandbox_audit",
                {
                    "pack_contract_status": "passed",
                    "pack_contract_failed_count": 0,
                    "pack_contract_warning_count": 0,
                    "sync_strategy_status": "passed",
                    "sync_strategy_failed_count": 0,
                    "sync_strategy_warning_count": 0,
                    "business_flow_status": "passed",
                    "business_flow_failed_count": 0,
                    "business_flow_warning_count": 0,
                    **sensitive_summary(),
                },
            ),
        ),
        artifact(
            "release_evidence_artifact:notification_channel_audit",
            "passed",
            summary=evidence_summary("notification_channel_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:storage_export_audit",
            "passed",
            summary=evidence_summary(
                "storage_export_audit",
                {
                    "storage_contract_status": "passed",
                    "storage_contract_failed_count": 0,
                    "storage_contract_warning_count": 0,
                    **policy_summary(),
                    **sensitive_summary(),
                },
            ),
        ),
        artifact(
            "release_evidence_artifact:conversion_supplier_audit",
            "passed",
            summary=evidence_summary("conversion_supplier_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:solver_governance_audit",
            "passed",
            summary=evidence_summary("solver_governance_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:production_env_audit",
            "passed",
            summary=evidence_summary("production_env_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "release_evidence_artifact:external_acceptance_audit",
            "passed",
            summary=evidence_summary("external_acceptance_audit", {**policy_summary(), **sensitive_summary()}),
        ),
        artifact(
            "production_env_verification",
            "passed",
            summary={
                "report_status": "passed",
                "error_count": 0,
                "rebuilt_report_match": True,
            },
        ),
        artifact(
            "external_acceptance_verification",
            "passed",
            summary={
                "report_status": "passed",
                "error_count": 0,
                "failed_evidence_check_count": 0,
            },
        ),
        artifact(
            "dependency_inventory",
            "passed",
            summary={
                "dependency_count": 10,
                "missing_install_count": 0,
                "review_required_count": 2,
            },
        ),
        artifact(
            "dependency_review_audit",
            "passed",
            summary={
                "review_required_count": 2,
                "approved_count": 2,
                **policy_summary(),
            },
        ),
        artifact(
            "dependency_review_verification",
            "passed",
            summary={
                "report_status": "passed",
                "error_count": 0,
            },
        ),
        artifact(
            "release_image_dependency_audit",
            "passed",
            summary={
                "release_blocking_missing_install_count": 0,
                "dependency_review_status": "passed",
                "error_count": 0,
                **policy_summary(),
            },
        ),
        artifact(
            "release_image_dependency_verification",
            "passed",
            summary={
                "report_status": "passed",
                "error_count": 0,
                "failed_output_check_count": 0,
            },
        ),
    ]
    return {
        "schema_version": 1,
        "status": "passed",
        "summary": {"artifact_count": len(artifacts), "error_count": 0},
        "artifacts": artifacts,
    }


def handoff_verification(manifest_path: Path) -> dict:
    return {
        "schema_version": 1,
        "status": "passed",
        "manifest_path": str(manifest_path.resolve()),
        "summary": {"failed_count": 0, "manifest_error_count": 0},
    }


def artifact(name: str, status: str, *, summary: dict | None = None) -> dict:
    return {
        "name": name,
        "required": True,
        "status": status,
        "summary": summary or {},
        "relative_path": f"{name}.json".replace(":", "-"),
        "size_bytes": 100,
        "sha256": "a" * 64,
    }


def policy_summary(status: str = "passed", *, failed: int = 0, warnings: int = 0) -> dict:
    return {
        "policy_contract_status": status,
        "policy_contract_failed_count": failed,
        "policy_contract_warning_count": warnings,
    }


def sensitive_summary(status: str = "passed", *, failed: int = 0) -> dict:
    return {
        "sensitive_scan_status": status,
        "sensitive_scan_failed_count": failed,
    }


def evidence_summary(name: str, summary: dict) -> dict:
    return {
        "evidence_artifact_name": name,
        "evidence_artifact_status": "passed",
        "evidence_artifact_required": True,
        "manifest_evidence_summary": summary,
        "evidence_summary": summary,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
