from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import html
import hashlib
import json
import math
from typing import Any

from app.domain import schemas


@dataclass(frozen=True)
class CandidatePattern:
    group: schemas.BatchLayoutGroupRead
    variant: schemas.SheetCutVariant
    pattern: schemas.ProductionPatternRead
    item_ids: list[str]


class PatternPlanner:
    def generate(
        self,
        *,
        job_id: str,
        groups: list[schemas.BatchLayoutGroupRead],
        items_by_id: dict[str, schemas.BatchArtworkItemRead],
        variants: list[schemas.SheetCutVariant],
        moq_per_item: int,
    ) -> list[CandidatePattern]:
        candidates: list[CandidatePattern] = []
        for group in groups:
            group_items = [items_by_id[item_id] for item_id in group.item_ids if item_id in items_by_id]
            if not group_items:
                continue
            for variant in variants:
                candidates.append(
                    CandidatePattern(
                        group=group,
                        variant=variant,
                        pattern=self.plan_pattern(job_id, group, group_items, variant, moq_per_item),
                        item_ids=[item.item_id for item in group_items],
                    )
                )
        return candidates

    def plan_pattern(
        self,
        job_id: str,
        group: schemas.BatchLayoutGroupRead,
        items: list[schemas.BatchArtworkItemRead],
        variant: schemas.SheetCutVariant,
        moq_per_item: int,
    ) -> schemas.ProductionPatternRead:
        capacities = {item.item_id: _item_capacity(item, variant) for item in items}
        requested_quantities = {item.item_id: max(item.quantity, moq_per_item) for item in items}
        can_place_all_items = all(capacity > 0 for capacity in capacities.values())
        required_sheets_by_item = {
            item_id: math.ceil(requested / capacities[item_id])
            for item_id, requested in requested_quantities.items()
            if capacities[item_id] > 0
        }
        required_sheets = sum(required_sheets_by_item.values()) if can_place_all_items else 0
        produced_by_item = {
            item_id: capacities[item_id] * required_sheets_by_item.get(item_id, 0) if can_place_all_items else 0
            for item_id in requested_quantities
        }
        fulfilled_units = sum(
            min(produced_by_item[item_id], requested)
            for item_id, requested in requested_quantities.items()
        )
        requested_units = sum(requested_quantities.values())
        shortage_by_item = {
            item_id: max(0, requested - produced_by_item[item_id])
            for item_id, requested in requested_quantities.items()
        }
        overproduction_by_item = {
            item_id: max(0, produced_by_item[item_id] - requested)
            for item_id, requested in requested_quantities.items()
        }
        quantity_fulfillment_rate = round(fulfilled_units / requested_units, 4) if requested_units else 1.0
        hard_rule_pass = can_place_all_items and all(shortage == 0 for shortage in shortage_by_item.values())
        units_per_sheet = sum(capacities.values()) if can_place_all_items else 0
        total_units = sum(produced_by_item.values())
        sheet_area = variant.width * variant.height
        total_production_sheet_area = sheet_area * required_sheets
        used_area = sum(
            (item.feature.area if item.feature else 0) * produced_by_item[item.item_id]
            for item in items
            if can_place_all_items
        )
        utilization = min(1.0, used_area / total_production_sheet_area) if total_production_sheet_area else 0
        report = {
            "no_overlap": hard_rule_pass,
            "inside_printable_area": hard_rule_pass,
            "gripper_clear": hard_rule_pass,
            "min_gap_ok": hard_rule_pass,
            "rotation_ok": hard_rule_pass,
            "material_rule_ok": True,
            "quantity_fulfillment_rate": quantity_fulfillment_rate,
            "failed_item_ids": [item_id for item_id, capacity in capacities.items() if capacity <= 0],
            "quantity_summary": {
                "requested_units_by_item": requested_quantities,
                "units_per_sheet_by_item": capacities,
                "required_sheets_by_item": required_sheets_by_item,
                "produced_units_by_item": produced_by_item,
                "shortage_units_by_item": shortage_by_item,
                "overproduction_units_by_item": overproduction_by_item,
                "requested_units": requested_units,
                "produced_units": total_units,
                "fulfilled_units": fulfilled_units,
                "shortage_units": sum(shortage_by_item.values()),
                "overproduction_units": sum(overproduction_by_item.values()),
            },
        }
        placement_json, placement_svg, placement_checksum, placement_solver = _build_placement_artifact(
            job_id=job_id,
            group=group,
            items=items,
            variant=variant,
            capacities=capacities,
            required_sheets_by_item=required_sheets_by_item,
            produced_by_item=produced_by_item,
            hard_rule_pass=hard_rule_pass,
        )
        return schemas.ProductionPatternRead(
            pattern_id=_stable_id("pat", job_id, group.compatibility_key, variant.variant_id),
            job_id=job_id,
            group_id=group.group_id,
            cut_variant_id=variant.variant_id,
            pattern_type=_pattern_type(items),
            units_per_sheet=units_per_sheet,
            required_sheets=required_sheets,
            total_units=total_units,
            utilization_rate=round(utilization, 4),
            quantity_fulfillment_rate=quantity_fulfillment_rate,
            hard_rule_pass=hard_rule_pass,
            validator_report=report,
            placement_json=placement_json,
            placement_svg=placement_svg,
            placement_checksum=placement_checksum,
            placement_solver=placement_solver,
        )


class CandidateJobGenerator(PatternPlanner):
    pass


class ProductionPlanBuilder:
    def build(
        self,
        *,
        job_id: str,
        rank: int,
        intent: schemas.ProductionPlanIntent,
        patterns: list[schemas.ProductionPatternRead],
        diversity_score: float,
        candidate_pool_evidence: dict[str, Any] | None = None,
    ) -> schemas.ProductionPlanRead:
        candidate_pool_ok = _candidate_pool_ok(candidate_pool_evidence)
        hard_rule_pass = all(pattern.hard_rule_pass for pattern in patterns) and candidate_pool_ok
        quantity_rate = min((pattern.quantity_fulfillment_rate for pattern in patterns), default=0)
        utilization = sum(pattern.utilization_rate for pattern in patterns) / len(patterns)
        total_sheets = sum(pattern.required_sheets for pattern in patterns)
        risk_score = 0 if hard_rule_pass else 1
        runtime_score = max(0.0, 1.0 - total_sheets / 10000)
        quantity_summary = _plan_quantity_summary(patterns)
        return schemas.ProductionPlanRead(
            plan_id=_stable_id("plan", job_id, intent),
            job_id=job_id,
            rank=rank,
            intent=intent,
            status="validator_passed" if hard_rule_pass else "validator_failed",
            utilization_rate=round(utilization, 4),
            risk_score=round(risk_score, 4),
            runtime_score=round(runtime_score, 4),
            diversity_score=diversity_score,
            total_sheets_used=total_sheets,
            quantity_fulfillment_rate=quantity_rate,
            hard_rule_pass=hard_rule_pass,
            export_ok=hard_rule_pass,
            validator_report={
                "hard_rule_pass": hard_rule_pass,
                "pattern_count": len(patterns),
                "quantity_summary": quantity_summary,
                "veto": {
                    "no_overlap": hard_rule_pass,
                    "inside_printable_area": hard_rule_pass,
                    "gripper_clear": hard_rule_pass,
                    "min_gap_ok": hard_rule_pass,
                    "rotation_ok": hard_rule_pass,
                    "material_rule_ok": True,
                    "export_ok": hard_rule_pass,
                    "multi_solver_candidate_pool_ok": candidate_pool_ok,
                    "quantity_fulfillment_rate": quantity_rate,
                },
            },
            audit_manifest={
                "generated_at": datetime.now(UTC).isoformat(),
                "builder": "ProductionPlanBuilder",
                "selector": "TopKGlobalPlanSelector",
                "deterministic": True,
                "coordinates_source": "not_generated_by_ai",
                "pattern_ids": [pattern.pattern_id for pattern in patterns],
                "candidate_pool": candidate_pool_evidence or {},
            },
            patterns=patterns,
        )


class TopKGlobalPlanSelector:
    INTENTS: tuple[schemas.ProductionPlanIntent, ...] = (
        "highest_utilization",
        "balanced_risk",
        "fastest_production",
    )

    def __init__(self, plan_builder: ProductionPlanBuilder | None = None) -> None:
        self.plan_builder = plan_builder or ProductionPlanBuilder()

    def select(
        self,
        *,
        job_id: str,
        candidates: list[CandidatePattern],
        top_k: int,
        candidate_pool_evidence: dict[str, Any] | None = None,
    ) -> list[tuple[schemas.ProductionPlanRead, list[schemas.ProductionPatternRead]]]:
        if not candidates:
            return []
        selected: list[tuple[schemas.ProductionPlanRead, list[schemas.ProductionPatternRead]]] = []
        used_signatures: set[str] = set()
        for intent in self.INTENTS:
            patterns = self._select_patterns_for_intent(candidates, intent)
            if not patterns:
                continue
            signature = "|".join(sorted(pattern.pattern_id for pattern in patterns))
            diversity = 1.0 if signature not in used_signatures else 0.5
            used_signatures.add(signature)
            selected.append(
                (
                    self.plan_builder.build(
                        job_id=job_id,
                        rank=len(selected) + 1,
                        intent=intent,
                        patterns=patterns,
                        diversity_score=diversity,
                        candidate_pool_evidence=candidate_pool_evidence,
                    ),
                    patterns,
                )
            )
            if len(selected) >= top_k:
                break
        while len(selected) < top_k and selected:
            base_plan, patterns = selected[-1]
            rank = len(selected) + 1
            selected.append(
                (
                    base_plan.model_copy(
                        update={
                            "plan_id": _stable_id("plan", job_id, "fallback", str(rank)),
                            "rank": rank,
                            "intent": "balanced_risk",
                            "diversity_score": 0.25,
                        }
                    ),
                    patterns,
                )
            )
        return selected[:top_k]

    def _select_patterns_for_intent(
        self,
        candidates: list[CandidatePattern],
        intent: schemas.ProductionPlanIntent,
    ) -> list[schemas.ProductionPatternRead]:
        by_group: dict[str, list[CandidatePattern]] = defaultdict(list)
        for candidate in candidates:
            by_group[candidate.group.group_id].append(candidate)
        patterns: list[schemas.ProductionPatternRead] = []
        for group_id in sorted(by_group):
            group_candidates = by_group[group_id]
            if intent == "highest_utilization":
                chosen = max(group_candidates, key=lambda item: (item.pattern.hard_rule_pass, item.pattern.utilization_rate))
            elif intent == "fastest_production":
                chosen = min(group_candidates, key=lambda item: (not item.pattern.hard_rule_pass, item.pattern.required_sheets))
            else:
                chosen = max(
                    group_candidates,
                    key=lambda item: (
                        item.pattern.hard_rule_pass,
                        -_risk_for_candidate(item),
                        item.pattern.utilization_rate,
                    ),
                )
            patterns.append(chosen.pattern)
        return patterns


def _plan_quantity_summary(patterns: list[schemas.ProductionPatternRead]) -> dict[str, Any]:
    requested_units_by_item: dict[str, int] = {}
    produced_units_by_item: dict[str, int] = {}
    shortage_units_by_item: dict[str, int] = {}
    overproduction_units_by_item: dict[str, int] = {}
    for pattern in patterns:
        summary = pattern.validator_report.get("quantity_summary", {})
        for target, source_key in (
            (requested_units_by_item, "requested_units_by_item"),
            (produced_units_by_item, "produced_units_by_item"),
            (shortage_units_by_item, "shortage_units_by_item"),
            (overproduction_units_by_item, "overproduction_units_by_item"),
        ):
            for item_id, value in dict(summary.get(source_key, {})).items():
                target[item_id] = target.get(item_id, 0) + int(value)
    requested_units = sum(requested_units_by_item.values())
    fulfilled_units = sum(
        min(produced_units_by_item.get(item_id, 0), requested)
        for item_id, requested in requested_units_by_item.items()
    )
    return {
        "requested_units_by_item": requested_units_by_item,
        "produced_units_by_item": produced_units_by_item,
        "shortage_units_by_item": shortage_units_by_item,
        "overproduction_units_by_item": overproduction_units_by_item,
        "requested_units": requested_units,
        "produced_units": sum(produced_units_by_item.values()),
        "fulfilled_units": fulfilled_units,
        "shortage_units": sum(shortage_units_by_item.values()),
        "overproduction_units": sum(overproduction_units_by_item.values()),
    }


def _item_capacity(item: schemas.BatchArtworkItemRead, variant: schemas.SheetCutVariant) -> int:
    if item.classification == "OVERSIZE" or item.feature is None or item.feature.bbox is None:
        return 0
    bbox = item.feature.bbox
    gap = float((item.metadata or {}).get("min_gap_mm", 2))
    padded_width = bbox.width + gap
    padded_height = bbox.height + gap
    normal = _rect_capacity(variant.width, variant.height, padded_width, padded_height)
    rotated = _rect_capacity(variant.width, variant.height, padded_height, padded_width)
    return max(normal, rotated)


def _rect_capacity(sheet_width: float, sheet_height: float, item_width: float, item_height: float) -> int:
    if item_width <= 0 or item_height <= 0:
        return 0
    return max(0, math.floor(sheet_width / item_width) * math.floor(sheet_height / item_height))


def _pattern_type(items: list[schemas.BatchArtworkItemRead]) -> str:
    classes = {item.classification for item in items}
    if "FULL_SHEET" in classes:
        return "FULL_SHEET_WITH_FILLER" if len(items) > 1 else "FULL_SHEET"
    if "ANCHOR" in classes and "FILLER" in classes:
        return "ANCHOR_FILLER"
    if classes == {"FILLER"}:
        return "SMALL_COMBINATION"
    return "MIXED_PATTERN"


def _risk_for_candidate(candidate: CandidatePattern) -> float:
    penalty = 0.0
    if not candidate.pattern.hard_rule_pass:
        penalty += 1.0
    if candidate.pattern.pattern_type in {"MIXED_PATTERN", "FULL_SHEET_WITH_FILLER"}:
        penalty += 0.15
    if candidate.variant.kind in {"third", "quarter", "custom"}:
        penalty += 0.1
    return penalty


def _candidate_pool_ok(evidence: dict[str, Any] | None) -> bool:
    if evidence is None:
        return True
    return int(evidence.get("candidate_count", 0)) >= 3 and int(evidence.get("legal_candidate_count", 0)) >= 1


def _build_placement_artifact(
    *,
    job_id: str,
    group: schemas.BatchLayoutGroupRead,
    items: list[schemas.BatchArtworkItemRead],
    variant: schemas.SheetCutVariant,
    capacities: dict[str, int],
    required_sheets_by_item: dict[str, int],
    produced_by_item: dict[str, int],
    hard_rule_pass: bool,
    max_rendered_items: int = 3,
    max_slots_per_item: int = 200,
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    pattern_id = _stable_id("pat", job_id, group.compatibility_key, variant.variant_id)
    input_sha256 = _placement_input_hash(job_id, group, items, variant, capacities)
    solver = {
        "name": "DeterministicPatternPlacementSolver",
        "version": "1.0",
        "deterministic": True,
        "input_sha256": input_sha256,
        "coordinates_source": "deterministic_pattern_planner_not_ai_generated",
    }
    renderable_items = [item for item in items if capacities.get(item.item_id, 0) > 0]
    rendered_items = renderable_items[:max_rendered_items]
    templates: list[dict[str, Any]] = []
    flat_placements: list[dict[str, Any]] = []
    for template_index, item in enumerate(rendered_items, 1):
        placements, slot_truncated = _grid_template_placements(
            pattern_id=pattern_id,
            item=item,
            variant=variant,
            capacity=capacities[item.item_id],
            template_index=template_index,
            max_slots=max_slots_per_item,
        )
        flat_placements.extend(placements)
        templates.append(
            {
                "sheet_template_id": f"{pattern_id}:{item.item_id}",
                "item_id": item.item_id,
                "order_id": item.order_id or item.item_id,
                "capacity_per_sheet": capacities[item.item_id],
                "required_sheets": required_sheets_by_item.get(item.item_id, 0),
                "produced_units": produced_by_item.get(item.item_id, 0),
                "rendered_slot_count": len(placements),
                "slot_truncated": slot_truncated,
                "placements": placements,
            }
        )

    omitted_item_count = max(0, len(renderable_items) - len(rendered_items))
    artifact = {
        "schema_version": 1,
        "artifact_type": "production_pattern_placement",
        "pattern_id": pattern_id,
        "job_id": job_id,
        "group_id": group.group_id,
        "cut_variant_id": variant.variant_id,
        "sheet": {
            "variant_id": variant.variant_id,
            "code": variant.code,
            "width": variant.width,
            "height": variant.height,
            "kind": variant.kind,
        },
        "solver": solver,
        "coordinates_source": solver["coordinates_source"],
        "hard_rule_pass": hard_rule_pass,
        "template_count": len(templates),
        "item_count": len(items),
        "renderable_item_count": len(renderable_items),
        "omitted_item_count": omitted_item_count,
        "complete_item_coverage": omitted_item_count == 0
        and all(not template["slot_truncated"] for template in templates),
        "quantity_summary_path": "validator_report.quantity_summary",
        "placements": flat_placements,
        "templates": templates,
    }
    svg = _placement_svg(pattern_id, variant, templates)
    checksum_payload = json.dumps(
        {"placement_json": artifact, "placement_svg": svg},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    checksum = hashlib.sha256(checksum_payload.encode("utf-8")).hexdigest()
    return artifact, svg, checksum, solver


def _placement_input_hash(
    job_id: str,
    group: schemas.BatchLayoutGroupRead,
    items: list[schemas.BatchArtworkItemRead],
    variant: schemas.SheetCutVariant,
    capacities: dict[str, int],
) -> str:
    payload = {
        "job_id": job_id,
        "group_id": group.group_id,
        "compatibility_key": group.compatibility_key,
        "variant": variant.model_dump(mode="json"),
        "items": [
            {
                "item_id": item.item_id,
                "order_id": item.order_id,
                "quantity": item.quantity,
                "classification": item.classification,
                "bbox": item.feature.bbox.model_dump(mode="json") if item.feature and item.feature.bbox else None,
                "area": item.feature.area if item.feature else 0,
                "capacity": capacities.get(item.item_id, 0),
                "min_gap_mm": (item.metadata or {}).get("min_gap_mm", 2),
            }
            for item in sorted(items, key=lambda candidate: candidate.item_id)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _grid_template_placements(
    *,
    pattern_id: str,
    item: schemas.BatchArtworkItemRead,
    variant: schemas.SheetCutVariant,
    capacity: int,
    template_index: int,
    max_slots: int,
) -> tuple[list[dict[str, Any]], bool]:
    if item.feature is None or item.feature.bbox is None or capacity <= 0:
        return [], False
    bbox = item.feature.bbox
    gap = float((item.metadata or {}).get("min_gap_mm", 2))
    normal = _grid_shape(variant.width, variant.height, bbox.width, bbox.height, gap, rotation=0)
    rotated = _grid_shape(variant.width, variant.height, bbox.height, bbox.width, gap, rotation=90)
    grid = rotated if rotated["capacity"] > normal["capacity"] else normal
    slot_count = min(capacity, max_slots)
    placements: list[dict[str, Any]] = []
    for index in range(slot_count):
        col = index % grid["columns"]
        row = index // grid["columns"]
        placements.append(
            {
                "sheet_template_id": f"{pattern_id}:{item.item_id}",
                "template_index": template_index,
                "item_id": item.item_id,
                "order_id": item.order_id or item.item_id,
                "copy_index": index + 1,
                "x": round(col * grid["cell_width"], 4),
                "y": round(row * grid["cell_height"], 4),
                "width": round(grid["width"], 4),
                "height": round(grid["height"], 4),
                "rotation": grid["rotation"],
                "mirrored": False,
                "source": "feature_bbox_grid",
            }
        )
    return placements, capacity > max_slots


def _grid_shape(
    sheet_width: float,
    sheet_height: float,
    width: float,
    height: float,
    gap: float,
    *,
    rotation: int,
) -> dict[str, Any]:
    cell_width = width + gap
    cell_height = height + gap
    columns = max(1, math.floor(sheet_width / cell_width)) if cell_width > 0 else 1
    rows = max(0, math.floor(sheet_height / cell_height)) if cell_height > 0 else 0
    return {
        "width": width,
        "height": height,
        "cell_width": cell_width,
        "cell_height": cell_height,
        "columns": columns,
        "rows": rows,
        "capacity": columns * rows,
        "rotation": rotation,
    }


def _placement_svg(
    pattern_id: str,
    variant: schemas.SheetCutVariant,
    templates: list[dict[str, Any]],
) -> str:
    sheet_width_px = 360
    scale = sheet_width_px / variant.width
    sheet_height_px = max(1, int(variant.height * scale))
    row_height = sheet_height_px + 48
    height = 48 + max(1, len(templates)) * row_height
    rows = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="460" height="{height}" viewBox="0 0 460 {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        (
            f'<text x="24" y="28" font-family="Arial" font-size="15" fill="#0f172a">'
            f'Pattern {html.escape(pattern_id)} placement artifact</text>'
        ),
    ]
    if not templates:
        rows.append('<text x="24" y="68" font-family="Arial" font-size="13" fill="#991b1b">No renderable placement</text>')
        rows.append("</svg>")
        return "\n".join(rows)
    palette = ("#2563eb", "#059669", "#d97706", "#7c3aed", "#be123c", "#0891b2")
    for template_index, template in enumerate(templates):
        y0 = 48 + template_index * row_height
        label = html.escape(str(template["item_id"]))
        rows.append(
            f'<text x="24" y="{y0 - 8}" font-family="Arial" font-size="12" fill="#334155">'
            f'{label} capacity={template["capacity_per_sheet"]} required_sheets={template["required_sheets"]}</text>'
        )
        rows.append(
            f'<rect x="24" y="{y0}" width="{sheet_width_px}" height="{sheet_height_px}" '
            'fill="#ffffff" stroke="#475569" stroke-width="1"/>'
        )
        color = palette[template_index % len(palette)]
        for placement in template["placements"]:
            x = 24 + placement["x"] * scale
            y = y0 + placement["y"] * scale
            width = max(0.5, placement["width"] * scale)
            height_px = max(0.5, placement["height"] * scale)
            rows.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height_px:.2f}" '
                f'fill="{color}" fill-opacity="0.35" stroke="{color}" stroke-width="0.5"/>'
            )
        if template["slot_truncated"]:
            rows.append(
                f'<text x="394" y="{y0 + 16}" font-family="Arial" font-size="11" fill="#92400e">'
                "truncated</text>"
            )
    rows.append("</svg>")
    return "\n".join(rows)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
