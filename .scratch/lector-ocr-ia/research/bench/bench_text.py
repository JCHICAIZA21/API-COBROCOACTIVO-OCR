"""Benchmark text-layer extraction and page rasterization.

Usage: python bench_text.py <data_dir> [iterations]
Measures ms per page (median of N runs over the 2-page native PDF, opening the
file each run, as a service would per request). Also checks field hits and
per-page text detection on mixed.pdf.
"""
import json
import statistics
import sys
import time
from pathlib import Path

DATA = Path(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 30
NATIVE = str(DATA / "native.pdf")
MIXED = str(DATA / "mixed.pdf")
truth = json.loads((DATA / "truth.json").read_text(encoding="utf-8"))


def timed(fn, n=N):
    fn()  # warm-up
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts) * 1000, out


def pdfium_text(path):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(path)
    out = [p.get_textpage().get_text_bounded() for p in pdf]
    pdf.close()
    return out


def pdfplumber_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def pdfminer_text(path):
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer
    return ["".join(e.get_text() for e in page if isinstance(e, LTTextContainer)) for page in extract_pages(path)]


def pymupdf_text(path):
    import pymupdf
    with pymupdf.open(path) as doc:
        return [p.get_text() for p in doc]


def pypdf_text(path):
    from pypdf import PdfReader
    return [p.extract_text() for p in PdfReader(path).pages]


def norm(s):
    return " ".join(s.split())


print(f"== Text layer, native.pdf (2 pages), median of {N} runs ==")
for name, fn in [("pypdfium2", pdfium_text), ("pdfplumber", pdfplumber_text),
                 ("pdfminer.six", pdfminer_text), ("PyMuPDF", pymupdf_text), ("pypdf", pypdf_text)]:
    ms, pages = timed(lambda: fn(NATIVE))
    full = norm(" ".join(pages))
    hits = sum(v in full for v in truth["fields"].values())
    accents = all(w in full for w in ("MUÑOZ", "PEÑA", "IDENTIFICACIÓN", "GÜIZA"))
    print(f"{name:13s} {ms / 2:7.2f} ms/page  fields {hits}/3  accents_ok={accents}")

print("\n== Per-page text detection on mixed.pdf (p1 native, p2 scanned) ==")
import pypdfium2 as pdfium  # noqa: E402

def detect():
    pdf = pdfium.PdfDocument(MIXED)
    res = []
    for p in pdf:
        n = len(p.get_textpage().get_text_bounded().strip())
        imgs = sum(1 for o in p.get_objects() if o.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE)
        res.append((n, imgs))
    pdf.close()
    return res

ms, res = timed(detect)
print(f"pypdfium2 detect: {ms / 2:.2f} ms/page -> (chars, image_objs) per page = {res}")

print(f"\n== Rasterization of 1 page (scanned.pdf p1), median of {N} runs ==")
SCANNED = str(DATA / "scanned.pdf")
for dpi in (150, 200, 300):
    def r_pdfium(dpi=dpi):
        pdf = pdfium.PdfDocument(SCANNED)
        img = pdf[0].render(scale=dpi / 72, grayscale=True).to_numpy()
        pdf.close()
        return img.shape
    def r_mupdf(dpi=dpi):
        import pymupdf
        with pymupdf.open(SCANNED) as d:
            pix = d[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            return (pix.height, pix.width)
    ms1, shape = timed(r_pdfium)
    ms2, _ = timed(r_mupdf)
    print(f"{dpi} dpi -> {shape}: pypdfium2 {ms1:6.1f} ms | PyMuPDF {ms2:6.1f} ms")

print("\n== Bounding boxes sample (pypdfium2 char boxes, pdfplumber words) ==")
pdf = pdfium.PdfDocument(NATIVE)
tp = pdf[0].get_textpage()
txt = tp.get_text_bounded()
i = txt.find("11001000000045678912")
print("pdfium charbox of comparendo first digit:", tp.get_charbox(i))
import pdfplumber  # noqa: E402
with pdfplumber.open(NATIVE) as p:
    w = [w for w in p.pages[0].extract_words() if w["text"] == "11001000000045678912"][0]
    print("pdfplumber word bbox:", {k: round(w[k], 1) for k in ("x0", "top", "x1", "bottom")})
