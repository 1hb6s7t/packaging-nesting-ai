from app.domain.schemas import NestingItem, NestingJob, NestingSolution, Placement, PolygonAsset, SheetSpec, UnplacedItem
from app.services.geometry import enrich_polygon, rectangle_asset
from app.services.reports import generate_solution_report
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


def test_validator_rejects_candidate_missing_from_placed_and_unplaced() -> None:
    job = _two_item_job()
    solution = NestingSolution(
        solution_id="sol_missing_candidate",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="a", order_id="O001", x=20, y=20)],
        unplaced_items=[],
    )

    report = validate_solution(job, solution)

    assert not report.is_valid
    assert any(issue.code == "missing_candidate_disposition" and issue.item_ids == ["b"] for issue in report.issues)


def test_validator_rejects_duplicate_and_conflicting_candidate_disposition() -> None:
    job = _two_item_job()
    solution = NestingSolution(
        solution_id="sol_duplicate_candidate",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[
            Placement(item_id="a", order_id="O001", x=20, y=20),
            Placement(item_id="a", order_id="O001", x=40, y=20),
        ],
        unplaced_items=[UnplacedItem(item_id="a", order_id="O001", reason="forced conflict")],
    )

    report = validate_solution(job, solution)

    codes = {issue.code for issue in report.issues}
    assert not report.is_valid
    assert "duplicate_placement" in codes
    assert "placed_and_unplaced" in codes
    assert "missing_candidate_disposition" in codes


def test_validator_rejects_unknown_unplaced_item() -> None:
    job = _two_item_job()
    solution = NestingSolution(
        solution_id="sol_unknown_unplaced",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="a", order_id="O001", x=20, y=20)],
        unplaced_items=[
            UnplacedItem(item_id="b", order_id="O002", reason="does not fit"),
            UnplacedItem(item_id="ghost", order_id="O999", reason="not in job"),
        ],
    )

    report = validate_solution(job, solution)

    assert not report.is_valid
    assert any(issue.code == "unknown_unplaced_item" and issue.item_ids == ["ghost"] for issue in report.issues)


def test_validator_rejects_duplicate_job_item_ids_and_order_mismatch() -> None:
    job = NestingJob.model_construct(
        job_id="job_duplicate_item_ids",
        sheet=SheetSpec(sheet_id="sheet", width=100, height=100, material="white_card", thickness="350gsm"),
        candidate_items=[
            NestingItem(item_id="dup", order_id="O001", polygon=rectangle_asset("box_a", 10, 5), min_gap_mm=0, bleed_mm=0),
            NestingItem(item_id="dup", order_id="O002", polygon=rectangle_asset("box_b", 10, 5), min_gap_mm=0, bleed_mm=0),
        ],
        fixed_items=[],
    )
    solution = NestingSolution(
        solution_id="sol_duplicate_job_item_ids",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="dup", order_id="WRONG", x=20, y=20)],
    )

    report = validate_solution(job, solution)

    codes = {issue.code for issue in report.issues}
    assert not report.is_valid
    assert "duplicate_job_item" in codes
    assert "placement_order_mismatch" in codes


def test_validator_rejects_unplaced_item_without_reason() -> None:
    job = _two_item_job()
    solution = NestingSolution(
        solution_id="sol_unplaced_reason",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="a", order_id="O001", x=20, y=20)],
        unplaced_items=[UnplacedItem(item_id="b", order_id="O002", reason="  ")],
    )

    report = validate_solution(job, solution)

    assert not report.is_valid
    assert any(issue.code == "unplaced_reason_missing" and issue.item_ids == ["b"] for issue in report.issues)


def test_solution_report_includes_delivery_summary_and_item_dispositions() -> None:
    job = _two_item_job()
    solution = NestingSolution(
        solution_id="sol_delivery_report",
        job_id=job.job_id,
        solver="unit-test",
        utilization_rate=0.7,
        waste_rate=0.3,
        placed_items=[Placement(item_id="a", order_id="O001", x=20, y=20, width=10, height=5)],
        unplaced_items=[UnplacedItem(item_id="b", order_id="O002", reason="does not fit current sheet")],
    )
    solution.validation_report = validate_solution(job, solution)

    report = generate_solution_report(job, solution)

    assert report["candidate_count"] == 2
    assert report["placed_count"] == 1
    assert report["unplaced_count"] == 1
    assert report["estimated_waste_cost"] == 0.0
    assert report["validation_summary"]["is_valid"] is True
    assert report["validation_summary"]["issue_count"] == 0
    assert report["placed_items"][0]["item_id"] == "a"
    assert report["unplaced_items"][0]["reason"] == "does not fit current sheet"

    invalid_solution = NestingSolution(
        solution_id="sol_delivery_report_invalid",
        job_id=job.job_id,
        solver="unit-test",
        placed_items=[Placement(item_id="a", order_id="O001", x=20, y=20, width=10, height=5)],
    )
    invalid_solution.validation_report = validate_solution(job, invalid_solution)
    invalid_report = generate_solution_report(job, invalid_solution)

    assert invalid_report["validation_summary"]["is_valid"] is False
    assert invalid_report["validation_summary"]["error_count"] == 1
    assert invalid_report["validation_summary"]["issue_codes"] == ["missing_candidate_disposition"]


def _two_item_job() -> NestingJob:
    return NestingJob(
        job_id="job_validator_completeness",
        sheet=SheetSpec(sheet_id="sheet", width=100, height=100, material="white_card", thickness="350gsm"),
        candidate_items=[
            NestingItem(
                item_id="a",
                order_id="O001",
                polygon=rectangle_asset("box_a", 10, 5),
                min_gap_mm=0,
                bleed_mm=0,
            ),
            NestingItem(
                item_id="b",
                order_id="O002",
                polygon=rectangle_asset("box_b", 10, 5),
                min_gap_mm=0,
                bleed_mm=0,
            ),
        ],
    )
