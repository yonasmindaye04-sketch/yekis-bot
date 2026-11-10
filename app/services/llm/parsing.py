"""Shared strict parsing for provider JSON output."""

import json

from pydantic import ValidationError

from app.services.extractor import ExtractedOpportunity
from app.services.llm.base import ProviderResponseError
from app.services.llm.schema import LLMOpportunity


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_llm_json(content: str) -> ExtractedOpportunity:
    """Parse JSON, then enforce the closed opportunity schema.

    Error messages include field paths only; provider output is never echoed.
    """
    try:
        parsed = json.loads(_strip_fences(content))
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("LLM response was not valid JSON") from exc

    try:
        validated = LLMOpportunity.model_validate(parsed)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ProviderResponseError(
            f"LLM response failed schema validation: {fields}"
        ) from exc

    return validated.to_extracted()
