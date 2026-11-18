# Política de uso de IA externa

Type: grilling
Status: resolved

## Question

Los PDFs contienen datos personales (identificación, comparendos). ¿Se permite enviar su contenido a APIs externas de IA (Anthropic / OpenAI)?

## Answer

Decidido por el usuario durante el trazado del mapa (2026-11-18): **se define antes de cada ejecución**, mediante un parámetro o decisión que puede permitirlo o prohibirlo.

Implicaciones (derivadas, revisables):

- El servicio no asume ninguna de las dos opciones. Un componente de política se consulta antes de elegir motores; si la IA externa está prohibida, solo se usan motores locales.
- La granularidad del parámetro (por solicitud, por lote, por cliente o global) y su valor por defecto se deciden en [Contrato de la API y origen de los PDFs](10-contrato-api-y-origen-pdf.md).
