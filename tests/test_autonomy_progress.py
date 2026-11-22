"""The autonomy cycle has to make progress.

The cycle used to evaluate the same five oldest opportunities in every run.
They never left the queue, they consumed the whole model budget, and no new
post was ever classified - so the backlog grew every hour and the channel
stayed silent.

The tests here pin the progress rules:

* a never-evaluated opportunity is offered before one that was evaluated;
* an evaluated opportunity is only offered again after the retry window;
* the cycles move forward instead of repeating themselves;
* an opportunity that is too old to be actionable is closed automatically;
* a genuine job post that is phrased unusually is not sent to a human.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.models import Opportunity
from app.services.autonomy import (
    evaluate_opportunity,
    opportunities_pending_evaluation,
    resolve_config,
    sweep_stale_opportunities,
    update_config,
)

JOB_POST = "Looking for a React developer. Budget $300. Delivery in 2 weeks."

# The post from the production review queue: a real consultancy offer that was
# phrased in a way the rule engine did not recognise.
UNUSUAL_JOB_POST = """Job Title: Financial analyst

Description: I need a financial analyst or economist with atleast 7 years of
experience in the financial sector for a consultancy job. Currently in proposal
development stage but if we get the contract, we need someone who can make
themselves available from mid January to the end of may.

Responsibility of the analyst is to collect information on financial sector
institutions, including banks, microfinance institutions, and private equity
firms; assisting with data analysis; and writing a report and a manual.

How to apply: send your CV and portfolio to apply@consultancy.et
"""


class StubProvider:
    """A model that agrees with the rule engine."""

    name = "llm"
    model = "stub"

    def __init__(self, classification: str = "consulting_opportunity", confidence: float = 0.93):
        from app.services.classification.schema import AIPostClassification

        self._result = AIPostClassification(
            classification=classification,
            job_category="data",
            engagement_type=(
                classification if classification in {"consulting_opportunity"} else None
            ),
            is_genuine_opportunity=True,
            is_publishable=True,
            confidence=confidence,
            title="Financial analyst",
            evidence=["The poster seeks a financial analyst for a consultancy."],
        )

    def classify(self, text: str):
        return self._result


@pytest.fixture()
def autonomy_settings():
    return Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        autonomy_evaluate_limit=5,
        autonomy_evaluation_retry_seconds=21600,
        autonomy_stale_after_hours=720,
    )


def _opportunity(db_session, source, external_id: str, text: str = JOB_POST) -> Opportunity:
    from app.services.ingestion import ingest_post

    opportunity, _created = ingest_post(
        db_session,
        source_id=source.id,
        external_id=external_id,
        original_text=text,
    )
    return opportunity


def test_a_never_evaluated_opportunity_comes_first(db_session, source, autonomy_settings):
    """New work is offered before anything that was already looked at."""
    recently = _opportunity(db_session, source, "progress-recent")
    recently.last_evaluated_at = datetime.now(UTC) - timedelta(hours=1)
    recently.evaluation_attempts = 1
    older = _opportunity(db_session, source, "progress-older")
    fresh = _opportunity(db_session, source, "progress-fresh")
    db_session.commit()

    selected = opportunities_pending_evaluation(
        db_session, limit=10, settings=autonomy_settings
    )
    ids = [item.id for item in selected]

    # The one that was evaluated an hour ago is still inside its retry
    # window, so it is not offered again.
    assert recently.id not in ids
    assert fresh.id in ids
    assert older.id in ids
    # Newest first: a fresh post is the only one worth publishing.
    assert ids.index(fresh.id) < ids.index(older.id)


def test_an_evaluated_opportunity_is_not_offered_again_immediately(
    db_session, source, autonomy_settings
):
    opportunity = _opportunity(db_session, source, "retry-window")
    opportunity.last_evaluated_at = datetime.now(UTC) - timedelta(minutes=5)
    opportunity.evaluation_attempts = 1
    db_session.commit()

    selected = opportunities_pending_evaluation(
        db_session, limit=10, settings=autonomy_settings
    )
    assert opportunity.id not in [item.id for item in selected]


def test_an_evaluated_opportunity_is_offered_again_after_the_retry_window(
    db_session, source, autonomy_settings
):
    opportunity = _opportunity(db_session, source, "retry-later")
    opportunity.last_evaluated_at = datetime.now(UTC) - timedelta(
        seconds=autonomy_settings.autonomy_evaluation_retry_seconds + 60
    )
    opportunity.evaluation_attempts = 1
    db_session.commit()

    selected = opportunities_pending_evaluation(
        db_session, limit=10, settings=autonomy_settings
    )
    assert opportunity.id in [item.id for item in selected]


def test_the_cycle_moves_forward_instead_of_repeating_itself(
    db_session, source, autonomy_settings
):
    """Two consecutive runs must not decide the same opportunity twice."""
    update_config(
        db_session,
        {"enabled": True, "mode": "production", "target_channel": "@target"},
        actor="test",
    )
    config = resolve_config(db_session, autonomy_settings)

    first = _opportunity(db_session, source, "run-1")
    evaluated_first = opportunities_pending_evaluation(
        db_session, limit=1, settings=autonomy_settings
    )
    assert [item.id for item in evaluated_first] == [first.id]

    evaluate_opportunity(
        db_session,
        first,
        config=config,
        provider=StubProvider("freelance_job"),
        settings=autonomy_settings,
    )
    db_session.commit()
    assert first.last_evaluated_at is not None
    assert first.evaluation_attempts == 1

    second = _opportunity(db_session, source, "run-2")
    evaluated_second = opportunities_pending_evaluation(
        db_session, limit=1, settings=autonomy_settings
    )
    assert [item.id for item in evaluated_second] == [second.id]


def test_a_stale_opportunity_is_closed_instead_of_waiting_for_a_human(
    db_session, source, autonomy_settings
):
    from app.models import ReviewEvent

    stale = _opportunity(db_session, source, "stale-1")
    stale.created_at = datetime.now(UTC) - timedelta(
        hours=autonomy_settings.autonomy_stale_after_hours + 24
    )
    db_session.commit()

    closed = sweep_stale_opportunities(db_session, settings=autonomy_settings)
    db_session.refresh(stale)

    assert closed == [stale.id]
    assert stale.review_status == "rejected"
    assert stale.approval_method == "auto"
    assert "too old" in (stale.approval_reason or "").lower()

    events = (
        db_session.query(ReviewEvent)
        .filter(ReviewEvent.opportunity_id == stale.id)
        .all()
    )
    assert events and events[0].event_type == "auto_rejection"


def test_a_fresh_opportunity_is_not_closed_by_the_stale_sweep(
    db_session, source, autonomy_settings
):
    fresh = _opportunity(db_session, source, "fresh-1")
    db_session.commit()

    assert sweep_stale_opportunities(db_session, settings=autonomy_settings) == []
    db_session.refresh(fresh)
    assert fresh.review_status != "rejected"


def test_a_genuine_post_with_an_unusual_wording_is_not_sent_to_a_human(
    db_session, source, autonomy_settings
):
    """A different wording is not a reason to ask a person.

    The post offers real consultancy work, pays at a rate the model can read,
    and explains how to apply. The rule engine alone rated it 0.78 because it
    did not see a budget word and did not recognise "I need a ...". The blend
    of the two confidences pushed it below the threshold and it went to human
    review - which is exactly what the operator asked to avoid.
    """
    update_config(
        db_session,
        {
            "enabled": True,
            "mode": "production",
            "target_channel": "@target",
            "auto_approval_threshold": 0.85,
        },
        actor="test",
    )
    config = resolve_config(db_session, autonomy_settings)
    opportunity = _opportunity(db_session, source, "unusual-1", UNUSUAL_JOB_POST)

    outcome = evaluate_opportunity(
        db_session,
        opportunity,
        config=config,
        provider=StubProvider("consulting_opportunity", confidence=0.93),
        settings=autonomy_settings,
    )
    db_session.refresh(opportunity)

    assert outcome.decision == "auto_approved"
    assert opportunity.review_status == "approved"
    assert opportunity.approval_method == "auto"
    assert opportunity.classification == "consulting_opportunity"
    # The model decided, and the rule engine did not object.
    assert opportunity.classification_confidence >= 0.85


def test_the_evaluation_records_that_it_ran(db_session, source, autonomy_settings):
    update_config(
        db_session,
        {"enabled": True, "mode": "production", "target_channel": "@target"},
        actor="test",
    )
    config = resolve_config(db_session, autonomy_settings)
    opportunity = _opportunity(db_session, source, "recorded-1")

    evaluate_opportunity(
        db_session,
        opportunity,
        config=config,
        provider=StubProvider("freelance_job"),
        settings=autonomy_settings,
    )
    db_session.refresh(opportunity)

    assert opportunity.last_evaluated_at is not None
    assert opportunity.evaluation_attempts == 1
