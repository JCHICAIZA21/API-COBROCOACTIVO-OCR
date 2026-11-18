# Definición declarativa de campos por tipo de documento

Type: prototype
Status: open
Blocked by: 04, 05, 06

## Question

¿Cómo se declaran los campos de cada tipo de PDF (la Plantilla de documento) para que **una sola definición** alimente todos los motores?

- Para reglas: regex, anclas de texto, zonas/coordenadas.
- Para LLM: JSON Schema de salida y descripción de cada campo.
- Normalizadores y validadores por campo (fecha, identificación, valores monetarios, nro_comparendo).

Prototipar la plantilla del PDF de muestra en dos formatos (YAML vs. modelos Pydantic) y probarla a mano contra 2–3 muestras.
