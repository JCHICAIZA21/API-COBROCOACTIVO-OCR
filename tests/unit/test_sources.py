import sys
import traceback
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from docreader.adapters.sources import FileShareSource, HttpSource
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


WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")


@pytest.mark.parametrize(
    "ref",
    [
        "../secret.pdf",
        "sub/../../secret.pdf",
        pytest.param("sub\\..\\..\\secret.pdf", marks=WINDOWS_ONLY),
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
async def test_fileshare_rejects_absolute_drive_unc_empty_or_nul_refs(share: Path, ref: str) -> None:
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
