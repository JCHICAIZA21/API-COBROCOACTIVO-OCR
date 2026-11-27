# Mapa: Eslabón de lectura OCR/IA

Type: map

## Destination

Microservicio en Python (eslabón de lectura OCR/IA) **especificado y con un MVP funcionando**: recibe datos de entrada (identificación, nro_comparendo, fecha…) de forma individual o masiva junto con la referencia a un PDF, extrae los campos del PDF con motores intercambiables (texto nativo, OCR local, Claude, OpenAI) según una **Política de IA externa** definida antes de cada ejecución, y entrega resultados estructurados listos para el siguiente eslabón. Validado sobre los PDFs de muestra contra los criterios de aceptación.

## Notes

- **Dominio**: cobro coactivo / comparendos. Cadena completa: entrada de datos (manual o masiva) → **lectura OCR/IA (este mapa)** → cruce con otras fuentes → reporte HTML/PDF con plantilla.
- **Excepción a "Plan, don't do"**: el destino incluye el MVP. Cuando el camino esté claro, los tickets de construcción (`task`) ejecutan el MVP dentro de este mapa.
- **Preferencias permanentes**: Python; principios SOLID; código limpio, estructurado y minimalista, fácil de entender y de reutilizar en otros proyectos; funcionamiento avanzado y ultrarrápido; motores intercambiables sin tocar el núcleo.
- **Skills, roles y delegación**: ver [plan-ejecucion.md](plan-ejecucion.md). Tiene el mapa de skills por rol, las fases, las compuertas y el entorno verificado.
- **Lenguaje del dominio (provisional)**: *Solicitud de lectura* (datos de entrada + referencia al PDF) · *Plantilla de documento* (campos esperados de un tipo de PDF) · *Campo* · *Motor de extracción* (texto nativo, OCR, LLM) · *Política de IA externa* (permite o prohíbe motores en la nube) · *Resultado de lectura* (valor, confianza, motor, evidencia por campo).
- **Tracker**: markdown local. Un archivo por ticket en `issues/`, con líneas `Type:`, `Status:` (`open`/`claimed`/`resolved`) y `Blocked by:`. Frontera = abiertos, sin bloqueo y sin reclamar; gana el número menor.

## Decisions so far

- [Política de uso de IA externa](issues/01-politica-ia-externa.md): se decide por parámetro antes de cada ejecución; el mismo servicio opera con o sin IA externa.
- [Volumen objetivo de procesamiento](issues/02-volumen-objetivo.md): más de 50.000 PDFs/día; procesamiento asíncrono, escalable y con el costo por documento como restricción.
- [Tipo de PDFs a soportar](issues/03-tipo-de-pdfs.md): mixtos (nativos y escaneados); se detecta la capa de texto por página.
- [Alcance del eslabón: extraer, validar y persistir](issues/07-alcance-del-eslabon.md): extrae y valida contra la entrada con evidencia; no escribe en las tablas de negocio.
- [Plataforma de despliegue e infraestructura](issues/08-plataforma-e-infraestructura.md): Docker Linux + compose (api, worker, Redis), PostgreSQL; PDFs desde 4 orígenes.
- [Criterios de aceptación del MVP](issues/09-criterios-aceptacion-mvp.md): ≥ 99,5 % de exactitud en campos clave; p95 ≤ 1 s nativo y ≤ 5 s escaneado; lo dudoso queda en `needs_review`.
- [Contrato de la API y origen de los PDFs](issues/10-contrato-api-y-origen-pdf.md): IA externa por solicitud con default prohibido, `X-API-Key`, lectura síncrona + lotes asíncronos, Ollama opcional.
- [Claude API vs OpenAI API para extracción estructurada](issues/06-claude-vs-openai-extraccion.md): SDK simétricos; candidatos Haiku 5.5 / gpt-6-luna; lotes y archivos no son ZDR; envío al exterior sujeto a Ley 1581.
- [Motores locales de texto y OCR para PDFs mixtos](issues/05-motores-locales-texto-y-ocr.md): pypdfium2 para texto; RapidOCR (PP-OCRv5 latin) por defecto y Tesseract como alternativa; modelos incluidos en la imagen.
- [Operación: reintentos, retención y modo de llamada LLM](issues/15-operacion-reintentos-retencion-llm.md): 3 intentos; retención configurable con 365 días por defecto; LLM solo síncrono (Opus 5.5 / gpt-6.2-sol); diseño para CPU.
- [Esqueleto de arquitectura hexagonal del servicio](issues/11-esqueleto-arquitectura.md): núcleo reutilizable + servicio, puertos y adaptadores, cascada de lo barato a lo caro; construido y medido.

## Not yet specified

- **Partición del MVP en incrementos verticales**: se puede especificar cuando estén resueltos el contrato, la arquitectura y el motor por defecto.
- **Empaquetado reutilizable**: núcleo como librería instalable (pip) separada del servicio, o un único paquete. Depende de la arquitectura.
- **Seguridad y retención**: autenticación del API, cifrado, tiempo de retención y borrado de PDFs y resultados con datos personales.
- **Resiliencia**: reintentos, cola de mensajes fallidos (DLQ), idempotencia, manejo de límites de tasa de los proveedores de IA.
- **Observabilidad**: métricas por motor (latencia, costo, confianza), trazas por solicitud, tablero de lotes.
- **Estrategia de pruebas**: PDFs dorados, pruebas de contrato, prueba de carga al volumen objetivo.
- **Gestión de costos**: presupuesto diario, uso de APIs por lotes para cargas no urgentes.
- **Versionado de plantillas** cuando cambie el formato de un PDF de origen.

## Out of scope

- **Cruce de los datos extraídos con otras fuentes**: es el siguiente eslabón de la cadena.
- **Generación del reporte o PDF resultante con plantilla**: es un eslabón posterior.
- **Interfaz de captura manual o de carga masiva de datos de entrada (UI)**: este eslabón recibe los datos por API.
