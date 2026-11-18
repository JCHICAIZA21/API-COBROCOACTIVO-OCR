"""HTTP routes. Handlers translate HTTP to domain calls and delegate to the injected services."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from docreader.domain import ReadResult
from docreader.settings import ServiceSettings

from .auth import require_client
from .deps import Deps
from .schemas import Health, Problem, ReadBody, TemplateOut


def _problem(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description}


_COMMON_PROBLEMS: dict[int | str, dict[str, Any]] = {
    401: _problem("Falta la llave de API o no es válida."),
    422: _problem("Solicitud inválida o documento rechazado."),
    500: _problem("Error interno."),
}

health_router = APIRouter(tags=["Salud"])


@health_router.get("/health", summary="Estado del servicio")
async def health() -> Health:
    """Responde `ok` si el servicio está en línea. No requiere llave."""
    return Health()


async def list_templates(deps: Deps) -> list[TemplateOut]:
    """Plantillas disponibles y los campos que lee cada una."""
    return [TemplateOut.of(t) for t in deps.templates.all()]


async def read(body: ReadBody, deps: Deps) -> ReadResult:
    """Lee un PDF ubicado por referencia (`path`, `url` o `blob`) y valida sus campos contra
    `expected`. Responde cuando la lectura termina."""
    return await deps.reader.read(body.to_request())


def build_v1_router(settings: ServiceSettings) -> APIRouter:
    """Protected `/v1` routes; upload limits come from `settings`."""
    router = APIRouter(
        prefix="/v1", dependencies=[Depends(require_client)], responses=_COMMON_PROBLEMS
    )
    router.add_api_route(
        "/templates",
        list_templates,
        methods=["GET"],
        tags=["Plantillas"],
        summary="Listar plantillas",
    )
    router.add_api_route(
        "/read",
        read,
        methods=["POST"],
        tags=["Lectura"],
        summary="Leer un PDF por referencia",
        responses={404: _problem("Plantilla o documento no encontrado.")},
    )
    return router
