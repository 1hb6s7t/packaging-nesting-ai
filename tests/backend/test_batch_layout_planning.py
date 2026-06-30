from app.domain.schemas import ArtworkFeature, BatchArtworkItemRead, BBox, SheetParentSpec
from app.services.batch_layout import (
    CompatibilityGroupingService,
    SheetCutVariantGenerator,
)
from app.services.batch_patterns import (
    CandidateJobGenerator,
    PatternPlanner,
    ProductionPlanBuilder,
    TopKGlobalPlanSelector,
)


def _item(item_id: str, material: str, width: float, height: float, quantity: int = 1000) -> BatchArtworkItemRead:
    return BatchArtworkItemRead(
        item_id=item_id,
        batch_id="batch_test",
        filename=f"{item_id}.svg",
        source_format="svg",
        status="parsed",
        quantity=quantity,
        material=material,
        thickness="350gsm",
        print_method="offset",
        spot_color="none",
        feature=ArtworkFeature(
            bbox=BBox(width=width, height=height, min_x=0, min_y=0, max_x=width, max_y=height),
            area=width * height,
            area_ratio=0.01,
            aspect_ratio=width / height,
            parse_confidence=0.95,
        ),
        classification="FILLER",
    )


def test_grouping_uses_material_thickness_print_and_customer_rules() -> None:
    items = [_item("a", "white_card", 80, 60), _item("b", "kraft", 80, 60)]

    groups = CompatibilityGroupingService().group(items)

    assert len(groups) == 2
    assert {group.material for group in groups} == {"white_card", "kraft"}
    assert all(group.stats["item_count"] == 1 for group in groups)


def test_cut_variant_generator_outputs_787_parent_and_standard_cuts() -> None:
    parent = SheetParentSpec(parent_id="PARENT_787_1092", width=787, height=1092)

    variants = SheetCutVariantGenerator().generate(parent)

    by_code = {variant.code: variant for variant in variants}
    assert by_code["PARENT"].width == 787
    assert by_code["ROTATED"].height == 787
    assert by_code["HALF-H"].height == 546
    assert by_code["QUARTER"].kind == "quarter"


def test_candidate_generation_and_top3_selector_produce_diverse_legal_plans() -> None:
    parent = SheetParentSpec(parent_id="PARENT_787_1092", width=787, height=1092)
    items = [_item("box_a", "white_card", 80, 60), _item("box_b", "white_card", 90, 70)]
    groups = CompatibilityGroupingService().group(items)
    group = groups[0].model_copy(update={"job_id": "job_top3", "group_id": "group_1"})
    variants = SheetCutVariantGenerator().generate(parent)
    items_by_id = {item.item_id: item for item in items}

    candidates = CandidateJobGenerator().generate(
        job_id="job_top3",
        groups=[group],
        items_by_id=items_by_id,
        variants=variants,
        moq_per_item=1000,
    )
    selected = TopKGlobalPlanSelector().select(job_id="job_top3", candidates=candidates, top_k=3)

    assert len(candidates) >= 3
    assert len(selected) == 3
    plans = [plan for plan, _patterns in selected]
    assert [plan.rank for plan in plans] == [1, 2, 3]
    assert {plan.intent for plan in plans} == {"highest_utilization", "balanced_risk", "fastest_production"}
    assert all(plan.hard_rule_pass for plan in plans)
    assert all(plan.quantity_fulfillment_rate == 1 for plan in plans)


def test_pattern_planner_tracks_mixed_item_quantity_fulfillment_by_item() -> None:
    parent = SheetParentSpec(parent_id="PARENT_787_1092", width=787, height=1092)
    variant = next(variant for variant in SheetCutVariantGenerator().generate(parent) if variant.code == "PARENT")
    items = [
        _item("anchor_box", "white_card", 180, 120, quantity=1000),
        _item("filler_box", "white_card", 90, 70, quantity=1000),
    ]
    group = CompatibilityGroupingService().group(items)[0].model_copy(update={"job_id": "job_mixed", "group_id": "group_1"})

    pattern = PatternPlanner().plan_pattern(
        "job_mixed",
        group,
        items,
        variant,
        moq_per_item=1000,
    )
    quantity_summary = pattern.validator_report["quantity_summary"]

    assert pattern.hard_rule_pass is True
    assert pattern.quantity_fulfillment_rate == 1
    assert quantity_summary["requested_units_by_item"] == {"anchor_box": 1000, "filler_box": 1000}
    assert quantity_summary["units_per_sheet_by_item"] == {"anchor_box": 36, "filler_box": 120}
    assert quantity_summary["required_sheets_by_item"] == {"anchor_box": 28, "filler_box": 9}
    assert pattern.required_sheets == 28
    assert quantity_summary["produced_units_by_item"] == {"anchor_box": 1008, "filler_box": 3360}
    assert quantity_summary["shortage_units"] == 0
    assert quantity_summary["overproduction_units_by_item"] == {"anchor_box": 8, "filler_box": 2360}

    plan = ProductionPlanBuilder().build(
        job_id="job_mixed",
        rank=1,
        intent="highest_utilization",
        patterns=[pattern],
        diversity_score=1,
    )
    plan_quantity_summary = plan.validator_report["quantity_summary"]

    assert plan.hard_rule_pass is True
    assert plan.total_sheets_used == 28
    assert plan.quantity_fulfillment_rate == 1
    assert plan_quantity_summary["produced_units_by_item"]["anchor_box"] == 1008
    assert plan_quantity_summary["produced_units_by_item"]["filler_box"] == 3360
    assert plan_quantity_summary["shortage_units"] == 0
