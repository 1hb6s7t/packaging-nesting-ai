from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.domain.schemas import NestingItem, NestingJob, PolygonAsset, SolverConfig, SolverName
from app.services.batch_planning import plan_batch
from app.services.solvers import SolverOrchestrator


def _job(solver_name: SolverName, command: list[str] | None = None) -> NestingJob:
    options = {"packing_solver_command": command, "sparrow_command": command} if command else {}
    return NestingJob(
        job_id=f"external_{solver_name.value}",
        sheet={
            "sheet_id": "external_sheet",
            "width": 200,
            "height": 200,
            "material": "white_card",
            "thickness": "350gsm",
        },
        candidate_items=[
            NestingItem(
                item_id="box_a",
                order_id="order_a",
                polygon=PolygonAsset(shape_id="shape_box_a", outer=[(0, 0), (10, 0), (10, 5), (0, 5)]),
                quantity=1,
                min_gap_mm=0,
                bleed_mm=0,
            )
        ],
        solver_config=SolverConfig(solver_name=solver_name, options=options),
    )


@pytest.mark.parametrize("solver_name", [SolverName.packing_solver, SolverName.sparrow])
def test_external_cli_adapter_accepts_valid_json_certificate(tmp_path: Path, solver_name: SolverName) -> None:
    cli_script = tmp_path / "fake_solver.py"
    cli_script.write_text(
        """
import json
import sys

payload = json.loads(sys.stdin.read())
item = payload["items"][0]
print("fake solver stderr evidence", file=sys.stderr)
print(json.dumps({
    "solution_id": "external_sol",
    "status": "candidate",
    "utilization_rate": 0.25,
    "waste_rate": 0.75,
    "certificate": {
        "engine": "fake-external-cli",
        "solver": payload["solver"],
        "item_count": len(payload["items"])
    },
    "placed_items": [{
        "item_id": item["item_id"],
        "order_id": item["order_id"],
        "x": 0,
        "y": 0,
        "rotation": 0,
        "width": item["width"],
        "height": item["height"]
    }],
    "unplaced_items": []
}))
""",
        encoding="utf-8",
    )
    job = _job(solver_name, [sys.executable, str(cli_script)])

    solution = SolverOrchestrator().solve(job)[0]

    assert solution.status == "valid"
    assert solution.solver == solver_name
    assert solution.validation_report is not None
    assert solution.validation_report.is_valid is True
    assert len(solution.placed_items) == 1
    assert solution.placed_items[0].item_id == "box_a"
    assert "external_sol" in solution.exports["stdout"]
    assert "fake solver stderr evidence" in solution.exports["stderr"]
    assert solution.exports["exit_code"] == "0"
    assert len(solution.exports["input_payload_sha256"]) == 64
    cli_result = json.loads(solution.exports["cli_result_json"])
    assert cli_result["status"] == "passed"
    assert cli_result["exit_code"] == 0
    assert cli_result["command"][1] == str(cli_script)
    assert json.loads(solution.exports["command_json"])[1] == str(cli_script)
    certificate = json.loads(solution.exports["external_certificate_json"])
    assert certificate["source"] == "external_solver_certificate"
    assert certificate["certificate"]["engine"] == "fake-external-cli"


@pytest.mark.parametrize("solver_name", [SolverName.packing_solver, SolverName.sparrow])
def test_external_cli_adapter_invalid_json_stays_failed(tmp_path: Path, solver_name: SolverName) -> None:
    cli_script = tmp_path / "bad_solver.py"
    cli_script.write_text("print('not-json')\n", encoding="utf-8")
    job = _job(solver_name, [sys.executable, str(cli_script)])

    solution = SolverOrchestrator().solve(job)[0]

    assert solution.status == "failed"
    assert solution.placed_items == []
    assert solution.unplaced_items[0].item_id == "box_a"
    assert "invalid JSON" in solution.unplaced_items[0].reason
    assert "not-json" in solution.exports["stdout"]
    assert solution.exports["cli_status"] == "passed"
    assert solution.exports["exit_code"] == "0"
    assert len(solution.exports["input_payload_sha256"]) == 64
    assert json.loads(solution.exports["cli_result_json"])["status"] == "passed"
    assert "invalid JSON" in solution.exports["error_message"]
    certificate = json.loads(solution.exports["certificate_json"])
    assert certificate["source"] == "external_solver_failure"
    assert certificate["status"] == "passed"


@pytest.mark.parametrize("solver_name", [SolverName.packing_solver, SolverName.sparrow])
def test_external_cli_adapter_missing_configuration_keeps_audit_evidence(solver_name: SolverName) -> None:
    solution = SolverOrchestrator().solve(_job(solver_name))[0]

    assert solution.status == "failed"
    assert solution.exports["cli_status"] == "not_configured"
    assert solution.exports["command_json"] == "[]"
    assert json.loads(solution.exports["cli_result_json"])["status"] == "not_configured"
    assert solver_name.value in solution.exports["error_message"]
    certificate = json.loads(solution.exports["certificate_json"])
    assert certificate["source"] == "external_solver_failure"
    assert certificate["status"] == "not_configured"


@pytest.mark.parametrize("solver_name", [SolverName.packing_solver, SolverName.sparrow])
def test_external_cli_adapter_missing_configuration_fails_batch_planning(solver_name: SolverName) -> None:
    result = plan_batch(_job(solver_name), "single_sheet")

    assert result.valid is False
    assert result.hard_rule_pass is False
    assert result.export_ok is False
    assert result.produced_units == 0
    assert result.shortage_units == 1
    assert solver_name.value in result.failure_reason or result.failure_reason == "validator_failed"
