"""Document sources: fetch PDF bytes from a file share, an allowlisted URL or a PostgreSQL BLOB.

References come from API clients and are untrusted. Error messages are generic on purpose:
they never echo the reference, which may contain personal data.
"""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import httpx

from docreader.domain import DocReaderError, DocumentNotFound, DocumentRejected, SourceKind

if TYPE_CHECKING:
    import asyncpg


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
            raise DocReaderError("No se pudo leer el documento compartido.") from None
        _check_size(len(data), self._max_bytes)
        return data

    def _resolve(self, ref: str) -> Path:
        """Map `ref` to a real path inside the base dir, or raise `DocumentRejected`."""
        # "\" is a separator on every platform (clients may be Windows systems), so "..\"
        # stays a traversal on Linux too. Normalized before any other check.
        ref = ref.replace("\\", "/")
        # Lexical checks next: resolving a UNC path (//host/share) would already reach the
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
        self._injected = client  # owned by the caller: never closed here
        self._own: httpx.AsyncClient | None = None

    async def fetch(self, ref: str) -> bytes:
        url = self._validate(ref)
        # `from None`: httpx errors may carry the URL, which may contain personal data.
        try:
            async with asyncio.timeout(self._timeout_s):  # total deadline, not per read
                return await self._download(url)
        except TimeoutError:
            raise DocReaderError("Se agotó el tiempo de descarga del documento.") from None
        except httpx.HTTPError:
            raise DocReaderError("No se pudo descargar el documento del origen.") from None

    async def aclose(self) -> None:
        if self._own is not None:
            await self._own.aclose()
            self._own = None

    async def _download(self, url: httpx.URL) -> bytes:
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

    def _validate(self, ref: str) -> httpx.URL:
        """Parse once with httpx and send that same object: no parser differential."""
        try:
            url = httpx.URL(ref)
        except httpx.InvalidURL:
            raise DocumentRejected("La URL del documento no es válida.") from None
        if url.scheme not in self._schemes or url.userinfo:
            raise DocumentRejected("La URL del documento no está permitida.")
        if not self._host_allowed(url.host.rstrip(".")):
            raise DocumentRejected("El dominio del documento no está en la lista permitida.")
        return url

    def _host_allowed(self, host: str) -> bool:
        """Exact entry, or a subdomain of a ".suffix" entry. IP literals only match exactly."""
        if host in self._exact:
            return True
        return not _is_ip_literal(host) and host.endswith(self._suffixes)

    def _http(self) -> httpx.AsyncClient:
        if self._injected is not None:
            return self._injected
        if self._own is None:  # lazy and shared; no await in between, so no race
            self._own = httpx.AsyncClient(timeout=self._timeout_s, follow_redirects=False)
        return self._own


PoolFactory = Callable[..., Awaitable["asyncpg.Pool"]]


class PostgresBlobSource:
    """Reads a document BLOB with a configured query; `ref` is bound to `$1`, never formatted."""

    kind: SourceKind = "blob"

    def __init__(
        self, dsn: str, query: str, *, max_bytes: int, pool_factory: PoolFactory | None = None
    ) -> None:
        self._dsn = dsn
        self._query = query
        self._max_bytes = max_bytes
        self._pool_factory = pool_factory
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    async def fetch(self, ref: str) -> bytes:
        import asyncpg  # optional dependency (extra "api"): only needed for this source

        if not ref:
            raise DocumentRejected("La referencia del documento no es válida.")
        pool = await self._get_pool()
        # `from None`: asyncpg and PostgreSQL echo parameter values in their messages.
        try:
            value = await pool.fetchval(self._query, ref)
        except (asyncpg.exceptions.DataError, ValueError):  # ref unfit for the column type
            raise DocumentRejected("La referencia del documento no es válida.") from None
        except _db_errors():
            raise DocReaderError("No se pudo consultar el documento.") from None
        if value is None:
            raise DocumentNotFound("El documento no existe en la base de datos.")
        if not isinstance(value, bytes | memoryview):
            raise DocReaderError("La consulta de documentos no devolvió contenido binario.")
        data = bytes(value)
        _check_size(len(data), self._max_bytes)
        return data

    async def aclose(self) -> None:
        async with self._pool_lock:
            if self._pool is not None:
                await self._pool.close()
                self._pool = None

    async def _get_pool(self) -> asyncpg.Pool:
        import asyncpg

        async with self._pool_lock:  # concurrent first calls share one pool
            if self._pool is None:
                factory = self._pool_factory or asyncpg.create_pool
                try:
                    self._pool = await factory(self._dsn, min_size=1, max_size=10)
                except _db_errors() as exc:
                    # Chained on purpose: connection errors carry no client data.
                    raise DocReaderError("No se pudo conectar a la base de datos.") from exc
            return self._pool


def _db_errors() -> tuple[type[Exception], ...]:
    """Infrastructure failures of asyncpg (imported lazily: optional dependency)."""
    import asyncpg

    return (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, TimeoutError)
