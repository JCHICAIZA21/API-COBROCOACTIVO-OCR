# Motores locales de texto y OCR para PDFs mixtos

Type: research
Status: resolved

## Question

¿Qué motores **locales** (sin enviar datos fuera) sirven para (a) extraer la capa de texto de PDFs nativos y (b) hacer OCR de páginas escaneadas en español? ¿Cómo se comparan?

Candidatos mínimos:

- **Texto**: pypdfium2, pdfplumber, pdfminer.six, PyMuPDF.
- **OCR**: Tesseract 5 (pytesseract/tesserocr), PaddleOCR, docTR, RapidOCR (ONNX), EasyOCR.

Criterios:

- Velocidad por página en CPU y en GPU.
- Precisión en español.
- **Licencia compatible con uso comercial** (ej.: PyMuPDF es AGPL o requiere licencia comercial).
- Instalación en Docker (Linux) y Windows.
- Si entrega coordenadas/zonas de cada texto.
- Mantenimiento activo.

Entregable: tabla comparativa y 1–2 candidatos por categoría para el [Benchmark de motores sobre las muestras](13-benchmark-motores.md).

## Answer

Hallazgos completos en [research/05-motores-locales.md](../research/05-motores-locales.md); scripts en [research/bench/](../research/bench/).

- **Capa de texto**: `pypdfium2` (Apache-2.0/BSD-3): ~6 ms/página; detección nativa/escaneada en ~8 ms. PDFium no es seguro entre hilos, así que se paraleliza por **procesos**. PyMuPDF queda descartado (AGPL).
- **OCR (CPU, español)**: `rapidocr` + `onnxruntime` con PP-OCRv5 latin, el más preciso (0,2 % de errores, 3/3 campos). Alternativa: Tesseract 5.5 vía `tesserocr` (más rápido, 2,9 % de errores). El usuario eligió **RapidOCR por defecto** (2026-11-18); los dos quedan implementados.
- **LLM local**: `gpt-oss:20b` no cabe en una GPU de 4 GB. Candidatos: `qwen3:4b` / `qwen3-vl:4b` (Apache-2.0). El usuario **no autoriza descargarlos por ahora**.
- **Modelos OCR dentro de la imagen Docker**, con checksums fijados y sin descargas en ejecución (decisión del usuario).
