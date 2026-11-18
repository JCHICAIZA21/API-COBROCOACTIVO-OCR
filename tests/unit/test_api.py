"""HTTP API behaviour, exercised through `create_app` with in-memory collaborators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docreader.domain import (
    DocReaderError,
    DocumentNotFound,
    DocumentRef,
    DocumentRejected,
    ReadRequest,
    TemplateNotFound,
)
from docreader.settings import ServiceSettings
from docreader_api.app import create_app

from .api_fakes import FakeReader, InMemoryJobStore, InMemoryQueue, InMemoryTemplates, make_template

KEY = "llave-cliente-a"
OTHER_KEY = "llave-cliente-b"
AUTH = {"X-API-Key": KEY}
PROBLEM = "application/problem+json"


@dataclass
class Api:
    client: TestClient
    reader: FakeReader
    jobs: InMemoryJobStore
    queue: InMemoryQueue
    upload_dir: Path


def build_api(
    tmp_path: Path,
    *,
    api_keys: dict[str, str] | None = None,
    reader: FakeReader | None = None,
) -> Api:
    settings = ServiceSettings(
        _env_file=None,
        api_keys={KEY: "cliente-a", OTHER_KEY: "cliente-b"} if api_keys is None else api_keys,
        upload_dir=tmp_path / "uploads",
        max_pdf_mb=1,
        max_batch_items=3,
    )
    reader = reader or FakeReader()
    jobs, queue = InMemoryJobStore(), InMemoryQueue()
    app = create_app(
        settings=settings,
        reader=reader,
        templates=InMemoryTemplates(make_template()),
        jobs=jobs,
        queue=queue,
    )
    client = TestClient(app, raise_server_exceptions=False)
    return Api(client, reader, jobs, queue, settings.upload_dir)


@pytest.fixture
def api(tmp_path: Path) -> Api:
    return build_api(tmp_path)


# --- Health and authentication -----------------------------------------------------------------


def test_health_is_public(api: Api) -> None:
    response = api.client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "otra-llave"}, {"X-API-Key": ""}])
def test_protected_routes_reject_missing_or_invalid_key(api: Api, headers: dict[str, str]) -> None:
    response = api.client.get("/v1/templates", headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["status"] == 401
    assert body["title"] == "No autorizado"


def test_without_configured_keys_every_protected_route_is_refused(tmp_path: Path) -> None:
    api = build_api(tmp_path, api_keys={})

    assert api.client.get("/v1/templates", headers=AUTH).status_code == 401
    assert api.client.get("/health").status_code == 200


def test_any_configured_key_is_accepted(api: Api) -> None:
    assert api.client.get("/v1/templates", headers={"X-API-Key": OTHER_KEY}).status_code == 200


def test_unknown_route_is_a_problem(api: Api) -> None:
    response = api.client.get("/v1/no-existe", headers=AUTH)

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM


# --- Templates ---------------------------------------------------------------------------------


def test_templates_list_their_fields(api: Api) -> None:
    response = api.client.get("/v1/templates", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "comparendo",
            "version": "1",
            "description": "Comparendo de tránsito",
            "fields": [
                {
                    "name": "identificacion",
                    "kind": "id_number",
                    "required": True,
                    "key": True,
                    "description": "Cédula",
                },
                {
                    "name": "fecha",
                    "kind": "date",
                    "required": False,
                    "key": False,
                    "description": "",
                },
            ],
        }
    ]


# --- Synchronous read by reference --------------------------------------------------------------

READ_BODY = {
    "template_id": "comparendo",
    "document": {"kind": "path", "value": "lote1/doc.pdf"},
    "expected": {"identificacion": "1023456789"},
    "reference": "fila-7",
}


def test_read_by_reference_returns_the_result(api: Api) -> None:
    response = api.client.post("/v1/read", json=READ_BODY, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["template_id"] == "comparendo"
    assert body["status"] == "ok"
    assert body["reference"] == "fila-7"
    [(request, content)] = api.reader.calls
    assert request == ReadRequest(
        template_id="comparendo",
        document=DocumentRef(kind="path", value="lote1/doc.pdf"),
        expected={"identificacion": "1023456789"},
        allow_external_ai=False,
        reference="fila-7",
    )
    assert content is None


def test_read_passes_the_external_ai_policy(api: Api) -> None:
    api.client.post("/v1/read", json={**READ_BODY, "allow_external_ai": True}, headers=AUTH)

    [(request, _)] = api.reader.calls
    assert request.allow_external_ai is True


def test_read_rejects_upload_references_pointing_to_the_upload_endpoint(api: Api) -> None:
    body = {**READ_BODY, "document": {"kind": "upload", "value": ""}}

    response = api.client.post("/v1/read", json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert "/v1/read/upload" in response.text
    assert api.reader.calls == []


def test_validation_errors_do_not_echo_the_input(api: Api) -> None:
    body = {**READ_BODY, "expected": {"identificacion": 1023456789}}

    response = api.client.post("/v1/read", json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    problem = response.json()
    assert problem["title"] == "Solicitud inválida"
    assert problem["errors"][0]["loc"] == ["body", "expected", "identificacion"]
    assert "1023456789" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "title"),
    [
        (TemplateNotFound("No existe la plantilla."), 404, "Plantilla no encontrada"),
        (DocumentNotFound("No existe el documento."), 404, "Documento no encontrado"),
        (DocumentRejected("El PDF está cifrado."), 422, "Documento rechazado"),
        (DocReaderError("No se pudo leer."), 422, "No se pudo leer el documento"),
    ],
)
def test_domain_errors_become_problems(
    tmp_path: Path, error: Exception, status: int, title: str
) -> None:
    api = build_api(tmp_path, reader=FakeReader(error))

    response = api.client.post("/v1/read", json=READ_BODY, headers=AUTH)

    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM
    assert response.json() == {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": str(error),
    }


def test_unexpected_errors_are_generic_500_problems(tmp_path: Path) -> None:
    api = build_api(tmp_path, reader=FakeReader(RuntimeError("secreto 1023456789")))

    response = api.client.post("/v1/read", json=READ_BODY, headers=AUTH)

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["title"] == "Error interno"
    assert "secreto" not in response.text
    assert "1023456789" not in response.text
