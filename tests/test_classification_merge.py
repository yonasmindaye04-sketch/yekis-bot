"""How the rule engine and the model are combined.

The two are not two equal votes. The rule engine counts keyword signals; the
model reads the whole post. The rule engine is a veto, the model is the
judgement:

* a rule verdict that contradicts the model (a closed marker, a scam shape) is
  never overruled;
* when both agree, the confidence is the average of the two;
* when the rules merely did not recognise the wording, the model's confidence
  stands, because a different phrasing is not a reason to send a genuine
  opportunity to a human.

The safety rules stay exactly as they were: an invented value, a prompt
injection, a high risk level, or a disagreement never publishes.
"""

from __future__ import annotations

import pytest

from app.services.classification.deterministic import analyze
from app.services.classification.normalize import normalize_post
from app.services.classification.pipeline import classify_post
from app.services.classification.schema import AIPostClassification

# A real consultancy offer that the rule engine under-scored.
UNUSUAL_JOB_POST = """Job Title: Financial analyst

Description: I need a financial analyst or economist with atleast 7 years of
experience in the financial sector for a consultancy job. Currently in proposal
development stage but if we get the contract, we need someone who can make
themselves available from mid January to the end of may.

Responsibility of the analyst is to collect information on financial sector
institutions.

How to apply: send your CV and portfolio to apply@consultancy.et
"""

CLOSED_POST = """Job Title: Video editor

Description: We are looking for a video editor with motion graphics experience.

How to apply: send your portfolio.

---------- Closed ----------
"""


class StubProvider:
    name = "llm"
    model = "stub"

    def __init__(self, classification: str, confidence: float, **overrides):
        payload = {
            "classification": classification,
            "is_genuine_opportunity": classification
            in {
                "freelance_job",
                "project_contract",
                "remote_contract",
                "consulting_opportunity",
                "short_term_contract",
            },
            "is_publishable": classification
            in {
                "freelance_job",
                "project_contract",
                "remote_contract",
                "consulting_opportunity",
                "short_term_contract",
            },
            "confidence": confidence,
            "engagement_type": classification
            if classification == "consulting_opportunity"
            else None,
            "evidence": ["The model read the post."],
        }
        payload.update(overrides)
        self._result = AIPostClassification(**payload)

    def classify(self, text: str):
        return self._result


def test_the_rule_engine_recognises_the_unusual_wording():
    """The rules have to see the offer, not just count the words they know."""
    analysis = analyze(normalize_post(UNUSUAL_JOB_POST))

    assert analysis.classification == "consulting_opportunity"
    assert analysis.offer_groups
    assert "duration" in analysis.offer_groups
    # The rules used to score this post at 0.78, which was below the
    # threshold and sent a real opportunity to a human.
    assert analysis.confidence >= 0.9


def test_the_model_confidence_stands_when_the_rules_only_under_scored():
    provider = StubProvider("consulting_opportunity", 0.93)
    result = classify_post(UNUSUAL_JOB_POST, provider=provider, threshold=0.85)

    assert result.classification == "consulting_opportunity"
    assert result.decision == "auto_approve_eligible"
    # The old blend (0.6 * 0.93 + 0.4 * 0.78) produced 0.87 and the post was
    # still borderline; the rules now confirm an offer with a duration.
    assert result.confidence >= 0.9


def test_a_closed_marker_beats_a_model_that_says_it_is_a_job():
    """A closed post is closed, whatever the model makes of its body."""
    provider = StubProvider("freelance_job", 0.95)
    result = classify_post(CLOSED_POST, provider=provider, threshold=0.85)

    assert result.classification == "closed_or_filled_job"
    assert result.decision == "auto_reject"
    assert not result.is_publishable


def test_an_invented_value_still_blocks_the_automatic_approval():
    provider = StubProvider(
        "consulting_opportunity",
        0.99,
        budget=75000.0,
        currency="ETB",
    )
    result = classify_post(UNUSUAL_JOB_POST, provider=provider, threshold=0.85)

    assert result.ai_notes
    assert result.decision == "human_review"


def test_a_disagreement_still_goes_to_a_human():
    provider = StubProvider("news_and_information", 0.97)
    result = classify_post(UNUSUAL_JOB_POST, provider=provider, threshold=0.85)

    # Precision over recall: the non-publishable label wins, and because the
    # two engines disagree it is a human decision.
    assert result.classification not in {
        "freelance_job",
        "project_contract",
        "remote_contract",
        "consulting_opportunity",
        "short_term_contract",
    }
    assert result.decision == "human_review"
    assert not result.is_publishable


def test_without_a_model_nothing_can_be_auto_approved():
    """No second opinion, no automatic approval - at any threshold."""
    for threshold in (0.85, 0.9, 0.95):
        result = classify_post(UNUSUAL_JOB_POST, use_ai=False, threshold=threshold)
        assert result.decision != "auto_approve_eligible", threshold
        assert result.confidence < threshold, threshold


@pytest.mark.parametrize(
    "text",
    [
        "I need to know how to write a CV and pass an interview. Here are tips.",
        "Looking for a job in finance? Send your CV to jobs@example.com.",
        "Breaking news: inflation rate according to the ministry of statistics.",
        "We are running a bootcamp. Enroll now, course fee 5000 ETB.",
    ],
)
def test_the_broadened_rules_do_not_turn_non_jobs_into_offers(text):
    """Precision is not negotiable while recall improves."""
    analysis = analyze(normalize_post(text))

    assert not analysis.offer_groups
    assert analysis.classification not in {
        "freelance_job",
        "project_contract",
        "remote_contract",
        "consulting_opportunity",
        "short_term_contract",
    }
