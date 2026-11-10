"""Authorized Telegram channel collector (M1).

Safety boundaries:
- Only sources already registered as ``source_type=telegram`` and active.
- Never joins a channel and never reads private chats.
- Deduplicates through the database's ``(source_id, external_id)`` constraint.
- Logs counts and ids only; message text and credentials are never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as default_settings
from app.db import SessionLocal
from app.integrations.telegram.base import ChannelMessage, TelegramClientPort
from app.models import Source
from app.services.ingestion import DuplicatePostError, IngestionError, ingest_post

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(
    consecutive_failures: int, *, base: int = 30, maximum: int = 3600
) -> int:
    """Exponential backoff with bounded jitter, never below the base."""
    exponent = max(1, min(consecutive_failures, 10))
    delay = min(maximum, base * (2 ** (exponent - 1)))
    return delay + random.randint(0, max(1, delay // 10))


@dataclass
class CollectorResult:
    source_id: int
    status: str
    fetched: int = 0
    ingested: int = 0
    duplicates: int = 0
    last_message_id: int | None = None
    error: str | None = None
    skipped_reason: str | None = None
    message_ids: list[int] = field(default_factory=list)


def _error_text(exc: BaseException) -> str:
    # Exception text could contain a token in pathological cases; keep the
    # stored error bounded and never include request headers/sessions.
    return f"{type(exc).__name__}: {exc}"[:500]


async def collect_source(
    db: Session,
    source: Source,
    client: TelegramClientPort,
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> CollectorResult:
    now = now or utcnow()
    fetch_limit = limit or default_settings.telegram_fetch_limit

    if source.source_type != "telegram":
        return CollectorResult(source.id, "skipped", skipped_reason="not_a_telegram_source")
    if not source.is_active:
        return CollectorResult(source.id, "skipped", skipped_reason="source_inactive")
    if source.next_attempt_at is not None:
        next_attempt = source.next_attempt_at
        if next_attempt.tzinfo is None:
            next_attempt = next_attempt.replace(tzinfo=UTC)
        if now < next_attempt:
            return CollectorResult(source.id, "skipped", skipped_reason="backoff")

    source_id = source.id
    min_id = source.last_collected_message_id or 0
    source.last_attempt_at = now
    db.commit()

    try:
        messages: list[ChannelMessage] = await client.fetch_messages(
            source.source_url, min_id=min_id, limit=fetch_limit
        )
        result = CollectorResult(source_id, "collected", fetched=len(messages))
        highest = min_id
        for message in messages:
            highest = max(highest, message.message_id)
            if message.message_id <= min_id:
                result.duplicates += 1
                continue
            try:
                ingest_post(
                    db,
                    source_id=source_id,
                    external_id=str(message.message_id),
                    original_text=message.text,
                    original_url=message.url,
                )
                result.ingested += 1
                result.message_ids.append(message.message_id)
            except DuplicatePostError:
                result.duplicates += 1
            except IngestionError as exc:
                # A single bad message must not advance the checkpoint past it.
                raise RuntimeError(
                    f"ingestion rejected message {message.message_id}: {exc}"
                ) from exc

        refreshed = db.get(Source, source_id)
        if refreshed is None:
            raise RuntimeError(f"source {source_id} disappeared during collection")
        refreshed.last_collected_message_id = highest
        refreshed.last_success_at = now
        refreshed.next_attempt_at = None
        refreshed.consecutive_failures = 0
        refreshed.last_error = None
        db.commit()
        result.last_message_id = highest
        logger.info(
            "telegram collect source_id=%s fetched=%s ingested=%s duplicates=%s last_id=%s",
            source_id,
            result.fetched,
            result.ingested,
            result.duplicates,
            highest,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - recorded as source health, then reported
        db.rollback()
        refreshed = db.get(Source, source_id)
        if refreshed is not None:
            refreshed.consecutive_failures = (refreshed.consecutive_failures or 0) + 1
            refreshed.last_attempt_at = now
            refreshed.last_error = _error_text(exc)
            delay = backoff_seconds(refreshed.consecutive_failures)
            refreshed.next_attempt_at = now + timedelta(seconds=delay)
            db.commit()
        logger.warning(
            "telegram collect failed source_id=%s failures=%s error=%s",
            source_id,
            getattr(refreshed, "consecutive_failures", None),
            _error_text(exc),
        )
        return CollectorResult(source_id, "failed", error=_error_text(exc))


async def collect_sources(
    db: Session,
    client: TelegramClientPort,
    *,
    source_ids: list[int] | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[CollectorResult]:
    statement = select(Source).where(
        Source.source_type == "telegram", Source.is_active.is_(True)
    ).order_by(Source.id)
    if source_ids:
        statement = statement.where(Source.id.in_(source_ids))
    sources = list(db.scalars(statement).all())
    return [
        await collect_source(db, source, client, limit=limit, now=now)
        for source in sources
    ]


async def _run_cli(source_ids: list[int] | None, limit: int | None) -> int:
    from app.integrations.telegram.client import TelethonClient

    if not default_settings.telegram_ready:
        print(
            "Telegram is not configured. Set TELEGRAM_ENABLED=true, TELEGRAM_API_ID, "
            "TELEGRAM_API_HASH, and authorize a session first.",
            file=sys.stderr,
        )
        return 2
    client = TelethonClient()
    try:
        with SessionLocal() as db:
            results = await collect_sources(db, client, source_ids=source_ids, limit=limit)
    finally:
        await client.close()
    print(json.dumps([asdict(result) for result in results], indent=2, default=str))
    return 0 if all(result.status != "failed" for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Collect new posts from authorized Telegram channels"
    )
    parser.add_argument("--source-id", type=int, action="append", dest="source_ids")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=default_settings.log_level.upper())
    return asyncio.run(_run_cli(args.source_ids, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
