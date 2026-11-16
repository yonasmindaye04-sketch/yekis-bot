"""Direct collection runner for scheduled cron workflows (e.g. GitHub Actions)."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from app.config import settings
from app.db import SessionLocal
from app.integrations.telegram.publisher import TelethonPublisher
from app.services.publishing import publish_due
from app.worker.tasks import collect_job


def main() -> int:
    print("Starting direct Telegram collection job...")
    db = SessionLocal()
    try:
        # 1. Scrape new messages and ingest opportunities
        outcome = collect_job(db)
        print(f"Collection finished with status: {outcome.status}")
        if outcome.value:
            print(f"Outcome details: {outcome.value}")

        # 2. Check for due / approved publications to broadcast
        print("Checking for due/approved broadcasts...")

        async def _publish():
            publisher = TelethonPublisher(settings=settings)
            try:
                return await publish_due(db, publisher=publisher, settings=settings)
            finally:
                await publisher.close()

        published = asyncio.run(_publish())
        print(f"Broadcast processing complete: {len(published)} items published.")

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
            if config.enabled:
                # Shadow mode records what the engine would approve. The
                # supervised and the production modes additionally publish.
                will_publish = config.mode in ("supervised", "production")
                blocked = publishing_blocked(db, config, datetime.now(UTC))
                if will_publish and blocked:
                    print(f"Autonomy is enabled but publishing is blocked: {blocked}")
                else:
                    print(
                        f"Autonomy active (mode={config.mode}, "
                        f"channel={config.channel}): evaluating pending opportunities..."
                    )
                    outcomes = evaluate_pending_opportunities(
                        db,
                        settings=settings,
                        limit=settings.autonomy_evaluate_limit,
                    )
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
                    print(f"Autonomous publish cycle result: {auto_res}")
            else:
                print(
                    "Autonomy inactive (enabled=False). Enable it from the admin "
                    "dashboard or with AUTONOMY_ENABLED=true."
                )
        except Exception as auto_exc:
            print(
                f"ERROR: autonomy cycle failed and nothing was published: {auto_exc}",
                file=sys.stderr,
            )
            raise
        return 0 if outcome.status in ("succeeded", "completed", "skipped") else 1
    except Exception as exc:
        print(f"Error during collection/publication execution: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
