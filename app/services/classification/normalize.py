"""Stage 1 — source normalization.

The collected post is ground truth and is never modified. Normalization builds
a *separate* representation that is safe to match against:

- URLs are replaced by a placeholder so a link in a bio cannot look like a
  payment request keyword;
- markdown and chat decoration is stripped;
- whitespace is collapsed so phrase matching cannot be defeated by line breaks
  or zero-width characters;
- forwarded-message preambles are detected but kept: a forwarded job offer is
  still an offer, a forwarded tip is still a tip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_PATTERN = re.compile(
    r"(?:https?://|www\.)\S+|t\.me/\S+|telegram\.me/\S+|\b\S+\.(?:com|net|org|io|co|et|xyz|ru|link|top|info|app)\b\S*",
    re.IGNORECASE,
)
DECORATION_PATTERN = re.compile(r"[*_`~`*]+")
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
FORWARD_PATTERN = re.compile(
    r"^\s*(?:fwd|forwarded|fw)\b.*?:\s*", re.IGNORECASE | re.DOTALL
)
MEDIA_PATTERN = re.compile(
    r"^\s*(?:\[?(?:photo|image|video|document|audio|voice|sticker|media)\]?)"
    r"(?:\s|$)",
    re.IGNORECASE | re.MULTILINE,
)
WHITESPACE_PATTERN = re.compile(r"[ \t\u00a0]+")
NEWLINE_PATTERN = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class NormalizedPost:
    """A normalized view of a post. ``original_text`` is never altered."""

    original_text: str
    text: str
    display_text: str
    urls: tuple[str, ...] = field(default_factory=tuple)
    forwarded: bool = False
    has_media: bool = False

    @property
    def lower(self) -> str:
        return self.text.lower()


def _strip_decoration(value: str) -> str:
    value = ZERO_WIDTH_PATTERN.sub("", value)
    value = DECORATION_PATTERN.sub(" ", value)
    return value


def normalize_post(text: str) -> NormalizedPost:
    """Build the normalized representation used by every later stage."""
    original = text or ""
    urls = tuple(match.group(0) for match in URL_PATTERN.finditer(original))

    forwarded = bool(FORWARD_PATTERN.match(original.strip()))
    has_media = bool(MEDIA_PATTERN.search(original))

    body = URL_PATTERN.sub(" ", original)
    body = _strip_decoration(body)
    body = WHITESPACE_PATTERN.sub(" ", body)
    body = NEWLINE_PATTERN.sub("\n\n", body).strip()

    # The forward preamble is metadata, not content. Removing it prevents
    # "Fwd from Elmi" from being read as a publisher statement.
    display = FORWARD_PATTERN.sub("", body).strip() or body

    return NormalizedPost(
        original_text=original,
        text=display,
        display_text=display,
        urls=urls,
        forwarded=forwarded,
        has_media=has_media,
    )
