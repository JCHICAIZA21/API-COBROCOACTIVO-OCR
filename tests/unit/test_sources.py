import asyncio
import sys
import traceback
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import httpx
import pytest
import respx
from asyncpg.exceptions._base import DataError as ClientDataError

from docreader.adapters.sources import FileShareSource, HttpSource, PostgresBlobSource
from docreader.domain import DocReaderError, DocumentNotFound, DocumentRejected
from docreader.ports import DocumentSource

PDF = b"%PDF-1.7 fake"


def assert_no_leak(exc: BaseException, secret: str) -> None:
    """The client's reference must not appear in the message nor in a logged traceback."""
    assert secret not in "".join(traceback.format_exception(exc))


# --- FileShareSource ---------------------------------------------------------------------------


@pytest.fixture
def share(tmp_path: Path) -> Path:
    base = tmp_path / "share"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "doc.pdf").write_bytes(PDF)
    (tmp_path / "secret.pdf").write_bytes(b"outside the share")
    return base


async def test_fileshare_reads_a_document_inside_the_base_dir(share: Path) -> None:
    source = FileShareSource(share, max_bytes=1024)

    assert isinstance(source, DocumentSource)
    assert source.kind == "path"
    assert await source.fetch("sub/doc.pdf") == PDF


async def test_fileshare_missing_document_is_not_found_without_leaking_the_ref(share: Path) -> None:
    source = FileShareSource(share, max_bytes=1024)
    ref = "sub/1234567890.pdf"

    with pytest.raises(DocumentNotFound) as exc_info:
        await source.fetch(ref)

    assert_no_leak(exc_info.value, "1234567890")


async def test_fileshare_treats_backslash_as_a_separator_on_every_platform(share: Path) -> None:
    source = FileShareSource(share, max_bytes=1024)  # clients may be Windows systems

    assert await source.fetch("sub\\doc.pdf") == PDF


@pytest.mark.parametrize(
    "ref",
    [
        "../secret.pdf",
        "sub/../../secret.pdf",
        "..\\secret.pdf",
        "sub\\..\\..\\secret.pdf",
        "sub/..\\../secret.pdf",
    ],
)
async def test_fileshare_rejects_traversal_outside_the_base_dir(share: Path, ref: str) -> None:
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected) as exc_info:
        await source.fetch(ref)

    assert_no_leak(exc_info.value, "secret")


@pytest.mark.parametrize(
    "ref",
    [
        "/etc/passwd",
        "\\secret.pdf",
        "C:\\Windows\\win.ini",
        "C:secret.pdf",
        "\\\\attacker\\share\\doc.pdf",
        "//attacker/share/doc.pdf",
        "",
        "sub/doc\x00.pdf",
    ],
)
async def test_fileshare_rejects_anchored_empty_or_nul_refs(share: Path, ref: str) -> None:
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected):
        await source.fetch(ref)


async def test_fileshare_rejects_absolute_paths_even_inside_the_base_dir(share: Path) -> None:
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected):
        await source.fetch(str(share / "sub" / "doc.pdf"))


def make_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return
    except OSError:  # Windows without developer mode or admin rights
        pass
    if sys.platform == "win32" and target.is_dir():
        import _winapi  # directory junctions need no privileges

        _winapi.CreateJunction(str(target), str(link))
        return
    pytest.skip("symlinks are not available on this machine")


async def test_fileshare_rejects_file_symlinks_escaping_the_base_dir(share: Path) -> None:
    make_symlink(share / "link.pdf", share.parent / "secret.pdf")
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected):
        await source.fetch("link.pdf")


async def test_fileshare_rejects_directory_symlinks_escaping_the_base_dir(share: Path) -> None:
    make_symlink(share / "outside", share.parent)
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected):
        await source.fetch("outside/secret.pdf")


async def test_fileshare_rejects_a_directory(share: Path) -> None:
    source = FileShareSource(share, max_bytes=1024)

    with pytest.raises(DocumentRejected):
        await source.fetch("sub")


async def test_fileshare_accepts_exactly_max_bytes_and_rejects_larger(share: Path) -> None:
    (share / "big.pdf").write_bytes(b"x" * 11)

    assert await FileShareSource(share, max_bytes=len(PDF)).fetch("sub/doc.pdf") == PDF
    with pytest.raises(DocumentRejected):
        await FileShareSource(share, max_bytes=10).fetch("big.pdf")


# --- HttpSource --------------------------------------------------------------------------------

DOC_URL = "https://docs.example.com/pdf/1234567890.pdf"


async def test_http_downloads_from_an_allowlisted_host(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(DOC_URL).respond(200, content=PDF)
    source = HttpSource(["docs.example.com"], max_bytes=1024)

    assert isinstance(source, DocumentSource)
    assert source.kind == "url"
    assert await source.fetch(DOC_URL) == PDF
    await source.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.example.com/a.pdf",  # plain http not enabled
        "ftp://docs.example.com/a.pdf",
        "file:///etc/passwd",
        "docs.example.com/a.pdf",  # no scheme
        "https:///a.pdf",  # no host
        "https://evil.com/a.pdf",
        "https://docs.example.com.evil.com/a.pdf",
        "https://evil-docs.example.com/a.pdf",  # not a subdomain of an exact entry
        "https://example.org/a.pdf",  # ".example.org" allows subdomains only
        "https://notexample.org/a.pdf",
        "https://docs.example.com@evil.com/a.pdf",  # userinfo trick
        "https://user:pw@docs.example.com/a.pdf",  # userinfo on an allowed host
        "https://127.0.0.1/a.pdf",
        "https://[::1]/a.pdf",
        "https://10.0.0.1.example.org.10/a.pdf",
        "https://169.254.169.254/latest/meta-data/",
        "https://127.1/a.pdf",  # short IPv4 form
        "not a url at all",
        "",
    ],
)
async def test_http_rejects_urls_outside_the_policy_without_any_request(
    respx_mock: respx.MockRouter, url: str
) -> None:
    source = HttpSource(["docs.example.com", ".example.org"], max_bytes=1024)

    with pytest.raises(DocumentRejected) as exc_info:
        await source.fetch(url)

    assert not respx_mock.calls
    assert_no_leak(exc_info.value, "a.pdf")


@pytest.mark.parametrize(
    "url",
    [
        "https://files.example.org/a.pdf",
        "https://deep.files.example.org/a.pdf",
        "https://DOCS.Example.COM/a.pdf",
        "https://docs.example.com:8443/a.pdf",
        "https://10.1.2.3/a.pdf",  # IP literal explicitly listed
    ],
)
async def test_http_accepts_allowlisted_hosts(respx_mock: respx.MockRouter, url: str) -> None:
    respx_mock.get(url__regex=r".*").respond(200, content=PDF)
    source = HttpSource(["docs.example.com", ".example.org", "10.1.2.3"], max_bytes=1024)

    assert await source.fetch(url) == PDF


async def test_http_allows_plain_http_only_when_enabled(respx_mock: respx.MockRouter) -> None:
    url = "http://docs.example.com/a.pdf"
    respx_mock.get(url).respond(200, content=PDF)

    assert await HttpSource(["docs.example.com"], max_bytes=1024, allow_http=True).fetch(url) == PDF


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_http_never_follows_redirects(respx_mock: respx.MockRouter, status: int) -> None:
    target = respx_mock.get("https://docs.example.com/other.pdf").respond(200, content=PDF)
    respx_mock.get(DOC_URL).respond(status, headers={"Location": "/other.pdf"})
    following = httpx.AsyncClient(follow_redirects=True)  # even if the caller's client would
    source = HttpSource(["docs.example.com"], max_bytes=1024, client=following)

    with pytest.raises(DocumentRejected):
        await source.fetch(DOC_URL)

    assert not target.called
    await following.aclose()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (404, DocumentNotFound),
        (410, DocumentNotFound),
        (401, DocumentRejected),
        (403, DocumentRejected),
        (500, DocReaderError),
        (503, DocReaderError),
    ],
)
async def test_http_maps_error_statuses(
    respx_mock: respx.MockRouter, status: int, error: type[DocReaderError]
) -> None:
    respx_mock.get(DOC_URL).respond(status, text="detalle del servidor")
    source = HttpSource(["docs.example.com"], max_bytes=1024)

    with pytest.raises(DocReaderError) as exc_info:
        await source.fetch(DOC_URL)

    assert type(exc_info.value) is error
    assert_no_leak(exc_info.value, "1234567890")


async def test_http_aborts_the_download_as_soon_as_it_exceeds_max_bytes(
    respx_mock: respx.MockRouter,
) -> None:
    sent = 0

    async def endless() -> AsyncIterator[bytes]:  # chunked, no Content-Length
        nonlocal sent
        for _ in range(10_000):
            sent += 1
            yield b"x" * 1024

    respx_mock.get(DOC_URL).mock(return_value=httpx.Response(200, content=endless()))
    source = HttpSource(["docs.example.com"], max_bytes=2048)

    with pytest.raises(DocumentRejected):
        await source.fetch(DOC_URL)

    assert sent <= 3


async def test_http_accepts_exactly_max_bytes_and_rejects_a_larger_body(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(DOC_URL).respond(200, content=PDF)
    respx_mock.get("https://docs.example.com/big.pdf").respond(200, content=PDF + b"x")
    source = HttpSource(["docs.example.com"], max_bytes=len(PDF))

    assert await source.fetch(DOC_URL) == PDF
    with pytest.raises(DocumentRejected):
        await source.fetch("https://docs.example.com/big.pdf")


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("connection refused for docs.example.com/pdf/1234567890.pdf"),
        httpx.RemoteProtocolError("peer closed connection"),
    ],
)
async def test_http_transport_failures_are_generic_reader_errors(
    respx_mock: respx.MockRouter, failure: Exception
) -> None:
    respx_mock.get(DOC_URL).mock(side_effect=failure)
    source = HttpSource(["docs.example.com"], max_bytes=1024)

    with pytest.raises(DocReaderError) as exc_info:
        await source.fetch(DOC_URL)

    assert type(exc_info.value) is DocReaderError
    assert_no_leak(exc_info.value, "1234567890")


async def test_http_enforces_a_total_deadline_against_slow_drip_servers(
    respx_mock: respx.MockRouter,
) -> None:
    async def slow() -> AsyncIterator[bytes]:
        yield b"%PDF"
        await asyncio.sleep(30)
        yield b"never"

    respx_mock.get(DOC_URL).mock(return_value=httpx.Response(200, content=slow()))
    source = HttpSource(["docs.example.com"], max_bytes=1024, timeout_s=0.2)

    with pytest.raises(DocReaderError) as exc_info:
        await asyncio.wait_for(source.fetch(DOC_URL), timeout=5)

    assert type(exc_info.value) is DocReaderError


async def test_http_aclose_closes_its_own_client_but_not_an_injected_one(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(DOC_URL).respond(200, content=PDF)
    injected = httpx.AsyncClient()
    owned_source = HttpSource(["docs.example.com"], max_bytes=1024)
    injected_source = HttpSource(["docs.example.com"], max_bytes=1024, client=injected)
    await owned_source.fetch(DOC_URL)
    await injected_source.fetch(DOC_URL)

    await owned_source.aclose()
    await injected_source.aclose()

    assert not injected.is_closed
    assert await owned_source.fetch(DOC_URL) == PDF  # a new client is created on demand
    await owned_source.aclose()
    await injected.aclose()


# --- PostgresBlobSource ------------------------------------------------------------------------

DSN = "postgresql://reader:secret@db:5432/docs"
QUERY = "SELECT content FROM documents WHERE id = $1"


class FakePool:
    """Stands in for asyncpg.Pool: records queries, returns a canned value or raises."""

    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    async def fetchval(self, query: str, *args: object) -> object:
        self.calls.append((query, args))
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        self.closed = True


class FakePoolFactory:
    def __init__(self, pool: FakePool) -> None:
        self.pool = pool
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, *args: object, **kwargs: object) -> FakePool:
        self.calls.append((args, kwargs))
        await asyncio.sleep(0)  # let concurrent callers interleave
        return self.pool


def blob_source(
    pool: FakePool, max_bytes: int = 1024
) -> tuple[PostgresBlobSource, FakePoolFactory]:
    factory = FakePoolFactory(pool)
    return PostgresBlobSource(DSN, QUERY, max_bytes=max_bytes, pool_factory=factory), factory


async def test_blob_runs_the_configured_query_with_the_ref_as_a_parameter() -> None:
    pool = FakePool(result=PDF)
    source, factory = blob_source(pool)
    ref = "1'; DROP TABLE documents; --"

    assert isinstance(source, DocumentSource)
    assert source.kind == "blob"
    assert await source.fetch(ref) == PDF
    assert pool.calls == [(QUERY, (ref,))]
    assert factory.calls == [((DSN,), {"min_size": 1, "max_size": 10})]


async def test_blob_accepts_memoryview_results() -> None:
    source, _ = blob_source(FakePool(result=memoryview(PDF)))

    data = await source.fetch("doc-1")

    assert data == PDF
    assert type(data) is bytes


async def test_blob_missing_row_is_not_found_without_leaking_the_ref() -> None:
    source, _ = blob_source(FakePool(result=None))
    ref = "1234567890"

    with pytest.raises(DocumentNotFound) as exc_info:
        await source.fetch(ref)

    assert_no_leak(exc_info.value, ref)


async def test_blob_accepts_exactly_max_bytes_and_rejects_larger() -> None:
    exact, _ = blob_source(FakePool(result=PDF), max_bytes=len(PDF))
    larger, _ = blob_source(FakePool(result=PDF + b"x"), max_bytes=len(PDF))

    assert await exact.fetch("doc-1") == PDF
    with pytest.raises(DocumentRejected):
        await larger.fetch("doc-1")


async def test_blob_unexpected_column_type_is_a_reader_error() -> None:
    source, _ = blob_source(FakePool(result="not bytes"))

    with pytest.raises(DocReaderError) as exc_info:
        await source.fetch("doc-1")

    assert type(exc_info.value) is DocReaderError


async def test_blob_rejects_an_empty_ref_without_querying() -> None:
    pool = FakePool(result=PDF)
    source, _ = blob_source(pool)

    with pytest.raises(DocumentRejected):
        await source.fetch("")

    assert not pool.calls


@pytest.mark.parametrize(
    "error",
    [  # both messages echo the parameter value, as asyncpg/PostgreSQL do
        asyncpg.exceptions.InvalidTextRepresentationError('invalid input for uuid: "1234567890"'),
        ClientDataError("invalid input for query argument $1: '1234567890' (expected int)"),
    ],
)
async def test_blob_ref_of_the_wrong_type_is_rejected_without_leaking_it(error: Exception) -> None:
    source, _ = blob_source(FakePool(error=error))
    ref = "1234567890"

    with pytest.raises(DocumentRejected) as exc_info:
        await source.fetch(ref)

    assert_no_leak(exc_info.value, ref)


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.exceptions.UndefinedTableError('relation "documents" does not exist'),
        asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed (ref 1234567890)"),
        ConnectionResetError("reset while reading 1234567890"),
        TimeoutError(),
    ],
)
async def test_blob_database_failures_are_generic_reader_errors(error: Exception) -> None:
    source, _ = blob_source(FakePool(error=error))
    ref = "1234567890"

    with pytest.raises(DocReaderError) as exc_info:
        await source.fetch(ref)

    assert type(exc_info.value) is DocReaderError
    assert_no_leak(exc_info.value, ref)


async def test_blob_pool_creation_failure_is_a_reader_error_and_is_retried() -> None:
    pool = FakePool(result=PDF)
    attempts = 0

    async def flaky_factory(*args: object, **kwargs: object) -> FakePool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("database down")
        return pool

    source = PostgresBlobSource(DSN, QUERY, max_bytes=1024, pool_factory=flaky_factory)

    with pytest.raises(DocReaderError):
        await source.fetch("doc-1")
    assert await source.fetch("doc-1") == PDF


async def test_blob_creates_one_shared_pool_under_concurrency_and_aclose_closes_it() -> None:
    pool = FakePool(result=PDF)
    source, factory = blob_source(pool)

    results = await asyncio.gather(*(source.fetch(f"doc-{i}") for i in range(5)))
    await source.aclose()

    assert results == [PDF] * 5
    assert len(factory.calls) == 1
    assert pool.closed
