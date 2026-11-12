"""Marketplace synchronization service.

Dispatches approved opportunities to an external marketplace webhook
or ingest API when enabled.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings
    from app.models import Opportunity

logger = logging.getLogger(__name__)


def serialize_opportunity_for_marketplace(opportunity: Opportunity) -> dict[str, Any]:
    """Convert an Opportunity into a standardized marketplace job payload."""
    return {
        "intelligence_opportunity_id": opportunity.id,
        "title": opportunity.title,
        "category": opportunity.category,
        "opportunity_type": opportunity.opportunity_type,
        "description": opportunity.description or opportunity.original_text,
        "skills": list(opportunity.skills or []),
        "budget_amount": opportunity.budget_amount,
        "budget_currency": opportunity.budget_currency,
        "duration": opportunity.duration,
        "remote": opportunity.remote,
        "quality_score": opportunity.quality_score,
        "risk_flags": list(opportunity.risk_flags or []),
        "status": opportunity.review_status,
        "original_url": opportunity.original_url,
    }


def dispatch_to_marketplace(opportunity: Opportunity, settings: Settings) -> bool:
    """Send an opportunity to the configured marketplace webhook endpoint."""
    if not settings.marketplace_sync_enabled or not settings.marketplace_webhook_url:
        return False

    payload = serialize_opportunity_for_marketplace(opportunity)
    body = json.dumps(payload, sort_keys=True)
    headers = {"Content-Type": "application/json"}

    if settings.marketplace_webhook_secret:
        sig = hmac.new(
            settings.marketplace_webhook_secret.encode("utf-8"),
            body.encode("utf-8"),
            "sha256",
        ).hexdigest()
        headers["X-Yekis-Signature"] = f"sha256={sig}"

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(settings.marketplace_webhook_url, content=body, headers=headers)
            if resp.status_code in (200, 201, 202):
                logger.info(
                    "marketplace sync succeeded for opportunity #%s",
                    opportunity.id,
                )
                return True
            logger.warning(
                "marketplace sync rejected for opportunity #%s: %s",
                opportunity.id,
                resp.status_code,
            )
            return False
    except Exception as exc:  # noqa: BLE001 - non-blocking sync failure
        logger.warning(
            "marketplace sync error for opportunity #%s: %s",
            opportunity.id,
            exc,
        )
        return False
