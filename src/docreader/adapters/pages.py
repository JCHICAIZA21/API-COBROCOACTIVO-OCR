"""PageReader over PDFium: the native text layer first, OCR only for pages without one."""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import BrokenExecutor, Executor, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
from itertools import islice

import pypdfium2 as pdfium
from PIL.Image import Image

from ..domain import DocumentRejected, PageText
from ..ports import OcrEngine

OcrFactory = Callable[[], OcrEngine]

# PDFium is not thread-safe: every call into it, in any process, holds this lock.
_PDFIUM = threading.Lock()
# Largest page image side handed to OCR, in pixels: bounds memory on huge or hostile pages.
_MAX_SIDE_PX = 5000
# Per worker thread (one per worker process): the OCR factory and its engine, built on first use.
_worker = threading.local()
_UNREADABLE = "el PDF está dañado o no se puede leer"


class PdfPageReader:
    """`PageReader` that runs PDFium and OCR in worker processes (`workers=0`: in-process)."""

    def __init__(
        self,
        ocr_factory: OcrFactory | None,
        *,
        workers: int,
        min_native_chars: int = 25,
        dpi: int = 200,
    ) -> None:
        self._ocr_factory = ocr_factory
        self._workers = workers
        self._min_native_chars = min_native_chars
        self._dpi = dpi
        self._executor: Executor | None = None

    async def read(self, pdf: bytes) -> list[PageText]:
        texts = await self._call(_native_texts, pdf)
        missing = [i for i, text in enumerate(texts) if len(text.strip()) < self._min_native_chars]
        ocr: dict[int, tuple[str, float]] = {}
        if self._ocr_factory is not None and missing:
            results = await asyncio.gather(
                *(self._call(_ocr_page, pdf, index, self._dpi) for index in missing)
            )
            ocr = dict(zip(missing, results, strict=True))
        pages = []
        for index, text in enumerate(texts):
            if index in ocr:
                ocr_text, confidence = ocr[index]
                page = PageText(
                    number=index + 1, text=ocr_text, origin="ocr", confidence=confidence
                )
            else:
                confidence = 0.0 if index in missing else 1.0
                page = PageText(number=index + 1, text=text, origin="native", confidence=confidence)
            pages.append(page)
        return pages

    def close(self) -> None:
        """Stop the workers. The reader starts new ones if used again."""
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    async def _call[T](self, task: Callable[..., T], *args: object) -> T:
        """Run `task` in a worker. A dying worker (say, PDFium crashing on a hostile PDF) breaks
        the whole pool: replace it and retry once, so other in-flight documents survive."""
        for _ in range(2):
            pool = self._pool()
            try:
                return await asyncio.get_running_loop().run_in_executor(pool, task, *args)
            except BrokenExecutor:
                if self._executor is pool:
                    self._executor = None
                pool.shutdown(wait=False, cancel_futures=True)
        raise DocumentRejected("no se pudo procesar el PDF: el lector de páginas falló")

    def _pool(self) -> Executor:
        """Created on first use. Workers build their own OCR engine (factory is picklable)."""
        if self._executor is None:
            init = {"initializer": _init_worker, "initargs": (self._ocr_factory,)}
            if self._workers > 0:
                # spawn: forking a process that already runs threads (event loop, ORT) can hang
                context = multiprocessing.get_context("spawn")
                self._executor = ProcessPoolExecutor(self._workers, context, **init)
            else:
                self._executor = ThreadPoolExecutor(1, **init)
        return self._executor


# --- Worker side: runs inside the pool -----------------------------------------------------


def _init_worker(ocr_factory: OcrFactory | None) -> None:
    _worker.factory = ocr_factory
    _worker.engine = None


def _engine() -> OcrEngine:
    """This worker's OCR engine, built once."""
    if _worker.engine is None:
        _worker.engine = _worker.factory()
    return _worker.engine


def _native_texts(pdf: bytes) -> list[str]:
    """Text layer of every page, with `\\n` line breaks."""
    with _document(pdf) as document:
        return [_clean(page.get_textpage().get_text_range()) for page in document]


def _ocr_page(pdf: bytes, index: int, dpi: int) -> tuple[str, float]:
    with _document(pdf) as document:
        image = _page_image(document[index], dpi)
    return _engine().recognize(image)


@contextmanager
def _document(pdf: bytes) -> Iterator[pdfium.PdfDocument]:
    """The open PDF, under the PDFium lock. Any PDFium error means an unreadable document."""
    with _PDFIUM:
        try:
            document = pdfium.PdfDocument(pdf)
        except pdfium.PdfiumError as exc:
            if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
                raise DocumentRejected("el PDF está protegido con contraseña") from None
            raise DocumentRejected(_UNREADABLE) from None
        try:
            yield document
        except pdfium.PdfiumError:
            raise DocumentRejected(_UNREADABLE) from None
        finally:
            document.close()


def _page_image(page: pdfium.PdfPage, dpi: int) -> Image:
    """Grayscale image of the page: its scan as embedded when it is upright and all the page
    has (~7x faster than rendering, at the scan's own resolution); otherwise a render."""
    scan = _upright_scan(page)
    if scan is not None:
        return scan.get_bitmap(render=False).to_pil().convert("L")
    scale = min(dpi / 72, _MAX_SIDE_PX / max(page.get_size()))
    return page.render(scale=scale, grayscale=True).to_pil().convert("L")


def _upright_scan(page: pdfium.PdfPage) -> pdfium.PdfImage | None:
    """The page's only object, if it is an image drawn without rotation or flips."""
    if page.get_rotation() != 0:
        return None
    objects = list(islice(page.get_objects(max_depth=1), 2))
    if len(objects) != 1 or not isinstance(objects[0], pdfium.PdfImage):
        return None
    image = objects[0]
    a, b, c, d, _, _ = image.get_matrix().get()
    if b or c or a <= 0 or d <= 0 or max(image.get_px_size()) > _MAX_SIDE_PX:
        return None
    return image


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")
