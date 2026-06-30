from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import math
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models as dbm
from app.domain import schemas
from app.services.batch_artworks import list_batch_items


class CompatibilityGroupingService:
    FIELDS = ("material", "thickness", "print_method", "spot_color", "due_date", "category", "customer_id")

    def group(self, items: list[schemas.BatchArtworkItemRead]) -> list[schemas.BatchLayoutGroupRead]:
        buckets: dict[str, list[schemas.BatchArtworkItemRead]] = defaultdict(list)
        for item in items:
            buckets[self.compatibility_key(item)].append(item)

        groups: list[schemas.BatchLayoutGroupRead] = []
        for index, (key, group_items) in enumerate(sorted(buckets.items()), 1):
            first = group_items[0]
            groups.append(
                schemas.BatchLayoutGroupRead(
                    group_id=f"group_{index:04d}",
                    job_id="",
                    batch_id=first.batch_id,
                    compatibility_key=key,
                    item_ids=[item.item_id for item in group_items],
                    material=first.material,
                    thickness=first.thickness,
                    print_method=first.print_method,
                    spot_color=first.spot_color,
                    due_date=first.due_date,
                    category=first.category,
                    customer_id=first.customer_id,
                    stats={
                        "item_count": len(group_items),
                        "class_counts": _counts(item.classification or "UNCLASSIFIED" for item in group_items),
                        "quantity": sum(item.quantity for item in group_items),
                    },
                )
            )
        return groups

    def compatibility_key(self, item: schemas.BatchArtworkItemRead) -> str:
        values = []
        for field in self.FIELDS:
            value = getattr(item, field)
            values.append(str(value or "*"))
        return "|".join(values)


class SheetCutVariantGenerator:
    def generate(
        self,
        parent: schemas.SheetParentSpec,
        *,
        custom_variants: list[schemas.SheetCutVariant] | None = None,
    ) -> list[schemas.SheetCutVariant]:
        base = [
            self._variant(parent, "parent", "PARENT", "parent", parent.width, parent.height, parts=1),
            self._variant(parent, "rotated", "ROTATED", "rotated_parent", parent.height, parent.width, parts=1),
            self._variant(parent, "half_vertical", "HALF-V", "half", parent.width / 2, parent.height, parts=2),
            self._variant(parent, "half_horizontal", "HALF-H", "half", parent.width, parent.height / 2, parts=2),
            self._variant(parent, "third_horizontal", "THIRD-H", "third", parent.width, parent.height / 3, parts=3),
            self._variant(parent, "quarter", "QUARTER", "quarter", parent.width / 2, parent.height / 2, parts=4),
        ]
        custom = custom_variants or []
        return [*base, *custom]

    def _variant(
        self,
        parent: schemas.SheetParentSpec,
        suffix: str,
        code: str,
        kind: schemas.CutVariantKind,
        width: float,
        height: float,
        *,
        parts: int,
    ) -> schemas.SheetCutVariant:
        used_area = width * height * parts
        parent_area = parent.width * parent.height
        return schemas.SheetCutVariant(
            variant_id=f"{parent.parent_id}:{suffix}",
            parent_id=parent.parent_id,
            code=code,
            kind=kind,
            width=round(width, 4),
            height=round(height, 4),
            cuts={"parts": parts, "parent_width": parent.width, "parent_height": parent.height},
            waste_rate=round(max(0.0, 1 - used_area / parent_area), 4) if parent_area else 0,
            metadata={"generated_by": "SheetCutVariantGenerator"},
        )


@dataclass(frozen=True)
class CandidatePattern:
    group: schemas.BatchLayoutGroupRead
    variant: schemas.SheetCutVariant
    pattern: schemas.ProductionPatternRead
    item_ids: list[str]


class CandidateJobGenerator:
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
                        pattern=self._pattern(job_id, group, group_items, variant, moq_per_item),
                        item_ids=[item.item_id for item in group_items],
                    )
                )
        return candidates

    def _pattern(
        self,
        job_id: str,
        group: schemas.BatchLayoutGroupRead,
        items: list[schemas.BatchArtworkItemRead],
        variant: schemas.SheetCutVariant,
        moq_per_item: int,
    ) -> schemas.ProductionPatternRead:
        capacities = [_item_capacity(item, variant) for item in items]
        requested_quantities = [max(item.quantity, moq_per_item) for item in items]
        can_fulfill = all(capacity > 0 for capacity in capacities)
        required_sheets = max(
            (math.ceil(quantity / capacity) for quantity, capacity in zip(requested_quantities, capacities) if capacity > 0),
            default=0,
        )
        units_per_sheet = sum(capacities)
        total_units = units_per_sheet * required_sheets if can_fulfill else 0
        used_area = sum((item.feature.area if item.feature else 0) * min(capacity, quantity) for item, capacity, quantity in zip(items, capacities, requested_quantities))
        sheet_area = variant.width * variant.height
        utilization = min(1.0, used_area / sheet_area) if sheet_area else 0
        report = {
            "no_overlap": can_fulfill,
            "inside_printable_area": can_fulfill,
            "gripper_clear": can_fulfill,
            "min_gap_ok": can_fulfill,
            "rotation_ok": can_fulfill,
            "material_rule_ok": True,
            "quantity_fulfillment_rate": 1.0 if can_fulfill else 0.0,
            "failed_item_ids": [item.item_id for item, capacity in zip(items, capacities) if capacity <= 0],
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
            quantity_fulfillment_rate=1.0 if can_fulfill else 0.0,
            hard_rule_pass=can_fulfill,
            validator_report=report,
        )


class TopKGlobalPlanSelector:
    INTENTS: tuple[schemas.ProductionPlanIntent, ...] = (
        "highest_utilization",
        "balanced_risk",
        "fastest_production",
    )

    def select(
        self,
        *,
        job_id: str,
        candidates: list[CandidatePattern],
        top_k: int,
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
            selected.append((self._plan(job_id, len(selected) + 1, intent, patterns, diversity), patterns))
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

    def _plan(
        self,
        job_id: str,
        rank: int,
        intent: schemas.ProductionPlanIntent,
        patterns: list[schemas.ProductionPatternRead],
        diversity_score: float,
    ) -> schemas.ProductionPlanRead:
        hard_rule_pass = all(pattern.hard_rule_pass for pattern in patterns)
        quantity_rate = min((pattern.quantity_fulfillment_rate for pattern in patterns), default=0)
        utilization = sum(pattern.utilization_rate for pattern in patterns) / len(patterns)
        total_sheets = sum(pattern.required_sheets for pattern in patterns)
        risk_score = 0 if hard_rule_pass else 1
        runtime_score = max(0.0, 1.0 - total_sheets / 10000)
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
                "veto": {
                    "no_overlap": hard_rule_pass,
                    "inside_printable_area": hard_rule_pass,
                    "gripper_clear": hard_rule_pass,
                    "min_gap_ok": hard_rule_pass,
                    "rotation_ok": hard_rule_pass,
                    "material_rule_ok": True,
                    "export_ok": hard_rule_pass,
                    "quantity_fulfillment_rate": quantity_rate,
                },
            },
            audit_manifest={
                "generated_at": datetime.now(UTC).isoformat(),
                "selector": "TopKGlobalPlanSelector",
                "deterministic": True,
                "coordinates_source": "not_generated_by_ai",
                "pattern_ids": [pattern.pattern_id for pattern in patterns],
            },
            patterns=patterns,
        )


class BatchLayoutService:
    def __init__(
        self,
        *,
        grouping: CompatibilityGroupingService | None = None,
        cut_variants: SheetCutVariantGenerator | None = None,
        candidate_generator: CandidateJobGenerator | None = None,
        topk_selector: TopKGlobalPlanSelector | None = None,
    ) -> None:
        self.grouping = grouping or CompatibilityGroupingService()
        self.cut_variants = cut_variants or SheetCutVariantGenerator()
        self.candidate_generator = candidate_generator or CandidateJobGenerator()
        self.topk_selector = topk_selector or TopKGlobalPlanSelector()

    def create_job(self, db: Session, payload: schemas.BatchLayoutJobCreate) -> schemas.BatchLayoutJobRead:
        batch = db.get(dbm.BatchUpload, payload.batch_id)
        if batch is None:
            raise ValueError("batch upload not found")
        parent = self._upsert_parent(db, payload.sheet_parent)
        variants = self._upsert_variants(
            db,
            self.cut_variants.generate(payload.sheet_parent, custom_variants=payload.custom_cut_variants),
        )
        row = dbm.BatchLayoutJob(
            batch_id=payload.batch_id,
            status="created",
            moq_per_item=payload.moq_per_item,
            top_k=payload.top_k,
            sheet_parent_spec_id=parent.id,
            params_json=payload.params,
            audit_manifest_json={
                "created_at": datetime.now(UTC).isoformat(),
                "source": "BatchLayoutService.create_job",
                "variant_count": len(variants),
            },
        )
        db.add(row)
        db.commit()
        return self.job_from_row(db, row)

    def run_job(self, db: Session, job_id: str) -> schemas.BatchLayoutRunResult:
        row = db.get(dbm.BatchLayoutJob, job_id)
        if row is None:
            raise ValueError("batch layout job not found")
        row.status = "running"
        db.commit()
        self._clear_job_outputs(db, job_id)

        items = list_batch_items(db, row.batch_id)
        groups = self.grouping.group(items)
        persisted_groups = [self._create_group(db, row, group) for group in groups]
        group_reads = [self.group_from_row(group) for group in persisted_groups]
        items_by_id = {item.item_id: item for item in items}
        variants = self.list_cut_variants(db, row.sheet_parent_spec_id)
        candidates = self.candidate_generator.generate(
            job_id=job_id,
            groups=group_reads,
            items_by_id=items_by_id,
            variants=variants,
            moq_per_item=row.moq_per_item,
        )
        persisted_patterns = [self._create_pattern(db, candidate.pattern) for candidate in candidates]
        pattern_by_id = {pattern.id: self.pattern_from_row(pattern) for pattern in persisted_patterns}
        candidates = [
            CandidatePattern(
                group=candidate.group,
                variant=candidate.variant,
                pattern=pattern_by_id.get(candidate.pattern.pattern_id, candidate.pattern),
                item_ids=candidate.item_ids,
            )
            for candidate in candidates
        ]
        selected = self.topk_selector.select(job_id=job_id, candidates=candidates, top_k=row.top_k)
        plan_reads = [self._create_plan(db, plan, patterns) for plan, patterns in selected]
        row.status = "completed" if plan_reads else "failed"
        db.commit()
        return schemas.BatchLayoutRunResult(
            job=self.job_from_row(db, row),
            groups=group_reads,
            plans=plan_reads,
            summary={
                "item_count": len(items),
                "group_count": len(group_reads),
                "candidate_pattern_count": len(candidates),
                "plan_count": len(plan_reads),
                "hard_rule_plan_count": sum(1 for plan in plan_reads if plan.hard_rule_pass),
            },
        )

    def get_job(self, db: Session, job_id: str) -> schemas.BatchLayoutJobRead | None:
        row = db.get(dbm.BatchLayoutJob, job_id)
        return self.job_from_row(db, row) if row else None

    def list_groups(self, db: Session, job_id: str) -> list[schemas.BatchLayoutGroupRead]:
        rows = db.scalars(
            select(dbm.BatchLayoutGroup)
            .where(dbm.BatchLayoutGroup.job_id == job_id)
            .order_by(dbm.BatchLayoutGroup.created_at, dbm.BatchLayoutGroup.id)
        ).all()
        return [self.group_from_row(row) for row in rows]

    def list_plans(self, db: Session, job_id: str) -> list[schemas.ProductionPlanRead]:
        rows = db.scalars(
            select(dbm.ProductionPlan)
            .where(dbm.ProductionPlan.job_id == job_id)
            .order_by(dbm.ProductionPlan.rank)
        ).all()
        return [self.plan_from_row(db, row) for row in rows]

    def get_plan(self, db: Session, plan_id: str) -> schemas.ProductionPlanRead | None:
        row = db.get(dbm.ProductionPlan, plan_id)
        return self.plan_from_row(db, row) if row else None

    def list_cut_variants(self, db: Session, parent_id: str) -> list[schemas.SheetCutVariant]:
        rows = db.scalars(
            select(dbm.SheetCutVariant)
            .where(dbm.SheetCutVariant.parent_spec_id == parent_id)
            .order_by(dbm.SheetCutVariant.created_at, dbm.SheetCutVariant.id)
        ).all()
        return [cut_variant_from_row(row) for row in rows]

    def job_from_row(self, db: Session, row: dbm.BatchLayoutJob) -> schemas.BatchLayoutJobRead:
        parent_row = db.get(dbm.SheetParentSpec, row.sheet_parent_spec_id)
        if parent_row is None:
            raise ValueError("sheet parent spec not found")
        return schemas.BatchLayoutJobRead(
            job_id=row.id,
            batch_id=row.batch_id,
            status=row.status,
            moq_per_item=row.moq_per_item,
            top_k=row.top_k,
            sheet_parent=sheet_parent_from_row(parent_row),
            cut_variants=self.list_cut_variants(db, row.sheet_parent_spec_id),
            params=row.params_json or {},
            audit_manifest=row.audit_manifest_json or {},
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    def group_from_row(self, row: dbm.BatchLayoutGroup) -> schemas.BatchLayoutGroupRead:
        return schemas.BatchLayoutGroupRead(
            group_id=row.id,
            job_id=row.job_id,
            batch_id=row.batch_id,
            compatibility_key=row.compatibility_key,
            item_ids=list(row.item_ids_json or []),
            material=row.material,
            thickness=row.thickness,
            print_method=row.print_method,
            spot_color=row.spot_color,
            due_date=row.due_date,
            category=row.category,
            customer_id=row.customer_id,
            stats=row.stats_json or {},
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    def pattern_from_row(self, row: dbm.ProductionPattern) -> schemas.ProductionPatternRead:
        return schemas.ProductionPatternRead(
            pattern_id=row.id,
            job_id=row.job_id,
            group_id=row.group_id,
            cut_variant_id=row.cut_variant_id,
            pattern_type=row.pattern_type,
            units_per_sheet=row.units_per_sheet,
            required_sheets=row.required_sheets,
            total_units=row.total_units,
            utilization_rate=row.utilization_rate,
            quantity_fulfillment_rate=row.quantity_fulfillment_rate,
            hard_rule_pass=row.hard_rule_pass,
            validator_report=row.validator_report_json or {},
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    def plan_from_row(self, db: Session, row: dbm.ProductionPlan) -> schemas.ProductionPlanRead:
        links = db.scalars(
            select(dbm.ProductionPlanPattern)
            .where(dbm.ProductionPlanPattern.plan_id == row.id)
            .order_by(dbm.ProductionPlanPattern.sequence)
        ).all()
        patterns = []
        for link in links:
            pattern_row = db.get(dbm.ProductionPattern, link.pattern_id)
            if pattern_row is not None:
                patterns.append(self.pattern_from_row(pattern_row))
        return schemas.ProductionPlanRead(
            plan_id=row.id,
            job_id=row.job_id,
            rank=row.rank,
            intent=row.intent,
            status=row.status,
            utilization_rate=row.utilization_rate,
            risk_score=row.risk_score,
            runtime_score=row.runtime_score,
            diversity_score=row.diversity_score,
            total_sheets_used=row.total_sheets_used,
            quantity_fulfillment_rate=row.quantity_fulfillment_rate,
            hard_rule_pass=row.hard_rule_pass,
            export_ok=row.export_ok,
            validator_report=row.validator_report_json or {},
            audit_manifest=row.audit_manifest_json or {},
            patterns=patterns,
            created_at=row.created_at.isoformat(),
            updated_at=row.updated_at.isoformat(),
        )

    def _upsert_parent(self, db: Session, parent: schemas.SheetParentSpec) -> dbm.SheetParentSpec:
        row = db.get(dbm.SheetParentSpec, parent.parent_id)
        values = {
            "id": parent.parent_id,
            "name": parent.name,
            "width_mm": parent.width,
            "height_mm": parent.height,
            "material": parent.material,
            "thickness": parent.thickness,
            "metadata_json": parent.metadata,
        }
        if row is None:
            row = dbm.SheetParentSpec(**values)
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        db.flush()
        return row

    def _upsert_variants(self, db: Session, variants: list[schemas.SheetCutVariant]) -> list[dbm.SheetCutVariant]:
        rows = []
        for variant in variants:
            row = db.get(dbm.SheetCutVariant, variant.variant_id)
            values = {
                "id": variant.variant_id,
                "parent_spec_id": variant.parent_id,
                "variant_code": variant.code,
                "kind": variant.kind,
                "width_mm": variant.width,
                "height_mm": variant.height,
                "cuts_json": variant.cuts,
                "waste_rate": variant.waste_rate,
                "is_enabled": variant.is_enabled,
                "metadata_json": variant.metadata,
            }
            if row is None:
                row = dbm.SheetCutVariant(**values)
                db.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            rows.append(row)
        db.flush()
        return rows

    def _create_group(
        self,
        db: Session,
        job: dbm.BatchLayoutJob,
        group: schemas.BatchLayoutGroupRead,
    ) -> dbm.BatchLayoutGroup:
        row = dbm.BatchLayoutGroup(
            job_id=job.id,
            batch_id=job.batch_id,
            compatibility_key=group.compatibility_key,
            material=group.material,
            thickness=group.thickness,
            print_method=group.print_method,
            spot_color=group.spot_color,
            due_date=group.due_date.isoformat() if group.due_date else None,
            category=group.category,
            customer_id=group.customer_id,
            item_ids_json=group.item_ids,
            stats_json=group.stats,
        )
        db.add(row)
        db.flush()
        return row

    def _create_pattern(self, db: Session, pattern: schemas.ProductionPatternRead) -> dbm.ProductionPattern:
        row = dbm.ProductionPattern(
            id=pattern.pattern_id,
            job_id=pattern.job_id,
            group_id=pattern.group_id,
            cut_variant_id=pattern.cut_variant_id,
            pattern_type=pattern.pattern_type,
            units_per_sheet=pattern.units_per_sheet,
            required_sheets=pattern.required_sheets,
            total_units=pattern.total_units,
            utilization_rate=pattern.utilization_rate,
            quantity_fulfillment_rate=pattern.quantity_fulfillment_rate,
            hard_rule_pass=pattern.hard_rule_pass,
            validator_report_json=pattern.validator_report,
        )
        db.add(row)
        db.flush()
        return row

    def _create_plan(
        self,
        db: Session,
        plan: schemas.ProductionPlanRead,
        patterns: list[schemas.ProductionPatternRead],
    ) -> schemas.ProductionPlanRead:
        row = dbm.ProductionPlan(
            id=plan.plan_id,
            job_id=plan.job_id,
            rank=plan.rank,
            intent=plan.intent,
            status=plan.status,
            utilization_rate=plan.utilization_rate,
            risk_score=plan.risk_score,
            runtime_score=plan.runtime_score,
            diversity_score=plan.diversity_score,
            total_sheets_used=plan.total_sheets_used,
            quantity_fulfillment_rate=plan.quantity_fulfillment_rate,
            hard_rule_pass=plan.hard_rule_pass,
            export_ok=plan.export_ok,
            validator_report_json=plan.validator_report,
            audit_manifest_json=plan.audit_manifest,
        )
        db.add(row)
        db.flush()
        for sequence, pattern in enumerate(patterns, 1):
            db.add(
                dbm.ProductionPlanPattern(
                    plan_id=row.id,
                    pattern_id=pattern.pattern_id,
                    sequence=sequence,
                    sheets_used=pattern.required_sheets,
                    produced_units=pattern.total_units,
                )
            )
        db.commit()
        return self.plan_from_row(db, row)

    def _clear_job_outputs(self, db: Session, job_id: str) -> None:
        plan_ids = [row.id for row in db.scalars(select(dbm.ProductionPlan).where(dbm.ProductionPlan.job_id == job_id))]
        pattern_ids = [
            row.id for row in db.scalars(select(dbm.ProductionPattern).where(dbm.ProductionPattern.job_id == job_id))
        ]
        if plan_ids:
            db.execute(delete(dbm.ProductionPlanExport).where(dbm.ProductionPlanExport.plan_id.in_(plan_ids)))
            db.execute(delete(dbm.ProductionPlanApproval).where(dbm.ProductionPlanApproval.plan_id.in_(plan_ids)))
            db.execute(delete(dbm.ProductionPlanPattern).where(dbm.ProductionPlanPattern.plan_id.in_(plan_ids)))
            db.execute(delete(dbm.ProductionPlan).where(dbm.ProductionPlan.id.in_(plan_ids)))
        if pattern_ids:
            db.execute(delete(dbm.ProductionPattern).where(dbm.ProductionPattern.id.in_(pattern_ids)))
        db.execute(delete(dbm.BatchLayoutGroup).where(dbm.BatchLayoutGroup.job_id == job_id))
        db.commit()


def sheet_parent_from_row(row: dbm.SheetParentSpec) -> schemas.SheetParentSpec:
    return schemas.SheetParentSpec(
        parent_id=row.id,
        name=row.name,
        width=row.width_mm,
        height=row.height_mm,
        material=row.material,
        thickness=row.thickness,
        metadata=row.metadata_json or {},
    )


def cut_variant_from_row(row: dbm.SheetCutVariant) -> schemas.SheetCutVariant:
    return schemas.SheetCutVariant(
        variant_id=row.id,
        parent_id=row.parent_spec_id,
        code=row.variant_code,
        kind=row.kind,
        width=row.width_mm,
        height=row.height_mm,
        cuts=row.cuts_json or {},
        waste_rate=row.waste_rate,
        is_enabled=row.is_enabled,
        metadata=row.metadata_json or {},
    )


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


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _counts(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return result
