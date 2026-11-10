"""Celery application and beat schedule.

The broker is Redis. No task talks to Redis directly; all durable state lives
in PostgreSQL so a lost broker message cannot lose an accepted job.
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "yekis_intelligence",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_soft_time_limit=max(1, settings.celery_task_time_limit_seconds - 30),
    task_time_limit=settings.celery_task_time_limit_seconds,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "collect-telegram": {
        "task": "yekis.collect_telegram",
        "schedule": settings.collect_interval_seconds,
    },
}

__all__ = ["celery_app"]
