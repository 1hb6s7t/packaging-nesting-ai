from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.db.base import Base
from app.db import models as dbm  # noqa: F401
from app.domain import schemas
from app.services import repository, storage
from app.services.artworks import checksum_bytes, preflight_artwork
from app.services.file_conversion import (
    apply_authenticated_conversion_callback,
    apply_conversion_result,
    check_file_conversion_sla,
    submit_external_conversion_job,
)


def build_conversion_supplier_audit_report(*, simulate_submit_failure: bool = False) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "failed",
        "summary": {},
        "checks": [],
        "submit": {},
        "token_rotation": {},
        "callback": {},
        "vendor_error": {},
        "sla": {},
        "errors": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="conversion-supplier-audit-") as temp_dir:
            temp_path = Path(temp_dir)
            engine = create_engine(f"sqlite:///{(temp_path / 'audit.sqlite').as_posix()}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            audit_settings = Settings(
                STORAGE_BACKEND="local",
                STORAGE_ROOT=temp_path / "objects",
                EXTERNAL_CONVERSION_SERVICE_URL="https://converter.example.test/api",
                EXTERNAL_CONVERSION_SERVICE_API_KEY="audit-converter-secret",
                EXTERNAL_CONVERSION_SLA_MINUTES=30,
            )
            original_get_settings = storage.get_settings
            storage.get_settings = lambda: audit_settings
            try:
                with SessionLocal() as db:
                    workflow = run_supplier_workflow(
                        db,
                        settings=audit_settings,
                        simulate_submit_failure=simulate_submit_failure,
                    )
            finally:
                storage.get_settings = original_get_settings
                engine.dispose()
    except Exception as exc:
        report["errors"].append(str(exc))
        report["summary"] = build_summary(report)
        return report

    report.update(workflow)
    report["checks"] = validate_workflow(report)
    report["summary"] = build_summary(report)
    report["status"] = "passed" if report["summary"]["failed_count"] == 0 else "failed"
    return report


def run_supplier_workflow(
    db: Session,
    *,
    settings: Settings,
    simulate_submit_failure: bool,
) -> dict[str, Any]:
    artwork_id = "conversion_supplier_audit_artwork"
    source_bytes = b"%PDF-1.7\nconversion supplier audit source\n"
    source = storage.write_bytes(f"artworks/{artwork_id}/source.pdf", source_bytes, content_type="application/pdf")
    repository.create_artwork(
        db,
        artwork_id=artwork_id,
        filename="source.pdf",
        content_type="application/pdf",
        checksum=checksum_bytes(source_bytes),
        source_format="pdf",
        storage_key=source.storage_key,
        preflight_report=preflight_artwork("source.pdf", source_bytes.decode("utf-8", errors="ignore"), "application/pdf"),
    )
    success_job = repository.create_file_conversion_job(
        db,
        artwork_id=artwork_id,
        source_format="pdf",
        target_format="svg",
        status="manual_required",
        log="created for supplier audit",
    )
    vendor_job = repository.create_file_conversion_job(
        db,
        artwork_id=artwork_id,
        source_format="ai",
        target_format="svg",
        status="manual_required",
        log="created for vendor error audit",
    )
    sla_job = repository.create_file_conversion_job(
        db,
        artwork_id=artwork_id,
        source_format="pdf",
        target_format="svg",
        status="queued",
        log="created for SLA audit",
        metadata={
            "sla_due_at": "2000-01-01T00:00:00",
            "callback_token_hash": hashlib.sha256(b"sla-audit-token").hexdigest(),
            "callback_token_tail": "token",
        },
    )

    requests: list[dict[str, Any]] = []
    transport = httpx.MockTransport(make_supplier_transport(requests, simulate_submit_failure=simulate_submit_failure))
    first = submit_external_conversion_job(
        db,
        success_job.id,
        settings=settings,
        request=schemas.FileConversionSubmitRequest(
            callback_token="audit-old-token-123456",
            callback_url="https://api.example.test/api/artworks/conversion-jobs/callback",
            sla_minutes=15,
            metadata={"tenant": "audit"},
        ),
        http_transport=transport,
    )
    if simulate_submit_failure:
        return {
            "submit": {
                "status": first.status,
                "remote_status_code": first.remote_status_code,
                "remote_response": first.remote_response,
                "message": first.message,
                "request_count": len(requests),
                "authorization_header_present": all(bool(item.get("authorization")) for item in requests),
                "multipart_contains_job_id": all(item.get("contains_job_id") for item in requests),
                "multipart_contains_source_bytes": all(item.get("contains_source_bytes") for item in requests),
            },
            "token_rotation": {},
            "callback": {},
            "vendor_error": {},
            "sla": {},
        }

    second = submit_external_conversion_job(
        db,
        success_job.id,
        settings=settings,
        request=schemas.FileConversionSubmitRequest(rotate_callback_token=True, sla_minutes=15, metadata={"tenant": "audit"}),
        http_transport=transport,
    )
    old_token = "audit-old-token-123456"
    new_token = str(requests[-1].get("callback_token") or "")
    old_token_rejected = False
    try:
        apply_authenticated_conversion_callback(
            db,
            success_job.id,
            schemas.FileConversionResultRequest(status="failed", log="old token should fail"),
            callback_token=old_token,
        )
    except PermissionError:
        old_token_rejected = True

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><rect id="cut" width="20" height="10"/></svg>'
    callback_result = apply_authenticated_conversion_callback(
        db,
        success_job.id,
        schemas.FileConversionResultRequest(
            status="completed",
            target_format="svg",
            content=svg,
            log="supplier callback ok",
            metadata={"remote_job_id": second.remote_response.get("remote_job_id")},
        ),
        callback_token=new_token,
    )
    vendor_result = apply_conversion_result(
        db,
        vendor_job.id,
        schemas.FileConversionResultRequest(
            status="failed",
            log="supplier cannot open AI file",
            metadata={"vendor_error_code": "UNSUPPORTED-FORMAT", "vendor_error_message": "AI plug-in missing"},
        ),
    )
    sla_result = check_file_conversion_sla(db, request=schemas.FileConversionSlaCheckRequest(notify=False))

    return {
        "submit": {
            "status": second.status,
            "remote_status_code": second.remote_status_code,
            "remote_response": second.remote_response,
            "request_count": len(requests),
            "authorization_header_present": all(bool(item.get("authorization")) for item in requests),
            "multipart_contains_job_id": all(item.get("contains_job_id") for item in requests),
            "multipart_contains_source_bytes": all(item.get("contains_source_bytes") for item in requests),
        },
        "token_rotation": {
            "rotated": new_token != old_token,
            "old_token_tail": first.job.metadata.get("callback_token_tail"),
            "new_token_tail": second.job.metadata.get("callback_token_tail"),
            "history_tail": (second.job.metadata.get("callback_token_history") or [{}])[-1].get("token_tail"),
            "submit_attempt": second.job.metadata.get("submit_attempt"),
            "hash_stored": bool(second.job.metadata.get("callback_token_hash")),
            "plaintext_stored": "callback_token" in second.job.metadata,
        },
        "callback": {
            "old_token_rejected": old_token_rejected,
            "job_status": callback_result.job.status,
            "polygon_count": callback_result.polygon_count,
            "has_artwork_version": callback_result.artwork_version is not None,
            "polygon_storage_key": callback_result.polygon_storage_key,
            "last_callback_status": callback_result.job.metadata.get("last_callback_status"),
        },
        "vendor_error": {
            "job_status": vendor_result.job.status,
            "code": (vendor_result.job.metadata.get("vendor_error") or {}).get("code"),
            "mapped_status": (vendor_result.job.metadata.get("vendor_error") or {}).get("mapped_status"),
            "message": (vendor_result.job.metadata.get("vendor_error") or {}).get("message"),
        },
        "sla": {
            "status": sla_result.status,
            "overdue_count": sla_result.overdue_count,
            "notification_count": sla_result.notification_count,
            "contains_sla_job": any(item.id == sla_job.id for item in sla_result.overdue_jobs),
        },
    }


def make_supplier_transport(requests: list[dict[str, Any]], *, simulate_submit_failure: bool):
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "contains_job_id": b'name="job_id"' in body,
                "contains_source_bytes": b"conversion supplier audit source" in body,
                "callback_token": multipart_field(body, "callback_token"),
            }
        )
        if simulate_submit_failure:
            return httpx.Response(503, text="supplier unavailable")
        return httpx.Response(202, json={"remote_job_id": f"AUDIT-REMOTE-{len(requests)}"})

    return handler


def validate_workflow(report: dict[str, Any]) -> list[dict[str, Any]]:
    submit = report.get("submit") or {}
    token = report.get("token_rotation") or {}
    callback = report.get("callback") or {}
    vendor_error = report.get("vendor_error") or {}
    sla = report.get("sla") or {}
    return [
        check_result("supplier submit accepted", submit.get("status") == "submitted", f"status={submit.get('status')}"),
        check_result("supplier returned 202", submit.get("remote_status_code") == 202, f"remote_status={submit.get('remote_status_code')}"),
        check_result("two submit attempts recorded", submit.get("request_count") == 2, f"requests={submit.get('request_count')}"),
        check_result("submit authorization header present", bool(submit.get("authorization_header_present")), "authorization checked"),
        check_result("submit multipart has job id", bool(submit.get("multipart_contains_job_id")), "job_id field checked"),
        check_result("submit multipart has source file bytes", bool(submit.get("multipart_contains_source_bytes")), "source file checked"),
        check_result("callback token rotated", bool(token.get("rotated")), f"old_tail={token.get('old_token_tail')} new_tail={token.get('new_token_tail')}"),
        check_result("token history stores old tail", token.get("history_tail") == token.get("old_token_tail"), f"history_tail={token.get('history_tail')}"),
        check_result("callback token hash stored", bool(token.get("hash_stored")), "callback token hash checked"),
        check_result("callback token plaintext not stored", not bool(token.get("plaintext_stored")), "metadata omits callback_token"),
        check_result("submit attempt incremented", token.get("submit_attempt") == 2, f"attempt={token.get('submit_attempt')}"),
        check_result("old callback token rejected", bool(callback.get("old_token_rejected")), "old token rejected"),
        check_result("new callback completed job", callback.get("job_status") == "completed", f"status={callback.get('job_status')}"),
        check_result("callback parsed polygon", callback.get("polygon_count") == 1, f"polygons={callback.get('polygon_count')}"),
        check_result("callback created artwork version", bool(callback.get("has_artwork_version")), "artwork version checked"),
        check_result("vendor error requires manual handling", vendor_error.get("job_status") == "manual_required", f"status={vendor_error.get('job_status')}"),
        check_result("vendor error code normalized", vendor_error.get("code") == "unsupported_format", f"code={vendor_error.get('code')}"),
        check_result("vendor error mapped status recorded", vendor_error.get("mapped_status") == "manual_required", f"mapped={vendor_error.get('mapped_status')}"),
        check_result("sla check marks overdue", sla.get("status") == "overdue" and sla.get("overdue_count", 0) >= 1, f"status={sla.get('status')} count={sla.get('overdue_count')}"),
        check_result("sla check keeps notify disabled", sla.get("notification_count") == 0, f"notifications={sla.get('notification_count')}"),
        check_result("sla report contains audit job", bool(sla.get("contains_sla_job")), "sla job checked"),
    ]


def check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def multipart_field(body: bytes, name: str) -> str:
    marker = f'name="{name}"'.encode("utf-8")
    try:
        marker_index = body.index(marker)
        value_start = body.index(b"\r\n\r\n", marker_index) + 4
        value_end = body.index(b"\r\n--", value_start)
    except ValueError:
        return ""
    return body[value_start:value_end].decode("utf-8", errors="ignore")


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks") or []
    return {
        "check_count": len(checks),
        "failed_count": sum(1 for item in checks if item.get("status") != "passed") + len(report.get("errors") or []),
        "submit_status": (report.get("submit") or {}).get("status"),
        "callback_status": (report.get("callback") or {}).get("job_status"),
        "vendor_error_status": (report.get("vendor_error") or {}).get("job_status"),
        "sla_status": (report.get("sla") or {}).get("status"),
    }


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit external conversion supplier submit, callback, error mapping, and SLA behavior.")
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    parser.add_argument("--simulate-submit-failure", action="store_true", help="Internal validation mode: make mocked supplier submit fail.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_conversion_supplier_audit_report(simulate_submit_failure=args.simulate_submit_failure)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"conversion supplier audit report: {output_path}", flush=True)
    summary = report["summary"]
    print(
        "conversion supplier audit "
        f"{report['status']} "
        f"failed={summary['failed_count']} "
        f"submit={summary.get('submit_status')} "
        f"callback={summary.get('callback_status')} "
        f"sla={summary.get('sla_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
