"""Document sources: fetch PDF bytes from a file share, an allowlisted URL or a PostgreSQL BLOB.

References come from API clients and are untrusted. Error messages are generic on purpose:
they never echo the reference, which may contain personal data.
"""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath

import httpx

from docreader.domain import DocReaderError, DocumentNotFound, DocumentRejected, SourceKind


def _check_size(size: int, max_bytes: int) -> None:
    if size > max_bytes:
        raise DocumentRejected("El documento supera el tamaño máximo permitido.")


def _check_status(status: int) -> None:
    """2xx passes. Redirects and other 4xx reject the reference; 5xx is an upstream failure."""
    if 200 <= status < 300:
        return
    if status in (404, 410):
        raise DocumentNotFound("El documento no existe en el origen.")
    if 300 <= status < 500:
        raise DocumentRejected(f"El origen rechazó la descarga del documento (HTTP {status}).")
    raise DocReaderError(f"El origen del documento falló (HTTP {status}).")


def _is_ip_literal(host: str) -> bool:
    """IPv6, or a numeric last label (resolvers read "127.1" or "2130706433" as IPv4)."""
    last = host.rsplit(".", 1)[-1]
    return ":" in host or last.isdigit() or last.startswith("0x")


class FileShareSource:
    """Reads documents under one base directory; `ref` is a path relative to it."""

    kind: SourceKind = "path"

    def __init__(self, base_dir: Path, *, max_bytes: int) -> None:
        self._base = base_dir.resolve()
        self._max_bytes = max_bytes

    async def fetch(self, ref: str) -> bytes:
        return await asyncio.to_thread(self._read, ref)

    def _read(self, ref: str) -> bytes:
        target = self._resolve(ref)
        # `from None` below: OSError messages carry the path, which may contain personal data.
        try:
            info = target.stat()
            if not stat.S_ISREG(info.st_mode):  # directories, FIFOs, devices
                raise DocumentRejected("La referencia no corresponde a un archivo.")
            _check_size(info.st_size, self._max_bytes)
            with target.open("rb") as file:
                data = file.read(self._max_bytes + 1)  # the file may grow after stat
        except (FileNotFoundError, NotADirectoryError):
            raise DocumentNotFound("El documento no existe en el directorio compartido.") from None
        except OSError:
            raise DocReaderError("No se pudo leer el documento del directorio compartido.") from None
        _check_size(len(data), self._max_bytes)
        return data

    def _resolve(self, ref: str) -> Path:
        """Map `ref` to a real path inside the base dir, or raise `DocumentRejected`."""
        # Lexical checks first: resolving a UNC path (\\host\share) would already reach the
        # network on Windows. PureWindowsPath also sees "/" roots, so this covers POSIX too.
        if not ref or "\x00" in ref or PureWindowsPath(ref).anchor:
            raise DocumentRejected("La ruta del documento no es válida.")
        try:
            target = (self._base / ref).resolve()  # follows symlinks and ".."
        except (OSError, ValueError):
            raise DocumentRejected("La ruta del documento no es válida.") from None
        if target == self._base or not target.is_relative_to(self._base):
            raise DocumentRejected("La ruta del documento está fuera del directorio permitido.")
        return target


class HttpSource:
    """Downloads documents from allowlisted hosts; `ref` is an absolute URL."""

    kind: SourceKind = "url"

    def __init__(
        self,
        allowed_hosts: Sequence[str],
        *,
        max_bytes: int,
        timeout_s: float = 20.0,
        allow_http: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        entries = {h.strip().lower().rstrip(".").strip("[]") for h in allowed_hosts} - {""}
        self._exact = frozenset(e for e in entries if not e.startswith("."))
        self._suffixes = tuple(e for e in entries if e.startswith(".") and len(e) > 1)
        self._schemes = frozenset({"https", "http"} if allow_http else {"https"})
        self._max_bytes = max_bytes
        self._timeout_s = timeout_s
        self._client = client

    async def fetch(self, ref: str) -> bytes:
        url = self._validate(ref)
        # Per-request `follow_redirects=False`: an injected client must not override it.
        # `identity`: no compressed body, so the size cap counts what is actually received.
        async with self._http().stream(
            "GET", url, follow_redirects=False, headers={"Accept-Encoding": "identity"}
        ) as response:
            _check_status(response.status_code)
            declared = response.headers.get("Content-Length", "")
            if declared.isdigit():
                _check_size(int(declared), self._max_bytes)
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                _check_size(len(body), self._max_bytes)
            return bytes(body)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    def _validate(self, ref: str) -> httpx.URL:
        """Parse once with httpx and send that same object: no parser differential."""
        try:
            url = httpx.URL(ref)
        except httpx.InvalidURL:
            raise DocumentRejected("La URL del documento no es válida.") from None
        if url.scheme not in self._schemes or url.userinfo:
            raise DocumentRejected("La URL del documento no está permitida.")
        host = url.host.rstrip(".")
        if not (host in self._exact or (not _is_ip_literal(host) and host.endswith(self._suffixes))):
            raise DocumentRejected("El dominio del documento no está en la lista permitida.")
        return url

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client
