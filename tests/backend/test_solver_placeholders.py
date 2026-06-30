import pytest

from app.domain.schemas import NestingItem, NestingJob, SheetSpec, SolverConfig, SolverName
from app.services.geometry import rectangle_asset
from app.services.solvers import SolverOrchestrator
from app.services.validator import validate_solution


@pytest.mark.parametrize("solver_name", [SolverName.packing_solver, SolverName.sparrow])
def test_placeholder_external_solver_stays_failed_when_validator_passes(solver_name: SolverName) -> None:
    job = NestingJob(
        job_id=f"job_placeholder_{solver_name.value}",
        sheet=SheetSpec(sheet_id="sheet_placeholder", width=100, height=100, material="white_card", thickness="350gsm"),
        candidate_items=[
            NestingItem(
                item_id="box_a",
                order_id="O001",
                polygon=rectangle_asset("box_a_shape", 10, 5),
                min_gap_mm=0,
                bleed_mm=0,
            ),
            NestingItem(
                item_id="box_b",
                order_id="O002",
                polygon=rectangle_asset("box_b_shape", 12, 6),
                min_gap_mm=0,
                bleed_mm=0,
            ),
        ],
        solver_config=SolverConfig(solver_name=solver_name),
    )

    solution = SolverOrchestrator().solve(job)[0]
    direct_report = validate_solution(job, solution)

    assert direct_report.is_valid
    assert solution.validation_report is not None
    assert solution.validation_report.is_valid
    assert solution.status == "failed"
    assert solution.placed_items == []
    assert {item.item_id for item in solution.unplaced_items} == {item.item_id for item in job.candidate_items}
    assert all(solver_name.value in item.reason for item in solution.unplaced_items)
