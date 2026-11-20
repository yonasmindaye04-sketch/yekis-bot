"""Tests for Telegram batch review alerts and interactive callbacks.

Verifies:
1. Zero-emoji standard in batch notification digest.
2. 10-item batch threshold enforcement and deduplication via ReviewEvent.
3. Interactive callback handling (Approve/Reject buttons).
4. Telegram webhook and trigger endpoints.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

from app.config import get_settings
from app.models import Publication, ReviewEvent
from app.services.alerts import (
    build_batch_inline_keyboard,
    format_batch_opportunity_alert,
    get_pending_human_review_opportunities,
    handle_telegram_callback,
    send_batch_review_alert,
)


def _create_opp(
    db_session, source_id: int, ext_id: str, title: str, status: str = "pending_review"
):
    from app.services.ingestion import ingest_post

    opp, _ = ingest_post(
        db_session,
        source_id=source_id,
        external_id=ext_id,
        original_text=f"Looking for a software engineer. Job Title: {title}. Budget $500.",
    )
    opp.review_status = status
    db_session.commit()
    return opp


def test_get_pending_human_review_opportunities_filtering(client, db_session):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Alert Source",
            "source_type": "manual",
            "source_url": "https://t.me/alert_source",
            "is_active": True,
        },
    ).json()

    opp1 = _create_opp(db_session, source["id"], "alt-1", "Backend Dev 1", "pending_review")
    opp2 = _create_opp(db_session, source["id"], "alt-2", "Backend Dev 2", "needs_review")
    opp3 = _create_opp(db_session, source["id"], "alt-3", "Backend Dev 3", "pending_review")

    # Mark opp1 as already batched
    event = ReviewEvent(
        opportunity_id=opp1.id,
        actor="system:alerts",
        event_type="admin_batch_alert",
        from_status="pending_review",
        to_status="pending_review",
        note="Batched",
    )
    db_session.add(event)

    # Mark opp2 as published
    pub = Publication(
        opportunity_id=opp2.id, channel="chan", status="published", created_by="system"
    )
    db_session.add(pub)
    db_session.commit()

    pending = get_pending_human_review_opportunities(db_session, limit=10)
    pending_ids = [o.id for o in pending]

    assert opp1.id not in pending_ids
    assert opp2.id not in pending_ids
    assert opp3.id in pending_ids


def test_batch_alert_zero_emoji_and_formatting(client, db_session):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Alert Source 2",
            "source_type": "manual",
            "source_url": "https://t.me/alert_source_2",
            "is_active": True,
        },
    ).json()

    opps = [
        _create_opp(db_session, source["id"], f"alt-fmt-{i}", f"Role {i}")
        for i in range(3)
    ]

    text = format_batch_opportunity_alert(opps, platform_url="https://yekis.com")
    emoji_pattern = re.compile(r"[\U00010000-\U0010ffff]", flags=re.UNICODE)
    assert not emoji_pattern.findall(text), "Batch alert must contain zero emoji"
    assert "[BATCH REVIEW DIGEST: 3 OPPORTUNITIES REQUIRING HUMAN REVIEW]" in text
    assert "#1. [OPP #" in text
    assert "Mission Control: https://yekis.com/admin/" in text


def test_build_batch_inline_keyboard(client, db_session):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Alert Source 3",
            "source_type": "manual",
            "source_url": "https://t.me/alert_source_3",
            "is_active": True,
        },
    ).json()

    opp1 = _create_opp(db_session, source["id"], "kb-1", "Role KB 1")
    keyboard = build_batch_inline_keyboard([opp1], platform_url="https://yekis.com")
    buttons = keyboard["inline_keyboard"]

    assert len(buttons) == 2  # 1 row for opp1 + 1 row for Mission Control link
    assert buttons[0][0]["callback_data"] == f"app:{opp1.id}"
    assert buttons[0][1]["callback_data"] == f"rej:{opp1.id}"
    assert buttons[1][0]["url"] == "https://yekis.com/admin/"


def test_send_batch_review_alert_logic(client, db_session):
    settings = get_settings()
    settings.admin_alerts_enabled = True
    settings.admin_alert_chat_id = "12345678"
    settings.telegram_bot_token = "mock-bot-token"

    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Alert Source 4",
            "source_type": "manual",
            "source_url": "https://t.me/alert_source_4",
            "is_active": True,
        },
    ).json()

    opp = _create_opp(db_session, source["id"], "send-1", "Role Send 1")

    # 1. With batch size 10 and only 1 candidate, returns 0 without sending
    with patch("httpx.Client.post") as mock_post:
        sent = send_batch_review_alert(db_session, settings, min_batch_size=10, force=False)
        assert sent == 0
        mock_post.assert_not_called()

    # 2. With force=True, dispatches the alert
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        sent = send_batch_review_alert(db_session, settings, min_batch_size=10, force=True)
        assert sent == 1
        mock_post.assert_called_once()

        # Opportunity is now recorded in ReviewEvent
        events = list(
            db_session.query(ReviewEvent)
            .filter_by(opportunity_id=opp.id, event_type="admin_batch_alert")
            .all()
        )
        assert len(events) == 1

    # 3. Second call will find 0 un-batched candidates
    with patch("httpx.Client.post") as mock_post:
        sent = send_batch_review_alert(db_session, settings, min_batch_size=10, force=True)
        assert sent == 0
        mock_post.assert_not_called()


def test_handle_telegram_callback(client, db_session):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Alert Source 5",
            "source_type": "manual",
            "source_url": "https://t.me/alert_source_5",
            "is_active": True,
        },
    ).json()

    opp_app = _create_opp(db_session, source["id"], "cb-app", "Role Callback App")
    opp_rej = _create_opp(db_session, source["id"], "cb-rej", "Role Callback Rej")

    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        # Approve callback
        query_app = {
            "id": "query-1",
            "data": f"app:{opp_app.id}",
            "from": {"id": 9999, "username": "admin_user"},
        }
        res_app = handle_telegram_callback(db_session, query_app, "mock-token")
        assert res_app["ok"] is True
        assert res_app["action"] == "app"

        db_session.refresh(opp_app)
        assert opp_app.review_status == "approved"

        # Reject callback
        query_rej = {
            "id": "query-2",
            "data": f"rej:{opp_rej.id}",
            "from": {"id": 9999, "username": "admin_user"},
        }
        res_rej = handle_telegram_callback(db_session, query_rej, "mock-token")
        assert res_rej["ok"] is True
        assert res_rej["action"] == "rej"

        db_session.refresh(opp_rej)
        assert opp_rej.review_status == "rejected"


def test_telegram_api_endpoints(reviewer_client, client):
    # Webhook endpoint processes callback queries
    webhook_payload = {
        "update_id": 100,
        "callback_query": {
            "id": "cb-api-1",
            "data": "app:999999",  # non-existent opp id handled cleanly
            "from": {"id": 111, "username": "test_admin"},
        },
    }
    resp = client.post("/api/v1/telegram/webhook", json=webhook_payload)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Manual trigger batch alert endpoint (reviewer client has permission)
    resp_trigger = reviewer_client.post("/api/v1/telegram/batch-alert?force=true")
    assert resp_trigger.status_code == 200
    assert resp_trigger.json()["ok"] is True
