from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.services.artworks import parse_dxf_polygons, parse_svg_polygons
from auth_helpers import auth_headers


client = TestClient(app)


def test_svg_path_and_circle_are_normalized() -> None:
    content = """
    <svg xmlns="http://www.w3.org/2000/svg" width="200" height="120">
      <path id="cut_path" d="M 0 0 L 120 0 L 120 80 L 0 80 Z"/>
      <circle id="safe_circle" cx="160" cy="60" r="20"/>
    </svg>
    """
    polygons = parse_svg_polygons(content, "art_svg")
    assert len(polygons) == 2
    assert polygons[0].area == 9600
    assert polygons[1].bbox.width == 40


def test_dxf_lwpolyline_is_normalized() -> None:
    content = """
0
SECTION
2
ENTITIES
0
LWPOLYLINE
8
cut
90
4
70
1
10
0
20
0
10
120
20
0
10
120
20
80
10
0
20
80
0
ENDSEC
0
EOF
"""
    polygons = parse_dxf_polygons(content, "art_dxf")
    assert len(polygons) == 1
    assert polygons[0].area == 9600
    assert polygons[0].metadata["source"] == "dxf_lwpolyline"


def test_artwork_upload_parse_writes_polygon_json() -> None:
    headers = auth_headers(client)
    content = b'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><path id="cut" d="M0 0 L120 0 L120 80 L0 80 Z"/></svg>'
    upload = client.post("/api/artworks/upload", files={"file": ("box.svg", content, "image/svg+xml")}, headers=headers)
    assert upload.status_code == 200
    artwork_id = upload.json()["artwork_id"]
    parsed = client.post(f"/api/artworks/{artwork_id}/parse-polygon", headers=headers)
    assert parsed.status_code == 200
    polygon_storage_key = Path(parsed.json()["polygon_storage_key"])
    assert polygon_storage_key.exists()
    assert parsed.json()["polygons"][0]["area"] == 9600
