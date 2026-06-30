import pytest

from app.domain.schemas import NestingItem, NestingJob, PolygonAsset, SheetSpec
from app.services.batch_planning import plan_batch


def _stress_job(quantity: int) -> NestingJob:
    return NestingJob(
        job_id=f"stress_787_qty_{quantity}",
        sheet=SheetSpec(
            sheet_id="SHEET_787_1092_STRESS",
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
        ),
        candidate_items=[
            NestingItem(
                item_id="carton_80x60",
                order_id=f"stress_order_{quantity}",
                polygon=PolygonAsset(shape_id="carton_80x60_shape", outer=[(0, 0), (80, 0), (80, 60), (0, 60)]),
                quantity=quantity,
                priority_score=1,
                allowed_rotations=[0, 90],
                min_gap_mm=2,
                bleed_mm=1,
            )
        ],
        constraints={"max_batch_candidates_per_sheet": 600},
        top_k=1,
    )


@pytest.mark.parametrize("quantity", [1000, 3000, 5000, 10000, 15000])
def test_787_pattern_stress_quantities_meet_moq(quantity: int) -> None:
    result = plan_batch(_stress_job(quantity), "pattern")

    assert result.hard_rule_pass is True
    assert result.quantity_fulfillment_rate == 1
    assert result.shortage_units == 0
    assert result.units_per_sheet > 0
    assert result.sheets_used > 0
    assert result.case_score > 0


@pytest.mark.parametrize("quantity", [1000, 3000])
def test_787_expanded_stress_solves_exact_remaining_quantity(quantity: int) -> None:
    result = plan_batch(_stress_job(quantity), "expanded")

    assert result.hard_rule_pass is True
    assert result.quantity_fulfillment_rate == 1
    assert result.produced_units == quantity
    assert result.shortage_units == 0
    assert result.overproduction_units == 0
    assert result.sheets_used > 1
