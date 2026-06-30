from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as dbm
from app.domain import schemas
from app.services import repository
from app.services.artworks import parse_vector_polygons
from app.services.geometry import calculate_bbox, enrich_polygon, printable_area_polygon


class ArtworkFeatureExtractor:
    def extract(
        self,
        polygons: list[schemas.PolygonAsset],
        *,
        preflight_report: schemas.PreflightReport | None = None,
        sheet: schemas.SheetSpec | None = None,
    ) -> schemas.ArtworkFeature:
        if polygons:
            enriched = [enrich_polygon(polygon) for polygon in polygons]
            bbox = _combined_bbox(enriched)
            area = round(sum(polygon.area or 0 for polygon in enriched), 4)
            bbox_area = bbox.width * bbox.height
            denominator = _printable_area(sheet) if sheet else bbox_area
            warnings = list(preflight_report.warnings if preflight_report else [])
            return schemas.ArtworkFeature(
                bbox=bbox,
                area=area,
                area_ratio=round(area / denominator, 4) if denominator else 0,
                aspect_ratio=round(bbox.width / bbox.height, 4) if bbox.height else 0,
                hole_count=sum(len(polygon.holes) for polygon in enriched),
                concavity=round(max(0.0, 1 - area / bbox_area), 4) if bbox_area else 0,
                parse_confidence=0.85 if warnings else 0.95,
                needs_manual_review=bool(preflight_report and preflight_report.requires_manual_review),
                warnings=warnings,
                metadata={
                    "polygon_count": len(enriched),
                    "feature_source": "direct_polygon",
                    "area_ratio_basis": "sheet" if sheet else "bbox",
                },
            )

        dimensions = preflight_report.dimensions_mm if preflight_report else None
        if dimensions:
            width = float(dimensions.get("width", 0))
            height = float(dimensions.get("height", 0))
            bbox = schemas.BBox(width=width, height=height, min_x=0, min_y=0, max_x=width, max_y=height)
            area = width * height
            denominator = _printable_area(sheet) if sheet else area
            return schemas.ArtworkFeature(
                bbox=bbox,
                area=round(area, 4),
                area_ratio=round(area / denominator, 4) if denominator else 0,
                aspect_ratio=round(width / height, 4) if height else 0,
                parse_confidence=0.35,
                needs_manual_review=True,
                warnings=list(preflight_report.warnings),
                metadata={"feature_source": "preflight_dimensions", "area_ratio_basis": "sheet" if sheet else "bbox"},
            )

        warnings = list(preflight_report.warnings if preflight_report else [])
        return schemas.ArtworkFeature(
            parse_confidence=0.15 if preflight_report else 0,
            needs_manual_review=True,
            warnings=warnings,
            metadata={"feature_source": "preflight_only"},
        )


class ArtworkClassifier:
    def classify(
        self,
        feature: schemas.ArtworkFeature,
        *,
        parent: schemas.SheetParentSpec | None = None,
        source_format: str | None = None,
    ) -> schemas.ArtworkClass:
        if feature.metadata.get("page_count", 1) not in {0, 1, "1"}:
            return "MULTI_PAGE"
        bbox = feature.bbox
        if bbox is None or bbox.width <= 0 or bbox.height <= 0:
            return "OVERSIZE" if source_format in {"pdf", "ai"} else "FILLER"
        parent_width = parent.width if parent else 787
        parent_height = parent.height if parent else 1092
        fits_normal = bbox.width <= parent_width and bbox.height <= parent_height
        fits_rotated = bbox.width <= parent_height and bbox.height <= parent_width
        if not (fits_normal or fits_rotated):
            return "OVERSIZE"
        coverage = max(bbox.width / parent_width, bbox.height / parent_height)
        area_ratio_is_sheet_based = feature.metadata.get("area_ratio_basis") == "sheet"
        if (area_ratio_is_sheet_based and feature.area_ratio >= 0.72) or coverage >= 0.82:
            return "FULL_SHEET"
        if (area_ratio_is_sheet_based and feature.area_ratio >= 0.16) or coverage >= 0.42:
            return "ANCHOR"
        return "FILLER"


class BatchArtworkService:
    def __init__(
        self,
        *,
        extractor: ArtworkFeatureExtractor | None = None,
        classifier: ArtworkClassifier | None = None,
    ) -> None:
        self.extractor = extractor or ArtworkFeatureExtractor()
        self.classifier = classifier or ArtworkClassifier()

    def create_batch(
        self,
        db: Session,
        *,
        source_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> schemas.BatchUploadRead:
        row = dbm.BatchUpload(source_name=source_name, status="uploaded", metadata_json=metadata or {})
        db.add(row)
        db.commit()
        return batch_upload_from_row(row)

    def create_item(
        self,
        db: Session,
        *,
        batch_id: str,
        artwork_id: str,
        filename: str,
        content_type: str | None,
        checksum: str,
        source_format: str,
        preflight_report: schemas.PreflightReport,
        quantity: int = 1000,
        metadata: dict[str, Any] | None = None,
    ) -> schemas.BatchArtworkItemRead:
        _require_batch(db, batch_id)
        row = dbm.BatchArtworkItem(
            batch_id=batch_id,
            artwork_file_id=artwork_id,
            filename=filename,
            content_type=content_type,
            checksum=checksum,
            source_format=source_format,
            status="uploaded",
            quantity=max(1, quantity),
            preflight_report_json=preflight_report.model_dump(mode="json"),
            metadata_json=metadata or {},
        )
        db.add(row)
        db.commit()
        self.refresh_batch_counts(db, batch_id)
        return batch_item_from_row(row)

    def preflight_batch(self, db: Session, batch_id: str) -> schemas.BatchArtworkSummary:
        _require_batch(db, batch_id)
        rows = _batch_item_rows(db, batch_id)
        for row in rows:
            report = _preflight_from_row(row)
            if report.can_parse_directly:
                row.status = "preflighted"
            elif report.requires_manual_review:
                row.status = "manual_review"
            else:
                row.status = "conversion_required"
        db.commit()
        self.refresh_batch_counts(db, batch_id)
        return self.summary(db, batch_id)

    def parse_batch(self, db: Session, batch_id: str) -> schemas.BatchArtworkSummary:
        _require_batch(db, batch_id)
        parent = schemas.SheetParentSpec()
        for row in _batch_item_rows(db, batch_id):
            self.parse_item_row(db, row, parent=parent)
        db.commit()
        self.refresh_batch_counts(db, batch_id)
        return self.summary(db, batch_id)

    def retry_failed_items(
        self,
        db: Session,
        batch_id: str,
        *,
        item_ids: list[str] | None = None,
    ) -> schemas.BatchArtworkSummary:
        _require_batch(db, batch_id)
        selected_ids = set(item_ids or [])
        parent = schemas.SheetParentSpec()
        for row in _batch_item_rows(db, batch_id):
            if row.status != "failed":
                continue
            if selected_ids and row.id not in selected_ids:
                continue
            row.retry_count += 1
            row.parse_error = None
            self.parse_item_row(db, row, parent=parent)
        db.commit()
        self.refresh_batch_counts(db, batch_id)
        return self.summary(db, batch_id)

    def parse_item_row(self, db: Session, row: dbm.BatchArtworkItem, *, parent: schemas.SheetParentSpec) -> None:
        report = _preflight_from_row(row)
        if row.source_format in {"svg", "dxf"}:
            try:
                content = repository.load_artwork_content(db, row.artwork_file_id or "")
                if content is None:
                    raise ValueError("original artwork content is missing from storage")
                polygons = parse_vector_polygons(content, row.source_format, row.artwork_file_id or row.id)
                repository.save_polygons(db, row.artwork_file_id or row.id, polygons)
                feature = self.extractor.extract(polygons, preflight_report=report)
                row.feature_json = feature.model_dump(mode="json")
                row.classification = self.classifier.classify(
                    feature,
                    parent=parent,
                    source_format=row.source_format,
                )
                row.status = "parsed"
                row.parse_error = None
            except Exception as exc:
                feature = self.extractor.extract([], preflight_report=report)
                row.feature_json = feature.model_dump(mode="json")
                row.classification = self.classifier.classify(feature, parent=parent, source_format=row.source_format)
                row.status = "failed"
                row.parse_error = str(exc)
            return

        feature = self.extractor.extract([], preflight_report=report)
        row.feature_json = feature.model_dump(mode="json")
        row.classification = self.classifier.classify(feature, parent=parent, source_format=row.source_format)
        row.status = "manual_review" if report.requires_manual_review else "conversion_required"
        row.parse_error = "Direct geometry parsing supports SVG/DXF; conversion is required before coordinates."

    def summary(self, db: Session, batch_id: str) -> schemas.BatchArtworkSummary:
        batch = _require_batch(db, batch_id)
        items = [batch_item_from_row(row) for row in _batch_item_rows(db, batch_id)]
        return schemas.BatchArtworkSummary(
            batch=batch_upload_from_row(batch),
            items=items,
            class_counts=dict(Counter(item.classification or "UNCLASSIFIED" for item in items)),
            format_counts=dict(Counter(item.source_format for item in items)),
            status_counts=dict(Counter(item.status for item in items)),
        )

    def refresh_batch_counts(self, db: Session, batch_id: str) -> schemas.BatchUploadRead:
        batch = _require_batch(db, batch_id)
        rows = _batch_item_rows(db, batch_id)
        reports = [_preflight_from_row(row) for row in rows]
        batch.item_count = len(rows)
        batch.uploaded_count = sum(1 for row in rows if row.status == "uploaded")
        batch.preflighted_count = sum(1 for row in rows if row.status == "preflighted")
        batch.parsed_count = sum(1 for row in rows if row.status == "parsed")
        batch.conversion_required_count = sum(1 for report in reports if report.requires_conversion)
        batch.manual_review_count = sum(1 for report in reports if report.requires_manual_review)
        batch.failed_count = sum(1 for row in rows if row.status == "failed")
        if batch.failed_count:
            batch.status = "failed"
        elif batch.parsed_count and batch.parsed_count + batch.conversion_required_count + batch.manual_review_count >= len(rows):
            batch.status = "parsed"
        elif any(row.status in {"preflighted", "conversion_required", "manual_review"} for row in rows):
            batch.status = "preflighted"
        else:
            batch.status = "uploaded"
        db.commit()
        return batch_upload_from_row(batch)


def get_batch_upload(db: Session, batch_id: str) -> schemas.BatchUploadRead | None:
    row = db.get(dbm.BatchUpload, batch_id)
    return batch_upload_from_row(row) if row else None


def list_batch_items(db: Session, batch_id: str) -> list[schemas.BatchArtworkItemRead]:
    return [batch_item_from_row(row) for row in _batch_item_rows(db, batch_id)]


def batch_upload_from_row(row: dbm.BatchUpload) -> schemas.BatchUploadRead:
    return schemas.BatchUploadRead(
        batch_id=row.id,
        source_name=row.source_name,
        status=row.status,
        item_count=row.item_count,
        uploaded_count=row.uploaded_count,
        preflighted_count=row.preflighted_count,
        parsed_count=row.parsed_count,
        conversion_required_count=row.conversion_required_count,
        manual_review_count=row.manual_review_count,
        failed_count=row.failed_count,
        metadata=row.metadata_json or {},
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def batch_item_from_row(row: dbm.BatchArtworkItem) -> schemas.BatchArtworkItemRead:
    feature = schemas.ArtworkFeature.model_validate(row.feature_json) if row.feature_json else None
    preflight = schemas.PreflightReport.model_validate(row.preflight_report_json) if row.preflight_report_json else None
    return schemas.BatchArtworkItemRead(
        item_id=row.id,
        batch_id=row.batch_id,
        artwork_id=row.artwork_file_id,
        filename=row.filename,
        content_type=row.content_type,
        checksum=row.checksum,
        source_format=row.source_format,
        status=row.status,
        order_id=row.order_id,
        quantity=row.quantity,
        material=row.material,
        thickness=row.thickness,
        print_method=row.print_method,
        spot_color=row.spot_color,
        due_date=_parse_date(row.due_date),
        category=row.category,
        customer_id=row.customer_id,
        preflight_report=preflight,
        feature=feature,
        classification=row.classification,
        parse_error=row.parse_error,
        retry_count=row.retry_count,
        metadata=row.metadata_json or {},
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _require_batch(db: Session, batch_id: str) -> dbm.BatchUpload:
    row = db.get(dbm.BatchUpload, batch_id)
    if row is None:
        raise ValueError("batch upload not found")
    return row


def _batch_item_rows(db: Session, batch_id: str) -> list[dbm.BatchArtworkItem]:
    return list(
        db.scalars(
            select(dbm.BatchArtworkItem)
            .where(dbm.BatchArtworkItem.batch_id == batch_id)
            .order_by(dbm.BatchArtworkItem.created_at, dbm.BatchArtworkItem.id)
        )
    )


def _preflight_from_row(row: dbm.BatchArtworkItem) -> schemas.PreflightReport:
    if row.preflight_report_json:
        return schemas.PreflightReport.model_validate(row.preflight_report_json)
    return schemas.PreflightReport(
        filename=row.filename,
        source_format=row.source_format,
        can_parse_directly=False,
        requires_conversion=True,
        requires_manual_review=True,
        warnings=["missing preflight report"],
    )


def _combined_bbox(polygons: list[schemas.PolygonAsset]) -> schemas.BBox:
    boxes = [polygon.bbox or calculate_bbox(polygon.outer) for polygon in polygons]
    min_x = min(box.min_x for box in boxes)
    min_y = min(box.min_y for box in boxes)
    max_x = max(box.max_x for box in boxes)
    max_y = max(box.max_y for box in boxes)
    return schemas.BBox(width=max_x - min_x, height=max_y - min_y, min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


def _printable_area(sheet: schemas.SheetSpec) -> float:
    printable = printable_area_polygon(sheet)
    return printable.area or 0


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
