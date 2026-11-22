"""FastAPI application entrypoint with OpenTelemetry tracing, Prometheus metrics, and probes."""

import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import (
    auth,
    benchmarks,
    connectors,
    curation,
    datasets,
    runs,
    tokens,
    webhooks,
    workspaces,
)
from app.core.config import get_settings
from app.core.deps import reset_api_token_scopes
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.observability import get_tracer, record_http_request, render_prometheus_metrics
from app.db.session import get_db
from app.storage.s3 import LocalStorageBackend, get_storage_backend

settings = get_settings()
configure_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = get_logger(__name__)
tracer = get_tracer(settings.otel_service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown lifecycle hooks."""
    logger.info(
        "qualitext_api_starting",
        env=settings.app_env,
        storage_backend=settings.storage_backend,
        allow_external_llm=settings.allow_external_llm,
    )
    yield
    logger.info("qualitext_api_stopping")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="QualiText — Platform Data Quality untuk NLP & LLM",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def observability_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach request ID, OpenTelemetry span, Prometheus metrics, and reset auth context."""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.monotonic()
    status_code = 500
    try:
        with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
            span.set_attribute("http.method", request.method)
            span.set_attribute("http.target", request.url.path)
            span.set_attribute("qualitext.request_id", request_id)
            response = await call_next(request)
            status_code = response.status_code
            span.set_attribute("http.status_code", status_code)
            response.headers["X-Request-ID"] = request_id
            return response
    finally:
        reset_api_token_scopes()
        elapsed = time.monotonic() - start
        route = request.scope.get("route")
        path_label = str(route.path) if route is not None else "__unmatched__"
        record_http_request(request.method, path_label, status_code, elapsed)


register_exception_handlers(app)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(workspaces.router)
api_v1.include_router(datasets.router)
api_v1.include_router(runs.router)
api_v1.include_router(curation.router)
api_v1.include_router(tokens.router)
api_v1.include_router(webhooks.router)
api_v1.include_router(connectors.router)
api_v1.include_router(benchmarks.router)
app.include_router(api_v1)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/readyz", tags=["system"], response_model=None)
async def readyz(db: AsyncSession = Depends(get_db)) -> dict[str, object] | JSONResponse:
    """Readiness probe validating Database, Object Storage, and Redis connectivity."""
    cfg = get_settings()
    checks: dict[str, str] = {}
    all_ok = True

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
        all_ok = False

    try:
        storage = get_storage_backend()
        if isinstance(storage, LocalStorageBackend):
            storage.root.mkdir(parents=True, exist_ok=True)
            if not os.access(storage.root, os.R_OK | os.W_OK):
                raise PermissionError("Local storage directory not writable")
        else:
            storage.exists("__readyz_probe__")
        checks["storage"] = "ok"
    except Exception:
        checks["storage"] = "error"
        all_ok = False

    if cfg.celery_task_always_eager:
        checks["redis"] = "skipped_eager"
    else:
        try:
            rclient = aioredis.from_url(cfg.redis_url, socket_connect_timeout=1.5)
            try:
                await rclient.ping()
                checks["redis"] = "ok"
            finally:
                await rclient.aclose()
        except Exception:
            checks["redis"] = "error"
            all_ok = False

    if not all_ok:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": checks},
        )
    return {"status": "ready", "checks": checks}


@app.get("/metrics", tags=["system"])
async def prometheus_metrics() -> Response:
    """Expose Prometheus runtime and quality pipeline metrics."""
    return Response(
        content=render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
