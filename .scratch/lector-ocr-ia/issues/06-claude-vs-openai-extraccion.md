# Claude API vs OpenAI API para extracción estructurada

Type: research
Status: resolved

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

## Answer

Hallazgos completos en [research/06-claude-vs-openai.md](../research/06-claude-vs-openai.md).

- Los dos SDK de Python son simétricos (cliente asíncrono, `parse()` con Pydantic, reintentos), así que basta con un puerto `FieldExtractor` y un adaptador por proveedor.
- Modelos candidatos para el fallback: `claude-haiku-5-5` / `gpt-6-luna` ($0,10 / $0,50 por MTok); para escalar, `claude-sonnet-5-5` / `gpt-6.1-terra`. Costo estimado con el modelo pequeño: $0,0003–$0,001 por documento.
- Las API por lotes y de archivos **no** son ZDR en ninguno de los dos. Para ZDR, el fallback va síncrono con el PDF en base64.
- No hay región en Latinoamérica: enviar datos es una transferencia internacional (Ley 1581), que debe validar el área legal.
- Regla del adaptador: revisar `stop_reason` / refusal **antes** de leer el contenido; un rechazo o truncamiento cuenta como campo sin resolver.
- Pendiente del usuario: modelo por defecto, sync vs lotes, qué se envía (texto o página) y presupuesto. Se decide en [Motor por defecto y reglas de escalamiento](14-motor-por-defecto-y-escalamiento.md).
