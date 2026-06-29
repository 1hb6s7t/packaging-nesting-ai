from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def verify_ci_evidence_manifest(
    manifest_path: Path,
    *,
    base_dir: Path | None = None,
    require_passed_manifest: bool = True,
) -> dict[str, Any]:
    resolved_manifest_path = manifest_path.resolve()
    resolved_base_dir = (base_dir.resolve() if base_dir else resolved_manifest_path.parent)
    manifest_errors: list[str] = []
    manifest_warnings: list[str] = []
    manifest = read_json_object(resolved_manifest_path, manifest_errors)

    if manifest.get("schema_version") != 1:
        manifest_errors.append("manifest schema_version must be 1")
    manifest_status = str(manifest.get("status") or "")
    if require_passed_manifest and manifest_status != "passed":
        manifest_errors.append(f"manifest status must be passed, got {manifest_status or '<missing>'}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest_errors.append("manifest artifacts must be a list")

    checks = [verify_artifact(item, resolved_base_dir) for item in artifacts]
    validate_summary(manifest.get("summary"), checks, manifest_errors)
    validate_frontend_gate_policy(manifest.get("frontend_gate_policy"), manifest_errors, manifest_warnings)

    failed_checks = [item for item in checks if item["status"] == "failed"]
    skipped_checks = [item for item in checks if item["status"] == "skipped"]
    verified_checks = [item for item in checks if item["status"] == "passed"]
    status = "passed" if not manifest_errors and not failed_checks else "failed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest_path": str(resolved_manifest_path),
        "base_dir": str(resolved_base_dir),
        "manifest_status": manifest_status,
        "status": status,
        "summary": {
            "artifact_count": len(checks),
            "verified_count": len(verified_checks),
            "failed_count": len(failed_checks),
            "skipped_count": len(skipped_checks),
            "manifest_error_count": len(manifest_errors),
            "manifest_warning_count": len(manifest_warnings),
            "failed_artifacts": [str(item["name"]) for item in failed_checks],
        },
        "manifest_errors": manifest_errors,
        "manifest_warnings": manifest_warnings,
        "checks": checks,
    }


def verify_artifact(artifact: Any, base_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return {
            "name": "<invalid>",
            "artifact_status": "<invalid>",
            "status": "failed",
            "path": None,
            "relative_path": None,
            "expected_size_bytes": None,
            "actual_size_bytes": None,
            "expected_sha256": None,
            "actual_sha256": None,
            "errors": ["artifact entry must be an object"],
        }

    name = str(artifact.get("name") or "<unnamed>")
    artifact_status = str(artifact.get("status") or "")
    relative_path = artifact.get("relative_path")
    expected_size = artifact.get("size_bytes")
    expected_sha256 = artifact.get("sha256")
    has_file_evidence = bool(relative_path or artifact.get("path") or expected_size is not None or expected_sha256)
    if artifact_status == "skipped" and not has_file_evidence:
        return artifact_check(
            name=name,
            artifact_status=artifact_status,
            status="skipped",
            path=None,
            relative_path=relative_path,
            expected_size=expected_size,
            actual_size=None,
            expected_sha256=expected_sha256,
            actual_sha256=None,
            errors=[],
        )

    if artifact_status not in {"passed", "skipped"}:
        errors.append(f"artifact status must be passed or skipped, got {artifact_status or '<missing>'}")

    artifact_path: Path | None = None
    actual_size: int | None = None
    actual_sha256: str | None = None
    try:
        artifact_path = resolve_artifact_path(artifact, base_dir)
    except ValueError as exc:
        errors.append(str(exc))

    if artifact_path is None:
        errors.append("artifact path is missing")
    elif not artifact_path.is_file():
        errors.append("artifact file is missing")
    else:
        actual_size = artifact_path.stat().st_size
        actual_sha256 = sha256_file(artifact_path)
        if not isinstance(expected_size, int):
            errors.append("artifact size_bytes is missing or invalid")
        elif actual_size != expected_size:
            errors.append(f"artifact size mismatch: expected {expected_size}, got {actual_size}")
        if not is_sha256_hex(expected_sha256):
            errors.append("artifact sha256 is missing or invalid")
        elif actual_sha256 != expected_sha256:
            errors.append("artifact sha256 mismatch")

    return artifact_check(
        name=name,
        artifact_status=artifact_status,
        status="failed" if errors else "passed",
        path=artifact_path,
        relative_path=relative_path,
        expected_size=expected_size,
        actual_size=actual_size,
        expected_sha256=expected_sha256,
        actual_sha256=actual_sha256,
        errors=errors,
    )


def artifact_check(
    *,
    name: str,
    artifact_status: str,
    status: str,
    path: Path | None,
    relative_path: Any,
    expected_size: Any,
    actual_size: int | None,
    expected_sha256: Any,
    actual_sha256: str | None,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "artifact_status": artifact_status,
        "status": status,
        "path": str(path) if path is not None else None,
        "relative_path": relative_path,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "errors": errors,
    }


def resolve_artifact_path(artifact: dict[str, Any], base_dir: Path) -> Path | None:
    relative_path = artifact.get("relative_path")
    if relative_path:
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"artifact relative_path is unsafe: {relative_path}")
        return base_dir / relative

    path_value = artifact.get("path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if path.is_absolute():
        if path.exists():
            return path
        return base_dir / path.name
    return base_dir / path


def validate_summary(summary: Any, checks: list[dict[str, Any]], errors: list[str]) -> None:
    if not isinstance(summary, dict):
        errors.append("manifest summary must be an object")
        return
    expected_counts = {
        "artifact_count": len(checks),
        "passed_count": sum(1 for check in checks if check["status"] == "passed"),
        "failed_count": sum(1 for check in checks if check["status"] == "failed"),
        "skipped_count": sum(1 for check in checks if check["status"] == "skipped"),
    }
    for key, actual in expected_counts.items():
        if summary.get(key) != actual:
            errors.append(f"manifest summary {key} mismatch: expected {actual}, got {summary.get(key)}")


def validate_frontend_gate_policy(policy: Any, errors: list[str], warnings: list[str]) -> None:
    if not isinstance(policy, dict):
        warnings.append("frontend_gate_policy is missing")
        return
    if policy.get("preflight_skipped") and not policy.get("allow_skipped_frontend"):
        errors.append("frontend gate was skipped without allow_skipped_frontend policy")
    if policy.get("preflight_skipped") and not policy.get("covered_by_ci_job"):
        errors.append("frontend gate was skipped without a covering CI job")
    if policy.get("preflight_skipped") and not policy.get("frontend_artifact_name"):
        errors.append("frontend gate was skipped without a frontend artifact name")


def read_json_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"manifest file could not be read: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("manifest root must be an object")
        return {}
    return payload


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


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a downloaded CI evidence manifest and its artifact files.")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to ci-evidence-manifest.json.")
    parser.add_argument("--base-dir", type=Path, help="Directory containing files referenced by manifest relative_path.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument("--allow-failed-manifest", action="store_true", help="Verify file integrity even when manifest status failed.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = verify_ci_evidence_manifest(
        args.manifest,
        base_dir=args.base_dir,
        require_passed_manifest=not args.allow_failed_manifest,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "ci evidence manifest verification "
        f"{report['status']} "
        f"manifest={report['manifest_path']} "
        f"verified={summary['verified_count']} "
        f"failed={summary['failed_count']} "
        f"skipped={summary['skipped_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
