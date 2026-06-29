from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_VERIFICATION_GATE = "release evidence pack verification"
EVIDENCE_ARTIFACT_PREFIX = "release_evidence_artifact:"
TOP_LEVEL_EVIDENCE_ARTIFACT_NAMES = {"dependency_inventory", "dependency_review_audit"}


def build_release_handoff_bundle(
    *,
    preflight_report: Path,
    preflight_verification: Path,
    evidence_manifest: Path | None = None,
    evidence_verification: Path | None = None,
    dependency_inventory: Path | None = None,
    dependency_review_audit: Path | None = None,
    release_image_dependency_audit: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    artifacts: list[dict[str, Any]] = []

    preflight_artifact = build_json_artifact(
        "release_preflight_report",
        preflight_report,
        required=True,
        validator=validate_preflight_report,
    )
    artifacts.append(preflight_artifact)
    errors.extend(preflight_artifact["errors"])

    preflight_verification_artifact = build_json_artifact(
        "release_preflight_verification",
        preflight_verification,
        required=True,
        validator=validate_status_report,
    )
    artifacts.append(preflight_verification_artifact)
    errors.extend(preflight_verification_artifact["errors"])

    preflight_payload = preflight_artifact.get("json_payload") if isinstance(preflight_artifact.get("json_payload"), dict) else {}
    evidence_manifest_path = evidence_manifest or derived_evidence_path(preflight_payload, "manifest_path")
    evidence_verification_path = evidence_verification or derived_evidence_path(preflight_payload, "verification_path")

    evidence_manifest_artifact = build_json_artifact(
        "release_evidence_manifest",
        evidence_manifest_path,
        required=True,
        validator=validate_status_report,
    )
    artifacts.append(evidence_manifest_artifact)
    errors.extend(evidence_manifest_artifact["errors"])

    evidence_verification_artifact = build_json_artifact(
        "release_evidence_verification",
        evidence_verification_path,
        required=True,
        validator=validate_status_report,
    )
    artifacts.append(evidence_verification_artifact)
    errors.extend(evidence_verification_artifact["errors"])

    evidence_payload = evidence_manifest_artifact.get("json_payload")
    evidence_base_dir = evidence_manifest_path.parent if evidence_manifest_path is not None else None
    for artifact in build_evidence_pack_artifacts(evidence_payload, evidence_base_dir):
        if artifact["status"] in {"failed", "missing"}:
            errors.extend(artifact["errors"])
        artifacts.append(artifact)

    dependency_inventory_path = dependency_inventory or evidence_artifact_path(evidence_payload, "dependency_inventory", evidence_base_dir)
    dependency_review_audit_path = dependency_review_audit or evidence_artifact_path(
        evidence_payload,
        "dependency_review_audit",
        evidence_base_dir,
    )
    optional_artifacts = [
        build_json_artifact(
            "dependency_inventory",
            dependency_inventory_path,
            required=False,
            validator=validate_dependency_inventory,
        ),
        build_json_artifact(
            "dependency_review_audit",
            dependency_review_audit_path,
            required=False,
            validator=validate_optional_status_report,
        ),
    ]
    if release_image_dependency_audit is not None:
        optional_artifacts.append(
            build_json_artifact(
                "release_image_dependency_audit",
                release_image_dependency_audit,
                required=False,
                validator=validate_status_report,
            )
        )
    for artifact in optional_artifacts:
        if artifact["path"] is None:
            warnings.append(f"{artifact['name']} was not included in the handoff bundle")
        elif artifact["status"] == "skipped":
            warnings.append(f"{artifact['name']} is skipped")
        elif artifact["status"] == "failed":
            errors.extend(artifact["errors"])
        artifacts.append(artifact)

    report_errors = errors
    summary = build_summary(artifacts, report_errors, warnings)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(REPO_ROOT),
        "status": "passed" if not report_errors else "failed",
        "summary": summary,
        "artifacts": [strip_json_payload(artifact) for artifact in artifacts],
        "errors": report_errors,
        "warnings": warnings,
    }


def build_json_artifact(
    name: str,
    path: Path | None,
    *,
    required: bool,
    validator,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "name": name,
        "required": required,
        "status": "missing" if required else "skipped",
        "path": str(path) if path else None,
        "relative_path": None,
        "size_bytes": None,
        "sha256": None,
        "summary": {},
        "errors": [],
    }
    if path is None:
        if required:
            artifact["errors"].append(f"{name} path is missing")
        return artifact
    resolved = resolve_path(path)
    artifact["path"] = str(resolved)
    artifact["relative_path"] = relative_path(resolved)
    if not resolved.is_file():
        artifact["status"] = "missing" if required else "failed"
        artifact["errors"].append(f"{name} file does not exist: {resolved}")
        return artifact
    try:
        payload = read_json(resolved)
    except Exception as exc:
        artifact["status"] = "failed"
        artifact["errors"].append(f"{name} file could not be read: {exc}")
        return artifact
    validation = validator(payload)
    artifact.update(
        {
            "status": validation["status"],
            "size_bytes": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
            "summary": validation["summary"],
            "errors": validation["errors"],
            "json_payload": payload,
        }
    )
    return artifact


def build_evidence_pack_artifacts(payload: Any, base_dir: Path | None) -> list[dict[str, Any]]:
    if base_dir is None or not isinstance(payload, dict):
        return []
    evidence_artifacts = payload.get("artifacts")
    if not isinstance(evidence_artifacts, list):
        return []
    artifacts: list[dict[str, Any]] = []
    for evidence_artifact in evidence_artifacts:
        if not isinstance(evidence_artifact, dict):
            artifacts.append(invalid_evidence_pack_artifact("evidence artifact entry must be an object"))
            continue
        artifact_name = str(evidence_artifact.get("name") or "<unnamed>")
        if artifact_name in TOP_LEVEL_EVIDENCE_ARTIFACT_NAMES:
            continue
        artifacts.append(build_evidence_pack_artifact(evidence_artifact, base_dir))
    return artifacts


def invalid_evidence_pack_artifact(error: str) -> dict[str, Any]:
    return {
        "name": f"{EVIDENCE_ARTIFACT_PREFIX}<invalid>",
        "required": True,
        "status": "failed",
        "path": None,
        "relative_path": None,
        "size_bytes": None,
        "sha256": None,
        "summary": {},
        "errors": [error],
    }


def build_evidence_pack_artifact(evidence_artifact: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    evidence_name = str(evidence_artifact.get("name") or "<unnamed>")
    evidence_status = str(evidence_artifact.get("status") or "")
    required = bool(evidence_artifact.get("required"))
    artifact: dict[str, Any] = {
        "name": f"{EVIDENCE_ARTIFACT_PREFIX}{evidence_name}",
        "required": required,
        "status": "skipped" if evidence_status == "skipped" else "failed",
        "path": None,
        "relative_path": None,
        "size_bytes": None,
        "sha256": None,
        "summary": {
            "evidence_artifact_name": evidence_name,
            "evidence_artifact_status": evidence_status,
            "evidence_artifact_required": required,
        },
        "errors": [],
    }

    if required and evidence_status != "passed":
        artifact["errors"].append(f"required evidence artifact {evidence_name} did not pass")

    path = evidence_artifact_resolved_path(evidence_artifact, base_dir)
    if path is None:
        if evidence_status == "skipped" and not required:
            return artifact
        artifact["status"] = "missing" if required else "failed"
        artifact["errors"].append(f"evidence artifact {evidence_name} path is missing")
        return artifact

    artifact["path"] = str(path)
    artifact["relative_path"] = relative_path(path)
    if not path.is_file():
        artifact["status"] = "missing" if required else "failed"
        artifact["errors"].append(f"evidence artifact {evidence_name} file does not exist: {path}")
        return artifact

    expected_size = evidence_artifact.get("size_bytes")
    expected_sha256 = evidence_artifact.get("sha256")
    actual_size = path.stat().st_size
    actual_sha256 = sha256_file(path)
    artifact["size_bytes"] = actual_size
    artifact["sha256"] = actual_sha256

    if not isinstance(expected_size, int):
        artifact["errors"].append(f"evidence artifact {evidence_name} manifest size_bytes is missing or invalid")
    elif actual_size != expected_size:
        artifact["errors"].append(
            f"evidence artifact {evidence_name} size mismatch: expected {expected_size}, got {actual_size}"
        )
    if not is_sha256_hex(expected_sha256):
        artifact["errors"].append(f"evidence artifact {evidence_name} manifest sha256 is missing or invalid")
    elif actual_sha256 != expected_sha256:
        artifact["errors"].append(f"evidence artifact {evidence_name} sha256 mismatch")

    try:
        payload = read_json(path)
    except Exception as exc:
        artifact["errors"].append(f"evidence artifact {evidence_name} file could not be read: {exc}")
    else:
        payload_status = str(payload.get("status") or "")
        if payload_status != evidence_status:
            artifact["errors"].append(
                f"evidence artifact {evidence_name} status mismatch: manifest {evidence_status}, file {payload_status or '<missing>'}"
            )
        if isinstance(payload.get("summary"), dict):
            artifact["summary"]["evidence_summary"] = payload["summary"]

    artifact["status"] = evidence_status if not artifact["errors"] else "failed"
    return artifact


def evidence_artifact_resolved_path(evidence_artifact: dict[str, Any], base_dir: Path) -> Path | None:
    relative = evidence_artifact.get("relative_path")
    if isinstance(relative, str) and relative:
        relative_path_value = Path(relative)
        if relative_path_value.is_absolute() or ".." in relative_path_value.parts:
            return None
        return base_dir / relative_path_value
    path = evidence_artifact.get("path")
    if isinstance(path, str) and path:
        return Path(path)
    return None


def validate_preflight_report(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("release preflight report schema_version must be 1")
    if payload.get("passed") is not True:
        errors.append("release preflight report must have passed=true")
    gates = payload.get("gates")
    gate_count = len(gates) if isinstance(gates, list) else 0
    if gate_count == 0:
        errors.append("release preflight report must include gates")
    return {
        "status": "passed" if not errors else "failed",
        "summary": {
            "passed": bool(payload.get("passed")),
            "gate_count": gate_count,
            "cleanup_status": (payload.get("cleanup") or {}).get("status") if isinstance(payload.get("cleanup"), dict) else None,
        },
        "errors": errors,
    }


def validate_status_report(payload: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if payload.get("schema_version") != 1:
        errors.append("status report schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"status report must be passed, got {payload.get('status') or '<missing>'}")
    return {
        "status": "passed" if not errors else "failed",
        "summary": dict(payload.get("summary") or {}),
        "errors": errors,
    }


def validate_optional_status_report(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("status")
    if status in {"passed", "skipped"}:
        return {
            "status": status,
            "summary": dict(payload.get("summary") or {}),
            "errors": [],
        }
    return validate_status_report(payload)


def validate_dependency_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("dependency inventory schema_version must be 1")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if not isinstance(summary.get("dependency_count"), int) or summary.get("dependency_count") <= 0:
        errors.append("dependency inventory dependency_count is missing or invalid")
    return {
        "status": "passed" if not errors else "failed",
        "summary": summary,
        "errors": errors,
    }


def derived_evidence_path(preflight_payload: dict[str, Any], key: str) -> Path | None:
    payload = evidence_gate_payload(preflight_payload)
    value = payload.get(key) if isinstance(payload, dict) else None
    return Path(value) if isinstance(value, str) and value else None


def evidence_gate_payload(preflight_payload: dict[str, Any]) -> dict[str, Any]:
    gates = preflight_payload.get("gates")
    if not isinstance(gates, list):
        return {}
    for gate in gates:
        if isinstance(gate, dict) and gate.get("name") == EVIDENCE_VERIFICATION_GATE:
            payload = gate.get("payload")
            return payload if isinstance(payload, dict) else {}
    return {}


def evidence_artifact_path(payload: Any, artifact_name: str, base_dir: Path | None) -> Path | None:
    if not isinstance(payload, dict):
        return None
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("name") != artifact_name:
            continue
        relative = artifact.get("relative_path")
        if isinstance(relative, str) and relative and base_dir is not None:
            return base_dir / relative
        path = artifact.get("path")
        if isinstance(path, str) and path:
            return Path(path)
    return None


def build_summary(artifacts: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "artifact_count": len(artifacts),
        "required_count": sum(1 for artifact in artifacts if artifact["required"]),
        "passed_count": sum(1 for artifact in artifacts if artifact["status"] == "passed"),
        "skipped_count": sum(1 for artifact in artifacts if artifact["status"] == "skipped"),
        "missing_count": sum(1 for artifact in artifacts if artifact["status"] == "missing"),
        "failed_count": sum(1 for artifact in artifacts if artifact["status"] == "failed"),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def strip_json_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "json_payload"}


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def relative_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = resolve_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a top-level release handoff manifest from generated gate evidence.")
    parser.add_argument("--preflight-report", type=Path, required=True, help="Path to release-preflight.json.")
    parser.add_argument("--preflight-verification", type=Path, required=True, help="Path to release-preflight-verification.json.")
    parser.add_argument("--evidence-manifest", type=Path, help="Optional path to release-evidence-pack.json; otherwise derived from preflight.")
    parser.add_argument("--evidence-verification", type=Path, help="Optional path to release-evidence-verification.json; otherwise derived from preflight.")
    parser.add_argument("--dependency-inventory", type=Path, help="Optional path to dependency-inventory.json; otherwise derived from evidence manifest.")
    parser.add_argument("--dependency-review-audit", type=Path, help="Optional path to dependency-review-audit.json; otherwise derived from evidence manifest.")
    parser.add_argument("--release-image-dependency-audit", type=Path, help="Optional path to release-image-dependency-audit.json.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/release-handoff-bundle.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_release_handoff_bundle(
        preflight_report=args.preflight_report,
        preflight_verification=args.preflight_verification,
        evidence_manifest=args.evidence_manifest,
        evidence_verification=args.evidence_verification,
        dependency_inventory=args.dependency_inventory,
        dependency_review_audit=args.dependency_review_audit,
        release_image_dependency_audit=args.release_image_dependency_audit,
    )
    output_path = write_json(args.output, report)
    summary = report["summary"]
    print(
        "release handoff bundle "
        f"{report['status']} "
        f"output={output_path} "
        f"artifacts={summary['artifact_count']} "
        f"errors={summary['error_count']} "
        f"warnings={summary['warning_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
