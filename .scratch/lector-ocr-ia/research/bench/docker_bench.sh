#!/bin/sh
# Runs inside python:3.12-slim with the scratchpad mounted at /w.
set -e
pip install -q --root-user-action=ignore "rapidocr==3.10.0" onnxruntime pillow numpy tesserocr pypdfium2 pdfplumber >/dev/null 2>&1
python -c "import tesserocr, onnxruntime; print('tesseract', tesserocr.tesseract_version().splitlines()[0], '| onnxruntime', onnxruntime.__version__)"
python -I /w/ocr_tess.py /w/data /w/results docker /w/tessdata_fast /w/tessdata_best
python -I /w/ocr_rapid.py /w/data /w/results docker 4 2>&1 | grep -v INFO
python -I /w/ocr_rapid.py /w/data /w/results docker1 1 2>&1 | grep -v INFO
