"""Celery tasks. Thin wrappers around the testable job runner."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import CollectedPost, Opportunity
from app.services.ingestion import DuplicatePostError, ingest_post
from app.services.jobs import JobOutcome, run_job
from app.worker.celery_app import celery_app


def _ingest_work(
    db: Session, *, source_id: int, external_id: str, original_text: str,
    original_url: str | None,
) -> dict:
    try:
        opportunity, created = ingest_post(
            db,
            source_id=source_id,
            external_id=external_id,
            original_text=original_text,
            original_url=original_url,
        )
        return {"opportunity_id": opportunity.id, "created": created}
    except DuplicatePostError:
        # A replay after a transient failure is success, not a new row.
        existing = db.scalar(
            select(Opportunity)
            .join(CollectedPost, Opportunity.collected_post_id == CollectedPost.id)
            .where(
                CollectedPost.source_id == source_id,
                CollectedPost.external_id == external_id,
            )
        )
        if existing is None:
            raise
        return {"opportunity_id": existing.id, "created": False}


def ingest_job(
    db: Session,
    *,
    source_id: int,
    external_id: str,
    original_text: str,
    original_url: str | None = None,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    sleep=None,
) -> JobOutcome:
    key = idempotency_key or f"ingest:{source_id}:{external_id}"
    kwargs = {
        "task_name": "yekis.ingest_post",
        "idempotency_key": key,
        "payload": {
            "source_id": source_id,
            "external_id": external_id,
            "original_url": original_url,
        },
        "fn": lambda: _ingest_work(
            db,
            source_id=source_id,
            external_id=external_id,
            original_text=original_text,
            original_url=original_url,
        ),
        "max_attempts": max_attempts,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return run_job(db, **kwargs)


def collect_job(
    db: Session,
    *,
    source_ids: list[int] | None = None,
    limit: int | None = None,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
) -> JobOutcome:
    from app.integrations.telegram.client import TelethonClient
    from app.integrations.telegram.collector import collect_sources

    key = idempotency_key or f"collect:{datetime.now(UTC).strftime('%Y%m%d%H%M')}"

    async def _collect() -> list[dict]:
        client = TelethonClient()
        try:
            results = await collect_sources(
                db, client, source_ids=source_ids, limit=limit
            )
            return [asdict(result) for result in results]
        finally:
            await client.close()

    def work() -> dict:
        return {"sources": asyncio.run(_collect())}

    return run_job(
        db,
        task_name="yekis.collect_telegram",
        idempotency_key=key,
        fn=work,
        payload={"source_ids": source_ids, "limit": limit},
        max_attempts=max_attempts,
    )


@celery_app.task(bind=True, name="yekis.ingest_post")
def ingest_task(self, payload: dict) -> dict:
    with SessionLocal() as db:
        outcome = ingest_job(
            db,
            source_id=int(payload["source_id"]),
            external_id=str(payload["external_id"]),
            original_text=str(payload["original_text"]),
            original_url=payload.get("original_url"),
            idempotency_key=payload.get("idempotency_key"),
        )
    return {
        "status": outcome.status,
        "attempts": outcome.attempts,
        "value": outcome.value,
        "error": outcome.error,
        "job_id": outcome.job_id,
    }


@celery_app.task(bind=True, name="yekis.collect_telegram")
def collect_telegram_task(
    self, source_ids: list[int] | None = None, limit: int | None = None
) -> dict:
    task_id = getattr(getattr(self, "request", None), "id", None)
    key = f"collect:{task_id}" if task_id else None
    with SessionLocal() as db:
        outcome = collect_job(
            db,
            source_ids=source_ids,
            limit=limit,
            idempotency_key=key,
            max_attempts=settings.celery_task_max_retries,
        )
    return {
        "status": outcome.status,
        "attempts": outcome.attempts,
        "value": outcome.value,
        "error": outcome.error,
        "job_id": outcome.job_id,
    }


@celery_app.task(bind=True, name="yekis.publish_due")
def publish_due_task(self) -> dict:
    from app.integrations.telegram.publisher import TelethonPublisher
    from app.services.publishing import publish_due

    task_id = getattr(getattr(self, "request", None), "id", None)
    key = f"publish-due:{task_id}" if task_id else None

    with SessionLocal() as db:
        def work() -> dict:
            async def _publish():
                publisher = TelethonPublisher()
                try:
                    results = await publish_due(db, publisher=publisher, settings=settings)
                finally:
                    await publisher.close()
                return [
                    {"id": item.id, "status": item.status}
                    for item in results
                ]

            return {"publications": asyncio.run(_publish())}

        outcome = run_job(
            db,
            task_name="yekis.publish_due",
            idempotency_key=key or "publish-due",
            fn=work,
            payload=None,
            max_attempts=1,
        )
    return {
        "status": outcome.status,
        "attempts": outcome.attempts,
        "value": outcome.value,
        "error": outcome.error,
        "job_id": outcome.job_id,
    }
