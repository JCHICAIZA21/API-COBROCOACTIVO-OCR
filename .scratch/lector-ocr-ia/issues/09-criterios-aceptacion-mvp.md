# Criterios de aceptación del MVP

Type: grilling
Status: open

## Question

¿Qué debe cumplir el MVP para darse por terminado?

- Precisión mínima por campo (ej.: exactitud en nro_comparendo e identificación).
- Throughput sostenido y de pico (50.000/día ≈ 35 docs/min de promedio; ¿cuál es el pico?).
- Latencia máxima por documento en modo individual.
- Costo máximo por documento o por día.
- Qué pasa con los documentos que no alcanzan la confianza mínima (estado "requiere revisión", revisión humana, reintento con otro motor).
