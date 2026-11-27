"""Score OCR result JSON files against truth.json.

Usage: python score.py <data_dir> <result1.json> [<result2.json> ...]
Each result JSON: {"engine": str, "ms_per_page": float, "ms_per_page_1thread": float|None,
                   "pages": [str, str]}
Metrics (order-independent, robust to OCR line ordering):
- word_recall: share of ground-truth words found exactly (multiset) in OCR output.
- accent_recall: same, only for words with á é í ó ú ñ ü (any case).
- fields: how many of identificacion / nro_comparendo / fecha appear (whitespace-insensitive).
- cer: character error rate on whitespace-normalised full text (sensitive to ordering).
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(sys.argv[1])
truth = json.loads((DATA / "truth.json").read_text(encoding="utf-8"))
ACC = re.compile(r"[áéíóúñüÁÉÍÓÚÑÜ]")


def words(s):
    return re.findall(r"[\wÁÉÍÓÚÑÜáéíóúñü./$-]+", s)


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


gt_text = "\n".join(truth["pages"])
gt_words = Counter(words(gt_text))
gt_acc = Counter({w: c for w, c in gt_words.items() if ACC.search(w)})

print(f"{'engine':52s} {'ms/pag':>8s} {'palabras':>9s} {'acentos':>8s} {'campos':>7s} {'CER':>6s}")
for f in sys.argv[2:]:
    r = json.loads(Path(f).read_text(encoding="utf-8"))
    text = "\n".join(r["pages"])
    ow = Counter(words(text))
    wr = sum(min(c, ow[w]) for w, c in gt_words.items()) / sum(gt_words.values())
    ar = sum(min(c, ow[w]) for w, c in gt_acc.items()) / sum(gt_acc.values())
    flat = re.sub(r"\s+", "", r["pages"][0])  # fields must be read on page 1 (header block)
    fh = sum(re.sub(r"\s+", "", v) in flat for v in truth["fields"].values())
    a, b = " ".join(gt_text.split()), " ".join(text.split())
    cer = lev(a, b) / len(a)
    print(f"{r['engine']:52s} {r['ms_per_page']:8.0f} {wr:9.1%} {ar:8.1%} {fh:>5d}/3 {cer:6.1%}")
