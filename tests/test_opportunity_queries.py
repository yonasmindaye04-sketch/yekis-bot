"""The review queue must not turn into one query per row.

The dashboard opens the review queue on a remote database. The answer
contains `is_published`, which reads the publications relationship of every
opportunity. With a lazy load that is one round trip to the database per row:
the queue took more than ten seconds to open, and the operator saw a spinner
instead of the work waiting for them.

This test counts the queries the endpoint makes.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine


def _count_queries(db_session) -> list[str]:
    statements: list[str] = []
    engine: Engine = db_session.get_bind()

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    return statements


def test_the_review_queue_does_not_run_a_query_per_row(client, db_session):
    source = client.post(
        "/api/v1/sources",
        json={
            "name": "Query count source",
            "source_type": "manual",
            "source_url": "https://t.me/query_count_source",
            "is_active": True,
        },
    ).json()
    for index in range(12):
        client.post(
            "/api/v1/ingestion/manual",
            json={
                "source_id": source["id"],
                "external_id": f"qc-{index}",
                "original_text": "Looking for a React developer. Budget $300. Two weeks.",
            },
        )

    statements = _count_queries(db_session)
    response = client.get("/api/v1/opportunities?status=actionable&limit=100")
    assert response.status_code == 200
    assert len(response.json()) == 12

    # The publications are loaded with one extra query, not one per row.
    assert len(statements) <= 4, f"the list endpoint ran {len(statements)} queries"


def test_the_queue_summary_is_a_handful_of_queries(client, db_session):
    statements = _count_queries(db_session)
    response = client.get("/api/v1/opportunities/queue-summary")
    assert response.status_code == 200
    assert "actionable" in response.json()
    assert len(statements) <= 6, f"the summary ran {len(statements)} queries"
