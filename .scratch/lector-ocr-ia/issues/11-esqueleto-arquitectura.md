# Esqueleto de arquitectura hexagonal del servicio

Type: prototype
Status: resolved
Blocked by: 07

## Question

¿Cómo se ve la arquitectura? Construir un esqueleto mínimo para reaccionar sobre él: interfaces, casos de uso y cableado, sin motores reales.

Hipótesis de partida, para validar o descartar:

- **Puertos y adaptadores**: dominio puro sin dependencias; el núcleo es un paquete independiente del framework web, reutilizable en otros proyectos. FastAPI y los workers son solo adaptadores de entrada.
- **Cascada de lo más barato a lo más caro**: capa de texto (ms) → reglas deterministas → OCR solo en páginas sin texto → LLM solo para campos con baja confianza y solo si la Política lo permite.
- **Motores como estrategias intercambiables** registradas por configuración (abierto/cerrado), interfaces pequeñas (segregación de interfaces), dependencias inyectadas en una única raíz de composición (inversión de dependencias).
- **Velocidad**: I/O asíncrono para APIs y almacenamiento; trabajo de CPU (rasterizado, OCR) en procesos/workers separados; caché idempotente por hash del PDF + versión de plantilla.

Entregable: esqueleto de código y diagrama, enlazados como asset desde este ticket.

## Answer

Construido y en verde (2026-11-18). Las cuatro hipótesis se confirmaron.

- **Núcleo `docreader`** (sin framework web, reutilizable): `domain.py` (objetos de valor inmutables y errores), `ports.py` (puertos pequeños: `DocumentSource`, `PageReader`, `OcrEngine`, `FieldExtractor`, `TemplateRepository`, `ExtractionCache`), `reader.py` (caso de uso `DocumentReader`), `normalize.py`, `templates.py` (plantillas YAML), `rules.py` (regex), `cache.py`, `settings.py` y `factory.py` (raíz de composición con imports perezosos).
- **Adaptadores**: `pages.py` (pypdfium2 + pool de procesos + OCR solo en páginas sin texto), `ocr_rapidocr.py`, `ocr_tesseract.py`, `llm_claude.py`, `llm_openai.py`, `llm_ollama.py` y `sources.py` (carpeta, HTTP con lista blanca, BLOB PostgreSQL).
- **Servicio `docreader_api`**: `app.py` (`create_app` con inyección de dependencias), `guard.py` (API key y límites antes de leer el cuerpo), `routes.py`, `schemas.py`, `errors.py` (RFC 9457), `jobs_pg.py`, `queue_redis.py` (Redis Streams), `worker.py` (con mantenimiento: retención y re-encolado) y `main.py` (cableado de producción).
- **Cascada**: texto nativo (ms) → reglas → OCR solo donde falta texto → LLM local/externo solo para campos pendientes y solo si la política lo permite.
- **Medido de punta a punta** con PDFs sintéticos (portátil): nativo 8 ms, mixto 2,9 s, escaneado de 2 páginas 5,1 s; 3/3 campos clave `match`.
