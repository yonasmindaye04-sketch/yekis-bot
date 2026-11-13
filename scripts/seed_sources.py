"""Seed active production sources if they don't already exist."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Source


def seed() -> None:
    db = SessionLocal()
    try:
        sources_to_seed = [
            {
                "name": "afriwork",
                "source_type": "telegram",
                "source_url": "https://t.me/freelance_ethio",
                "category": "freelance",
                "is_active": True,
            },
            {
                "name": "hahu jobs",
                "source_type": "telegram",
                "source_url": "https://t.me/hahujobs",
                "category": "jobs",
                "is_active": True,
            },
        ]
        for src_data in sources_to_seed:
            existing = (
                db.execute(select(Source).where(Source.source_url == src_data["source_url"]))
                .scalars()
                .first()
            )
            if not existing:
                src = Source(**src_data)
                db.add(src)
                print(f"Seeded source: {src_data['name']} ({src_data['source_url']})")
            else:
                existing.is_active = True
                existing.next_attempt_at = None
                existing.consecutive_failures = 0
                existing.last_error = None
                print(f"Source active and backoff reset: {existing.name} (id={existing.id})")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
