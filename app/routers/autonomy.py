"""Autonomous approval and publishing control endpoints.

These routes expose the control panel that the admin dashboard uses. They
never publish to a channel that is not allowlisted, and they never bypass the
deterministic risk engine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Opportunity
from app.schemas import (
    AutonomyDecisionRead,
    AutonomyEvaluateRead,
    AutonomyOverviewRead,
    AutonomySettingsRead,
    AutonomySettingsUpdate,
)
from app.security.auth import Principal, require_role
from app.services.autonomy import (
    evaluate_opportunity,
    overview,
    recent_decisions,
    resolve_config,
    update_config,
)

router = APIRouter(prefix="/autonomy", tags=["autonomy"])
READ_ROLES = ("admin", "reviewer", "analyst")


def _settings_payload(config) -> dict:
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "auto_approval_threshold": config.threshold,
        "approved_source_ids": list(config.approved_source_ids),
        "approved_categories": list(config.approved_categories),
        "daily_publish_limit": config.daily_publish_limit,
        "min_interval_seconds": config.min_interval_seconds,
        "pilot_channel": config.pilot_channel,
        "target_channel": config.target_channel,
        "source": config.source,
    }


@router.get(
    "/settings",
    response_model=AutonomySettingsRead,
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def autonomy_settings(settings=Depends(get_settings), db: Session = Depends(get_db)):
    return _settings_payload(resolve_config(db, settings))


@router.patch(
    "/settings",
    response_model=AutonomySettingsRead,
    dependencies=[Depends(require_role("admin"))],
)
def update_autonomy_settings(
    payload: AutonomySettingsUpdate,
    principal: Principal = Depends(require_role("admin")),
    settings=Depends(get_settings),
    db: Session = Depends(get_db),
):
    try:
        config = update_config(
            db,
            payload.model_dump(exclude_unset=True),
            actor=principal.name,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_autonomy_settings", "message": str(exc)},
        ) from exc
    return _settings_payload(config)


@router.get(
    "/overview",
    response_model=AutonomyOverviewRead,
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def autonomy_overview(settings=Depends(get_settings), db: Session = Depends(get_db)):
    config = resolve_config(db, settings)
    counters = overview(db)
    return {
        "enabled": config.enabled,
        "mode": config.mode,
        "auto_approval_threshold": config.threshold,
        "daily_publish_limit": config.daily_publish_limit,
        "min_interval_seconds": config.min_interval_seconds,
        "channel": config.channel,
        **counters,
    }


@router.get(
    "/decisions",
    response_model=list[AutonomyDecisionRead],
    dependencies=[Depends(require_role(*READ_ROLES))],
)
def autonomy_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    decisions = recent_decisions(db, limit=limit + offset)
    return decisions[offset:]


@router.post(
    "/opportunities/{opportunity_id}/evaluate",
    response_model=AutonomyEvaluateRead,
    dependencies=[Depends(require_role("reviewer"))],
)
def evaluate_opportunity_endpoint(
    opportunity_id: int,
    principal: Principal = Depends(require_role("reviewer")),
    settings=Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Classify one opportunity now and apply the autonomy decision.

    The reviewer role may trigger the evaluation, but the decision itself is
    still bound by the deterministic risk engine and the configured
    threshold.
    """
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "opportunity_not_found", "message": "Opportunity not found"},
        )
    outcome = evaluate_opportunity(db, opportunity, settings=settings)
    db.refresh(opportunity)
    result = outcome.classification
    return {
        "opportunity_id": opportunity.id,
        "decision": outcome.decision,
        "reason": outcome.reason,
        "applied": outcome.applied,
        "classification": result.classification if result else "insufficient_information",
        "confidence": result.confidence if result else 0.0,
        "risk_level": result.risk_level if result else "none",
        "review_status": opportunity.review_status,
    }
