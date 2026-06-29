from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "solver_governance_audit.py"


def load_solver_governance_audit_module():
    spec = importlib.util.spec_from_file_location("solver_governance_audit", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_solver_governance_audit_passes_registry_guards_rectpack_and_benchmark() -> None:
    module = load_solver_governance_audit_module()

    report = module.build_solver_governance_audit_report()

    assert report["status"] == "passed"
    assert report["summary"]["failed_count"] == 0
    assert report["registry"]["solver_count"] == 5
    assert report["registry"]["enabled_unconfigured_stubs"] == []
    assert report["registry"]["rectpack"]["enabled"] is True
    assert report["registry"]["rectpack"]["license_policy"] == "open_source"
    assert report["registry"]["external_disabled_count"] == 4
    assert report["guards"]["stub_enable_rejected"] is True
    assert report["guards"]["disabled_license_rejected"] is True
    assert report["guards"]["runtime_enabled_stub_rejected"] is True
    assert report["rectpack"]["valid"] is True
    assert report["rectpack"]["placed_count"] == report["rectpack"]["candidate_count"]
    assert report["rectpack"]["unplaced_count"] == 0
    assert report["rectpack"]["deterministic_placement"] is True
    assert report["rectpack"]["utilization_rate"] > 0
    assert report["benchmark"]["solver_name"] == "RectpackSolver"
    assert report["benchmark"]["valid"] is True
    assert report["benchmark"]["persisted_run_count"] >= 1
    assert set(report["adapters"]["external_placeholder_names"]) == {
        "MockPhoenixSolver",
        "OrToolsSolver",
        "PackingSolver",
        "SparrowSolver",
    }
    assert all(check["status"] == "passed" for check in report["checks"])


def test_solver_governance_audit_fails_when_enabled_external_stub_exists() -> None:
    module = load_solver_governance_audit_module()

    report = module.build_solver_governance_audit_report(simulate_enabled_stub=True)

    assert report["status"] == "failed"
    assert report["summary"]["failed_count"] >= 1
    assert report["summary"]["enabled_unconfigured_stub_count"] == 1
    assert report["registry"]["enabled_unconfigured_stubs"] == ["OrToolsSolver"]
    assert any(
        check["name"] == "no enabled unconfigured solver stubs" and check["status"] == "failed"
        for check in report["checks"]
    )


def test_cli_writes_report_and_returns_nonzero_on_failure(tmp_path: Path) -> None:
    module = load_solver_governance_audit_module()
    output_path = tmp_path / "audit.json"

    exit_code = module.main(["--simulate-enabled-stub", "--output", str(output_path)])

    assert exit_code == 1
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["status"] == "failed"
    assert written["registry"]["enabled_unconfigured_stubs"] == ["OrToolsSolver"]


def test_report_writer_resolves_relative_paths(tmp_path: Path, monkeypatch) -> None:
    module = load_solver_governance_audit_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report = {"schema_version": 1, "status": "passed"}

    output_path = module.write_report(Path("reports/solver-governance-audit.json"), report)

    assert output_path == tmp_path / "reports" / "solver-governance-audit.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report
