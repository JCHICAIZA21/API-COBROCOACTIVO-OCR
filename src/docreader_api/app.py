"""Application factory: builds the HTTP API from injected collaborators (no globals)."""

from __future__ import annotations

from fastapi import FastAPI

from docreader.ports import TemplateRepository
from docreader.settings import ServiceSettings

from .deps import ApiDeps, Reader
from .errors import install_error_handling
from .ports import JobStore, WorkQueue
from .routes import build_v1_router, health_router

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
) -> FastAPI:
    app = FastAPI(
        title="API de lectura de documentos",
        description=_DESCRIPTION,
        version="1.0.0",
        openapi_tags=_TAGS,
    )
    app.state.deps = ApiDeps(settings, reader, templates, jobs, queue)
    install_error_handling(app)
    app.include_router(health_router)
    app.include_router(build_v1_router(settings))
    return app
