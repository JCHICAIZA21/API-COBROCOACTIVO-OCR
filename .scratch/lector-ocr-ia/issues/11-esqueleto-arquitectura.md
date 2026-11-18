# Esqueleto de arquitectura hexagonal del servicio

Type: prototype
Status: claimed
Blocked by: 07

## Question

¿Cómo se ve la arquitectura? Construir un esqueleto mínimo para reaccionar sobre él: interfaces, casos de uso y cableado, sin motores reales.

Hipótesis de partida, para validar o descartar:

- **Puertos y adaptadores**: dominio puro sin dependencias; el núcleo es un paquete independiente del framework web, reutilizable en otros proyectos. FastAPI y los workers son solo adaptadores de entrada.
- **Cascada de lo más barato a lo más caro**: capa de texto (ms) → reglas deterministas → OCR solo en páginas sin texto → LLM solo para campos con baja confianza y solo si la Política lo permite.
- **Motores como estrategias intercambiables** registradas por configuración (abierto/cerrado), interfaces pequeñas (segregación de interfaces), dependencias inyectadas en una única raíz de composición (inversión de dependencias).
- **Velocidad**: I/O asíncrono para APIs y almacenamiento; trabajo de CPU (rasterizado, OCR) en procesos/workers separados; caché idempotente por hash del PDF + versión de plantilla.

Entregable: esqueleto de código y diagrama, enlazados como asset desde este ticket.
