# Motores locales de texto, OCR y LLM para PDFs mixtos

Responde a [05 · Motores locales de texto y OCR](../issues/05-motores-locales-texto-y-ocr.md). Las fuentes se leyeron el **2026-11-18** (ver [Fuentes](#fuentes)). Las mediciones son propias, sobre un PDF sintético (ver [Cómo se midió](#5-cómo-se-midió)).

## Resumen

- **Capa de texto → `pypdfium2`** (Apache-2.0 / BSD-3). Extrae texto a **~6 ms/página**, detecta por página si hay texto o solo imagen, da cajas por carácter, rasteriza y saca la imagen embebida de una página escaneada sin re-renderizarla. Alternativa: **`pdfplumber`** (MIT), solo si una plantilla necesita palabras o tablas con coordenadas y una API cómoda. Es unas 25 veces más lento (~158 ms/página). **PyMuPDF queda descartado: es AGPL-3.0** o requiere licencia comercial de Artifex.
- **OCR en español con CPU → `RapidOCR` 3.x + ONNX Runtime con el modelo `PP-OCRv5 latin mobile`** (Apache-2.0). Fue el más preciso: **CER 0,2 %**, 98,6 % de las palabras con tilde o ñ y 3/3 campos. Alternativa: **Tesseract 5.5 vía `tesserocr`** (Apache-2.0 + MIT) con `spa` *fast*. Es más rápido en esta máquina (~2,0 s frente a ~6,8 s por página), pero comete más errores (CER 2,9 %, por ejemplo `1,023.456.789`). Su wheel ya trae Tesseract en Windows y Linux. **Se descartan EasyOCR** (el más lento, CER 8 %, último release en 2024-09) y **docTR** (lento, y sus modelos por defecto no tienen á/í/ó/ú/ñ).
- **LLM local (Ollama) → solo como respaldo para campos dudosos, no como camino principal.** `gpt-oss:20b` (Apache-2.0, 14 GB) **no cabe en 4 GB de VRAM**, así que corre casi todo en CPU. Su latencia **quedó pendiente de medir**. Para esta GPU, los candidatos por licencia y tamaño son **`qwen3:4b`** (texto, 2,5 GB) y **`qwen3-vl:2b` / `qwen3-vl:4b`** (visión, 1,9 / 3,3 GB), todos Apache-2.0. **No se han medido** porque habría que descargarlos. `qwen2.5vl:3b` queda descartado porque su licencia (`qwen-research`) no permite uso comercial.
- **Los tiempos absolutos de OCR medidos en este portátil son pesimistas.** La CPU es híbrida (núcleos P y E) y escala mal con los hilos. PaddleOCR publica entre 0,2 y 0,8 s por imagen en un Xeon. La medición en Docker/Linux **quedó pendiente**, con el script listo. Las comparaciones *entre* motores sí son válidas, porque todos se midieron igual.

## 1. Capa de texto (PDF nativo)

Medido sobre `native.pdf` (2 páginas). Cada corrida abre el archivo, extrae el texto y lo cierra, como haría el servicio en cada solicitud. Se reporta la mediana de 30 corridas.

| Motor (versión) | Licencia (verificada) | ms/página | Campos / tildes | Coordenadas | Windows / Docker | Mantenimiento |
|---|---|---|---|---|---|---|
| **pypdfium2** 5.15.2 | Apache-2.0 / BSD-3-Clause; PDFium: BSD | **5,9** | 3/3 ✓ | Sí, por carácter (`get_charbox`, `get_rect`) | Wheels binarios (probado en Windows) | Release 2026-11-09 |
| PyMuPDF 2.1 | **AGPL-3.0 o licencia comercial Artifex** ⚠ | 6,2 | 3/3 ✓ | Sí (palabras, bloques) | Wheels; Python ≥ 3.11 | Release 2026-11-02 |
| pypdf 7.1.1 | BSD-3-Clause | 14,8 | 3/3 ✓ | Limitadas (por *visitor*) | Python puro | Release 2026-11-18 |
| pdfminer.six 20260107 | MIT | 55,0 | 3/3 ✓ | Sí (`LTChar`, `LTTextBox`) | Python puro | Release 2026-01-07 |
| pdfplumber 0.11.10 | MIT | 157,6 | 3/3 ✓ | Sí: palabras, caracteres, líneas y tablas (`extract_words`) | Python puro (usa pdfminer.six) | Release 2026-06-15 |

- **Detección por página** (ticket 03). Con `pypdfium2` se cuentan los caracteres de la capa de texto y los objetos imagen de cada página en **8,4 ms/página**. En `mixed.pdf` dio `[(1384 caracteres, 0 imágenes), (0 caracteres, 1 imagen)]`, así que basta una regla simple para decidir si una página va a OCR.
- **Concurrencia.** "PDFium is inherently not thread-safe" (README de pypdfium2). Para paralelizar hay que usar **procesos**, no hilos.

## 2. Rasterización para OCR

Mediana de 30 corridas, página 1 de `scanned.pdf` (un JPEG de 1700×2200 embebido).

| Operación | pypdfium2 | PyMuPDF |
|---|---|---|
| Renderizar página escaneada a 150 ppp | 159 ms | 134 ms |
| Renderizar página escaneada a 200 ppp | 192 ms | 97 ms |
| Renderizar página escaneada a 300 ppp | 319 ms | 274 ms |
| Renderizar página **vectorial** a 200 / 300 ppp | 20 / 27 ms | — |
| **Extraer la imagen embebida sin renderizar** (`PdfImage.get_bitmap(render=False)`) | **27 ms** | — |

- **Optimización clara.** Si una página escaneada es una sola imagen a página completa, conviene **extraer esa imagen** en vez de renderizarla: es unas 7 veces más rápido y conserva la resolución original del escaneo. Solo se renderiza si la página mezcla vectores e imágenes.
- PyMuPDF renderiza algo más rápido, pero su licencia lo descarta. `pdf2image` (MIT) necesita los binarios de Poppler instalados en el sistema, una dependencia extra en Windows. La licencia de Poppler no se verificó en esta sesión.

## 3. OCR de páginas escaneadas (español, CPU)

**Condiciones de la medición.** Dos páginas escaneadas sintéticas a 200 ppp, con ruido, desenfoque y una inclinación de 0,8°. Windows 11, Intel Core Ultra 7 155H, 4 hilos, sin GPU. Se reporta la mediana por página tras un calentamiento.

**Métricas.** *CER*: tasa de error de caracteres sobre el texto completo. *Palabras* y *tildes*: porcentaje de palabras correctas, en total y solo entre las que llevan á/é/í/ó/ú/ñ/ü. *Campos*: identificación, nro. de comparendo y fecha leídos exactamente en la página 1.

| Motor + modelo (versión) | Licencia código / modelos | ms/página | CER | Palabras | Tildes | Campos | Coordenadas | Instalación |
|---|---|---|---|---|---|---|---|---|
| **RapidOCR 3.10 · PP-OCRv5 latin mobile** | Apache-2.0 / Apache-2.0 | 6.770 | **0,2 %** | **99,3 %** | **98,6 %** | **3/3** | Caja por línea (4 puntos) y *score*; caja por palabra o carácter opcional | `pip install rapidocr onnxruntime`; los modelos se descargan en el primer uso |
| RapidOCR · PP-OCRv6 small (`es`) | Apache-2.0 / Apache-2.0 | 7.433 | 0,6 % | 93,0 %¹ | 93,2 % | 3/3 | Igual | Igual |
| RapidOCR · PP-OCRv6 tiny (`es`) | Apache-2.0 / Apache-2.0 | **2.193** | 0,9 % | 93,0 %¹ | 89,2 % | 2/3² | Igual | Igual |
| **Tesseract 5.5.4 · `spa` fast** (tesserocr 2.13) | Apache-2.0 (Tesseract y tessdata) + MIT (tesserocr) | **1.977** | 2,9 % | 90,6 % | 93,2 % | 2/3³ | Caja y confianza por palabra (`iterate_level`) | El wheel incluye Tesseract y Leptonica (Windows y Linux); `spa.traineddata` aparte (2,3 MB) |
| Tesseract 5.5.4 · `spa` best | Igual | 4.852 | 3,5 % | 88,0 % | 95,9 % | 2/3³ | Igual | `spa.traineddata` de 13,6 MB |
| docTR 1.1 · fast_base + PARSeq multilingüe (HF) | Apache-2.0 / Apache-2.0 | 27.654 | 2,9 % | 85,1 % | 93,2 % | 3/3 | Geometría por palabra | PyTorch (cientos de MB) |
| docTR 1.1 · modelo por defecto (`crnn_vgg16_bn`) | Apache-2.0 | 11.365 | 3,7 % | 78,8 % | **8,1 %**⁴ | 2/3³ | Igual | PyTorch |
| EasyOCR 1.7.2 (`es`) | Apache-2.0 | 43.812 | 8,0 % | 87,2 % | 86,5 % | 2/3³ | Caja por línea y confianza | PyTorch; **último release 2024-09-24** |
| PaddleOCR 3.7 (paddlepaddle 3.3) | Apache-2.0 | no medido⁵ | — | — | — | — | Sí | `paddlepaddle` es pesado; la GPU usa índices propios |

¹ PP-OCRv6 a veces une palabras (`No.11001000000045678912`). El CER casi no cambia, pero la métrica por palabra baja.
² PP-OCRv6 tiny **omitió un dígito** del comparendo (`1100100000045678912`). Una regla que valide la longitud del número lo detectaría.
³ El fallo fue **punto leído como coma** en la identificación (`1,023.456.789`). Normalizando la identificación a solo dígitos, el campo pasa.
⁴ Los modelos por defecto de docTR usan `VOCABS["french"]`, que **no contiene á, í, ó, ú ni ñ** (`doctr/datasets/vocabs.py`). Para español hace falta el modelo comunitario `Felix92/doctr-torch-parseq-multilingual-v1`.
⁵ RapidOCR ejecuta en ONNX los mismos pesos PP-OCR de PaddleOCR. Por eso PaddleOCR no se instaló aparte.

**Soporte del español por motor**

- **Tesseract:** paquete `spa` en `tessdata_fast` (el que traen las distribuciones Linux) y en `tessdata_best` (más lento y en coma flotante).
- **PaddleOCR / RapidOCR:** el modelo `latin_PP-OCRv5_mobile_rec` lista el español explícitamente (84,7 % en el conjunto latino de PaddleOCR). PP-OCRv6 (2026-06-11) trae un solo modelo multilingüe de 50 idiomas, 49 en *tiny*. RapidOCR acepta `lang_type="es"` para v6.
- **EasyOCR:** `es` aparece en su tabla de idiomas.
- **docTR:** ver la nota ⁴.

**Velocidad de referencia publicada.** PP-OCRv6 en un Xeon 8350C: *tiny* 0,20–0,32 s/imagen, *small* 0,59–0,79 s y *medium* 1,40–2,05 s, con OpenVINO o Paddle, incluyendo E/S. Son entre 7 y 10 veces mejores que lo medido aquí. En este portátil, PP-OCRv6 tiny tardó 3,4 s con todos los hilos, 3,0 s con 6, **2,0 s con 4** y 3,8 s con 1. Esa mala escala con los hilos sugiere que Windows asigna los procesos en segundo plano a núcleos de eficiencia. **Los tiempos absolutos hay que repetirlos en Linux** (pendiente).

**GPU (no medida).** `onnxruntime-gpu` necesita CUDA 12.8+ o 13 y cuDNN 9. Puede cargarlos desde paquetes pip de NVIDIA (`onnxruntime.preload_dlls()`). Con una CPU de servidor probablemente no hace falta para 50.000 PDFs/día, pero depende de la proporción de páginas escaneadas (ticket 04).

## 4. LLM local (Ollama) como extractor de respaldo

Entorno: Ollama 0.40.16 con `gpt-oss:20b` instalado y una RTX 500 Ada de 4 GB. Ollama acepta un JSON Schema en `format` (también en modelos de visión) y `temperature: 0`. Para `gpt-oss`, el razonamiento no se puede apagar: el nivel mínimo es `think: "low"`.

| Modelo (Ollama) | Tipo | Descarga | ¿Cabe en 4 GB? | Licencia (Hugging Face) | Veredicto |
|---|---|---|---|---|---|
| `gpt-oss:20b` (instalado) | Texto | 14 GB (MXFP4) | **No**: corre casi todo en CPU | Apache-2.0 | Sirve funcionalmente. **Latencia pendiente** (script listo). En producción, solo con una GPU de 16 GB o más |
| `qwen3:4b` | Texto | 2,5 GB | Sí | Apache-2.0 (Qwen3-4B-Instruct-2507) | **Candidato** de texto a medir |
| `qwen3-vl:2b` / `qwen3-vl:4b` | Visión | 1,9 / 3,3 GB | Sí (el de 4b, con poco margen para la caché KV) | Apache-2.0 | **Candidato** de visión a medir (OCR en 32 idiomas según Ollama) |
| `granite3.2-vision:2b` | Visión (documentos) | 2,4 GB | Sí | Apache-2.0 | Alternativa; no indica idiomas |
| `qwen2.5vl:3b` | Visión | 3,2 GB | Sí | **`qwen-research` (no comercial)** ⚠ | **Descartado** por licencia |
| `qwen2.5vl:7b` | Visión | 6,0 GB | No | Apache-2.0 | Solo con una GPU mayor |
| `llama3.2-vision:11b` | Visión | 7,8 GB | No | Licencia Llama 3.2 (propia: atribución "Built with Llama", límite de 700 M de usuarios) ⚠ | **Descartado**: no cabe y "for image+text applications, English is the only language supported" |
| `deepseek-ocr:3b` | Visión (OCR) | 6,7 GB | No | MIT | Solo con una GPU mayor |

**Viabilidad.** Las reglas sobre el texto toman milisegundos, y un LLM local en esta máquina tomaría segundos o decenas de segundos por documento. Por eso el LLM solo cabe como **respaldo para los campos con baja confianza o que no pasen validación**, nunca para todo el volumen. Ejemplo ilustrativo, no medido: si se llamara para el 5 % de 50.000 documentos y cada llamada tardara unos 30 s, serían 2.500 llamadas y unas 21 horas de cómputo en serie al día. Eso no cabe en este portátil, así que hay que medir el costo real en el benchmark (ticket 13) y dimensionar la GPU del servidor.

## 5. Cómo se midió

- **Datos.** `gen_pdf.py` genera un mandamiento de pago sintético de 2 páginas con `reportlab`: nombres con tildes y ñ, `C.C. 1.023.456.789`, comparendo `11001000000045678912` y fecha `02/01/2024`. A partir de él crea `native.pdf`, `scanned.pdf` (renderizado a 200 ppp, en grises, rotado 0,8°, con desenfoque, ruido "sal y pimienta" y JPEG de calidad 65) y `mixed.pdf` (página 1 nativa, página 2 escaneada). No se usaron datos reales.
- **Hardware.** Windows 11, Intel Core Ultra 7 155H (16 núcleos y 22 hilos, híbrida), 32 GB de RAM, con cargador y plan "Equilibrado". Python 3.12 con `uv run --with …`; no se instaló nada en el sistema.
- **Scripts** (en el *scratchpad* de la sesión, que **no es persistente**): `gen_pdf.py`, `bench_text.py`, `bench_raster2.py`, `ocr_rapid.py`, `ocr_tess.py`, `ocr_torch.py`, `score.py`, más `docker_bench.sh` y `llm_ollama.py`, preparados pero no ejecutados. Ruta: `C:\Users\JCHICA~1\AppData\Local\Temp\claude\bdfffe85992694a5\0724d7eb-a340-4be4-af35-3d1078f9d49c\scratchpad\`.
- **Límites.** Es un solo documento sintético y de buena calidad tipográfica, así que sirve para ordenar los motores, no para estimar la exactitud con escaneos reales (ticket 13). No se probó preprocesamiento (eliminar ruido, binarizar), que suele mejorar Tesseract.

### Pendiente (no ejecutado, por indicación del director)

1. RapidOCR y Tesseract en **Docker Linux** (`python:3.12-slim`, script `docker_bench.sh`), para tener tiempos absolutos representativos del servidor y medir con 1 hilo (rendimiento por núcleo).
2. Latencia de **`gpt-oss:20b`** en frío y en tibio, extrayendo 3 campos con JSON Schema (`llm_ollama.py`).
3. `qwen3:4b` y `qwen3-vl:2b`/`4b`: requieren descargar entre 2 y 3 GB cada uno.
4. PaddleOCR nativo, el backend OpenVINO de RapidOCR y OCR con GPU.

## 6. Recomendación para el benchmark (ticket 13)

| Categoría | Candidato 1 | Candidato 2 | Descartados |
|---|---|---|---|
| Capa de texto | `pypdfium2`: detección, texto, cajas, raster y extracción de imagen con una sola dependencia | `pdfplumber`, solo para plantillas que necesiten palabras o tablas con coordenadas | PyMuPDF (AGPL) |
| OCR español (CPU) | `rapidocr` + `onnxruntime`, PP-OCRv5 latin mobile; evaluar PP-OCRv6 tiny como modo rápido, con validación de campos | Tesseract 5 vía `tesserocr` (`spa` fast) | EasyOCR (lento, sin releases desde 2024), docTR (lento; por defecto sin tildes del español) |
| LLM local (respaldo) | `qwen3:4b` sobre el texto del OCR | `qwen3-vl:4b` (o `2b`) sobre la imagen de la página | `qwen2.5vl:3b` (licencia no comercial), `llama3.2-vision` (no cabe y solo inglés), `gpt-oss:20b` en GPUs de 4 GB |

**Notas para empaquetar**

- **Modelos dentro de la imagen.** RapidOCR descarga los modelos de `modelscope.cn` en el primer uso. Para Docker y entornos sin salida a internet hay que **incluirlos en la imagen** y fijar la versión, y lo mismo con `spa.traineddata`. Esas descargas no envían datos del documento, pero sí son salidas de red.
- **Paralelismo por procesos.** Un *worker* por núcleo (o por grupo de 2–4 hilos), porque PDFium no es seguro entre hilos. `tesserocr` libera el GIL y admite hilos con una instancia por *worker*.
- **Validar y normalizar antes de usar un LLM.** Normalizar la identificación a solo dígitos y validar la longitud del comparendo y el formato de la fecha resuelve varios de los fallos vistos, sin LLM.

## Fuentes

**Licencias, versiones y actividad (fuente primaria)**

- PyPI JSON API, `https://pypi.org/pypi/<paquete>/json`: pypdfium2, pdfplumber, pdfminer.six, PyMuPDF, pypdf, pytesseract, tesserocr, paddleocr, paddlepaddle, python-doctr, rapidocr, rapidocr-onnxruntime, easyocr, onnxruntime, torch, ollama, pdf2image.
- GitHub REST API, `https://api.github.com/repos/<repo>`: licencia SPDX y último *push* de cada repositorio citado.
- https://raw.githubusercontent.com/pymupdf/PyMuPDF/main/COPYING (AGPL-3.0)
- https://raw.githubusercontent.com/tesseract-ocr/tesseract/main/LICENSE (Apache-2.0)
- https://raw.githubusercontent.com/JaidedAI/EasyOCR/master/LICENSE (Apache-2.0)
- https://raw.githubusercontent.com/mindee/doctr/main/LICENSE (Apache-2.0)
- https://raw.githubusercontent.com/pypdfium2-team/pypdfium2/main/README.md (licencia; "PDFium is inherently not thread-safe")
- Hugging Face API, `https://huggingface.co/api/models/<id>`: Qwen/Qwen2.5-VL-3B-Instruct (`qwen-research`), Qwen/Qwen2.5-VL-7B-Instruct, Qwen/Qwen3-VL-2B/4B/8B-Instruct, Qwen/Qwen3-4B-Instruct-2507, openai/gpt-oss-20b, ibm-granite/granite-vision-3.2-2b (Apache-2.0), meta-llama/Llama-3.2-11B-Vision-Instruct (`llama3.2`), google/gemma-3-4b-it (`gemma`), deepseek-ai/DeepSeek-OCR (MIT), PaddlePaddle/PaddleOCR-VL (Apache-2.0), Felix92/doctr-torch-parseq-multilingual-v1 (Apache-2.0).
- https://raw.githubusercontent.com/meta-llama/llama-models/main/models/llama3_2/LICENSE

**Documentación técnica**

- https://pypi.org/project/tesserocr/ (los wheels incluyen Tesseract; tessdata aparte; liberación del GIL)
- https://github.com/tesseract-ocr/tessdoc/blob/main/Data-Files.md (diferencias entre tessdata, fast y best)
- https://raw.githubusercontent.com/RapidAI/RapidOCR/main/README.md
- https://rapidai.github.io/RapidOCRDocs/main/model_list/ y https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/parameters/
- https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html
- https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html
- https://www.paddleocr.ai/latest/en/version3.x/installation.html
- https://raw.githubusercontent.com/mindee/doctr/main/doctr/datasets/vocabs.py y `doctr/models/recognition/{crnn,parseq}/pytorch.py` (vocabulario por defecto `french`)
- https://huggingface.co/Felix92/doctr-torch-parseq-multilingual-v1
- https://www.jaided.ai/easyocr/
- https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
- https://docs.ollama.com/capabilities/structured-outputs y https://docs.ollama.com/capabilities/thinking
- https://ollama.com/library/gpt-oss, `/qwen3`, `/qwen3-vl`, `/qwen2.5vl/tags`, `/llama3.2-vision`, `/granite3.2-vision`, `/deepseek-ocr`
