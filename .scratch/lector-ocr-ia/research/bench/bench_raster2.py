"""Rasterization alternatives: render vector page vs extract embedded scan image."""
import statistics
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium

DATA = Path(sys.argv[1])
N = 30


def timed(fn):
    fn()
    ts = []
    for _ in range(N):
        t0 = time.perf_counter()
        out = fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts) * 1000, out


def render_native(dpi):
    pdf = pdfium.PdfDocument(str(DATA / "native.pdf"))
    a = pdf[0].render(scale=dpi / 72, grayscale=True).to_numpy()
    pdf.close()
    return a.shape


def extract_image():
    pdf = pdfium.PdfDocument(str(DATA / "scanned.pdf"))
    imgs = [o for o in pdf[0].get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE]
    a = imgs[0].get_bitmap(render=False).to_numpy()
    pdf.close()
    return a.shape


for dpi in (200, 300):
    ms, shape = timed(lambda: render_native(dpi))
    print(f"render native vector page {dpi} dpi {shape}: {ms:.1f} ms")
ms, shape = timed(extract_image)
print(f"extract embedded scan image (no render) {shape}: {ms:.1f} ms")
