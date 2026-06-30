from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import models as dbm
from app.domain import schemas
from app.services import repository
from app.services.artworks import (
    checksum_bytes,
    new_artwork_id,
    parse_vector_polygons,
    preflight_artwork,
    save_artwork_bytes,
)
from app.services.batch_artworks import BatchArtworkService
from app.services.batch_layout import BatchLayoutService
from app.services.batch_planning import plan_batch
from app.services.benchmark_importers import load_public_dataset_as_benchmark_case


@dataclass(frozen=True)
class SyntheticArtworkFixture:
    filename: str
    content_type: str
    content: str
    quantity: int
    metadata: dict[str, Any]


class EnterpriseBenchmarkRunner:
    def __init__(
        self,
        *,
        batch_artworks: BatchArtworkService | None = None,
        batch_layout: BatchLayoutService | None = None,
    ) -> None:
        self.batch_artworks = batch_artworks or BatchArtworkService()
        self.batch_layout = batch_layout or BatchLayoutService()

    def run_batch_pipeline(
        self,
        db: Session,
        *,
        file_count: int = 1500,
        include_pdf_fallback: bool = False,
        moq_per_item: int = 1000,
        top_k: int = 3,
        benchmark_type: str = "batch_1500",
    ) -> schemas.BatchBenchmarkRunRead:
        if file_count < 1:
            raise ValueError("file_count must be >= 1")
        fixtures = list(_fixtures(file_count, include_pdf_fallback=include_pdf_fallback, moq_per_item=moq_per_item))
        stage_runtime_ms: dict[str, int] = {}

        started = time.perf_counter()
        batch = self.batch_artworks.create_batch(
            db,
            source_name=f"enterprise_batch_{file_count}",
            metadata={
                "benchmark": benchmark_type,
                "fixture_count": len(fixtures),
                "include_pdf_fallback": include_pdf_fallback,
            },
        )
        self._persist_fixtures_bulk(db, batch.batch_id, fixtures)
        stage_runtime_ms["upload_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        preflight_summary = self.batch_artworks.preflight_batch(db, batch.batch_id)
        stage_runtime_ms["preflight_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        parse_summary = self._parse_generated_batch_bulk(db, batch.batch_id)
        stage_runtime_ms["parse_ms"] = _elapsed_ms(started)

        started = time.perf_counter()
        job = self.batch_layout.create_job(
            db,
            schemas.BatchLayoutJobCreate(
                batch_id=batch.batch_id,
                sheet_parent=schemas.SheetParentSpec(parent_id=f"PARENT_787_1092_{batch.batch_id[:8]}"),
                moq_per_item=moq_per_item,
                top_k=top_k,
                params={
                    "source": "EnterpriseBenchmarkRunner.run_batch_pipeline",
                    "solver_evidence_item_limit": 12,
                    "solver_evidence_time_limit_sec": 1,
                },
            ),
        )
        layout_result = self.batch_layout.run_job(db, job.job_id)
        stage_runtime_ms["layout_ms"] = _elapsed_ms(started)

        parsed_count = parse_summary.status_counts.get("parsed", 0)
        direct_parseable_count = preflight_summary.batch.item_count - preflight_summary.batch.conversion_required_count
        failed_count = parse_summary.status_counts.get("failed", 0)
        plans = layout_result.plans
        legal_plans = [
            plan
            for plan in plans
            if plan.hard_rule_pass
            and plan.quantity_fulfillment_rate >= 1.0
            and plan.validator_report.get("veto", {}).get("export_ok") is True
        ]
        quantity_rate = min((plan.quantity_fulfillment_rate for plan in plans), default=0.0)
        hard_rule_rate = _rate(plan.hard_rule_pass for plan in plans)
        topk_rate = len(legal_plans) / len(plans) if plans else 0.0
        avg_score = _case_score(hard_rule_rate, quantity_rate, topk_rate)
        total_runtime_ms = sum(stage_runtime_ms.values())
        metrics = {
            "pipeline": "batch_artwork_to_batch_layout",
            "synthetic": True,
            "fixture_source": "generated_svg_dxf_pdf_placeholders",
            "batch_id": batch.batch_id,
            "job_id": job.job_id,
            "file_count": file_count,
            "sheet_parent": {
                "width": 787,
                "height": 1092,
                "material": "white_card",
                "thickness": "350gsm",
            },
            "moq_per_item": moq_per_item,
            "top_k": top_k,
            "format_counts": parse_summary.format_counts,
            "status_counts": parse_summary.status_counts,
            "class_counts": parse_summary.class_counts,
            "direct_parseable_count": direct_parseable_count,
            "direct_parse_success_count": parsed_count,
            "direct_parse_failure_count": failed_count,
            "direct_parse_success_rate": parsed_count / direct_parseable_count if direct_parseable_count else 0,
            "conversion_required_count": parse_summary.batch.conversion_required_count,
            "manual_review_count": parse_summary.batch.manual_review_count,
            "group_count": layout_result.summary.get("group_count", 0),
            "candidate_pattern_count": layout_result.summary.get("candidate_pattern_count", 0),
            "plan_count": len(plans),
            "legal_plan_count": len(legal_plans),
            "multi_solver_candidate_count": layout_result.summary.get("multi_solver_candidate_count", 0),
            "multi_solver_legal_candidate_count": layout_result.summary.get("multi_solver_legal_candidate_count", 0),
            "stage_runtime_ms": stage_runtime_ms,
            "runtime_ms": total_runtime_ms,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        return create_batch_benchmark_run(
            db,
            benchmark_type=benchmark_type,
            status="passed" if hard_rule_rate == 1.0 and quantity_rate >= 1.0 and topk_rate >= 1.0 else "failed",
            file_count=file_count,
            p95_runtime_ms=max(stage_runtime_ms.values()) if stage_runtime_ms else None,
            hard_rule_pass_rate=hard_rule_rate,
            quantity_fulfillment_rate=quantity_rate,
            topk_legal_rate=topk_rate,
            avg_case_score=avg_score,
            metrics=metrics,
            job_id=job.job_id,
        )

    def _persist_fixtures_bulk(
        self,
        db: Session,
        batch_id: str,
        fixtures: list[SyntheticArtworkFixture],
        *,
        chunk_size: int = 1000,
    ) -> None:
        batch_row = db.get(dbm.BatchUpload, batch_id)
        persisted = 0
        for fixture in fixtures:
            artwork_id = new_artwork_id()
            data = fixture.content.encode("utf-8")
            checksum = checksum_bytes(data)
            storage_key = save_artwork_bytes(artwork_id, fixture.filename, data)
            report = preflight_artwork(fixture.filename, fixture.content, fixture.content_type)
            db.add(
                dbm.ArtworkFile(
                    id=artwork_id,
                    filename=fixture.filename,
                    content_type=fixture.content_type,
                    checksum=checksum,
                    source_format=report.source_format,
                    storage_key=storage_key,
                    status="uploaded",
                )
            )
            db.add(
                dbm.FilePreflightReport(
                    artwork_file_id=artwork_id,
                    can_parse_directly=report.can_parse_directly,
                    requires_conversion=report.requires_conversion,
                    requires_manual_review=report.requires_manual_review,
                    report=report.model_dump(mode="json"),
                )
            )
            db.add(
                dbm.BatchArtworkItem(
                    batch_id=batch_id,
                    artwork_file_id=artwork_id,
                    filename=fixture.filename,
                    content_type=fixture.content_type,
                    checksum=checksum,
                    source_format=report.source_format,
                    status="uploaded",
                    quantity=max(1, fixture.quantity),
                    preflight_report_json=report.model_dump(mode="json"),
                    metadata_json=fixture.metadata,
                )
            )
            persisted += 1
            if persisted % chunk_size == 0:
                if batch_row is not None:
                    batch_row.item_count = persisted
                    batch_row.uploaded_count = persisted
                db.commit()
                batch_row = db.get(dbm.BatchUpload, batch_id)
        if batch_row is not None:
            batch_row.item_count = persisted
            batch_row.uploaded_count = persisted
        db.commit()
        self.batch_artworks.refresh_batch_counts(db, batch_id)

    def _parse_generated_batch_bulk(
        self,
        db: Session,
        batch_id: str,
        *,
        chunk_size: int = 1000,
    ) -> schemas.BatchArtworkSummary:
        parent = schemas.SheetParentSpec()
        rows = db.scalars(
            select(dbm.BatchArtworkItem)
            .where(dbm.BatchArtworkItem.batch_id == batch_id)
            .order_by(dbm.BatchArtworkItem.created_at, dbm.BatchArtworkItem.id)
        ).all()
        parsed_artwork_ids: list[str] = []
        parsed_count = 0
        batch_row = db.get(dbm.BatchUpload, batch_id)
        for index, row in enumerate(rows, start=1):
            report = schemas.PreflightReport.model_validate(row.preflight_report_json)
            if row.source_format in {"svg", "dxf"}:
                try:
                    content = repository.load_artwork_content(db, row.artwork_file_id or "")
                    if content is None:
                        raise ValueError("original artwork content is missing from storage")
                    polygons = parse_vector_polygons(content, row.source_format, row.artwork_file_id or row.id)
                    for polygon in polygons:
                        bbox = polygon.bbox
                        db.add(
                            dbm.PolygonAsset(
                                id=f"{row.artwork_file_id}:{polygon.shape_id}",
                                artwork_file_id=row.artwork_file_id,
                                unit=polygon.unit,
                                polygon_json=polygon.model_dump(mode="json"),
                                area=polygon.area or 0,
                                bbox_width=bbox.width if bbox else 0,
                                bbox_height=bbox.height if bbox else 0,
                            )
                        )
                    feature = self.batch_artworks.extractor.extract(polygons, preflight_report=report)
                    row.feature_json = feature.model_dump(mode="json")
                    row.classification = self.batch_artworks.classifier.classify(
                        feature,
                        parent=parent,
                        source_format=row.source_format,
                    )
                    row.status = "parsed"
                    row.parse_error = None
                    parsed_count += 1
                    if row.artwork_file_id:
                        parsed_artwork_ids.append(row.artwork_file_id)
                except Exception as exc:
                    feature = self.batch_artworks.extractor.extract([], preflight_report=report)
                    row.feature_json = feature.model_dump(mode="json")
                    row.classification = self.batch_artworks.classifier.classify(
                        feature,
                        parent=parent,
                        source_format=row.source_format,
                    )
                    row.status = "failed"
                    row.parse_error = str(exc)
            else:
                feature = self.batch_artworks.extractor.extract([], preflight_report=report)
                row.feature_json = feature.model_dump(mode="json")
                row.classification = self.batch_artworks.classifier.classify(
                    feature,
                    parent=parent,
                    source_format=row.source_format,
                )
                row.status = "manual_review" if report.requires_manual_review else "conversion_required"
                row.parse_error = "Direct geometry parsing supports SVG/DXF; conversion is required before coordinates."

            if index % chunk_size == 0:
                self._mark_artworks_parsed(db, parsed_artwork_ids)
                parsed_artwork_ids = []
                if batch_row is not None:
                    batch_row.parsed_count = parsed_count
                    batch_row.preflighted_count = max(0, len(rows) - parsed_count)
                db.commit()
                batch_row = db.get(dbm.BatchUpload, batch_id)

        self._mark_artworks_parsed(db, parsed_artwork_ids)
        if batch_row is not None:
            batch_row.parsed_count = parsed_count
            batch_row.preflighted_count = max(0, len(rows) - parsed_count)
        db.commit()
        self.batch_artworks.refresh_batch_counts(db, batch_id)
        return self.batch_artworks.summary(db, batch_id)

    def _mark_artworks_parsed(self, db: Session, artwork_ids: list[str]) -> None:
        if not artwork_ids:
            return
        db.execute(update(dbm.ArtworkFile).where(dbm.ArtworkFile.id.in_(artwork_ids)).values(status="parsed"))

    def _persist_fixture(self, db: Session, batch_id: str, fixture: SyntheticArtworkFixture) -> None:
        artwork_id = new_artwork_id()
        data = fixture.content.encode("utf-8")
        checksum = checksum_bytes(data)
        storage_key = save_artwork_bytes(artwork_id, fixture.filename, data)
        report = preflight_artwork(fixture.filename, fixture.content, fixture.content_type)
        repository.create_artwork(
            db,
            artwork_id=artwork_id,
            filename=fixture.filename,
            content_type=fixture.content_type,
            checksum=checksum,
            source_format=report.source_format,
            storage_key=storage_key,
            preflight_report=report,
        )
        self.batch_artworks.create_item(
            db,
            batch_id=batch_id,
            artwork_id=artwork_id,
            filename=fixture.filename,
            content_type=fixture.content_type,
            checksum=checksum,
            source_format=report.source_format,
            preflight_report=report,
            quantity=fixture.quantity,
            metadata=fixture.metadata,
        )

    def run_or_dataset_case(
        self,
        db: Session,
        *,
        path: Path,
        case_id: str,
        name: str | None = None,
        sheet_width: float | None = None,
        sheet_height: float | None = None,
        material: str = "dataset_material",
        thickness: str = "dataset_thickness",
        planning_mode: schemas.PlanningMode = "pattern",
    ) -> schemas.BatchBenchmarkRunRead:
        case = load_public_dataset_as_benchmark_case(
            path,
            case_id=case_id,
            name=name,
            sheet_width=sheet_width,
            sheet_height=sheet_height,
            material=material,
            thickness=thickness,
            planning_mode=planning_mode,
        )
        saved = repository.upsert_benchmark_case(db, case, source="or_dataset")
        job = schemas.NestingJob(
            job_id=f"{saved.case_id}_enterprise_run",
            sheet=saved.sheet,
            candidate_items=saved.items,
            constraints={"source": "or_dataset", "max_batch_candidates_per_sheet": 600},
            top_k=1,
        )
        started = time.perf_counter()
        result = plan_batch(job, saved.planning_mode)
        runtime_ms = _elapsed_ms(started)
        sheet_787x1092 = saved.sheet.width == 787 and saved.sheet.height == 1092
        moq_1000 = all(item.quantity >= 1000 for item in saved.items)
        metrics = {
            "pipeline": "or_dataset_to_pattern_planner",
            "source": "or_dataset",
            "dataset_path": str(path),
            "case_id": saved.case_id,
            "planning_mode": saved.planning_mode,
            "item_count": len(saved.items),
            "sheet_parent": {
                "width": saved.sheet.width,
                "height": saved.sheet.height,
                "material": saved.sheet.material,
                "thickness": saved.sheet.thickness,
            },
            "sheet_787x1092": sheet_787x1092,
            "moq_1000": moq_1000,
            "requested_units": result.requested_units,
            "produced_units": result.produced_units,
            "shortage_units": result.shortage_units,
            "overproduction_units": result.overproduction_units,
            "units_per_sheet": result.units_per_sheet,
            "sheets_used": result.sheets_used,
            "utilization_rate": result.utilization_rate,
            "runtime_ms": runtime_ms,
            "planner_runtime_ms": result.runtime_ms,
            "hard_rule_pass": result.hard_rule_pass,
            "export_ok": result.export_ok,
            "quantity_fulfillment_rate": result.quantity_fulfillment_rate,
            "case_score": result.case_score,
        }
        return create_batch_benchmark_run(
            db,
            benchmark_type="or_dataset",
            status="passed" if result.hard_rule_pass and result.quantity_fulfillment_rate >= 1.0 else "failed",
            file_count=len(saved.items),
            p95_runtime_ms=result.runtime_ms,
            hard_rule_pass_rate=1.0 if result.hard_rule_pass else 0.0,
            quantity_fulfillment_rate=result.quantity_fulfillment_rate,
            topk_legal_rate=1.0 if result.hard_rule_pass else 0.0,
            avg_case_score=result.case_score,
            metrics=metrics,
            job_id=job.job_id,
        )


def create_batch_benchmark_run(
    db: Session,
    *,
    benchmark_type: str,
    status: str,
    file_count: int,
    p95_runtime_ms: int | None,
    hard_rule_pass_rate: float,
    quantity_fulfillment_rate: float,
    topk_legal_rate: float,
    avg_case_score: float,
    metrics: dict[str, Any],
    job_id: str | None = None,
) -> schemas.BatchBenchmarkRunRead:
    row = dbm.BatchBenchmarkRun(
        job_id=job_id,
        benchmark_type=benchmark_type,
        status=status,
        file_count=file_count,
        p95_runtime_ms=p95_runtime_ms,
        hard_rule_pass_rate=round(hard_rule_pass_rate, 4),
        quantity_fulfillment_rate=round(quantity_fulfillment_rate, 4),
        topk_legal_rate=round(topk_legal_rate, 4),
        avg_case_score=round(avg_case_score, 4),
        metrics_json=metrics,
    )
    db.add(row)
    db.commit()
    return batch_benchmark_run_from_row(row)


def batch_benchmark_run_from_row(row: dbm.BatchBenchmarkRun) -> schemas.BatchBenchmarkRunRead:
    return schemas.BatchBenchmarkRunRead(
        run_id=row.id,
        job_id=row.job_id,
        benchmark_type=row.benchmark_type,
        status=row.status,
        file_count=row.file_count,
        p95_runtime_ms=row.p95_runtime_ms,
        peak_rss_mb=row.peak_rss_mb,
        hard_rule_pass_rate=row.hard_rule_pass_rate,
        quantity_fulfillment_rate=row.quantity_fulfillment_rate,
        topk_legal_rate=row.topk_legal_rate,
        avg_case_score=row.avg_case_score,
        metrics=row.metrics_json or {},
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _fixtures(
    file_count: int,
    *,
    include_pdf_fallback: bool,
    moq_per_item: int,
) -> list[SyntheticArtworkFixture]:
    fixtures: list[SyntheticArtworkFixture] = []
    fallback_every = 25 if include_pdf_fallback else 0
    for index in range(file_count):
        quantity = moq_per_item + (index % 3) * 100
        if fallback_every and index % fallback_every == fallback_every - 1:
            fixtures.append(
                SyntheticArtworkFixture(
                    filename=f"fallback_{index:04d}.pdf",
                    content_type="application/pdf",
                    content="%PDF-1.4\n% benchmark placeholder requiring vector conversion\n",
                    quantity=quantity,
                    metadata={"fixture_kind": "pdf_fallback", "min_gap_mm": 2, "bleed_mm": 1},
                )
            )
            continue
        width = 36 + (index % 11) * 3
        height = 28 + (index % 7) * 4
        if index % 5 == 0:
            fixtures.append(
                SyntheticArtworkFixture(
                    filename=f"shape_{index:04d}.dxf",
                    content_type="application/dxf",
                    content=_dxf_rectangle(width, height),
                    quantity=quantity,
                    metadata={"fixture_kind": "dxf_rectangle", "min_gap_mm": 2, "bleed_mm": 1},
                )
            )
        else:
            fixtures.append(
                SyntheticArtworkFixture(
                    filename=f"shape_{index:04d}.svg",
                    content_type="image/svg+xml",
                    content=(
                        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
                        f'<rect id="cut_{index}" x="0" y="0" width="{width}" height="{height}"/>'
                        "</svg>"
                    ),
                    quantity=quantity,
                    metadata={"fixture_kind": "svg_rectangle", "min_gap_mm": 2, "bleed_mm": 1},
                )
            )
    return fixtures


def _dxf_rectangle(width: float, height: float) -> str:
    points = [(0, 0), (width, 0), (width, height), (0, height)]
    parts = ["0", "SECTION", "2", "ENTITIES", "0", "LWPOLYLINE", "90", str(len(points)), "70", "1"]
    for x, y in points:
        parts.extend(["10", str(x), "20", str(y)])
    parts.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(parts)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _rate(values) -> float:
    collected = list(values)
    if not collected:
        return 0.0
    return sum(1 for value in collected if value) / len(collected)


def _case_score(hard_rule_rate: float, quantity_rate: float, topk_rate: float) -> float:
    if hard_rule_rate < 1.0 or quantity_rate < 1.0:
        return 0.0
    return min(100.0, 35 + 10 * quantity_rate + 20 * topk_rate + 25 * hard_rule_rate + 10)
