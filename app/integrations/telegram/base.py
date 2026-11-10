from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class ChannelMessage:
    message_id: int
    text: str
    url: str | None = None
    posted_at: datetime | None = None


class TelegramClientPort(Protocol):
    """Minimal collection surface.

    The real implementation is Telethon; tests use a fake. The collector never
    needs to join channels, send messages, or read a user's private chats.
    """

    async def fetch_messages(
        self, channel: str, min_id: int, limit: int
    ) -> list[ChannelMessage]: ...

    async def close(self) -> None: ...
