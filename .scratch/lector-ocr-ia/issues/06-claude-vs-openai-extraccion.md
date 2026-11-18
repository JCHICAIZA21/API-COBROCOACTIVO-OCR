# Claude API vs OpenAI API para extracción estructurada

Type: research
Status: open

## Question

¿Qué ofrecen la API de Claude (Anthropic) y la de OpenAI para extraer campos estructurados de PDFs a más de 50.000 documentos/día?

Por proveedor:

- Entrada nativa de PDF: límites de páginas y tamaño; si procesa texto, imagen o ambos; carga reutilizable de archivos.
- Salidas estructuradas con JSON Schema: garantías de validez.
- API por lotes: descuento y ventana de entrega.
- Límites de tasa alcanzables para el volumen objetivo.
- Retención de datos, opciones de cero retención y región de inferencia (relevante para datos personales).
- SDK de Python asíncrono, reintentos y manejo de rechazos o errores.
- **Costo estimado por documento** (1–3 páginas) por modelo: Claude Haiku 5.5, Sonnet 5.5 y Opus 5.5, y sus equivalentes de OpenAI. Extrapolado a 50.000/día, en modo normal y por lotes.

Entregable: tabla comparativa, costo diario estimado y un modelo candidato por proveedor para el [Benchmark de motores sobre las muestras](13-benchmark-motores.md).
