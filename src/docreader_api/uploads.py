"""Uploaded PDFs: bounded reads and storage under `upload_dir` with server-generated names."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import UploadFile

from .errors import ApiError

MIB = 1024 * 1024


async def _chunks(upload: UploadFile, max_mb: int) -> AsyncIterator[bytes]:
    """Yield the upload in chunks; stop with 413 as soon as it exceeds `max_mb`."""
    size = 0
    while chunk := await upload.read(MIB):
        size += len(chunk)
        if size > max_mb * MIB:
            raise ApiError(413, "PDF demasiado grande", f"Cada PDF admite como máximo {max_mb} MB.")
        yield chunk


def order_by_manifest(filenames: Sequence[str], files: Sequence[UploadFile]) -> list[UploadFile]:
    """Return `files` in manifest order; each must be described exactly once (422 otherwise).

    The error never names the files: client file names may carry personal data.
    """
    by_name = {upload.filename: upload for upload in files}
    described = set(filenames)
    unique = len(by_name) == len(files) and len(described) == len(filenames)
    if not unique or by_name.keys() != described:
        raise ApiError(
            422,
            "El manifiesto no coincide con los archivos",
            "Cada archivo de `files` debe aparecer una sola vez en `manifest.items` (por "
            "`filename`) y cada elemento del manifiesto debe tener su archivo.",
        )
    return [by_name[name] for name in filenames]


async def read_pdf(upload: UploadFile, max_mb: int) -> bytes:
    return b"".join([chunk async for chunk in _chunks(upload, max_mb)])


async def store_pdfs(uploads: Sequence[UploadFile], directory: Path, max_mb: int) -> list[str]:
    """Save each upload as `<uuid>.pdf` (never the client's name). All or nothing."""
    await anyio.Path(directory).mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    try:
        for upload in uploads:
            names.append(f"{uuid4()}.pdf")
            async with await anyio.open_file(directory / names[-1], "xb") as out:
                async for chunk in _chunks(upload, max_mb):
                    await out.write(chunk)
    except BaseException:
        await discard_pdfs(directory, names)
        raise
    return names


async def discard_pdfs(directory: Path, names: Sequence[str]) -> None:
    with anyio.CancelScope(shield=True):  # clean up even when the request is cancelled
        for name in names:
            await anyio.Path(directory / name).unlink(missing_ok=True)
