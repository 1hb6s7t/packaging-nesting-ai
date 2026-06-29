import pytest

from app.domain.schemas import PolygonAsset, SheetSpec
from app.services.geometry import (
    calculate_area,
    calculate_blank_area,
    calculate_min_distance,
    detect_collision,
    enrich_polygon,
    has_exact_geometry_backend,
    offset_polygon,
    printable_area_polygon,
    rectangle_asset,
    repair_polygon,
    translate_polygon,
    validate_polygon,
)


def test_polygon_repair_and_area() -> None:
    polygon = rectangle_asset("box", 120, 80)
    repaired = repair_polygon(polygon)
    valid, issues = validate_polygon(repaired)
    assert valid, issues
    assert calculate_area(repaired.outer) == 9600


def test_collision_and_blank_area() -> None:
    sheet = SheetSpec(sheet_id="s1", width=500, height=400, material="card", thickness="350gsm")
    printable = printable_area_polygon(sheet)
    first = rectangle_asset("a", 100, 100)
    second = rectangle_asset("b", 100, 100)
    assert detect_collision(first, second)
    assert calculate_blank_area(printable, [first, second]) == printable.area - first.area


def test_collision_uses_polygon_geometry_before_gap_distance() -> None:
    first = enrich_polygon(PolygonAsset(shape_id="tri_a", outer=[(0, 0), (4, 0), (0, 4)]))
    second = enrich_polygon(PolygonAsset(shape_id="tri_b", outer=[(3.1, 3.1), (6, 3.1), (3.1, 6)]))

    assert first.bbox.max_x > second.bbox.min_x
    assert first.bbox.max_y > second.bbox.min_y
    assert not detect_collision(first, second)
    assert 1 < calculate_min_distance(first, second) < 2
    assert not detect_collision(first, second, min_gap_mm=1)
    assert detect_collision(first, second, min_gap_mm=2)


@pytest.mark.skipif(not has_exact_geometry_backend(), reason="Shapely backend is not installed")
def test_shapely_repairs_self_intersecting_polygon() -> None:
    bowtie = enrich_polygon(PolygonAsset(shape_id="bowtie", outer=[(0, 0), (4, 4), (0, 4), (4, 0)]))

    valid, issues = validate_polygon(bowtie)
    assert not valid
    assert any("invalid" in issue or "zero" in issue for issue in issues)

    repaired = repair_polygon(bowtie)
    repaired_valid, repaired_issues = validate_polygon(repaired)

    assert repaired_valid, repaired_issues
    assert repaired.area == pytest.approx(4)


@pytest.mark.skipif(not has_exact_geometry_backend(), reason="Shapely backend is not installed")
def test_blank_area_uses_true_polygon_difference() -> None:
    printable = rectangle_asset("printable", 10, 10)
    half_outside = translate_polygon(rectangle_asset("half_outside", 10, 10), 5, 0)

    assert calculate_blank_area(printable, [half_outside]) == pytest.approx(50)


@pytest.mark.skipif(not has_exact_geometry_backend(), reason="Shapely backend is not installed")
def test_offset_polygon_preserves_concave_shape() -> None:
    l_shape = enrich_polygon(PolygonAsset(shape_id="l_shape", outer=[(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]))
    expanded = offset_polygon(l_shape, 1)

    assert expanded.area == pytest.approx(27)
    assert expanded.area < 36
    assert (2.0, 2.0) in expanded.outer
