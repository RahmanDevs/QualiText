"""Celery task definitions and Redis progress publishing for quality runs."""

import asyncio
import json
from typing import Any

import redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.workers.celery_app import celery_app

RUN_PROGRESS_CHANNEL_PREFIX = "qualitext:run:"
logger = get_logger(__name__)


def publish_run_progress(payload: dict[str, Any]) -> None:
    """Publish quality run progress event to Redis pub/sub when worker mode is active."""
    settings = get_settings()
    if settings.celery_task_always_eager:
        return
    channel = f"{RUN_PROGRESS_CHANNEL_PREFIX}{payload['run_id']}"
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            client.publish(channel, json.dumps(payload))
        finally:
            client.close()
    except Exception as exc:
        logger.warning(
            "run_progress_publish_failed",
            run_id=payload.get("run_id"),
            error_type=type(exc).__name__,
        )


async def _run_in_worker(run_id: str) -> dict[str, Any]:
    from app.services.run_service import execute_run_by_id

    async with AsyncSessionLocal() as db:
        run = await execute_run_by_id(db, run_id)
        return {
            "run_id": run.id,
            "status": run.status,
            "dqi_total": run.dqi_total,
        }


@celery_app.task(name="qualitext.execute_quality_run")
def execute_quality_run_task(run_id: str) -> dict[str, Any]:
    """Execute a queued QualityRun asynchronously inside the Celery worker."""
    return asyncio.run(_run_in_worker(run_id))
