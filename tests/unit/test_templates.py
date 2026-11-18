from pathlib import Path

import pytest

from docreader.domain import DocReaderError, TemplateNotFound
from docreader.templates import YamlTemplateRepository, load_template

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

MINIMAL = """\
id: {id}
version: "1"
fields:
  - name: placa
    kind: plate
    patterns: ['{pattern}']
"""


def write(directory: Path, name: str, *, id: str = "t1", pattern: str = r"placa (?P<value>\\w+)"):
    path = directory / name
    path.write_text(MINIMAL.format(id=id, pattern=pattern), encoding="utf-8")
    return path


def test_load_template_reads_comparendo() -> None:
    template = load_template(TEMPLATES_DIR / "comparendo.yaml")

    assert template.id == "comparendo"
    assert [f.name for f in template.fields] == [
        "nro_comparendo",
        "identificacion",
        "fecha",
        "placa",
        "valor",
    ]
    assert template.fields[0].kind == "id_number"
    assert template.fields[0].key is True
    assert template.fields[3].required is False


def test_load_template_rejects_regex_that_does_not_compile(tmp_path: Path) -> None:
    path = write(tmp_path, "roto.yaml", pattern=r"placa (?P<value>\\w+")

    with pytest.raises(DocReaderError, match="roto.yaml"):
        load_template(path)


@pytest.mark.parametrize(
    "content",
    [
        "id: t1\nversion: '1'\nfields:\n  - name: x\n    kind: color\n",  # unknown kind
        "id: t1\nfields: []\n",  # missing version
        "id: t1\nversion: '1'\nfields: [\n",  # malformed YAML
        "- just\n- a list\n",
    ],
)
def test_load_template_rejects_malformed_file(tmp_path: Path, content: str) -> None:
    path = tmp_path / "malo.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DocReaderError, match="malo.yaml"):
        load_template(path)


def test_load_template_rejects_duplicate_field_names(tmp_path: Path) -> None:
    path = tmp_path / "doble.yaml"
    path.write_text(
        "id: t1\nversion: '1'\nfields:\n  - name: x\n  - name: x\n", encoding="utf-8"
    )

    with pytest.raises(DocReaderError, match="doble.yaml"):
        load_template(path)


def test_repository_loads_project_templates() -> None:
    repo = YamlTemplateRepository(TEMPLATES_DIR)

    assert repo.get("comparendo").version == "0.1.0"
    assert "comparendo" in [t.id for t in repo.all()]


def test_repository_loads_yaml_and_yml_files(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", id="a")
    write(tmp_path, "b.yml", id="b")
    (tmp_path / "notas.txt").write_text("no es plantilla", encoding="utf-8")

    repo = YamlTemplateRepository(tmp_path)

    assert sorted(t.id for t in repo.all()) == ["a", "b"]
    assert repo.get("b").id == "b"


def test_repository_unknown_template_raises_not_found(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", id="a")

    with pytest.raises(TemplateNotFound):
        YamlTemplateRepository(tmp_path).get("otra")


def test_repository_rejects_duplicate_ids_naming_the_file(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", id="mismo")
    write(tmp_path, "b.yaml", id="mismo")

    with pytest.raises(DocReaderError, match=r"b\.yaml"):
        YamlTemplateRepository(tmp_path)


def test_repository_fails_fast_on_invalid_file(tmp_path: Path) -> None:
    write(tmp_path, "a.yaml", id="a")
    write(tmp_path, "roto.yml", id="b", pattern="(")

    with pytest.raises(DocReaderError, match="roto.yml"):
        YamlTemplateRepository(tmp_path)


def test_repository_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(DocReaderError):
        YamlTemplateRepository(tmp_path / "no-existe")
