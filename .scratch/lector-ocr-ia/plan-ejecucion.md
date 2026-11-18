# Plan de ejecución y delegación

Complementa a [map.md](map.md). Aquí está **quién hace qué y con qué skills**. Cada sesión o agente lo consulta para no tener que repetir instrucciones.

## Entorno (verificado 2026-11-18)

- Windows 11; **sin Python de sistema**. Usar `uv` (0.10.7) con CPython 3.12 (`uv run`, `uv add`).
- Docker 29 (Linux) en ejecución. Tesseract **no** está instalado en Windows; usarlo vía Docker o elegir un OCR instalable con pip.
- GPU NVIDIA RTX 500 Ada (laptop, ~4 GB VRAM). Ollama instalado con `gpt-oss:20b` (solo texto).
- `ANTHROPIC_API_KEY` y `OPENAI_API_KEY` **no** configuradas: los adaptadores LLM se prueban con dobles de prueba hasta tener llaves.
- La carpeta no es repo git; los ejecutores trabajan sobre **archivos disjuntos** asignados por el director.
- **El venv vive fuera de OneDrive** (para no sincronizar miles de archivos): todo comando `uv` se ejecuta con `UV_PROJECT_ENVIRONMENT="C:/Users/JCHICAIZA/.venvs/docreader"`. Versiones resueltas: anthropic 2.10, openai 3.55, fastapi 0.165, pypdfium2 5.15, pydantic 2.14, asyncpg 0.32, redis 8.1.
- Solo el director modifica `pyproject.toml` / `uv.lock` y el esqueleto (`domain.py`, `ports.py`, `settings.py`, `docreader_api/ports.py`).

## Asignación de la construcción (F2)

| Ejecutor | Archivos |
|---|---|
| Director (esqueleto + cableado) | `docreader/{domain,ports,settings,factory,__init__}.py`, `docreader_api/{ports,main}.py`, `templates/` |
| E1 Núcleo | `docreader/{normalize,templates,rules,cache,reader}.py` |
| E2 Páginas/OCR | `docreader/adapters/{pages,ocr_*}.py`, `tests/pdf_factory.py` |
| E3 LLM | `docreader/adapters/llm_{common,claude,openai,ollama}.py` |
| E4 Orígenes | `docreader/adapters/sources.py` |
| E5 API HTTP | `docreader_api/{app,auth,schemas,routes}.py` |
| E6 Lotes | `docreader_api/{jobs_pg,queue_redis,worker}.py`, `schema.sql` |
| E7 DevOps | `Dockerfile`, `compose.yaml`, `.env.example`, `.dockerignore` |
| QA | `tests/e2e/`, `tests/load/`, generador de dataset sintético |

## Convenciones

- Código e identificadores en **inglés**; documentación, plantillas de campos y mensajes al usuario en **español**.
- Layout `src/`: `docreader` = núcleo reutilizable sin framework web; `docreader_api` = cascarón de servicio (API + worker).
- Cada agente escribe solo en los archivos que se le asignan. El director registra las respuestas en los tickets y en `map.md`.

## Roles y skills

| Rol | Quién | Responsabilidad | Skills |
|---|---|---|---|
| **Director** | sesión principal | Mapa, descomposición, asignación, integración, preguntas HITL al usuario, decisión final | wayfinder (método), `grilling`, `domain-modeling`, `writing-for-agents`, `workflow-authoring` |
| **Investigador** | subagente `general-purpose` | Tickets `research`; hallazgos en `research/NN-*.md` | `research`, `claude-api`, `defuddle` |
| **Arquitecto** | director + subagente `Plan` | Puertos, módulos, raíz de composición (ticket de arquitectura) | `codebase-design`, `software-architecture`, `python-design-patterns`, `clean-code-principles`, `design-patterns-implementation`, `engineering:system-design` |
| **Diseño de producto / API** | subagente `general-purpose` | Contrato HTTP, modelo de errores, experiencia del desarrollador que reutiliza la librería | `api-design-principles`, `product-management:write-spec` |
| **Ejecutor** (uno por módulo) | subagente `general-purpose` | Implementar su módulo con pruebas primero | `tdd`, `python-design-patterns`, `claude-api` (solo adaptador Claude), `diagnosing-bugs` |
| **Pruebas / QA** | subagente `general-purpose` | Generador de PDFs sintéticos (nativo y escaneado), pruebas doradas, de contrato y de carga | `tdd-guide`, `engineering:testing-strategy`, `anthropic-skills:pdf` |
| **Revisor de calidad** | subagente `code-quality-auditor` | SOLID, simplicidad, legibilidad, duplicación | `clean-code-principles`, `simplify`, `code-review` |
| **Seguridad** | subagente `general-purpose` | Vulnerabilidades (PDF malicioso, SSRF, path traversal, secretos), datos personales (Ley 1581) | `code-security-audit`, `security-review`, `security-compliance` |
| **DevOps** | subagente `general-purpose` | Dockerfile, compose (API + worker + cola), checklist de despliegue | `docker-compose-orchestration`, `engineering:deploy-checklist` |
| **Documentación** | subagente `general-purpose` | README de uso como librería y como servicio | `engineering:documentation` |
| **Costo / latencia LLM** | subagente `general-purpose` | Optimizar llamadas a Claude cuando haya llaves y muestras | `claude-api` (`cost-optimize`, `latency-optimize`) |

## Fases y compuertas

1. **F0 Preparación**: investigación de motores locales y de Claude vs OpenAI (en paralelo, AFK) + decisiones HITL de alcance, infraestructura, criterios y contrato.
2. **F1 Arquitectura**: el director fija el dominio, los puertos y el cableado. *Compuerta*: revisión del revisor de calidad.
3. **F2 Construcción paralela** (ejecutores en archivos disjuntos): núcleo y pipeline · adaptadores PDF/OCR · adaptadores LLM · servicio API + worker · DevOps · QA. *Compuerta*: `uv run pytest` en verde y `ruff` limpio.
4. **F3 Revisión cruzada**: calidad, seguridad, producto/API y pruebas, en paralelo. El director prioriza los hallazgos y los ejecutores corrigen.
5. **F4 Benchmark con muestras reales** (cuando llegue el ticket de muestras): ajustar la plantilla, medir y decidir el motor por defecto.

## Protocolo de preguntas

Toda decisión de implementación que el usuario no haya mencionado y que no tenga un valor por defecto convencional se le **pregunta** (vía el director) antes de construirla. Los subagentes no preguntan directamente: reportan la duda al director en su respuesta final.
