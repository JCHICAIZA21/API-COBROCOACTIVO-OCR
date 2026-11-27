"""RapidOCR (ONNX Runtime, CPU) on the 2 scanned pages.

Usage: python ocr_rapid.py <data_dir> <out_dir> <label> <threads>
"""
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

DATA, OUT, LABEL, THREADS = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
imgs = [np.array(Image.open(DATA / f"scan_p{i}.png").convert("RGB")) for i in (1, 2)]

COMMON = {"Global.use_cls": False,
          "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
          "EngineConfig.onnxruntime.inter_op_num_threads": 1}
CONFIGS = {
    "rapidocr v6-tiny es": {
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.TINY, "Det.lang_type": "es",
        "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.TINY, "Rec.lang_type": "es"},
    "rapidocr v6-small es": {
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL, "Det.lang_type": "es",
        "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL, "Rec.lang_type": "es"},
    "rapidocr v5-mobile latin": {
        "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.MOBILE,
        "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE, "Rec.lang_type": LangRec.LATIN},
}


def to_text(res):
    items = sorted(zip(res.boxes, res.txts), key=lambda bt: (round(bt[0][0][1] / 15), bt[0][0][0]))
    return "\n".join(t for _, t in items)


for name, params in CONFIGS.items():
    eng = RapidOCR(params={**COMMON, **params})
    for im in imgs:
        eng(im)  # warm-up
    ts, results = [], []
    for _ in range(2):
        results = []
        for im in imgs:
            t0 = time.perf_counter()
            results.append(eng(im))
            ts.append(time.perf_counter() - t0)
    ms = statistics.median(ts) * 1000
    r0 = results[0]
    k = next(i for i, t in enumerate(r0.txts) if "COMPARENDO" in t)
    full = f"{name} ({LABEL}, {THREADS} hilos)"
    out = {"engine": full, "ms_per_page": ms, "ms_per_page_1thread": None,
           "pages": [to_text(r) for r in results],
           "sample": [r0.txts[k], [list(map(float, p)) for p in r0.boxes[k]], float(r0.scores[k])]}
    fn = OUT / ("res_" + name.replace(" ", "_") + f"_{LABEL}.json")
    fn.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{full}: {ms:.0f} ms/page; sample {out['sample']}", flush=True)
