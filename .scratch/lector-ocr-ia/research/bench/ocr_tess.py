"""Tesseract 5 via tesserocr on the 2 scanned pages.

Usage: python ocr_tess.py <data_dir> <out_dir> <label> <tessdata_dir> [<tessdata_dir2> ...]
"""
import json
import statistics
import sys
import time
from pathlib import Path

import tesserocr
from PIL import Image
from tesserocr import PSM, PyTessBaseAPI, RIL, iterate_level

DATA, OUT, LABEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
imgs = [Image.open(DATA / f"scan_p{i}.png").convert("L") for i in (1, 2)]
print("tesserocr", tesserocr.__version__, "tesseract", tesserocr.tesseract_version().splitlines()[0])

for td in sys.argv[4:]:
    kind = Path(td).name
    with PyTessBaseAPI(path=td, lang="spa", psm=PSM.AUTO) as api:
        def ocr(im):
            api.SetImage(im)
            return api.GetUTF8Text()
        for im in imgs:
            ocr(im)  # warm-up
        ts, pages = [], []
        for _ in range(3):
            pages = []
            for im in imgs:
                t0 = time.perf_counter()
                pages.append(ocr(im))
                ts.append(time.perf_counter() - t0)
        ms = statistics.median(ts) * 1000
        # word boxes + confidences are available through the result iterator
        api.SetImage(imgs[0])
        api.Recognize()
        sample = []
        for w in iterate_level(api.GetIterator(), RIL.WORD):
            t = w.GetUTF8Text(RIL.WORD)
            if t and t.startswith("1100100"):
                sample = [t, w.BoundingBox(RIL.WORD), round(w.Confidence(RIL.WORD), 1)]
                break
    name = f"tesseract spa {kind} ({LABEL})"
    out = {"engine": name, "ms_per_page": ms, "ms_per_page_1thread": None, "pages": pages, "sample": sample}
    (OUT / f"res_tess_{kind}_{LABEL}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: {ms:.0f} ms/page; word box sample {sample}", flush=True)
