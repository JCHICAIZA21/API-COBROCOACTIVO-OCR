# Alcance del eslabón: extraer, validar y persistir

Type: grilling
Status: open

## Question

¿Dónde termina exactamente este eslabón?

- (a) ¿Solo extrae campos, o también compara lo extraído contra los datos de entrada (identificación, nro_comparendo, fecha…) y emite un veredicto por campo (coincide / no coincide / ilegible)?
- (b) ¿Persiste él mismo los resultados en "las otras tablas", o devuelve JSON/evento y otro componente persiste?
- (c) ¿Debe conservar evidencia para auditoría del proceso de cobro coactivo (texto crudo, página, coordenadas, motor usado, confianza)?
