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
            summary=evidence_summary("deployment_compose_audit", policy_summary("warning", warnings=1)),
        ),
        artifact(
            "release_evidence_artifact:repository_hygiene_audit",
            "passed",
            summary=evidence_summary("repository_hygiene_audit", policy_summary()),
        ),
        artifact(
            "release_evidence_artifact:production_env_audit",
            "passed",
            summary=evidence_summary("production_env_audit", policy_summary()),
        ),
        artifact(
            "release_evidence_artifact:external_acceptance_audit",
            "passed",
            summary=evidence_summary("external_acceptance_audit", policy_summary()),
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
            "release_image_dependency_audit",
            "passed",
            summary={
                "release_blocking_missing_install_count": 0,
                "dependency_review_status": "passed",
                "error_count": 0,
                **policy_summary(),
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


def evidence_summary(name: str, summary: dict) -> dict:
    return {
        "evidence_artifact_name": name,
        "evidence_artifact_status": "passed",
        "evidence_artifact_required": True,
        "evidence_summary": summary,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
