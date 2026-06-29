from __future__ import annotations

import time
from uuid import uuid4

from app.domain.schemas import NestingJob, NestingSolution, Placement, SolverConfig, SolverName, UnplacedItem
from app.services.geometry import calculate_bbox, enrich_polygon, printable_area_polygon, rotate_polygon, translate_polygon
from app.services.solvers.base import SolverAdapter


class RectpackSolverAdapter(SolverAdapter):
    name = SolverName.rectpack
    version = "mvp-shelf-0.1.0"

    def supports(self, job: NestingJob) -> bool:
        return bool(job.candidate_items)

    def solve(self, job: NestingJob, config: SolverConfig) -> NestingSolution:
        start = time.perf_counter()
        printable = printable_area_polygon(job.sheet)
        bounds = printable.bbox or calculate_bbox(printable.outer)
        cursor_x = bounds.min_x
        cursor_y = bounds.min_y
        row_height = 0.0
        placed: list[Placement] = []
        unplaced: list[UnplacedItem] = []

        candidates = sorted(job.candidate_items, key=lambda item: item.priority_score, reverse=True)
        for item in candidates:
            polygon = enrich_polygon(item.polygon)
            best_rotation = 0
            best_width = polygon.bbox.width if polygon.bbox else 0
            best_height = polygon.bbox.height if polygon.bbox else 0
            for rotation in item.allowed_rotations:
                rotated = rotate_polygon(polygon, rotation)
                bbox = rotated.bbox or calculate_bbox(rotated.outer)
                if bbox.width <= bounds.width and bbox.height <= bounds.height:
                    best_rotation = rotation
                    best_width = bbox.width
                    best_height = bbox.height
                    polygon = rotated
                    break

            clearance = item.bleed_mm + item.min_gap_mm / 2
            padded_width = best_width + clearance * 2
            padded_height = best_height + clearance * 2
            if cursor_x + padded_width > bounds.max_x:
                cursor_x = bounds.min_x
                cursor_y += row_height
                row_height = 0.0
            if cursor_y + padded_height > bounds.max_y:
                unplaced.append(UnplacedItem(item_id=item.item_id, order_id=item.order_id, reason="剩余空白区域无法满足外接尺寸和安全间距"))
                continue

            place_x = cursor_x + clearance
            place_y = cursor_y + clearance
            placed_polygon = translate_polygon(
                polygon,
                place_x - (polygon.bbox.min_x if polygon.bbox else 0),
                place_y - (polygon.bbox.min_y if polygon.bbox else 0),
            )
            placed.append(
                Placement(
                    item_id=item.item_id,
                    order_id=item.order_id,
                    x=round(place_x, 3),
                    y=round(place_y, 3),
                    rotation=best_rotation,
                    mirrored=False,
                    width=round(best_width, 3),
                    height=round(best_height, 3),
                    polygon=placed_polygon,
                )
            )
            cursor_x += padded_width
            row_height = max(row_height, padded_height)

        printable_area = printable.area or 1
        used_area = sum(p.polygon.area or 0 for p in placed if p.polygon)
        utilization = min(1.0, used_area / printable_area)
        runtime_ms = int((time.perf_counter() - start) * 1000)
        return NestingSolution(
            solution_id=f"sol_{uuid4().hex[:16]}",
            job_id=job.job_id,
            solver=self.name,
            status="candidate",
            rank=1,
            runtime_ms=runtime_ms,
            utilization_rate=round(utilization, 4),
            waste_rate=round(1 - utilization, 4),
            placed_items=placed,
            unplaced_items=unplaced,
        )
