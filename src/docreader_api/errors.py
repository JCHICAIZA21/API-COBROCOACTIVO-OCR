"""RFC 9457 problem details: every error leaves the API as `application/problem+json`."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docreader.domain import DocReaderError, DocumentNotFound, DocumentRejected, TemplateNotFound

from .schemas import Problem, ProblemError

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)


class ApiError(StarletteHTTPException):
    """An error reported to the caller as a problem.

    An HTTPException, so FastAPI lets it through even when raised while reading the body.
    """

    def __init__(
        self, status: int, title: str, detail: str, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(status, detail, dict(headers) if headers else None)
        self.title = title

    def response(self) -> JSONResponse:
        return problem_response(self.status_code, self.title, self.detail, headers=self.headers)


# First match wins: subclasses before DocReaderError.
_DOMAIN_PROBLEMS: tuple[tuple[type[DocReaderError], int, str], ...] = (
    (TemplateNotFound, 404, "Plantilla no encontrada"),
    (DocumentNotFound, 404, "Documento no encontrado"),
    (DocumentRejected, 422, "Documento rechazado"),
    (DocReaderError, 422, "No se pudo leer el documento"),
)

_HTTP_TITLES: dict[int, str] = {
    400: "Solicitud mal formada",
    404: "Recurso no encontrado",
    405: "Método no permitido",
    413: "Contenido demasiado grande",
    415: "Tipo de contenido no soportado",
}


def problem_response(
    status: int,
    title: str,
    detail: str | None = None,
    *,
    errors: list[ProblemError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    problem = Problem(title=title, status=status, detail=detail, errors=errors)
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return exc.response()


async def _domain_error(_: Request, exc: Exception) -> JSONResponse:
    status, title = next((s, t) for kind, s, t in _DOMAIN_PROBLEMS if isinstance(exc, kind))
    return problem_response(status, title, str(exc) or None)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Only location and message: the rejected input may hold personal data.
    errors = [
        ProblemError(loc=list(e.get("loc", ())), msg=str(e.get("msg", ""))) for e in exc.errors()
    ]
    return problem_response(
        422, "Solicitud inválida", "La solicitud tiene datos inválidos.", errors=errors
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    title = _HTTP_TITLES.get(exc.status_code) or HTTPStatus(exc.status_code).phrase
    return problem_response(exc.status_code, title, headers=exc.headers)


class UnhandledErrorMiddleware:
    """Answers unexpected errors with a generic 500 problem.

    Logs the exception type and stack frames, never the message: it may carry personal data.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            logger.error(
                "Unhandled %s on %s %s\n%s",
                type(exc).__name__,
                scope.get("method"),
                scope.get("path"),
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            if started:
                raise
            response = problem_response(
                500, "Error interno", "Ocurrió un error inesperado. Intente de nuevo más tarde."
            )
            await response(scope, receive, send)


def install_error_handling(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(DocReaderError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_middleware(UnhandledErrorMiddleware)
