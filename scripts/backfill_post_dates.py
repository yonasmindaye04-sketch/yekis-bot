"""Fill in the publication date of the posts that were collected without one.

The first collections scraped the whole history of the channels, so most of
the collected posts were published years ago. Without the publication date
the queue cannot tell a fresh advert from a dead one, and the review queue
fills with posts nobody can act on.

The script reads the date of every collected post from Telegram
(``get_messages`` accepts a list of ids and returns them in order) and
stores it on the post. It is safe to run repeatedly: posts that already have
a date are left alone.

    python -m scripts.backfill_post_dates
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import CollectedPost, Source


async def _backfill() -> int:
    from app.integrations.telegram.client import TelethonClient

    db = SessionLocal()
    updated = 0
    try:
        posts = list(
            db.scalars(
                select(CollectedPost).where(CollectedPost.published_at.is_(None)).order_by(
                    CollectedPost.source_id, CollectedPost.id
                )
            ).all()
        )
        if not posts:
            print("Every collected post already has a publication date.")
            return 0

        sources = {
            source.id: source
            for source in db.scalars(select(Source).where(Source.is_active.is_(True))).all()
        }
        by_source: dict[int, list[CollectedPost]] = {}
        for post in posts:
            by_source.setdefault(post.source_id, []).append(post)

        client = TelethonClient(settings=settings)
        try:
            for source_id, source_posts in by_source.items():
                source = sources.get(source_id)
                if source is None or not source.source_url:
                    print(f"source {source_id} is missing or inactive, skipped")
                    continue
                ids = [int(post.external_id) for post in source_posts]
                dates = await client.get_message_dates(source.source_url, ids)
                for post in source_posts:
                    published_at = dates.get(int(post.external_id))
                    if published_at is None:
                        continue
                    post.published_at = published_at
                    updated += 1
                db.commit()
                print(f"source {source_id}: updated {len(source_posts)} posts")
        finally:
            await client.close()
    finally:
        db.close()
    return updated


def main() -> int:
    updated = asyncio.run(_backfill())
    print(f"Backfilled {updated} post dates at {datetime.now(UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
