import json

from app.domain.schemas import NestingItem, NestingJob, SheetSpec, SolverName
from app.services.geometry import rectangle_asset
from app.services.solvers.multi_orchestrator import MultiSolverOrchestrator


def test_multi_solver_orchestrator_builds_ranked_candidate_pool_with_evidence() -> None:
    job = NestingJob(
        job_id="job_multi_solver_pool",
        sheet=SheetSpec(
            sheet_id="sheet_pool",
            width=260,
            height=220,
            margin_top=10,
            margin_right=10,
            margin_bottom=10,
            margin_left=10,
            gripper_mm=0,
            material="white_card",
            thickness="350gsm",
        ),
        candidate_items=[
            NestingItem(
                item_id="box_a",
                order_id="order_a",
                polygon=rectangle_asset("shape_a", 40, 30),
                min_gap_mm=0,
                bleed_mm=0,
            ),
            NestingItem(
                item_id="box_b",
                order_id="order_b",
                polygon=rectangle_asset("shape_b", 35, 25),
                min_gap_mm=0,
                bleed_mm=0,
            ),
        ],
    )

    orchestrator = MultiSolverOrchestrator()
    solutions = orchestrator.solve_candidate_pool(
        job,
        solver_names=[SolverName.rectpack, SolverName.packing_solver, SolverName.sparrow],
        seeds=[0, 17],
        time_limits_sec=[1],
        rotation_policies=["as_declared", "zero_only"],
    )
    report = orchestrator.candidate_pool_report(solutions)

    assert len(solutions) == 12
    assert [solution.rank for solution in solutions] == list(range(1, 13))
    assert report["candidate_count"] == 12
    assert report["legal_candidate_count"] >= 2
    assert report["failed_candidate_count"] >= 4
    assert set(report["solver_names"]) == {"PackingSolver", "RectpackSolver", "SparrowSolver"}
    assert set(report["rotation_policies"]) == {"as_declared", "zero_only"}
    assert all(solution.validation_report is not None for solution in solutions)
    assert all(solution.score is not None for solution in solutions)

    rectpack = [solution for solution in solutions if solution.solver == SolverName.rectpack]
    external_failed = [solution for solution in solutions if solution.solver in {SolverName.packing_solver, SolverName.sparrow}]
    assert any(solution.status == "valid" for solution in rectpack)
    assert all(solution.status == "failed" for solution in external_failed)

    manifest = json.loads(solutions[0].exports["audit_manifest_json"])
    assert manifest["candidate_id"] == solutions[0].solution_id
    assert manifest["solver_name"]
    assert manifest["rotation_policy"] in {"as_declared", "zero_only"}
    assert "validator_issue_codes" in manifest
