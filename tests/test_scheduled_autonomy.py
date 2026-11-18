"""Tests for the scheduled autonomous path.

The scheduled job (the GitHub Actions collector workflow) is the only place
where automatic publishing can happen without a human. These tests cover the
configuration contract it depends on:

* a blank environment variable must not crash the process;
* the shadow mode records decisions but publishes nothing;
* the overview tells the operator why nothing is being published.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.config import Settings
from app.services.autonomy import publishing_blocked, resolve_config, update_config


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "publishing_enabled": True,
        "publishing_allowed_channels": ["@target"],
        "publishing_max_attempts": 3,
        "platform_url": "https://yekis.com",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_blank_environment_values_fall_back_to_the_defaults(monkeypatch):
    """A secret that exists but is empty must not crash the process."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("AUTONOMY_ENABLED", "")
    monkeypatch.setenv("AUTONOMY_DAILY_PUBLISH_LIMIT", "")
    monkeypatch.setenv("AUTONOMY_AUTO_APPROVAL_THRESHOLD", "")
    monkeypatch.setenv("CLASSIFICATION_PROVIDER", "")
    monkeypatch.setenv("AUTONOMY_MODE", "")

    settings = Settings(_env_file=None)

    assert settings.autonomy_enabled is False
    assert settings.autonomy_daily_publish_limit == 5
    assert settings.autonomy_auto_approval_threshold == 0.95
    assert settings.classification_provider == "rules"
    assert settings.autonomy_mode == "shadow"


def test_evaluate_limit_has_a_safe_default():
    settings = Settings(_env_file=None, database_url="sqlite+pysqlite:///:memory:")
    assert settings.autonomy_evaluate_limit == 5


def test_overview_reports_why_nothing_is_published(db_session):
    from app.routers.autonomy import autonomy_overview

    update_config(
        db_session,
        {
            "enabled": True,
            "mode": "production",
            "target_channel": "@target",
            "approved_source_ids": [1],
        },
        actor="test-admin",
    )
    config = resolve_config(db_session)

    # Everything is configured and nothing has been published yet, so the
    # engine is free to publish.
    assert publishing_blocked(db_session, config, config_now(), _settings()) is None

    payload = autonomy_overview(settings=_settings(), db=db_session)
    assert payload["enabled"] is True
    assert payload["publishing_blocked_reason"] is None

    # In shadow mode the overview tells the operator exactly what is blocking.
    update_config(db_session, {"mode": "shadow"}, actor="test-admin")
    payload = autonomy_overview(settings=_settings(), db=db_session)
    assert payload["publishing_blocked_reason"] == "the autonomy engine is in shadow mode"


def config_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def test_shadow_mode_blocks_publishing(db_session):
    update_config(
        db_session,
        {"enabled": True, "mode": "shadow", "target_channel": "@target"},
        actor="test-admin",
    )
    config = resolve_config(db_session)
    reason = publishing_blocked(db_session, config, config_now(), _settings())
    assert reason == "the autonomy engine is in shadow mode"


def test_disabled_engine_blocks_publishing(db_session):
    update_config(db_session, {"enabled": False}, actor="test-admin")
    config = resolve_config(db_session)
    reason = publishing_blocked(db_session, config, config_now(), _settings())
    assert reason == "autonomous publishing is disabled"


def test_missing_channel_blocks_publishing(db_session):
    update_config(
        db_session,
        {"enabled": True, "mode": "production", "target_channel": None},
        actor="test-admin",
    )
    config = resolve_config(db_session)
    reason = publishing_blocked(db_session, config, config_now(), _settings())
    assert "no channel is configured" in reason


def test_channel_outside_the_allowlist_blocks_publishing(db_session):
    """The channel allowlist is enforced before anything is sent.

    The allowlist comes from the environment of whichever process runs the
    pipeline, so the operator has to see the mismatch instead of a silent
    "nothing happened".
    """
    update_config(
        db_session,
        {"enabled": True, "mode": "production", "target_channel": "@elsewhere"},
        actor="test-admin",
    )
    config = resolve_config(db_session)
    reason = publishing_blocked(
        db_session, config, config_now(), _settings(publishing_allowed_channels=["@target"])
    )
    assert reason is not None
    assert "PUBLISHING_ALLOWED_CHANNELS" in reason
