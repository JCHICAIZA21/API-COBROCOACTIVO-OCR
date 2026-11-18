# Criterios de aceptación del MVP

Type: grilling
Status: resolved

## Question

¿Qué debe cumplir el MVP para darse por terminado?

- Precisión mínima por campo (ej.: exactitud en nro_comparendo e identificación).
- Throughput sostenido y de pico (50.000/día ≈ 35 docs/min de promedio; ¿cuál es el pico?).
- Latencia máxima por documento en modo individual.
- Costo máximo por documento o por día.
- Qué pasa con los documentos que no alcanzan la confianza mínima (estado "requiere revisión", revisión humana, reintento con otro motor).

## Answer

Decidido por el usuario (2026-11-18): **criterios estrictos**.

- Exactitud **≥ 99,5 %** en campos clave, medida sobre las muestras con valores esperados.
- Latencia **p95 ≤ 1 s** por PDF nativo y **≤ 5 s** por PDF escaneado, en modo individual.
- Throughput: al menos el volumen objetivo (≥ 35 docs/min sostenidos por nodo con motores locales).
- Lo que no alcance la confianza mínima queda en estado `needs_review`.
- Costo máximo: no fijado. Se revisa con el benchmark en [Motor por defecto y reglas de escalamiento](14-motor-por-defecto-y-escalamiento.md).
