"""Idempotent job execution with bounded retries and a dead-letter record.

Celery supplies the broker, scheduling, and worker lifecycle. This module owns
the part that must be provable in tests: one durable execution row per
idempotency key, monotonic attempt counting, exponential backoff with jitter,
and a dead-letter row when retries are exhausted.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import DeadLetterJob, JobExecution


class JobAlreadyRunning(RuntimeError):
    pass


@dataclass
class JobOutcome:
    status: str
    attempts: int
    value: Any = None
    error: str | None = None
    job_id: int | None = None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    return str(value)


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:2000]


def retry_delay(
    attempt: int,
    *,
    base_seconds: int | None = None,
    max_seconds: int | None = None,
    jitter: Callable[[], float] | None = None,
) -> float:
    """Exponential backoff with bounded jitter; attempt starts at 1."""
    base = settings.celery_retry_base_seconds if base_seconds is None else base_seconds
    maximum = settings.celery_retry_max_seconds if max_seconds is None else max_seconds
    delay = min(maximum, base * (2 ** max(0, attempt - 1)))
    jitter_fn = jitter or random.random
    return float(delay + jitter_fn() * max(0.0, delay * 0.1))


def run_job(
    db: Session,
    *,
    task_name: str,
    idempotency_key: str,
    fn: Callable[[], Any],
    payload: dict | None = None,
    max_attempts: int | None = None,
    base_seconds: int | None = None,
    max_seconds: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] | None = None,
) -> JobOutcome:
    """Execute ``fn`` at most once per idempotency key.

    A succeeded or dead-lettered key is returned as-is without invoking ``fn``
    again. A failed key is retried with exponential backoff until
    ``max_attempts`` is exhausted, at which point a DeadLetterJob row is
    written in the same transaction as the terminal status.
    """

    max_attempts = max_attempts or settings.celery_task_max_retries
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    job = db.scalar(
        select(JobExecution).where(JobExecution.idempotency_key == idempotency_key)
    )
    if job is not None:
        if job.status in ("succeeded", "dead_lettered"):
            return JobOutcome(
                status=job.status,
                attempts=job.attempts,
                value=job.result,
                error=job.last_error,
                job_id=job.id,
            )
        if job.status == "running":
            raise JobAlreadyRunning(f"Job {idempotency_key} is already running")
    else:
        job = JobExecution(
            task_name=task_name,
            idempotency_key=idempotency_key,
            status="pending",
            attempts=0,
            max_attempts=max_attempts,
            payload=_jsonable(payload),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    job.max_attempts = max_attempts
    while job.attempts < max_attempts:
        job.attempts += 1
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        job.last_error = None
        db.commit()

        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - recorded as job state
            db.rollback()
            job = db.get(JobExecution, job.id)
            if job is None:
                raise
            job.attempts = max(job.attempts, 0)
            job.last_error = _error_text(exc)
            job.updated_at = datetime.now(UTC)
            if job.attempts >= max_attempts:
                job.status = "dead_lettered"
                job.finished_at = datetime.now(UTC)
                db.add(
                    DeadLetterJob(
                        job_execution_id=job.id,
                        task_name=task_name,
                        idempotency_key=idempotency_key,
                        payload=_jsonable(payload),
                        error=job.last_error,
                        attempts=job.attempts,
                    )
                )
                db.commit()
                return JobOutcome(
                    status="dead_lettered",
                    attempts=job.attempts,
                    error=job.last_error,
                    job_id=job.id,
                )
            job.status = "failed"
            db.commit()
            if job.attempts < max_attempts:
                sleep(
                    retry_delay(
                        job.attempts,
                        base_seconds=base_seconds,
                        max_seconds=max_seconds,
                        jitter=jitter,
                    )
                )
            continue

        job = db.get(JobExecution, job.id)
        if job is None:
            raise RuntimeError("job row disappeared during execution")
        job.status = "succeeded"
        job.result = _jsonable(value)
        job.finished_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        db.commit()
        return JobOutcome(
            status="succeeded",
            attempts=job.attempts,
            value=job.result,
            job_id=job.id,
        )

    return JobOutcome(
        status="dead_lettered",
        attempts=job.attempts,
        error=job.last_error,
        job_id=job.id,
    )
