from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import CollectedPost, DeadLetterJob, JobExecution, Opportunity
from app.services.jobs import JobAlreadyRunning, retry_delay, run_job
from app.worker.tasks import ingest_job


def _no_sleep(_: float) -> None:
    return None


def test_transient_failure_is_retried_then_succeeds_once(db_session):
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("telegram temporarily unavailable")
        return {"message": "ok"}

    outcome = run_job(
        db_session,
        task_name="test.flaky",
        idempotency_key="flaky-1",
        fn=flaky,
        max_attempts=5,
        base_seconds=1,
        max_seconds=10,
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )

    assert outcome.status == "succeeded"
    assert outcome.attempts == 3
    assert outcome.value == {"message": "ok"}
    jobs = db_session.scalars(select(JobExecution)).all()
    assert len(jobs) == 1
    assert jobs[0].attempts == 3
    assert jobs[0].status == "succeeded"
    assert db_session.scalar(select(func.count()).select_from(DeadLetterJob)) == 0


def test_exhausted_retries_land_in_dead_letter(db_session):
    def always_fails():
        raise RuntimeError("permanent failure")

    outcome = run_job(
        db_session,
        task_name="test.dead",
        idempotency_key="dead-1",
        fn=always_fails,
        payload={"source_id": 1},
        max_attempts=3,
        base_seconds=1,
        max_seconds=5,
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )

    assert outcome.status == "dead_lettered"
    assert outcome.attempts == 3
    letters = db_session.scalars(select(DeadLetterJob)).all()
    assert len(letters) == 1
    assert letters[0].idempotency_key == "dead-1"
    assert "permanent failure" in letters[0].error
    assert letters[0].attempts == 3


def test_succeeded_key_is_not_executed_again(db_session):
    calls = {"count": 0}

    def work():
        calls["count"] += 1
        return {"value": calls["count"]}

    first = run_job(
        db_session, task_name="test.once", idempotency_key="once-1", fn=work
    )
    second = run_job(
        db_session, task_name="test.once", idempotency_key="once-1", fn=work
    )

    assert first.status == second.status == "succeeded"
    assert calls["count"] == 1
    assert second.value == {"value": 1}
    assert db_session.scalar(select(func.count()).select_from(JobExecution)) == 1


def test_dead_lettered_key_is_not_retried_without_a_new_key(db_session):
    calls = {"count": 0}

    def work():
        calls["count"] += 1
        raise RuntimeError("nope")

    run_job(
        db_session,
        task_name="test.dead",
        idempotency_key="dead-2",
        fn=work,
        max_attempts=2,
        base_seconds=1,
        max_seconds=1,
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )
    outcome = run_job(
        db_session,
        task_name="test.dead",
        idempotency_key="dead-2",
        fn=work,
        max_attempts=2,
        sleep=_no_sleep,
    )

    assert outcome.status == "dead_lettered"
    assert calls["count"] == 2


def test_running_key_is_not_executed_concurrently(db_session):
    job = JobExecution(
        task_name="test.running",
        idempotency_key="running-1",
        status="running",
        attempts=1,
        max_attempts=3,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(JobAlreadyRunning):
        run_job(
            db_session,
            task_name="test.running",
            idempotency_key="running-1",
            fn=lambda: {"never": True},
        )


def test_ingestion_retry_does_not_duplicate_rows(db_session, source):
    calls = {"count": 0}

    def flaky_then_ingest():
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("database blip")
        return {"attempt": calls["count"]}

    # The job runner retries before the outer ingestion wrapper observes a
    # second row; the unique constraint remains the final backstop.
    outcome = run_job(
        db_session,
        task_name="test.ingest-retry",
        idempotency_key="ingest-retry-1",
        fn=flaky_then_ingest,
        max_attempts=3,
        base_seconds=1,
        sleep=_no_sleep,
        jitter=lambda: 0.0,
    )
    assert outcome.status == "succeeded"
    assert outcome.attempts == 2

    payload = {
        "source_id": source.id,
        "external_id": "retry-message-1",
        "original_text": "Need a python developer. Pay $1,000.",
        "idempotency_key": "ingest:source:retry-message-1",
    }
    first = ingest_job(db_session, **payload, sleep=_no_sleep)
    replay = ingest_job(db_session, **payload, sleep=_no_sleep)

    assert first.status == replay.status == "succeeded"
    assert first.value["created"] is True
    # Same key: the stored result is returned and fn is not executed again.
    assert replay.value == first.value

    # A new key re-enters the ingestion wrapper; the explicit duplicate branch
    # reports success without creating a second row.
    retry_payload = {**payload, "idempotency_key": "ingest:source:retry-message-1:replay"}
    duplicate = ingest_job(db_session, **retry_payload, sleep=_no_sleep)
    assert duplicate.status == "succeeded"
    assert duplicate.value["created"] is False

    assert db_session.scalar(select(func.count()).select_from(CollectedPost)) == 1
    assert db_session.scalar(select(func.count()).select_from(Opportunity)) == 1


def test_retry_delay_is_exponential_and_bounded():
    delays = [
        retry_delay(attempt, base_seconds=2, max_seconds=10, jitter=lambda: 0.0)
        for attempt in range(1, 7)
    ]
    assert delays == [2, 4, 8, 10, 10, 10]
    assert all(delay <= 11 for delay in delays)
