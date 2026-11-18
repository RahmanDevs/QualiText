"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, curation, datasets, runs, tokens, workspaces
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(log_level=settings.log_level, log_format=settings.log_format)
logger = get_logger(__name__)


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
    version="0.1.0",
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

register_exception_handlers(app)

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(auth.router)
api_v1.include_router(workspaces.router)
api_v1.include_router(datasets.router)
api_v1.include_router(runs.router)
api_v1.include_router(curation.router)
api_v1.include_router(tokens.router)
app.include_router(api_v1)


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/readyz", tags=["system"])
async def readyz() -> dict[str, str]:
    """Readiness probe."""
    return {"status": "ready"}
