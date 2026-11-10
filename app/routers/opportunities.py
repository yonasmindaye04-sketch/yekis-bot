from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db import get_db
from app.models import Opportunity
from app.schemas import OpportunityRead, ReviewUpdate

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.get("", response_model=list[OpportunityRead])
def list_opportunities(
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    statement = select(Opportunity).order_by(Opportunity.id.desc())
    if status:
        statement = statement.where(Opportunity.review_status == status)
    return list(db.scalars(statement).all())


@router.patch("/{opportunity_id}/review", response_model=OpportunityRead)
def review_opportunity(opportunity_id: int, payload: ReviewUpdate, db: Session = Depends(get_db)):
    opportunity = db.get(Opportunity, opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    opportunity.review_status = payload.decision
    opportunity.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(opportunity)
    return opportunity
