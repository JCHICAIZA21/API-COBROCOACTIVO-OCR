"""API key authentication: header `X-API-Key`, checked in constant time against every key."""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from .deps import Deps
from .errors import ApiError

API_KEY_HEADER = "X-API-Key"

_api_key_header = APIKeyHeader(
    name=API_KEY_HEADER, auto_error=False, description="Llave de API asignada al cliente."
)


def authenticate(api_key: str | None, keys: Mapping[str, str]) -> str | None:
    """Return the client owning `api_key`, or None. Fails closed when no keys are configured."""
    if not api_key:
        return None
    given = api_key.encode()
    client = None
    for key, name in keys.items():  # no early exit: timing does not reveal which key matched
        if hmac.compare_digest(given, key.encode()):
            client = name
    return client


def unauthorized() -> ApiError:
    return ApiError(
        401,
        "No autorizado",
        f"Falta el header {API_KEY_HEADER} o la llave no es válida.",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def require_client(
    deps: Deps, api_key: Annotated[str | None, Security(_api_key_header)]
) -> str:
    """Identify the caller. Second line of defense: `RequestGuard` already refused bad keys."""
    client = authenticate(api_key, deps.settings.api_keys)
    if client is None:
        raise unauthorized()
    return client


Client = Annotated[str, Depends(require_client)]
