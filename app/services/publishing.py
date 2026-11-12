"""Approval-gated publication.

Hard rules:
- only an opportunity whose ``review_status`` is ``approved`` may be published;
- publishing must be explicitly enabled and the destination must be allowlisted;
- the publication row is committed as ``sending`` before the network call, and a
  row left in ``sending`` is never retried automatically (it may already have
  been posted; a human must reconcile it);
- an unapproved or rejected opportunity is re-checked at send time, not only at
  scheduling time.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings as default_settings
from app.integrations.telegram.channel import InvalidChannelReference, channel_reference
from app.integrations.telegram.publisher import PublisherPort, TelethonPublisher
from app.models import Opportunity, Publication
from app.security.auth import Principal
from app.services.formatter import FormattingError, format_publication

logger = logging.getLogger(__name__)


class PublishingError(Exception):
    code = "publishing_error"
    status_code = 400


class OpportunityNotFound(PublishingError):
    code = "opportunity_not_found"
    status_code = 404


class PublishingDisabled(PublishingError):
    code = "publishing_disabled"
    status_code = 409


class NotApproved(PublishingError):
    code = "not_approved"
    status_code = 409


class ChannelNotAllowed(PublishingError):
    code = "channel_not_allowed"
    status_code = 403


class PublicationUnknown(PublishingError):
    code = "publication_unknown"
    status_code = 409


class PublicationExhausted(PublishingError):
    code = "publication_exhausted"
    status_code = 409


class PublicationFailed(PublishingError):
    code = "publication_failed"
    status_code = 502


class PublicationNotDue(PublishingError):
    code = "publication_not_due"
    status_code = 409


class InvalidSchedule(PublishingError):
    code = "invalid_schedule"
    status_code = 422


class FormatFailed(PublishingError):
    code = "format_failed"
    status_code = 422


def get_publisher() -> PublisherPort:
    return TelethonPublisher()


async def publisher_session() -> AsyncIterator[PublisherPort]:
    publisher = get_publisher()
    try:
        yield publisher
    finally:
        await publisher.close()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def allowed_channels(settings=default_settings) -> set[str]:
    result: set[str] = set()
    raw = getattr(settings, "allowed_publish_channels", None)
    if raw is None:
        raw = settings.publishing_allowed_channels
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    for channel in raw:
        try:
            result.add(channel_reference(channel))
        except InvalidChannelReference:
            logger.warning("ignoring invalid PUBLISHING_ALLOWED_CHANNELS entry")
    return result


def validate_channel(channel: str, settings=default_settings) -> str:
    try:
        reference = channel_reference(channel)
    except InvalidChannelReference as exc:
        raise ChannelNotAllowed(str(exc)) from exc
    if reference not in allowed_channels(settings):
        raise ChannelNotAllowed(
            f"channel {reference!r} is not in PUBLISHING_ALLOWED_CHANNELS"
        )
    return reference


def _load_opportunity(db: Session, opportunity_id: int) -> Opportunity:
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise OpportunityNotFound("Opportunity not found")
    return opportunity


def create_or_get_publication(
    db: Session,
    *,
    opportunity: Opportunity,
    channel: str,
    principal: Principal,
    settings=default_settings,
    scheduled_at: datetime | None = None,
    now: datetime | None = None,
) -> Publication:
    if not settings.publishing_enabled:
        raise PublishingDisabled("publishing is disabled")
    if opportunity.review_status != "approved":
        raise NotApproved("only an approved opportunity can be published")
    reference = validate_channel(channel, settings)
    now = now or datetime.now(UTC)

    if scheduled_at is not None:
        scheduled_at = _utc(scheduled_at)
        if scheduled_at <= now:
            raise InvalidSchedule("scheduled_at must be in the future")

    existing = db.scalar(
        select(Publication).where(
            Publication.opportunity_id == opportunity.id,
            Publication.channel == reference,
        )
    )
    if existing is not None:
        if existing.status == "published":
            return existing
        if existing.status == "sending":
            raise PublicationUnknown(
                "a previous attempt may already have posted; reconcile manually"
            )
        max_attempts = settings.publishing_max_attempts
        if existing.status == "failed" and existing.attempts >= max_attempts:
            existing.attempts = 0
        if scheduled_at is not None:
            existing.status = "scheduled"
            existing.scheduled_at = scheduled_at
        else:
            existing.status = "pending"
            existing.scheduled_at = None
        existing.error = None
        existing.updated_at = now
        db.commit()
        db.refresh(existing)
        return existing

    publication = Publication(
        opportunity_id=opportunity.id,
        channel=reference,
        status="scheduled" if scheduled_at is not None else "pending",
        scheduled_at=scheduled_at,
        attempts=0,
        created_by=principal.name,
        created_at=now,
        updated_at=now,
    )
    db.add(publication)
    db.commit()
    db.refresh(publication)
    return publication


async def publish_publication(
    db: Session,
    publication: Publication,
    *,
    publisher: PublisherPort,
    principal: Principal | None = None,
    settings=default_settings,
    now: datetime | None = None,
    force: bool = False,
) -> Publication:
    if not settings.publishing_enabled:
        raise PublishingDisabled("publishing is disabled")

    now = now or datetime.now(UTC)
    publication = db.get(Publication, publication.id)
    if publication is None:
        raise PublicationFailed("publication row disappeared")
    if publication.status == "published":
        return publication
    if publication.status == "sending":
        raise PublicationUnknown("a previous attempt may already have posted")

    opportunity = _load_opportunity(db, publication.opportunity_id)
    if opportunity.review_status != "approved":
        raise NotApproved("opportunity is no longer approved")
    validate_channel(publication.channel, settings)

    scheduled_at = _utc(publication.scheduled_at)
    if scheduled_at is not None and scheduled_at > now and not force:
        raise PublicationNotDue("publication is scheduled for later")

    max_attempts = settings.publishing_max_attempts
    if publication.attempts >= max_attempts:
        raise PublicationExhausted("publication attempts are exhausted")

    source = opportunity.post.source if opportunity.post else None
    try:
        text = format_publication(opportunity, source, settings=settings)
    except FormattingError as exc:
        publication.status = "failed"
        publication.error = f"FormattingError: {exc}"[:2000]
        publication.updated_at = now
        db.commit()
        raise FormatFailed(str(exc)) from exc

    publication.status = "sending"
    publication.attempts += 1
    publication.error = None
    publication.updated_at = now
    db.commit()

    try:
        reference = await publisher.send_message(publication.channel, text)
    except Exception as exc:  # noqa: BLE001 - normalized into publication state
        publication = db.get(Publication, publication.id)
        if publication is None:
            raise PublicationFailed("publication row disappeared during send") from exc
        publication.status = "failed"
        publication.error = f"{type(exc).__name__}: {exc}"[:2000]
        publication.updated_at = datetime.now(UTC)
        db.commit()
        logger.warning(
            "publication failed id=%s opportunity_id=%s attempts=%s",
            publication.id,
            publication.opportunity_id,
            publication.attempts,
        )
        raise PublicationFailed(
            "telegram send failed; see the publication record for the error"
        ) from exc

    publication = db.get(Publication, publication.id)
    if publication is None:
        raise PublicationFailed("publication row disappeared after send")
    publication.status = "published"
    publication.external_message_id = reference.external_message_id
    publication.published_at = datetime.now(UTC)
    publication.error = None
    publication.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(publication)
    logger.info(
        "published opportunity_id=%s publication_id=%s channel=%s",
        publication.opportunity_id,
        publication.id,
        publication.channel,
    )
    return publication


async def publish_opportunity(
    db: Session,
    *,
    opportunity_id: int,
    channel: str,
    principal: Principal,
    publisher: PublisherPort,
    settings=default_settings,
    scheduled_at: datetime | None = None,
    now: datetime | None = None,
) -> Publication:
    opportunity = _load_opportunity(db, opportunity_id)
    publication = create_or_get_publication(
        db,
        opportunity=opportunity,
        channel=channel,
        principal=principal,
        settings=settings,
        scheduled_at=scheduled_at,
        now=now,
    )
    if publication.status in ("scheduled", "published"):
        return publication
    return await publish_publication(
        db, publication, publisher=publisher, principal=principal, settings=settings, now=now
    )


async def publish_due(
    db: Session,
    *,
    publisher: PublisherPort,
    settings=default_settings,
    now: datetime | None = None,
    limit: int = 50,
) -> list[Publication]:
    if not settings.publishing_enabled:
        return []
    now = now or datetime.now(UTC)
    statement = (
        select(Publication)
        .where(Publication.status.in_(("scheduled", "pending")))
        .where(
            or_(
                Publication.scheduled_at.is_(None),
                Publication.scheduled_at <= now,
            )
        )
        .order_by(Publication.id)
        .limit(limit)
    )
    due = list(db.scalars(statement).all())
    results: list[Publication] = []
    for publication in due:
        try:
            results.append(
                await publish_publication(
                    db,
                    publication,
                    publisher=publisher,
                    settings=settings,
                    now=now,
                )
            )
        except PublishingError as exc:
            logger.warning(
                "scheduled publication skipped id=%s code=%s",
                publication.id,
                exc.code,
            )
    return results
