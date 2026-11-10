"""Service-to-service authentication and role checks.

Credentials are configured as a JSON object in ``AUTH_API_KEYS``:

    {"<key>": {"role": "reviewer", "name": "Ops reviewer"}}

The raw key is never stored in process after parsing: only a SHA-256
fingerprint is kept, and comparisons use ``hmac.compare_digest``. Requests
present the key in ``X-API-Key`` or ``Authorization: Bearer ...``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Depends, Header, HTTPException

from app.config import Settings
from app.config import settings as default_settings

logger = logging.getLogger(__name__)

VALID_ROLES = frozenset({"admin", "reviewer", "collector", "analyst"})
MIN_KEY_LENGTH = 16


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    key_id: str | None = None


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_credentials(raw: str) -> dict[str, Principal]:
    """Parse and validate AUTH_API_KEYS into fingerprint → principal."""
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AUTH_API_KEYS must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("AUTH_API_KEYS must be a JSON object")

    credentials: dict[str, Principal] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
            raise RuntimeError(
                f"AUTH_API_KEYS contains a key shorter than {MIN_KEY_LENGTH} characters"
            )
        if not isinstance(value, dict):
            raise RuntimeError("AUTH_API_KEYS values must be objects")
        role = str(value.get("role", "")).lower()
        if role not in VALID_ROLES:
            raise RuntimeError(f"AUTH_API_KEYS has an invalid role: {role!r}")
        name = str(value.get("name") or role)[:120]
        credentials[_fingerprint(key)] = Principal(
            name=name, role=role, key_id=_fingerprint(key)[:12]
        )
    return credentials


@lru_cache(maxsize=1)
def _default_credentials() -> dict[str, Principal]:
    return load_credentials(default_settings.auth_api_keys)


def credentials_for(settings: Settings) -> dict[str, Principal]:
    if settings is default_settings:
        return _default_credentials()
    return load_credentials(settings.auth_api_keys)


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": "authentication_required", "message": message},
        headers={"WWW-Authenticate": "ApiKey"},
    )


def get_principal(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> Principal:
    candidate = x_api_key or _bearer(authorization)
    if not candidate:
        if default_settings.app_env == "development" and default_settings.auth_allow_anonymous_dev:
            return Principal(name="development-anonymous", role="admin")
        raise _unauthorized("Provide an API key via X-API-Key or Authorization: Bearer")

    fingerprint = _fingerprint(candidate)
    for stored, principal in credentials_for(default_settings).items():
        if hmac.compare_digest(fingerprint, stored):
            return principal
    raise _unauthorized("Invalid API key")


def require_role(*roles: str):
    """Return a dependency that allows the listed roles (admin always passes)."""
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.role == "admin" or principal.role in allowed:
            return principal
        raise HTTPException(
            status_code=403,
            detail={
                "code": "insufficient_role",
                "message": f"role {principal.role!r} cannot perform this action",
            },
        )

    return dependency


def validate_security_config(settings: Settings) -> None:
    """Fail startup loudly when a non-development process has no credentials."""
    credentials = credentials_for(settings)
    if not credentials and settings.app_env != "development":
        raise RuntimeError(
            "AUTH_API_KEYS is empty; configure at least one credential outside development"
        )
    if not credentials:
        logger.warning(
            "AUTH_API_KEYS is empty; every protected route will return 401 until configured"
        )
