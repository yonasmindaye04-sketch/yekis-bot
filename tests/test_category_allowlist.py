"""The category allowlist must understand the vocabulary it is given.

The live production list was typed by a human ("graphics", "video editor")
while the extractor emits the canonical categories ("design", "media"). The
exact comparison hid every matching opportunity behind a human review, so
the tests below use the production data as it was.
"""

from __future__ import annotations

import pytest

from app.models import Opportunity
from app.services.autonomy import _category_allowed
from app.services.classification.taxonomy import canonical_category


def _config(categories: tuple[str, ...]):
    from app.services.autonomy import AutonomyConfig

    return AutonomyConfig(
        enabled=True,
        mode="production",
        threshold=0.85,
        approved_source_ids=(),
        approved_categories=categories,
        daily_publish_limit=15,
        min_interval_seconds=900,
        target_channel="@yekis_channel",
    )


def _opportunity(category: str | None, classification: str = "freelance_job") -> Opportunity:
    opportunity = Opportunity()
    opportunity.title = "Graphic Designer"
    opportunity.category = category
    opportunity.classification = classification
    return opportunity


# The list exactly as configured in production.
PRODUCTION_LIST = (
    "software",
    "video editor",
    "graphics",
    "motion graphics",
    "animator",
    "illustrator",
)


def test_a_synonym_entry_unlocks_the_canonical_category():
    # Production case #1053: category "design", the list says "graphics".
    assert _category_allowed(_opportunity("design"), _config(PRODUCTION_LIST)) is True


def test_a_canonical_entry_matches_directly():
    assert _category_allowed(_opportunity("software"), _config(PRODUCTION_LIST)) is True


def test_a_media_synonym_matches_a_media_post():
    assert _category_allowed(_opportunity("media"), _config(PRODUCTION_LIST)) is True


def test_a_category_outside_the_list_is_still_blocked():
    # Production case #1066: a marketing post, the list does not include it.
    assert _category_allowed(_opportunity("marketing"), _config(PRODUCTION_LIST)) is False


def test_an_empty_allowlist_allows_everything():
    assert _category_allowed(_opportunity("marketing"), _config(())) is True


def test_a_classification_can_be_used_as_an_entry():
    assert _category_allowed(_opportunity("marketing", "consulting_opportunity"),
                             _config(("consulting_opportunity",))) is True


def test_a_post_without_a_category_is_blocked_when_a_list_exists():
    assert _category_allowed(_opportunity(None), _config(PRODUCTION_LIST)) is False


def test_a_post_without_a_category_is_allowed_when_the_list_is_empty():
    assert _category_allowed(_opportunity(None), _config(())) is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("graphics", "design"),
        ("Animator", "design"),
        ("Motion Graphics", "design"),
        ("illustrator", "design"),
        ("Video Editor", "media"),
        ("software", "software"),
        ("an unknown thing", None),
        ("freelance_job", None),
    ],
)
def test_canonical_category_maps_the_words_a_human_types(value, expected):
    assert canonical_category(value) == expected
