from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_PREFLIGHT_REPORT = Path("tmp/ci-release-preflight.json")
DEFAULT_PREFLIGHT_VERIFICATION = Path("tmp/ci-release-preflight-verification.json")
DEFAULT_DEPENDENCY_INVENTORY = Path("tmp/ci-dependency-inventory.json")
DEFAULT_EVIDENCE_DIR = Path("tmp/ci-release-evidence")
DEFAULT_OUTPUT = Path("tmp/ci-evidence-manifest.json")
EVIDENCE_MANIFEST_NAME = "release-evidence-pack.json"
EVIDENCE_VERIFICATION_NAME = "release-evidence-verification.json"

sys.path.insert(0, str(SCRIPT_DIR))

import verify_release_evidence_pack
import verify_release_handoff_bundle
import verify_release_preflight


def build_ci_evidence_manifest(
    *,
    preflight_report: Path,
    preflight_verification: Path,
    dependency_inventory: Path | None,
    evidence_dir: Path,
    handoff_manifest: Path | None = None,
    handoff_verification: Path | None = None,
    output_path: Path | None = None,
    allow_skipped_frontend: bool = False,
    frontend_build_job: str | None = None,
    frontend_artifact_name: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved_preflight_report = resolve_repo_path(preflight_report)
    resolved_preflight_verification = resolve_repo_path(preflight_verification)
    resolved_dependency_inventory = resolve_repo_path(dependency_inventory) if dependency_inventory else None
    resolved_evidence_dir = resolve_repo_path(evidence_dir)
    resolved_handoff_manifest = resolve_repo_path(handoff_manifest) if handoff_manifest else None
    resolved_handoff_verification = resolve_repo_path(handoff_verification) if handoff_verification else None
    resolved_output = resolve_repo_path(output_path) if output_path else None
    base_dir = (resolved_output.parent if resolved_output else REPO_ROOT).resolve()

    errors: list[str] = []
    warnings: list[str] = []
    artifacts: list[dict[str, Any]] = []

    preflight_validation = validate_preflight_report(
        resolved_preflight_report,
        allow_skipped_frontend=allow_skipped_frontend,
    )
    artifacts.append(
        build_json_artifact(
            "release_preflight_report",
            resolved_preflight_report,
            base_dir,
            required=True,
            validation=preflight_validation,
        )
    )

    preflight_verification_validation = validate_preflight_verification(
        resolved_preflight_verification,
        expected_report=resolved_preflight_report,
    )
    artifacts.append(
        build_json_artifact(
            "release_preflight_verification",
            resolved_preflight_verification,
            base_dir,
            required=True,
            validation=preflight_verification_validation,
        )
    )

    if resolved_dependency_inventory is not None:
        artifacts.append(
            build_json_artifact(
                "dependency_inventory",
                resolved_dependency_inventory,
                base_dir,
                required=True,
                validation=validate_dependency_inventory(resolved_dependency_inventory),
            )
        )

    evidence_manifest = resolved_evidence_dir / EVIDENCE_MANIFEST_NAME
    evidence_verification = resolved_evidence_dir / EVIDENCE_VERIFICATION_NAME
    evidence_pack_validation = validate_evidence_manifest(evidence_manifest)
    artifacts.append(
        build_json_artifact(
            "release_evidence_manifest",
            evidence_manifest,
            base_dir,
            required=True,
            validation=evidence_pack_validation,
        )
    )
    artifacts.append(
        build_json_artifact(
            "release_evidence_verification",
            evidence_verification,
            base_dir,
            required=True,
            validation=validate_evidence_verification(evidence_verification, expected_manifest=evidence_manifest),
        )
    )
    artifacts.extend(build_evidence_artifact_entries(evidence_pack_validation.get("verification"), base_dir))

    if resolved_handoff_manifest is not None:
        artifacts.append(
            build_json_artifact(
                "release_handoff_manifest",
                resolved_handoff_manifest,
                base_dir,
                required=True,
                validation=validate_handoff_manifest(resolved_handoff_manifest),
            )
        )
    if resolved_handoff_verification is not None:
        artifacts.append(
            build_json_artifact(
                "release_handoff_verification",
                resolved_handoff_verification,
                base_dir,
                required=True,
                validation=validate_handoff_verification(
                    resolved_handoff_verification,
                    expected_manifest=resolved_handoff_manifest,
                ),
            )
        )

    for artifact in artifacts:
        if artifact["status"] == "failed":
            errors.extend(artifact["errors"])

    preflight_payload = safe_read_json(resolved_preflight_report)
    frontend_gate_policy = build_frontend_gate_policy(
        preflight_payload,
        allow_skipped_frontend=allow_skipped_frontend,
        frontend_build_job=frontend_build_job,
        frontend_artifact_name=frontend_artifact_name,
    )
    warnings.extend(frontend_gate_policy["warnings"])
    errors.extend(frontend_gate_policy["errors"])

    summary = build_summary(artifacts, errors, warnings)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not errors else "failed",
        "repo_root": str(REPO_ROOT),
        "base_dir": str(base_dir),
        "github": github_context(env or os.environ),
        "frontend_gate_policy": {
            key: value for key, value in frontend_gate_policy.items() if key not in {"errors", "warnings"}
        },
        "summary": summary,
        "artifacts": artifacts,
        "errors": errors,
        "warnings": warnings,
    }


def build_json_artifact(
    name: str,
    path: Path,
    base_dir: Path,
    *,
    required: bool,
    validation: dict[str, Any],
) -> dict[str, Any]:
    artifact = {
        "name": name,
        "required": required,
        "status": "failed" if required else "skipped",
        "path": str(path),
        "relative_path": relative_path(path, base_dir),
        "size_bytes": None,
        "sha256": None,
        "summary": validation.get("summary") if isinstance(validation.get("summary"), dict) else {},
        "errors": list(validation.get("errors") or []),
        "warnings": list(validation.get("warnings") or []),
    }
    if not path.is_file():
        artifact["errors"].append(f"{name} file does not exist: {path}")
        return artifact
    artifact["size_bytes"] = path.stat().st_size
    artifact["sha256"] = sha256_file(path)
    artifact["status"] = "passed" if not artifact["errors"] else "failed"
    return artifact


def validate_preflight_report(path: Path, *, allow_skipped_frontend: bool) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "failed", "summary": {}, "errors": [f"preflight report file does not exist: {path}"]}
    report = verify_release_preflight.verify_release_preflight_report(
        path,
        require_frontend=not allow_skipped_frontend,
    )
    return {
        "status": report["status"],
        "summary": report["summary"],
        "errors": list(report.get("errors") or []),
        "warnings": list(report.get("warnings") or []),
    }


def validate_preflight_verification(path: Path, *, expected_report: Path) -> dict[str, Any]:
    payload = safe_read_json(path)
    if payload is None:
        return {"status": "failed", "summary": {}, "errors": [f"preflight verification file is not valid JSON: {path}"]}
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("preflight verification schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"preflight verification status must be passed, got {payload.get('status') or '<missing>'}")
    validate_report_path(payload, "report_path", expected_report, "preflight verification", errors)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if summary.get("error_count") not in {0, None}:
        errors.append("preflight verification summary has errors")
    return {"status": "passed" if not errors else "failed", "summary": summary, "errors": errors}


def validate_dependency_inventory(path: Path) -> dict[str, Any]:
    payload = safe_read_json(path)
    if payload is None:
        return {"status": "failed", "summary": {}, "errors": [f"dependency inventory file is not valid JSON: {path}"]}
    errors: list[str] = []
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("dependency inventory dependencies must be a list")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    dependency_count = summary.get("dependency_count")
    if not isinstance(dependency_count, int) or dependency_count < 0:
        errors.append("dependency inventory summary.dependency_count is missing or invalid")
    return {"status": "passed" if not errors else "failed", "summary": summary, "errors": errors}


def validate_evidence_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "failed", "summary": {}, "errors": [f"release evidence manifest file does not exist: {path}"]}
    verification = verify_release_evidence_pack.verify_release_evidence_pack(path)
    errors = list(verification.get("manifest_errors") or [])
    for check in verification.get("checks") or []:
        if isinstance(check, dict) and check.get("status") == "failed":
            errors.extend(f"{check.get('name')}: {error}" for error in check.get("errors") or [])
    return {
        "status": verification["status"],
        "summary": verification["summary"],
        "errors": errors,
        "verification": verification,
    }


def validate_evidence_verification(path: Path, *, expected_manifest: Path) -> dict[str, Any]:
    payload = safe_read_json(path)
    if payload is None:
        return {"status": "failed", "summary": {}, "errors": [f"release evidence verification file is not valid JSON: {path}"]}
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("release evidence verification schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"release evidence verification status must be passed, got {payload.get('status') or '<missing>'}")
    validate_report_path(payload, "manifest_path", expected_manifest, "release evidence verification", errors)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if summary.get("failed_count") not in {0, None}:
        errors.append("release evidence verification summary has failed checks")
    if summary.get("manifest_error_count") not in {0, None}:
        errors.append("release evidence verification summary has manifest errors")
    return {"status": "passed" if not errors else "failed", "summary": summary, "errors": errors}


def validate_handoff_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "failed", "summary": {}, "errors": [f"release handoff manifest file does not exist: {path}"]}
    verification = verify_release_handoff_bundle.verify_release_handoff_bundle(path, base_dir=REPO_ROOT)
    if verification["status"] != "passed" and path.parent.resolve() != REPO_ROOT.resolve():
        local_verification = verify_release_handoff_bundle.verify_release_handoff_bundle(path, base_dir=path.parent)
        if local_verification["status"] == "passed":
            verification = local_verification
    errors = list(verification.get("manifest_errors") or [])
    for check in verification.get("checks") or []:
        if isinstance(check, dict) and check.get("status") == "failed":
            errors.extend(f"{check.get('name')}: {error}" for error in check.get("errors") or [])
    return {
        "status": verification["status"],
        "summary": verification["summary"],
        "errors": errors,
    }


def validate_handoff_verification(path: Path, *, expected_manifest: Path | None) -> dict[str, Any]:
    payload = safe_read_json(path)
    if payload is None:
        return {"status": "failed", "summary": {}, "errors": [f"release handoff verification file is not valid JSON: {path}"]}
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("release handoff verification schema_version must be 1")
    if payload.get("status") != "passed":
        errors.append(f"release handoff verification status must be passed, got {payload.get('status') or '<missing>'}")
    if expected_manifest is None:
        errors.append("release handoff verification cannot be validated without a handoff manifest")
    else:
        validate_report_path(payload, "manifest_path", expected_manifest, "release handoff verification", errors)
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    if summary.get("failed_count") not in {0, None}:
        errors.append("release handoff verification summary has failed checks")
    if summary.get("manifest_error_count") not in {0, None}:
        errors.append("release handoff verification summary has manifest errors")
    return {"status": "passed" if not errors else "failed", "summary": summary, "errors": errors}


def build_evidence_artifact_entries(verification: Any, base_dir: Path) -> list[dict[str, Any]]:
    if not isinstance(verification, dict):
        return []
    checks = verification.get("checks")
    if not isinstance(checks, list):
        return []
    artifacts: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        path = Path(str(check["path"])) if check.get("path") else None
        artifacts.append(
            {
                "name": f"release_evidence_artifact:{check.get('name') or '<unnamed>'}",
                "required": False,
                "status": check.get("status") or "failed",
                "path": str(path) if path else None,
                "relative_path": relative_path(path, base_dir) if path else check.get("relative_path"),
                "size_bytes": check.get("actual_size_bytes"),
                "sha256": check.get("actual_sha256"),
                "summary": {
                    "artifact_status": check.get("artifact_status"),
                    "expected_size_bytes": check.get("expected_size_bytes"),
                    "expected_sha256": check.get("expected_sha256"),
                },
                "errors": list(check.get("errors") or []),
                "warnings": [],
            }
        )
    return artifacts


def build_frontend_gate_policy(
    preflight_payload: dict[str, Any] | None,
    *,
    allow_skipped_frontend: bool,
    frontend_build_job: str | None,
    frontend_artifact_name: str | None,
) -> dict[str, Any]:
    options = preflight_payload.get("options") if isinstance(preflight_payload, dict) else {}
    options = options if isinstance(options, dict) else {}
    preflight_skipped = bool(options.get("skip_frontend"))
    errors: list[str] = []
    warnings: list[str] = []
    if preflight_skipped and not allow_skipped_frontend:
        errors.append("preflight skipped frontend build without allow_skipped_frontend policy")
    if preflight_skipped and allow_skipped_frontend and not frontend_build_job:
        warnings.append("preflight skipped frontend build; no covering CI job name was recorded")
    return {
        "preflight_skipped": preflight_skipped,
        "allow_skipped_frontend": allow_skipped_frontend,
        "covered_by_ci_job": frontend_build_job,
        "frontend_artifact_name": frontend_artifact_name,
        "errors": errors,
        "warnings": warnings,
    }


def validate_report_path(payload: dict[str, Any], key: str, expected_path: Path, label: str, errors: list[str]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        errors.append(f"{label} {key} is required")
        return
    try:
        actual = resolve_repo_path(Path(value)).resolve()
        expected = expected_path.resolve()
    except OSError as exc:
        errors.append(f"{label} {key} could not be resolved: {exc}")
        return
    if actual != expected:
        errors.append(f"{label} {key} must match: {actual} != {expected}")


def build_summary(artifacts: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> dict[str, Any]:
    failed = [artifact for artifact in artifacts if artifact["status"] == "failed"]
    return {
        "artifact_count": len(artifacts),
        "passed_count": sum(1 for artifact in artifacts if artifact["status"] == "passed"),
        "failed_count": len(failed),
        "skipped_count": sum(1 for artifact in artifacts if artifact["status"] == "skipped"),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "failed_artifacts": [artifact["name"] for artifact in failed],
    }


def github_context(env: dict[str, str]) -> dict[str, Any]:
    context = {
        "server_url": env.get("GITHUB_SERVER_URL"),
        "repository": env.get("GITHUB_REPOSITORY"),
        "run_id": env.get("GITHUB_RUN_ID"),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "workflow": env.get("GITHUB_WORKFLOW"),
        "job": env.get("GITHUB_JOB"),
        "ref": env.get("GITHUB_REF"),
        "sha": env.get("GITHUB_SHA"),
        "actor": env.get("GITHUB_ACTOR"),
    }
    if context["server_url"] and context["repository"] and context["run_id"]:
        context["run_url"] = f"{context['server_url']}/{context['repository']}/actions/runs/{context['run_id']}"
    else:
        context["run_url"] = None
    return context


def resolve_repo_path(path: Path | None) -> Path:
    if path is None:
        raise ValueError("path is required")
    return path if path.is_absolute() else REPO_ROOT / path


def relative_path(path: Path, base_dir: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return path.name


def safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = resolve_repo_path(path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a top-level manifest for GitHub CI release evidence artifacts.")
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_REPORT)
    parser.add_argument("--preflight-verification", type=Path, default=DEFAULT_PREFLIGHT_VERIFICATION)
    parser.add_argument("--dependency-inventory", type=Path, default=DEFAULT_DEPENDENCY_INVENTORY)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--handoff-manifest", type=Path)
    parser.add_argument("--handoff-verification", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-skipped-frontend", action="store_true")
    parser.add_argument("--frontend-build-job", default=None)
    parser.add_argument("--frontend-artifact-name", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_ci_evidence_manifest(
        preflight_report=args.preflight_report,
        preflight_verification=args.preflight_verification,
        dependency_inventory=args.dependency_inventory,
        evidence_dir=args.evidence_dir,
        handoff_manifest=args.handoff_manifest,
        handoff_verification=args.handoff_verification,
        output_path=args.output,
        allow_skipped_frontend=args.allow_skipped_frontend,
        frontend_build_job=args.frontend_build_job,
        frontend_artifact_name=args.frontend_artifact_name,
    )
    write_json(args.output, manifest)
    summary = manifest["summary"]
    print(
        "ci evidence manifest "
        f"{manifest['status']} "
        f"artifacts={summary['artifact_count']} "
        f"errors={summary['error_count']} "
        f"warnings={summary['warning_count']}",
        flush=True,
    )
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
