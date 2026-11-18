# Alcance del eslabón: extraer, validar y persistir

Type: grilling
Status: resolved

## Question

¿Dónde termina exactamente este eslabón?

- (a) ¿Solo extrae campos, o también compara lo extraído contra los datos de entrada (identificación, nro_comparendo, fecha…) y emite un veredicto por campo (coincide / no coincide / ilegible)?
- (b) ¿Persiste él mismo los resultados en "las otras tablas", o devuelve JSON/evento y otro componente persiste?
- (c) ¿Debe conservar evidencia para auditoría del proceso de cobro coactivo (texto crudo, página, coordenadas, motor usado, confianza)?

## Answer

Decidido por el usuario (2026-11-18): **extraer + validar y devolver**.

- (a) Extrae los campos y los compara contra los datos de entrada, con veredicto por campo: `match` / `mismatch` / `not_found` / `not_provided`.
- (b) **No** escribe en las tablas de negocio; devuelve el resultado y otro componente persiste. Solo guarda el estado de sus propios trabajos por lote (ver [Plataforma de despliegue e infraestructura](08-plataforma-e-infraestructura.md)).
- (c) Devuelve evidencia por campo: página, fragmento de texto, motor y confianza.
