"""Review state machine and append-only audit events."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import Opportunity, ReviewEvent
from app.security.auth import Principal

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending_review": {"approved", "rejected", "needs_review"},
    "needs_review": {"approved", "rejected", "needs_review"},
    "approved": {"rejected", "needs_review"},
    "rejected": {"needs_review"},
}


class InvalidTransition(Exception):
    code = "invalid_transition"
    status_code = 409


class InvalidEdit(Exception):
    code = "invalid_edit"
    status_code = 422


def apply_review(
    db: Session,
    opportunity: Opportunity,
    *,
    decision: str,
    note: str | None,
    principal: Principal,
) -> tuple[Opportunity, ReviewEvent | None]:
    if decision == opportunity.review_status:
        return opportunity, None

    allowed = ALLOWED_TRANSITIONS.get(opportunity.review_status, set())
    if decision not in allowed:
        raise InvalidTransition(
            f"{opportunity.review_status} cannot transition to {decision}"
        )

    from_status = opportunity.review_status
    opportunity.review_status = decision
    opportunity.reviewed_at = datetime.now(UTC)
    opportunity.reviewed_by = principal.name
    if note is not None:
        opportunity.reviewer_note = note

    event = ReviewEvent(
        opportunity_id=opportunity.id,
        actor=principal.name,
        event_type="status_change",
        from_status=from_status,
        to_status=decision,
        note=note,
    )
    db.add(event)
    db.commit()
    db.refresh(opportunity)
    db.refresh(event)
    return opportunity, event


def apply_edit(
    db: Session,
    opportunity: Opportunity,
    *,
    updates: dict,
    principal: Principal,
    note: str | None = None,
) -> tuple[Opportunity, ReviewEvent | None]:
    if not updates:
        raise InvalidEdit("no editable fields were supplied")

    changes: dict[str, dict] = {}
    for field, value in updates.items():
        previous = getattr(opportunity, field)
        if previous != value:
            changes[field] = {"from": previous, "to": value}
            setattr(opportunity, field, value)

    if not changes:
        return opportunity, None

    event = ReviewEvent(
        opportunity_id=opportunity.id,
        actor=principal.name,
        event_type="field_edit",
        from_status=opportunity.review_status,
        to_status=opportunity.review_status,
        note=note or f"edited: {', '.join(sorted(changes))}",
        changes=changes,
    )
    db.add(event)
    db.commit()
    db.refresh(opportunity)
    db.refresh(event)
    return opportunity, event
