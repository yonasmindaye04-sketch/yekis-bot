"""Read-only market and source-quality analytics.

These are content-intelligence aggregates over collected posts and reviewed
opportunities. No marketplace escrow, wallet, payout, or contract data is read
or written.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import CollectedPost, Opportunity, Publication, Source

STATUSES = ("pending_review", "needs_review", "approved", "rejected")
REVIEWED_STATUSES = ("approved", "rejected")


def _number(value: Any) -> float:
    return round(float(value), 2) if value is not None else 0.0


def _buckets(rows) -> list[dict]:
    return [{"key": str(key or "unknown"), "count": int(count)} for key, count in rows]


def _budget_buckets(rows) -> list[dict]:
    return [
        {
            "currency": currency or "unknown",
            "count": int(count),
            "min_amount": _number(minimum),
            "max_amount": _number(maximum),
            "avg_amount": _number(average),
        }
        for currency, count, minimum, maximum, average in rows
    ]


def overview(db: Session) -> dict:
    total_sources = db.scalar(select(func.count()).select_from(Source)) or 0
    active_sources = (
        db.scalar(select(func.count()).select_from(Source).where(Source.is_active.is_(True)))
        or 0
    )
    total_posts = db.scalar(select(func.count()).select_from(CollectedPost)) or 0
    total_opportunities = db.scalar(select(func.count()).select_from(Opportunity)) or 0
    status_rows = db.execute(
        select(Opportunity.review_status, func.count()).group_by(Opportunity.review_status)
    ).all()
    by_status = {status: 0 for status in STATUSES}
    by_status.update({str(status): int(count) for status, count in status_rows})

    by_type = _buckets(
        db.execute(
            select(Opportunity.opportunity_type, func.count()).group_by(
                Opportunity.opportunity_type
            )
        ).all()
    )
    by_category = _buckets(
        db.execute(
            select(Opportunity.category, func.count()).group_by(Opportunity.category)
        ).all()
    )
    budget_by_currency = _budget_buckets(
        db.execute(
            select(
                Opportunity.budget_currency,
                func.count(),
                func.min(Opportunity.budget_amount),
                func.max(Opportunity.budget_amount),
                func.avg(Opportunity.budget_amount),
            )
            .where(Opportunity.budget_amount.isnot(None))
            .group_by(Opportunity.budget_currency)
        ).all()
    )

    reviewed = by_status.get("approved", 0) + by_status.get("rejected", 0)
    approval_rate = (
        round(by_status.get("approved", 0) / reviewed * 100, 2) if reviewed else 0.0
    )
    published = (
        db.scalar(
            select(func.count()).select_from(Publication).where(Publication.status == "published")
        )
        or 0
    )
    publication_failures = (
        db.scalar(
            select(func.count()).select_from(Publication).where(Publication.status == "failed")
        )
        or 0
    )
    risk_flagged = (
        db.scalar(
            select(func.count())
            .select_from(Opportunity)
            .where(Opportunity.risk_score > 0)
        )
        or 0
    )
    avg_quality = db.scalar(select(func.avg(Opportunity.quality_score))) or 0
    last_collected_at = db.scalar(select(func.max(CollectedPost.collected_at)))

    return {
        "total_sources": int(total_sources),
        "active_sources": int(active_sources),
        "total_posts": int(total_posts),
        "total_opportunities": int(total_opportunities),
        "by_status": by_status,
        "by_type": by_type,
        "by_category": by_category,
        "budget_by_currency": budget_by_currency,
        "risk_flagged": int(risk_flagged),
        "avg_quality": _number(avg_quality),
        "approval_rate": approval_rate,
        "published": int(published),
        "publication_failures": int(publication_failures),
        "last_collected_at": last_collected_at,
    }


def source_quality(db: Session) -> list[dict]:
    post_counts = (
        select(
            CollectedPost.source_id.label("source_id"),
            func.count(CollectedPost.id).label("posts"),
        )
        .group_by(CollectedPost.source_id)
        .subquery()
    )
    opportunity_counts = (
        select(
            CollectedPost.source_id.label("source_id"),
            func.count(Opportunity.id).label("opportunities"),
            func.avg(Opportunity.quality_score).label("avg_quality"),
            func.sum(case((Opportunity.risk_score > 0, 1), else_=0)).label("risk_flagged"),
            func.sum(case((Opportunity.review_status == "approved", 1), else_=0)).label("approved"),
            func.sum(case((Opportunity.review_status == "rejected", 1), else_=0)).label("rejected"),
            func.sum(
                case((Opportunity.review_status == "pending_review", 1), else_=0)
            ).label("pending_review"),
            func.sum(
                case((Opportunity.review_status == "needs_review", 1), else_=0)
            ).label("needs_review"),
        )
        .join(Opportunity, Opportunity.collected_post_id == CollectedPost.id)
        .group_by(CollectedPost.source_id)
        .subquery()
    )
    publication_counts = (
        select(
            CollectedPost.source_id.label("source_id"),
            func.count(Publication.id).label("publications"),
            func.sum(case((Publication.status == "published", 1), else_=0)).label("published"),
            func.sum(case((Publication.status == "failed", 1), else_=0)).label(
                "publication_failures"
            ),
        )
        .join(Opportunity, Opportunity.id == Publication.opportunity_id)
        .join(CollectedPost, CollectedPost.id == Opportunity.collected_post_id)
        .group_by(CollectedPost.source_id)
        .subquery()
    )

    rows = db.execute(
        select(
            Source,
            func.coalesce(post_counts.c.posts, 0),
            func.coalesce(opportunity_counts.c.opportunities, 0),
            func.coalesce(opportunity_counts.c.avg_quality, 0),
            func.coalesce(opportunity_counts.c.risk_flagged, 0),
            func.coalesce(opportunity_counts.c.approved, 0),
            func.coalesce(opportunity_counts.c.rejected, 0),
            func.coalesce(opportunity_counts.c.pending_review, 0),
            func.coalesce(opportunity_counts.c.needs_review, 0),
            func.coalesce(publication_counts.c.publications, 0),
            func.coalesce(publication_counts.c.published, 0),
            func.coalesce(publication_counts.c.publication_failures, 0),
        )
        .outerjoin(post_counts, post_counts.c.source_id == Source.id)
        .outerjoin(opportunity_counts, opportunity_counts.c.source_id == Source.id)
        .outerjoin(publication_counts, publication_counts.c.source_id == Source.id)
        .order_by(Source.id)
    ).all()

    result = []
    for row in rows:
        (
            source,
            posts,
            opportunities,
            avg_quality,
            risk_flagged,
            approved,
            rejected,
            pending_review,
            needs_review,
            publications,
            published,
            publication_failures,
        ) = row
        reviewed = int(approved) + int(rejected)
        result.append(
            {
                "id": source.id,
                "name": source.name,
                "source_type": source.source_type,
                "is_active": source.is_active,
                "posts": int(posts),
                "opportunities": int(opportunities),
                "approved": int(approved),
                "rejected": int(rejected),
                "pending_review": int(pending_review),
                "needs_review": int(needs_review),
                "approval_rate": round(int(approved) / reviewed * 100, 2) if reviewed else 0.0,
                "risk_flagged": int(risk_flagged),
                "avg_quality": _number(avg_quality),
                "publications": int(publications),
                "published": int(published),
                "publication_failures": int(publication_failures),
                "last_success_at": source.last_success_at,
                "last_collected_message_id": source.last_collected_message_id,
                "consecutive_failures": source.consecutive_failures,
                "last_error": source.last_error,
            }
        )
    return result


def market(db: Session) -> dict:
    skills_counter: Counter[str] = Counter()
    for skills in db.scalars(
        select(Opportunity.skills).where(Opportunity.skills.isnot(None))
    ):
        if isinstance(skills, list):
            skills_counter.update(skill for skill in skills if isinstance(skill, str) and skill)

    budget_values: dict[str, list[float]] = defaultdict(list)
    for currency, amount in db.execute(
        select(Opportunity.budget_currency, Opportunity.budget_amount).where(
            Opportunity.budget_amount.isnot(None)
        )
    ).all():
        budget_values[currency or "unknown"].append(float(amount))
    budget = [
        {
            "currency": currency,
            "count": len(values),
            "min_amount": _number(min(values)),
            "max_amount": _number(max(values)),
            "avg_amount": _number(sum(values) / len(values)),
            "median_amount": _number(median(values)),
        }
        for currency, values in sorted(budget_values.items())
    ]

    remote_known = (
        db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.remote.isnot(None))
        )
        or 0
    )
    remote_true = (
        db.scalar(
            select(func.count()).select_from(Opportunity).where(Opportunity.remote.is_(True))
        )
        or 0
    )

    return {
        "skills_demand": [
            {"key": skill, "count": count}
            for skill, count in skills_counter.most_common(20)
        ],
        "budget": budget,
        "remote_share": round(remote_true / remote_known * 100, 2) if remote_known else 0.0,
        "by_category": _buckets(
            db.execute(
                select(Opportunity.category, func.count()).group_by(Opportunity.category)
            ).all()
        ),
        "by_type": _buckets(
            db.execute(
                select(Opportunity.opportunity_type, func.count()).group_by(
                    Opportunity.opportunity_type
                )
            ).all()
        ),
    }


def trends(db: Session, *, days: int = 30) -> list[dict]:
    days = max(1, min(days, 365))
    since = datetime.now(UTC) - timedelta(days=days)
    days_map: dict[str, dict[str, int]] = defaultdict(
        lambda: {"posts": 0, "opportunities": 0, "approved": 0, "published": 0}
    )

    def day_key(value) -> str:
        return str(value)

    for day, count in db.execute(
        select(func.date(CollectedPost.collected_at), func.count())
        .where(CollectedPost.collected_at >= since)
        .group_by(func.date(CollectedPost.collected_at))
    ).all():
        days_map[day_key(day)]["posts"] += int(count)
    for day, count in db.execute(
        select(func.date(Opportunity.created_at), func.count())
        .where(Opportunity.created_at >= since)
        .group_by(func.date(Opportunity.created_at))
    ).all():
        days_map[day_key(day)]["opportunities"] += int(count)
    for day, count in db.execute(
        select(func.date(Opportunity.reviewed_at), func.count())
        .where(
            Opportunity.reviewed_at >= since,
            Opportunity.review_status == "approved",
        )
        .group_by(func.date(Opportunity.reviewed_at))
    ).all():
        days_map[day_key(day)]["approved"] += int(count)
    for day, count in db.execute(
        select(func.date(Publication.published_at), func.count())
        .where(Publication.published_at >= since, Publication.status == "published")
        .group_by(func.date(Publication.published_at))
    ).all():
        days_map[day_key(day)]["published"] += int(count)

    return [
        {"day": day, **values}
        for day, values in sorted(days_map.items())
    ]
