# Contrato de la API y origen de los PDFs

Type: grilling
Status: open
Blocked by: 07, 08

## Question

¿Cuál es el contrato público del servicio?

- Endpoints: lectura individual síncrona y/o trabajos por lote asíncronos (crear trabajo, consultar estado, webhook o evento al terminar).
- Solicitud: datos de entrada + referencia al PDF (subida multipart, URL, ruta en almacenamiento compartido, id en BD).
- Respuesta: por campo valor, confianza, motor usado, evidencia y veredicto de validación (según [Alcance del eslabón: extraer, validar y persistir](07-alcance-del-eslabon.md)).
- **Política de IA externa** ([Política de uso de IA externa](01-politica-ia-externa.md)): granularidad (por solicitud, por lote, por cliente, global) y valor por defecto.
- Idempotencia, autenticación y versionado del API.
