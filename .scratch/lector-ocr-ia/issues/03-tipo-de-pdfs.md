# Tipo de PDFs a soportar

Type: grilling
Status: resolved

## Question

¿Los PDFs a procesar son nativos digitales (texto seleccionable), escaneados, o mixtos?

## Answer

Decidido por el usuario durante el trazado del mapa (2026-11-18): **mixtos / por confirmar con las muestras**.

Implicaciones (derivadas, revisables):

- El servicio soporta ambos tipos. Detecta por página si existe capa de texto y aplica OCR (o visión) solo a las páginas que no la tienen.
- La proporción real nativo/escaneado se mide con las muestras de [Entregar PDFs de muestra con valores esperados](04-muestras-pdf-y-valores-esperados.md) y afecta al costo estimado.
