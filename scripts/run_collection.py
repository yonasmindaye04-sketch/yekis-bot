"""Direct collection runner for scheduled cron workflows (e.g. GitHub Actions)."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.integrations.telegram.publisher import TelethonPublisher
from app.services.publishing import publish_due
from app.worker.tasks import collect_job


def _write_step_summary(summary: dict[str, object]) -> None:
    """Write the run summary where GitHub Actions shows it.

    A green workflow run that says nothing is the reason a failing pipeline
    went unnoticed for days. The summary states what the run actually did.
    """
    lines = [
        "## Yekis collector run",
        "",
        f"- mode: {summary.get('mode')}",
        f"- channel: {summary.get('channel') or 'not set'}",
    ]
    collected = summary.get("collected")
    if collected is not None:
        lines.append(f"- posts collected: {collected}")
    lines += [
        f"- opportunities evaluated: {summary.get('evaluated')}",
        f"- decisions: {summary.get('decisions') or 'none'}",
        f"- published: {summary.get('published')}",
    ]
    blocked = str(summary.get("blocked") or "")
    lines.append(f"- publishing blocked: {blocked or 'no'}")
    text = "\n".join(lines) + "\n"

    print("Run summary:")
    for line in lines[2:]:
        print("  " + line[2:])

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - depends on the runner
            print(f"(could not write the step summary: {exc})")


def _collected_count(outcome) -> int:
    """How many posts the collection step actually stored.

    The run summary has to distinguish "the collector found nothing" from
    "the collector stored nothing", because the first one is normal and the
    second one is a failure.
    """
    value = getattr(outcome, "value", None)
    if not isinstance(value, dict):
        return 0
    sources = value.get("sources") or []
    return sum(int(source.get("ingested") or 0) for source in sources)


def main() -> int:
    print("Starting direct Telegram collection job...")
    db = SessionLocal()
    try:
        # Pre-collection self-healing: clear transient scraper errors so sources do not stall
        from sqlalchemy import select

        from app.models import Source

        stalled_sources = list(
            db.scalars(select(Source).where(Source.last_error.is_not(None))).all()
        )
        healed_count = 0
        for s in stalled_sources:
            if s.last_error and any(
                err in s.last_error
                for err in ("iter_messages", "unexpected keyword argument", "TelegramNotConfigured")
            ):
                s.last_error = None
                s.consecutive_failures = 0
                s.next_attempt_at = None
                healed_count += 1
        if healed_count:
            db.commit()
            print(f"Self-healed {healed_count} sources from transient errors.")

        # 1. Scrape new messages and ingest opportunities
        outcome = collect_job(db)
        print(f"Collection finished with status: {outcome.status}")
        if outcome.value:
            print(f"Outcome details: {outcome.value}")

        # 1.5 Auto-reject closed opportunities, and close the posts that are
        # too old to be actionable. Both belong to the scheduled job: the read
        # endpoints must not write.
        try:
            from app.services.review import cleanup_closed_opportunities

            closed_ids = cleanup_closed_opportunities(db)
            if closed_ids:
                print(f"Auto-rejected {len(closed_ids)} closed opportunities: {closed_ids}")
        except Exception as clean_exc:
            print(f"Warning: closed opportunities cleanup encountered an issue: {clean_exc}")

        stale_ids: list[int] = []
        try:
            from app.services.autonomy import sweep_stale_opportunities

            stale_ids = sweep_stale_opportunities(db, settings=settings)
            if stale_ids:
                print(f"Closed {len(stale_ids)} stale opportunities (too old to act on).")
        except Exception as stale_exc:
            print(f"Warning: stale opportunity sweep encountered an issue: {stale_exc}")

        # 2. Check for due / approved publications to broadcast
        print("Checking for due/approved broadcasts...")

        async def _publish():
            publisher = TelethonPublisher(settings=settings)
            try:
                return await publish_due(db, publisher=publisher, settings=settings)
            finally:
                await publisher.close()

        # A failing broadcast must not stop the autonomy cycle below it. The
        # two are independent: a transient Telegram error used to abort the
        # whole run before a single opportunity was classified.
        try:
            published = asyncio.run(_publish())
            print(f"Broadcast processing complete: {len(published)} items published.")
        except Exception as publish_exc:
            print(
                f"WARNING: the broadcast step failed ({publish_exc}); "
                "the autonomy cycle continues.",
                file=sys.stderr,
            )

        # 3. Autonomous evaluation and auto-publishing cycle
        #
        # The autonomy engine is configured through the `autonomy_settings`
        # table (owned by the dashboard) and through the environment. It is off
        # by default: in shadow mode the engine only records what it would
        # approve and publishes nothing.
        try:
            from app.services.autonomy import (
                evaluate_pending_opportunities,
                publishing_blocked,
                resolve_config,
                run_autonomous_publish_cycle,
            )

            config = resolve_config(db, settings)
            summary: dict[str, object] = {
                "mode": config.mode,
                "channel": config.channel or "",
                "collected": _collected_count(outcome),
                "evaluated": 0,
                "decisions": {},
                "published": 0,
                "blocked": "",
            }
            if config.enabled:
                # Shadow mode records what the engine would approve. The
                # supervised and the production modes additionally publish.
                will_publish = config.mode in ("supervised", "production")
                blocked = publishing_blocked(db, config, datetime.now(UTC))
                summary["blocked"] = blocked or ""
                if will_publish and blocked:
                    print(f"Autonomy is enabled but publishing is blocked: {blocked}")
                else:
                    print(
                        f"Autonomy active (mode={config.mode}, "
                        f"channel={config.channel}): evaluating pending opportunities..."
                    )
                    budget = settings.autonomy_evaluation_time_budget_seconds
                    deadline = time.monotonic() + budget if budget > 0 else None
                    outcomes = evaluate_pending_opportunities(
                        db,
                        settings=settings,
                        limit=settings.autonomy_evaluate_limit,
                        deadline=deadline,
                    )
                    counts: dict[str, int] = {}
                    for item in outcomes:
                        counts[item.decision] = counts.get(item.decision, 0) + 1
                    summary["evaluated"] = len(outcomes)
                    summary["decisions"] = counts
                    print(
                        f"Evaluated {len(outcomes)} opportunities: "
                        f"{[o.decision for o in outcomes]}"
                    )

                if will_publish and not blocked:

                    async def _auto_publish():
                        pub = TelethonPublisher(settings=settings)
                        try:
                            return await run_autonomous_publish_cycle(
                                db, publisher=pub, settings=settings
                            )
                        finally:
                            await pub.close()

                    auto_res = asyncio.run(_auto_publish())
                    summary["published"] = len(auto_res.get("published", []))
                    print(f"Autonomous publish cycle result: {auto_res}")
            else:
                print(
                    "Autonomy inactive (enabled=False). Enable it from the admin "
                    "dashboard or with AUTONOMY_ENABLED=true."
                )
            _write_step_summary(summary)
        except Exception as auto_exc:
            print(
                f"ERROR: autonomy cycle failed and nothing was published: {auto_exc}",
                file=sys.stderr,
            )
            raise

        # 4. Batch admin review alerts: notify admin on Telegram when reaching batch size
        try:
            from app.services.alerts import send_batch_review_alert

            dispatched = send_batch_review_alert(
                db,
                settings=settings,
                min_batch_size=settings.admin_alert_batch_size,
            )
            if dispatched:
                print(
                    f"Dispatched {dispatched} opportunities in admin Telegram batch review alert."
                )
        except Exception as alert_exc:
            print(f"Warning: batch review alert notification issue: {alert_exc}")

        return 0 if outcome.status in ("succeeded", "completed", "skipped") else 1
    except Exception as exc:
        print(f"Error during collection/publication execution: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
