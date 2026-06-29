from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_go_live_readiness_report.py"
PRODUCTION_ENV_BLOCKER = "production env audit artifact must be passed, got skipped"
EXTERNAL_ACCEPTANCE_BLOCKER = "external acceptance audit artifact must be passed, got skipped"


def load_verify_go_live_readiness_report_module():
    spec = importlib.util.spec_from_file_location("verify_go_live_readiness_report", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verify_go_live_readiness_report_accepts_exact_allowed_blockers(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    write_json(report_path, readiness_report([PRODUCTION_ENV_BLOCKER, EXTERNAL_ACCEPTANCE_BLOCKER]))

    verification = module.verify_go_live_readiness_report(
        report_path,
        allowed_blockers=[PRODUCTION_ENV_BLOCKER, EXTERNAL_ACCEPTANCE_BLOCKER],
    )

    assert verification["status"] == "passed"
    assert verification["summary"]["unexpected_blocker_count"] == 0
    assert verification["summary"]["missing_allowed_blocker_count"] == 0


def test_verify_go_live_readiness_report_rejects_unexpected_blocker(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    write_json(report_path, readiness_report([PRODUCTION_ENV_BLOCKER, "database migration smoke failed"]))

    verification = module.verify_go_live_readiness_report(
        report_path,
        allowed_blockers=[PRODUCTION_ENV_BLOCKER, EXTERNAL_ACCEPTANCE_BLOCKER],
    )

    assert verification["status"] == "failed"
    assert verification["unexpected_blockers"] == ["database migration smoke failed"]
    assert EXTERNAL_ACCEPTANCE_BLOCKER in verification["missing_allowed_blockers"]


def test_verify_go_live_readiness_report_rejects_missing_allowed_blocker(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    write_json(report_path, readiness_report([PRODUCTION_ENV_BLOCKER]))

    verification = module.verify_go_live_readiness_report(
        report_path,
        allowed_blockers=[PRODUCTION_ENV_BLOCKER, EXTERNAL_ACCEPTANCE_BLOCKER],
    )

    assert verification["status"] == "failed"
    assert verification["missing_allowed_blockers"] == [EXTERNAL_ACCEPTANCE_BLOCKER]


def test_verify_go_live_readiness_report_accepts_full_pass_without_allowed_blockers(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    write_json(report_path, readiness_report([]))

    verification = module.verify_go_live_readiness_report(report_path)

    assert verification["status"] == "passed"
    assert verification["report_status"] == "passed"
    assert verification["summary"]["blocker_count"] == 0


def test_verify_go_live_readiness_report_rejects_summary_mismatch(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    report = readiness_report([PRODUCTION_ENV_BLOCKER])
    report["summary"]["blocker_count"] = 0
    write_json(report_path, report)

    verification = module.verify_go_live_readiness_report(report_path, allowed_blockers=[PRODUCTION_ENV_BLOCKER])

    assert verification["status"] == "failed"
    assert any("summary blocker_count mismatch" in error for error in verification["errors"])


def test_verify_go_live_readiness_report_cli_writes_report_and_returns_nonzero(tmp_path: Path) -> None:
    module = load_verify_go_live_readiness_report_module()
    report_path = tmp_path / "go-live-readiness.json"
    output_path = tmp_path / "go-live-readiness-verification.json"
    write_json(report_path, readiness_report([PRODUCTION_ENV_BLOCKER, "database migration smoke failed"]))

    exit_code = module.main(
        [
            "--report",
            str(report_path),
            "--output",
            str(output_path),
            "--allow-blocker",
            PRODUCTION_ENV_BLOCKER,
            "--allow-blocker",
            EXTERNAL_ACCEPTANCE_BLOCKER,
        ]
    )

    verification = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert verification["status"] == "failed"
    assert verification["unexpected_blockers"] == ["database migration smoke failed"]


def readiness_report(blockers: list[str]) -> dict:
    checks = [
        {
            "name": "handoff_manifest",
            "status": "passed",
            "summary": {},
            "errors": [],
        }
    ]
    if blockers:
        checks.append(
            {
                "name": "go_live_evidence",
                "status": "failed",
                "summary": {},
                "errors": blockers,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": "2026-06-29T00:00:00+00:00",
        "status": "failed" if blockers else "passed",
        "handoff_manifest": "/tmp/release-handoff-bundle.json",
        "handoff_verification": "/tmp/release-handoff-verification.json",
        "summary": {
            "check_count": len(checks),
            "passed_check_count": 1,
            "failed_check_count": 1 if blockers else 0,
            "blocker_count": len(blockers),
            "warning_count": 0,
        },
        "blockers": blockers,
        "warnings": [],
        "checks": checks,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
