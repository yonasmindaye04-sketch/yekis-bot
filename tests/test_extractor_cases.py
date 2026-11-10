import pytest

from app.services.extractor import extract_opportunity


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Need a graphic designer, budget ETB 15,000, 3 days, remote.",
            {
                "opportunity_type": "freelance_job",
                "category": "design",
                "budget_amount": 15000.0,
                "budget_currency": "ETB",
                "duration": "3 days",
                "remote": True,
                "risk_flags": [],
            },
        ),
        (
            "Just a random market news update about the Ethiopian economy today.",
            {
                "opportunity_type": "news",
                "category": None,
                "budget_amount": None,
                "risk_flags": [],
            },
        ),
        (
            (
                "URGENT! Send 500 birr registration fee via telebirr to get a $5000 "
                "remote job. Pay upfront."
            ),
            {
                "opportunity_type": "freelance_job",
                "budget_amount": None,
                "budget_currency": None,
                "risk_flags": ["upfront_payment", "off_platform_contact", "urgency_pressure"],
            },
        ),
        (
            "We need someone who does not react well to pressure, designing interiors.",
            {
                "opportunity_type": "other",
                "category": "design",
                "skills": [],
            },
        ),
        (
            "Need a python developer. Pay $1,000.",
            {
                "opportunity_type": "freelance_job",
                "category": "software",
                "budget_amount": 1000.0,
                "budget_currency": "USD",
                "risk_flags": [],
            },
        ),
    ],
)
def test_audit_probe_cases(text, expected):
    result = extract_opportunity(text)
    for field, value in expected.items():
        assert getattr(result, field) == value, f"{field} for {text!r}"


def test_react_verb_is_not_a_skill_or_software_category():
    result = extract_opportunity(
        "We need someone who does not react well to pressure, designing interiors."
    )
    assert "react" not in result.skills
    assert result.category == "design"


def test_upfront_fee_routes_to_human_review_and_never_becomes_a_budget():
    result = extract_opportunity(
        "URGENT! Send 500 birr registration fee via telebirr to get a $5000 remote job. "
        "Pay upfront."
    )
    assert "upfront_payment" in result.risk_flags
    assert result.risk_score >= 40
    assert result.budget_amount is None
