from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import models as dbm
from app.domain import schemas
from app.services import repository
from app.services.batch_artworks import list_batch_items
from app.services.batch_patterns import (
    CandidateJobGenerator,
    CandidatePattern,
    TopKGlobalPlanSelector,
)
from app.services.geometry import rectangle_asset
from app.services.solvers.multi_orchestrator import MultiSolverOrchestrator


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


class BatchLayoutService:
    def __init__(
        self,
        *,
        grouping: CompatibilityGroupingService | None = None,
        cut_variants: SheetCutVariantGenerator | None = None,
        candidate_generator: CandidateJobGenerator | None = None,
        topk_selector: TopKGlobalPlanSelector | None = None,
        multi_solver: MultiSolverOrchestrator | None = None,
    ) -> None:
        self.grouping = grouping or CompatibilityGroupingService()
        self.cut_variants = cut_variants or SheetCutVariantGenerator()
        self.candidate_generator = candidate_generator or CandidateJobGenerator()
        self.topk_selector = topk_selector or TopKGlobalPlanSelector()
        self.multi_solver = multi_solver or MultiSolverOrchestrator()

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
        candidate_pool_evidence = self._build_solver_candidate_evidence(db, row, items, variants)
        selected = self.topk_selector.select(
            job_id=job_id,
            candidates=candidates,
            top_k=row.top_k,
            candidate_pool_evidence=candidate_pool_evidence,
        )
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
                "multi_solver_candidate_count": candidate_pool_evidence.get("candidate_count", 0),
                "multi_solver_legal_candidate_count": candidate_pool_evidence.get("legal_candidate_count", 0),
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

    def get_pattern(self, db: Session, pattern_id: str) -> schemas.ProductionPatternRead | None:
        row = db.get(dbm.ProductionPattern, pattern_id)
        return self.pattern_from_row(row) if row else None

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
            placement_json=row.placement_json or {},
            placement_svg=row.placement_svg or "",
            placement_checksum=row.placement_checksum,
            placement_solver=row.placement_solver_json or {},
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
            placement_json=pattern.placement_json,
            placement_svg=pattern.placement_svg,
            placement_checksum=pattern.placement_checksum,
            placement_solver_json=pattern.placement_solver,
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

    def _build_solver_candidate_evidence(
        self,
        db: Session,
        job: dbm.BatchLayoutJob,
        items: list[schemas.BatchArtworkItemRead],
        variants: list[schemas.SheetCutVariant],
    ) -> dict[str, Any]:
        params = job.params_json or {}
        if params.get("multi_solver_evidence_enabled", True) is False:
            return {
                "orchestrator": "MultiSolverOrchestrator",
                "candidate_count": 0,
                "legal_candidate_count": 0,
                "skipped_reason": "disabled_by_job_params",
            }
        parent = db.get(dbm.SheetParentSpec, job.sheet_parent_spec_id)
        if parent is None or not variants:
            return {
                "orchestrator": "MultiSolverOrchestrator",
                "candidate_count": 0,
                "legal_candidate_count": 0,
                "skipped_reason": "missing_sheet_context",
            }
        limit = int(params.get("solver_evidence_item_limit", 12))
        solver_items = self._solver_items_for_evidence(db, items, limit=max(1, limit))
        if not solver_items:
            return {
                "orchestrator": "MultiSolverOrchestrator",
                "candidate_count": 0,
                "legal_candidate_count": 0,
                "skipped_reason": "no_parseable_geometry",
            }
        variant = next((candidate for candidate in variants if candidate.kind == "parent"), variants[0])
        solver_job = schemas.NestingJob(
            job_id=f"{job.id}_candidate_pool",
            sheet=schemas.SheetSpec(
                sheet_id=variant.variant_id,
                name=variant.code,
                width=variant.width,
                height=variant.height,
                margin_top=10,
                margin_right=10,
                margin_bottom=10,
                margin_left=10,
                gripper_mm=20,
                material=parent.material,
                thickness=parent.thickness,
            ),
            candidate_items=solver_items,
            top_k=3,
            solver_config=schemas.SolverConfig(time_limit_sec=int(params.get("solver_evidence_time_limit_sec", 1))),
        )
        solutions = self.multi_solver.solve_candidate_pool(
            solver_job,
            seeds=[int(seed) for seed in params.get("solver_evidence_seeds", [0, 17])],
            time_limits_sec=[int(params.get("solver_evidence_time_limit_sec", 1))],
            rotation_policies=params.get("solver_evidence_rotation_policies", ["as_declared", "prefer_90", "zero_only"]),
        )
        report = self.multi_solver.candidate_pool_report(solutions)
        report.update(
            {
                "job_id": solver_job.job_id,
                "item_count": len(solver_items),
                "sheet_variant_id": variant.variant_id,
                "source": "batch_layout_run",
            }
        )
        return report

    def _solver_items_for_evidence(
        self,
        db: Session,
        items: list[schemas.BatchArtworkItemRead],
        *,
        limit: int,
    ) -> list[schemas.NestingItem]:
        solver_items: list[schemas.NestingItem] = []
        for item in items:
            if item.classification == "OVERSIZE" or item.feature is None:
                continue
            polygons = repository.get_polygons(db, item.artwork_id or "") if item.artwork_id else []
            polygon = polygons[0] if polygons else None
            if polygon is None and item.feature.bbox is not None:
                bbox = item.feature.bbox
                polygon = rectangle_asset(
                    f"{item.item_id}_feature_bbox",
                    bbox.width,
                    bbox.height,
                    {"source": "feature_bbox_fallback"},
                )
            if polygon is None:
                continue
            solver_items.append(
                schemas.NestingItem(
                    item_id=item.item_id,
                    order_id=item.order_id or item.item_id,
                    polygon=polygon,
                    quantity=1,
                    priority_score=float((item.metadata or {}).get("priority_score", 0)),
                    allowed_rotations=list((item.metadata or {}).get("allowed_rotations", [0, 90, 180, 270])),
                    min_gap_mm=float((item.metadata or {}).get("min_gap_mm", 2)),
                    bleed_mm=float((item.metadata or {}).get("bleed_mm", 1)),
                )
            )
            if len(solver_items) >= limit:
                break
        return solver_items


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


def _counts(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value)
        result[key] = result.get(key, 0) + 1
    return result
