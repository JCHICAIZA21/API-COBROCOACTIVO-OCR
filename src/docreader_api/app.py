"""Application factory: builds the HTTP API from injected collaborators (no globals)."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.types import Lifespan

from docreader.ports import TemplateRepository
from docreader.settings import ServiceSettings

from .deps import ApiDeps, Reader
from .errors import install_error_handling
from .guard import RequestGuard
from .ports import JobStore, WorkQueue
from .routes import V1_PREFIX, build_v1_router, health_router, upload_body_caps

__all__ = ["Reader", "create_app"]

_DESCRIPTION = """
Lee campos de documentos PDF (texto nativo, OCR e IA) y los valida contra los datos de entrada.

- **Lectura síncrona**: `POST /v1/read` (PDF por referencia) y `POST /v1/read/upload` (PDF subido).
- **Lotes asíncronos**: `POST /v1/jobs` y `POST /v1/jobs/upload`; el avance se consulta en
  `GET /v1/jobs/{job_id}` y los resultados en `GET /v1/jobs/{job_id}/results`.

Todas las rutas de `/v1` exigen el header `X-API-Key`. La IA externa está prohibida salvo que la
solicitud la permita (`allow_external_ai`) y la configuración del servicio la habilite.
Los errores siguen el formato RFC 9457 (`application/problem+json`).
"""

_TAGS = [
    {"name": "Lectura", "description": "Lectura síncrona de un PDF."},
    {"name": "Lotes", "description": "Trabajos asíncronos con muchos PDF."},
    {"name": "Plantillas", "description": "Tipos de documento y sus campos."},
    {"name": "Salud", "description": "Estado del servicio."},
]


def create_app(
    *,
    settings: ServiceSettings,
    reader: Reader,
    templates: TemplateRepository,
    jobs: JobStore,
    queue: WorkQueue,
    lifespan: Lifespan[FastAPI] | None = None,
) -> FastAPI:
    """Build the API. `lifespan` opens and closes the collaborators' resources, if any."""
    app = FastAPI(
        title="API de lectura de documentos",
        description=_DESCRIPTION,
        version="1.0.0",
        openapi_tags=_TAGS,
        lifespan=lifespan,
    )
    app.state.deps = ApiDeps(settings, reader, templates, jobs, queue)
    app.add_middleware(
        RequestGuard,
        prefix=V1_PREFIX,
        api_keys=settings.api_keys,
        body_caps=upload_body_caps(settings),
    )
    install_error_handling(app)  # added last: its catch-all middleware wraps the guard
    app.include_router(health_router)
    app.include_router(build_v1_router(settings))
    return app
