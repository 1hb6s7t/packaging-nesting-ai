from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_SCRIPT_PATH = REPO_ROOT / "scripts" / "release_evidence_pack.py"
VERIFY_SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_evidence_pack.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_release_evidence_pack_accepts_generated_pack(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    evidence_module.build_release_evidence_pack(output_dir=output_dir)
    report = verify_module.verify_release_evidence_pack(output_dir / "release-evidence-pack.json")
    manifest = json.loads((output_dir / "release-evidence-pack.json").read_text(encoding="utf-8"))

    assert report["status"] == "passed"
    assert report["summary"]["artifact_count"] == 11
    assert report["summary"]["verified_count"] + report["summary"]["skipped_count"] == 11
    assert report["summary"]["verified_count"] == 10
    assert report["summary"]["skipped_count"] == 1
    assert report["summary"]["failed_count"] == 0
    assert report["manifest_errors"] == []
    assert {item["status"] for item in report["checks"]} <= {"passed", "skipped"}
    assert manifest["summary"]["policy_contract_status"] in {"passed", "warning"}
    assert manifest["summary"]["policy_contract_failed_count"] == 0


def test_verify_release_evidence_pack_uses_relative_paths_after_copy(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_copy_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_copy", VERIFY_SCRIPT_PATH)
    original_dir = tmp_path / "original"
    copied_dir = tmp_path / "copied"

    evidence_module.build_release_evidence_pack(output_dir=original_dir)
    shutil.copytree(original_dir, copied_dir)
    manifest_path = copied_dir / "release-evidence-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        if artifact.get("relative_path"):
            artifact["path"] = str(tmp_path / "stale" / artifact["relative_path"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(manifest_path)

    assert report["status"] == "passed"
    assert report["summary"]["verified_count"] == 10
    assert report["summary"]["skipped_count"] == 1
    assert all(
        item["path"] is None or str(copied_dir) in item["path"]
        for item in report["checks"]
    )


def test_verify_release_evidence_pack_detects_tampered_artifact(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_tamper_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_tamper", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    pack = evidence_module.build_release_evidence_pack(output_dir=output_dir)
    by_name = {item["name"]: item for item in pack["artifacts"]}
    artifact_path = output_dir / by_name["customer_sandbox_audit"]["relative_path"]
    artifact_path.write_text(artifact_path.read_text(encoding="utf-8") + "\n{}", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(output_dir / "release-evidence-pack.json")

    assert report["status"] == "failed"
    assert report["summary"]["failed_artifacts"] == ["customer_sandbox_audit"]
    failed_check = next(item for item in report["checks"] if item["name"] == "customer_sandbox_audit")
    assert "artifact size mismatch" in "\n".join(failed_check["errors"])
    assert "artifact sha256 mismatch" in failed_check["errors"]


def test_verify_release_evidence_pack_checks_generated_optional_artifact_files(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_skipped_tamper_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_skipped_tamper", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    pack = evidence_module.build_release_evidence_pack(output_dir=output_dir)
    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert by_name["external_acceptance_audit"]["status"] == "skipped"
    artifact_path = output_dir / by_name["external_acceptance_audit"]["relative_path"]
    artifact_path.write_text(artifact_path.read_text(encoding="utf-8") + "\n{}", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(output_dir / "release-evidence-pack.json")

    assert report["status"] == "failed"
    assert report["summary"]["failed_artifacts"] == ["external_acceptance_audit"]
    failed_check = next(item for item in report["checks"] if item["name"] == "external_acceptance_audit")
    assert failed_check["artifact_status"] == "skipped"
    assert "artifact size mismatch" in "\n".join(failed_check["errors"])
    assert "artifact sha256 mismatch" in failed_check["errors"]


def test_verify_release_evidence_pack_rejects_unsafe_relative_path(tmp_path: Path) -> None:
    verify_module = load_module("verify_release_evidence_pack_for_unsafe_path", VERIFY_SCRIPT_PATH)
    manifest_path = tmp_path / "release-evidence-pack.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "artifacts": [
                    {
                        "name": "unsafe",
                        "status": "passed",
                        "path": None,
                        "relative_path": "../outside.json",
                        "size_bytes": 1,
                        "sha256": "0" * 64,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_module.verify_release_evidence_pack(manifest_path)

    assert report["status"] == "failed"
    assert report["summary"]["failed_artifacts"] == ["unsafe"]
    assert "unsafe" in report["checks"][0]["errors"][0]


def test_verify_release_evidence_pack_rejects_failed_manifest_policy_contract(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_policy_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_policy", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    evidence_module.build_release_evidence_pack(output_dir=output_dir)
    manifest_path = output_dir / "release-evidence-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy_contract"]["status"] = "failed"
    manifest["policy_contract"]["failed_count"] = 1
    manifest["summary"]["policy_contract_status"] = "failed"
    manifest["summary"]["policy_contract_failed_count"] = 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(manifest_path)

    assert report["status"] == "failed"
    assert "manifest policy_contract must be passed or warning, got failed" in report["manifest_errors"]
    assert "manifest policy_contract has failed checks" in report["manifest_errors"]


def test_verify_release_evidence_pack_rejects_manifest_policy_summary_mismatch(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_policy_summary_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_policy_summary", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    evidence_module.build_release_evidence_pack(output_dir=output_dir)
    manifest_path = output_dir / "release-evidence-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["summary"]["policy_contract_failed_count"] = 9
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(manifest_path)

    assert report["status"] == "failed"
    assert "manifest summary policy_contract_failed_count does not match policy_contract failed_count" in report["manifest_errors"]


def test_verify_release_evidence_pack_rejects_failed_artifact_policy_summary(tmp_path: Path) -> None:
    evidence_module = load_module("release_evidence_pack_for_artifact_policy_verify", EVIDENCE_SCRIPT_PATH)
    verify_module = load_module("verify_release_evidence_pack_for_artifact_policy", VERIFY_SCRIPT_PATH)
    output_dir = tmp_path / "evidence"

    evidence_module.build_release_evidence_pack(output_dir=output_dir)
    manifest_path = output_dir / "release-evidence-pack.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(item for item in manifest["artifacts"] if item["name"] == "repository_hygiene_audit")
    artifact["summary"]["policy_contract_status"] = "failed"
    artifact["summary"]["policy_contract_failed_count"] = 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = verify_module.verify_release_evidence_pack(manifest_path)

    assert report["status"] == "failed"
    assert "repository_hygiene_audit policy_contract_status must be one of ['passed', 'warning'], got failed" in report["manifest_errors"]
    assert "repository_hygiene_audit policy_contract_failed_count must be 0" in report["manifest_errors"]


def test_verify_release_evidence_pack_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    verify_module = load_module("verify_release_evidence_pack_for_cli", VERIFY_SCRIPT_PATH)
    manifest_path = tmp_path / "release-evidence-pack.json"
    output_path = tmp_path / "verification.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "status": "failed", "artifacts": []}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    exit_code = verify_module.main(["--manifest", str(manifest_path), "--output", str(output_path)])

    assert exit_code == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["summary"]["manifest_error_count"] >= 1
    assert "manifest policy_contract is missing or invalid" in report["manifest_errors"]
    assert any("missing expected entries" in error for error in report["manifest_errors"])
