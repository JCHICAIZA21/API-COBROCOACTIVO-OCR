"""Request guard: refuses bad API keys and oversized uploads before the body is read."""

from __future__ import annotations

from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import API_KEY_HEADER, authenticate, unauthorized
from .errors import ApiError
from .uploads import MIB


class RequestGuard:
    """Pure ASGI middleware for the routes under `prefix`.

    - No valid API key: 401 without touching the body.
    - Routes in `body_caps` (path -> max bytes): 413 when `Content-Length` exceeds the cap, and
      a stream without it is cut off as soon as it passes the cap.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        prefix: str,
        api_keys: Mapping[str, str],
        body_caps: Mapping[str, int],
    ) -> None:
        self.app = app
        self.prefix = prefix
        self.api_keys = api_keys
        self.body_caps = body_caps

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = _route_path(scope) if scope["type"] == "http" else ""
        if path != self.prefix and not path.startswith(f"{self.prefix}/"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if authenticate(headers.get(API_KEY_HEADER), self.api_keys) is None:
            await unauthorized().response()(scope, receive, send)
            return
        cap = self.body_caps.get(path)
        if cap is None:
            await self.app(scope, receive, send)
            return
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cap:
            await _too_large(cap).response()(scope, receive, send)
            return
        await self.app(scope, _capped(receive, cap), send)


def _capped(receive: Receive, cap: int) -> Receive:
    """Wrap `receive` so the body stops with 413 once it passes `cap` bytes."""
    received = 0

    async def capped_receive() -> Message:
        nonlocal received
        message = await receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > cap:
                raise _too_large(cap)  # an HTTPException: the app answers it as a problem
        return message

    return capped_receive


def _too_large(cap: int) -> ApiError:
    return ApiError(
        413,
        "Contenido demasiado grande",
        f"El cuerpo de la solicitud supera el máximo de {cap // MIB} MB.",
    )


def _route_path(scope: Scope) -> str:
    path: str = scope["path"]
    return path.removeprefix(scope.get("root_path", ""))
