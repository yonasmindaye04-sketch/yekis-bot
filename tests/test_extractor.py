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
    assert result.client_name is None


def test_extracts_client_name_from_multiline_organization():
    text = (
        "ICT Expert - Consultant, Addis Ababa, Ethiopia\n"
        "***\n"
        "Organization:\n"
        "International Livestock Research Institute (ILRI)\n\n"
        "Closing date:\n"
        "Sunday, 16 December 2018"
    )
    result = extract_opportunity(text)
    assert result.client_name == "International Livestock Research Institute (ILRI)"


def test_extracts_client_name_from_company_label():
    text = (
        "Senior Backend Engineer\n"
        "Company: Addis Software PLC\n"
        "Budget: $2500 USD\n"
    )
    result = extract_opportunity(text)
    assert result.client_name == "Addis Software PLC"


def test_extract_opportunity_skips_closure_banner_for_title():
    text = (
        "************ Closed ************\n\n"
        "Job Title: Editing Dates/Content and Layout of a Design\n\n"
        "Description: Graphic Design skills needed. Photoshop+Illustrator.\n"
        "Delivery in 48 hours\n\n"
        "#Design\n"
        "@freelance_ethio"
    )
    result = extract_opportunity(text)
    assert result.title == "Editing Dates/Content and Layout of a Design"

