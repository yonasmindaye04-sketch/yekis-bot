"""One-time interactive Telegram login.

Two account types are supported and they are NOT interchangeable:

- ``--as user`` (recommended for collection): a real Telegram account signed in
  with phone number, login code, and optional 2FA. Required to read channel
  history, because Telegram restricts ``GetHistoryRequest`` for bots.
- ``--as bot`` (recommended for publishing): a bot token from BotFather. Bots
  may send messages but cannot read channel history.

Sessions are account credentials: they are written under a git-ignored path and
must never be copied into an image, a log, or a commit.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.config import settings


async def _login(as_bot: bool) -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the environment first.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if as_bot and not settings.telegram_bot_token:
        print(
            "Set TELEGRAM_BOT_TOKEN in the environment (create one with @BotFather).",
            file=sys.stderr,
        )
        raise SystemExit(2)

    from telethon import TelegramClient

    session_path = Path(
        settings.telegram_bot_session_path if as_bot else settings.telegram_session_path
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=settings.telegram_proxy,
    )

    if as_bot:
        # Non-interactive bot login. The bot must already be a member/admin of
        # the channels it should publish to.
        await client.start(bot_token=settings.telegram_bot_token)
    else:
        # Interactive user login: phone number, login code, and 2FA if enabled.
        await client.start()

    me = await client.get_me()
    label = "bot" if getattr(me, "bot", False) else "user"
    print(f"Authorized as {label} {getattr(me, 'username', None) or getattr(me, 'id', 'unknown')}.")
    print(f"Session stored at {session_path} (git-ignored).")
    if not as_bot:
        print("This session can read channel history for the collector.")
    else:
        print("This session can publish. Bots cannot read channel history.")
    await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Log in to Telegram")
    parser.add_argument(
        "--as",
        dest="account",
        choices=["user", "bot"],
        default="bot" if settings.telegram_bot_token else "user",
        help=(
            "account type: 'user' for reading channel history (collector), "
            "'bot' for publishing"
        ),
    )
    args = parser.parse_args(argv)
    asyncio.run(_login(as_bot=args.account == "bot"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
