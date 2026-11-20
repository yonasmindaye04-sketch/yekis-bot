"""Admin and reviewer alert notifications for newly ingested opportunities.

Strict zero-emoji corporate style.
Non-blocking / error-tolerant: alert failures never interrupt ingestion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.config import Settings
    from app.models import Opportunity

logger = logging.getLogger(__name__)


def format_opportunity_alert(opportunity: Opportunity) -> str:
    """Format an opportunity alert for Telegram admin delivery."""
    budget = ""
    if opportunity.budget_amount is not None:
        amount = f"{opportunity.budget_amount:,.2f}".rstrip("0").rstrip(".")
        curr = opportunity.budget_currency or ""
        budget = f"{amount} {curr}".strip()

    lines = [
        f"[NEW OPPORTUNITY INGESTED #{opportunity.id}]",
        f"Title: {opportunity.title}",
    ]
    if opportunity.category:
        lines.append(f"Category: {opportunity.category}")
    if budget:
        lines.append(f"Compensation: {budget}")
    if opportunity.duration:
        lines.append(f"Duration: {opportunity.duration}")
    if opportunity.remote is not None:
        lines.append(f"Work Mode: {'Remote' if opportunity.remote else 'On-site'}")
    if opportunity.skills:
        lines.append("Skills: " + ", ".join(opportunity.skills[:6]))

    lines.append(f"Quality Score: {opportunity.quality_score}/100 | Risk: {opportunity.risk_score}")
    lines.append(f"Status: {opportunity.review_status}")
    lines.append("")
    lines.append("Review in Mission Control:")
    lines.append("http://localhost:8100/admin/")

    return "\n".join(lines)


def send_opportunity_alert(opportunity: Opportunity, settings: Settings) -> bool:
    """Send an immediate alert to the configured admin chat ID."""
    if not settings.admin_alerts_enabled:
        return False
    if not settings.admin_alert_chat_id or not settings.telegram_bot_token:
        logger.debug("admin alert skipped: chat id or bot token missing")
        return False

    text = format_opportunity_alert(opportunity)
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.admin_alert_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("admin alert dispatched for opportunity #%s", opportunity.id)
                return True
            logger.warning(
                "admin alert failed for opportunity #%s: %s %s",
                opportunity.id,
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception as exc:  # noqa: BLE001 - non-blocking notification failure
        logger.warning(
            "admin alert error for opportunity #%s: %s",
            opportunity.id,
            exc,
        )
        return False



def get_pending_human_review_opportunities(
    db: Session, limit: int = 10
) -> list[Opportunity]:
    """Retrieve opportunities requiring human review that have not been batched yet."""
    from sqlalchemy import select

    from app.models import Opportunity, Publication, ReviewEvent

    notified_subquery = select(ReviewEvent.opportunity_id).where(
        ReviewEvent.event_type == "admin_batch_alert"
    )
    published_subquery = select(Publication.opportunity_id).where(
        Publication.status == "published"
    )
    statement = (
        select(Opportunity)
        .where(Opportunity.review_status.in_(("pending_review", "needs_review")))
        .where(Opportunity.classification != "closed_or_filled_job")
        .where(Opportunity.quarantined.is_(False))
        .where(Opportunity.id.notin_(notified_subquery))
        .where(Opportunity.id.notin_(published_subquery))
        .order_by(Opportunity.id.desc())
        .limit(limit)
    )
    return list(db.scalars(statement).all())


def format_batch_opportunity_alert(
    opportunities: list[Opportunity], platform_url: str = "https://yekis.com"
) -> str:
    """Format a 10-item batch summary digest for Telegram delivery with zero emoji."""
    lines = [
        f"[BATCH REVIEW DIGEST: {len(opportunities)} OPPORTUNITIES REQUIRING HUMAN REVIEW]",
        "The following opportunities require your review before publication:",
        "",
    ]
    for idx, opp in enumerate(opportunities, 1):
        budget = "Not specified"
        if opp.budget_amount is not None:
            amt = f"{opp.budget_amount:,.2f}".rstrip("0").rstrip(".")
            curr = opp.budget_currency or ""
            budget = f"{amt} {curr}".strip()

        mode = "Remote" if opp.remote else ("On-site" if opp.remote is False else "Unspecified")
        skills = ", ".join(opp.skills[:5]) if opp.skills else "None listed"

        lines.append(f"#{idx}. [OPP #{opp.id}] {opp.title}")
        lines.append(f"Category: {opp.category or 'General'} | Type: {opp.opportunity_type}")
        lines.append(
            f"Compensation: {budget} | Duration: {opp.duration or 'Not specified'} | Mode: {mode}"
        )
        lines.append(f"Skills: {skills} | Risk: {opp.risk_level} ({opp.risk_score})")
        if opp.original_url:
            lines.append(f"Source URL: {opp.original_url}")
        lines.append("")

    lines.append("Use the action buttons below to Approve or Reject each item.")
    lines.append(f"Mission Control: {platform_url}/admin/")
    return "\n".join(lines)


def build_batch_inline_keyboard(
    opportunities: list[Opportunity], platform_url: str = ""
) -> dict:
    """Build Telegram inline keyboard with Approve and Reject buttons for each post."""
    keyboard: list[list[dict]] = []
    for _idx, opp in enumerate(opportunities, 1):
        keyboard.append([
            {"text": f"Approve #{opp.id}", "callback_data": f"app:{opp.id}"},
            {"text": f"Reject #{opp.id}", "callback_data": f"rej:{opp.id}"},
        ])
    base_url = platform_url.rstrip("/") if platform_url else "https://yekis.com"
    keyboard.append([{"text": "Open Mission Control Dashboard", "url": f"{base_url}/admin/"}])
    return {"inline_keyboard": keyboard}


def send_batch_review_alert(
    db: Session,
    settings: Settings,
    min_batch_size: int = 10,
    force: bool = False,
) -> int:
    """Send batched review alert to admin Telegram chat when count reaches min_batch_size."""
    from datetime import UTC, datetime

    from app.models import ReviewEvent

    if not settings.admin_alerts_enabled:
        return 0
    if not settings.admin_alert_chat_id or not settings.telegram_bot_token:
        logger.debug("admin batch alert skipped: chat id or bot token missing")
        return 0

    candidates = get_pending_human_review_opportunities(db, limit=min_batch_size)
    if not force and len(candidates) < min_batch_size:
        logger.info(
            "admin batch alert: %s/%s pending opportunities (waiting for %s)",
            len(candidates),
            min_batch_size,
            min_batch_size,
        )
        return 0

    if not candidates:
        return 0

    text = format_batch_opportunity_alert(candidates, settings.platform_url)
    keyboard = build_batch_inline_keyboard(candidates, settings.platform_url)
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.admin_alert_chat_id,
        "text": text,
        "disable_web_page_preview": True,
        "reply_markup": keyboard,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("admin batch alert sent for %s opportunities", len(candidates))
                now = datetime.now(UTC)
                for opp in candidates:
                    note = f"Dispatched in Telegram batch review alert (batch of {len(candidates)})"
                    event = ReviewEvent(
                        opportunity_id=opp.id,
                        actor="system:alerts",
                        event_type="admin_batch_alert",
                        from_status=opp.review_status,
                        to_status=opp.review_status,
                        note=note,
                        created_at=now,
                    )
                    db.add(event)
                db.commit()
                return len(candidates)
            logger.warning(
                "admin batch alert failed: %s %s", resp.status_code, resp.text[:200]
            )
            return 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin batch alert error: %s", exc)
        return 0


def handle_telegram_callback(
    db: Session,
    callback_query: dict,
    bot_token: str,
) -> dict:
    """Handle interactive Approve/Reject button clicks from Telegram inline keyboard."""
    from app.models import Opportunity
    from app.security.auth import Principal
    from app.services.review import apply_review

    query_id = callback_query.get("id")
    data = callback_query.get("data", "")
    from_user = callback_query.get("from", {}).get("username") or str(
        callback_query.get("from", {}).get("id", "telegram_admin")
    )

    if not data or ":" not in data:
        return {"ok": False, "error": "invalid_callback_data"}

    action, opp_id_str = data.split(":", 1)
    try:
        opp_id = int(opp_id_str)
    except ValueError:
        return {"ok": False, "error": "invalid_opportunity_id"}

    opportunity = db.get(Opportunity, opp_id)
    if opportunity is None:
        answer_text = f"Opportunity #{opp_id} not found."
    else:
        decision = "approved" if action == "app" else "rejected"
        principal = Principal(name=f"telegram:{from_user}", role="admin")
        apply_review(
            db,
            opportunity,
            decision=decision,
            note="Decision made via Telegram batch alert button",
            principal=principal,
        )
        answer_text = f"Opportunity #{opp_id} marked as {decision.upper()}."

    # Answer callback query so Telegram dismisses the button loading state
    if bot_token and query_id:
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(
                    f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                    json={
                        "callback_query_id": query_id,
                        "text": answer_text,
                        "show_alert": False,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("answerCallbackQuery failed: %s", exc)

    return {"ok": True, "action": action, "opportunity_id": opp_id, "message": answer_text}
