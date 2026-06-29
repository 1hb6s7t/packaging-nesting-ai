from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.schemas import NestingJob, NestingSolution, PolygonAsset, PreflightReport, ProductionOrder, SheetSpec


@dataclass
class AppStore:
    orders: dict[str, ProductionOrder] = field(default_factory=dict)
    sheets: dict[str, SheetSpec] = field(default_factory=dict)
    artworks: dict[str, dict] = field(default_factory=dict)
    polygons: dict[str, list[PolygonAsset]] = field(default_factory=dict)
    preflight_reports: dict[str, PreflightReport] = field(default_factory=dict)
    jobs: dict[str, NestingJob] = field(default_factory=dict)
    solutions: dict[str, NestingSolution] = field(default_factory=dict)
    job_solutions: dict[str, list[str]] = field(default_factory=dict)


store = AppStore()

