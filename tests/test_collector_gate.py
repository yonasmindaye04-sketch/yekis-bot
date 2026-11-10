from types import SimpleNamespace

import app.worker.tasks as tasks


def test_worker_collection_is_skipped_when_telegram_is_disabled(db_session, monkeypatch):
    monkeypatch.setattr(
        tasks, "settings", SimpleNamespace(telegram_ready=False)
    )

    outcome = tasks.collect_job(db_session)

    assert outcome.status == "skipped"
    assert outcome.attempts == 0
    assert outcome.value == {"reason": "telegram_disabled_or_unconfigured"}
