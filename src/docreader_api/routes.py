"""HTTP routes. Handlers translate HTTP to domain calls and delegate to the injected services."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import Json

from docreader.domain import DocumentRef, ReadRequest, ReadResult
from docreader.settings import ServiceSettings

from .auth import Client, require_client
from .deps import ApiDeps, Deps
from .errors import ApiError
from .schemas import (
    BatchBody,
    Health,
    JobAccepted,
    JobItemOut,
    JobResults,
    JobStatus,
    Problem,
    ReadBody,
    TemplateOut,
    UploadManifest,
)
from .uploads import MIB, discard_pdfs, order_by_manifest, read_pdf, store_pdfs

V1_PREFIX = "/v1"
_READ_UPLOAD = "/read/upload"
_JOBS_UPLOAD = "/jobs/upload"
_FORM_FIELDS_MB = 1  # room for the non-file fields of a single-PDF upload


def _problem(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description}


_COMMON_PROBLEMS: dict[int | str, dict[str, Any]] = {
    401: _problem("Falta la llave de API o no es válida."),
    422: _problem("Solicitud inválida o documento rechazado."),
    500: _problem("Error interno."),
}
_too_large = _problem(
    "Un PDF supera `DOCREADER_MAX_PDF_MB` o la solicitud supera su tamaño máximo "
    "(lotes: `DOCREADER_MAX_UPLOAD_BATCH_MB`)."
)
_job_missing = _problem("El trabajo no existe o pertenece a otro cliente.")

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


async def read_upload(
    deps: Deps,
    file: Annotated[UploadFile, File(description="PDF a leer.")],
    template_id: Annotated[str, Form(min_length=1, description="Id de la plantilla.")],
    expected: Annotated[
        Json[dict[str, str]] | None,
        Form(description='Objeto JSON con los datos a validar, p. ej. `{"campo": "valor"}`.'),
    ] = None,
    allow_external_ai: Annotated[
        bool, Form(description="Permite IA externa (nube). Por defecto está prohibida.")
    ] = False,
    reference: Annotated[
        str | None, Form(description="Id de correlación del cliente; se devuelve.")
    ] = None,
) -> ReadResult:
    """Lee un PDF subido en la solicitud (multipart) y valida sus campos contra `expected`.
    Responde cuando la lectura termina."""
    request = ReadRequest(
        template_id=template_id,
        document=DocumentRef(kind="upload"),
        expected=expected or {},
        allow_external_ai=allow_external_ai,
        reference=reference,
    )
    return await deps.reader.read(request, await read_pdf(file, deps.settings.max_pdf_mb))


async def create_job(
    body: BatchBody, request: Request, response: Response, deps: Deps, client: Client
) -> JobAccepted:
    """Crea un trabajo asíncrono con los PDF del lote, ubicados por referencia. Responde de
    inmediato; el avance se consulta en `status_url`."""
    _check_batch_size(len(body.items), deps.settings)
    return await _submit(request, response, deps, client, body.to_requests())


async def create_upload_job(
    request: Request,
    response: Response,
    deps: Deps,
    client: Client,
    files: Annotated[list[UploadFile], File(description="PDF del lote.")],
    manifest: Annotated[
        Json[UploadManifest],
        Form(
            description="JSON `UploadManifest`: plantilla, política de IA y un elemento por "
            "archivo (`filename`, `expected`, `reference`)."
        ),
    ],
) -> JobAccepted:
    """Crea un trabajo asíncrono con PDF subidos (multipart) y su manifiesto. Responde de
    inmediato; el avance se consulta en `status_url`."""
    ordered = order_by_manifest([item.filename for item in manifest.items], files)
    upload_dir = deps.settings.upload_dir
    names = await store_pdfs(ordered, upload_dir, deps.settings.max_pdf_mb)
    try:
        return await _submit(request, response, deps, client, manifest.to_requests(names))
    except BaseException:
        await discard_pdfs(upload_dir, names)
        raise


async def get_job(job_id: UUID, deps: Deps, client: Client) -> JobStatus:
    """Avance del trabajo. Solo lo ve el cliente que lo creó."""
    job = await deps.jobs.get_job(job_id, client)
    if job is None:
        raise _job_not_found()
    return JobStatus.of(job)


async def get_job_results(
    job_id: UUID,
    deps: Deps,
    client: Client,
    offset: Annotated[int, Query(ge=0, description="Documentos a saltar.")] = 0,
    limit: Annotated[int, Query(ge=1, le=500, description="Documentos por página.")] = 100,
) -> JobResults:
    """Resultados del lote por páginas, en el orden de envío. Incluye los pendientes."""
    job = await deps.jobs.get_job(job_id, client)
    if job is None:
        raise _job_not_found()
    items = await deps.jobs.list_items(job_id, client, offset, limit)
    return JobResults(
        job_id=job_id,
        total=job.total,
        offset=offset,
        limit=limit,
        items=[JobItemOut.of(item) for item in items],
    )


def _check_batch_size(count: int, settings: ServiceSettings) -> None:
    if count > settings.max_batch_items:
        raise ApiError(
            422,
            "Lote demasiado grande",
            f"Un lote admite como máximo {settings.max_batch_items} documentos.",
        )


async def _submit(
    request: Request, response: Response, deps: ApiDeps, client: str, reads: list[ReadRequest]
) -> JobAccepted:
    """Store the job and enqueue its items. Unknown templates fail before anything is stored."""
    for template_id in {read.template_id for read in reads}:
        deps.templates.get(template_id)
    job_id, item_ids = await deps.jobs.create_job(client, reads)
    await deps.queue.enqueue(item_ids)
    status_url = str(request.app.url_path_for("get_job", job_id=str(job_id)))
    response.headers["Location"] = status_url
    return JobAccepted(job_id=job_id, total=len(item_ids), status_url=status_url)


def _job_not_found() -> ApiError:
    return ApiError(404, "Trabajo no encontrado", "No existe ese trabajo para este cliente.")


def upload_body_caps(settings: ServiceSettings) -> dict[str, int]:
    """Max request body in bytes per multipart route (enforced by `RequestGuard`)."""
    return {
        f"{V1_PREFIX}{_READ_UPLOAD}": (settings.max_pdf_mb + _FORM_FIELDS_MB) * MIB,
        f"{V1_PREFIX}{_JOBS_UPLOAD}": settings.max_upload_batch_mb * MIB,
    }


def build_v1_router(settings: ServiceSettings) -> APIRouter:
    """Protected `/v1` routes; the multipart file count comes from `settings`."""
    router = APIRouter(
        prefix=V1_PREFIX, dependencies=[Depends(require_client)], responses=_COMMON_PROBLEMS
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
    router.add_api_route(
        _READ_UPLOAD,
        read_upload,
        methods=["POST"],
        tags=["Lectura"],
        summary="Leer un PDF subido",
        responses={404: _problem("Plantilla no encontrada."), 413: _too_large},
    )
    router.add_api_route(
        "/jobs",
        create_job,
        methods=["POST"],
        status_code=202,
        tags=["Lotes"],
        summary="Crear un lote por referencia",
        responses={404: _problem("Plantilla no encontrada.")},
    )
    router.add_api_route(
        _JOBS_UPLOAD,
        create_upload_job,
        methods=["POST"],
        status_code=202,
        tags=["Lotes"],
        summary="Crear un lote con PDF subidos",
        responses={
            400: _problem("Multipart mal formado o con más archivos que el máximo del lote."),
            404: _problem("Plantilla no encontrada."),
            413: _too_large,
        },
        form_limits={"max_files": settings.max_batch_items},
    )
    router.add_api_route(
        "/jobs/{job_id}",
        get_job,
        methods=["GET"],
        name="get_job",
        tags=["Lotes"],
        summary="Consultar el estado de un lote",
        responses={404: _job_missing},
    )
    router.add_api_route(
        "/jobs/{job_id}/results",
        get_job_results,
        methods=["GET"],
        tags=["Lotes"],
        summary="Consultar los resultados de un lote",
        responses={404: _job_missing},
    )
    return router
