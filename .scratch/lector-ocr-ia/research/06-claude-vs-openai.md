# Claude API vs OpenAI API para extracción estructurada

Responde a [06 · Claude API vs OpenAI API](../issues/06-claude-vs-openai-extraccion.md). Todas las fuentes se leyeron el **2026-11-18** (ver [Fuentes](#fuentes)). Precios en USD.

## Resumen

- **Modelo candidato por proveedor (fallback de campos):** `claude-haiku-5-5` y `gpt-6-luna`. Tienen el mismo precio ($0,10 / $0,50 por MTok) y los dos aceptan imagen, PDF y JSON Schema estricto. Para escalar los campos que el modelo pequeño no resuelva: `claude-sonnet-5-5` y `gpt-6.1-terra` (o `gpt-6.2-sol` si se quiere el mismo precio que Sonnet). Opus 5.5 y GPT-6.2 Astra no se justifican para extraer 4–10 campos.
- **Costo:** con el modelo pequeño, el LLM cuesta entre **$0,0003 y $0,001 por documento** en modo normal (la mitad por lotes). A 50.000 documentos/día eso es **$15–$50/día** si el LLM se llama para el 100 %, y **$0,75–$2,50/día** si solo se llama para el 5 %. Con Sonnet o Sol el costo sube unas 20 veces.
- **Datos personales:** ninguno de los dos ofrece región en Colombia ni en Latinoamérica. Las API por lotes y de archivos **no son elegibles para cero retención (ZDR)** en ninguno de los dos. Si la política exige ZDR, el fallback tiene que ir por la API síncrona, con el PDF en base64 dentro de la solicitud.
- **SDK:** los dos SDK de Python son casi simétricos (cliente asíncrono, `parse()` con Pydantic, 2 reintentos automáticos, la misma jerarquía de excepciones), así que un solo puerto `LlmExtractor` con dos adaptadores es sencillo.

## 1. Modelos comparados (por nivel de precio)

| Nivel | Claude | $/MTok entrada / salida (lotes) | OpenAI | $/MTok entrada / salida (lotes) |
|---|---|---|---|---|
| Pequeño | `claude-haiku-5-5` | 0,10 / 0,50 (0,05 / 0,25)¹ | `gpt-6-luna` | 0,10 / 0,50 (0,05 / 0,25) |
| Intermedio | — | — | `gpt-6.1-terra` | 1 / 5 (0,50 / 2,50) |
| Medio | `claude-sonnet-5-5` | 2 / 10 (1 / 5) | `gpt-6.2-sol` | 2 / 10 (1 / 5) |
| Superior | `claude-opus-5-5` | 4 / 20 (2 / 10) | `gpt-6.2-astra` | 8 / 40 (4 / 20) |

¹ Haiku 5.5 tiene una segunda tarifa para prompts de más de 100.000 tokens ($0,50 / $2,50). No aplica a documentos de 1–3 páginas.

- OpenAI describe Luna como "our most efficient model for focused, high-volume tasks" y Terra como un modelo "for visual reasoning".
- Los cuatro modelos de OpenAI tienen 1,05 M tokens de contexto y 128K de salida. Los de Claude tienen 1M y 128K.

## 2. Entrada de PDF e imágenes

| Aspecto | Claude | OpenAI |
|---|---|---|
| PDF nativo | Bloque `document` (base64, URL o `file_id`). Cada página se convierte en imagen **y además** se extrae su texto; el modelo recibe los dos. | `input_file` (base64 `file_data`, `file_id` o `file_url`; este último solo en Responses). En modelos con visión se extraen texto **e** imagen de cada página. |
| Límites de PDF | 128 MB por solicitud; 600 páginas (100 si el contexto es menor de 1M). Sin contraseña. | 50 MB por archivo y 50 MB en total por solicitud. No publica límite de páginas. |
| Tokens por página de PDF | Texto: "typically 1,500–3,000 tokens". Imagen: como máximo **1.568** tokens visuales en cualquier modelo (siempre se usa el nivel Standard). | No publica una fórmula. El parámetro `detail` (`low`/`high`/`auto`, solo en Responses) afecta solo a la imagen; el texto extraído siempre se incluye. En GPT-5.6 y posteriores, `auto` equivale a `high`. |
| Imagen suelta (página escaneada) | Parches de 28×28 px: `⌈w/28⌉·⌈h/28⌉` tokens. Los modelos 4.7 y posteriores llegan hasta 2.576 px y 4.784 tokens. | Parches de 32×32 px × multiplicador del modelo (1,2 en gpt-6-astra y gpt-5.6-*). `detail=high` tiene un tope de 2.500 parches; `low` usa 512×512. |
| Límites de imagen | 600 por solicitud, 10 MB cada una (base64), 8000×8000 px (máximo 3.000 px si hay más de 20 imágenes). JPEG, PNG, GIF, WebP. | 1.500 por solicitud, 512 MB en total y 30.000 parches por imagen. PNG, JPEG, WEBP, GIF. |
| Carga reutilizable | Files API (GA, `client.files.upload`): 500 MB por archivo, 100 GB por organización, se conservan hasta que se borren. **No ZDR.** | Files API (`client.files.create(purpose="user_data")`): se conservan hasta que se borren o hasta `expires_after`. **No ZDR.** |

Dos implicaciones:

- **PDF escaneado en Claude:** como no tiene capa de texto, cuesta unos 1.568 tokens por página. Es más barato que enviar la página como imagen a 150 ppp (2.714 tokens), pero la resolución queda limitada a ~1.568 px. Hay que medir en el benchmark si la letra pequeña sigue siendo legible.
- **PDF nativo en OpenAI:** con `detail="low"` la imagen de cada página cuesta unos 308 tokens (256 parches × 1,2) y el texto extraído se incluye igual.

## 3. Salida estructurada y SDK de Python

| Aspecto | Claude (`anthropic`) | OpenAI (`openai`) |
|---|---|---|
| Cliente asíncrono | `anthropic.AsyncAnthropic()`. Para alta concurrencia, `anthropic[aiohttp]` + `DefaultAioHttpClient()`. | `openai.AsyncOpenAI()`. Para alta concurrencia, `openai[aiohttp]` + `DefaultAioHttpClient()`. |
| Pydantic | `client.messages.parse(model=…, max_tokens=…, messages=…, output_format=MiModelo)` → `response.parsed_output` | `client.responses.parse(model=…, input=…, text_format=MiModelo)` → `response.output_parsed`; o `client.chat.completions.parse(…, response_format=MiModelo)` → `choices[0].message.parsed` |
| Esquema JSON crudo | `messages.create(…, output_config={"format": {"type": "json_schema", "schema": {…}}})` (el parámetro `output_format` de `create` quedó obsoleto) | Responses: `text.format` con `json_schema` y `strict` |
| Garantía | Decodificación restringida: "Always valid… Type safe… No retries needed for schema violations". Excepciones: rechazo, `max_tokens` y mayúsculas en los `enum`. | "Constrains completed, non-refusal model output to your supplied JSON Schema". Excepciones: rechazo y respuesta incompleta. |
| Restricciones de esquema | `additionalProperties: false`. Máximo 24 parámetros opcionales y 16 con unión. No admite `minLength`/`maxLength`, `minimum`/`maximum` ni `oneOf` (el SDK los mueve a la descripción del campo). | **Todos** los campos deben ser `required` (un campo opcional se expresa como `["string","null"]`); `additionalProperties: false`. Máximo 5.000 propiedades y 10 niveles de anidación. Sí admite `minimum`/`maximum` y `pattern`. |
| Primera llamada | Se compila una gramática (latencia extra) y queda en caché 24 h. | Latencia extra con un esquema nuevo. |
| Razonamiento | Haiku 5.5: adaptativo por defecto con esfuerzo `medium`; acepta `thinking={"type":"disabled"}` con esfuerzo ≤ `high`. Sonnet 5.5: para apagarlo se usa `between_tools`. Opus 5.5 **no** permite apagarlo; se reduce con `effort="low"`. El razonamiento se factura como salida. | Luna y Terra aceptan `reasoning.effort="none"`; Sol y Astra **rechazan** `none` (el mínimo es `low`). El razonamiento se factura como salida. |
| Otros | Incompatible con citations y con prefill. `inference_geo` es un parámetro de primer nivel. | `store=False` en Responses para no guardar estado (por defecto se guarda 30 días). |

## 4. Lotes y límites de tasa para más de 50.000 documentos/día

Carga de referencia: 50.000 documentos/día son ~35 por minuto en 24 h, o ~104 por minuto si se procesan en una ventana de 8 h. En el escenario (b) eso supone ~0,7–0,9 M tokens de entrada por minuto.

| Aspecto | Claude | OpenAI |
|---|---|---|
| Descuento por lotes | 50 % en entrada y salida | 50 % |
| Entrega | La mayoría en menos de 1 h; un lote vence a las 24 h y las solicitudes vencidas no se cobran. Resultados disponibles 29 días. | Ventana fija de `24h`; las solicitudes vencidas van al archivo de errores. Archivos de salida disponibles 30 días (configurable). |
| Tamaño de lote | 100.000 solicitudes **o 256 MB** | 50.000 solicitudes **o 200 MB** por archivo JSONL; hasta 2.000 lotes por hora |
| Cola de lotes | Start 200.000, Build 300.000, Scale 500.000 solicitudes. Los lotes no tienen límite de rampa. | Límite de tokens de entrada en cola por modelo: Luna 20 M (Build), 1.000 M (Launch), 15.000 M (Grow) |
| Límite de tasa síncrono (por modelo) | Haiku, Sonnet y Opus 5.5 — Start: 4.000 RPM, 2 M ITPM, 400K OTPM; Build: 10.000 RPM, 5 M ITPM; Scale: 13.000 RPM, 10 M ITPM. Los tokens leídos de caché no cuentan. | Luna — Build: 5.000 RPM y 2 M TPM; Launch: 10.000 RPM y 10 M TPM. Sol, Terra y Astra — Build: 1 M TPM; Launch: 4 M TPM. |
| Tope de gasto mensual | Start $500, Build $1.000, Scale $200.000 | Build $500, Launch $5.000, Grow $200.000 |
| Particularidades | Hay un límite de rampa (429 `ramp_limit_exceeded`) cuando el uso sube de golpe. `max_tokens` no consume OTPM. | El TPM se calcula como el **máximo** entre `max_tokens` y la estimación de la solicitud, así que `max_output_tokens` debe ajustarse al tamaño real de la respuesta. Ramp-up: no más de +50 % cada 15 minutos. |

Conclusiones:

- Los límites de tasa alcanzan sin problema con el nivel de entrada de cada proveedor.
- **Lo que manda es el tope de gasto.** Por ejemplo, Haiku al 100 % en el escenario (b) cuesta unos $1.220/mes: no cabe en Start ni en Build y exige el nivel Scale. Con el 20 % son unos $245/mes y basta con Start.
- **Los lotes con binarios llenan los bytes antes que el número de solicitudes.** Un PDF de ~300 KB ocupa ~400 KB en base64, así que caben unos 600 documentos por lote. Para meter más hay que referenciar con `file_id`, y entonces se sale de ZDR.

## 5. Retención, ZDR y región

| Aspecto | Claude | OpenAI |
|---|---|---|
| Retención por defecto (API) | Entradas y salidas se borran "within 30 days of receipt or generation" (artículo de privacidad, actualizado el 2026-07-01). La documentación técnica dice además que el contenido de la conversación "is not retained by default", salvo los Covered Models. | Registros de monitoreo de abuso: hasta 30 días. Responses guarda el estado de la aplicación 30 días salvo que se envíe `store=False`. |
| Entrenamiento | No se entrena con los datos sin permiso expreso. | No se entrena con los datos por defecto. |
| ZDR | Se solicita a ventas y se activa por organización. Son elegibles Messages, PDF, salida estructurada (calificada: el esquema se guarda 24 h), caché de prompts e `inference_geo`. **No** son elegibles Batches (29 días) ni Files. El contenido marcado por trust & safety puede guardarse hasta 2 años. | ZDR o Modified Abuse Monitoring, sujetos a aprobación previa de ventas. Son elegibles Responses y Chat Completions. **No** lo son Batches ni Files. |
| Región de inferencia | `inference_geo`: `"global"` (por defecto, puede correr en cualquier geografía) o `"us"` (**×1,1** en todos los tokens). El almacenamiento en reposo solo puede ser `"us"`. | Proyectos con residencia de datos: procesamiento en EE. UU., UE, Australia, Japón, India y EAU; Canadá, Singapur, Corea y Reino Unido solo almacenamiento. **+10 %** de recargo. Lotes con residencia solo en EE. UU. y UE. |
| Latinoamérica | No hay | No hay |

Para la Ley 1581, cualquiera de los dos supone una **transferencia internacional** de datos personales (a EE. UU. o a la UE). Esto debe validarlo el área legal.

## 6. Costo por documento y por día

**Supuestos** (las cifras de tokens se aplican igual a los dos proveedores, aunque sus tokenizadores cuentan distinto; las cifras reales se medirán con `usage` en el benchmark [13](../issues/13-benchmark-motores.md)):

- **Salida:** 300 tokens por documento, sin razonamiento. Si un modelo razona, cada 1.000 tokens de razonamiento suman $0,0005 por documento en Haiku o Luna, $0,005 en Terra, $0,01 en Sonnet o Sol, $0,02 en Opus y $0,04 en Astra.
- **(a) Solo texto:** 1.500 tokens de entrada en total.
- **(b) PDF nativo de 2 páginas:** 500 tokens de instrucciones y esquema más, por cada página, 1.500 tokens de texto (el límite inferior que da Anthropic) y la imagen.
  - Claude: la imagen cuesta 1.568 tokens, así que la entrada suma **6.636** tokens.
  - OpenAI: la imagen cuesta unos 2.496 tokens. Es un supuesto, porque OpenAI no publica la resolución con que rasteriza; se toma una página Carta a 150 ppp con `detail=high` (`auto`). La entrada suma **8.492** tokens.
  - Si el texto llega al límite superior (3.000 tokens por página), Claude sube a 9.636 tokens de entrada.
- **(c) Dos imágenes de página** Carta a 150 ppp (1275×1650 px) más 500 tokens:
  - Claude: 46×59 = 2.714 tokens por página, **5.928** en total.
  - OpenAI: 40×52 parches × 1,2 = 2.496 tokens por página, **5.492** en total. El multiplicador de 1,2 está supuesto para gpt-6-luna, gpt-6.1-terra y gpt-6.2-sol, que la tabla de visión todavía no lista.
- **Sin caché ni región:** no se aplica caché de prompts (ahorraría algo en las instrucciones repetidas) ni los recargos de región (×1,1).

**Costo diario.** Formato: modo normal / lotes. Hay 50.000 documentos/día, de modo que el LLM procesa 50.000 (100 %), 10.000 (20 %) o 2.500 (5 %).

### (a) Solo texto — 1.500 de entrada + 300 de salida

| Modelo | $/doc normal | $/doc lotes | 100 % | 20 % | 5 % |
|---|---|---|---|---|---|
| claude-haiku-5-5 | 0,00030 | 0,00015 | $15,00 / $7,50 | $3,00 / $1,50 | $0,75 / $0,38 |
| gpt-6-luna | 0,00030 | 0,00015 | $15,00 / $7,50 | $3,00 / $1,50 | $0,75 / $0,38 |
| gpt-6.1-terra | 0,00300 | 0,00150 | $150 / $75 | $30 / $15 | $7,50 / $3,75 |
| claude-sonnet-5-5 | 0,00600 | 0,00300 | $300 / $150 | $60 / $30 | $15 / $7,50 |
| gpt-6.2-sol | 0,00600 | 0,00300 | $300 / $150 | $60 / $30 | $15 / $7,50 |
| claude-opus-5-5 | 0,01200 | 0,00600 | $600 / $300 | $120 / $60 | $30 / $15 |
| gpt-6.2-astra | 0,02400 | 0,01200 | $1.200 / $600 | $240 / $120 | $60 / $30 |

### (b) PDF nativo de 2 páginas

| Modelo | $/doc normal | $/doc lotes | 100 % | 20 % | 5 % |
|---|---|---|---|---|---|
| claude-haiku-5-5 | 0,00081 | 0,00041 | $40,68 / $20,34 | $8,14 / $4,07 | $2,03 / $1,02 |
| gpt-6-luna | 0,00100 | 0,00050 | $49,96 / $24,98 | $9,99 / $5,00 | $2,50 / $1,25 |
| gpt-6.1-terra | 0,00999 | 0,00500 | $500 / $250 | $100 / $50 | $25 / $12,49 |
| claude-sonnet-5-5 | 0,01627 | 0,00814 | $814 / $407 | $163 / $81 | $40,68 / $20,34 |
| gpt-6.2-sol | 0,01998 | 0,00999 | $999 / $500 | $200 / $100 | $49,96 / $24,98 |
| claude-opus-5-5 | 0,03254 | 0,01627 | $1.627 / $814 | $325 / $163 | $81 / $40,68 |
| gpt-6.2-astra | 0,07994 | 0,03997 | $3.997 / $1.998 | $799 / $400 | $200 / $100 |

### (c) Dos imágenes de página

| Modelo | $/doc normal | $/doc lotes | 100 % | 20 % | 5 % |
|---|---|---|---|---|---|
| gpt-6-luna | 0,00070 | 0,00035 | $34,96 / $17,48 | $6,99 / $3,50 | $1,75 / $0,87 |
| claude-haiku-5-5 | 0,00074 | 0,00037 | $37,14 / $18,57 | $7,43 / $3,71 | $1,86 / $0,93 |
| gpt-6.1-terra | 0,00699 | 0,00350 | $350 / $175 | $70 / $35 | $17,48 / $8,74 |
| gpt-6.2-sol | 0,01398 | 0,00699 | $699 / $350 | $140 / $70 | $34,96 / $17,48 |
| claude-sonnet-5-5 | 0,01486 | 0,00743 | $743 / $371 | $149 / $74 | $37,14 / $18,57 |
| claude-opus-5-5 | 0,02971 | 0,01486 | $1.486 / $743 | $297 / $149 | $74 / $37,14 |
| gpt-6.2-astra | 0,05594 | 0,02797 | $2.797 / $1.398 | $559 / $280 | $140 / $70 |

## 7. Errores, reintentos y rechazos (SDK de Python)

| Aspecto | Claude | OpenAI |
|---|---|---|
| Reintentos automáticos | 2 por defecto, con backoff exponencial, en errores de conexión, 408, 409, 429 y 5xx (incluido el 529 por sobrecarga). Se configuran con `max_retries` o `with_options()`. | 2 por defecto, en errores de conexión, 408, 409, 429 y 5xx. `max_retries`; `0` los desactiva. |
| Tiempo de espera | 10 minutos por defecto; `timeout` en segundos o `anthropic.Timeout`. Lanza `APITimeoutError`. | 10 minutos por defecto; `timeout`. Lanza `APITimeoutError`. |
| Excepciones | `BadRequestError` (400), `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `RateLimitError` (429), `InternalServerError` (5xx), `APIStatusError` (base) y `APIConnectionError`. Expone `e.type` y `_request_id`. | La misma jerarquía de nombres, con base `openai.APIError`. Expone `_request_id`. |
| 429 que no conviene reintentar | Tope de gasto: `error.details.error_code == "enforced_spend_limit_reached"`, sin `retry-after`. | Errores de cuota o de facturación ("don't retry quota, billing… errors"). |
| Rechazo | HTTP 200 con `stop_reason == "refusal"` y `stop_details.category`. En Haiku 5.5 las categorías son cyber, bio, frontier_llm y general_harms. La salida puede no cumplir el esquema y los tokens se facturan. Haiku **no** tiene fallback del lado del servidor. Sonnet y Opus aceptan `fallbacks` (beta), pero no dentro de lotes. | Responses: un ítem de contenido de tipo `refusal` y `output_parsed` en `None`. Chat: `message.refusal` viene con contenido y `message.parsed` vacío. Aparte, si `finish_reason == "content_filter"`, `parse()` lanza `ContentFilterFinishReasonError`. |
| Respuesta truncada | `stop_reason == "max_tokens"`: el JSON puede quedar incompleto. | Responses: `status == "incomplete"` con `max_output_tokens`. Chat: `parse()` lanza `LengthFinishReasonError`. |

**Regla del adaptador:** revisar el motivo de parada **antes** de leer el contenido. Un rechazo o un truncamiento se trata como "campo sin resolver": se pasa al siguiente motor o a revisión manual, sin reintentar el mismo modelo.

## 8. Recomendación

| Proveedor | Por defecto | Escalamiento | Configuración inicial (a validar en el benchmark) |
|---|---|---|---|
| Claude | `claude-haiku-5-5` | `claude-sonnet-5-5` | `messages.parse` con Pydantic; `effort="low"` (o `thinking` desactivado); `max_tokens` ~1.024; en páginas escaneadas, comparar el PDF nativo (1.568 tokens por página) con imágenes a 150 ppp. |
| OpenAI | `gpt-6-luna` | `gpt-6.1-terra` | `responses.parse` con `text_format`; `reasoning={"effort": "none"}`; `store=False`; `max_output_tokens` bajo (consume TPM). |

## Dudas para decisión del usuario

1. **ZDR o lotes:** las API por lotes (50 % de descuento) y de archivos no son ZDR en ninguno de los dos. ¿Se acepta la retención estándar (hasta ~30 días) para los documentos que vayan por lotes, o el fallback será siempre síncrono?
2. **Región:** Claude solo ofrece `global` o `us`; OpenAI ofrece UE con +10 %. ¿Hay alguna preferencia legal (UE o EE. UU.) para la transferencia internacional según la Ley 1581?
3. **Qué se envía al LLM:** ¿solo texto (escenario a, el más barato) o también la página (b/c), que es necesaria para los escaneados y cuesta 2–3 veces más? Afecta directamente a la política de IA externa.
4. **Presupuesto y nivel de cuenta:** con fallback al 100 % hace falta el nivel Scale de Claude o Launch de OpenAI, por el tope de gasto. ¿Cuál es el presupuesto diario o mensual objetivo?
5. **Contrato:** confirmar con cada proveedor la retención por defecto. En Anthropic, el artículo de privacidad dice "within 30 days" y la documentación técnica dice "not retained by default"; conviene fijarlo en el DPA.

## Fuentes

Todas leídas el 2026-11-18.

**Anthropic**

- Skill `claude-api` (caché del 2026-11-03): modelos, métodos del SDK de Python, Batches, Files, errores y migración a Haiku, Sonnet y Opus 5.5.
- https://platform.claude.com/docs/en/build-with-claude/pdf-support — límites de PDF, procesamiento como texto + imagen, tokens por página.
- https://platform.claude.com/docs/en/build-with-claude/vision — fórmula de 28 px, niveles de resolución, límites de imagen.
- https://platform.claude.com/docs/en/about-claude/pricing — precios, lotes, tarifa larga de Haiku 5.5, multiplicador ×1,1 de `inference_geo`.
- https://platform.claude.com/docs/en/api/rate-limits — niveles, ITPM/OTPM, cola de lotes, topes de gasto, límite de rampa.
- https://platform.claude.com/docs/en/build-with-claude/batch-processing — 100.000 solicitudes o 256 MB, 24 h, 29 días.
- https://platform.claude.com/docs/en/build-with-claude/structured-outputs — garantía, límites de esquema, salidas inválidas, ZDR calificado.
- https://platform.claude.com/docs/en/manage-claude/api-and-data-retention — ZDR, elegibilidad por función, retención de 2 años por trust & safety.
- https://platform.claude.com/docs/en/manage-claude/data-residency — `inference_geo`, geografía del workspace.
- https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data — retención por defecto de 30 días (actualizado el 2026-07-01).

**OpenAI**

- https://developers.openai.com/api/docs/pricing (redirigido desde platform.openai.com/docs/pricing) — precios normal, por lotes, flex y fast; recargo del 10 % por región.
- https://developers.openai.com/api/docs/models — lista de modelos (Astra, Sol, Terra, Luna) y opciones de razonamiento.
- https://developers.openai.com/api/docs/models/gpt-6-luna y https://developers.openai.com/api/docs/models/gpt-6.2-sol — límites de tasa por nivel.
- https://developers.openai.com/api/docs/guides/pdf-files — entrada de PDF y `detail`.
- https://developers.openai.com/api/docs/guides/images-vision — parches de 32 px, multiplicadores, límites.
- https://developers.openai.com/api/docs/guides/structured-outputs — garantía, subconjunto de esquema, rechazos.
- https://developers.openai.com/api/docs/guides/batch — 50 %, 24 h, 50.000 solicitudes o 200 MB.
- https://developers.openai.com/api/docs/guides/rate-limits — niveles Build, Launch y Grow; topes de gasto; reintentos.
- https://developers.openai.com/api/docs/guides/reasoning — `reasoning.effort` y facturación del razonamiento.
- https://developers.openai.com/api/docs/guides/your-data — retención, ZDR y MAM, `store`.
- https://developers.openai.com/api/docs/guides/data-residency — regiones y endpoints.
- https://github.com/openai/openai-python (README y `helpers.md`) — cliente asíncrono, reintentos, excepciones, `parse()`.
