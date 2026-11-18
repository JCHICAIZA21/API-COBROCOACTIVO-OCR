"""Document templates declared as YAML files."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .domain import DocReaderError, DocumentTemplate, TemplateNotFound


def _invalid(path: Path, detail: object) -> DocReaderError:
    return DocReaderError(f"plantilla inválida en {path.name}: {detail}")


def load_template(path: Path) -> DocumentTemplate:
    """Load one template file; fail fast if it is malformed or a regex does not compile."""
    try:
        template = DocumentTemplate.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise _invalid(path, exc) from exc
    names = [field.name for field in template.fields]
    if len(set(names)) != len(names):
        raise _invalid(path, "nombres de campo repetidos")
    for field in template.fields:
        for pattern in field.patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise _invalid(path, f"regex del campo '{field.name}': {exc}") from exc
    return template


class YamlTemplateRepository:
    """Every `*.yaml` / `*.yml` template of a directory, loaded and validated at construction."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_dir():
            raise DocReaderError(f"directorio de plantillas no encontrado: {directory}")
        self._templates: dict[str, DocumentTemplate] = {}
        for path in sorted(p for p in directory.iterdir() if p.suffix in (".yaml", ".yml")):
            template = load_template(path)
            if template.id in self._templates:
                raise _invalid(path, f"id '{template.id}' repetido")
            self._templates[template.id] = template

    def get(self, template_id: str) -> DocumentTemplate:
        try:
            return self._templates[template_id]
        except KeyError:
            raise TemplateNotFound(f"plantilla no encontrada: {template_id}") from None

    def all(self) -> list[DocumentTemplate]:
        return list(self._templates.values())
