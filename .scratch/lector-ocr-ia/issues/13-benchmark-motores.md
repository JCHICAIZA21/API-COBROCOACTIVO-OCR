# Benchmark de motores sobre las muestras

Type: task
Status: open
Blocked by: 04, 05, 06, 09, 12

## Question

(AFK: lo ejecuta el agente.) Medir, sobre las muestras con valores esperados, cada combinación candidata: texto + reglas, OCR local (candidatos de 05), Claude (candidato de 06) y OpenAI (candidato de 06).

- Exactitud por campo.
- Latencia p50/p95 por documento.
- Costo por documento, extrapolado a 50.000/día.
- Respetar la Política: los motores externos solo procesan las muestras autorizadas en 04.

Entregable: tabla de resultados frente a los [Criterios de aceptación del MVP](09-criterios-aceptacion-mvp.md), con los scripts enlazados.
