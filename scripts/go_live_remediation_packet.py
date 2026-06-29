from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))

import external_acceptance_audit
import production_env_audit
import repository_hygiene_audit


DEFAULT_OUTPUT_DIR = Path("artifacts/go-live-remediation")
DEFAULT_ENV_EXAMPLE = Path(".env.production.example")
PACKET_MANIFEST_NAME = "go-live-remediation-packet.json"
PACKET_READINESS_NAME = "go-live-remediation-readiness.json"
PRODUCTION_ENV_AUDIT_OUTPUTS = ("production-env-audit.json",)
EXTERNAL_ACCEPTANCE_AUDIT_OUTPUTS = (
    "external-acceptance.json",
    "external-acceptance-refresh-report.json",
    "external-acceptance-audit.json",
)


def build_go_live_remediation_packet(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    go_live_report: Path | None = None,
    env_example: Path = DEFAULT_ENV_EXAMPLE,
) -> dict[str, Any]:
    resolved_output_dir = resolve_repo_path(output_dir)
    resolved_go_live_report = resolve_repo_path(go_live_report) if go_live_report else None
    resolved_env_example = resolve_repo_path(env_example)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    go_live_payload = read_json(resolved_go_live_report) if resolved_go_live_report and resolved_go_live_report.is_file() else {}
    blockers = [str(item) for item in go_live_payload.get("blockers", [])] if isinstance(go_live_payload, dict) else []
    handoff_manifest = str(go_live_payload.get("handoff_manifest") or "artifacts/release-handoff-bundle.json")
    handoff_verification = str(go_live_payload.get("handoff_verification") or "artifacts/release-handoff-verification.json")

    env_template_path = write_env_template(resolved_output_dir, resolved_env_example)
    env_draft_path = resolved_output_dir / "production-env.draft"
    env_draft_report = production_env_audit.build_production_env_draft(
        template_file=env_template_path,
        output_file=env_draft_path,
    )
    env_draft_report_path = write_json(resolved_output_dir / "production-env-draft-report.json", env_draft_report)
    external_template_path = write_json(
        resolved_output_dir / "external-acceptance.template.json",
        external_acceptance_audit.build_external_acceptance_template(),
    )
    evidence_dir = resolved_output_dir / "external-evidence-files"
    evidence_dir.mkdir(exist_ok=True)
    evidence_readme_path = write_evidence_readme(evidence_dir)
    command_path = write_command_script(
        resolved_output_dir,
        handoff_manifest=handoff_manifest,
        handoff_verification=handoff_verification,
    )
    readme_path = write_packet_readme(
        resolved_output_dir,
        env_template_path=env_template_path,
        env_draft_path=env_draft_path,
        env_draft_report_path=env_draft_report_path,
        external_template_path=external_template_path,
        command_path=command_path,
    )

    tasks = build_tasks(blockers, resolved_output_dir)
    packet = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pending" if any(task["status"] == "pending" for task in tasks) else "ready",
        "go_live_report": str(resolved_go_live_report) if resolved_go_live_report else None,
        "source_handoff_manifest": handoff_manifest,
        "source_handoff_verification": handoff_verification,
        "output_dir": str(resolved_output_dir),
        "summary": {
            "blocker_count": len(blockers),
            "pending_task_count": sum(1 for task in tasks if task["status"] == "pending"),
            "file_count": 7,
        },
        "remaining_blockers": blockers,
        "tasks": tasks,
        "files": [
            file_entry(env_template_path, "production env base template used to create the draft"),
            file_entry(env_draft_path, "production env draft with generated application secrets; copy to .env.production and fill external credentials"),
            file_entry(env_draft_report_path, "redacted production env draft completion report"),
            file_entry(external_template_path, "external acceptance template; copy to external-acceptance.draft.json and attach real evidence"),
            file_entry(evidence_readme_path, "instructions for external evidence file collection"),
            file_entry(command_path, "PowerShell command sequence for rebuilding final go-live evidence"),
            file_entry(readme_path, "human-readable remediation packet instructions"),
        ],
    }
    packet_path = write_json(resolved_output_dir / PACKET_MANIFEST_NAME, packet)
    packet["packet_path"] = str(packet_path)
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return packet


def build_go_live_remediation_packet_readiness(*, packet_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    resolved_packet_dir = resolve_repo_path(packet_dir)
    checks = [
        validate_packet_static_files(resolved_packet_dir),
        validate_packet_repository_hygiene(resolved_packet_dir),
        validate_packet_production_env(resolved_packet_dir),
        validate_packet_external_acceptance(resolved_packet_dir),
    ]
    failed_count = sum(1 for check in checks if check["status"] == "failed")
    pending_count = sum(1 for check in checks if check["status"] == "pending")
    passed_count = sum(1 for check in checks if check["status"] == "passed")
    status = "ready" if failed_count == 0 and pending_count == 0 else ("failed" if failed_count else "pending")
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "packet_dir": str(resolved_packet_dir),
        "summary": {
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "pending_check_count": pending_count,
            "failed_check_count": failed_count,
        },
        "checks": checks,
    }


def validate_packet_static_files(packet_dir: Path) -> dict[str, Any]:
    required_files = [
        PACKET_MANIFEST_NAME,
        "production-env.template",
        "production-env.draft",
        "production-env-draft-report.json",
        "external-acceptance.template.json",
        "external-evidence-files/README.md",
        "run-go-live-evidence.ps1",
        "README.md",
    ]
    missing = [name for name in required_files if not (packet_dir / name).is_file()]
    return readiness_check(
        "packet_static_files",
        "failed" if missing else "passed",
        errors=[f"required packet file is missing: {name}" for name in missing],
        summary={"required_file_count": len(required_files), "missing_file_count": len(missing)},
    )


def remove_stale_packet_outputs(packet_dir: Path, names: tuple[str, ...]) -> list[str]:
    removed: list[str] = []
    for name in names:
        path = packet_dir / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    return removed


def validate_packet_production_env(packet_dir: Path) -> dict[str, Any]:
    env_file = packet_dir / ".env.production"
    audit_file = packet_dir / "production-env-audit.json"
    if not env_file.is_file():
        removed = remove_stale_packet_outputs(packet_dir, PRODUCTION_ENV_AUDIT_OUTPUTS)
        return readiness_check(
            "production_env_audit",
            "pending",
            errors=[f"production env file is missing: {env_file}"],
            summary={
                "reason": "copy production-env.draft to .env.production and replace remaining placeholders",
                "removed_stale_output_count": len(removed),
                "removed_stale_outputs": removed,
            },
        )
    audit_report = production_env_audit.build_env_audit_report(env_file)
    write_json(audit_file, audit_report)
    errors = list(audit_report.get("errors") or [])
    status = "passed" if audit_report.get("status") == "passed" and not errors else "failed"
    return readiness_check(
        "production_env_audit",
        status,
        errors=errors,
        summary={
            **dict(audit_report.get("summary") or {}),
            "audit_status": audit_report.get("status"),
            "error_count": audit_report.get("error_count", len(errors)),
            "missing_recommended_count": len(audit_report.get("missing_recommended_keys") or []),
            "audit_file": str(audit_file),
        },
    )


def validate_packet_repository_hygiene(packet_dir: Path) -> dict[str, Any]:
    audit_file = packet_dir / "repository-hygiene-audit.json"
    audit_report = repository_hygiene_audit.build_repository_hygiene_audit()
    write_json(audit_file, audit_report)
    errors = list(audit_report.get("errors") or [])
    status = "passed" if audit_report.get("status") == "passed" and not errors else "failed"
    return readiness_check(
        "repository_hygiene_audit",
        status,
        errors=errors,
        summary={
            **dict(audit_report.get("summary") or {}),
            "audit_status": audit_report.get("status"),
            "audit_file": str(audit_file),
        },
    )


def validate_packet_external_acceptance(packet_dir: Path) -> dict[str, Any]:
    draft_file = packet_dir / "external-acceptance.draft.json"
    refreshed_file = packet_dir / "external-acceptance.json"
    refresh_report_file = packet_dir / "external-acceptance-refresh-report.json"
    audit_file = packet_dir / "external-acceptance-audit.json"
    if not draft_file.is_file():
        removed = remove_stale_packet_outputs(packet_dir, EXTERNAL_ACCEPTANCE_AUDIT_OUTPUTS)
        return readiness_check(
            "external_acceptance_audit",
            "pending",
            errors=[f"external acceptance draft file is missing: {draft_file}"],
            summary={
                "reason": "copy external-acceptance.template.json to external-acceptance.draft.json and attach real evidence",
                "removed_stale_output_count": len(removed),
                "removed_stale_outputs": removed,
            },
        )

    refresh_report = external_acceptance_audit.refresh_external_acceptance_evidence_metadata(
        acceptance_file=draft_file,
        output_file=refreshed_file,
    )
    write_json(refresh_report_file, refresh_report)
    if refresh_report.get("status") != "passed":
        errors = list(refresh_report.get("errors") or [])
        removed = remove_stale_packet_outputs(
            packet_dir,
            ("external-acceptance.json", "external-acceptance-audit.json"),
        )
        return readiness_check(
            "external_acceptance_audit",
            "failed",
            errors=errors,
            summary={
                **dict(refresh_report.get("summary") or {}),
                "refresh_status": refresh_report.get("status"),
                "refresh_report": str(refresh_report_file),
                "refreshed_manifest": str(refreshed_file),
                "removed_stale_output_count": len(removed),
                "removed_stale_outputs": removed,
            },
        )

    audit_report = external_acceptance_audit.build_external_acceptance_audit(
        acceptance_file=refreshed_file,
        require_acceptance_file=True,
    )
    write_json(audit_file, audit_report)
    errors = list(audit_report.get("errors") or [])
    status = "passed" if audit_report.get("status") == "passed" and not errors else "failed"
    return readiness_check(
        "external_acceptance_audit",
        status,
        errors=errors + compact_external_acceptance_area_errors(audit_report),
        summary={
            **dict(audit_report.get("summary") or {}),
            "refresh_status": refresh_report.get("status"),
            "audit_status": audit_report.get("status"),
            "refresh_report": str(refresh_report_file),
            "refreshed_manifest": str(refreshed_file),
            "audit_file": str(audit_file),
        },
    )


def compact_external_acceptance_area_errors(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for area in report.get("missing_areas") or []:
        errors.append(f"external acceptance area is missing: {area}")
    for item in report.get("invalid_areas") or []:
        area = item.get("area")
        for error in item.get("errors") or []:
            errors.append(f"external acceptance area {area}: {error}")
    for item in report.get("failed_evidence_files") or []:
        area = item.get("area")
        relative_path = item.get("relative_path")
        for error in item.get("errors") or []:
            errors.append(f"external acceptance evidence {area}/{relative_path}: {error}")
    return errors


def readiness_check(
    name: str,
    status: str,
    *,
    errors: list[str] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary or {},
        "errors": errors or [],
    }


def build_tasks(blockers: list[str], output_dir: Path) -> list[dict[str, Any]]:
    env_pending = any("production env audit" in blocker.lower() for blocker in blockers)
    external_pending = any("external acceptance audit" in blocker.lower() for blocker in blockers)
    repository_pending = any("repository hygiene audit" in blocker.lower() for blocker in blockers)
    return [
        {
            "id": "repository_hygiene_audit",
            "status": "pending" if repository_pending else "ready",
            "reason": "repository hygiene audit is required by go-live readiness"
            if repository_pending
            else "repository hygiene audit is regenerated by the final evidence script",
            "required_input": str(REPO_ROOT / ".gitignore"),
            "expected_output": str(output_dir / "repository-hygiene-audit.json"),
            "command": 'python scripts\\repository_hygiene_audit.py --output "<packet>\\repository-hygiene-audit.json"',
        },
        {
            "id": "production_env_audit",
            "status": "pending" if env_pending else "ready",
            "reason": "production env audit is required by go-live readiness" if env_pending else "no blocker in current go-live report",
            "required_input": str(output_dir / ".env.production"),
            "template": str(output_dir / "production-env.template"),
            "draft": str(output_dir / "production-env.draft"),
            "draft_report": str(output_dir / "production-env-draft-report.json"),
            "expected_output": str(output_dir / "production-env-audit.json"),
            "command": 'python scripts\\production_env_audit.py --env-file "<packet>\\.env.production" --output "<packet>\\production-env-audit.json"',
        },
        {
            "id": "external_acceptance_audit",
            "status": "pending" if external_pending else "ready",
            "reason": "external acceptance audit is required by go-live readiness" if external_pending else "no blocker in current go-live report",
            "required_input": str(output_dir / "external-acceptance.draft.json"),
            "template": str(output_dir / "external-acceptance.template.json"),
            "expected_refreshed_manifest": str(output_dir / "external-acceptance.json"),
            "expected_output": str(output_dir / "external-acceptance-audit.json"),
            "required_areas": list(external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS),
            "required_document_fields": ["environment", "reviewer", "reviewed_at"],
            "required_entry_fields": ["area", "status", "summary", "ticket", "evidence_files"],
            "required_evidence_fields": ["path", "description"],
            "command": 'python scripts\\external_acceptance_audit.py --refresh-evidence-metadata "<packet>\\external-acceptance.draft.json" --refreshed-output "<packet>\\external-acceptance.json" --output "<packet>\\external-acceptance-refresh-report.json"; python scripts\\external_acceptance_audit.py --acceptance-file "<packet>\\external-acceptance.json" --require-acceptance-file --output "<packet>\\external-acceptance-audit.json"',
        },
        {
            "id": "release_image_dependency_audit",
            "status": "ready",
            "reason": "keeps final handoff tied to the release image dependency inventory",
            "expected_outputs": [
                "artifacts\\dependency-inventory-release-image.json",
                "artifacts\\dependency-review-audit-release-image.json",
                "artifacts\\release-image-dependency-audit.json",
            ],
        },
        {
            "id": "final_handoff_and_go_live_readiness",
            "status": "pending" if blockers else "ready",
            "reason": "rerun after production env and external acceptance evidence are real and audited",
            "command_script": str(output_dir / "run-go-live-evidence.ps1"),
        },
    ]


def write_env_template(output_dir: Path, env_example: Path) -> Path:
    target = output_dir / "production-env.template"
    if env_example.is_file():
        shutil.copyfile(env_example, target)
    else:
        target.write_text(
            "\n".join(
                [
                    "# Create .env.production from this template before go-live.",
                    "APP_ENV=production",
                    "DATABASE_URL=postgresql+psycopg://app:<REPLACE_WITH_DB_PASSWORD>@<REPLACE_WITH_POSTGRES_HOST>:5432/packaging_nesting",
                    "REDIS_URL=rediss://:<REPLACE_WITH_REDIS_PASSWORD>@<REPLACE_WITH_REDIS_HOST>:6379/0",
                    "STORAGE_BACKEND=minio",
                    "TASK_EXECUTION_BACKEND=celery",
                    "AUTH_SECRET_KEY=<REPLACE_WITH_32_PLUS_RANDOM_CHARS>",
                    "DEFAULT_ADMIN_EMAIL=<REPLACE_WITH_ADMIN_EMAIL>",
                    "DEFAULT_ADMIN_PASSWORD=<REPLACE_WITH_INITIAL_ADMIN_PASSWORD_12_PLUS>",
                    "MINIO_ENDPOINT=<REPLACE_WITH_MINIO_ENDPOINT>",
                    "MINIO_BUCKET=packaging-prod",
                    "MINIO_ACCESS_KEY=<REPLACE_WITH_MINIO_ACCESS_KEY>",
                    "MINIO_SECRET_KEY=<REPLACE_WITH_MINIO_SECRET_12_PLUS>",
                    "SECURITY_HEADERS_ENABLED=true",
                    "SECURITY_HSTS_ENABLED=true",
                    "CORS_ORIGINS=https://<REPLACE_WITH_FRONTEND_HOST>",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    return target


def write_evidence_readme(evidence_dir: Path) -> Path:
    path = evidence_dir / "README.md"
    lines = [
        "# External evidence files",
        "",
        "Store real customer or supplier evidence files in this directory, then reference them from external-acceptance.draft.json using paths relative to that draft manifest.",
        "",
        "Required areas:",
        *[f"- {area}" for area in external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS],
        "",
        "Reference each evidence file from external-acceptance.draft.json using paths relative to that manifest.",
        "At the top level, fill environment, reviewer, and reviewed_at. reviewed_at must be a timezone-aware ISO datetime such as 2026-06-29T10:00:00Z.",
        "For each required area, set status to passed and fill summary, ticket, evidence_files[].path, and evidence_files[].description.",
        "Then run the packet command script. It refreshes size_bytes and sha256 automatically before auditing.",
        "The audit command recalculates both values and fails on missing files, path escapes, size mismatches, or hash mismatches.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_packet_readme(
    output_dir: Path,
    *,
    env_template_path: Path,
    env_draft_path: Path,
    env_draft_report_path: Path,
    external_template_path: Path,
    command_path: Path,
) -> Path:
    path = output_dir / "README.md"
    path.write_text(
        "\n".join(
            [
                "# Go-live remediation packet",
                "",
                "1. Copy production-env.draft to .env.production and replace every remaining placeholder with real production values.",
                "   The draft already contains generated AUTH_SECRET_KEY and DEFAULT_ADMIN_PASSWORD values.",
                "2. Copy external-acceptance.template.json to external-acceptance.draft.json, fill environment, reviewer, and timezone-aware reviewed_at, set every required area to passed, fill summary and ticket, and attach real evidence file paths with descriptions.",
                "3. Do not hand-fill size_bytes or sha256; run-go-live-evidence.ps1 refreshes them into external-acceptance.json before auditing.",
                "4. Run run-go-live-evidence.ps1 from the repository root.",
                "   The script writes go-live-remediation-readiness.json and a repository hygiene audit before the heavier release image and preflight gates.",
                "   The script stops at the first failing native command so the first error is the remediation target.",
                "   If required inputs are missing or evidence refresh fails, readiness removes stale audit outputs from earlier runs.",
                "",
                f"Env template: {env_template_path.name}",
                f"Env draft: {env_draft_path.name}",
                f"Env draft report: {env_draft_report_path.name}",
                f"External acceptance template: {external_template_path.name}",
                f"Readiness report: {PACKET_READINESS_NAME} (generated by the command script)",
                f"Command script: {command_path.name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_command_script(
    output_dir: Path,
    *,
    handoff_manifest: str,
    handoff_verification: str,
) -> Path:
    path = output_dir / "run-go-live-evidence.ps1"
    packet_dir = output_dir.relative_to(REPO_ROOT).as_posix().replace("/", "\\")
    content = f"""$ErrorActionPreference = "Stop"
$PacketDir = "{packet_dir}"
$EnvFile = Join-Path $PacketDir ".env.production"
$AcceptanceDraftFile = Join-Path $PacketDir "external-acceptance.draft.json"
$AcceptanceFile = Join-Path $PacketDir "external-acceptance.json"

function Invoke-NativeStep {{
  param(
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][scriptblock]$Command
  )
  Write-Output "==> $Name"
  & $Command
  if ($LASTEXITCODE -ne 0) {{
    throw "$Name failed with exit code $LASTEXITCODE"
  }}
}}

if (-not (Test-Path -LiteralPath $EnvFile)) {{
  throw "Create $EnvFile from production-env.draft and replace all remaining placeholders before running this script."
}}
if (-not (Test-Path -LiteralPath $AcceptanceDraftFile)) {{
  throw "Create $AcceptanceDraftFile from external-acceptance.template.json and attach real evidence before running this script."
}}

Invoke-NativeStep "production env audit" {{
  python scripts\\production_env_audit.py --env-file $EnvFile --output (Join-Path $PacketDir "production-env-audit.json")
}}
Invoke-NativeStep "external acceptance evidence refresh" {{
  python scripts\\external_acceptance_audit.py --refresh-evidence-metadata $AcceptanceDraftFile --refreshed-output $AcceptanceFile --output (Join-Path $PacketDir "external-acceptance-refresh-report.json")
}}
Invoke-NativeStep "external acceptance audit" {{
  python scripts\\external_acceptance_audit.py --acceptance-file $AcceptanceFile --require-acceptance-file --output (Join-Path $PacketDir "external-acceptance-audit.json")
}}
Invoke-NativeStep "go-live remediation readiness" {{
  python scripts\\go_live_remediation_packet.py --audit-packet $PacketDir --output (Join-Path $PacketDir "go-live-remediation-readiness.json")
}}
Invoke-NativeStep "repository hygiene audit" {{
  python scripts\\repository_hygiene_audit.py --output (Join-Path $PacketDir "repository-hygiene-audit.json")
}}
Invoke-NativeStep "release image dependency audit" {{
  python scripts\\release_image_dependency_audit.py --inventory-output artifacts\\dependency-inventory-release-image.json --review-output artifacts\\dependency-review-audit-release-image.json --output artifacts\\release-image-dependency-audit.json
}}
Invoke-NativeStep "release preflight" {{
  python scripts\\release_preflight.py --report-path artifacts\\release-preflight.json --inventory-path artifacts\\dependency-inventory-local.json --evidence-output-dir artifacts\\release-evidence --env-file $EnvFile --require-production-env --external-acceptance-file $AcceptanceFile --require-external-acceptance
}}
Invoke-NativeStep "release preflight verification" {{
  python scripts\\verify_release_preflight.py --report artifacts\\release-preflight.json --output artifacts\\release-preflight-verification.json
}}
Invoke-NativeStep "release evidence verification" {{
  python scripts\\verify_release_evidence_pack.py --manifest artifacts\\release-evidence\\release-evidence-pack.json --output artifacts\\release-evidence\\release-evidence-verification-extra.json
}}
Invoke-NativeStep "release handoff bundle" {{
  python scripts\\release_handoff_bundle.py --preflight-report artifacts\\release-preflight.json --preflight-verification artifacts\\release-preflight-verification.json --dependency-inventory artifacts\\dependency-inventory-release-image.json --dependency-review-audit artifacts\\dependency-review-audit-release-image.json --release-image-dependency-audit artifacts\\release-image-dependency-audit.json --output artifacts\\release-handoff-bundle.json
}}
Invoke-NativeStep "release handoff verification" {{
  python scripts\\verify_release_handoff_bundle.py --manifest artifacts\\release-handoff-bundle.json --output artifacts\\release-handoff-verification.json
}}
Invoke-NativeStep "go-live readiness audit" {{
  python scripts\\go_live_readiness_audit.py --handoff-manifest artifacts\\release-handoff-bundle.json --handoff-verification artifacts\\release-handoff-verification.json --output artifacts\\go-live-readiness.json
}}

Write-Output "Go-live evidence script completed. Review artifacts\\go-live-readiness.json for final status."
"""
    path.write_text(content, encoding="utf-8")
    return path


def file_entry(path: Path, purpose: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "relative_path": path.relative_to(REPO_ROOT).as_posix() if path.is_relative_to(REPO_ROOT) else path.name,
        "size_bytes": path.stat().st_size,
        "purpose": purpose,
    }


def resolve_repo_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else REPO_ROOT / path


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate templates and commands for remaining go-live readiness blockers.")
    parser.add_argument("--go-live-report", type=Path, help="Existing go-live readiness JSON report.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--env-example", type=Path, default=DEFAULT_ENV_EXAMPLE)
    parser.add_argument("--audit-packet", type=Path, help="Audit a filled go-live remediation packet directory.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path for --audit-packet.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.audit_packet:
        report = build_go_live_remediation_packet_readiness(packet_dir=args.audit_packet)
        output_path = args.output or (resolve_repo_path(args.audit_packet) / PACKET_READINESS_NAME)
        write_json(output_path, report)
        summary = report["summary"]
        print(
            "go-live remediation readiness "
            f"{report['status']} "
            f"path={output_path} "
            f"passed={summary['passed_check_count']} "
            f"pending={summary['pending_check_count']} "
            f"failed={summary['failed_check_count']}",
            flush=True,
        )
        return 0 if report["status"] == "ready" else 1
    packet = build_go_live_remediation_packet(
        output_dir=args.output_dir,
        go_live_report=args.go_live_report,
        env_example=args.env_example,
    )
    print(
        "go-live remediation packet "
        f"{packet['status']} "
        f"path={packet['packet_path']} "
        f"pending_tasks={packet['summary']['pending_task_count']} "
        f"blockers={packet['summary']['blocker_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
