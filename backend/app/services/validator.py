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
    job_items = [*job.fixed_items, *job.candidate_items]
    item_by_id = {item.item_id: item for item in job_items}
    candidate_ids = {item.item_id for item in job.candidate_items}
    placed_ids: list[str] = []
    unplaced_ids: list[str] = []
    issues.extend(_job_item_identity_issues(job_items))

    placed_polygons: list[tuple[str, PolygonAsset, float]] = []
    for placement in solution.placed_items:
        placed_ids.append(placement.item_id)
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
        if placement.order_id != item.order_id:
            issues.append(
                ValidationIssue(
                    code="placement_order_mismatch",
                    message=f"placement {placement.item_id} order_id does not match the job item",
                    item_ids=[placement.item_id],
                )
            )
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

    for unplaced in solution.unplaced_items:
        unplaced_ids.append(unplaced.item_id)
        if not unplaced.reason.strip():
            issues.append(
                ValidationIssue(
                    code="unplaced_reason_missing",
                    message=f"unplaced item {unplaced.item_id} must include a reason",
                    item_ids=[unplaced.item_id],
                )
            )
        item = item_by_id.get(unplaced.item_id)
        if item is None:
            issues.append(
                ValidationIssue(
                    code="unknown_unplaced_item",
                    message=f"unplaced item references unknown item {unplaced.item_id}",
                    item_ids=[unplaced.item_id],
                )
            )
            continue
        if unplaced.item_id not in candidate_ids:
            issues.append(
                ValidationIssue(
                    code="fixed_item_marked_unplaced",
                    message=f"fixed item {unplaced.item_id} cannot be reported as unplaced",
                    item_ids=[unplaced.item_id],
                )
            )
        if unplaced.order_id is not None and unplaced.order_id != item.order_id:
            issues.append(
                ValidationIssue(
                    code="unplaced_order_mismatch",
                    message=f"unplaced item {unplaced.item_id} order_id does not match the job item",
                    item_ids=[unplaced.item_id],
                )
            )

    issues.extend(_placement_completeness_issues(candidate_ids, placed_ids, unplaced_ids))

    return ValidationReport(
        is_valid=not any(issue.severity == "error" for issue in issues),
        overlap=any(issue.code == "overlap" for issue in issues),
        out_of_bounds=any(issue.code == "out_of_bounds" for issue in issues),
        gripper_conflict=any(issue.code == "gripper_conflict" for issue in issues),
        min_gap_violation=any(issue.code == "overlap" for issue in issues),
        rotation_invalid=any(issue.code == "rotation_invalid" for issue in issues),
        issues=issues,
    )


def _job_item_identity_issues(job_items: list[NestingItem]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            code="duplicate_job_item",
            message=f"job item_id {item_id} is declared more than once",
            item_ids=[item_id],
        )
        for item_id in sorted(_duplicates([item.item_id for item in job_items]))
    ]


def _placement_completeness_issues(
    candidate_ids: set[str],
    placed_ids: list[str],
    unplaced_ids: list[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    placed_set = set(placed_ids)
    unplaced_set = set(unplaced_ids)
    for item_id in sorted(_duplicates(placed_ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_placement",
                message=f"candidate item {item_id} is placed more than once",
                item_ids=[item_id],
            )
        )
    for item_id in sorted(_duplicates(unplaced_ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_unplaced",
                message=f"candidate item {item_id} is reported unplaced more than once",
                item_ids=[item_id],
            )
        )
    for item_id in sorted((placed_set & unplaced_set) & candidate_ids):
        issues.append(
            ValidationIssue(
                code="placed_and_unplaced",
                message=f"candidate item {item_id} is both placed and reported unplaced",
                item_ids=[item_id],
            )
        )
    for item_id in sorted(candidate_ids - placed_set - unplaced_set):
        issues.append(
            ValidationIssue(
                code="missing_candidate_disposition",
                message=f"candidate item {item_id} is neither placed nor reported unplaced",
                item_ids=[item_id],
            )
        )
    return issues


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _placed_polygon(item: NestingItem, placement: Placement) -> PolygonAsset:
    if placement.polygon is not None:
        return enrich_polygon(placement.polygon)
    polygon = rotate_polygon(enrich_polygon(item.polygon), placement.rotation)
    bbox = polygon.bbox or calculate_bbox(polygon.outer)
    return translate_polygon(polygon, placement.x - bbox.min_x, placement.y - bbox.min_y)
