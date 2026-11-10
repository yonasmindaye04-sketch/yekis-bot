from app.services.extractor import extract_opportunity


def test_extracts_job_fields_without_inventing_deadline():
    result = extract_opportunity(
        "Looking for a React developer to build a dashboard. "
        "Must know TypeScript and Tailwind. Budget $300. Delivery in 2 weeks."
    )
    assert result.opportunity_type == "freelance_job"
    assert result.category == "software"
    assert "react" in result.skills
    assert result.budget_amount == 300
    assert result.budget_currency == "USD"
    assert result.duration == "2 weeks"
