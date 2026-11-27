# Operación: reintentos, retención y modo de llamada LLM

Type: grilling
Status: resolved

## Question

¿Cuántos reintentos tiene un ítem que falla repetidamente, cuánto tiempo se conservan lotes y resultados (datos personales), qué hardware se asume y cómo se llama a los LLM externos?

## Answer

Decidido por el usuario (2026-11-18):

- **Reintentos**: un ítem queda `failed` al **3.er intento** fallido.
- **Retención**: configurable (`DOCREADER_RETENTION_DAYS`), **365 días por defecto**; `0` = sin borrado automático. Al vencer se borran el trabajo, sus ítems y los archivos subidos.
- **LLM externos**: solo llamadas **síncronas** (sin API por lotes, que no es de cero retención). Modelos por defecto: **`claude-opus-5-5`** y **`gpt-6.2-sol`**.
- **Hardware**: por definir. Se diseña para **solo CPU**, con GPU opcional.
