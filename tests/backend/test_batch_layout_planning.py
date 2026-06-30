from app.domain.schemas import ArtworkFeature, BatchArtworkItemRead, BBox, SheetParentSpec
from app.services.batch_layout import (
    CandidateJobGenerator,
    CompatibilityGroupingService,
    SheetCutVariantGenerator,
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
