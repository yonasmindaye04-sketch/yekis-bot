"""Direct collection runner for scheduled cron workflows (e.g. GitHub Actions)."""

from __future__ import annotations

import asyncio
import sys

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
        try:
            from app.services.autonomy import (
                evaluate_pending_opportunities,
                resolve_config,
                run_autonomous_publish_cycle,
            )

            config = resolve_config(db, settings)
            if config.enabled and config.mode in ("supervised", "production"):
                print(f"Autonomy active (mode={config.mode}): evaluating pending opportunities...")
                outcomes = evaluate_pending_opportunities(db, settings=settings)
                print(f"Evaluated {len(outcomes)} opportunities: {[o.decision for o in outcomes]}")

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
                    f"Autonomy inactive or in shadow mode (enabled={config.enabled}, mode={config.mode})."
                )
        except Exception as auto_exc:
            print(f"Notice: Autonomy cycle encountered: {auto_exc}", file=sys.stderr)

        return 0 if outcome.status in ("succeeded", "completed", "skipped") else 1
    except Exception as exc:
        print(f"Error during collection/publication execution: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
