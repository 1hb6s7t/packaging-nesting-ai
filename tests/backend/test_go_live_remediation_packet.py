from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "go_live_remediation_packet.py"


def load_go_live_remediation_packet_module():
    spec = importlib.util.spec_from_file_location("go_live_remediation_packet", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_go_live_remediation_packet_writes_templates_and_command_script(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    go_live_report = output_dir / "go-live-readiness.json"
    go_live_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "failed",
                "handoff_manifest": "tmp/release-handoff-bundle.json",
                "handoff_verification": "tmp/release-handoff-verification.json",
                "blockers": [
                    "production env audit artifact must be passed, got skipped",
                    "external acceptance audit artifact must be passed, got skipped",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    packet = module.build_go_live_remediation_packet(
        output_dir=output_dir,
        go_live_report=go_live_report,
    )

    assert packet["status"] == "pending"
    assert packet["summary"]["blocker_count"] == 2
    assert packet["summary"]["pending_task_count"] == 3
    assert packet["summary"]["file_count"] == 7
    assert (output_dir / "production-env.template").is_file()
    assert (output_dir / "production-env.draft").is_file()
    assert (output_dir / "production-env-draft-report.json").is_file()
    assert (output_dir / "external-acceptance.template.json").is_file()
    assert (output_dir / "external-evidence-files" / "README.md").is_file()
    assert (output_dir / "run-go-live-evidence.ps1").is_file()
    assert (output_dir / "go-live-remediation-packet.json").is_file()

    tasks = {item["id"]: item for item in packet["tasks"]}
    assert tasks["repository_hygiene_audit"]["status"] == "ready"
    assert tasks["production_env_audit"]["status"] == "pending"
    assert tasks["production_env_audit"]["draft"].endswith("production-env.draft")
    assert tasks["production_env_audit"]["draft_report"].endswith("production-env-draft-report.json")
    assert tasks["production_env_audit"]["expected_verification"].endswith("production-env-verification.json")
    assert tasks["external_acceptance_audit"]["status"] == "pending"
    assert tasks["external_acceptance_audit"]["required_document_fields"] == [
        "environment",
        "reviewer",
        "reviewed_at",
    ]
    assert tasks["external_acceptance_audit"]["required_entry_fields"] == [
        "area",
        "status",
        "summary",
        "ticket",
        "evidence_files",
    ]
    assert tasks["external_acceptance_audit"]["required_evidence_fields"] == ["path", "description"]
    assert tasks["external_acceptance_audit"]["expected_verification"].endswith(
        "external-acceptance-verification.json"
    )
    assert tasks["release_image_dependency_audit"]["status"] == "ready"
    assert "artifacts\\dependency-review-verification-release-image.json" in tasks["release_image_dependency_audit"][
        "expected_outputs"
    ]
    assert "artifacts\\release-image-dependency-verification.json" in tasks["release_image_dependency_audit"][
        "expected_outputs"
    ]
    assert tasks["final_handoff_and_go_live_readiness"]["status"] == "pending"
    assert "artifacts\\release-evidence\\production-env-verification.json" in tasks[
        "final_handoff_and_go_live_readiness"
    ]["expected_outputs"]
    assert "artifacts\\release-evidence\\external-acceptance-verification.json" in tasks[
        "final_handoff_and_go_live_readiness"
    ]["expected_outputs"]
    assert "artifacts\\dependency-review-verification-release-image.json" in tasks[
        "final_handoff_and_go_live_readiness"
    ]["expected_outputs"]

    command_script = (output_dir / "run-go-live-evidence.ps1").read_text(encoding="utf-8")
    assert "function Invoke-NativeStep" in command_script
    assert 'throw "$Name failed with exit code $LASTEXITCODE"' in command_script
    assert 'Invoke-NativeStep "production env audit"' in command_script
    assert 'Invoke-NativeStep "production env audit verification"' in command_script
    assert 'Invoke-NativeStep "external acceptance audit verification"' in command_script
    assert 'Invoke-NativeStep "go-live readiness audit"' in command_script
    assert "production-env.draft" in command_script
    assert "verify_production_env_audit.py" in command_script
    assert "production-env-verification.json" in command_script
    assert "repository_hygiene_audit.py" in command_script
    assert "release_image_dependency_audit.py" in command_script
    assert 'Invoke-NativeStep "release image dependency review verification"' in command_script
    assert 'Invoke-NativeStep "release image dependency audit verification"' in command_script
    assert 'Invoke-NativeStep "release evidence production env verification"' in command_script
    assert 'Invoke-NativeStep "release evidence external acceptance verification"' in command_script
    assert "verify_dependency_review_audit.py" in command_script
    assert "dependency-review-verification-release-image.json" in command_script
    assert "verify_release_image_dependency_audit.py" in command_script
    assert "release-image-dependency-verification.json" in command_script
    assert "artifacts\\release-evidence\\production-env-verification.json" in command_script
    assert "artifacts\\release-evidence\\external-acceptance-verification.json" in command_script
    assert "--dependency-inventory artifacts\\dependency-inventory-release-image.json" in command_script
    assert "--dependency-review-audit artifacts\\dependency-review-audit-release-image.json" in command_script
    assert "--dependency-review-verification artifacts\\dependency-review-verification-release-image.json" in command_script
    assert "--release-image-dependency-audit artifacts\\release-image-dependency-audit.json" in command_script
    assert "--release-image-dependency-verification artifacts\\release-image-dependency-verification.json" in command_script
    assert "--production-env-verification artifacts\\release-evidence\\production-env-verification.json" in command_script
    assert (
        "--external-acceptance-verification artifacts\\release-evidence\\external-acceptance-verification.json"
        in command_script
    )
    assert "--require-production-env" in command_script
    assert "--require-external-acceptance" in command_script
    assert "--refresh-evidence-metadata $AcceptanceDraftFile" in command_script
    assert "--refreshed-output $AcceptanceFile" in command_script
    assert "verify_external_acceptance_audit.py" in command_script
    assert "external-acceptance-verification.json" in command_script
    assert "--audit-packet $PacketDir" in command_script
    assert "go-live-remediation-readiness.json" in command_script
    assert 'external-acceptance.draft.json' in command_script
    packet_readme = (output_dir / "README.md").read_text(encoding="utf-8")
    evidence_readme = (output_dir / "external-evidence-files" / "README.md").read_text(encoding="utf-8")
    assert "timezone-aware reviewed_at" in packet_readme
    assert "stops at the first failing native command" in packet_readme
    assert "production-env-verification.json" in packet_readme
    assert "release-evidence production env and external acceptance audits" in packet_readme
    assert "removes stale audit outputs" in packet_readme
    assert "dependency-review-verification-release-image.json" in packet_readme
    assert "release-image-dependency-verification.json" in packet_readme
    assert "reviewed_at must be a timezone-aware ISO datetime" in evidence_readme
    assert "fill summary and ticket" in packet_readme
    assert "descriptions" in packet_readme
    assert "summary, ticket, evidence_files[].path, and evidence_files[].description" in evidence_readme

    external_template = json.loads((output_dir / "external-acceptance.template.json").read_text(encoding="utf-8"))
    assert [entry["area"] for entry in external_template["entries"]] == list(
        module.external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS
    )
    for entry in external_template["entries"]:
        assert entry["evidence_files"] == [
            {
                "path": f"external-evidence-files/{entry['area']}.json",
                "size_bytes": 0,
                "sha256": "",
                "description": "",
            }
        ]
    env_draft = module.production_env_audit.parse_env_file(output_dir / "production-env.draft")
    env_draft_report = json.loads((output_dir / "production-env-draft-report.json").read_text(encoding="utf-8"))
    assert env_draft.errors == []
    assert env_draft_report["status"] == "pending"
    assert env_draft_report["summary"]["generated_secret_count"] == 2
    assert env_draft_report["generated_secret_keys"] == ["AUTH_SECRET_KEY", "DEFAULT_ADMIN_PASSWORD"]
    assert env_draft.values["AUTH_SECRET_KEY"] not in json.dumps(env_draft_report, ensure_ascii=False)
    assert env_draft.values["DEFAULT_ADMIN_PASSWORD"] not in json.dumps(env_draft_report, ensure_ascii=False)


def test_go_live_remediation_packet_marks_repository_hygiene_blocker_pending(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    go_live_report = output_dir / "go-live-readiness.json"
    go_live_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "failed",
                "blockers": ["repository hygiene audit artifact is missing"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    packet = module.build_go_live_remediation_packet(output_dir=output_dir, go_live_report=go_live_report)

    tasks = {item["id"]: item for item in packet["tasks"]}
    assert tasks["repository_hygiene_audit"]["status"] == "pending"
    assert tasks["repository_hygiene_audit"]["required_input"].endswith(".gitignore")


def test_go_live_remediation_packet_marks_evidence_ready_when_report_has_no_blockers(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    go_live_report = output_dir / "go-live-readiness.json"
    go_live_report.write_text(
        json.dumps({"schema_version": 1, "status": "passed", "blockers": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    packet = module.build_go_live_remediation_packet(output_dir=output_dir, go_live_report=go_live_report)

    assert packet["status"] == "ready"
    assert packet["summary"]["pending_task_count"] == 0


def test_go_live_remediation_packet_readiness_is_pending_until_real_inputs_exist(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    module.build_go_live_remediation_packet(output_dir=output_dir)

    report = module.build_go_live_remediation_packet_readiness(packet_dir=output_dir)

    assert report["status"] == "pending"
    assert report["summary"]["passed_check_count"] == 2
    assert report["summary"]["pending_check_count"] == 2
    checks = {item["name"]: item for item in report["checks"]}
    assert checks["packet_static_files"]["status"] == "passed"
    assert checks["repository_hygiene_audit"]["status"] == "passed"
    assert checks["production_env_audit"]["status"] == "pending"
    assert checks["external_acceptance_audit"]["status"] == "pending"


def test_go_live_remediation_packet_readiness_passes_with_filled_inputs(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    module.build_go_live_remediation_packet(output_dir=output_dir)
    write_completed_production_env(module, output_dir)
    write_completed_external_acceptance_draft(module, output_dir)

    exit_code = module.main(
        [
            "--audit-packet",
            str(output_dir),
            "--output",
            str(output_dir / "go-live-remediation-readiness.json"),
        ]
    )

    report = json.loads((output_dir / "go-live-remediation-readiness.json").read_text(encoding="utf-8"))
    env_audit = json.loads((output_dir / "production-env-audit.json").read_text(encoding="utf-8"))
    acceptance_refresh = json.loads((output_dir / "external-acceptance-refresh-report.json").read_text(encoding="utf-8"))
    acceptance_audit = json.loads((output_dir / "external-acceptance-audit.json").read_text(encoding="utf-8"))
    refreshed_acceptance = json.loads((output_dir / "external-acceptance.json").read_text(encoding="utf-8"))
    env_values = module.production_env_audit.parse_env_file(output_dir / ".env.production").values

    assert exit_code == 0
    assert report["status"] == "ready"
    assert report["summary"]["failed_check_count"] == 0
    assert report["summary"]["pending_check_count"] == 0
    assert report["summary"]["passed_check_count"] == 4
    assert env_audit["status"] == "passed"
    assert acceptance_refresh["status"] == "passed"
    assert acceptance_audit["status"] == "passed"
    assert acceptance_refresh["summary"]["updated_evidence_file_count"] == len(
        module.external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS
    )
    for entry in refreshed_acceptance["entries"]:
        evidence = entry["evidence_files"][0]
        evidence_path = output_dir / evidence["path"]
        assert evidence["size_bytes"] == evidence_path.stat().st_size
        assert evidence["sha256"] == module.external_acceptance_audit.sha256_file(evidence_path)
    serialized_report = json.dumps(report, ensure_ascii=False)
    assert env_values["AUTH_SECRET_KEY"] not in serialized_report
    assert env_values["DEFAULT_ADMIN_PASSWORD"] not in serialized_report


def test_go_live_remediation_packet_readiness_removes_stale_outputs_when_inputs_are_missing(tmp_path: Path) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    module.build_go_live_remediation_packet(output_dir=output_dir)
    write_completed_production_env(module, output_dir)
    write_completed_external_acceptance_draft(module, output_dir)

    assert module.main(["--audit-packet", str(output_dir)]) == 0
    assert (output_dir / "production-env-audit.json").is_file()
    assert (output_dir / "external-acceptance.json").is_file()
    assert (output_dir / "external-acceptance-refresh-report.json").is_file()
    assert (output_dir / "external-acceptance-audit.json").is_file()

    (output_dir / ".env.production").unlink()
    (output_dir / "external-acceptance.draft.json").unlink()

    report = module.build_go_live_remediation_packet_readiness(packet_dir=output_dir)
    checks = {item["name"]: item for item in report["checks"]}

    assert report["status"] == "pending"
    assert checks["production_env_audit"]["status"] == "pending"
    assert checks["production_env_audit"]["summary"]["removed_stale_outputs"] == ["production-env-audit.json"]
    assert checks["external_acceptance_audit"]["status"] == "pending"
    assert checks["external_acceptance_audit"]["summary"]["removed_stale_outputs"] == [
        "external-acceptance.json",
        "external-acceptance-refresh-report.json",
        "external-acceptance-audit.json",
    ]
    assert not (output_dir / "production-env-audit.json").exists()
    assert not (output_dir / "external-acceptance.json").exists()
    assert not (output_dir / "external-acceptance-refresh-report.json").exists()
    assert not (output_dir / "external-acceptance-audit.json").exists()


def test_go_live_remediation_packet_readiness_removes_stale_acceptance_outputs_on_refresh_failure(
    tmp_path: Path,
) -> None:
    module = load_go_live_remediation_packet_module()
    output_dir = repo_tmp_dir(tmp_path)
    module.build_go_live_remediation_packet(output_dir=output_dir)
    write_completed_production_env(module, output_dir)
    write_completed_external_acceptance_draft(module, output_dir)

    assert module.main(["--audit-packet", str(output_dir)]) == 0
    stale_manifest = output_dir / "external-acceptance.json"
    stale_audit = output_dir / "external-acceptance-audit.json"
    refresh_report = output_dir / "external-acceptance-refresh-report.json"
    assert stale_manifest.is_file()
    assert stale_audit.is_file()
    assert refresh_report.is_file()

    first_area = module.external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS[0]
    (output_dir / "external-evidence-files" / f"{first_area}.json").unlink()

    report = module.build_go_live_remediation_packet_readiness(packet_dir=output_dir)
    checks = {item["name"]: item for item in report["checks"]}
    acceptance_check = checks["external_acceptance_audit"]

    assert report["status"] == "failed"
    assert acceptance_check["status"] == "failed"
    assert acceptance_check["summary"]["refresh_status"] == "failed"
    assert acceptance_check["summary"]["removed_stale_outputs"] == [
        "external-acceptance.json",
        "external-acceptance-audit.json",
    ]
    assert refresh_report.is_file()
    assert json.loads(refresh_report.read_text(encoding="utf-8"))["status"] == "failed"
    assert not stale_manifest.exists()
    assert not stale_audit.exists()


def repo_tmp_dir(tmp_path: Path) -> Path:
    path = REPO_ROOT / "tmp" / "pytest-go-live-remediation" / tmp_path.name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_completed_production_env(module, output_dir: Path) -> None:
    draft_path = output_dir / "production-env.draft"
    env_path = output_dir / ".env.production"
    text = draft_path.read_text(encoding="utf-8")
    replacements = {
        "<REPLACE_WITH_DB_PASSWORD_12_PLUS>": "StrongDbPassword123!",
        "<REPLACE_WITH_POSTGRES_HOST>": "postgres.prod.internal",
        "<REPLACE_WITH_REDIS_PASSWORD>": "StrongRedisPassword123!",
        "<REPLACE_WITH_REDIS_HOST>": "redis.prod.internal",
        "<REPLACE_WITH_ADMIN_EMAIL>": "ops-admin@packaging-prod.internal",
        "<REPLACE_WITH_MINIO_ENDPOINT>": "minio.prod.internal:9000",
        "<REPLACE_WITH_MINIO_ACCESS_KEY>": "prod-minio-access-key",
        "<REPLACE_WITH_MINIO_SECRET_12_PLUS>": "StrongMinioSecret123!",
        "<REPLACE_WITH_FRONTEND_HOST>": "planner.prod.internal",
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    env_path.write_text(text, encoding="utf-8")
    assert module.production_env_audit.build_env_audit_report(env_path)["status"] == "passed"


def write_completed_external_acceptance_draft(module, output_dir: Path) -> None:
    evidence_dir = output_dir / "external-evidence-files"
    entries = []
    for area in module.external_acceptance_audit.REQUIRED_ACCEPTANCE_AREAS:
        evidence_path = evidence_dir / f"{area}.json"
        evidence_path.write_text(json.dumps({"area": area, "status": "passed"}, ensure_ascii=False), encoding="utf-8")
        entries.append(
            {
                "area": area,
                "status": "passed",
                "summary": f"{area} accepted with real go-live evidence",
                "ticket": "GO-LIVE-1",
                "evidence_files": [
                    {
                        "path": f"external-evidence-files/{area}.json",
                        "size_bytes": 1,
                        "sha256": "0" * 64,
                        "description": f"{area} acceptance evidence",
                    }
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "environment": "customer-production-2026-06-29",
        "reviewer": "delivery-owner",
        "reviewed_at": "2026-06-29T10:00:00Z",
        "entries": entries,
    }
    (output_dir / "external-acceptance.draft.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
