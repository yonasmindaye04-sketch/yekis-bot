"""Strict parsing of Telegram channel references.

Only public channel/user references are accepted. Invite links are rejected
because following them would make the service join a channel, which is outside
the authorized-collection boundary.
"""

from urllib.parse import urlparse


class InvalidChannelReference(ValueError):
    pass


def channel_reference(source_url: str) -> str:
    raw = source_url.strip()
    if not raw:
        raise InvalidChannelReference("empty channel reference")
    if raw.startswith("@"):
        candidate = raw[1:].strip()
    elif raw.startswith(("https://", "http://")):
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        if host not in ("t.me", "www.t.me", "telegram.me", "www.telegram.me"):
            raise InvalidChannelReference("only t.me channel links are allowed")
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise InvalidChannelReference("missing channel name")
        if parts[0] in ("joinchat", "+") or parts[0].startswith("+"):
            raise InvalidChannelReference("invite links are not allowed; use a public channel")
        candidate = parts[0]
    else:
        candidate = raw.split("/", 1)[0]

    if not candidate or candidate.startswith("+") or not candidate.replace("_", "").isalnum():
        raise InvalidChannelReference("invalid channel name")
    return candidate


def message_url(channel: str, message_id: int) -> str:
    return f"https://t.me/{channel}/{message_id}"
