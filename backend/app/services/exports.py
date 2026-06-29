from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from app.domain.schemas import NestingJob, NestingSolution, Point
from app.services.reports import generate_solution_report
from app.services.storage import write_bytes


@dataclass(frozen=True)
class ExportArtifact:
    export_id: str
    export_type: str
    storage_key: str
    checksum: str
    storage_backend: str
    storage_object_key: str
    storage_version_id: str | None = None
    storage_etag: str | None = None
    storage_size_bytes: int | None = None


def create_solution_export(job: NestingJob, solution: NestingSolution, export_type: str) -> ExportArtifact:
    export_id = f"exp_{uuid4().hex[:16]}"
    if export_type == "pdf":
        data = generate_solution_pdf(job, solution)
        object_key = f"exports/{solution.solution_id}/{export_id}.pdf"
        content_type = "application/pdf"
    elif export_type == "dxf":
        data = generate_solution_dxf(job, solution).encode("utf-8")
        object_key = f"exports/{solution.solution_id}/{export_id}.dxf"
        content_type = "application/dxf"
    else:
        raise ValueError(f"unsupported export type: {export_type}")
    stored = write_bytes(object_key, data, content_type=content_type)
    return ExportArtifact(
        export_id=export_id,
        export_type=export_type,
        storage_key=stored.storage_key,
        checksum=hashlib.sha256(data).hexdigest(),
        storage_backend=stored.backend,
        storage_object_key=stored.object_key,
        storage_version_id=stored.version_id,
        storage_etag=stored.etag,
        storage_size_bytes=stored.size,
    )


def generate_solution_dxf(job: NestingJob, solution: NestingSolution) -> str:
    lines = [
        "0",
        "SECTION",
        "2",
        "HEADER",
        "9",
        "$INSUNITS",
        "70",
        "4",
        "0",
        "ENDSEC",
        "0",
        "SECTION",
        "2",
        "ENTITIES",
    ]
    lines.extend(_dxf_polyline("SHEET", [(0, 0), (job.sheet.width, 0), (job.sheet.width, job.sheet.height), (0, job.sheet.height)]))
    for placement in solution.placed_items:
        if placement.polygon:
            lines.extend(_dxf_polyline(_safe_layer(f"ITEM_{placement.item_id}"), placement.polygon.outer))
    lines.extend(["0", "ENDSEC", "0", "EOF"])
    return "\n".join(lines) + "\n"


def generate_solution_pdf(job: NestingJob, solution: NestingSolution) -> bytes:
    report = generate_solution_report(job, solution)
    text_lines = [
        "Packaging Nesting Production Export",
        f"Job: {job.job_id}",
        f"Solution: {solution.solution_id}",
        f"Status: {solution.status}",
        f"Solver: {solution.solver}",
        f"Sheet: {job.sheet.width} x {job.sheet.height} mm, {job.sheet.material} {job.sheet.thickness}",
        f"Utilization: {solution.utilization_rate:.4f}",
        f"Waste: {solution.waste_rate:.4f}",
        f"Placed items: {len(solution.placed_items)}",
        f"Unplaced items: {len(solution.unplaced_items)}",
        f"Validation valid: {bool(solution.validation_report and solution.validation_report.is_valid)}",
        f"Score: {json.dumps(report.get('score'), ensure_ascii=False) if report.get('score') else '-'}",
    ]
    for placement in solution.placed_items[:18]:
        text_lines.append(
            f"{placement.item_id} order={placement.order_id} x={placement.x} y={placement.y} "
            f"rot={placement.rotation} w={placement.width} h={placement.height}"
        )
    content = _pdf_content_stream(job, solution, text_lines)
    return _build_pdf(content)


def _dxf_polyline(layer: str, points: list[Point]) -> list[str]:
    rows = ["0", "LWPOLYLINE", "8", layer, "90", str(len(points)), "70", "1"]
    for x, y in points:
        rows.extend(["10", _fmt(x), "20", _fmt(y)])
    return rows


def _safe_layer(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)[:80] or "ITEM"


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _pdf_content_stream(job: NestingJob, solution: NestingSolution, text_lines: list[str]) -> str:
    commands = ["0.95 0.97 1 rg 40 470 515 330 re f", "0 0 0 RG 40 470 515 330 re S"]
    scale = min(515 / job.sheet.width, 300 / job.sheet.height)
    origin_x = 40
    origin_y = 485
    sheet_height = job.sheet.height * scale
    commands.append(f"0.88 0.96 0.90 rg {_fmt(origin_x)} {_fmt(origin_y)} {_fmt(job.sheet.width * scale)} {_fmt(sheet_height)} re f")
    commands.append(f"0.1 0.4 0.2 RG {_fmt(origin_x)} {_fmt(origin_y)} {_fmt(job.sheet.width * scale)} {_fmt(sheet_height)} re S")
    for placement in solution.placed_items:
        if placement.polygon:
            commands.append("0.82 0.90 1 rg")
            commands.append(_pdf_polygon_path(placement.polygon.outer, origin_x, origin_y, job.sheet.height, scale) + " f")
            commands.append("0.2 0.25 0.35 RG")
            commands.append(_pdf_polygon_path(placement.polygon.outer, origin_x, origin_y, job.sheet.height, scale) + " S")
    text = ["BT", "/F1 10 Tf", "14 TL", "40 440 Td"]
    for index, line in enumerate(text_lines[:28]):
        if index:
            text.append("T*")
        text.append(f"({_pdf_escape(line)}) Tj")
    text.append("ET")
    return "\n".join(commands + text)


def _pdf_polygon_path(points: list[Point], origin_x: float, origin_y: float, sheet_height_mm: float, scale: float) -> str:
    if not points:
        return ""
    mapped = [_pdf_point(point, origin_x, origin_y, sheet_height_mm, scale) for point in points]
    first = mapped[0]
    parts = [f"{_fmt(first[0])} {_fmt(first[1])} m"]
    for x, y in mapped[1:]:
        parts.append(f"{_fmt(x)} {_fmt(y)} l")
    parts.append("h")
    return " ".join(parts)


def _pdf_point(point: Point, origin_x: float, origin_y: float, sheet_height_mm: float, scale: float) -> tuple[float, float]:
    x, y = point
    return origin_x + x * scale, origin_y + (sheet_height_mm - y) * scale


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(content: str) -> bytes:
    stream = content.encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)
