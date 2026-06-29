from __future__ import annotations

from app.domain.schemas import NestingItem, NestingJob, NestingSolution, Placement, PolygonAsset, ValidationIssue, ValidationReport
from app.services.geometry import (
    EPSILON,
    calculate_bbox,
    calculate_container_margin,
    detect_collision,
    detect_containment,
    enrich_polygon,
    gripper_area_polygon,
    printable_area_polygon,
    rotate_polygon,
    translate_polygon,
)


def validate_solution(job: NestingJob, solution: NestingSolution) -> ValidationReport:
    issues: list[ValidationIssue] = []
    printable = printable_area_polygon(job.sheet)
    gripper = gripper_area_polygon(job.sheet)
    item_by_id = {item.item_id: item for item in [*job.fixed_items, *job.candidate_items]}

    placed_polygons: list[tuple[str, PolygonAsset, float]] = []
    for placement in solution.placed_items:
        item = item_by_id.get(placement.item_id)
        if item is None:
            issues.append(
                ValidationIssue(
                    code="unknown_item",
                    message=f"placement references unknown item {placement.item_id}",
                    item_ids=[placement.item_id],
                )
            )
            continue
        if placement.rotation not in item.allowed_rotations:
            issues.append(
                ValidationIssue(
                    code="rotation_invalid",
                    message=f"{placement.item_id} rotation {placement.rotation} is not allowed",
                    item_ids=[placement.item_id],
                )
            )
        polygon = _placed_polygon(item, placement)
        clearance = item.bleed_mm + item.min_gap_mm / 2
        printable_margin = calculate_container_margin(printable, polygon)
        if not detect_containment(printable, polygon) or printable_margin + EPSILON < clearance:
            issues.append(
                ValidationIssue(
                    code="out_of_bounds",
                    message=f"{placement.item_id} exceeds printable area or violates printable clearance",
                    item_ids=[placement.item_id],
                )
            )
        if detect_collision(gripper, polygon, min_gap_mm=clearance):
            issues.append(
                ValidationIssue(
                    code="gripper_conflict",
                    message=f"{placement.item_id} overlaps gripper area",
                    item_ids=[placement.item_id],
                )
            )
        placed_polygons.append((placement.item_id, polygon, clearance))

    for index, (left_id, left_poly, left_clearance) in enumerate(placed_polygons):
        for right_id, right_poly, right_clearance in placed_polygons[index + 1 :]:
            if detect_collision(left_poly, right_poly, min_gap_mm=left_clearance + right_clearance):
                issues.append(
                    ValidationIssue(
                        code="overlap",
                        message=f"{left_id} overlaps {right_id} after bleed and safety gap offset",
                        item_ids=[left_id, right_id],
                    )
                )

    return ValidationReport(
        is_valid=not any(issue.severity == "error" for issue in issues),
        overlap=any(issue.code == "overlap" for issue in issues),
        out_of_bounds=any(issue.code == "out_of_bounds" for issue in issues),
        gripper_conflict=any(issue.code == "gripper_conflict" for issue in issues),
        min_gap_violation=any(issue.code == "overlap" for issue in issues),
        rotation_invalid=any(issue.code == "rotation_invalid" for issue in issues),
        issues=issues,
    )


def _placed_polygon(item: NestingItem, placement: Placement) -> PolygonAsset:
    if placement.polygon is not None:
        return enrich_polygon(placement.polygon)
    polygon = rotate_polygon(enrich_polygon(item.polygon), placement.rotation)
    bbox = polygon.bbox or calculate_bbox(polygon.outer)
    return translate_polygon(polygon, placement.x - bbox.min_x, placement.y - bbox.min_y)
