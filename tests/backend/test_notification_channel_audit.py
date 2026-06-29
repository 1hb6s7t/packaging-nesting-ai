from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "notification_channel_audit.py"
SAMPLE_PACK_PATH = REPO_ROOT / "samples" / "notifications" / "webhook-channel-pack.json"


def load_notification_channel_audit_module():
    spec = importlib.util.spec_from_file_location("notification_channel_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sample_pack_audit_passes_and_covers_required_events() -> None:
    module = load_notification_channel_audit_module()

    report = module.build_notification_channel_audit_report(SAMPLE_PACK_PATH)

    assert report["status"] == "passed"
    assert report["summary"]["template_count"] == 6
    assert report["summary"]["webhook_template_count"] == 5
    assert report["summary"]["email_template_count"] == 1
    assert report["summary"]["channel_counts"] == {"email": 1, "webhook": 5}
    assert report["summary"]["failed_count"] == 0
    assert report["coverage"]["status"] == "passed"
    assert report["coverage"]["missing_events"] == []
    assert all(template["status"] == "passed" for template in report["templates"])

    failure_template = next(
        template for template in report["templates"] if template["event_type"] == "work_task.failure_high"
    )
    assert failure_template["signature"]["verified"] is True
    assert failure_template["attempt_count"] == 2

    schedule_template = next(
        template for template in report["templates"] if template["event_type"] == "production.schedule_blocked"
    )
    assert schedule_template["dedupe_status"] == "skipped"

    email_template = next(template for template in report["templates"] if template["channel"] == "email")
    assert email_template["status"] == "passed"
    assert email_template["email"]["verified"] is True
    assert email_template["email"]["to"] == "admin@example.com"
    assert email_template["email"]["subject"] == "[OPS] Email task failure threshold OPS-TASK-EMAIL"
    assert email_template["dedupe_status"] == "skipped"


def test_audit_fails_when_required_event_missing(tmp_path: Path) -> None:
    module = load_notification_channel_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    removed_event_type = "work_task.stale_running"
    broken_pack["templates"] = [
        item for item in broken_pack["templates"] if item["event_type"] != removed_event_type
    ]
    pack_path = tmp_path / "missing-event-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_notification_channel_audit_report(pack_path)

    assert report["status"] == "failed"
    assert report["coverage"]["status"] == "failed"
    assert removed_event_type in report["coverage"]["missing_events"]
    assert report["summary"]["missing_required_event_count"] == 1
    assert report["summary"]["failed_count"] >= 1


def test_audit_fails_when_email_recipient_expectation_is_wrong(tmp_path: Path) -> None:
    module = load_notification_channel_audit_module()
    pack = json.loads(SAMPLE_PACK_PATH.read_text(encoding="utf-8"))
    broken_pack = copy.deepcopy(pack)
    email_template = next(item for item in broken_pack["templates"] if item["channel"] == "email")
    email_template["expected_recipient_email"] = "missing-recipient@example.test"
    pack_path = tmp_path / "wrong-email-pack.json"
    pack_path.write_text(json.dumps(broken_pack), encoding="utf-8")

    report = module.build_notification_channel_audit_report(pack_path)

    assert report["status"] == "failed"
    failed_template = next(template for template in report["templates"] if template["channel"] == "email")
    assert failed_template["status"] == "failed"
    assert failed_template["email"]["verified"] is False
    assert any(check["name"] == "email expected recipient" for check in failed_template["checks"])


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_notification_channel_audit_module()
    pack_path = tmp_path / "empty-pack.json"
    output_path = tmp_path / "audit.json"
    pack_path.write_text(json.dumps({"templates": []}), encoding="utf-8")

    exit_code = module.main(["--pack", str(pack_path), "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert "pack does not contain any notification templates" in written["errors"]


def test_report_writer_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    module = load_notification_channel_audit_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report = {"schema_version": 1, "status": "passed"}

    output_path = module.write_report(Path("reports/notification-channel-audit.json"), report)

    assert output_path == tmp_path / "reports" / "notification-channel-audit.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
