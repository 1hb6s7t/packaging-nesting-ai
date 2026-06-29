from dataclasses import dataclass
import sys
from pathlib import Path

from fastapi.routing import APIRoute

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.api.router import api_router


PUBLIC_ROUTES = {
    ("GET", "/health"),
    ("GET", "/health/ready"),
    ("POST", "/auth/login"),
    ("POST", "/artworks/preflight"),
    ("GET", "/ai/tools"),
}
TOKEN_AUTH_ROUTES = {
    ("POST", "/artworks/conversion-jobs/{job_id}/callback"),
}
AUTH_DEPENDENCY_NAMES = {
    "app.services.security.get_current_user",
    "app.services.security.dependency",
}


@dataclass(frozen=True)
class RouteAuthSurface:
    method: str
    path: str
    dependencies: set[str]


def test_business_routes_require_bearer_or_declared_callback_token_auth() -> None:
    routes = _flatten_api_routes()
    unprotected = [
        route
        for route in routes
        if (route.method, route.path) not in PUBLIC_ROUTES
        and (route.method, route.path) not in TOKEN_AUTH_ROUTES
        and route.dependencies.isdisjoint(AUTH_DEPENDENCY_NAMES)
    ]
    assert unprotected == []


def test_anonymous_route_allowlist_is_explicit() -> None:
    routes = _flatten_api_routes()
    anonymous = {
        (route.method, route.path)
        for route in routes
        if route.dependencies.isdisjoint(AUTH_DEPENDENCY_NAMES)
    }
    assert anonymous == PUBLIC_ROUTES | TOKEN_AUTH_ROUTES


def _flatten_api_routes() -> list[RouteAuthSurface]:
    flattened: list[RouteAuthSurface] = []
    _walk_routes(api_router.routes, prefix="", output=flattened)
    return sorted(flattened, key=lambda item: (item.path, item.method))


def _walk_routes(routes: list, *, prefix: str, output: list[RouteAuthSurface]) -> None:
    for route in routes:
        if isinstance(route, APIRoute):
            dependencies = {
                f"{getattr(dependency.call, '__module__', '')}.{getattr(dependency.call, '__name__', '')}"
                for dependency in route.dependant.dependencies
            }
            for method in route.methods:
                output.append(RouteAuthSurface(method=method, path=f"{prefix}{route.path}", dependencies=dependencies))
            continue
        original_router = getattr(route, "original_router", None)
        include_context = getattr(route, "include_context", None)
        if original_router is not None and include_context is not None:
            _walk_routes(original_router.routes, prefix=f"{prefix}{include_context.prefix}", output=output)
