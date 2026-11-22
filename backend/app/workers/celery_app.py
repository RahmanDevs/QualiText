"""Celery worker configuration with eager-mode support for fast local development."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "qualitext",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="qualitext.ping")
def ping_worker() -> dict[str, str]:
    """Healthcheck task to verify worker execution."""
    return {"status": "pong"}
