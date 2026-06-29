from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from app.domain.schemas import BBox, Point, PolygonAsset, SheetSpec

try:
    from shapely.geometry import GeometryCollection, MultiPolygon, Point as ShapelyPoint, Polygon as ShapelyPolygon
    from shapely.ops import unary_union
    from shapely.validation import explain_validity, make_valid
except ImportError:  # pragma: no cover - exercised by deployments without optimization dependencies.
    GeometryCollection = MultiPolygon = ShapelyPoint = ShapelyPolygon = None  # type: ignore[assignment]
    unary_union = None  # type: ignore[assignment]
    make_valid = None  # type: ignore[assignment]
    explain_validity = None  # type: ignore[assignment]

EPSILON = 1e-9


@dataclass(frozen=True)
class Rect:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return max(0.0, self.max_x - self.min_x)

    @property
    def height(self) -> float:
        return max(0.0, self.max_y - self.min_y)

    @property
    def area(self) -> float:
        return self.width * self.height

    def intersects(self, other: "Rect", gap: float = 0) -> bool:
        return not (
            self.max_x + gap <= other.min_x
            or other.max_x + gap <= self.min_x
            or self.max_y + gap <= other.min_y
            or other.max_y + gap <= self.min_y
        )

    def contains(self, other: "Rect") -> bool:
        return (
            other.min_x >= self.min_x
            and other.max_x <= self.max_x
            and other.min_y >= self.min_y
            and other.max_y <= self.max_y
        )


def close_ring(points: list[Point]) -> list[Point]:
    if not points:
        return points
    return points if points[0] == points[-1] else [*points, points[0]]


def ring_edges(points: list[Point]) -> list[tuple[Point, Point]]:
    ring = close_ring(points)
    return list(zip(ring, ring[1:])) if len(ring) >= 4 else []


def polygon_boundary_edges(polygon: PolygonAsset) -> list[tuple[Point, Point]]:
    edges = ring_edges(polygon.outer)
    for hole in polygon.holes:
        edges.extend(ring_edges(hole))
    return edges


def polygon_outer_edges(polygon: PolygonAsset) -> list[tuple[Point, Point]]:
    return ring_edges(polygon.outer)


def calculate_area(points: list[Point]) -> float:
    ring = close_ring(points)
    if len(ring) < 4:
        return 0.0
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def calculate_perimeter(points: list[Point]) -> float:
    ring = close_ring(points)
    return sum(math.dist(a, b) for a, b in zip(ring, ring[1:]))


def calculate_bbox(points: list[Point]) -> BBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return BBox(width=max_x - min_x, height=max_y - min_y, min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y)


def has_exact_geometry_backend() -> bool:
    return ShapelyPolygon is not None and unary_union is not None


def rect_from_polygon(polygon: PolygonAsset) -> Rect:
    bbox = polygon.bbox or calculate_bbox(polygon.outer)
    return Rect(bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y)


def enrich_polygon(polygon: PolygonAsset) -> PolygonAsset:
    bbox = calculate_bbox(polygon.outer)
    area = calculate_area(polygon.outer) - sum(calculate_area(hole) for hole in polygon.holes)
    return polygon.model_copy(update={"bbox": bbox, "area": area, "perimeter": calculate_perimeter(polygon.outer)})


def validate_polygon(polygon: PolygonAsset) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if len(polygon.outer) < 3:
        issues.append("outer ring has fewer than three points")
    if calculate_area(polygon.outer) <= 0:
        issues.append("polygon area is zero")
    if any(len(hole) < 3 for hole in polygon.holes):
        issues.append("one or more holes have fewer than three points")
    if has_exact_geometry_backend() and len(polygon.outer) >= 3:
        geometry = _to_shapely_polygon(polygon)
        if geometry is None:
            issues.append("polygon cannot be constructed")
        elif not geometry.is_valid:
            reason = explain_validity(geometry) if explain_validity else "invalid geometry"
            issues.append(f"polygon geometry is invalid: {reason}")
    return not issues, issues


def repair_polygon(polygon: PolygonAsset) -> PolygonAsset:
    if has_exact_geometry_backend():
        geometry = _to_shapely_polygon(polygon)
        repaired = _repair_shapely_geometry(geometry) if geometry is not None else None
        primary = _primary_shapely_polygon(repaired) if repaired is not None else None
        if primary is not None and not primary.is_empty and primary.area > EPSILON:
            return _polygon_asset_from_shapely(primary, polygon, metadata={"repaired_by": "shapely"})

    repaired = polygon.model_copy(
        update={
            "outer": close_ring(polygon.outer)[:-1],
            "holes": [close_ring(hole)[:-1] for hole in polygon.holes],
        }
    )
    return enrich_polygon(repaired)


def rotate_polygon(polygon: PolygonAsset, degrees: int) -> PolygonAsset:
    radians = math.radians(degrees % 360)
    cos_v = math.cos(radians)
    sin_v = math.sin(radians)
    points = [(x * cos_v - y * sin_v, x * sin_v + y * cos_v) for x, y in polygon.outer]
    return enrich_polygon(polygon.model_copy(update={"outer": points}))


def translate_polygon(polygon: PolygonAsset, dx: float, dy: float) -> PolygonAsset:
    points = [(x + dx, y + dy) for x, y in polygon.outer]
    holes = [[(x + dx, y + dy) for x, y in hole] for hole in polygon.holes]
    return enrich_polygon(polygon.model_copy(update={"outer": points, "holes": holes}))


def mirror_polygon(polygon: PolygonAsset) -> PolygonAsset:
    bbox = polygon.bbox or calculate_bbox(polygon.outer)
    points = [(bbox.max_x - (x - bbox.min_x), y) for x, y in polygon.outer]
    return enrich_polygon(polygon.model_copy(update={"outer": points}))


def offset_polygon(polygon: PolygonAsset, offset_mm: float) -> PolygonAsset:
    if has_exact_geometry_backend():
        geometry = _normalized_shapely_polygon(polygon)
        if geometry is not None:
            buffered = geometry.buffer(offset_mm, join_style=2)
            primary = _primary_shapely_polygon(buffered)
            if primary is None or primary.is_empty or primary.area <= EPSILON:
                raise ValueError("offset eliminates polygon geometry")
            return _polygon_asset_from_shapely(
                primary,
                polygon,
                metadata={"geometry_backend": "shapely", "offset_mm": offset_mm},
            )

    bbox = polygon.bbox or calculate_bbox(polygon.outer)
    points = [
        (bbox.min_x - offset_mm, bbox.min_y - offset_mm),
        (bbox.max_x + offset_mm, bbox.min_y - offset_mm),
        (bbox.max_x + offset_mm, bbox.max_y + offset_mm),
        (bbox.min_x - offset_mm, bbox.max_y + offset_mm),
    ]
    return enrich_polygon(polygon.model_copy(update={"outer": points, "holes": []}))


def sheet_area_polygon(sheet: SheetSpec) -> PolygonAsset:
    return enrich_polygon(
        PolygonAsset(
            shape_id=f"{sheet.sheet_id}_sheet",
            outer=[(0, 0), (sheet.width, 0), (sheet.width, sheet.height), (0, sheet.height)],
            metadata={"type": "sheet_area"},
        )
    )


def printable_area_polygon(sheet: SheetSpec) -> PolygonAsset:
    x1 = sheet.margin_left
    y1 = sheet.margin_top + sheet.gripper_mm
    x2 = sheet.width - sheet.margin_right
    y2 = sheet.height - sheet.margin_bottom
    return enrich_polygon(
        PolygonAsset(
            shape_id=f"{sheet.sheet_id}_printable",
            outer=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
            metadata={"type": "printable_area"},
        )
    )


def gripper_area_polygon(sheet: SheetSpec) -> PolygonAsset:
    return enrich_polygon(
        PolygonAsset(
            shape_id=f"{sheet.sheet_id}_gripper",
            outer=[
                (sheet.margin_left, sheet.margin_top),
                (sheet.width - sheet.margin_right, sheet.margin_top),
                (sheet.width - sheet.margin_right, sheet.margin_top + sheet.gripper_mm),
                (sheet.margin_left, sheet.margin_top + sheet.gripper_mm),
            ],
            metadata={"type": "gripper_area"},
        )
    )


def detect_collision(a: PolygonAsset, b: PolygonAsset, min_gap_mm: float = 0) -> bool:
    if not rect_from_polygon(a).intersects(rect_from_polygon(b), gap=min_gap_mm):
        return False
    a_geometry = _normalized_shapely_polygon(a)
    b_geometry = _normalized_shapely_polygon(b)
    if a_geometry is not None and b_geometry is not None:
        if a_geometry.intersects(b_geometry):
            return True
        return min_gap_mm > 0 and a_geometry.distance(b_geometry) + EPSILON < min_gap_mm
    if polygons_touch_or_overlap(a, b):
        return True
    if min_gap_mm <= 0:
        return False
    return calculate_min_distance(a, b) < min_gap_mm


def detect_containment(container: PolygonAsset, subject: PolygonAsset) -> bool:
    if not rect_from_polygon(container).contains(rect_from_polygon(subject)):
        return False
    container_geometry = _normalized_shapely_polygon(container)
    subject_geometry = _normalized_shapely_polygon(subject)
    if container_geometry is not None and subject_geometry is not None:
        return container_geometry.covers(subject_geometry)
    if not all(polygon_contains_point(container, point, include_boundary=True) for point in subject.outer):
        return False
    container_edges = polygon_boundary_edges(container)
    for subject_edge in polygon_outer_edges(subject):
        if any(_segments_properly_intersect(subject_edge[0], subject_edge[1], left, right) for left, right in container_edges):
            return False
    return True


def calculate_blank_area(printable: PolygonAsset, occupied: Iterable[PolygonAsset]) -> float:
    occupied_polygons = list(occupied)
    printable_geometry = _normalized_shapely_polygon(printable)
    occupied_geometries = [geometry for polygon in occupied_polygons if (geometry := _normalized_shapely_polygon(polygon))]
    if printable_geometry is not None:
        if not occupied_geometries:
            return max(0.0, printable_geometry.area)
        occupied_union = unary_union(occupied_geometries) if unary_union else None
        if occupied_union is not None:
            return max(0.0, printable_geometry.difference(occupied_union).area)

    used = sum(poly.area if poly.area is not None else calculate_area(poly.outer) for poly in occupied_polygons)
    printable_area = printable.area if printable.area is not None else calculate_area(printable.outer)
    return max(0.0, printable_area - used)


def calculate_min_distance(a: PolygonAsset, b: PolygonAsset) -> float:
    a_geometry = _normalized_shapely_polygon(a)
    b_geometry = _normalized_shapely_polygon(b)
    if a_geometry is not None and b_geometry is not None:
        return a_geometry.distance(b_geometry)
    if polygons_touch_or_overlap(a, b):
        return 0.0
    distances = [
        _segment_distance(a_left, a_right, b_left, b_right)
        for a_left, a_right in polygon_boundary_edges(a)
        for b_left, b_right in polygon_boundary_edges(b)
    ]
    return min(distances) if distances else math.inf


def calculate_container_margin(container: PolygonAsset, subject: PolygonAsset) -> float:
    if not detect_containment(container, subject):
        return 0.0
    container_geometry = _normalized_shapely_polygon(container)
    subject_geometry = _normalized_shapely_polygon(subject)
    if container_geometry is not None and subject_geometry is not None:
        return subject_geometry.boundary.distance(container_geometry.boundary)
    distances = [
        _segment_distance(subject_left, subject_right, container_left, container_right)
        for subject_left, subject_right in polygon_outer_edges(subject)
        for container_left, container_right in polygon_boundary_edges(container)
    ]
    return min(distances) if distances else math.inf


def polygons_touch_or_overlap(a: PolygonAsset, b: PolygonAsset) -> bool:
    if not rect_from_polygon(a).intersects(rect_from_polygon(b)):
        return False
    a_geometry = _normalized_shapely_polygon(a)
    b_geometry = _normalized_shapely_polygon(b)
    if a_geometry is not None and b_geometry is not None:
        return a_geometry.intersects(b_geometry)
    for left_a, right_a in polygon_boundary_edges(a):
        for left_b, right_b in polygon_boundary_edges(b):
            if _segments_intersect(left_a, right_a, left_b, right_b):
                return True
    return polygon_contains_point(a, b.outer[0], include_boundary=True) or polygon_contains_point(
        b, a.outer[0], include_boundary=True
    )


def polygon_contains_point(polygon: PolygonAsset, point: Point, include_boundary: bool = True) -> bool:
    geometry = _normalized_shapely_polygon(polygon)
    if geometry is not None and ShapelyPoint is not None:
        shapely_point = ShapelyPoint(point)
        return geometry.covers(shapely_point) if include_boundary else geometry.contains(shapely_point)
    outer_position = _point_ring_position(point, polygon.outer)
    if outer_position == "boundary":
        return include_boundary
    if outer_position != "inside":
        return False
    for hole in polygon.holes:
        hole_position = _point_ring_position(point, hole)
        if hole_position == "boundary":
            return include_boundary
        if hole_position == "inside":
            return False
    return True


def _to_shapely_polygon(polygon: PolygonAsset) -> Any | None:
    if not has_exact_geometry_backend():
        return None
    try:
        holes = [close_ring(hole) for hole in polygon.holes if len(hole) >= 3]
        geometry = ShapelyPolygon(close_ring(polygon.outer), holes)  # type: ignore[misc]
    except Exception:
        return None
    return geometry if not geometry.is_empty else None


def _normalized_shapely_polygon(polygon: PolygonAsset) -> Any | None:
    geometry = _to_shapely_polygon(polygon)
    if geometry is None:
        return None
    if not geometry.is_valid:
        geometry = _repair_shapely_geometry(geometry)
    return _primary_shapely_polygon(geometry)


def _repair_shapely_geometry(geometry: Any) -> Any:
    repaired = make_valid(geometry) if make_valid else geometry
    if repaired is None or repaired.is_empty:
        repaired = geometry.buffer(0)
    if not repaired.is_valid:
        repaired = repaired.buffer(0)
    return repaired


def _primary_shapely_polygon(geometry: Any) -> Any | None:
    if not has_exact_geometry_backend() or geometry is None or geometry.is_empty:
        return None
    if ShapelyPolygon is not None and isinstance(geometry, ShapelyPolygon):
        return geometry
    if MultiPolygon is not None and isinstance(geometry, MultiPolygon):
        return max(geometry.geoms, key=lambda item: item.area, default=None)
    if GeometryCollection is not None and isinstance(geometry, GeometryCollection):
        polygons = [item for item in (_primary_shapely_polygon(item) for item in geometry.geoms) if item is not None]
        return max(polygons, key=lambda item: item.area, default=None)
    return None


def _polygon_asset_from_shapely(
    geometry: Any,
    source: PolygonAsset,
    *,
    metadata: dict[str, Any] | None = None,
) -> PolygonAsset:
    outer = [(float(x), float(y)) for x, y in list(geometry.exterior.coords)[:-1]]
    holes = [
        [(float(x), float(y)) for x, y in list(interior.coords)[:-1]]
        for interior in geometry.interiors
        if len(interior.coords) >= 4
    ]
    merged_metadata = {**source.metadata, **(metadata or {})}
    return enrich_polygon(source.model_copy(update={"outer": outer, "holes": holes, "metadata": merged_metadata}))


def _point_ring_position(point: Point, ring: list[Point]) -> str:
    if len(ring) < 3:
        return "outside"
    for left, right in ring_edges(ring):
        if _point_on_segment(point, left, right):
            return "boundary"
    x, y = point
    inside = False
    closed = close_ring(ring)
    for (x1, y1), (x2, y2) in zip(closed, closed[1:]):
        crosses = (y1 > y) != (y2 > y)
        if crosses:
            intersection_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if intersection_x > x:
                inside = not inside
    return "inside" if inside else "outside"


def _segments_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    if _segments_properly_intersect(a1, a2, b1, b2):
        return True
    return (
        _point_on_segment(a1, b1, b2)
        or _point_on_segment(a2, b1, b2)
        or _point_on_segment(b1, a1, a2)
        or _point_on_segment(b2, a1, a2)
    )


def _segments_properly_intersect(a1: Point, a2: Point, b1: Point, b2: Point) -> bool:
    o1 = _orientation(a1, a2, b1)
    o2 = _orientation(a1, a2, b2)
    o3 = _orientation(b1, b2, a1)
    o4 = _orientation(b1, b2, a2)
    return o1 * o2 < -EPSILON and o3 * o4 < -EPSILON


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_on_segment(point: Point, left: Point, right: Point) -> bool:
    if abs(_orientation(left, right, point)) > EPSILON:
        return False
    return (
        min(left[0], right[0]) - EPSILON <= point[0] <= max(left[0], right[0]) + EPSILON
        and min(left[1], right[1]) - EPSILON <= point[1] <= max(left[1], right[1]) + EPSILON
    )


def _segment_distance(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    if _segments_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _point_segment_distance(a1, b1, b2),
        _point_segment_distance(a2, b1, b2),
        _point_segment_distance(b1, a1, a2),
        _point_segment_distance(b2, a1, a2),
    )


def _point_segment_distance(point: Point, left: Point, right: Point) -> float:
    dx = right[0] - left[0]
    dy = right[1] - left[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= EPSILON:
        return math.dist(point, left)
    t = ((point[0] - left[0]) * dx + (point[1] - left[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    projection = (left[0] + t * dx, left[1] + t * dy)
    return math.dist(point, projection)


def rectangle_asset(shape_id: str, width: float, height: float, metadata: dict | None = None) -> PolygonAsset:
    return enrich_polygon(
        PolygonAsset(
            shape_id=shape_id,
            outer=[(0, 0), (width, 0), (width, height), (0, height)],
            metadata=metadata or {},
        )
    )
