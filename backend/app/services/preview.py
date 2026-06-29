from __future__ import annotations

from html import escape

from app.domain.schemas import NestingJob, NestingSolution
from app.services.geometry import gripper_area_polygon, printable_area_polygon


def _polygon_points(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{round(x, 2)},{round(y, 2)}" for x, y in points)


def generate_solution_svg(job: NestingJob, solution: NestingSolution) -> str:
    printable = printable_area_polygon(job.sheet)
    gripper = gripper_area_polygon(job.sheet)
    width = job.sheet.width
    height = job.sheet.height
    pieces: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}mm" height="{height}mm">',
        '<rect x="0" y="0" width="100%" height="100%" fill="#f8fafc" stroke="#0f172a" stroke-width="1"/>',
        f'<polygon points="{_polygon_points(printable.outer)}" fill="#ecfdf5" stroke="#16a34a" stroke-width="0.8"/>',
        f'<polygon points="{_polygon_points(gripper.outer)}" fill="#fee2e2" stroke="#ef4444" stroke-width="0.8"/>',
    ]
    palette = ["#dbeafe", "#fef3c7", "#fce7f3", "#ede9fe", "#cffafe", "#dcfce7"]
    for index, placement in enumerate(solution.placed_items):
        if not placement.polygon:
            continue
        color = palette[index % len(palette)]
        label = escape(f"{placement.order_id} r{placement.rotation}")
        points = _polygon_points(placement.polygon.outer)
        pieces.append(f'<polygon points="{points}" fill="{color}" stroke="#334155" stroke-width="0.8"/>')
        pieces.append(
            f'<text x="{placement.x + 2}" y="{placement.y + 8}" font-size="8" font-family="Arial" fill="#0f172a">{label}</text>'
        )
    for index, item in enumerate(solution.unplaced_items):
        pieces.append(
            f'<text x="8" y="{height - 12 - index * 10}" font-size="8" font-family="Arial" fill="#b91c1c">'
            f'{escape(item.order_id or item.item_id)}: {escape(item.reason)}</text>'
        )
    pieces.append("</svg>")
    return "\n".join(pieces)

