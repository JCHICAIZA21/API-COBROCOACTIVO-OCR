# Entregar PDFs de muestra con valores esperados

Type: task
Status: open

## Question

(HITL: lo hace el usuario.) Sin muestras reales no se pueden definir campos ni medir motores. Checklist:

- [ ] 5–10 PDFs por cada variante de formato que exista (nativo y escaneado, si ambos existen). Si contienen datos reales, guardarlos solo en local y no subirlos a ningún repositorio.
- [ ] Para cada PDF, sus **valores esperados** (la verdad de referencia) en un CSV: `archivo, identificacion, nro_comparendo, fecha, …`.
- [ ] Lista de campos a extraer, con su formato y reglas (ej.: fecha `dd/mm/aaaa`, identificación numérica, valores monetarios con separador de miles).
- [ ] Indicar cuáles de esos campos son los "valores a validar" contra los datos de entrada.
- [ ] Indicar qué muestras se pueden usar con motores de IA externa durante el benchmark (según [Política de uso de IA externa](01-politica-ia-externa.md)).

Ubicación sugerida: `.scratch/lector-ocr-ia/muestras/`.
