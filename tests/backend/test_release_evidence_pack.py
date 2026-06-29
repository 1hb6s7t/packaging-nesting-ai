from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_evidence_pack.py"


def load_release_evidence_pack_module():
    spec = importlib.util.spec_from_file_location("release_evidence_pack", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_evidence_pack_writes_default_local_artifacts(tmp_path: Path, monkeypatch) -> None:
    module = load_release_evidence_pack_module()
    output_dir = tmp_path / "evidence"
    monkeypatch.setattr(module.release_inventory, "build_dependency_inventory", lambda repo_root: inventory_with_review_item())

    pack = module.build_release_evidence_pack(output_dir=output_dir)

    assert pack["status"] == "passed"
    assert pack["summary"]["artifact_count"] == 11
    assert pack["summary"]["passed_count"] == 8
    assert pack["summary"]["skipped_artifacts"] == [
        "production_env_audit",
        "external_acceptance_audit",
        "dependency_review_audit",
    ]
    by_name = {item["name"]: item for item in pack["artifacts"]}
    for name in {
        "deployment_compose_audit",
        "repository_hygiene_audit",
        "customer_sandbox_audit",
        "notification_channel_audit",
        "storage_export_audit",
        "conversion_supplier_audit",
        "solver_governance_audit",
        "dependency_inventory",
    }:
        assert by_name[name]["status"] == "passed"
        artifact_path = Path(by_name[name]["path"])
        assert artifact_path.exists()
        assert by_name[name]["relative_path"] == artifact_path.name
        assert by_name[name]["size_bytes"] == artifact_path.stat().st_size
        assert by_name[name]["sha256"] == module.sha256_file(artifact_path)
        assert by_name[name]["summary"]["sensitive_scan_status"] == "passed"
        assert by_name[name]["summary"]["sensitive_scan_failed_count"] == 0
    assert by_name["production_env_audit"]["status"] == "skipped"
    assert by_name["production_env_audit"]["path"] is None
    assert by_name["production_env_audit"]["relative_path"] is None
    assert by_name["production_env_audit"]["size_bytes"] is None
    assert by_name["production_env_audit"]["sha256"] is None
    assert by_name["dependency_review_audit"]["status"] == "skipped"
    assert by_name["dependency_review_audit"]["required"] is False
    assert by_name["dependency_review_audit"]["summary"]["review_required_count"] == 1
    assert by_name["external_acceptance_audit"]["status"] == "skipped"
    assert by_name["external_acceptance_audit"]["required"] is False
    assert by_name["external_acceptance_audit"]["summary"]["required_area_count"] == 5
    manifest = json.loads((output_dir / "release-evidence-pack.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "passed"
    assert manifest["manifest_path"] == str(output_dir / "release-evidence-pack.json")
    manifest_by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest_by_name["customer_sandbox_audit"]["sha256"] == by_name["customer_sandbox_audit"]["sha256"]


def test_release_evidence_pack_includes_production_env_when_provided(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()
    env_file = tmp_path / ".env.production"
    write_valid_env(env_file)
    output_dir = tmp_path / "evidence"

    pack = module.build_release_evidence_pack(output_dir=output_dir, env_file=env_file)

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "passed"
    assert pack["summary"]["required_failed_count"] == 0
    assert by_name["production_env_audit"]["status"] == "passed"
    assert by_name["dependency_review_audit"]["status"] in {"passed", "skipped"}
    production_report = json.loads((output_dir / "production-env-audit.json").read_text(encoding="utf-8"))
    assert production_report["status"] == "passed"
    assert production_report["sensitive_scan"]["status"] == "passed"
    serialized = json.dumps(production_report, ensure_ascii=False)
    assert "StrongProduction123!" not in serialized
    assert "prod-secret-key-with-at-least-32-chars" not in serialized
    assert "prod-minio-secret-123" not in serialized


def test_release_evidence_pack_can_require_production_env_file(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()

    pack = module.build_release_evidence_pack(output_dir=tmp_path / "evidence", require_production_env=True)

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "failed"
    assert pack["summary"]["required_failed_count"] == 1
    assert by_name["production_env_audit"]["status"] == "failed"
    assert by_name["production_env_audit"]["summary"]["reason"] == "--env-file was not provided"


def test_release_evidence_pack_can_require_dependency_review_file(tmp_path: Path, monkeypatch) -> None:
    module = load_release_evidence_pack_module()
    monkeypatch.setattr(module.release_inventory, "build_dependency_inventory", lambda repo_root: inventory_with_review_item())

    pack = module.build_release_evidence_pack(
        output_dir=tmp_path / "evidence",
        require_dependency_review=True,
    )

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "failed"
    assert by_name["dependency_review_audit"]["required"] is True
    assert by_name["dependency_review_audit"]["status"] == "failed"
    assert by_name["dependency_review_audit"]["summary"]["missing_ack_count"] == 1


def test_release_evidence_pack_can_require_external_acceptance_file(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()

    pack = module.build_release_evidence_pack(
        output_dir=tmp_path / "evidence",
        require_external_acceptance=True,
    )

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "failed"
    assert by_name["external_acceptance_audit"]["required"] is True
    assert by_name["external_acceptance_audit"]["status"] == "failed"
    assert by_name["external_acceptance_audit"]["summary"]["missing_area_count"] == 5


def test_release_evidence_pack_accepts_external_acceptance_file(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()
    output_dir = tmp_path / "evidence"
    acceptance_file = write_valid_external_acceptance(module.external_acceptance_audit, tmp_path)

    pack = module.build_release_evidence_pack(
        output_dir=output_dir,
        external_acceptance_file=acceptance_file,
        require_external_acceptance=True,
    )

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "passed"
    assert by_name["external_acceptance_audit"]["required"] is True
    assert by_name["external_acceptance_audit"]["status"] == "passed"
    report = json.loads((output_dir / "external-acceptance-audit.json").read_text(encoding="utf-8"))
    assert report["summary"]["passed_area_count"] == 5
    assert report["summary"]["verified_evidence_file_count"] == 5


def test_release_evidence_pack_accepts_dependency_review_file(tmp_path: Path, monkeypatch) -> None:
    module = load_release_evidence_pack_module()
    output_dir = tmp_path / "evidence"
    review_file = tmp_path / "dependency-review.json"
    monkeypatch.setattr(module.release_inventory, "build_dependency_inventory", lambda repo_root: inventory_with_review_item())
    review_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29T10:00:00Z",
                "entries": [
                    {
                        "ecosystem": "python",
                        "name": "ortools",
                        "scope": "runtime",
                        "version": "9.12.4544",
                        "license": None,
                        "decision": "approved",
                        "reason": "release image metadata reviewed by owner",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack = module.build_release_evidence_pack(
        output_dir=output_dir,
        dependency_review_file=review_file,
        require_dependency_review=True,
    )

    by_name = {item["name"]: item for item in pack["artifacts"]}
    assert pack["status"] == "passed"
    assert by_name["dependency_review_audit"]["required"] is True
    assert by_name["dependency_review_audit"]["status"] == "passed"
    report = json.loads((output_dir / "dependency-review-audit.json").read_text(encoding="utf-8"))
    assert report["summary"]["approved_count"] == 1


def test_cli_writes_manifest_and_returns_nonzero_when_artifact_fails(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()
    output_dir = tmp_path / "evidence"

    exit_code = module.main(["--simulate-storage-missing", "--output-dir", str(output_dir)])

    assert exit_code == 1
    manifest = json.loads((output_dir / "release-evidence-pack.json").read_text(encoding="utf-8"))
    by_name = {item["name"]: item for item in manifest["artifacts"]}
    assert manifest["status"] == "failed"
    assert by_name["storage_export_audit"]["status"] == "failed"
    assert "storage_export_audit" in manifest["summary"]["failed_artifacts"]


def test_release_evidence_artifact_redacts_and_fails_unredacted_sensitive_payload(tmp_path: Path) -> None:
    module = load_release_evidence_pack_module()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()

    artifact = module.run_report_artifact(
        name="leaky_report",
        filename="leaky.json",
        output_dir=output_dir,
        command=["python", "scripts\\leaky.py"],
        builder=lambda: {
            "status": "passed",
            "summary": {"check_count": 1},
            "metadata": {
                "webhook_url": "https://hooks.example.test/send?key=raw-key",
                "signature_secret": "plain-signature-secret",
                "signature_header": "X-Signature",
                "callback_token_hash": "safe-hash",
                "callback_token_tail": "123456",
                "access_token_ttl_minutes": 480,
            },
            "token_rotation": {
                "rotated": True,
                "old_token_tail": "123456",
                "new_token_tail": "abcdef",
            },
            "database_url": "postgresql://app:plain-db-password@db:5432/app",
        },
    )

    assert artifact["status"] == "failed"
    assert artifact["summary"]["sensitive_scan_status"] == "failed"
    assert artifact["summary"]["sensitive_scan_failed_count"] == 3
    assert artifact["size_bytes"] == (output_dir / "leaky.json").stat().st_size
    assert artifact["sha256"] == module.sha256_file(output_dir / "leaky.json")

    written = json.loads((output_dir / "leaky.json").read_text(encoding="utf-8"))
    assert written["metadata"]["webhook_url"] == "***"
    assert written["metadata"]["signature_secret"] == "***"
    assert written["metadata"]["signature_header"] == "X-Signature"
    assert written["metadata"]["callback_token_hash"] == "safe-hash"
    assert written["metadata"]["callback_token_tail"] == "123456"
    assert written["metadata"]["access_token_ttl_minutes"] == 480
    assert written["token_rotation"]["rotated"] is True
    assert written["token_rotation"]["old_token_tail"] == "123456"
    assert written["token_rotation"]["new_token_tail"] == "abcdef"
    assert written["database_url"] == "postgresql://app:***@db:5432/app"
    serialized = json.dumps(written, ensure_ascii=False)
    assert "raw-key" not in serialized
    assert "plain-signature-secret" not in serialized
    assert "plain-db-password" not in serialized
    assert written["sensitive_scan"]["findings"][0]["reason"]


def write_valid_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "APP_ENV=production",
                "DATABASE_URL=postgresql+psycopg://app:StrongDbPassword123!@db:5432/packaging",
                "REDIS_URL=rediss://:StrongRedisPassword123!@redis.prod.internal:6379/0",
                "STORAGE_BACKEND=minio",
                "TASK_EXECUTION_BACKEND=celery",
                "AUTH_SECRET_KEY=prod-secret-key-with-at-least-32-chars",
                "DEFAULT_ADMIN_EMAIL=ops-admin@packaging-prod.internal",
                "DEFAULT_ADMIN_PASSWORD=StrongProduction123!",
                "MINIO_ENDPOINT=minio.prod.internal:9000",
                "MINIO_BUCKET=packaging-prod",
                "MINIO_ACCESS_KEY=prod-minio-access",
                "MINIO_SECRET_KEY=prod-minio-secret-123",
                "SECURITY_HEADERS_ENABLED=true",
                "SECURITY_HSTS_ENABLED=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_valid_external_acceptance(external_module, tmp_path: Path) -> Path:
    entries = []
    for area in external_module.REQUIRED_ACCEPTANCE_AREAS:
        evidence_path = tmp_path / f"{area}.json"
        evidence_path.write_text(json.dumps({"area": area, "status": "passed"}, ensure_ascii=False), encoding="utf-8")
        entries.append(
            {
                "area": area,
                "status": "passed",
                "summary": f"{area} accepted by external owner",
                "ticket": "REL-EXT-1",
                "evidence_files": [
                    {
                        "path": evidence_path.name,
                        "size_bytes": evidence_path.stat().st_size,
                        "sha256": external_module.sha256_file(evidence_path),
                        "description": f"{area} evidence",
                    }
                ],
            }
        )
    acceptance_file = tmp_path / "external-acceptance.json"
    acceptance_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "environment": "customer-production-2026-06-29",
                "reviewer": "delivery-owner",
                "reviewed_at": "2026-06-29T10:00:00Z",
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return acceptance_file


def inventory_with_review_item() -> dict:
    return {
        "schema_version": 1,
        "summary": {
            "dependency_count": 1,
            "review_required_count": 1,
            "review_required": [
                {
                    "ecosystem": "python",
                    "name": "ortools",
                    "scope": "runtime",
                    "installed": True,
                    "version": "9.12.4544",
                    "license": None,
                    "reason": "missing license metadata",
                }
            ],
        },
        "dependencies": [],
    }
