from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import tempfile
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
DEFAULT_PACK_PATH = REPO_ROOT / "samples" / "notifications" / "webhook-channel-pack.json"

sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings
from app.db.base import Base
from app.db import models as dbm  # noqa: F401
from app.domain import schemas
from app.services import repository
from app.services.adapters import REQUIRED_INTEGRATION_NOTIFICATION_EVENTS
from app.services.messaging import dispatch_message_event
from app.services.security import hash_password


def load_pack(pack_path: Path) -> dict[str, Any]:
    return json.loads(pack_path.read_text(encoding="utf-8-sig"))


def build_notification_channel_audit_report(pack_path: Path = DEFAULT_PACK_PATH) -> dict[str, Any]:
    resolved_pack_path = pack_path if pack_path.is_absolute() else REPO_ROOT / pack_path
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "pack_path": str(resolved_pack_path),
        "required_events": list(REQUIRED_INTEGRATION_NOTIFICATION_EVENTS),
        "status": "failed",
        "summary": {},
        "templates": [],
        "coverage": {},
        "errors": [],
    }
    if not resolved_pack_path.exists():
        report["errors"].append(f"pack file not found: {resolved_pack_path}")
        report["summary"] = build_summary(report)
        return report
    try:
        pack = load_pack(resolved_pack_path)
    except Exception as exc:
        report["errors"].append(f"pack file is not valid JSON: {exc}")
        report["summary"] = build_summary(report)
        return report
    template_specs = pack.get("templates")
    if not isinstance(template_specs, list) or not template_specs:
        report["errors"].append("pack does not contain any notification templates")
        report["summary"] = build_summary(report)
        return report

    with tempfile.TemporaryDirectory(prefix="notification-channel-audit-") as temp_dir:
        db_path = Path(temp_dir) / "audit.sqlite"
        engine = create_engine(f"sqlite:///{db_path.as_posix()}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        try:
            with SessionLocal() as db:
                repository.seed_rbac(db, hash_password)
                report["templates"] = [audit_template(db, spec) for spec in template_specs]
        finally:
            engine.dispose()

    report["coverage"] = build_required_event_coverage(report["templates"])
    report["summary"] = build_summary(report)
    report["status"] = "passed" if report["summary"]["failed_count"] == 0 else "failed"
    return report


def audit_template(db, spec: dict[str, Any]) -> dict[str, Any]:
    template_report: dict[str, Any] = {
        "name": spec.get("name"),
        "event_type": spec.get("event_type"),
        "channel": spec.get("channel"),
        "provider": provider_from_spec(spec),
        "status": "failed",
        "template_id": None,
        "dispatch_status": None,
        "request_count": 0,
        "email_count": 0,
        "attempt_count": 0,
        "dedupe_status": None,
        "signature": {"expected": bool(spec.get("expect_signature")), "verified": False},
        "email": {"expected": spec.get("expected_recipient_email"), "verified": False},
        "checks": [],
        "errors": [],
    }
    requests: list[dict[str, Any]] = []
    emails: list[EmailMessage] = []
    try:
        template = repository.create_message_template(
            db,
            payload=schemas.MessageTemplateCreate(
                name=str(spec["name"]),
                event_type=str(spec["event_type"]),
                channel=spec.get("channel", "webhook"),
                title_template=str(spec["title_template"]),
                message_template=str(spec["message_template"]),
                recipient_permission_code=spec.get("recipient_permission_code"),
                recipient_group_id=spec.get("recipient_group_id"),
                escalation_permission_code=spec.get("escalation_permission_code"),
                escalation_group_id=spec.get("escalation_group_id"),
                escalation_after_minutes=spec.get("escalation_after_minutes"),
                metadata=dict(spec.get("metadata") or {}),
                is_active=bool(spec.get("is_active", True)),
            ),
        )
        template_report["template_id"] = template.id
        if template.channel == "email":
            result = dispatch_message_event(
                db,
                event_type=template.event_type,
                context=dict(spec.get("context") or {}),
                default_title="notification channel audit",
                default_message="notification channel audit",
                target_type=spec.get("target_type"),
                target_id=spec.get("target_id") or template.event_type,
                payload=dict(spec.get("payload") or {}),
                channel_filter={"email"},
                settings=Settings(
                    SMTP_HOST="smtp.audit.example.test",
                    SMTP_FROM_EMAIL=str(spec.get("smtp_from_email") or "alerts@example.test"),
                ),
                email_sender=lambda message, settings: emails.append(message),
            )
            dispatch = result.dispatches[0] if result.dispatches else None
            template_report["dispatch_status"] = result.status
            template_report["email_count"] = len(emails)
            template_report["email"] = email_summary(spec, emails, dispatch)
            template_report["checks"] = validate_email_dispatch(spec, result, emails)
            if spec.get("expect_dedupe"):
                dedupe = dispatch_message_event(
                    db,
                    event_type=template.event_type,
                    context=dict(spec.get("context") or {}),
                    default_title="notification channel audit",
                    default_message="notification channel audit",
                    target_type=spec.get("target_type"),
                    target_id=spec.get("target_id") or template.event_type,
                    payload=dict(spec.get("payload") or {}),
                    channel_filter={"email"},
                    settings=Settings(SMTP_HOST="smtp.audit.example.test", SMTP_FROM_EMAIL="alerts@example.test"),
                    email_sender=lambda message, settings: emails.append(message),
                )
                template_report["dedupe_status"] = dedupe.status
                template_report["checks"].extend(validate_dedupe(dedupe))
            template_report["status"] = "passed" if template_passed(template_report) else "failed"
            return template_report

        response_sequence = normalize_response_sequence(spec.get("audit_response_sequence"))
        transport = httpx.MockTransport(make_audit_transport(response_sequence=response_sequence, requests=requests))
        result = dispatch_message_event(
            db,
            event_type=template.event_type,
            context=dict(spec.get("context") or {}),
            default_title="notification channel audit",
            default_message="notification channel audit",
            target_type=spec.get("target_type"),
            target_id=spec.get("target_id") or template.event_type,
            payload=dict(spec.get("payload") or {}),
            channel_filter={"webhook"},
            settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
            http_transport=transport,
        )
        dispatch = result.dispatches[0] if result.dispatches else None
        template_report["dispatch_status"] = result.status
        template_report["request_count"] = len(requests)
        template_report["attempt_count"] = (dispatch.payload or {}).get("attempt_count") if dispatch else 0
        template_report["signature"] = {
            "expected": bool(spec.get("expect_signature")),
            "verified": signatures_verified(spec, requests),
            "header": (spec.get("metadata") or {}).get("signature_header") or "X-Packaging-Signature",
        }
        template_report["checks"] = validate_dispatch(spec, result, requests)
        if spec.get("expect_dedupe"):
            dedupe = dispatch_message_event(
                db,
                event_type=template.event_type,
                context=dict(spec.get("context") or {}),
                default_title="notification channel audit",
                default_message="notification channel audit",
                target_type=spec.get("target_type"),
                target_id=spec.get("target_id") or template.event_type,
                payload=dict(spec.get("payload") or {}),
                channel_filter={"webhook"},
                settings=Settings(EXTERNAL_ALERT_WEBHOOK_URL=None),
                http_transport=httpx.MockTransport(make_audit_transport(response_sequence=[204], requests=requests)),
            )
            template_report["dedupe_status"] = dedupe.status
            template_report["checks"].extend(validate_dedupe(dedupe))
    except Exception as exc:
        template_report["errors"].append(str(exc))
    template_report["status"] = "passed" if template_passed(template_report) else "failed"
    return template_report


def make_audit_transport(*, response_sequence: list[int], requests: list[dict[str, Any]]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        status_code = response_sequence[min(len(requests), len(response_sequence) - 1)]
        requests.append(
            {
                "url": str(request.url),
                "headers": {key: value for key, value in request.headers.items()},
                "body": body.decode("utf-8"),
                "json": parse_json_body(body),
                "response_status_code": status_code,
            }
        )
        return httpx.Response(status_code, json={"audit": "ok"} if status_code < 400 else {"audit": "failed"})

    return handler


def normalize_response_sequence(value: Any) -> list[int]:
    if isinstance(value, list) and value:
        return [int(item) for item in value]
    return [202]


def parse_json_body(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def validate_dispatch(
    spec: dict[str, Any],
    result: schemas.MessageDispatchResult,
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    dispatch = result.dispatches[0] if result.dispatches else None
    expected_provider = spec.get("expected_provider") or provider_from_spec(spec)
    expected_attempt_count = int(spec.get("expected_attempt_count") or len(normalize_response_sequence(spec.get("audit_response_sequence"))))
    checks.append(check_result("dispatch sent", result.status == "sent", f"status={result.status}"))
    checks.append(check_result("http request count", len(requests) == expected_attempt_count, f"requests={len(requests)} expected={expected_attempt_count}"))
    if dispatch is not None:
        provider = (dispatch.payload or {}).get("webhook_provider")
        attempt_count = (dispatch.payload or {}).get("attempt_count")
        checks.append(check_result("provider matched", provider == expected_provider, f"provider={provider} expected={expected_provider}"))
        checks.append(
            check_result(
                "attempt count matched",
                int(attempt_count or 0) == expected_attempt_count,
                f"attempt_count={attempt_count} expected={expected_attempt_count}",
            )
        )
    if requests:
        checks.append(check_result("payload shape valid", payload_shape_valid(expected_provider, requests[-1]["json"]), f"provider={expected_provider}"))
    if spec.get("expect_signature"):
        checks.append(check_result("signature verified", signatures_verified(spec, requests), "signature header and digest checked"))
    return checks


def validate_dedupe(result: schemas.MessageDispatchResult) -> list[dict[str, Any]]:
    dispatch = result.dispatches[0] if result.dispatches else None
    reason = (dispatch.payload or {}).get("reason") if dispatch else None
    return [
        check_result("dedupe skipped second dispatch", result.status == "skipped", f"status={result.status}"),
        check_result("dedupe reason recorded", reason == "dedupe_window", f"reason={reason}"),
    ]


def validate_email_dispatch(
    spec: dict[str, Any],
    result: schemas.MessageDispatchResult,
    emails: list[EmailMessage],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    dispatch = result.dispatches[0] if result.dispatches else None
    expected_recipient = str(spec.get("expected_recipient_email") or "").strip()
    expected_subject = str(spec.get("expected_subject") or "").strip()
    checks.append(check_result("email dispatch sent", result.status == "sent", f"status={result.status}"))
    checks.append(check_result("email message captured", len(emails) == 1, f"emails={len(emails)} expected=1"))
    if dispatch is not None:
        checks.append(
            check_result(
                "email recipient count positive",
                int(dispatch.recipient_count or 0) > 0,
                f"recipient_count={dispatch.recipient_count}",
            )
        )
        external_status = (dispatch.external_push or {}).get("status")
        checks.append(check_result("email external push sent", external_status == "sent", f"external_status={external_status}"))
    if emails:
        message = emails[-1]
        to_header = str(message.get("To") or "")
        subject = str(message.get("Subject") or "")
        event_header = str(message.get("X-Packaging-Event") or "")
        body = message.get_content()
        checks.extend(
            [
                check_result(
                    "email expected recipient",
                    bool(expected_recipient) and expected_recipient in to_header,
                    f"to={to_header} expected={expected_recipient}",
                ),
                check_result(
                    "email subject matched",
                    bool(expected_subject) and subject == expected_subject,
                    f"subject={subject} expected={expected_subject}",
                ),
                check_result(
                    "email event header matched",
                    event_header == str(spec.get("event_type") or ""),
                    f"header={event_header}",
                ),
                check_result(
                    "email body includes event",
                    str(spec.get("event_type") or "") in body,
                    "body includes X-Packaging event text",
                ),
            ]
        )
    return checks


def email_summary(
    spec: dict[str, Any],
    emails: list[EmailMessage],
    dispatch: schemas.MessageDispatchRecord | None,
) -> dict[str, Any]:
    if not emails:
        return {
            "expected": spec.get("expected_recipient_email"),
            "verified": False,
            "recipient_count": dispatch.recipient_count if dispatch else 0,
        }
    message = emails[-1]
    expected_recipient = str(spec.get("expected_recipient_email") or "")
    expected_subject = str(spec.get("expected_subject") or "")
    return {
        "expected": expected_recipient,
        "verified": expected_recipient in str(message.get("To") or "")
        and str(message.get("Subject") or "") == expected_subject
        and str(message.get("X-Packaging-Event") or "") == str(spec.get("event_type") or ""),
        "recipient_count": dispatch.recipient_count if dispatch else 0,
        "to": str(message.get("To") or ""),
        "from": str(message.get("From") or ""),
        "subject": str(message.get("Subject") or ""),
        "event_header": str(message.get("X-Packaging-Event") or ""),
    }


def check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "passed" if passed else "failed", "detail": detail}


def provider_from_spec(spec: dict[str, Any]) -> str:
    metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
    return str(metadata.get("webhook_provider") or metadata.get("provider") or "generic").strip().lower()


def payload_shape_valid(provider: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if provider in {"feishu", "lark"}:
        return payload.get("msg_type") == "text" and isinstance((payload.get("content") or {}).get("text"), str)
    if provider in {"wecom", "wechat_work", "enterprise_wechat"}:
        if payload.get("msgtype") == "markdown":
            return isinstance((payload.get("markdown") or {}).get("content"), str)
        if payload.get("msgtype") == "text":
            return isinstance((payload.get("text") or {}).get("content"), str)
        return False
    return payload.get("source") == "packaging_nesting" and payload.get("event_type") and payload.get("title")


def signatures_verified(spec: dict[str, Any], requests: list[dict[str, Any]]) -> bool:
    metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
    secret = str(metadata.get("signature_secret") or metadata.get("webhook_secret") or "")
    if not secret:
        return not bool(spec.get("expect_signature"))
    signature_header = str(metadata.get("signature_header") or "X-Packaging-Signature").lower()
    timestamp_header = str(metadata.get("signature_timestamp_header") or "X-Packaging-Timestamp").lower()
    for request in requests:
        headers = {str(key).lower(): str(value) for key, value in (request.get("headers") or {}).items()}
        signature = headers.get(signature_header)
        timestamp = headers.get(timestamp_header)
        body = str(request.get("body") or "").encode("utf-8")
        if not signature or not timestamp:
            return False
        expected = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256).hexdigest()
        if signature != f"sha256={expected}":
            return False
    return bool(requests)


def build_required_event_coverage(template_reports: list[dict[str, Any]]) -> dict[str, Any]:
    passed_events = {str(item.get("event_type")) for item in template_reports if item.get("status") == "passed"}
    required_events = set(REQUIRED_INTEGRATION_NOTIFICATION_EVENTS)
    missing = sorted(required_events - passed_events)
    return {
        "status": "passed" if not missing else "failed",
        "required_count": len(required_events),
        "covered_count": len(required_events) - len(missing),
        "missing_events": missing,
    }


def template_passed(template_report: dict[str, Any]) -> bool:
    if template_report.get("errors"):
        return False
    return all(check.get("status") == "passed" for check in template_report.get("checks") or [])


def build_summary(report: dict[str, Any]) -> dict[str, Any]:
    templates = report.get("templates") or []
    coverage = report.get("coverage") or {}
    template_failed_count = sum(1 for item in templates if item.get("status") != "passed")
    error_count = len(report.get("errors") or [])
    coverage_failed = 1 if coverage.get("status") == "failed" else 0
    channel_counts: dict[str, int] = {}
    for item in templates:
        channel = str(item.get("channel") or "unknown")
        channel_counts[channel] = channel_counts.get(channel, 0) + 1
    return {
        "template_count": len(templates),
        "template_passed_count": sum(1 for item in templates if item.get("status") == "passed"),
        "template_failed_count": template_failed_count,
        "channel_counts": dict(sorted(channel_counts.items())),
        "email_template_count": channel_counts.get("email", 0),
        "webhook_template_count": channel_counts.get("webhook", 0),
        "coverage_status": coverage.get("status"),
        "missing_required_event_count": len(coverage.get("missing_events") or []),
        "error_count": error_count,
        "failed_count": template_failed_count + error_count + coverage_failed,
    }


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit webhook and email notification templates against mocked channel endpoints.")
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK_PATH, help="Notification channel audit JSON pack.")
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_notification_channel_audit_report(args.pack)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"notification channel audit report: {output_path}", flush=True)
    summary = report["summary"]
    print(
        "notification channel audit "
        f"{report['status']} "
        f"templates={summary['template_count']} "
        f"failed={summary['failed_count']} "
        f"coverage={summary.get('coverage_status')}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
