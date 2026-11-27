"""HTTP contract: request and response bodies of the API (documented in OpenAPI, in Spanish)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreader.domain import DocumentRef, DocumentTemplate, FieldKind, ReadRequest, ReadResult

from .ports import ItemStatus, JobItem, JobSummary

UPLOAD_ENDPOINTS_HINT = (
    "Los PDF se suben por POST /v1/read/upload o POST /v1/jobs/upload (multipart)."
)

TemplateId = Annotated[str, Field(min_length=1, description="Id de la plantilla del documento.")]
ExpectedValues = Annotated[
    dict[str, str],
    Field(
        default_factory=dict,
        description="Datos de entrada a validar contra el PDF, por nombre de campo.",
    ),
]
AllowExternalAi = Annotated[
    bool,
    Field(
        default=False,
        description="Permite motores de IA externa (nube). Por defecto está prohibido y la "
        "configuración del servicio puede prohibirlo siempre.",
    ),
]
Reference = Annotated[
    str | None,
    Field(default=None, description="Id de correlación del cliente; se devuelve en el resultado."),
]


class _Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Errors and health ------------------------------------------------------------------------


class ProblemError(_Schema):
    loc: list[str | int] = Field(description="Ubicación del dato inválido.")
    msg: str = Field(description="Descripción del error.")


class Problem(_Schema):
    """Error en formato RFC 9457 (`application/problem+json`)."""

    type: str = "about:blank"
    title: str = Field(description="Resumen del problema.")
    status: int = Field(description="Código HTTP.")
    detail: str | None = Field(default=None, description="Explicación para este caso.")
    errors: list[ProblemError] | None = Field(
        default=None, description="Errores de validación, si los hay."
    )


class Health(_Schema):
    status: Literal["ok"] = "ok"


# --- Templates --------------------------------------------------------------------------------


class TemplateFieldOut(_Schema):
    name: str = Field(description="Nombre del campo.")
    kind: FieldKind = Field(description="Tipo de dato del campo.")
    required: bool = Field(description="Si el campo es obligatorio.")
    key: bool = Field(description="Si es un campo clave para la validación.")
    description: str = Field(description="Descripción del campo.")


class TemplateOut(_Schema):
    """Plantilla de documento: qué campos se leen de un tipo de PDF."""

    id: str
    version: str
    description: str
    fields: list[TemplateFieldOut]

    @classmethod
    def of(cls, template: DocumentTemplate) -> TemplateOut:
        return cls(
            id=template.id,
            version=template.version,
            description=template.description,
            fields=[
                TemplateFieldOut(
                    name=f.name,
                    kind=f.kind,
                    required=f.required,
                    key=f.key,
                    description=f.description,
                )
                for f in template.fields
            ],
        )


# --- Reads by reference -----------------------------------------------------------------------


class DocumentByReference(_Schema):
    """PDF que el servicio obtiene por su cuenta."""

    kind: Literal["path", "url", "blob"] = Field(
        description="`path`: ruta bajo el directorio permitido; `url`: URL de un dominio "
        "permitido; `blob`: id del documento en la base de datos."
    )
    value: str = Field(min_length=1, description="Ruta, URL o id del documento.")

    @model_validator(mode="before")
    @classmethod
    def _uploads_have_their_own_endpoints(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("kind") == "upload":
            raise ValueError(UPLOAD_ENDPOINTS_HINT)
        return data

    def to_ref(self) -> DocumentRef:
        return DocumentRef(kind=self.kind, value=self.value)


class ReadBody(_Schema):
    """Solicitud de lectura de un PDF por referencia."""

    template_id: TemplateId
    document: DocumentByReference
    expected: ExpectedValues
    allow_external_ai: AllowExternalAi
    reference: Reference

    def to_request(self) -> ReadRequest:
        return ReadRequest(
            template_id=self.template_id,
            document=self.document.to_ref(),
            expected=self.expected,
            allow_external_ai=self.allow_external_ai,
            reference=self.reference,
        )


# --- Batch jobs -------------------------------------------------------------------------------


class BatchItem(_Schema):
    document: DocumentByReference
    expected: ExpectedValues
    reference: Reference
    template_id: str | None = Field(
        default=None,
        min_length=1,
        description="Plantilla de este documento; si falta, la del lote.",
    )


class BatchBody(_Schema):
    """Lote de lecturas asíncronas de PDF por referencia."""

    template_id: TemplateId
    allow_external_ai: AllowExternalAi
    items: list[BatchItem] = Field(
        min_length=1, description="Documentos del lote (máximo `DOCREADER_MAX_BATCH_ITEMS`)."
    )

    def to_requests(self) -> list[ReadRequest]:
        return [
            ReadRequest(
                template_id=item.template_id or self.template_id,
                document=item.document.to_ref(),
                expected=item.expected,
                allow_external_ai=self.allow_external_ai,
                reference=item.reference,
            )
            for item in self.items
        ]


class JobAccepted(_Schema):
    """Trabajo creado; su avance se consulta en `status_url`."""

    job_id: UUID
    total: int = Field(description="Cantidad de documentos del lote.")
    status_url: str = Field(description="Ruta para consultar el estado del trabajo.")


class JobStatus(_Schema):
    """Avance de un trabajo por lote."""

    job_id: UUID
    created_at: datetime
    total: int
    pending: int
    processing: int
    done: int
    failed: int
    needs_review: int = Field(description="Terminados con estado `needs_review`.")
    finished: bool = Field(description="True cuando no quedan documentos pendientes ni en curso.")

    @classmethod
    def of(cls, job: JobSummary) -> JobStatus:
        return cls(
            job_id=job.id,
            created_at=job.created_at,
            total=job.total,
            pending=job.pending,
            processing=job.processing,
            done=job.done,
            failed=job.failed,
            needs_review=job.needs_review,
            finished=job.finished,
        )


class JobItemOut(_Schema):
    position: int = Field(description="Posición del documento en el lote (desde 0).")
    status: ItemStatus
    reference: str | None = Field(description="Id de correlación enviado por el cliente.")
    result: ReadResult | None = Field(description="Resultado de lectura, cuando terminó bien.")
    error: str | None = Field(description="Motivo del fallo, cuando falló.")

    @classmethod
    def of(cls, item: JobItem) -> JobItemOut:
        return cls(
            position=item.position,
            status=item.status,
            reference=item.request.reference,
            result=item.result,
            error=item.error,
        )


class JobResults(_Schema):
    """Página de resultados de un lote, en el orden de envío."""

    job_id: UUID
    total: int = Field(description="Cantidad total de documentos del lote.")
    offset: int
    limit: int
    items: list[JobItemOut]


# --- Batch jobs of uploaded PDFs --------------------------------------------------------------


class ManifestItem(_Schema):
    filename: str = Field(min_length=1, description="Nombre del archivo de `files` que describe.")
    expected: ExpectedValues
    reference: Reference


class UploadManifest(_Schema):
    """Describe cada archivo de `files` exactamente una vez, en el orden del lote."""

    template_id: TemplateId
    allow_external_ai: AllowExternalAi
    items: list[ManifestItem] = Field(min_length=1)

    def to_requests(self, stored_names: Sequence[str]) -> list[ReadRequest]:
        """One request per item; `stored_names` are the server-side names, in item order."""
        return [
            ReadRequest(
                template_id=self.template_id,
                document=DocumentRef(kind="upload", value=name),
                expected=item.expected,
                allow_external_ai=self.allow_external_ai,
                reference=item.reference,
            )
            for item, name in zip(self.items, stored_names, strict=True)
        ]
