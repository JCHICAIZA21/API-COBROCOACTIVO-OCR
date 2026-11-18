# Plataforma de despliegue e infraestructura

Type: grilling
Status: resolved

## Question

¿Dónde y cómo correrá el servicio?

- Nube u on-premise (Azure, AWS, GCP, servidores propios Windows/Linux).
- Contenedores: Docker y/o Kubernetes.
- Disponibilidad de GPU para OCR.
- Broker de cola para lotes (Redis, RabbitMQ, SQS, Azure Service Bus…).
- Motor de base de datos de la organización (SQL Server, PostgreSQL, Oracle…).
- Dónde viven hoy los PDFs (carpeta compartida, Blob/S3, base de datos, sistema externo).

## Answer

Decidido por el usuario (2026-11-18):

- **Docker Linux + compose**: contenedores `api` + `worker` + **Redis** (cola de trabajos). En Windows se desarrolla con Docker Desktop.
- **PostgreSQL** como base de datos (estado de trabajos por lote y origen de PDFs tipo BLOB).
- GPU: no es requisito; el OCR debe rendir en CPU. Ollama (LLM local opcional) puede aprovechar GPU donde exista.
- Origen de los PDFs: **los cuatro**. Subida al API, ruta en carpeta compartida, URL HTTP y BLOB en base de datos. Detalle en [Contrato de la API y origen de los PDFs](10-contrato-api-y-origen-pdf.md).
