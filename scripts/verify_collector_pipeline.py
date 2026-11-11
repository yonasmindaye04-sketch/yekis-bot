"""Verify the Telegram collector pipeline against the live database.

Covers the parts of collection that do not require a Telegram account session:

- source registration and activation filtering
- message ingestion through the shared `ingest_post` service
- duplicate prevention across consecutive runs
- checkpoint advancement and resume-after-checkpoint
- failure handling: the checkpoint must not advance past an unhandled message
- backoff and source health after a failed fetch

The Telegram transport itself is exercised with the fake client so the test
never depends on network access or on a personal account. Live message reads
require a user session (`python -m app.integrations.telegram.login --as user`);
Telegram forbids history reads for bot accounts.

Usage:

    python scripts/verify_collector_pipeline.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# The collector pipeline is verified with the deterministic rules extractor so
# the check stays fast and does not depend on provider latency. Pass
# `--provider configured` to exercise the provider configured in .env.
if "--provider" in sys.argv:
    index = sys.argv.index("--provider")
    provider_choice = sys.argv[index + 1] if index + 1 < len(sys.argv) else "rules"
else:
    provider_choice = "rules"
if provider_choice != "configured":
    os.environ["EXTRACTION_PROVIDER"] = "rules"

from sqlalchemy import func, select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.integrations.telegram.base import ChannelMessage  # noqa: E402
from app.integrations.telegram.client import FakeTelegramClient  # noqa: E402
from app.integrations.telegram.collector import collect_source  # noqa: E402
from app.models import CollectedPost, Opportunity, Source  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)


def _message(message_id: int, text: str) -> ChannelMessage:
    return ChannelMessage(
        message_id=message_id,
        text=text,
        url=f"https://t.me/verify_channel/{message_id}",
    )


def main() -> int:
    with SessionLocal() as db:
        source = Source(
            name="Collector pipeline verification",
            source_type="telegram",
            source_url="https://t.me/verify_collector_channel",
            category="software",
            is_active=True,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id
        print(f"Created telegram source id={source_id}")
        print(f"Extraction provider: {provider_choice}")

        try:
            # --- run 1: collect three messages --------------------------------
            fake = FakeTelegramClient(
                {
                    source.source_url: [
                        _message(101, "Looking for a Python developer. Budget $500. 2 weeks."),
                        _message(102, "Need a graphic designer. Budget ETB 15,000. 3 days."),
                        _message(103, "We are hiring a React developer. Budget $900. 4 weeks."),
                    ]
                }
            )
            first = asyncio.run(collect_source(db, source, fake))
            db.refresh(source)

            check(first.status == "collected", "run 1 collects successfully", first.status)
            check(first.fetched == 3, "run 1 fetched 3 messages", str(first.fetched))
            check(first.ingested == 3, "run 1 ingested 3 opportunities", str(first.ingested))
            check(first.duplicates == 0, "run 1 had no duplicates", str(first.duplicates))
            check(
                source.last_collected_message_id == 103,
                "checkpoint advanced to the highest message id",
                str(source.last_collected_message_id),
            )
            check(source.consecutive_failures == 0, "no failures recorded")
            check(source.last_success_at is not None, "last_success_at recorded")
            check(source.next_attempt_at is None, "backoff cleared after success")

            posts = db.scalar(
                select(func.count())
                .select_from(CollectedPost)
                .where(CollectedPost.source_id == source_id)
            )
            opportunities = db.scalar(
                select(func.count())
                .select_from(Opportunity)
                .join(CollectedPost, Opportunity.collected_post_id == CollectedPost.id)
                .where(CollectedPost.source_id == source_id)
            )
            check(posts == 3, "database holds 3 posts", str(posts))
            check(opportunities == 3, "database holds 3 opportunities", str(opportunities))

            # --- run 2: the checkpoint filters already-seen messages ----------
            second = asyncio.run(collect_source(db, source, fake))
            db.refresh(source)
            check(second.status == "collected", "run 2 completes", second.status)
            check(
                second.fetched == 0,
                "run 2 fetched nothing new because of the checkpoint",
                str(second.fetched),
            )
            check(second.ingested == 0, "run 2 ingested nothing new", str(second.ingested))
            posts_after = db.scalar(
                select(func.count())
                .select_from(CollectedPost)
                .where(CollectedPost.source_id == source_id)
            )
            check(posts_after == 3, "no duplicate rows were created", str(posts_after))

            # --- run 2b: replayed messages are rejected by the constraint ----
            # Rewind the checkpoint so the same messages are delivered again.
            # The unique (source_id, external_id) constraint must reject them.
            source.last_collected_message_id = 100
            db.commit()
            replay = asyncio.run(collect_source(db, source, fake))
            db.refresh(source)
            check(replay.status == "collected", "replay run completes", replay.status)
            check(
                replay.duplicates == 3,
                "replayed messages are counted as duplicates",
                str(replay.duplicates),
            )
            check(replay.ingested == 0, "replay ingested nothing", str(replay.ingested))
            posts_replayed = db.scalar(
                select(func.count())
                .select_from(CollectedPost)
                .where(CollectedPost.source_id == source_id)
            )
            check(
                posts_replayed == 3,
                "the unique constraint prevented duplicate rows",
                str(posts_replayed),
            )
            check(
                source.last_collected_message_id == 103,
                "checkpoint recovered to the highest seen id",
                str(source.last_collected_message_id),
            )

            # --- run 3: new messages continue from the checkpoint --------------
            fake._messages[source.source_url].append(
                _message(104, "Seeking a data analyst. Budget $1,200. 6 weeks.")
            )
            third = asyncio.run(collect_source(db, source, fake))
            db.refresh(source)
            check(third.ingested == 1, "run 3 ingested only the new message", str(third.ingested))
            check(
                source.last_collected_message_id == 104,
                "checkpoint advanced to 104",
                str(source.last_collected_message_id),
            )

            # --- failure: the checkpoint must not move ------------------------
            failing = FakeTelegramClient(
                {source.source_url: []}, fail_with=RuntimeError("telegram unreachable")
            )
            failed = asyncio.run(collect_source(db, source, failing))
            db.refresh(source)
            check(failed.status == "failed", "a failed fetch is reported as failed", failed.status)
            check(
                source.last_collected_message_id == 104,
                "checkpoint did not advance on failure",
                str(source.last_collected_message_id),
            )
            check(
                source.consecutive_failures == 1,
                "consecutive_failures incremented",
                str(source.consecutive_failures),
            )
            check(
                source.next_attempt_at is not None,
                "backoff window scheduled after failure",
            )
            check(
                source.last_error is not None and "unreachable" in source.last_error,
                "failure reason stored",
                str(source.last_error),
            )

            # --- backoff: a run inside the window is skipped ------------------
            calls_before = len(failing.calls)
            skipped = asyncio.run(collect_source(db, source, failing))
            check(
                skipped.status == "skipped",
                "a run inside the backoff window is skipped",
                skipped.status,
            )
            check(
                skipped.skipped_reason == "backoff",
                "skip reason is the backoff window",
                str(skipped.skipped_reason),
            )
            check(
                len(failing.calls) == calls_before,
                "no Telegram request was made during backoff",
            )

            # --- recovery: after the window a healthy run succeeds -----------
            source.next_attempt_at = None
            db.commit()
            recovered = asyncio.run(collect_source(db, source, fake))
            db.refresh(source)
            check(recovered.status == "collected", "recovery run succeeds", recovered.status)
            check(
                source.consecutive_failures == 0,
                "failure counter reset",
                str(source.consecutive_failures),
            )
            check(source.last_error is None, "last_error cleared after recovery")
        finally:
            db.refresh(source)
            source.is_active = False
            db.commit()
            print(f"Deactivated verification source id={source_id}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("all collector pipeline checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
