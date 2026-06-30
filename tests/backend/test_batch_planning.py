from app.domain.schemas import NestingItem, NestingJob, PolygonAsset, SheetSpec
from app.services.batch_planning import plan_batch


def _sheet_787() -> SheetSpec:
    return SheetSpec(
        sheet_id="SHEET_787_1092",
        name="787x1092",
        width=787,
        height=1092,
        margin_top=10,
        margin_right=10,
        margin_bottom=10,
        margin_left=10,
        gripper_mm=20,
        material="white_card",
        thickness="350gsm",
    )


def _rect_item(item_id: str, width: float, height: float, quantity: int) -> NestingItem:
    return NestingItem(
        item_id=item_id,
        order_id=f"order_{item_id}",
        polygon=PolygonAsset(shape_id=f"shape_{item_id}", outer=[(0, 0), (width, 0), (width, height), (0, height)]),
        quantity=quantity,
        priority_score=1,
        allowed_rotations=[0, 90],
        min_gap_mm=2,
        bleed_mm=1,
    )


def test_pattern_batch_plan_satisfies_1000_moq_on_787_sheet() -> None:
    job = NestingJob(job_id="batch_pattern_787", sheet=_sheet_787(), candidate_items=[_rect_item("box", 80, 60, 1000)])

    result = plan_batch(job, "pattern")

    assert result.planning_mode == "pattern"
    assert result.requested_units == 1000
    assert result.units_per_sheet > 0
    assert result.sheets_used == 7
    assert result.produced_units >= 1000
    assert result.shortage_units == 0
    assert result.overproduction_units == result.produced_units - 1000
    assert result.quantity_fulfillment_rate == 1
    assert result.hard_rule_pass is True
    assert result.valid is True
    assert result.export_ok is True
    assert result.metrics["solution_statuses"] == ["valid"]


def test_expanded_batch_plan_solves_remaining_quantity_without_overproduction() -> None:
    job = NestingJob(job_id="batch_expanded_787", sheet=_sheet_787(), candidate_items=[_rect_item("box", 80, 60, 250)])

    result = plan_batch(job, "expanded")

    assert result.planning_mode == "expanded"
    assert result.requested_units == 250
    assert result.sheets_used == 2
    assert result.produced_units == 250
    assert result.shortage_units == 0
    assert result.overproduction_units == 0
    assert result.quantity_fulfillment_rate == 1
    assert result.hard_rule_pass is True
    assert result.metrics["units_by_sheet"][0] > result.metrics["units_by_sheet"][1]


def test_pattern_batch_plan_reports_quantity_shortage_when_shape_cannot_fit() -> None:
    job = NestingJob(job_id="batch_shortage_787", sheet=_sheet_787(), candidate_items=[_rect_item("oversize", 2000, 1800, 1000)])

    result = plan_batch(job, "pattern")

    assert result.valid is False
    assert result.hard_rule_pass is False
    assert result.units_per_sheet == 0
    assert result.sheets_used == 0
    assert result.produced_units == 0
    assert result.shortage_units == 1000
    assert result.quantity_fulfillment_rate == 0
    assert result.failure_reason in {"quantity_shortage", "validator_failed"}
