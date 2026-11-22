from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import get_db
from app.integrations.telegram.publisher import PublisherPort
from app.models import Opportunity, Publication, ReviewEvent
from app.schemas import (
    OpportunityRead,
    OpportunityUpdate,
    PublicationRead,
    PublishRequest,
    ReviewEventRead,
    ReviewUpdate,
)
from app.security.auth import Principal, require_role
from app.services.classification.taxonomy import (
    NON_TARGET_CLASSIFICATIONS,
    PUBLISHABLE_CLASSIFICATIONS,
    UNCERTAIN_CLASSIFICATIONS,
    UNSAFE_CLASSIFICATIONS,
)
from app.services.publishing import (
    PublishingError,
    publish_opportunity,
    publisher_session,
)
from app.services.review import InvalidEdit, InvalidTransition, apply_edit, apply_review

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
READ_ROLES = ("admin", "reviewer", "collector", "analyst")

#: Dashboard filters over the classification taxonomy.
CLASSIFICATION_GROUPS: dict[str, tuple[str, ...]] = {
    "freelance": PUBLISHABLE_CLASSIFICATIONS,
    "employment": NON_TARGET_CLASSIFICATIONS,
    "education": (
        "educational_content",
        "skills_tips",
        "training_announcement",
    ),
    "announcement": (
        "closed_or_filled_job",
        "general_announcement",
        "news_and_information",
        "community_discussion",
        "marketplace_promotion",
    ),
    "suspicious": UNSAFE_CLASSIFICATIONS,
    "ambiguous": UNCERTAIN_CLASSIFICATIONS,
}


def _get_opportunity(db: Session, opportunity_id: int) -> Opportunity:
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "opportunity_not_found", "message": "Opportunity not found"},
        )
    return opportunity


@router.get(
    "",
    response_model=list[OpportunityRead],
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def list_opportunities(
    status: str | None = Query(
        default=None,
        pattern="^(actionable|pending_review|needs_review|approved|published|rejected|closed|all)$",
    ),
    sort: str = Query(default="newest", pattern="^(newest|quality|updated)$"),
    risk_only: bool = Query(default=False),
    classification: str | None = Query(default=None, max_length=40),
    group: str | None = Query(
        default=None,
        pattern="^(freelance|employment|education|announcement|suspicious|ambiguous)$",
    ),
    quarantined: bool | None = Query(default=None),
    created_from: date | None = Query(default=None),
    created_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    # Ordering: newest by default so fresh and updated jobs always arrive at the top
    # The publications are loaded with a second query instead of one query
    # per row. The `is_published` field of the answer reads that
    # relationship, and a lazy load there is one round trip to the database
    # for every opportunity in the page: on a remote database that is what
    # made the review queue take more than ten seconds to open.
    if sort == "quality":
        statement = select(Opportunity).options(
            selectinload(Opportunity.publications)
        ).order_by(
            Opportunity.quality_score.desc(), Opportunity.id.desc()
        )
    elif sort == "updated":
        statement = select(Opportunity).options(
            selectinload(Opportunity.publications)
        ).order_by(
            Opportunity.reviewed_at.desc().nullslast(), Opportunity.id.desc()
        )
    else:
        statement = select(Opportunity).options(
            selectinload(Opportunity.publications)
        ).order_by(Opportunity.id.desc())

    published_subquery = (
        select(Publication.opportunity_id)
        .where(Publication.status == "published")
    )

    # The closed-post sweep is a write operation and it belongs to the
    # scheduled job, not to a read. It used to run inside this endpoint, which
    # scanned every waiting opportunity on every page load and made the
    # dashboard wait for more than ten seconds.
    if status in ("actionable", None):
        statement = statement.where(
            Opportunity.review_status.in_(("pending_review", "needs_review"))
        )
        statement = statement.where(Opportunity.classification != "closed_or_filled_job")
        statement = statement.where(Opportunity.id.notin_(published_subquery))
    elif status == "pending_review":
        statement = statement.where(Opportunity.review_status == "pending_review")
        statement = statement.where(Opportunity.classification != "closed_or_filled_job")
        statement = statement.where(Opportunity.id.notin_(published_subquery))
    elif status == "needs_review":
        statement = statement.where(Opportunity.review_status == "needs_review")
        statement = statement.where(Opportunity.classification != "closed_or_filled_job")
        statement = statement.where(Opportunity.id.notin_(published_subquery))
    elif status == "approved":
        statement = statement.where(Opportunity.review_status == "approved")
        statement = statement.where(Opportunity.id.notin_(published_subquery))
    elif status == "published":
        statement = statement.where(Opportunity.id.in_(published_subquery))
    elif status == "rejected":
        statement = statement.where(Opportunity.review_status == "rejected")
        statement = statement.where(Opportunity.classification != "closed_or_filled_job")
    elif status == "closed":
        statement = statement.where(
            or_(
                Opportunity.classification == "closed_or_filled_job",
                Opportunity.approval_reason.ilike("%closed%"),
            )
        )
    # status == "all" applies no status filter

    if risk_only:
        statement = statement.where(Opportunity.risk_score > 0)
    if classification:
        statement = statement.where(Opportunity.classification == classification)
    if group:
        statement = statement.where(
            Opportunity.classification.in_(CLASSIFICATION_GROUPS[group])
        )
    if quarantined is not None:
        statement = statement.where(Opportunity.quarantined.is_(quarantined))
    # The date range includes both days: the upper bound is the last instant of
    # the selected day, so a post collected at any time that day is included.
    if created_from is not None:
        statement = statement.where(
            Opportunity.created_at >= datetime.combine(created_from, time.min)
        )
    if created_to is not None:
        statement = statement.where(
            Opportunity.created_at <= datetime.combine(created_to, time.max)
        )
    return list(db.scalars(statement.limit(limit).offset(offset)).all())


@router.get(
    "/queue-summary",
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def queue_summary(db: Session = Depends(get_db)) -> dict:
    """Exact queue counts, independent of the page size of the list endpoint.

    The dashboard shows the size of the review backlog next to the tab. Two
    things make that number wrong if it is taken from the list response: the
    page size caps it, and a narrowed filter reports zero even though work is
    waiting in another queue - which is how an operator ends up looking at an
    empty review queue while hundreds of posts still need a decision.
    """
    published = select(Publication.opportunity_id).where(
        Publication.status == "published"
    )
    closed = or_(
        Opportunity.classification == "closed_or_filled_job",
        # coalesce keeps the expression boolean: comparing a NULL
        # approval_reason with ILIKE yields NULL, and the negation below would
        # silently drop every row that was never rejected for being closed.
        func.coalesce(Opportunity.approval_reason, "").ilike("%closed%"),
    )

    counts: dict[str, int] = {"pending_review": 0, "needs_review": 0, "approved": 0}
    rows = db.execute(
        select(Opportunity.review_status, func.count(Opportunity.id))
        .where(Opportunity.review_status.in_(tuple(counts)))
        .where(Opportunity.classification != "closed_or_filled_job")
        .where(Opportunity.id.notin_(published))
        .group_by(Opportunity.review_status)
    ).all()
    for status_name, count in rows:
        counts[status_name] = int(count)

    rejected = db.scalar(
        select(func.count(Opportunity.id))
        .where(Opportunity.review_status == "rejected")
        .where(~closed)
    )
    closed_count = db.scalar(select(func.count(Opportunity.id)).where(closed))
    published_count = db.scalar(
        select(func.count(Opportunity.id)).where(Opportunity.id.in_(published))
    )
    total = db.scalar(select(func.count(Opportunity.id)))

    return {
        **counts,
        "rejected": int(rejected or 0),
        "closed": int(closed_count or 0),
        "published": int(published_count or 0),
        # The single number the dashboard shows as the review backlog.
        "actionable": counts["pending_review"] + counts["needs_review"],
        "total": int(total or 0),
    }
def export_opportunities(
    status: str | None = Query(default="approved"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    from app.services.marketplace_sync import serialize_opportunity_for_marketplace

    statement = select(Opportunity).order_by(Opportunity.id.desc())
    if status:
        statement = statement.where(Opportunity.review_status == status)
    opportunities = list(db.scalars(statement.limit(limit).offset(offset)).all())
    return [serialize_opportunity_for_marketplace(opp) for opp in opportunities]


@router.get(
    "/{opportunity_id}",
    response_model=OpportunityRead,
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    return _get_opportunity(db, opportunity_id)


@router.get(
    "/{opportunity_id}/events",
    response_model=list[ReviewEventRead],
    dependencies=[Depends(require_role("admin", "reviewer", "analyst"))],
)
def list_review_events(
    opportunity_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    _get_opportunity(db, opportunity_id)
    statement = (
        select(ReviewEvent)
        .where(ReviewEvent.opportunity_id == opportunity_id)
        .order_by(ReviewEvent.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())


@router.patch(
    "/{opportunity_id}/review",
    response_model=OpportunityRead,
    dependencies=[Depends(require_role("reviewer"))],
)
def review_opportunity(
    opportunity_id: int,
    payload: ReviewUpdate,
    principal: Principal = Depends(require_role("reviewer")),
    db: Session = Depends(get_db),
):
    opportunity = _get_opportunity(db, opportunity_id)
    try:
        apply_review(
            db,
            opportunity,
            decision=payload.decision,
            note=payload.reviewer_note,
            principal=principal,
        )
    except InvalidTransition as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return opportunity


@router.post(
    "/{opportunity_id}/publish",
    response_model=PublicationRead,
)
async def publish_endpoint(
    opportunity_id: int,
    payload: PublishRequest,
    principal: Principal = Depends(require_role("reviewer")),
    publisher: PublisherPort = Depends(publisher_session),
    settings=Depends(get_settings),
    db: Session = Depends(get_db),
):
    try:
        publication = await publish_opportunity(
            db,
            opportunity_id=opportunity_id,
            channel=payload.channel,
            principal=principal,
            publisher=publisher,
            settings=settings,
            scheduled_at=payload.scheduled_at,
        )
        if publication.status == "published":
            try:
                from app.services.marketplace_sync import dispatch_to_marketplace

                opp = db.get(Opportunity, opportunity_id)
                if opp:
                    dispatch_to_marketplace(opp, settings)
            except Exception:  # noqa: BLE001
                pass
    except PublishingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return publication


@router.patch(
    "/{opportunity_id}",
    response_model=OpportunityRead,
    dependencies=[Depends(require_role("reviewer"))],
)
def edit_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    principal: Principal = Depends(require_role("reviewer")),
    db: Session = Depends(get_db),
):
    opportunity = _get_opportunity(db, opportunity_id)
    try:
        apply_edit(
            db,
            opportunity,
            updates=payload.model_dump(exclude_unset=True),
            principal=principal,
        )
    except InvalidEdit as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return opportunity


@router.post(
    "/cleanup-closed",
    dependencies=[Depends(require_role("reviewer"))],
)
def cleanup_closed(
    principal: Principal = Depends(require_role("reviewer")),
    db: Session = Depends(get_db),
):
    from app.services.review import cleanup_closed_opportunities

    closed_ids = cleanup_closed_opportunities(db, actor=principal.name)
    return {"cleaned_count": len(closed_ids), "cleaned_ids": closed_ids}
