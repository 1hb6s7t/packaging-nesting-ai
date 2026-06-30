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
    assert report["summary"]["gate_count"] == 6
    assert report["summary"]["passed_gate_count"] == 6
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
    payload["options"]["skip_benchmark_gate"] = True
    payload["gates"] = [payload["gates"][0]]
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    strict_report = module.verify_release_preflight_report(report_path)
    assert strict_report["status"] == "failed"
    assert "release evidence pack gate was skipped" in strict_report["errors"]
    assert "benchmark release gate was skipped" in strict_report["errors"]
    assert "frontend build was skipped" in strict_report["errors"]
    assert "API health smoke was skipped" in strict_report["errors"]
    allowed_report = module.verify_release_preflight_report(
        report_path,
        require_evidence_pack=False,
        require_frontend=False,
        require_benchmark_gate=False,
        require_smoke=False,
    )

    assert allowed_report["status"] == "passed"


def test_verify_release_preflight_allows_explicit_skipped_optional_gates(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["skip_frontend"] = True
    payload["options"]["skip_smoke"] = True
    for gate in payload["gates"]:
        if gate["name"] in {"frontend production build", "API health smoke"}:
            gate["status"] = "skipped"
            gate["duration_sec"] = 0
            gate.pop("exit_code", None)
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path, require_frontend=False, require_smoke=False)

    assert report["status"] == "passed"
    assert report["summary"]["failed_gate_count"] == 0
    assert report["summary"]["failed_gates"] == []


def test_verify_release_preflight_rejects_failed_benchmark_gate(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        if gate["name"] == "benchmark release gate":
            gate["payload"]["status"] = "failed"
            gate["payload"]["summary"]["error_count"] = 1
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "benchmark release gate status must be passed, got failed" in report["errors"]
    assert "benchmark release gate summary has errors" in report["errors"]


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


def test_verify_release_preflight_accepts_required_dependency_review_verification(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["dependency_review_file"] = "artifacts/dependency-review.json"
    payload["options"]["require_dependency_review"] = True
    mark_evidence_artifact_passed(
        payload,
        "dependency_review_audit",
        relative_path="dependency-review-audit.json",
        summary=dependency_review_artifact_summary(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence dependency review verification",
        dependency_review_verification_payload(),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "passed"
    assert report["summary"]["error_count"] == 0


def test_verify_release_preflight_requires_dependency_review_verification_gate(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["dependency_review_file"] = "artifacts/dependency-review.json"
    payload["options"]["require_dependency_review"] = True
    mark_evidence_artifact_passed(
        payload,
        "dependency_review_audit",
        relative_path="dependency-review-audit.json",
        summary=dependency_review_artifact_summary(),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "dependency review verification gate is missing" in report["errors"]


def test_verify_release_preflight_rejects_failed_dependency_review_verification(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["dependency_review_file"] = "artifacts/dependency-review.json"
    payload["options"]["require_dependency_review"] = True
    mark_evidence_artifact_passed(
        payload,
        "dependency_review_audit",
        relative_path="dependency-review-audit.json",
        summary=dependency_review_artifact_summary(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence dependency review verification",
        dependency_review_verification_payload(error_count=1),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "dependency review verification summary has errors" in report["errors"]


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


def test_verify_release_preflight_accepts_required_production_and_external_verifications(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["env_file"] = ".env.production"
    payload["options"]["require_production_env"] = True
    payload["options"]["external_acceptance_file"] = "artifacts/external-acceptance.json"
    payload["options"]["require_external_acceptance"] = True
    mark_evidence_artifact_passed(
        payload,
        "production_env_audit",
        relative_path="production-env-audit.json",
        summary=production_env_artifact_summary(),
    )
    mark_evidence_artifact_passed(
        payload,
        "external_acceptance_audit",
        relative_path="external-acceptance-audit.json",
        summary=external_acceptance_artifact_summary(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence production env verification",
        production_env_verification_payload(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence external acceptance verification",
        external_acceptance_verification_payload(),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "passed"
    assert report["summary"]["gate_count"] == 8
    assert report["summary"]["error_count"] == 0


def test_verify_release_preflight_requires_production_env_verification_gate(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["env_file"] = ".env.production"
    payload["options"]["require_production_env"] = True
    mark_evidence_artifact_passed(
        payload,
        "production_env_audit",
        relative_path="production-env-audit.json",
        summary=production_env_artifact_summary(),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "production env verification gate is missing" in report["errors"]


def test_verify_release_preflight_rejects_failed_production_env_verification(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["env_file"] = ".env.production"
    payload["options"]["require_production_env"] = True
    mark_evidence_artifact_passed(
        payload,
        "production_env_audit",
        relative_path="production-env-audit.json",
        summary=production_env_artifact_summary(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence production env verification",
        production_env_verification_payload(rebuilt_report_match=False),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "production env verification must match the supplied env file" in report["errors"]


def test_verify_release_preflight_rejects_failed_external_acceptance_verification(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    payload["options"]["external_acceptance_file"] = "artifacts/external-acceptance.json"
    payload["options"]["require_external_acceptance"] = True
    mark_evidence_artifact_passed(
        payload,
        "external_acceptance_audit",
        relative_path="external-acceptance-audit.json",
        summary=external_acceptance_artifact_summary(),
    )
    append_evidence_file_verification_gate(
        payload,
        "release evidence external acceptance verification",
        external_acceptance_verification_payload(failed_evidence_check_count=1),
    )
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "external acceptance verification has failed evidence checks" in report["errors"]


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


def test_verify_release_preflight_rejects_failed_pack_policy_summary(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        gate_payload = gate.get("payload")
        if isinstance(gate_payload, dict) and isinstance(gate_payload.get("pack_summary"), dict):
            gate_payload["pack_summary"]["policy_contract_status"] = "failed"
            gate_payload["pack_summary"]["policy_contract_failed_count"] = 1
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "release evidence pack policy contract has failed checks" in report["errors"]


def test_verify_release_preflight_rejects_failed_artifact_policy_summary(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        gate_payload = gate.get("payload")
        artifacts = gate_payload.get("artifacts") if isinstance(gate_payload, dict) else None
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if isinstance(artifact, dict) and artifact.get("name") == "deployment_compose_audit":
                artifact["summary"]["policy_contract_status"] = "failed"
                artifact["summary"]["policy_contract_failed_count"] = 1
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "evidence artifact deployment_compose_audit policy_contract_failed_count must be 0" in report["errors"]


def test_verify_release_preflight_rejects_failed_artifact_sensitive_scan_summary(tmp_path: Path) -> None:
    module = load_verify_release_preflight_module()
    payload = valid_preflight_report()
    for gate in payload["gates"]:
        gate_payload = gate.get("payload")
        artifacts = gate_payload.get("artifacts") if isinstance(gate_payload, dict) else None
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if isinstance(artifact, dict) and artifact.get("name") == "customer_sandbox_audit":
                artifact["summary"]["sensitive_scan_status"] = "failed"
                artifact["summary"]["sensitive_scan_failed_count"] = 1
    report_path = tmp_path / "release-preflight.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = module.verify_release_preflight_report(report_path)

    assert report["status"] == "failed"
    assert "evidence artifact customer_sandbox_audit sensitive scan has failed findings" in report["errors"]


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
            "policy_contract_status": "passed",
            "policy_contract_failed_count": 0,
            "policy_contract_warning_count": 0,
        },
        "artifacts": [
            {
                "name": "deployment_compose_audit",
                "required": True,
                "status": "passed",
                "relative_path": "deployment-compose-audit.json",
                "size_bytes": 123,
                "sha256": artifact_sha,
                "summary": {
                    "policy_contract_status": "passed",
                    "policy_contract_failed_count": 0,
                    "sensitive_scan_status": "passed",
                    "sensitive_scan_failed_count": 0,
                },
            },
            {
                "name": "customer_sandbox_audit",
                "required": True,
                "status": "passed",
                "relative_path": "customer-sandbox-audit.json",
                "size_bytes": 123,
                "sha256": artifact_sha,
                "summary": {
                    "pack_contract_status": "passed",
                    "pack_contract_failed_count": 0,
                    "sync_strategy_status": "passed",
                    "sync_strategy_failed_count": 0,
                    "business_flow_status": "passed",
                    "business_flow_failed_count": 0,
                    "sensitive_scan_status": "passed",
                    "sensitive_scan_failed_count": 0,
                },
            },
            {
                "name": "production_env_audit",
                "required": False,
                "status": "skipped",
                "relative_path": None,
                "size_bytes": None,
                "sha256": None,
                "summary": {"reason": "--env-file was not provided"},
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
            {
                "name": "backend release gate tests",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
            },
            {
                "name": "benchmark release gate",
                "kind": "command",
                "status": "passed",
                "duration_sec": 1.0,
                "exit_code": 0,
                "payload": benchmark_gate_payload(),
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


def mark_evidence_artifact_passed(
    payload: dict,
    artifact_name: str,
    *,
    relative_path: str,
    summary: dict,
) -> None:
    artifact_payload = {
        "name": artifact_name,
        "required": True,
        "status": "passed",
        "relative_path": relative_path,
        "size_bytes": 123,
        "sha256": "b" * 64,
        "summary": dict(summary),
    }
    for gate in payload["gates"]:
        gate_payload = gate.get("payload")
        artifacts = gate_payload.get("artifacts") if isinstance(gate_payload, dict) else None
        if not isinstance(artifacts, list):
            continue
        existing = next(
            (artifact for artifact in artifacts if isinstance(artifact, dict) and artifact.get("name") == artifact_name),
            None,
        )
        if existing is None:
            artifacts.append(dict(artifact_payload))
        else:
            existing.update(artifact_payload)


def append_evidence_file_verification_gate(payload: dict, name: str, gate_payload: dict) -> None:
    payload["gates"].append(
        {
            "name": name,
            "kind": "command",
            "status": "passed",
            "duration_sec": 1.0,
            "exit_code": 0,
            "payload": gate_payload,
        }
    )


def production_env_artifact_summary() -> dict:
    return {
        "policy_contract_status": "passed",
        "policy_contract_failed_count": 0,
        "sensitive_scan_status": "passed",
        "sensitive_scan_failed_count": 0,
    }


def dependency_review_artifact_summary() -> dict:
    return {
        "policy_contract_status": "passed",
        "policy_contract_failed_count": 0,
        "sensitive_scan_status": "passed",
        "sensitive_scan_failed_count": 0,
    }


def external_acceptance_artifact_summary() -> dict:
    return {
        "policy_contract_status": "passed",
        "policy_contract_failed_count": 0,
        "sensitive_scan_status": "passed",
        "sensitive_scan_failed_count": 0,
    }


def production_env_verification_payload(*, rebuilt_report_match: bool = True, error_count: int = 0) -> dict:
    return {
        "path": "tmp/release-preflight-evidence/production-env-verification.json",
        "exists": True,
        "status": "passed",
        "report_status": "passed",
        "report_path": "tmp/release-preflight-evidence/production-env-audit.json",
        "summary": {
            "rebuilt_report_match": rebuilt_report_match,
            "error_count": error_count,
        },
    }


def dependency_review_verification_payload(*, error_count: int = 0) -> dict:
    return {
        "path": "tmp/release-preflight-evidence/dependency-review-verification.json",
        "exists": True,
        "status": "passed",
        "report_status": "passed",
        "report_path": "tmp/release-preflight-evidence/dependency-review-audit.json",
        "summary": {
            "error_count": error_count,
        },
    }


def external_acceptance_verification_payload(*, failed_evidence_check_count: int = 0, error_count: int = 0) -> dict:
    return {
        "path": "tmp/release-preflight-evidence/external-acceptance-verification.json",
        "exists": True,
        "status": "passed",
        "report_status": "passed",
        "report_path": "tmp/release-preflight-evidence/external-acceptance-audit.json",
        "summary": {
            "failed_evidence_check_count": failed_evidence_check_count,
            "error_count": error_count,
        },
    }


def benchmark_gate_payload(*, error_count: int = 0) -> dict:
    status = "passed" if error_count == 0 else "failed"
    return {
        "report_path": "tmp/release-preflight-evidence/benchmark-release-gate.json",
        "exists": True,
        "status": status,
        "thresholds": {
            "min_quantity_fulfillment_rate": 1.0,
            "max_p95_runtime_ms": 2000,
            "max_total_runtime_ms": 15000,
            "max_peak_rss_mb": None,
        },
        "case_count": 6,
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
            "error_count": error_count,
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
