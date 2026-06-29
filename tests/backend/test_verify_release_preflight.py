from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_release_preflight.py"


def load_verify_release_preflight_module():
    spec = importlib.util.spec_from_file_location("verify_release_preflight", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_release_preflight_accepts_complete_report_with_dependency_warning(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(valid_preflight_report(), ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "passed"
    assert report["summary"]["gate_count"] == 5
    assert report["summary"]["passed_gate_count"] == 5
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["warning_count"] == 1
    assert "dependency inventory has 1 review-required item(s)" in report["warnings"]


def test_verify_release_preflight_explains_local_missing_dependency_warning(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["dependency_inventory_summary"] = local_missing_dependency_summary()
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "passed"
    assert report["summary"]["warning_count"] == 1
    assert (
        "dependency inventory has 2 review-required item(s) because "
        "2 release-blocking package(s) are missing in this environment"
    ) in report["warnings"][0]
    assert "release image dependency inventory" in report["warnings"][0]


def test_verify_release_preflight_rejects_missing_evidence_payload(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        if gate["name"] == "release evidence pack verification":
            gate["payload"] = None
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "release evidence pack verification payload is missing" in report["errors"]


def test_verify_release_preflight_requires_default_release_gates_unless_allowed(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["skip_frontend"] = True
    payload["options"]["skip_smoke"] = True
    payload["options"]["skip_evidence_pack"] = True
    payload["gates"] = [payload["gates"][0]]
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    strict_report = module.verify_release_preflight_report(report_path)
    allowed_report = module.verify_release_preflight_report(
        report_path,
        require_evidence_pack=False,
        require_frontend=False,
        require_smoke=False,
    )

    assert strict_report["status"] == "failed"
    assert "release evidence pack gate was skipped" in strict_report["errors"]
    assert "frontend build was skipped" in strict_report["errors"]
    assert "API health smoke was skipped" in strict_report["errors"]
    assert allowed_report["status"] == "passed"


def test_verify_release_preflight_requires_dependency_review_artifact_when_option_is_set(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["dependency_review_file"] = "artifacts/dependency-review.json"
    payload["options"]["require_dependency_review"] = True
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "dependency review audit artifact is missing" in report["errors"]


def test_verify_release_preflight_requires_external_acceptance_artifact_when_option_is_set(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["external_acceptance_file"] = "artifacts/external-acceptance.json"
    payload["options"]["require_external_acceptance"] = True
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "external acceptance audit artifact is missing" in report["errors"]


def test_verify_release_preflight_requires_deployment_compose_artifact_by_default(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        payload_data = gate.get("payload")
        if isinstance(payload_data, dict) and isinstance(payload_data.get("artifacts"), list):
            payload_data["artifacts"] = [
                item for item in payload_data["artifacts"] if item["name"] != "deployment_compose_audit"
            ]
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "deployment compose audit artifact is missing" in report["errors"]


def test_verify_release_preflight_requires_production_env_artifact_when_option_is_set(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["env_file"] = ".env.production"
    payload["options"]["require_production_env"] = True
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "production env audit artifact must be passed, got skipped" in report["errors"]


def test_verify_release_preflight_cli_writes_report_and_can_fail_on_dependency_review(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    report_path = tmp_path / "release-preflight.json"
    output_path = tmp_path / "verification.json"
    report_path.write_text(json.dumps(valid_preflight_report(), ensure_ascii=False), encoding="utf-8")

    exit_code = module.main(
        [
            "--report",
            str(report_path),
            "--output",
            str(output_path),
            "--fail-on-dependency-review",
        ]
    )

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["summary"]["error_count"] == 1
    assert "dependency inventory has 1 review-required item(s)" in written["errors"]


def test_verify_release_preflight_fail_on_dependency_review_uses_missing_dependency_guidance(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["dependency_inventory_summary"] = local_missing_dependency_summary()
    report_path = tmp_path / "release-preflight.json"
    output_path = tmp_path / "verification.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    exit_code = module.main(
        [
            "--report",
            str(report_path),
            "--output",
            str(output_path),
            "--fail-on-dependency-review",
        ]
    )

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert written["status"] == "failed"
    assert "regenerate and use the release image dependency inventory before go-live" in written["errors"][0]



def valid_preflight_report() -> dict:
    artifact_sha = "a" * 64
    evidence_payload = {
        "output_dir": "tmp/release-preflight-evidence",
        "manifest_path": "tmp/release-preflight-evidence/release-evidence-pack.json",
        "verification_path": "tmp/release-preflight-evidence/release-evidence-verification.json",
        "manifest_exists": True,
        "pack_status": "passed",
        "pack_summary": {
            "artifact_count": 3,
            "required_count": 2,
            "passed_count": 2,
            "failed_count": 0,
            "required_failed_count": 0,
            "skipped_count": 1,
        },
        "artifacts": [
            {
                "name": "deployment_compose_audit",
                "required": True,
                "status": "passed",
                "relative_path": "deployment-compose-audit.json",
                "size_bytes": 123,
                "sha256": artifact_sha,
            },
            {
                "name": "customer_sandbox_audit",
                "required": True,
                "status": "passed",
                "relative_path": "customer-sandbox-audit.json",
                "size_bytes": 123,
                "sha256": artifact_sha,
            },
            {
                "name": "production_env_audit",
                "required": False,
                "status": "skipped",
                "relative_path": None,
                "size_bytes": None,
                "sha256": None,
            },
        ],
    }

    verification_payload = {
        **evidence_payload,
        "verification_report_exists": True,
        "verification_status": "passed",
        "verification_summary": {
            "artifact_count": 3,
            "verified_count": 2,
            "failed_count": 0,
            "skipped_count": 1,
            "manifest_error_count": 0,
        },
    }

    return {
        "schema_version": 1,
        "passed": True,
        "options": {
            "full_backend": False,
            "skip_frontend": False,
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
            {
                "name": "backend release gate tests",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
            },
            {
                "name": "release evidence pack generation",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
                "payload": evidence_payload,
            },
            {
                "name": "release evidence pack verification",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
                "payload": verification_payload,
            },
            {
                "name": "frontend production build",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
            },
            {
                "name": "API health smoke",
                "kind": "smoke",
                "status": "passed",
                "duration_sec": 1.0,
                "payload": {
                    "port": 50000,
                    "health": "{\"status\":\"ok\"}",
                    "ready": "{\"status\":\"ok\"}",
                },
            },
        ],
        "cleanup": {
            "name": "cleanup pycache",
            "kind": "cleanup",
            "status": "passed",
            "duration_sec": 0.1,
            "payload": {"removed_count": 3},
        },
        "dependency_inventory_summary": {
            "dependency_count": 10,
            "review_required_count": 1,
            "review_required": [{"name": "example"}],
        },
    }


def local_missing_dependency_summary() -> dict:
    return {
        "dependency_count": 10,
        "review_required_count": 2,
        "release_blocking_missing_install_count": 2,
        "review_required": [
            {
                "ecosystem": "python",
                "name": "celery",
                "scope": "runtime",
                "installed": False,
                "version": None,
                "license": None,
                "reason": "package is not installed in this environment; regenerate inventory in the release image",
            },
            {
                "ecosystem": "python",
                "name": "minio",
                "scope": "runtime",
                "installed": False,
                "version": None,
                "license": None,
                "reason": "package is not installed in this environment; regenerate inventory in the release image",
            },
        ],
    }
