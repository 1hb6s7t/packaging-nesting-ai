from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def verify_release_handoff_bundle(
    manifest_path: Path,
    *,
    base_dir: Path | None = None,
    require_passed_bundle: bool = True,
) -> dict[str, Any]:
    resolved_manifest_path = manifest_path.resolve()
    manifest = read_json(resolved_manifest_path)
    manifest_errors: list[str] = []

    if manifest.get("schema_version") != 1:
        manifest_errors.append("handoff manifest schema_version must be 1")
    manifest_status = str(manifest.get("status") or "")
    if require_passed_bundle and manifest_status != "passed":
        manifest_errors.append(f"handoff manifest status must be passed, got {manifest_status or '<missing>'}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        manifest_errors.append("handoff manifest artifacts must be a list")

    artifact_base_dir = resolve_base_dir(base_dir, manifest, resolved_manifest_path)
    checks = [
        verify_handoff_artifact(
            item,
            artifact_base_dir,
            manifest_dir=resolved_manifest_path.parent,
            require_passed_bundle=require_passed_bundle,
        )
        for item in artifacts
    ]
    failed_checks = [item for item in checks if item["status"] == "failed"]
    skipped_checks = [item for item in checks if item["status"] == "skipped"]
    verified_checks = [item for item in checks if item["status"] == "passed"]
    status = "passed" if not manifest_errors and not failed_checks else "failed"
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest_path": str(resolved_manifest_path),
        "base_dir": str(artifact_base_dir),
        "manifest_status": manifest_status,
        "status": status,
        "summary": {
            "artifact_count": len(checks),
            "verified_count": len(verified_checks),
            "failed_count": len(failed_checks),
            "skipped_count": len(skipped_checks),
            "manifest_error_count": len(manifest_errors),
            "failed_artifacts": [str(item["name"]) for item in failed_checks],
        },
        "manifest_errors": manifest_errors,
        "checks": checks,
    }


def verify_handoff_artifact(
    artifact: Any,
    base_dir: Path,
    *,
    manifest_dir: Path,
    require_passed_bundle: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return {
            "name": "<invalid>",
            "artifact_status": "<invalid>",
            "status": "failed",
            "required": None,
            "path": None,
            "relative_path": None,
            "expected_size_bytes": None,
            "actual_size_bytes": None,
            "expected_sha256": None,
            "actual_sha256": None,
            "errors": ["handoff artifact entry must be an object"],
        }

    name = str(artifact.get("name") or "<unnamed>")
    artifact_status = str(artifact.get("status") or "")
    required = bool(artifact.get("required"))
    relative_path = artifact.get("relative_path")
    expected_size = artifact.get("size_bytes")
    expected_sha256 = artifact.get("sha256")

    if require_passed_bundle and required and artifact_status != "passed":
        errors.append(f"required handoff artifact status must be passed, got {artifact_status or '<missing>'}")
    if require_passed_bundle and artifact_status == "failed":
        errors.append("handoff artifact status must not be failed")

    artifact_path: Path | None = None
    actual_size: int | None = None
    actual_sha256: str | None = None

    has_file_evidence = bool(relative_path or artifact.get("path") or expected_size is not None or expected_sha256)
    if artifact_status == "skipped" and not has_file_evidence:
        return {
            "name": name,
            "artifact_status": artifact_status,
            "status": "failed" if errors else "skipped",
            "required": required,
            "path": None,
            "relative_path": relative_path,
            "expected_size_bytes": expected_size,
            "actual_size_bytes": None,
            "expected_sha256": expected_sha256,
            "actual_sha256": None,
            "errors": errors,
        }

    try:
        artifact_path = resolve_artifact_path(artifact, base_dir, manifest_dir=manifest_dir)
    except ValueError as exc:
        errors.append(str(exc))

    if artifact_path is None:
        errors.append("handoff artifact path is missing")
    elif not artifact_path.is_file():
        errors.append("handoff artifact file is missing")
    else:
        actual_size = artifact_path.stat().st_size
        actual_sha256 = sha256_file(artifact_path)
        if not isinstance(expected_size, int):
            errors.append("handoff artifact size_bytes is missing or invalid")
        elif actual_size != expected_size:
            errors.append(f"handoff artifact size mismatch: expected {expected_size}, got {actual_size}")
        if not is_sha256_hex(expected_sha256):
            errors.append("handoff artifact sha256 is missing or invalid")
        elif actual_sha256 != expected_sha256:
            errors.append("handoff artifact sha256 mismatch")

    return {
        "name": name,
        "artifact_status": artifact_status,
        "status": "failed" if errors else "passed",
        "required": required,
        "path": str(artifact_path) if artifact_path is not None else None,
        "relative_path": relative_path,
        "expected_size_bytes": expected_size,
        "actual_size_bytes": actual_size,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_sha256,
        "errors": errors,
    }


def resolve_base_dir(base_dir: Path | None, manifest: dict[str, Any], manifest_path: Path) -> Path:
    if base_dir is not None:
        return base_dir.resolve()
    repo_root = manifest.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        return Path(repo_root).resolve()
    return manifest_path.parent


def resolve_artifact_path(artifact: dict[str, Any], base_dir: Path, *, manifest_dir: Path) -> Path | None:
    relative_path = artifact.get("relative_path")
    if relative_path:
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"handoff artifact relative_path is unsafe: {relative_path}")
        return base_dir / relative

    path_value = artifact.get("path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if path.is_absolute():
        if path.exists():
            return path
        return manifest_dir / path.name
    return base_dir / path


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
    output_path = path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify release handoff manifest artifact size and SHA-256 integrity.")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to release-handoff-bundle.json.")
    parser.add_argument("--base-dir", type=Path, help="Base directory used to resolve artifact relative_path values.")
    parser.add_argument("--output", type=Path, help="Optional JSON verification report path.")
    parser.add_argument(
        "--allow-failed-bundle",
        action="store_true",
        help="Verify file integrity even when the handoff manifest or an artifact status is failed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = verify_release_handoff_bundle(
        args.manifest,
        base_dir=args.base_dir,
        require_passed_bundle=not args.allow_failed_bundle,
    )
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "release handoff verification "
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
