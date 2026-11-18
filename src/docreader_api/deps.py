"""Collaborators of the HTTP API, injected once by `create_app` and resolved per request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol, cast

from fastapi import Depends, Request

from docreader.domain import ReadRequest, ReadResult
from docreader.ports import TemplateRepository
from docreader.settings import ServiceSettings

from .ports import JobStore, WorkQueue


class Reader(Protocol):
    """Reads one document. `content` carries the PDF bytes of `upload` requests."""

    async def read(self, request: ReadRequest, content: bytes | None = None) -> ReadResult: ...


@dataclass(frozen=True, slots=True)
class ApiDeps:
    settings: ServiceSettings
    reader: Reader
    templates: TemplateRepository
    jobs: JobStore
    queue: WorkQueue


def get_deps(request: Request) -> ApiDeps:
    return cast(ApiDeps, request.app.state.deps)


Deps = Annotated[ApiDeps, Depends(get_deps)]
