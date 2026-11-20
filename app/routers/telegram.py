"""Telegram webhook and callback router.

Handles interactive Approve/Reject button clicks from Telegram inline keyboards
and administrative webhook configuration.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.security.auth import Principal, require_role
from app.services.alerts import handle_telegram_callback, send_batch_review_alert

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Receive Telegram webhook updates and process inline button callbacks."""
    callback_query = payload.get("callback_query")
    if not callback_query:
        # Ignore message updates or irrelevant events gracefully
        return {"ok": True, "processed": False, "reason": "no_callback_query"}

    result = handle_telegram_callback(
        db,
        callback_query,
        settings.telegram_bot_token,
    )
    return result


@router.post("/batch-alert")
def trigger_batch_alert(
    force: bool = Query(default=False, description="Send even if fewer than batch size"),
    min_size: int = Query(default=10, description="Minimum batch size"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _principal: Principal = Depends(require_role("admin", "reviewer")),
) -> dict[str, Any]:
    """Manually trigger sending a batch review alert to the admin Telegram chat."""
    sent_count = send_batch_review_alert(
        db,
        settings,
        min_batch_size=min_size,
        force=force,
    )
    return {
        "ok": True,
        "dispatched_count": sent_count,
        "message": f"Dispatched {sent_count} opportunities in batch review alert.",
    }


@router.post("/setup-webhook")
def setup_webhook(
    webhook_url: str = Query(..., description="Full public HTTPS URL to /api/v1/telegram/webhook"),
    settings: Settings = Depends(get_settings),
    _principal: Principal = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Register the webhook URL with Telegram's Bot API."""
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN is not configured")

    api_url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(api_url, json={"url": webhook_url})
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Failed to set webhook: {exc}") from exc
