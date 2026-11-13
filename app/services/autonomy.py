"""Autonomous approval and publishing.

This service is the only place where an approval can be granted without a
human. It is deliberately conservative:

* it is **off by default** and the default mode is ``shadow``, where every
  decision is recorded but nothing changes;
* an automatic approval requires a publishable classification, a confidence at
  or above the configured threshold, a clean deterministic risk assessment, an
  approved source, an approved category, no duplicate, and valid content;
* every automatic decision is written to an append-only decision log and to
  the review audit trail;
* publishing re-checks eligibility immediately before the message is sent.

The service never touches marketplace finances. It only publishes a formatted
opportunity to a Telegram channel that the owner has explicitly allowlisted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings as default_settings
from app.models import (
    AutonomyDecision,
    AutonomySettings,
    CollectedPost,
    Opportunity,
    Publication,
    ReviewEvent,
    Source,
)
from app.services.classification.pipeline import (
    DECISION_AUTO_APPROVE,
    DECISION_AUTO_REJECT,
    DECISION_QUARANTINE,
    ClassificationResult,
    classify_post,
)
from app.services.classification.taxonomy import (
    AUTONOMY_MODES,
    DEFAULT_AUTO_APPROVAL_THRESHOLD,
    PUBLISHABLE_SET,
    SYSTEM_ACTOR,
)

logger = logging.getLogger(__name__)

MODE_SHADOW = "shadow"
MODE_SUPERVISED = "supervised"
MODE_PRODUCTION = "production"

#: Decision labels written to the append-only decision log.
DECISION_AUTO_APPROVED = "auto_approved"
DECISION_AUTO_REJECTED = "auto_rejected"
DECISION_HUMAN_REVIEW_REQUIRED = "human_review_required"
DECISION_QUARANTINED = "quarantined"
DECISION_WOULD_AUTO_APPROVE = "would_auto_approve"
DECISION_WOULD_AUTO_REJECT = "would_auto_reject"
DECISION_PUBLISH_SKIPPED = "publish_skipped"
DECISION_AUTO_PUBLISHED = "auto_published"

#: Minimum length of a post that may be published automatically.
MIN_CONTENT_LENGTH = 40


@dataclass(frozen=True)
class AutonomyConfig:
    """Effective autonomy configuration."""

    enabled: bool = False
    mode: str = MODE_SHADOW
    threshold: float = DEFAULT_AUTO_APPROVAL_THRESHOLD
    approved_source_ids: tuple[int, ...] = ()
    approved_categories: tuple[str, ...] = ()
    daily_publish_limit: int = 5
    min_interval_seconds: int = 900
    pilot_channel: str | None = None
    target_channel: str | None = None
    source: str = "environment"

    @property
    def is_shadow(self) -> bool:
        return self.mode == MODE_SHADOW

    @property
    def publishes(self) -> bool:
        """True only when the configuration may actually publish something."""
        return self.enabled and self.mode in (MODE_SUPERVISED, MODE_PRODUCTION)

    @property
    def channel(self) -> str | None:
        if self.mode == MODE_SUPERVISED:
            return self.pilot_channel
        if self.mode == MODE_PRODUCTION:
            return self.target_channel
        return None


@dataclass
class AutonomyOutcome:
    """Result of evaluating one opportunity."""

    decision: str
    reason: str
    classification: ClassificationResult | None = None
    applied: bool = False
    details: dict = field(default_factory=dict)


# --- Configuration ---------------------------------------------------------


def resolve_config(db: Session, settings=default_settings) -> AutonomyConfig:
    """Resolve the effective configuration.

    A dashboard override in ``autonomy_settings`` always wins over the
    environment defaults.
    """
    row = db.get(AutonomySettings, 1)
    if row is None:
        return AutonomyConfig(
            enabled=bool(settings.autonomy_enabled),
            mode=(settings.autonomy_mode or MODE_SHADOW).strip().lower(),
            threshold=float(settings.autonomy_auto_approval_threshold),
            approved_source_ids=tuple(settings.autonomy_source_id_list),
            approved_categories=tuple(settings.autonomy_category_list),
            daily_publish_limit=int(settings.autonomy_daily_publish_limit),
            min_interval_seconds=int(settings.autonomy_min_interval_seconds),
            pilot_channel=settings.autonomy_pilot_channel or None,
            target_channel=settings.autonomy_target_channel or None,
            source="environment",
        )
    return AutonomyConfig(
        enabled=bool(row.enabled),
        mode=(row.mode or MODE_SHADOW).strip().lower(),
        threshold=float(row.auto_approval_threshold),
        approved_source_ids=tuple(int(value) for value in (row.approved_source_ids or [])),
        approved_categories=tuple(
            str(value).lower() for value in (row.approved_categories or [])
        ),
        daily_publish_limit=int(row.daily_publish_limit),
        min_interval_seconds=int(row.min_interval_seconds),
        pilot_channel=row.pilot_channel,
        target_channel=row.target_channel,
        source="database",
    )


def update_config(
    db: Session, updates: dict, *, actor: str, settings=default_settings
) -> AutonomyConfig:
    """Persist a dashboard override and return the new configuration."""
    current = resolve_config(db, settings)
    row = db.get(AutonomySettings, 1)
    if row is None:
        row = AutonomySettings(
            id=1,
            enabled=current.enabled,
            mode=current.mode,
            auto_approval_threshold=current.threshold,
            approved_source_ids=list(current.approved_source_ids),
            approved_categories=list(current.approved_categories),
            daily_publish_limit=current.daily_publish_limit,
            min_interval_seconds=current.min_interval_seconds,
            pilot_channel=current.pilot_channel,
            target_channel=current.target_channel,
        )
        db.add(row)

    if "enabled" in updates:
        row.enabled = bool(updates["enabled"])
    if "mode" in updates:
        mode = str(updates["mode"]).strip().lower()
        if mode not in AUTONOMY_MODES:
            raise ValueError(f"mode must be one of {AUTONOMY_MODES}")
        row.mode = mode
    if "auto_approval_threshold" in updates:
        value = float(updates["auto_approval_threshold"])
        if not 0.5 <= value <= 1.0:
            raise ValueError("auto_approval_threshold must be between 0.5 and 1.0")
        row.auto_approval_threshold = value
    if "approved_source_ids" in updates:
        row.approved_source_ids = [int(value) for value in updates["approved_source_ids"]]
    if "approved_categories" in updates:
        row.approved_categories = [
            str(value).strip().lower() for value in updates["approved_categories"]
        ]
    if "daily_publish_limit" in updates:
        value = int(updates["daily_publish_limit"])
        if value < 0:
            raise ValueError("daily_publish_limit must not be negative")
        row.daily_publish_limit = value
    if "min_interval_seconds" in updates:
        value = int(updates["min_interval_seconds"])
        if value < 0:
            raise ValueError("min_interval_seconds must not be negative")
        row.min_interval_seconds = value
    if "pilot_channel" in updates:
        row.pilot_channel = updates["pilot_channel"] or None
    if "target_channel" in updates:
        row.target_channel = updates["target_channel"] or None

    row.updated_by = actor
    row.updated_at = datetime.now(UTC)
    db.commit()
    return resolve_config(db, settings)


# --- Decision log ----------------------------------------------------------


def record_decision(
    db: Session,
    *,
    opportunity: Opportunity,
    decision: str,
    reason: str,
    confidence: float = 0.0,
    threshold: float = 0.0,
    mode: str = MODE_SHADOW,
    actor: str = SYSTEM_ACTOR,
    details: dict | None = None,
) -> AutonomyDecision:
    """Append one row to the immutable autonomy decision log."""
    source_id = opportunity.post.source_id if opportunity.post else None
    row = AutonomyDecision(
        opportunity_id=opportunity.id,
        source_id=source_id,
        decision=decision,
        classification=opportunity.classification,
        confidence=float(confidence),
        threshold=float(threshold),
        mode=mode,
        actor=actor,
        reason=reason,
        details=details or {},
        created_at=datetime.now(UTC),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _audit(
    db: Session,
    opportunity: Opportunity,
    *,
    event_type: str,
    from_status: str,
    to_status: str,
    note: str,
) -> ReviewEvent:
    event = ReviewEvent(
        opportunity_id=opportunity.id,
        actor=SYSTEM_ACTOR,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        note=note,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    return event


# --- Evaluation ------------------------------------------------------------


def apply_classification(
    opportunity: Opportunity, result: ClassificationResult
) -> Opportunity:
    """Store the classification result on the opportunity."""
    opportunity.classification = result.classification
    opportunity.engagement_type = result.engagement_type
    opportunity.is_genuine_opportunity = result.is_genuine_opportunity
    opportunity.is_publishable = result.is_publishable
    opportunity.classification_confidence = round(float(result.confidence), 4)
    opportunity.classification_evidence = list(result.evidence)
    opportunity.classification_reasons = list(result.rejection_reasons)
    opportunity.classification_method = result.method
    opportunity.classifier_version = result.classifier_version
    opportunity.model_provider = result.model_provider
    opportunity.model_name = result.model_name
    opportunity.risk_level = result.risk_level
    opportunity.risk_assessment = result.risk_assessment
    for field_name in (
        "title",
        "skills",
        "budget",
        "duration",
        "deadline",
        "client_name",
        "application_url",
    ):
        value = result.detected_fields.get(field_name)
        if value in (None, [], ""):
            continue
        if field_name == "budget":
            opportunity.budget_amount = float(value)
        elif field_name == "skills":
            if not opportunity.skills:
                opportunity.skills = list(value)
        elif field_name == "duration":
            opportunity.duration = str(value)[:120]
        elif field_name == "title":
            opportunity.title = str(value)[:247]
        else:
            setattr(opportunity, field_name, value)
    return opportunity


def _source_allowed(opportunity: Opportunity, config: AutonomyConfig) -> bool:
    """Only approved, active sources may be published automatically."""
    post = opportunity.post
    if post is None:
        return False
    source = post.source
    if source is None or not source.is_active:
        return False
    if not config.approved_source_ids:
        return False
    return source.id in config.approved_source_ids


def _category_allowed(opportunity: Opportunity, config: AutonomyConfig) -> bool:
    if not config.approved_categories:
        return True
    category = (opportunity.category or "").strip().lower()
    return category in config.approved_categories


def _content_valid(opportunity: Opportunity) -> bool:
    """Basic content quality gate for autonomous publishing."""
    if not opportunity.title or len(opportunity.title.strip()) < 8:
        return False
    text = opportunity.original_text or ""
    if len(text.strip()) < MIN_CONTENT_LENGTH:
        return False
    return True


def _is_duplicate(db: Session, opportunity: Opportunity) -> bool:
    """True when the same content was already collected from another post."""
    post = opportunity.post
    if post is None:
        return False
    duplicate = db.scalar(
        select(CollectedPost.id).where(
            CollectedPost.content_hash == post.content_hash,
            CollectedPost.id != post.id,
        )
    )
    return duplicate is not None


def evaluate_opportunity(
    db: Session,
    opportunity: Opportunity,
    *,
    config: AutonomyConfig | None = None,
    provider=None,
    use_ai: bool = True,
    settings=default_settings,
    now: datetime | None = None,
) -> AutonomyOutcome:
    """Classify one opportunity and apply the autonomy decision.

    In shadow mode the decision is recorded and nothing else changes. In
    supervised and production mode the decision is applied to the review
    status and written to the audit trail.
    """
    config = config or resolve_config(db, settings)
    now = now or datetime.now(UTC)
    text = opportunity.original_text or ""

    result = classify_post(
        text, provider=provider, threshold=config.threshold, use_ai=use_ai
    )
    apply_classification(opportunity, result)

    from_status = opportunity.review_status
    decision, reason, details = _decide_for_autonomy(db, opportunity, result, config)

    if config.is_shadow or not config.enabled:
        shadow_decision = {
            DECISION_AUTO_APPROVED: DECISION_WOULD_AUTO_APPROVE,
            DECISION_AUTO_REJECTED: DECISION_WOULD_AUTO_REJECT,
        }.get(decision, decision)
        db.commit()
        record_decision(
            db,
            opportunity=opportunity,
            decision=shadow_decision,
            reason=reason,
            confidence=result.confidence,
            threshold=config.threshold,
            mode=config.mode,
            details=details,
        )
        return AutonomyOutcome(
            decision=shadow_decision,
            reason=reason,
            classification=result,
            applied=False,
            details=details,
        )

    if decision == DECISION_AUTO_APPROVED:
        opportunity.review_status = "approved"
        opportunity.approval_method = "auto"
        opportunity.approval_reason = reason
        opportunity.approved_at = now
        opportunity.approved_by = SYSTEM_ACTOR
        opportunity.quarantined = False
        _audit(
            db,
            opportunity,
            event_type="auto_approval",
            from_status=from_status,
            to_status="approved",
            note=reason,
        )
    elif decision == DECISION_AUTO_REJECTED:
        opportunity.review_status = "rejected"
        opportunity.approval_method = "auto"
        opportunity.approval_reason = reason
        opportunity.approved_at = now
        opportunity.approved_by = SYSTEM_ACTOR
        _audit(
            db,
            opportunity,
            event_type="auto_rejection",
            from_status=from_status,
            to_status="rejected",
            note=reason,
        )
    elif decision == DECISION_QUARANTINED:
        opportunity.review_status = "needs_review"
        opportunity.quarantined = True
        opportunity.approval_method = "auto"
        opportunity.approval_reason = reason
        opportunity.approved_at = now
        opportunity.approved_by = SYSTEM_ACTOR
        _audit(
            db,
            opportunity,
            event_type="auto_quarantine",
            from_status=from_status,
            to_status="needs_review",
            note=reason,
        )
    else:
        if from_status == "pending_review":
            opportunity.review_status = "needs_review"
            _audit(
                db,
                opportunity,
                event_type="auto_review_request",
                from_status=from_status,
                to_status="needs_review",
                note=reason,
            )

    db.commit()
    record_decision(
        db,
        opportunity=opportunity,
        decision=decision,
        reason=reason,
        confidence=result.confidence,
        threshold=config.threshold,
        mode=config.mode,
        details=details,
    )
    return AutonomyOutcome(
        decision=decision,
        reason=reason,
        classification=result,
        applied=True,
        details=details,
    )


def _decide_for_autonomy(
    db: Session,
    opportunity: Opportunity,
    result: ClassificationResult,
    config: AutonomyConfig,
) -> tuple[str, str, dict]:
    """Apply the autonomy gates on top of the classification decision."""
    details: dict = {
        "classification": result.classification,
        "confidence": round(float(result.confidence), 4),
        "risk_level": result.risk_level,
        "risk_flags": list(result.risk_flags),
    }

    if result.decision == DECISION_QUARANTINE:
        return (
            DECISION_QUARANTINED,
            "The deterministic risk engine reported a high or critical risk indicator.",
            details,
        )
    if result.decision == DECISION_AUTO_REJECT:
        reason = (
            result.rejection_reasons[0]
            if result.rejection_reasons
            else "The post is not a publishable freelance or contract opportunity."
        )
        return DECISION_AUTO_REJECTED, reason, details
    if result.decision != DECISION_AUTO_APPROVE:
        reason = (
            result.rejection_reasons[0]
            if result.rejection_reasons
            else "The opportunity did not reach the automatic approval threshold."
        )
        return DECISION_HUMAN_REVIEW_REQUIRED, reason, details

    if not _content_valid(opportunity):
        details["content_quality"] = "failed"
        return (
            DECISION_HUMAN_REVIEW_REQUIRED,
            "The post did not pass the content quality validation for automatic approval.",
            details,
        )

    if not _source_allowed(opportunity, config):
        details["source_allowed"] = False
        return (
            DECISION_HUMAN_REVIEW_REQUIRED,
            "The source is not approved for autonomous publishing.",
            details,
        )

    if not _category_allowed(opportunity, config):
        details["category_allowed"] = False
        return (
            DECISION_HUMAN_REVIEW_REQUIRED,
            "The job category is not approved for autonomous publishing.",
            details,
        )

    if _is_duplicate(db, opportunity):
        details["duplicate"] = True
        return (
            DECISION_HUMAN_REVIEW_REQUIRED,
            "The same content was already collected from another post.",
            details,
        )

    return (
        DECISION_AUTO_APPROVED,
        ("The post is a genuine freelance or contract opportunity, the deterministic "
        "risk engine found no indicator, and the confidence is at or above the "
        f"threshold of {config.threshold:.2f}."),
        details,
    )


# --- Publishing ------------------------------------------------------------


def _normalize_channel(channel: str | None) -> str | None:
    """Normalise a channel reference the same way the publisher does."""
    if not channel:
        return None
    from app.integrations.telegram.channel import (
        InvalidChannelReference,
        channel_reference,
    )

    try:
        return channel_reference(channel)
    except InvalidChannelReference:
        return channel.strip()


def published_today(db: Session, channel: str, now: datetime) -> int:
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
    reference = _normalize_channel(channel)
    return int(
        db.scalar(
            select(func.count())
            .select_from(Publication)
            .where(Publication.status == "published")
            .where(Publication.channel == reference)
            .where(Publication.published_at >= start)
        )
        or 0
    )


def last_published_at(db: Session, channel: str) -> datetime | None:
    reference = _normalize_channel(channel)
    return db.scalar(
        select(Publication.published_at)
        .where(Publication.status == "published")
        .where(Publication.channel == reference)
        .order_by(Publication.published_at.desc())
        .limit(1)
    )


def publishing_blocked(db: Session, config: AutonomyConfig, now: datetime) -> str | None:
    """Return the reason why publishing is currently blocked, if it is."""
    if not config.enabled:
        return "autonomous publishing is disabled"
    if config.is_shadow:
        return "the autonomy engine is in shadow mode"
    channel = config.channel
    if not channel:
        return f"no channel is configured for {config.mode} mode"
    if config.daily_publish_limit <= 0:
        return "the daily publishing limit is zero"
    if published_today(db, channel, now) >= config.daily_publish_limit:
        return "the daily publishing limit has been reached"
    last = last_published_at(db, channel)
    if last is not None and config.min_interval_seconds > 0:
        earliest = _utc(last) + timedelta(seconds=config.min_interval_seconds)
        if earliest > now:
            return "the minimum interval between publications has not elapsed"
    return None


def eligible_publications(
    db: Session, config: AutonomyConfig, channel: str
) -> list[Opportunity]:
    """Opportunities that may be published autonomously right now."""
    reference = _normalize_channel(channel)
    statement = (
        select(Opportunity)
        .join(CollectedPost, Opportunity.collected_post_id == CollectedPost.id)
        .join(Source, CollectedPost.source_id == Source.id)
        .where(Opportunity.review_status == "approved")
        .where(Opportunity.approval_method == "auto")
        .where(Opportunity.is_publishable.is_(True))
        .where(Opportunity.quarantined.is_(False))
        .where(Opportunity.classification.in_(tuple(PUBLISHABLE_SET)))
        .where(Opportunity.classification_confidence >= config.threshold)
        .where(Opportunity.risk_level.notin_(("high", "critical")))
        .where(Source.is_active.is_(True))
        .where(Source.id.in_(config.approved_source_ids or (-1,)))
        .order_by(Opportunity.id)
    )
    opportunities = list(db.scalars(statement).all())

    eligible: list[Opportunity] = []
    published_hashes = set(
        db.scalars(
            select(Publication.content_hash).where(
                Publication.status == "published",
                Publication.channel == reference,
                Publication.content_hash.is_not(None),
            )
        ).all()
    )
    for opportunity in opportunities:
        if not _category_allowed(opportunity, config):
            continue
        if not _content_valid(opportunity):
            continue
        if _is_duplicate(db, opportunity):
            continue
        existing = db.scalar(
            select(Publication.id).where(
                Publication.opportunity_id == opportunity.id,
                Publication.channel == reference,
            )
        )
        if existing is not None:
            continue
        post = opportunity.post
        if post is not None and post.content_hash in published_hashes:
            continue
        eligible.append(opportunity)
    return eligible


def recheck_eligibility(
    db: Session, opportunity: Opportunity, config: AutonomyConfig | None = None
) -> str | None:
    """Send-time eligibility recheck for an autonomous publication.

    Returns ``None`` when the opportunity may still be sent, otherwise the
    reason why it must not be sent.
    """
    config = config or resolve_config(db)
    if not config.enabled:
        return "autonomous publishing is disabled"
    if opportunity.review_status != "approved":
        return "the opportunity is no longer approved"
    if opportunity.quarantined:
        return "the opportunity is quarantined"
    if not opportunity.is_publishable:
        return "the opportunity is not publishable"
    if opportunity.classification not in PUBLISHABLE_SET:
        return "the classification is outside the publishable taxonomy"
    if opportunity.risk_level in ("high", "critical"):
        return "the deterministic risk assessment is high or critical"
    if opportunity.classification_confidence < config.threshold:
        return "the confidence is below the automatic approval threshold"
    if not _source_allowed(opportunity, config):
        return "the source is not approved for autonomous publishing"
    if not _category_allowed(opportunity, config):
        return "the job category is not approved for autonomous publishing"
    if not _content_valid(opportunity):
        return "the content did not pass the quality validation"
    return None


# --- Overview --------------------------------------------------------------


def overview(db: Session) -> dict:
    """Counters for the autonomous publishing control panel."""

    def count(model, *conditions) -> int:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        return int(db.scalar(statement) or 0)

    auto_approved = count(
        Opportunity,
        Opportunity.approval_method == "auto",
        Opportunity.review_status == "approved",
    )
    human_review = count(
        Opportunity,
        Opportunity.review_status.in_(("pending_review", "needs_review")),
    )
    rejected = count(Opportunity, Opportunity.review_status == "rejected")
    quarantined = count(Opportunity, Opportunity.quarantined.is_(True))
    published = count(Publication, Publication.status == "published")
    failed = count(Publication, Publication.status == "failed")
    return {
        "auto_approved": auto_approved,
        "human_review": human_review,
        "rejected": rejected,
        "quarantined": quarantined,
        "published": published,
        "failed": failed,
        "decisions": count(AutonomyDecision),
        "would_auto_approve": count(
            AutonomyDecision, AutonomyDecision.decision == "would_auto_approve"
        ),
        "would_auto_reject": count(
            AutonomyDecision, AutonomyDecision.decision == "would_auto_reject"
        ),
    }


def recent_decisions(db: Session, limit: int = 50) -> list[AutonomyDecision]:
    return list(
        db.scalars(
            select(AutonomyDecision)
            .order_by(AutonomyDecision.id.desc())
            .limit(limit)
        ).all()
    )


def opportunities_pending_evaluation(db: Session, limit: int = 50) -> list[Opportunity]:
    return list(
        db.scalars(
            select(Opportunity)
            .where(
                Opportunity.review_status.in_(("pending_review", "needs_review")),
                Opportunity.approval_method.is_(None),
            )
            .order_by(Opportunity.id)
            .limit(limit)
        ).all()
    )


async def run_autonomous_publish_cycle(
    db: Session,
    *,
    publisher,
    settings=default_settings,
    now: datetime | None = None,
    limit: int = 10,
) -> dict:
    """Publish eligible opportunities without any human in the loop.

    The cycle is fail-closed: it publishes nothing when the engine is
    disabled, when the mode is ``shadow``, when the channel is not
    allowlisted, when the daily limit is reached, or when the minimum
    interval between publications has not elapsed.
    """
    from app.security.auth import Principal
    from app.services.publishing import (
        PublishingError,
        create_or_get_publication,
        publish_publication,
        validate_channel,
    )

    now = now or datetime.now(UTC)
    config = resolve_config(db, settings)
    outcome: dict = {
        "published": [],
        "skipped": [],
        "mode": config.mode,
        "channel": config.channel,
    }

    if not settings.publishing_enabled:
        outcome["skipped"].append("publishing is disabled")
        return outcome

    blocked = publishing_blocked(db, config, now)
    if blocked:
        outcome["skipped"].append(blocked)
        return outcome

    channel = config.channel
    assert channel is not None
    try:
        channel = validate_channel(channel, settings)
    except PublishingError as exc:
        outcome["skipped"].append(f"channel is not allowlisted: {exc}")
        return outcome

    principal = Principal(name=SYSTEM_ACTOR, role="admin")
    candidates = eligible_publications(db, config, channel)
    outcome["candidates"] = len(candidates)

    for opportunity in candidates[:limit]:
        # The limit and the minimum interval are re-checked for every single
        # message, not only once per cycle.
        blocked_now = publishing_blocked(db, config, now)
        if blocked_now:
            outcome["skipped"].append(blocked_now)
            break
        publication = create_or_get_publication(
            db,
            opportunity=opportunity,
            channel=channel,
            principal=principal,
            settings=settings,
            now=now,
            is_autonomous=True,
            autonomy_mode=config.mode,
        )
        if publication.status == "published":
            continue
        try:
            published = await publish_publication(
                db,
                publication,
                publisher=publisher,
                settings=settings,
                now=now,
            )
        except PublishingError as exc:
            outcome["skipped"].append(f"{opportunity.id}: {exc.code}")
            record_decision(
                db,
                opportunity=opportunity,
                decision=DECISION_PUBLISH_SKIPPED,
                reason=exc.code,
                confidence=opportunity.classification_confidence,
                threshold=config.threshold,
                mode=config.mode,
                details={"channel": channel, "error": exc.code},
            )
            continue
        outcome["published"].append(
            {
                "publication_id": published.id,
                "opportunity_id": published.opportunity_id,
                "channel": published.channel,
            }
        )
        record_decision(
            db,
            opportunity=opportunity,
            decision=DECISION_AUTO_PUBLISHED,
            reason="Published autonomously by the Yekis autonomy engine.",
            confidence=opportunity.classification_confidence,
            threshold=config.threshold,
            mode=config.mode,
            details={"channel": channel, "publication_id": published.id},
        )
    return outcome


def evaluate_pending_opportunities(
    db: Session,
    *,
    config: AutonomyConfig | None = None,
    provider=None,
    use_ai: bool = True,
    settings=default_settings,
    limit: int = 50,
) -> list[AutonomyOutcome]:
    """Evaluate every opportunity that is still waiting for a decision."""
    config = config or resolve_config(db, settings)
    outcomes: list[AutonomyOutcome] = []
    for opportunity in opportunities_pending_evaluation(db, limit=limit):
        outcomes.append(
            evaluate_opportunity(
                db,
                opportunity,
                config=config,
                provider=provider,
                use_ai=use_ai,
                settings=settings,
            )
        )
    return outcomes


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
