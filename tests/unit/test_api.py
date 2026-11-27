"""HTTP API behaviour, exercised through `create_app` with in-memory collaborators."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from docreader.domain import (
    DocReaderError,
    DocumentNotFound,
    DocumentRef,
    DocumentRejected,
    DocumentTemplate,
    ReadRequest,
    ReadStatus,
    TemplateNotFound,
)
from docreader.settings import ServiceSettings
from docreader_api.app import create_app

from .api_fakes import (
    FakeReader,
    InMemoryJobStore,
    InMemoryQueue,
    InMemoryTemplates,
    make_result,
    make_template,
)

KEY = "llave-cliente-a"
OTHER_KEY = "llave-cliente-b"
AUTH = {"X-API-Key": KEY}
PROBLEM = "application/problem+json"


@dataclass
class Api:
    app: FastAPI
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
    templates: list[DocumentTemplate] | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> Api:
    settings = ServiceSettings(
        _env_file=None,
        api_keys={KEY: "cliente-a", OTHER_KEY: "cliente-b"} if api_keys is None else api_keys,
        upload_dir=tmp_path / "uploads",
        max_pdf_mb=1,
        max_batch_items=3,
        max_upload_batch_mb=3,
    )
    reader = reader or FakeReader()
    jobs, queue = InMemoryJobStore(), InMemoryQueue()
    app = create_app(
        settings=settings,
        reader=reader,
        templates=InMemoryTemplates(*(templates or [make_template()])),
        jobs=jobs,
        queue=queue,
        lifespan=lifespan,
    )
    client = TestClient(app, raise_server_exceptions=False)
    return Api(app, client, reader, jobs, queue, settings.upload_dir)


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


# --- Synchronous read of an uploaded PDF --------------------------------------------------------

PDF = b"%PDF-1.7\n" + b"0" * 1000
MIB = 1024 * 1024


def upload_form(**overrides: str) -> dict[str, str]:
    return {"template_id": "comparendo", **overrides}


def test_read_upload_sends_the_bytes_to_the_reader(api: Api) -> None:
    response = api.client.post(
        "/v1/read/upload",
        files={"file": ("doc.pdf", PDF, "application/pdf")},
        data=upload_form(
            expected='{"identificacion": "1023456789"}',
            allow_external_ai="true",
            reference="fila-9",
        ),
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["reference"] == "fila-9"
    [(request, content)] = api.reader.calls
    assert request == ReadRequest(
        template_id="comparendo",
        document=DocumentRef(kind="upload"),
        expected={"identificacion": "1023456789"},
        allow_external_ai=True,
        reference="fila-9",
    )
    assert content == PDF


def test_read_upload_defaults_to_no_expected_values_and_no_external_ai(api: Api) -> None:
    response = api.client.post(
        "/v1/read/upload",
        files={"file": ("doc.pdf", PDF, "application/pdf")},
        data=upload_form(),
        headers=AUTH,
    )

    assert response.status_code == 200
    [(request, _)] = api.reader.calls
    assert request.expected == {}
    assert request.allow_external_ai is False
    assert request.reference is None


@pytest.mark.parametrize("expected", ["no es json", "[1, 2]", '{"identificacion": 10234}'])
def test_read_upload_rejects_expected_that_is_not_a_json_object_of_strings(
    api: Api, expected: str
) -> None:
    response = api.client.post(
        "/v1/read/upload",
        files={"file": ("doc.pdf", PDF, "application/pdf")},
        data=upload_form(expected=expected),
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert "10234" not in response.text
    assert api.reader.calls == []


def test_read_upload_enforces_the_pdf_size_limit(api: Api) -> None:
    response = api.client.post(
        "/v1/read/upload",
        files={"file": ("doc.pdf", b"0" * (MIB + 1), "application/pdf")},
        data=upload_form(),
        headers=AUTH,
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert api.reader.calls == []


def test_read_upload_cuts_off_bodies_far_above_the_limit(api: Api) -> None:
    response = api.client.post(
        "/v1/read/upload",
        files={"file": ("doc.pdf", b"0" * (3 * MIB), "application/pdf")},
        data=upload_form(),
        headers=AUTH,
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert api.reader.calls == []


def test_read_upload_requires_the_file(api: Api) -> None:
    response = api.client.post("/v1/read/upload", data=upload_form(), headers=AUTH)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM


# --- Batch jobs by reference --------------------------------------------------------------------

JOB_BODY = {
    "template_id": "comparendo",
    "allow_external_ai": True,
    "items": [
        {"document": {"kind": "path", "value": "a.pdf"}, "expected": {"identificacion": "1"}},
        {
            "document": {"kind": "url", "value": "https://docs.example/b.pdf"},
            "reference": "fila-2",
            "template_id": "otra",
        },
    ],
}


def test_create_job_stores_and_enqueues_every_item(tmp_path: Path) -> None:
    api = build_api(tmp_path, templates=[make_template(), make_template("otra")])

    response = api.client.post("/v1/jobs", json=JOB_BODY, headers=AUTH)

    assert response.status_code == 202
    body = response.json()
    assert body["total"] == 2
    assert response.headers["location"] == body["status_url"] == f"/v1/jobs/{body['job_id']}"
    job_items = sorted(api.jobs.items.values(), key=lambda item: item.position)
    assert [item.request for item in job_items] == [
        ReadRequest(
            template_id="comparendo",
            document=DocumentRef(kind="path", value="a.pdf"),
            expected={"identificacion": "1"},
            allow_external_ai=True,
        ),
        ReadRequest(
            template_id="otra",
            document=DocumentRef(kind="url", value="https://docs.example/b.pdf"),
            allow_external_ai=True,
            reference="fila-2",
        ),
    ]
    assert api.queue.enqueued == [item.id for item in job_items]
    assert {job.client for job in api.jobs.jobs.values()} == {"cliente-a"}


def test_job_status_is_served_at_the_location(api: Api) -> None:
    body = {**JOB_BODY, "items": JOB_BODY["items"][:1]}
    location = api.client.post("/v1/jobs", json=body, headers=AUTH).headers["location"]

    response = api.client.get(location, headers=AUTH)

    assert response.status_code == 200
    status = response.json()
    assert status["total"] == 1
    assert status["pending"] == 1
    assert status["done"] == status["failed"] == status["needs_review"] == 0
    assert status["finished"] is False


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"document": {"kind": "path", "value": "a.pdf"}}] * 4,  # max_batch_items is 3
        [{"document": {"kind": "upload", "value": "x.pdf"}}],
    ],
    ids=["empty", "too-many", "upload"],
)
def test_create_job_rejects_invalid_batches(api: Api, items: list[object]) -> None:
    response = api.client.post("/v1/jobs", json={**JOB_BODY, "items": items}, headers=AUTH)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert api.jobs.jobs == {}
    assert api.queue.enqueued == []


def test_create_job_rejects_unknown_templates_before_storing(api: Api) -> None:
    response = api.client.post("/v1/jobs", json=JOB_BODY, headers=AUTH)  # "otra" is unknown

    assert response.status_code == 404
    assert response.json()["title"] == "Plantilla no encontrada"
    assert api.jobs.jobs == {}


# --- Job status and results ---------------------------------------------------------------------

THREE_ITEMS = {
    "template_id": "comparendo",
    "items": [
        {"document": {"kind": "path", "value": f"{n}.pdf"}, "reference": f"fila-{n}"}
        for n in range(3)
    ],
}


def create_job(api: Api) -> str:
    return api.client.post("/v1/jobs", json=THREE_ITEMS, headers=AUTH).json()["job_id"]


def progress(api: Api) -> None:
    """Item 0 done (needs review), item 1 failed, item 2 still pending."""
    first, second, _ = sorted(api.jobs.items.values(), key=lambda item: item.position)
    asyncio.run(
        api.jobs.complete_item(first.id, make_result(first.request, ReadStatus.NEEDS_REVIEW))
    )
    asyncio.run(api.jobs.fail_item(second.id, "Documento no encontrado."))


def test_job_status_counts_progress(api: Api) -> None:
    job_id = create_job(api)
    progress(api)

    status = api.client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()

    assert status["job_id"] == job_id
    assert (status["total"], status["pending"], status["done"], status["failed"]) == (3, 1, 1, 1)
    assert status["needs_review"] == 1
    assert status["finished"] is False


def test_job_results_are_paginated_in_order(api: Api) -> None:
    job_id = create_job(api)
    progress(api)

    response = api.client.get(f"/v1/jobs/{job_id}/results?offset=0&limit=2", headers=AUTH)

    assert response.status_code == 200
    page = response.json()
    assert (page["job_id"], page["total"], page["offset"], page["limit"]) == (job_id, 3, 0, 2)
    first, second = page["items"]
    assert (first["position"], first["status"], first["reference"]) == (0, "done", "fila-0")
    assert first["result"]["status"] == "needs_review"
    assert first["error"] is None
    assert (second["position"], second["status"]) == (1, "failed")
    assert second["result"] is None
    assert second["error"] == "Documento no encontrado."

    rest = api.client.get(f"/v1/jobs/{job_id}/results?offset=2", headers=AUTH).json()
    assert [(i["position"], i["status"]) for i in rest["items"]] == [(2, "pending")]


@pytest.mark.parametrize("path", ["", "/results"])
def test_jobs_of_other_clients_are_not_found(api: Api, path: str) -> None:
    job_id = create_job(api)

    response = api.client.get(f"/v1/jobs/{job_id}{path}", headers={"X-API-Key": OTHER_KEY})

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["title"] == "Trabajo no encontrado"


@pytest.mark.parametrize("path", ["", "/results"])
def test_unknown_jobs_are_not_found(api: Api, path: str) -> None:
    response = api.client.get(f"/v1/jobs/{uuid4()}{path}", headers=AUTH)

    assert response.status_code == 404


@pytest.mark.parametrize(
    "query", ["limit=501", "limit=0", "offset=-1"], ids=["over-500", "zero", "negative"]
)
def test_job_results_bound_the_page(api: Api, query: str) -> None:
    job_id = create_job(api)

    response = api.client.get(f"/v1/jobs/{job_id}/results?{query}", headers=AUTH)

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM


def test_job_ids_must_be_uuids(api: Api) -> None:
    assert api.client.get("/v1/jobs/no-es-uuid", headers=AUTH).status_code == 422


# --- Batch jobs of uploaded PDFs ----------------------------------------------------------------

STORED_NAME = re.compile(r"^[0-9a-f-]{36}\.pdf$")


def manifest(*filenames: str, template_id: str = "comparendo") -> str:
    return json.dumps(
        {
            "template_id": template_id,
            "allow_external_ai": True,
            "items": [
                {"filename": name, "expected": {"identificacion": "1"}, "reference": f"ref-{name}"}
                for name in filenames
            ],
        }
    )


def pdf_files(*names: str, size: int = 100) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (name, name.encode() * size, "application/pdf")) for name in names]


def stored_files(api: Api) -> list[Path]:
    return sorted(api.upload_dir.iterdir()) if api.upload_dir.exists() else []


def test_upload_job_stores_files_under_server_names_in_manifest_order(api: Api) -> None:
    response = api.client.post(
        "/v1/jobs/upload",
        files=pdf_files("a.pdf", "b.pdf"),
        data={"manifest": manifest("b.pdf", "a.pdf")},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json()["total"] == 2
    assert response.headers["location"] == f"/v1/jobs/{response.json()['job_id']}"
    requests = [i.request for i in sorted(api.jobs.items.values(), key=lambda i: i.position)]
    assert [r.reference for r in requests] == ["ref-b.pdf", "ref-a.pdf"]
    for request, client_name in zip(requests, ["b.pdf", "a.pdf"], strict=True):
        assert request.document.kind == "upload"
        assert STORED_NAME.match(request.document.value)
        stored = api.upload_dir / request.document.value
        assert stored.read_bytes() == client_name.encode() * 100
        assert request.template_id == "comparendo"
        assert request.expected == {"identificacion": "1"}
        assert request.allow_external_ai is True
    assert len(stored_files(api)) == 2
    assert len(api.queue.enqueued) == 2


@pytest.mark.parametrize(
    ("files", "described"),
    [
        (["a.pdf", "b.pdf"], ["a.pdf"]),  # a file without manifest entry
        (["a.pdf"], ["a.pdf", "b.pdf"]),  # a manifest entry without file
        (["a.pdf", "a.pdf"], ["a.pdf"]),  # the same file name twice
        (["a.pdf"], ["a.pdf", "a.pdf"]),  # described twice
    ],
    ids=["undescribed-file", "missing-file", "duplicate-file", "duplicate-entry"],
)
def test_upload_job_requires_each_file_described_exactly_once(
    api: Api, files: list[str], described: list[str]
) -> None:
    response = api.client.post(
        "/v1/jobs/upload",
        files=pdf_files(*files),
        data={"manifest": manifest(*described)},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert stored_files(api) == []
    assert api.jobs.jobs == {}


def test_upload_job_rejects_an_invalid_manifest(api: Api) -> None:
    response = api.client.post(
        "/v1/jobs/upload",
        files=pdf_files("a.pdf"),
        data={"manifest": "no es json"},
        headers=AUTH,
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"][:2] == ["body", "manifest"]


def test_upload_job_rejects_too_many_files(api: Api) -> None:
    names = [f"{n}.pdf" for n in range(4)]  # max_batch_items is 3

    response = api.client.post(
        "/v1/jobs/upload",
        files=pdf_files(*names),
        data={"manifest": manifest(*names)},
        headers=AUTH,
    )

    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM
    assert api.jobs.jobs == {}


def test_upload_job_rejects_oversized_pdfs_and_keeps_nothing(api: Api) -> None:
    files = [*pdf_files("a.pdf"), ("files", ("b.pdf", b"0" * (MIB + 1), "application/pdf"))]

    response = api.client.post(
        "/v1/jobs/upload",
        files=files,
        data={"manifest": manifest("a.pdf", "b.pdf")},
        headers=AUTH,
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert stored_files(api) == []
    assert api.jobs.jobs == {}


def test_upload_job_with_unknown_template_keeps_nothing(api: Api) -> None:
    response = api.client.post(
        "/v1/jobs/upload",
        files=pdf_files("a.pdf"),
        data={"manifest": manifest("a.pdf", template_id="otra")},
        headers=AUTH,
    )

    assert response.status_code == 404
    assert stored_files(api) == []
    assert api.jobs.jobs == {}


# --- OpenAPI ------------------------------------------------------------------------------------


def test_openapi_documents_the_contract(api: Api) -> None:
    schema = api.client.get("/openapi.json").json()

    assert schema["info"]["title"] == "API de lectura de documentos"
    assert {tag["name"] for tag in schema["tags"]} == {"Lectura", "Lotes", "Plantillas", "Salud"}
    [scheme] = schema["components"]["securitySchemes"].values()
    assert (scheme["type"], scheme["in"], scheme["name"]) == ("apiKey", "header", "X-API-Key")
    paths = schema["paths"]
    assert set(paths) == {
        "/health",
        "/v1/templates",
        "/v1/read",
        "/v1/read/upload",
        "/v1/jobs",
        "/v1/jobs/upload",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/results",
    }
    assert "security" not in paths["/health"]["get"]
    assert paths["/v1/read"]["post"]["security"]
    assert set(paths["/v1/jobs"]["post"]["responses"]) >= {"202", "401", "404", "422"}
    ok = paths["/v1/read"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok == {"$ref": "#/components/schemas/ReadResult"}


# --- Request guard: decided before the body is read --------------------------------------------

CHUNK = 256 * 1024
MULTIPART = "multipart/form-data; boundary=lote"
PART_HEAD = (
    b'--lote\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
    b"Content-Type: application/pdf\r\n\r\n"
)
# Caps in build_api: read/upload = max_pdf_mb (1) + 1 MiB of form fields; jobs/upload = 3 MiB.
UPLOAD_CAPS = [("/v1/read/upload", 2 * MIB), ("/v1/jobs/upload", 3 * MIB)]


@dataclass
class Exchange:
    status: int
    content_type: str
    chunks_read: int


async def send_raw(
    app: FastAPI,
    path: str,
    *,
    headers: dict[str, str],
    chunks: int,
    content_length: int | None = None,
) -> Exchange:
    """Drive the ASGI app directly with a multipart stream, counting the chunks it pulls."""
    read = 0

    async def receive() -> dict[str, Any]:
        nonlocal read
        read += 1
        body = PART_HEAD if read == 1 else b"0" * CHUNK
        return {"type": "http.request", "body": body, "more_body": read < chunks}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    raw_headers.append((b"content-type", MULTIPART.encode()))
    if content_length is not None:
        raw_headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": raw_headers,
        "server": ("test", 80),
        "client": ("test", 1234),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    content_type = dict(start["headers"]).get(b"content-type", b"").decode()
    return Exchange(start["status"], content_type, read)


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "otra-llave"}])
async def test_guard_refuses_bad_keys_without_reading_the_body(
    api: Api, headers: dict[str, str]
) -> None:
    outcome = await send_raw(api.app, "/v1/jobs/upload", headers=headers, chunks=10)

    assert (outcome.status, outcome.content_type, outcome.chunks_read) == (401, PROBLEM, 0)


@pytest.mark.parametrize(("path", "cap"), UPLOAD_CAPS)
async def test_guard_refuses_declared_oversized_uploads_without_reading_them(
    api: Api, path: str, cap: int
) -> None:
    outcome = await send_raw(api.app, path, headers=AUTH, chunks=10, content_length=cap + 1)

    assert (outcome.status, outcome.content_type, outcome.chunks_read) == (413, PROBLEM, 0)


@pytest.mark.parametrize(("path", "cap"), UPLOAD_CAPS)
async def test_guard_cuts_off_streams_without_length_once_past_the_cap(
    api: Api, path: str, cap: int
) -> None:
    outcome = await send_raw(api.app, path, headers=AUTH, chunks=40)  # ~10 MiB, chunked

    assert (outcome.status, outcome.content_type) == (413, PROBLEM)
    assert outcome.chunks_read <= cap // CHUNK + 2
    assert api.jobs.jobs == {}
    assert api.reader.calls == []


def test_batch_uploads_have_their_own_total_cap(api: Api) -> None:
    names = ("a", "b", "c")
    files = [("files", (name, b"0" * (MIB - 1000), "application/pdf")) for name in names]

    response = api.client.post(
        "/v1/jobs/upload", files=files, data={"manifest": manifest(*names)}, headers=AUTH
    )

    assert response.status_code == 202  # ~3 MiB: over the single-read cap, under the batch cap


# --- Lifespan -----------------------------------------------------------------------------------


def test_create_app_runs_the_given_lifespan(tmp_path: Path) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        events.append("start")
        yield
        events.append("stop")

    api = build_api(tmp_path, lifespan=lifespan)
    with api.client:
        assert events == ["start"]
    assert events == ["start", "stop"]
