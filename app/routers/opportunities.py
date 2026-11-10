from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.telegram.publisher import PublisherPort
from app.models import Opportunity, ReviewEvent
from app.schemas import (
    OpportunityRead,
    OpportunityUpdate,
    PublicationRead,
    PublishRequest,
    ReviewEventRead,
    ReviewUpdate,
)
from app.security.auth import Principal, require_role
from app.services.publishing import (
    PublishingError,
    publish_opportunity,
    publisher_session,
)
from app.services.review import InvalidEdit, InvalidTransition, apply_edit, apply_review

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
READ_ROLES = ("admin", "reviewer", "collector", "analyst")


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
        default=None, pattern="^(pending_review|needs_review|approved|rejected)$"
    ),
    risk_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    statement = select(Opportunity).order_by(
        Opportunity.quality_score.desc(), Opportunity.id.desc()
    )
    if status:
        statement = statement.where(Opportunity.review_status == status)
    if risk_only:
        statement = statement.where(Opportunity.risk_score > 0)
    return list(db.scalars(statement.limit(limit).offset(offset)).all())


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
