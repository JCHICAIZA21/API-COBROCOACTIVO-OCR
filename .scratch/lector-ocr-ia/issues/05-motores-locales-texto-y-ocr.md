# Motores locales de texto y OCR para PDFs mixtos

Type: research
Status: open

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
