from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any


MAX_ROUTE_METRICS = 500
OTHER_ROUTE = "__other__"


@dataclass
class _RouteMetric:
    method: str
    route: str
    status_class: str
    count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0
    max_duration_ms: float = 0


_lock = Lock()
_metrics: dict[tuple[str, str, str], _RouteMetric] = {}


def record_request_metric(*, method: str, route: str, status_code: int, duration_ms: float) -> None:
    status_class = f"{status_code // 100}xx"
    key = (method.upper(), route, status_class)
    with _lock:
        if key not in _metrics and len(_metrics) >= MAX_ROUTE_METRICS:
            key = (method.upper(), OTHER_ROUTE, status_class)
        metric = _metrics.setdefault(key, _RouteMetric(method=key[0], route=key[1], status_class=key[2]))
        metric.count += 1
        if status_code >= 500:
            metric.error_count += 1
        metric.total_duration_ms += max(0, duration_ms)
        metric.max_duration_ms = max(metric.max_duration_ms, max(0, duration_ms))


def snapshot_api_metrics() -> dict[str, Any]:
    with _lock:
        route_metrics = list(_metrics.values())
        routes = [
            {
                "method": metric.method,
                "route": metric.route,
                "status_class": metric.status_class,
                "count": metric.count,
                "error_count": metric.error_count,
                "total_duration_ms": round(metric.total_duration_ms, 2),
                "avg_duration_ms": round(metric.total_duration_ms / metric.count, 2) if metric.count else 0,
                "max_duration_ms": round(metric.max_duration_ms, 2),
            }
            for metric in route_metrics
        ]
    total_requests = sum(item["count"] for item in routes)
    error_count = sum(item["error_count"] for item in routes)
    total_duration_ms = round(sum(item["total_duration_ms"] for item in routes), 2)
    return {
        "total_requests": total_requests,
        "error_count": error_count,
        "total_duration_ms": total_duration_ms,
        "avg_duration_ms": round(total_duration_ms / total_requests, 2) if total_requests else 0,
        "routes": sorted(routes, key=lambda item: (item["route"], item["method"], item["status_class"])),
    }


def reset_api_metrics() -> None:
    with _lock:
        _metrics.clear()
