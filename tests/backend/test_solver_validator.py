from app.domain.schemas import NestingItem, NestingJob, NestingSolution, Placement, PolygonAsset, SheetSpec
from app.services.geometry import enrich_polygon, rectangle_asset
from app.services.solvers import SolverOrchestrator
from app.services.validator import validate_solution


def test_rectpack_solver_runs_and_validator_marks_solution() -> None:
    job = NestingJob(
        job_id="job_test",
        sheet=SheetSpec(
            sheet_id="sheet_889_1194",
            width=889,
            height=1194,
            margin_top=10,
            margin_right=10,
            margin_bottom=10,
            margin_left=10,
            gripper_mm=12,
            material="white_card",
            thickness="350gsm",
            cost_per_sheet=4.2,
        ),
        candidate_items=[
            NestingItem(
                item_id="item_1",
                order_id="O001",
                polygon=rectangle_asset("shape_1", 120, 80),
                priority_score=0.9,
            ),
            NestingItem(
                item_id="item_2",
                order_id="O002",
                polygon=rectangle_asset("shape_2", 160, 120),
                priority_score=0.7,
            ),
        ],
    )
    solution = SolverOrchestrator().solve(job)[0]
    assert solution.placed_items
    assert solution.validation_report is not None
    assert solution.validation_report.is_valid
    assert solution.score is not None


def test_validator_uses_polygon_geometry_instead_of_bbox_overlap() -> None:
    first = enrich_polygon(PolygonAsset(shape_id="tri_a", outer=[(10, 10), (14, 10), (10, 14)]))
    second = enrich_polygon(PolygonAsset(shape_id="tri_b", outer=[(13.1, 13.1), (16, 13.1), (13.1, 16)]))
    job = NestingJob(
        job_id="job_precise_geometry",
        sheet=SheetSpec(sheet_id="sheet", width=100, height=100, material="white_card", thickness="350gsm"),
        candidate_items=[
            NestingItem(item_id="a", order_id="O001", polygon=first, min_gap_mm=1, bleed_mm=0),
            NestingItem(item_id="b", order_id="O002", polygon=second, min_gap_mm=1, bleed_mm=0),
        ],
    )
    solution = NestingSolution(
        solution_id="sol_precise_geometry",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[
            Placement(item_id="a", order_id="O001", x=10, y=10, polygon=first),
            Placement(item_id="b", order_id="O002", x=13.1, y=13.1, polygon=second),
        ],
    )

    report = validate_solution(job, solution)

    assert report.is_valid, [issue.model_dump() for issue in report.issues]


def test_validator_reconstructs_placement_polygon_when_payload_omits_it() -> None:
    job = NestingJob(
        job_id="job_placement_reconstruct",
        sheet=SheetSpec(sheet_id="sheet", width=100, height=100, material="white_card", thickness="350gsm"),
        candidate_items=[
            NestingItem(
                item_id="box",
                order_id="O001",
                polygon=rectangle_asset("box_shape", 10, 5),
                min_gap_mm=2,
                bleed_mm=1,
            )
        ],
    )
    solution = NestingSolution(
        solution_id="sol_placement_reconstruct",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="box", order_id="O001", x=20, y=20, rotation=90)],
    )

    report = validate_solution(job, solution)

    assert report.is_valid, [issue.model_dump() for issue in report.issues]
