"""Reusable ingestion pipeline.

The HTTP router, the Telegram collector, and the background worker all call
``ingest_post`` so deduplication and extraction cannot drift between entry
points.
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CollectedPost, Opportunity, Source
from app.services.extractor import ExtractedOpportunity
from app.services.llm.base import ExtractionProvider
from app.services.llm.orchestrator import extract_with_provider


class IngestionError(Exception):
    """Base class for expected ingestion failures."""

    code = "ingestion_error"
    status_code = 400


class SourceNotFoundError(IngestionError):
    code = "source_not_found"
    status_code = 404


class SourceInactiveError(IngestionError):
    code = "source_inactive"
    status_code = 409


class DuplicatePostError(IngestionError):
    code = "duplicate_post"
    status_code = 409


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_opportunity(
    post: CollectedPost, extracted: ExtractedOpportunity, extraction_method: str
) -> Opportunity:
    return Opportunity(
        collected_post_id=post.id,
        opportunity_type=extracted.opportunity_type,
        title=extracted.title,
        category=extracted.category,
        skills=extracted.skills,
        budget_amount=extracted.budget_amount,
        budget_currency=extracted.budget_currency,
        duration=extracted.duration,
        remote=extracted.remote,
        description=extracted.description,
        quality_score=extracted.quality_score,
        risk_flags=extracted.risk_flags,
        risk_score=extracted.risk_score,
        review_status="needs_review" if extracted.risk_flags else "pending_review",
        extraction_method=extraction_method,
    )


def ingest_post(
    db: Session,
    *,
    source_id: int,
    external_id: str,
    original_text: str,
    original_url: str | None = None,
    require_active_source: bool = True,
    extracted: ExtractedOpportunity | None = None,
    provider: ExtractionProvider | None = None,
) -> tuple[Opportunity, bool]:
    """Persist one collected post and its opportunity.

    Returns ``(opportunity, created)``. A duplicate ``(source_id, external_id)``
    raises :class:`DuplicatePostError`; the collector catches it and treats the
    post as already handled. The pre-check is only a fast path: the unique
    constraint is the real guarantee, and an IntegrityError is translated into
    the same typed error instead of leaking a 500.
    """

    source = db.get(Source, source_id)
    if source is None:
        raise SourceNotFoundError(f"Source {source_id} not found")
    if require_active_source and not source.is_active:
        raise SourceInactiveError(f"Source {source_id} is inactive")

    existing = db.scalar(
        select(CollectedPost.id).where(
            CollectedPost.source_id == source_id,
            CollectedPost.external_id == external_id,
        )
    )
    if existing is not None:
        raise DuplicatePostError(f"Post {external_id} already ingested for source {source_id}")

    post = CollectedPost(
        source_id=source_id,
        external_id=external_id,
        original_text=original_text,
        original_url=original_url,
        content_hash=content_hash(original_text),
    )
    db.add(post)
    try:
        db.flush()
        if extracted is None:
            extracted, extraction_method = extract_with_provider(
                original_text, provider=provider
            )
        else:
            extraction_method = "rules"
        opportunity = _build_opportunity(post, extracted, extraction_method)
        db.add(opportunity)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicatePostError(
            f"Post {external_id} already ingested for source {source_id}"
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(opportunity)
    try:
        from app.config import settings
        from app.services.alerts import send_opportunity_alert
        send_opportunity_alert(opportunity, settings)
    except Exception:  # noqa: BLE001
        pass
    return opportunity, True
