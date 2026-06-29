from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.db.base import Base
from app.db import models as dbm  # noqa: F401
from app.services import repository, storage


@dataclass(frozen=True)
class AuditExport:
    export_id: str
    export_type: str
    storage_key: str
    original_payload: bytes


def build_storage_export_audit_report(*, simulate_missing: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "solution_id": None,
        "summary": {},
        "checks": [],
        "storage_contract": {},
        "policy_contract": {},
        "manifest": {},
        "recovery": {},
        "tamper_recovery": {},
        "version_drift_recovery": {},
        "errors": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="storage-export-audit-") as temp_dir:
            temp_path = Path(temp_dir)
            engine = create_engine(f"sqlite:///{(temp_path / 'audit.sqlite').as_posix()}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            try:
                with SessionLocal() as db:
                    solution_id = f"storage-audit-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
                    report["solution_id"] = solution_id
                    with temporary_local_storage_settings(temp_path / "objects"):
                        storage_contract = build_storage_contract(temp_path / "objects", solution_id)
                        exports = create_audit_exports(db, solution_id)
                        mark_export_expired(db, exports["dxf"].export_id)
                        if simulate_missing:
                            export_storage_path(exports["dxf"]).unlink(missing_ok=True)
                        manifest = repository.build_solution_export_manifest(db, solution_id)
                        recovery = repository.build_solution_export_recovery_report(
                            db, solution_id, include_archive_dry_run=True
                        )
                        dxf_row = db.get(dbm.SolutionExport, exports["dxf"].export_id)
                        tamper_recovery = build_tamper_recovery(db, exports["pdf_v2"], solution_id)
                        version_drift_recovery = build_version_drift_recovery(db, exports["pdf_v2"], solution_id)
                        checks = validate_audit_results(
                            storage_contract=storage_contract,
                            manifest=manifest,
                            recovery=recovery.model_dump(mode="json"),
                            tamper_recovery=tamper_recovery.model_dump(mode="json"),
                            version_drift_recovery=version_drift_recovery.model_dump(mode="json"),
                            dxf_active_after_dry_run=bool(dxf_row and dxf_row.lifecycle_status == "active"),
                        )
                        policy_contract = validate_storage_policy_contract(
                            solution_id=solution_id,
                            storage_contract=storage_contract,
                            manifest=manifest,
                            recovery=recovery.model_dump(mode="json"),
                        )
            finally:
                engine.dispose()
    except Exception as exc:
        report["errors"].append(str(exc))
        report["summary"] = build_summary(report)
        return report

    report["storage_contract"] = storage_contract
    report["policy_contract"] = policy_contract
    report["manifest"] = manifest
    report["recovery"] = recovery.model_dump(mode="json")
    report["tamper_recovery"] = tamper_recovery.model_dump(mode="json")
    report["version_drift_recovery"] = version_drift_recovery.model_dump(mode="json")
    report["checks"] = checks
    report["summary"] = build_summary(report)
    report["status"] = "passed" if report["summary"]["failed_count"] == 0 else "failed"
    return report


@contextmanager
def temporary_local_storage_settings(storage_root: Path):
    audit_settings = Settings(STORAGE_BACKEND="local", STORAGE_ROOT=storage_root)
    original_get_settings = storage.get_settings
    storage.get_settings = lambda: audit_settings
    try:
        yield audit_settings
    finally:
        storage.get_settings = original_get_settings


def build_storage_contract(storage_root: Path, solution_id: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    readiness: dict[str, Any] = {}
    probe: dict[str, Any] = {}
    minio_uri = f"minio://packaging-nesting/exports/{solution_id}/adapter-contract/probe.pdf"
    minio_parsed = storage.parse_storage_key(minio_uri)
    canonical_probe_key = f"exports/{solution_id}/adapter-contract/probe.txt"
    try:
        readiness = storage.readiness_check()
        normalized_probe_key = storage.normalize_object_key(
            f"exports\\{solution_id}\\adapter-contract\\probe.txt"
        )
        payload = b"storage adapter contract probe"
        stored = storage.write_bytes(normalized_probe_key, payload, content_type="text/plain")
        info = storage.inspect_object(stored.storage_key, version_id=stored.version_id)
        roundtrip = storage.read_bytes(stored.storage_key, version_id=stored.version_id)
        stored_path = Path(stored.storage_key)
        probe = {
            "storage_key": stored.storage_key,
            "backend": stored.backend,
            "object_key": stored.object_key,
            "size": stored.size,
            "etag": stored.etag,
            "version_id": stored.version_id,
            "inspect_exists": info.exists,
            "inspect_size": info.size,
            "inspect_etag": info.etag,
            "inspect_version_id": info.version_id,
            "roundtrip_size": len(roundtrip),
        }
        checks.extend(
            [
                check_result(
                    "storage root is absolute",
                    storage_root.is_absolute(),
                    str(storage_root),
                ),
                check_result(
                    "readiness writes local storage root",
                    readiness.get("backend") == "local" and readiness.get("writable") is True,
                    f"backend={readiness.get('backend')} writable={readiness.get('writable')}",
                ),
                check_result(
                    "adapter object key canonical",
                    stored.object_key == canonical_probe_key and "\\" not in stored.object_key,
                    f"object_key={stored.object_key}",
                ),
                check_result(
                    "local storage key uses configured root",
                    path_within(stored_path, storage_root),
                    f"storage_key={stored.storage_key}",
                ),
                check_result(
                    "adapter write/read roundtrip",
                    roundtrip == payload,
                    f"size={len(roundtrip)}",
                ),
                check_result(
                    "adapter inspect metadata complete",
                    info.exists
                    and info.size == len(payload)
                    and bool(info.etag)
                    and bool(info.version_id)
                    and info.version_id == stored.version_id,
                    f"exists={info.exists} size={info.size} etag={bool(info.etag)} version_id={info.version_id}",
                ),
                check_result(
                    "unsafe object keys rejected",
                    all(
                        raises_value_error(lambda key=key: storage.normalize_object_key(key))
                        for key in [
                            "../outside.txt",
                            "exports/../outside.txt",
                            "/absolute/outside.txt",
                            "C:/absolute/outside.txt",
                            "minio://bucket/outside.txt",
                        ]
                    ),
                    "rejects traversal, absolute paths, drive paths, and URI-style object keys",
                ),
                check_result(
                    "minio uri parser preserves bucket and object key",
                    minio_parsed == {
                        "backend": "minio",
                        "bucket": "packaging-nesting",
                        "object_key": f"exports/{solution_id}/adapter-contract/probe.pdf",
                    },
                    f"parsed={minio_parsed}",
                ),
            ]
        )
    except Exception as exc:
        checks.append(check_result("storage adapter contract executed", False, str(exc)))
    summary = {
        "check_count": len(checks),
        "failed_count": sum(1 for item in checks if item.get("status") != "passed"),
    }
    return {
        "status": "passed" if summary["failed_count"] == 0 else "failed",
        "storage_root": str(storage_root),
        "readiness": readiness,
        "probe": probe,
        "minio_uri_parse": minio_parsed,
        "checks": checks,
        "summary": summary,
    }


def create_audit_exports(db: Session, solution_id: str) -> dict[str, AuditExport]:
    return {
        "pdf_v1": create_export(
            db,
            solution_id=solution_id,
            export_id="audit_pdf_v1",
            export_type="pdf",
            payload=b"%PDF-1.4\nstorage audit pdf v1\n%%EOF\n",
        ),
        "pdf_v2": create_export(
            db,
            solution_id=solution_id,
            export_id="audit_pdf_v2",
            export_type="pdf",
            payload=b"%PDF-1.4\nstorage audit pdf v2\n%%EOF\n",
        ),
        "dxf": create_export(
            db,
            solution_id=solution_id,
            export_id="audit_dxf_v1",
            export_type="dxf",
            payload=b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n",
        ),
    }


def create_export(
    db: Session,
    *,
    solution_id: str,
    export_id: str,
    export_type: str,
    payload: bytes,
) -> AuditExport:
    object_key = f"exports/{solution_id}/{export_id}.{export_type}"
    content_type = "application/pdf" if export_type == "pdf" else "application/dxf"
    stored = storage.write_bytes(object_key, payload, content_type=content_type)
    info = storage.inspect_object(stored.storage_key, version_id=stored.version_id)
    repository.create_solution_export_record(
        db,
        export_id=export_id,
        solution_id=solution_id,
        export_type=export_type,
        storage_key=stored.storage_key,
        checksum=hashlib.sha256(payload).hexdigest(),
        storage_backend=stored.backend,
        storage_object_key=stored.object_key,
        storage_version_id=stored.version_id or info.version_id,
        storage_etag=stored.etag or info.etag,
        storage_size_bytes=stored.size,
    )
    return AuditExport(export_id=export_id, export_type=export_type, storage_key=stored.storage_key, original_payload=payload)


def mark_export_expired(db: Session, export_id: str) -> None:
    row = db.get(dbm.SolutionExport, export_id)
    if row is None:
        raise RuntimeError(f"export not found: {export_id}")
    row.retention_until = repository.utc_now() - timedelta(days=1)
    db.commit()


def build_tamper_recovery(db: Session, export: AuditExport, solution_id: str):
    export_storage_path(export).write_bytes(b"tampered storage export payload")
    return repository.build_solution_export_recovery_report(db, solution_id, include_archive_dry_run=False)


def build_version_drift_recovery(db: Session, export: AuditExport, solution_id: str):
    path = export_storage_path(export)
    path.write_bytes(export.original_payload)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    return repository.build_solution_export_recovery_report(db, solution_id, include_archive_dry_run=False)


def export_storage_path(export: AuditExport) -> Path:
    parsed = storage.parse_storage_key(export.storage_key)
    if parsed["backend"] != "local":
        raise RuntimeError("storage export audit uses the local storage adapter")
    return Path(export.storage_key)


def validate_audit_results(
    *,
    storage_contract: dict[str, Any],
    manifest: dict[str, Any],
    recovery: dict[str, Any],
    tamper_recovery: dict[str, Any],
    version_drift_recovery: dict[str, Any],
    dxf_active_after_dry_run: bool,
) -> list[dict[str, Any]]:
    exports = manifest.get("exports") or []
    pdf_v1 = next((item for item in exports if item.get("id") == "audit_pdf_v1"), {})
    pdf_v2 = next((item for item in exports if item.get("id") == "audit_pdf_v2"), {})
    dxf = next((item for item in exports if item.get("id") == "audit_dxf_v1"), {})
    tampered_pdf = next(
        (item for item in tamper_recovery.get("items", []) if item.get("export_id") == "audit_pdf_v2"),
        {},
    )
    drifted_pdf = next(
        (item for item in version_drift_recovery.get("items", []) if item.get("export_id") == "audit_pdf_v2"),
        {},
    )
    contract_checks = list(storage_contract.get("checks") or [])
    return contract_checks + [
        check_result("manifest has three exports", manifest.get("export_count") == 3, f"count={manifest.get('export_count')}"),
        check_result("pdf v1 superseded", pdf_v1.get("lifecycle_status") == "superseded", f"status={pdf_v1.get('lifecycle_status')}"),
        check_result("pdf v2 active", pdf_v2.get("lifecycle_status") == "active", f"status={pdf_v2.get('lifecycle_status')}"),
        check_result("expired dxf counted", manifest.get("expired_export_count") == 1, f"expired={manifest.get('expired_export_count')}"),
        check_result(
            "storage metadata complete",
            all(item.get("storage_exists") and item.get("storage_etag") and item.get("storage_version_id") for item in exports),
            "storage_exists, etag, and version_id present",
        ),
        check_result("recovery passed", recovery.get("status") == "passed", f"status={recovery.get('status')}"),
        check_result("recovery checked all exports", recovery.get("checked_count") == 3, f"checked={recovery.get('checked_count')}"),
        check_result(
            "archive dry-run non destructive",
            (recovery.get("archive_dry_run") or {}).get("archived_count") == 0 and dxf_active_after_dry_run,
            f"archived={(recovery.get('archive_dry_run') or {}).get('archived_count')} active_after={dxf_active_after_dry_run}",
        ),
        check_result("tamper detected", tampered_pdf.get("status") == "checksum_mismatch", f"status={tampered_pdf.get('status')}"),
        check_result("version drift detected", drifted_pdf.get("status") == "version_mismatch", f"status={drifted_pdf.get('status')}"),
        check_result("dxf active in manifest", dxf.get("lifecycle_status") == "active", f"status={dxf.get('lifecycle_status')}"),
    ]


def validate_storage_policy_contract(
    *,
    solution_id: str,
    storage_contract: dict[str, Any],
    manifest: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    exports = [item for item in manifest.get("exports") or [] if isinstance(item, dict)]
    recovery_items = [item for item in recovery.get("items") or [] if isinstance(item, dict)]
    archive_dry_run = recovery.get("archive_dry_run") if isinstance(recovery.get("archive_dry_run"), dict) else {}
    expired_exports = [item for item in exports if item.get("retention_until") and item.get("retention_until") <= str(manifest.get("generated_at") or "")]
    archive_targets = archive_dry_run.get("archived_exports") if isinstance(archive_dry_run.get("archived_exports"), list) else []
    archive_target_ids = sorted(str(item.get("id")) for item in archive_targets if isinstance(item, dict) and item.get("id"))
    expired_export_ids = sorted(str(item.get("id")) for item in expired_exports if item.get("id"))
    checks.extend(
        [
            policy_check(
                code="storage_contract.passed",
                status="passed" if storage_contract.get("status") == "passed" else "failed",
                message="storage adapter contract passed" if storage_contract.get("status") == "passed" else "storage adapter contract must pass",
                evidence={"status": storage_contract.get("status")},
            ),
            policy_check(
                code="manifest.solution_scope",
                status="passed" if manifest.get("solution_id") == solution_id and exports else "failed",
                message="manifest is scoped to the audited solution" if manifest.get("solution_id") == solution_id and exports else "manifest must be scoped to the audited solution and contain exports",
                evidence={"solution_id": manifest.get("solution_id"), "expected": solution_id, "export_count": len(exports)},
            ),
            policy_check(
                code="manifest.export_count",
                status="passed" if manifest.get("export_count") == len(exports) and len(exports) > 0 else "failed",
                message="manifest export_count matches listed exports" if manifest.get("export_count") == len(exports) and len(exports) > 0 else "manifest export_count must match listed exports",
                evidence={"export_count": manifest.get("export_count"), "listed_count": len(exports)},
            ),
            policy_check(
                code="recovery.solution_scope",
                status="passed" if recovery.get("solution_id") == solution_id else "failed",
                message="recovery report is scoped to the audited solution" if recovery.get("solution_id") == solution_id else "recovery report must be scoped to the audited solution",
                evidence={"solution_id": recovery.get("solution_id"), "expected": solution_id},
            ),
            policy_check(
                code="recovery.checked_count",
                status="passed" if recovery.get("checked_count") == len(exports) and len(recovery_items) == len(exports) else "failed",
                message="recovery drill checks every manifest export" if recovery.get("checked_count") == len(exports) and len(recovery_items) == len(exports) else "recovery drill must check every manifest export",
                evidence={
                    "checked_count": recovery.get("checked_count"),
                    "recovery_item_count": len(recovery_items),
                    "manifest_export_count": len(exports),
                },
            ),
            policy_check(
                code="recovery.status",
                status="passed" if recovery.get("status") == "passed" else "failed",
                message="recovery drill passed" if recovery.get("status") == "passed" else "recovery drill must pass before handoff",
                evidence={
                    "status": recovery.get("status"),
                    "missing_count": recovery.get("missing_count"),
                    "checksum_mismatch_count": recovery.get("checksum_mismatch_count"),
                    "version_mismatch_count": recovery.get("version_mismatch_count"),
                },
            ),
            policy_check(
                code="archive.dry_run.present",
                status="passed" if archive_dry_run.get("dry_run") is True and archive_dry_run.get("status") == "dry_run" else "failed",
                message="archive dry-run is included in recovery report" if archive_dry_run.get("dry_run") is True and archive_dry_run.get("status") == "dry_run" else "recovery report must include archive dry-run",
                evidence={"status": archive_dry_run.get("status"), "dry_run": archive_dry_run.get("dry_run")},
            ),
            policy_check(
                code="archive.dry_run.non_destructive",
                status="passed" if archive_dry_run.get("archived_count") == 0 else "failed",
                message="archive dry-run is non-destructive" if archive_dry_run.get("archived_count") == 0 else "archive dry-run must not mutate lifecycle state",
                evidence={"archived_count": archive_dry_run.get("archived_count")},
            ),
            policy_check(
                code="archive.expired_coverage",
                status="passed" if archive_target_ids == expired_export_ids else "failed",
                message="archive dry-run targets exactly the expired exports" if archive_target_ids == expired_export_ids else "archive dry-run must target every expired export and only expired exports",
                evidence={"expired_export_ids": expired_export_ids, "archive_target_ids": archive_target_ids},
            ),
        ]
    )
    checks.extend(validate_export_metadata_policy(solution_id=solution_id, exports=exports, recovery_items=recovery_items))
    checks.extend(validate_export_version_policy(exports))
    failed_count = sum(1 for check in checks if check["status"] == "failed")
    warning_count = sum(1 for check in checks if check["status"] == "warning")
    return {
        "status": "failed" if failed_count else "warning" if warning_count else "passed",
        "passed_count": sum(1 for check in checks if check["status"] == "passed"),
        "warning_count": warning_count,
        "failed_count": failed_count,
        "failed_checks": [check for check in checks if check["status"] == "failed"],
        "warning_checks": [check for check in checks if check["status"] == "warning"],
        "checks": checks,
    }


def validate_export_metadata_policy(
    *,
    solution_id: str,
    exports: list[dict[str, Any]],
    recovery_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    recovery_by_id = {str(item.get("export_id")): item for item in recovery_items if item.get("export_id")}
    for item in exports:
        export_id = str(item.get("id") or "")
        object_key = str(item.get("storage_object_key") or "")
        recovery = recovery_by_id.get(export_id, {})
        metadata_fields = [
            item.get("storage_backend"),
            item.get("storage_object_key"),
            item.get("storage_version_id"),
            item.get("storage_etag"),
            item.get("storage_size_bytes"),
            item.get("checksum"),
            item.get("retention_until"),
            item.get("download_path"),
        ]
        checks.extend(
            [
                policy_check(
                    code="export.metadata.complete",
                    status="passed" if all(present(value) for value in metadata_fields) else "failed",
                    export_id=export_id,
                    message="export storage and lifecycle metadata is complete" if all(present(value) for value in metadata_fields) else "export must include storage metadata, checksum, retention, and download path",
                    evidence={
                        "storage_backend": item.get("storage_backend"),
                        "has_object_key": bool(item.get("storage_object_key")),
                        "has_version_id": bool(item.get("storage_version_id")),
                        "has_etag": bool(item.get("storage_etag")),
                        "storage_size_bytes": item.get("storage_size_bytes"),
                        "has_checksum": bool(item.get("checksum")),
                        "retention_until": item.get("retention_until"),
                    },
                ),
                policy_check(
                    code="export.object_key.scope",
                    status="passed" if object_key.startswith(f"exports/{solution_id}/") and ".." not in object_key and "\\" not in object_key else "failed",
                    export_id=export_id,
                    message="export object key is scoped under the solution export prefix" if object_key.startswith(f"exports/{solution_id}/") and ".." not in object_key and "\\" not in object_key else "export object key must stay under the solution export prefix",
                    evidence={"storage_object_key": object_key, "expected_prefix": f"exports/{solution_id}/"},
                ),
                policy_check(
                    code="export.storage_key.not_object_key",
                    status="passed" if item.get("storage_key") != item.get("storage_object_key") else "failed",
                    export_id=export_id,
                    message="storage_key keeps backend-specific locator separate from object key" if item.get("storage_key") != item.get("storage_object_key") else "storage_key must not collapse to object_key",
                    evidence={"storage_key_equals_object_key": item.get("storage_key") == item.get("storage_object_key")},
                ),
                policy_check(
                    code="export.current_metadata.matches",
                    status="passed"
                    if item.get("storage_exists")
                    and item.get("storage_version_id") == item.get("current_storage_version_id")
                    and item.get("storage_etag") == item.get("current_storage_etag")
                    and item.get("storage_size_bytes") == item.get("current_storage_size_bytes")
                    else "failed",
                    export_id=export_id,
                    message="current object metadata matches persisted export metadata" if item.get("storage_exists") and item.get("storage_version_id") == item.get("current_storage_version_id") and item.get("storage_etag") == item.get("current_storage_etag") and item.get("storage_size_bytes") == item.get("current_storage_size_bytes") else "current object metadata must match persisted export metadata",
                    evidence={
                        "storage_exists": item.get("storage_exists"),
                        "storage_version_id": item.get("storage_version_id"),
                        "current_storage_version_id": item.get("current_storage_version_id"),
                        "storage_etag": item.get("storage_etag"),
                        "current_storage_etag": item.get("current_storage_etag"),
                    },
                ),
                policy_check(
                    code="export.recovery.metadata.matches",
                    status="passed"
                    if recovery.get("status") == "ok"
                    and recovery.get("expected_storage_version_id") == item.get("storage_version_id")
                    and recovery.get("actual_storage_version_id") == item.get("storage_version_id")
                    and recovery.get("expected_etag") == item.get("storage_etag")
                    and recovery.get("actual_etag") == item.get("storage_etag")
                    else "failed",
                    export_id=export_id,
                    message="recovery item confirms expected version and ETag" if recovery.get("status") == "ok" and recovery.get("expected_storage_version_id") == item.get("storage_version_id") and recovery.get("actual_storage_version_id") == item.get("storage_version_id") and recovery.get("expected_etag") == item.get("storage_etag") and recovery.get("actual_etag") == item.get("storage_etag") else "recovery item must confirm expected version and ETag",
                    evidence={
                        "recovery_status": recovery.get("status"),
                        "expected_storage_version_id": recovery.get("expected_storage_version_id"),
                        "actual_storage_version_id": recovery.get("actual_storage_version_id"),
                        "expected_etag": recovery.get("expected_etag"),
                        "actual_etag": recovery.get("actual_etag"),
                    },
                ),
            ]
        )
    return checks


def validate_export_version_policy(exports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    export_types = sorted({str(item.get("export_type")) for item in exports if item.get("export_type")})
    by_id = {str(item.get("id")): item for item in exports if item.get("id")}
    for export_type in export_types:
        typed_exports = [item for item in exports if item.get("export_type") == export_type]
        active = [item for item in typed_exports if item.get("lifecycle_status") == "active"]
        latest = max(typed_exports, key=lambda item: int(item.get("version") or 0), default={})
        superseded = [item for item in typed_exports if item.get("lifecycle_status") == "superseded"]
        checks.extend(
            [
                policy_check(
                    code="version_chain.single_active",
                    status="passed" if len(active) == 1 else "failed",
                    export_type=export_type,
                    message="exactly one active export exists for this type" if len(active) == 1 else "each export type must have exactly one active export",
                    evidence={"active_ids": [item.get("id") for item in active]},
                ),
                policy_check(
                    code="version_chain.latest_active",
                    status="passed" if latest and latest.get("lifecycle_status") == "active" else "failed",
                    export_type=export_type,
                    message="latest export version is active" if latest and latest.get("lifecycle_status") == "active" else "latest export version must be active",
                    evidence={"latest_id": latest.get("id"), "latest_version": latest.get("version"), "latest_status": latest.get("lifecycle_status")},
                ),
            ]
        )
        for item in superseded:
            successor = by_id.get(str(item.get("superseded_by_export_id")))
            checks.append(
                policy_check(
                    code="version_chain.superseded_link",
                    status="passed"
                    if successor
                    and successor.get("export_type") == export_type
                    and int(successor.get("version") or 0) > int(item.get("version") or 0)
                    else "failed",
                    export_id=str(item.get("id") or ""),
                    export_type=export_type,
                    message="superseded export links to a newer export of the same type" if successor and successor.get("export_type") == export_type and int(successor.get("version") or 0) > int(item.get("version") or 0) else "superseded export must link to a newer export of the same type",
                    evidence={
                        "version": item.get("version"),
                        "superseded_by_export_id": item.get("superseded_by_export_id"),
                        "successor_version": successor.get("version") if successor else None,
                    },
                )
            )
    return checks


def policy_check(
    *,
    code: str,
    status: str,
    message: str,
    export_id: str | None = None,
    export_type: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "export_id": export_id,
        "export_type": export_type,
        "status": status,
        "severity": "critical" if status == "failed" else "warning" if status == "warning" else "info",
        "message": message,
        "evidence": evidence or {},
    }


def check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def raises_value_error(func) -> bool:
    try:
        func()
    except ValueError:
        return True
    except Exception:
        return False
    return False


def path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") or []
    storage_contract = report.get("storage_contract") or {}
    policy_contract = report.get("policy_contract") or {}
    storage_contract_summary = storage_contract.get("summary") or {}
    policy_failed_count = int(policy_contract.get("failed_count") or 0)
    policy_warning_count = int(policy_contract.get("warning_count") or 0)
    storage_probe = storage_contract.get("probe") or {}
    return {
        "check_count": len(checks),
        "failed_count": policy_failed_count
        + sum(1 for item in checks if item.get("status") != "passed")
        + len(report.get("errors") or []),
        "export_count": (report.get("manifest") or {}).get("export_count", 0),
        "storage_contract_status": storage_contract.get("status"),
        "storage_contract_failed_count": storage_contract_summary.get("failed_count", 0),
        "policy_contract_status": policy_contract.get("status"),
        "policy_contract_failed_count": policy_failed_count,
        "policy_contract_warning_count": policy_warning_count,
        "storage_backend": storage_probe.get("backend"),
        "recovery_status": (report.get("recovery") or {}).get("status"),
        "tamper_status": (next((item for item in (report.get("tamper_recovery") or {}).get("items", []) if item.get("export_id") == "audit_pdf_v2"), {}) or {}).get("status"),
        "version_drift_status": (next((item for item in (report.get("version_drift_recovery") or {}).get("items", []) if item.get("export_id") == "audit_pdf_v2"), {}) or {}).get("status"),
    }


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit solution export storage metadata, recovery, tamper detection, and archive dry-run.")
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    parser.add_argument("--simulate-missing", action="store_true", help="Internal validation mode: remove one object before recovery.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_storage_export_audit_report(simulate_missing=args.simulate_missing)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"storage export audit report: {output_path}", flush=True)
    summary = report["summary"]
    print(
        "storage export audit "
        f"{report['status']} "
        f"exports={summary.get('export_count')} "
        f"failed={summary['failed_count']} "
        f"recovery={summary.get('recovery_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
