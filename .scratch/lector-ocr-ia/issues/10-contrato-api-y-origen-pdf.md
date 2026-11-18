# Contrato de la API y origen de los PDFs

Type: grilling
Status: resolved
Blocked by: 07, 08

## Question

¿Cuál es el contrato público del servicio?

- Endpoints: lectura individual síncrona y/o trabajos por lote asíncronos (crear trabajo, consultar estado, webhook o evento al terminar).
- Solicitud: datos de entrada + referencia al PDF (subida multipart, URL, ruta en almacenamiento compartido, id en BD).
- Respuesta: por campo valor, confianza, motor usado, evidencia y veredicto de validación (según [Alcance del eslabón: extraer, validar y persistir](07-alcance-del-eslabon.md)).
- **Política de IA externa** ([Política de uso de IA externa](01-politica-ia-externa.md)): granularidad (por solicitud, por lote, por cliente, global) y valor por defecto.
- Idempotencia, autenticación y versionado del API.

## Answer

Decidido por el usuario (2026-11-18):

- **Política de IA externa**: se define por solicitud o por lote (`allow_external_ai`) y **por defecto está prohibida**. Un tope global de configuración (`DOCREADER_EXTERNAL_AI_ENABLED`, por defecto `false`) puede prohibirla siempre.
- **Autenticación**: header `X-API-Key`, con llaves por cliente en configuración.
- **Origen del PDF**: subida multipart, ruta bajo un directorio base permitido, URL de dominios en lista blanca, y BLOB en PostgreSQL con consulta configurable.
- **LLM local (Ollama)**: adaptador opcional, desactivado por defecto.

Diseño por defecto del director (lo revisa el rol de diseño de API):

- `POST /v1/read` (JSON con referencia) y `POST /v1/read/upload` (multipart) son síncronos y devuelven el resultado.
- `POST /v1/jobs` (JSON) y `POST /v1/jobs/upload` (multipart + manifiesto) son asíncronos, para lotes.
- `GET /v1/jobs/{id}` (estado) y `GET /v1/jobs/{id}/results` (resultados paginados). Al terminar un lote se consulta por polling; el webhook queda para después.
- `GET /v1/templates`, `GET /health`. Versionado en la ruta (`/v1`).
- Idempotencia: caché de resultados por `sha256(PDF) + versión de plantilla + política`.
