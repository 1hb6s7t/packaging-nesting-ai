from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from app.domain.schemas import PolygonAsset, PreflightReport
from app.services.geometry import enrich_polygon, rectangle_asset
from app.services.storage import write_bytes, write_text


SUPPORTED_DIRECT_FORMATS = {"svg", "dxf"}
ARCHIVE_ONLY_FORMATS = {"cdr", "ai", "pdf", "eps", "plt"}
CURVE_STEPS = 16


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_format(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower().strip(".")
    if suffix:
        return suffix
    if content_type and "svg" in content_type:
        return "svg"
    return "unknown"


def save_artwork_bytes(artwork_id: str, filename: str, data: bytes) -> str:
    safe_name = Path(filename).name or "original.bin"
    stored = write_bytes(f"artworks/{artwork_id}/{safe_name}", data)
    return stored.storage_key


def save_polygon_json(artwork_id: str, polygons: list[PolygonAsset]) -> str:
    stored = write_text(
        f"artworks/{artwork_id}/polygon.json",
        json.dumps([polygon.model_dump(mode="json") for polygon in polygons], ensure_ascii=False, indent=2),
        content_type="application/json; charset=utf-8",
    )
    return stored.storage_key


def preflight_artwork(filename: str, content: str | None = None, content_type: str | None = None) -> PreflightReport:
    source_format = detect_format(filename, content_type)
    warnings: list[str] = []
    can_parse = source_format in SUPPORTED_DIRECT_FORMATS
    requires_conversion = source_format not in SUPPORTED_DIRECT_FORMATS
    requires_manual_review = source_format in ARCHIVE_ONLY_FORMATS or source_format == "pdf"
    dimensions = None
    detected_layers: list[str] = []

    if source_format == "svg" and content:
        try:
            root = ET.fromstring(content)
            width = _parse_length(root.attrib.get("width"))
            height = _parse_length(root.attrib.get("height"))
            if width and height:
                dimensions = {"width": width, "height": height}
            detected_layers = _detect_layers(content)
        except ET.ParseError as exc:
            warnings.append(f"SVG XML parse failed: {exc}")
            can_parse = False

    if source_format == "dxf" and content:
        detected_layers = _detect_dxf_layers(content)
    if source_format in {"cdr", "ai"}:
        warnings.append("CDR/AI 第一版只归档和预检，需要客户导出 SVG/DXF 或走转换服务")
    if source_format == "pdf":
        warnings.append("PDF 需要抽取矢量路径并人工确认刀线层")
    if source_format == "unknown":
        warnings.append("无法识别文件格式")

    return PreflightReport(
        filename=filename,
        source_format=source_format,
        can_parse_directly=can_parse,
        requires_conversion=requires_conversion,
        requires_manual_review=requires_manual_review,
        warnings=warnings,
        detected_layers=detected_layers,
        dimensions_mm=dimensions,
    )


def parse_vector_polygons(content: str, source_format: str, artwork_id: str) -> list[PolygonAsset]:
    if source_format == "svg":
        return parse_svg_polygons(content, artwork_id)
    if source_format == "dxf":
        return parse_dxf_polygons(content, artwork_id)
    raise ValueError(f"{source_format} is not directly parseable in MVP; convert to SVG/DXF first")


def parse_svg_polygons(content: str, artwork_id: str) -> list[PolygonAsset]:
    root = ET.fromstring(content)
    ns_pattern = re.compile(r"\{.*\}")
    polygons: list[PolygonAsset] = []

    for index, element in enumerate(root.iter()):
        tag = ns_pattern.sub("", element.tag)
        shape_id = element.attrib.get("id") or f"{artwork_id}_{tag}_{index}"
        layer = _layer_name(element)
        if tag == "rect":
            polygon = _parse_svg_rect(element, shape_id, layer)
            if polygon:
                polygons.append(polygon)
        elif tag in {"polygon", "polyline"}:
            points = _parse_points(element.attrib.get("points", ""))
            if len(points) >= 3:
                polygons.append(
                    enrich_polygon(
                        PolygonAsset(shape_id=shape_id, outer=points, metadata={"source": f"svg_{tag}", "layer": layer})
                    )
                )
        elif tag == "path":
            points = flatten_svg_path(element.attrib.get("d", ""))
            if len(points) >= 3:
                polygons.append(
                    enrich_polygon(
                        PolygonAsset(shape_id=shape_id, outer=points, metadata={"source": "svg_path", "layer": layer})
                    )
                )
        elif tag == "circle":
            cx = _parse_length(element.attrib.get("cx")) or 0
            cy = _parse_length(element.attrib.get("cy")) or 0
            radius = _parse_length(element.attrib.get("r")) or 0
            if radius > 0:
                polygons.append(
                    enrich_polygon(
                        PolygonAsset(
                            shape_id=shape_id,
                            outer=_ellipse_points(cx, cy, radius, radius),
                            metadata={"source": "svg_circle", "layer": layer},
                        )
                    )
                )
        elif tag == "ellipse":
            cx = _parse_length(element.attrib.get("cx")) or 0
            cy = _parse_length(element.attrib.get("cy")) or 0
            rx = _parse_length(element.attrib.get("rx")) or 0
            ry = _parse_length(element.attrib.get("ry")) or 0
            if rx > 0 and ry > 0:
                polygons.append(
                    enrich_polygon(
                        PolygonAsset(
                            shape_id=shape_id,
                            outer=_ellipse_points(cx, cy, rx, ry),
                            metadata={"source": "svg_ellipse", "layer": layer},
                        )
                    )
                )

    if not polygons:
        raise ValueError("No supported SVG rect/polygon/polyline/path/circle/ellipse geometry found")
    return [enrich_polygon(poly) for poly in polygons]


def parse_dxf_polygons(content: str, artwork_id: str) -> list[PolygonAsset]:
    pairs = _dxf_pairs(content)
    polygons: list[PolygonAsset] = []
    line_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    index = 0
    shape_index = 0
    while index < len(pairs):
        code, value = pairs[index]
        if code == "0" and value == "LWPOLYLINE":
            points, closed, next_index = _parse_lwpolyline(pairs, index + 1)
            if len(points) >= 3 and (closed or _same_point(points[0], points[-1])):
                polygons.append(
                    enrich_polygon(
                        PolygonAsset(
                            shape_id=f"{artwork_id}_lwpolyline_{shape_index}",
                            outer=_drop_closing_point(points),
                            metadata={"source": "dxf_lwpolyline"},
                        )
                    )
                )
                shape_index += 1
            index = next_index
            continue
        if code == "0" and value == "LINE":
            segment, next_index = _parse_dxf_line(pairs, index + 1)
            if segment:
                line_segments.append(segment)
            index = next_index
            continue
        index += 1

    stitched = _stitch_segments(line_segments)
    for points in stitched:
        if len(points) >= 3:
            polygons.append(
                enrich_polygon(
                    PolygonAsset(
                        shape_id=f"{artwork_id}_line_loop_{shape_index}",
                        outer=_drop_closing_point(points),
                        metadata={"source": "dxf_line_loop"},
                    )
                )
            )
            shape_index += 1

    if not polygons:
        raise ValueError("No closed DXF LWPOLYLINE or stitched LINE loop found")
    return polygons


def flatten_svg_path(path_data: str) -> list[tuple[float, float]]:
    tokens = re.findall(r"[MmLlHhVvCcQqZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", path_data)
    points: list[tuple[float, float]] = []
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)
    command = ""
    index = 0

    def read_float() -> float:
        nonlocal index
        value = float(tokens[index])
        index += 1
        return value

    while index < len(tokens):
        if re.match(r"^[A-Za-z]$", tokens[index]):
            command = tokens[index]
            index += 1
        if not command:
            break
        absolute = command.isupper()
        op = command.upper()
        if op == "M":
            x, y = read_float(), read_float()
            cursor = _resolve_point(cursor, x, y, absolute)
            start = cursor
            points.append(cursor)
            command = "L" if absolute else "l"
        elif op == "L":
            x, y = read_float(), read_float()
            cursor = _resolve_point(cursor, x, y, absolute)
            points.append(cursor)
        elif op == "H":
            x = read_float()
            cursor = (x, cursor[1]) if absolute else (cursor[0] + x, cursor[1])
            points.append(cursor)
        elif op == "V":
            y = read_float()
            cursor = (cursor[0], y) if absolute else (cursor[0], cursor[1] + y)
            points.append(cursor)
        elif op == "C":
            c1 = _resolve_point(cursor, read_float(), read_float(), absolute)
            c2 = _resolve_point(cursor, read_float(), read_float(), absolute)
            end = _resolve_point(cursor, read_float(), read_float(), absolute)
            points.extend(_cubic_points(cursor, c1, c2, end)[1:])
            cursor = end
        elif op == "Q":
            c = _resolve_point(cursor, read_float(), read_float(), absolute)
            end = _resolve_point(cursor, read_float(), read_float(), absolute)
            points.extend(_quadratic_points(cursor, c, end)[1:])
            cursor = end
        elif op == "Z":
            cursor = start
            if points and not _same_point(points[-1], start):
                points.append(start)
        else:
            raise ValueError(f"Unsupported SVG path command: {command}")
    return _drop_closing_point(points)


def _parse_svg_rect(element: ET.Element, shape_id: str, layer: str | None) -> PolygonAsset | None:
    x = _parse_length(element.attrib.get("x")) or 0
    y = _parse_length(element.attrib.get("y")) or 0
    width = _parse_length(element.attrib.get("width")) or 0
    height = _parse_length(element.attrib.get("height")) or 0
    if width <= 0 or height <= 0:
        return None
    poly = rectangle_asset(shape_id, width, height, {"source": "svg_rect", "layer": layer})
    return enrich_polygon(poly.model_copy(update={"outer": [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]}))


def _parse_points(value: str) -> list[tuple[float, float]]:
    numbers = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", value)]
    return list(zip(numbers[0::2], numbers[1::2]))


def _parse_length(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def _detect_layers(content: str) -> list[str]:
    return sorted(set(re.findall(r'id=["\']([^"\']*(?:cut|crease|bleed|safe)[^"\']*)["\']', content, flags=re.I)))


def _detect_dxf_layers(content: str) -> list[str]:
    pairs = _dxf_pairs(content)
    return sorted({value for code, value in pairs if code == "8"})


def _layer_name(element: ET.Element) -> str | None:
    return element.attrib.get("data-layer") or element.attrib.get("id")


def _ellipse_points(cx: float, cy: float, rx: float, ry: float, steps: int = 48) -> list[tuple[float, float]]:
    return [
        (cx + math.cos(2 * math.pi * index / steps) * rx, cy + math.sin(2 * math.pi * index / steps) * ry)
        for index in range(steps)
    ]


def _resolve_point(cursor: tuple[float, float], x: float, y: float, absolute: bool) -> tuple[float, float]:
    return (x, y) if absolute else (cursor[0] + x, cursor[1] + y)


def _cubic_points(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]
) -> list[tuple[float, float]]:
    points = []
    for step in range(CURVE_STEPS + 1):
        t = step / CURVE_STEPS
        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t**2 * p2[0] + t**3 * p3[0]
        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t**2 * p2[1] + t**3 * p3[1]
        points.append((x, y))
    return points


def _quadratic_points(
    p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float]
) -> list[tuple[float, float]]:
    points = []
    for step in range(CURVE_STEPS + 1):
        t = step / CURVE_STEPS
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1]
        points.append((x, y))
    return points


def _dxf_pairs(content: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in content.replace("\r\n", "\n").split("\n") if line.strip()]
    return [(lines[index], lines[index + 1]) for index in range(0, len(lines) - 1, 2)]


def _parse_lwpolyline(pairs: list[tuple[str, str]], start_index: int) -> tuple[list[tuple[float, float]], bool, int]:
    points: list[tuple[float, float]] = []
    closed = False
    pending_x: float | None = None
    index = start_index
    while index < len(pairs):
        code, value = pairs[index]
        if code == "0":
            break
        if code == "70":
            closed = bool(int(float(value)) & 1)
        elif code == "10":
            pending_x = float(value)
        elif code == "20" and pending_x is not None:
            points.append((pending_x, float(value)))
            pending_x = None
        index += 1
    return points, closed, index


def _parse_dxf_line(
    pairs: list[tuple[str, str]], start_index: int
) -> tuple[tuple[tuple[float, float], tuple[float, float]] | None, int]:
    values: dict[str, float] = {}
    index = start_index
    while index < len(pairs):
        code, value = pairs[index]
        if code == "0":
            break
        if code in {"10", "20", "11", "21"}:
            values[code] = float(value)
        index += 1
    if {"10", "20", "11", "21"}.issubset(values):
        return ((values["10"], values["20"]), (values["11"], values["21"])), index
    return None, index


def _stitch_segments(
    segments: list[tuple[tuple[float, float], tuple[float, float]]]
) -> list[list[tuple[float, float]]]:
    remaining = segments[:]
    loops: list[list[tuple[float, float]]] = []
    while remaining:
        start, end = remaining.pop(0)
        points = [start, end]
        changed = True
        while changed:
            changed = False
            for index, (left, right) in enumerate(remaining):
                if _same_point(points[-1], left):
                    points.append(right)
                elif _same_point(points[-1], right):
                    points.append(left)
                else:
                    continue
                remaining.pop(index)
                changed = True
                break
        if len(points) >= 4 and _same_point(points[0], points[-1]):
            loops.append(points)
    return loops


def _same_point(a: tuple[float, float], b: tuple[float, float], tolerance: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance


def _drop_closing_point(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) > 1 and _same_point(points[0], points[-1]):
        return points[:-1]
    return points


def new_artwork_id() -> str:
    return f"art_{uuid4().hex[:16]}"
