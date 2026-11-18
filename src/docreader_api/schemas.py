"""HTTP contract: request and response bodies of the API (documented in OpenAPI, in Spanish)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docreader.domain import DocumentRef, DocumentTemplate, FieldKind, ReadRequest

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
