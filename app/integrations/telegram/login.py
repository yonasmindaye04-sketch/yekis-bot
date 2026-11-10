"""One-time interactive Telethon login.

Run this manually on the machine that will collect messages. The resulting
session file is an account credential: it is written under
TELEGRAM_SESSION_PATH, which is git-ignored, and must never be copied into an
image, a log, or a commit.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from app.config import settings


async def _login() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the environment first.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    from telethon import TelegramClient

    session_path = Path(settings.telegram_session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path), settings.telegram_api_id, settings.telegram_api_hash)
    if settings.telegram_bot_token:
        # Non-interactive bot login. The bot must already be a member/admin of
        # the channels it should read or publish to.
        await client.start(bot_token=settings.telegram_bot_token)
    else:
        # Interactive user login: phone number, login code, and 2FA if enabled.
        await client.start()
    me = await client.get_me()
    print(f"Authorized as {getattr(me, 'username', None) or getattr(me, 'id', 'unknown')}.")
    print(f"Session stored at {session_path} (git-ignored).")
    await client.disconnect()


def main() -> int:
    asyncio.run(_login())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
