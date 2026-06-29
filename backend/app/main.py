from contextlib import asynccontextmanager
import logging
import re
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import Settings, assert_production_security_settings, get_settings
from app.core.logging import configure_logging
from app.db.session import init_db
from app.services import api_metrics


REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
request_logger = logging.getLogger("app.request")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    assert_production_security_settings(settings)
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    add_request_context_middleware(app)
    add_security_headers_middleware(app, settings)
    add_error_handlers(app, settings)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


def add_request_context_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context_middleware(request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            api_metrics.record_request_metric(
                method=request.method,
                route=request_route_template(request),
                status_code=500,
                duration_ms=duration_ms,
            )
            request_logger.exception(
                "request failed request_id=%s method=%s path=%s duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        api_metrics.record_request_metric(
            method=request.method,
            route=request_route_template(request),
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        request_logger.info(
            "request completed request_id=%s method=%s path=%s status_code=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


def request_route_template(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return request.url.path
    return route_template_with_request_prefix(
        path=request.url.path,
        template=str(template),
        path_params=request.path_params,
    )


def route_template_with_request_prefix(path: str, template: str, path_params: dict[str, Any]) -> str:
    concrete_template = template
    for name, value in path_params.items():
        concrete_template = concrete_template.replace(f"{{{name}}}", str(value))
    if concrete_template and path.endswith(concrete_template):
        prefix = path[: -len(concrete_template)]
        if prefix:
            return f"{prefix.rstrip('/')}{template}"
    return template


def normalize_request_id(value: str | None) -> str:
    candidate = (value or "").strip()
    if candidate and REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def add_security_headers_middleware(app: FastAPI, settings: Settings) -> None:
    if not settings.security_headers_enabled:
        return

    @app.middleware("http")
    async def security_headers_middleware(request, call_next):
        response = await call_next(request)
        for name, value in security_response_headers(settings).items():
            if name not in response.headers:
                response.headers[name] = value
        return response


def add_error_handlers(app: FastAPI, settings: Settings) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return error_response(
            request,
            settings,
            status_code=exc.status_code,
            detail=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        return error_response(
            request,
            settings,
            status_code=422,
            detail=jsonable_encoder(exc.errors()),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_logger.exception(
            "unhandled exception request_id=%s method=%s path=%s",
            request_id_from_state_or_header(request),
            request.method,
            request.url.path,
        )
        return error_response(
            request,
            settings,
            status_code=500,
            detail="internal server error",
        )


def error_response(
    request: Request,
    settings: Settings,
    *,
    status_code: int,
    detail: Any,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_from_state_or_header(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    for name, value in security_response_headers(settings).items():
        response_headers.setdefault(name, value)
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "request_id": request_id},
        headers=response_headers,
    )


def request_id_from_state_or_header(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return normalize_request_id(request_id or request.headers.get(REQUEST_ID_HEADER))


def security_response_headers(settings: Settings) -> dict[str, str]:
    if not settings.security_headers_enabled:
        return {}
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if settings.security_hsts_enabled:
        headers["Strict-Transport-Security"] = f"max-age={settings.security_hsts_max_age_sec}"
    return headers


app = create_app()
