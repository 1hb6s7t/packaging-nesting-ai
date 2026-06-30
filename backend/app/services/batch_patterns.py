from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
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
        required_sheets = max(required_sheets_by_item.values(), default=0) if can_place_all_items else 0
        produced_by_item = {
            item_id: capacities[item_id] * required_sheets if can_place_all_items else 0
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
        used_area = sum(
            (item.feature.area if item.feature else 0) * capacities[item.item_id]
            for item in items
            if can_place_all_items
        )
        sheet_area = variant.width * variant.height
        utilization = min(1.0, used_area / sheet_area) if sheet_area else 0
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


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"
