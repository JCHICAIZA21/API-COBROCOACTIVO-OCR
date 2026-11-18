# Volumen objetivo de procesamiento

Type: grilling
Status: resolved

## Question

¿Qué volumen y latencia se esperan para la lectura masiva?

## Answer

Decidido por el usuario durante el trazado del mapa (2026-11-18): **alto, más de 50.000 PDFs/día**.

Implicaciones (derivadas, revisables):

- 50.000/día ≈ 35 documentos/minuto de promedio sostenido; los picos se fijan en [Criterios de aceptación del MVP](09-criterios-aceptacion-mvp.md).
- La lectura masiva va por trabajos asíncronos con cola y workers que escalan horizontalmente; no por peticiones HTTP que esperan el resultado.
- El costo por documento es una restricción de primer orden: un LLM caro aplicado a todo el volumen domina el presupuesto. Hay que usar primero los motores baratos y escalar solo cuando haga falta.
- La idempotencia es obligatoria: el mismo PDF no se reprocesa (por ejemplo, con caché por hash).
