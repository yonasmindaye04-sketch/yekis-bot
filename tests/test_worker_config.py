from app.worker import tasks as _tasks  # noqa: F401  (registers task names)
from app.worker.celery_app import celery_app


def test_worker_uses_late_acks_and_prefetch_one():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.timezone == "UTC"


def test_beat_schedule_and_task_registration():
    schedule = celery_app.conf.beat_schedule["collect-telegram"]
    assert schedule["task"] == "yekis.collect_telegram"
    assert schedule["schedule"] > 0
    assert "yekis.ingest_post" in celery_app.tasks
    assert "yekis.collect_telegram" in celery_app.tasks
