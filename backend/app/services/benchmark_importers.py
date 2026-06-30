from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.domain.schemas import BenchmarkCase, NestingItem, PlanningMode, PolygonAsset, SheetSpec


def load_public_dataset_as_benchmark_case(
    path: Path,
    *,
    case_id: str,
    name: str | None = None,
    sheet_width: float | None = None,
    sheet_height: float | None = None,
    material: str = "dataset_material",
    thickness: str = "dataset_thickness",
    planning_mode: PlanningMode = "pattern",
) -> BenchmarkCase:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return benchmark_case_from_mapping(
            payload,
            case_id=case_id,
            name=name or path.stem,
            sheet_width=sheet_width,
            sheet_height=sheet_height,
            material=material,
            thickness=thickness,
            planning_mode=planning_mode,
        )
    if suffix == ".csv":
        rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
        return benchmark_case_from_rows(
            rows,
            case_id=case_id,
            name=name or path.stem,
            sheet_width=sheet_width,
            sheet_height=sheet_height,
            material=material,
            thickness=thickness,
            planning_mode=planning_mode,
        )
    rows, inferred_sheet = parse_plain_text_dataset(path.read_text(encoding="utf-8"))
    return benchmark_case_from_rows(
        rows,
        case_id=case_id,
        name=name or path.stem,
        sheet_width=sheet_width or inferred_sheet[0],
        sheet_height=sheet_height or inferred_sheet[1],
        material=material,
        thickness=thickness,
        planning_mode=planning_mode,
    )


def benchmark_case_from_mapping(
    payload: Any,
    *,
    case_id: str,
    name: str,
    sheet_width: float | None,
    sheet_height: float | None,
    material: str,
    thickness: str,
    planning_mode: PlanningMode,
) -> BenchmarkCase:
    if not isinstance(payload, dict):
        raise ValueError("JSON dataset must be an object")
    if {"case_id", "sheet", "items"}.issubset(payload):
        data = {**payload, "case_id": case_id, "name": name or payload.get("name") or case_id}
        data.setdefault("planning_mode", planning_mode)
        return BenchmarkCase.model_validate(data)

    sheet_payload = payload.get("sheet") if isinstance(payload.get("sheet"), dict) else {}
    resolved_sheet_width = first_number(
        sheet_width,
        sheet_payload.get("width"),
        payload.get("sheet_width"),
        payload.get("bin_width"),
        payload.get("container_width"),
        payload.get("W"),
    )
    resolved_sheet_height = first_number(
        sheet_height,
        sheet_payload.get("height"),
        payload.get("sheet_height"),
        payload.get("bin_height"),
        payload.get("container_height"),
        payload.get("H"),
    )
    item_payloads = first_list(payload.get("items"), payload.get("rectangles"), payload.get("boxes"), payload.get("pieces"))
    if item_payloads is None:
        raise ValueError("JSON dataset must include items, rectangles, boxes, or pieces")
    return BenchmarkCase(
        case_id=case_id,
        name=name,
        sheet=build_sheet(case_id, resolved_sheet_width, resolved_sheet_height, material, thickness),
        items=[item_from_mapping(row, index) for index, row in enumerate(item_payloads, 1)],
        planning_mode=planning_mode,
        baseline_utilization_rate=maybe_float(payload.get("baseline_utilization_rate")),
    )


def benchmark_case_from_rows(
    rows: list[dict[str, Any]],
    *,
    case_id: str,
    name: str,
    sheet_width: float | None,
    sheet_height: float | None,
    material: str,
    thickness: str,
    planning_mode: PlanningMode,
) -> BenchmarkCase:
    if not rows:
        raise ValueError("dataset must contain at least one item row")
    return BenchmarkCase(
        case_id=case_id,
        name=name,
        sheet=build_sheet(case_id, sheet_width, sheet_height, material, thickness),
        items=[item_from_mapping(row, index) for index, row in enumerate(rows, 1)],
        planning_mode=planning_mode,
    )


def parse_plain_text_dataset(content: str) -> tuple[list[dict[str, Any]], tuple[float | None, float | None]]:
    rows: list[dict[str, Any]] = []
    inferred_sheet: tuple[float | None, float | None] = (None, None)
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        values = [float(part) for part in line.replace(",", " ").split()]
        if len(values) < 2:
            continue
        if inferred_sheet == (None, None):
            inferred_sheet = (values[0], values[1])
            if len(values) == 2:
                continue
        width, height = values[0], values[1]
        quantity = int(values[2]) if len(values) >= 3 else 1
        rows.append({"width": width, "height": height, "quantity": quantity})
    return rows, inferred_sheet


def item_from_mapping(row: Any, index: int) -> NestingItem:
    if not isinstance(row, dict):
        raise ValueError(f"dataset item #{index} must be an object")
    width = first_number(row.get("width"), row.get("w"), row.get("dx"))
    height = first_number(row.get("height"), row.get("h"), row.get("dy"))
    quantity_value = row.get("quantity", row.get("qty", row.get("demand", 1)))
    quantity = int(quantity_value)
    if quantity < 1:
        raise ValueError(f"dataset item #{index} quantity must be >= 1")
    item_id = str(row.get("item_id") or row.get("id") or f"item_{index:04d}")
    order_id = str(row.get("order_id") or row.get("order") or f"dataset_order_{index:04d}")
    return NestingItem(
        item_id=item_id,
        order_id=order_id,
        polygon=PolygonAsset(
            shape_id=str(row.get("shape_id") or f"shape_{item_id}"),
            outer=[(0, 0), (width, 0), (width, height), (0, height)],
        ),
        quantity=quantity,
        priority_score=float(row.get("priority_score", row.get("priority", 0))),
        allowed_rotations=[int(value) for value in row.get("allowed_rotations", [0, 90])],
        min_gap_mm=float(row.get("min_gap_mm", row.get("gap", 0))),
        bleed_mm=float(row.get("bleed_mm", row.get("bleed", 0))),
        metadata={"dataset_row": index, **dict(row.get("metadata") or {})},
    )


def build_sheet(
    case_id: str,
    width: float | None,
    height: float | None,
    material: str,
    thickness: str,
) -> SheetSpec:
    if width is None or height is None:
        raise ValueError("sheet width and height are required")
    return SheetSpec(
        sheet_id=f"{case_id}_sheet",
        width=width,
        height=height,
        material=material,
        thickness=thickness,
    )


def first_number(*values: Any) -> float:
    for value in values:
        parsed = maybe_float(value)
        if parsed is not None:
            return parsed
    raise ValueError("required numeric value is missing")


def maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_list(*values: Any) -> list[Any] | None:
    for value in values:
        if isinstance(value, list):
            return value
    return None
