"""Field extraction from (OCR) text with a local Ollama model; measures latency.

Usage: python llm_ollama.py <data_dir> <ocr_result.json> <model> [think] [runs]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

DATA, SRC, MODEL = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
THINK = sys.argv[4] if len(sys.argv) > 4 else None
RUNS = int(sys.argv[5]) if len(sys.argv) > 5 else 3
truth = json.loads((DATA / "truth.json").read_text(encoding="utf-8"))["fields"]
text = "\n\n".join(json.loads(SRC.read_text(encoding="utf-8"))["pages"])

SCHEMA = {
    "type": "object",
    "properties": {
        "identificacion": {"type": "string", "description": "Número de documento del ejecutado, tal como aparece"},
        "nro_comparendo": {"type": "string", "description": "Número de la orden de comparendo"},
        "fecha": {"type": "string", "description": "Fecha de imposición del comparendo, DD/MM/AAAA"},
    },
    "required": ["identificacion", "nro_comparendo", "fecha"],
}
PROMPT = (
    "Extrae del siguiente documento de cobro coactivo los campos pedidos. "
    "Copia los valores exactamente como aparecen; si un campo no está, usa cadena vacía. "
    f"Responde solo con JSON que cumpla este esquema: {json.dumps(SCHEMA, ensure_ascii=False)}\n\n"
    f"DOCUMENTO:\n{text}"
)


def call():
    body = {"model": MODEL, "stream": False, "format": SCHEMA,
            "messages": [{"role": "user", "content": PROMPT}],
            "options": {"temperature": 0, "num_ctx": 4096}, "keep_alive": "10m"}
    if THINK:
        body["think"] = THINK
    req = urllib.request.Request("http://localhost:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.load(r)
    wall = time.perf_counter() - t0
    return wall, d


for i in range(RUNS + 1):
    wall, d = call()
    try:
        got = json.loads(d["message"]["content"])
    except Exception:  # noqa: BLE001
        got = {}
    ok = sum(str(got.get(k, "")).replace(" ", "") == v.replace(" ", "") for k, v in truth.items())
    ns = 1e9
    tps = d.get("eval_count", 0) / (d.get("eval_duration", 1) / ns)
    pps = d.get("prompt_eval_count", 0) / (d.get("prompt_eval_duration", 1) / ns)
    label = "frío" if i == 0 else f"tibio {i}"
    print(f"{MODEL} think={THINK} [{label}] wall={wall:.1f}s load={d.get('load_duration', 0)/ns:.1f}s "
          f"prompt={d.get('prompt_eval_count')}tok@{pps:.0f}tok/s gen={d.get('eval_count')}tok@{tps:.1f}tok/s "
          f"campos={ok}/3 -> {got}", flush=True)
