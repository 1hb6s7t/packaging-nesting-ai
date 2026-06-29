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
            finally:
                engine.dispose()
    except Exception as exc:
        report["errors"].append(str(exc))
        report["summary"] = build_summary(report)
        return report

    report["storage_contract"] = storage_contract
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


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") or []
    storage_contract = report.get("storage_contract") or {}
    storage_contract_summary = storage_contract.get("summary") or {}
    storage_probe = storage_contract.get("probe") or {}
    return {
        "check_count": len(checks),
        "failed_count": sum(1 for item in checks if item.get("status") != "passed") + len(report.get("errors") or []),
        "export_count": (report.get("manifest") or {}).get("export_count", 0),
        "storage_contract_status": storage_contract.get("status"),
        "storage_contract_failed_count": storage_contract_summary.get("failed_count", 0),
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
