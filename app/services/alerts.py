"""Admin and reviewer alert notifications for newly ingested opportunities.

Strict zero-emoji corporate style.
Non-blocking / error-tolerant: alert failures never interrupt ingestion.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
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
