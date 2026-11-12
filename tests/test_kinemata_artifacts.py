"""Public-engine contracts: optional in unit jobs, required by CI conformance."""

from __future__ import annotations

import importlib
import json
import os
import re
import tomllib
from pathlib import Path

import pytest
import yaml

if os.environ.get("KANI_REQUIRE_KINEMATA") == "1":
  importlib.import_module("kinemata")
else:
  pytest.importorskip("kinemata")

from kinemata.adapters.mapping import MappingRegistry
from kinemata.config import ConfigError
from kinemata.contract import MISSING, Selected, at_path, population

import kanibako
import kinemata_artifacts
from kanibako.settings import config_io
from kinemata_artifacts import ArtifactRegistry
from tests.test_kinemata_manifest import (
  EDGE_YAML,
  _assert_same_registry,
  _load_registry,
  _typed,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET = "kinemata_artifacts:ArtifactRegistry"
OWNED_EDGE_YAML = config_io.convert_legacy_doc_text(EDGE_YAML)


def _declaration():
  text = (ROOT / "kinemata.toml").read_text(encoding="utf-8")
  blocks = re.findall(r"(?ms)^\[\[registry\]\]\n.*?(?=^\[|\Z)", text)
  found = []
  for block in blocks:
    row = tomllib.loads(block)["registry"][0]
    assert row.get("source") != "generated-artifacts.yaml"
    if row["name"] == "api-doc-artifacts":
      assert row["kind"] == "import"
      assert row["target"] == TARGET
      found.append(block)
  assert len(found) == 1
  return found[0]


def test_declaration_matches_public_native_loader(tmp_path):
  block = _declaration()
  text = config_io.read_doc_text(ROOT / "generated-artifacts.yaml", encoding="utf-8")
  document = config_io.parse_doc_text(text)
  assert isinstance(document, dict) and document["artifacts"]
  native = tmp_path / "native.yaml"
  native.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
  old = block.replace('kind = "import"', 'kind = "yaml-mapping"').replace(
    f'target = "{TARGET}"', f"source = {json.dumps(str(native))}"
  )
  actual = _load_registry(tmp_path, block, "owned.toml")
  expected = _load_registry(tmp_path, old, "native.toml")
  _assert_same_registry(actual, expected)
  assert isinstance(actual, ArtifactRegistry)
  assert actual.name == "api-doc-artifacts"
  assert all(entry.extra == {"value": None} for entry in actual.entries())


def test_source_identity_and_cwd_independence(tmp_path, monkeypatch):
  assert Path(kinemata_artifacts.__file__).resolve() == ROOT / "src/kinemata_artifacts.py"
  assert Path(kanibako.__file__).resolve() == ROOT / "src/kanibako/__init__.py"
  assert Path(config_io.__file__).resolve() == ROOT / "src/kanibako/settings/config_io.py"
  text = config_io.read_doc_text(ROOT / "generated-artifacts.yaml", encoding="utf-8")
  expected = config_io.parse_doc_text(text)["artifacts"]
  (tmp_path / "generated-artifacts.yaml").write_text(
    'artifacts: {"decoy": None}\n', encoding="utf-8"
  )
  monkeypatch.chdir(tmp_path)
  reads = []
  reader = config_io.read_doc_text

  def record(path, **kwargs):
    reads.append(path)
    return reader(path, **kwargs)

  monkeypatch.setattr(kinemata_artifacts, "read_doc_text", record)
  actual = ArtifactRegistry(section="artifacts")
  _assert_same_registry(actual, MappingRegistry(expected))
  assert reads == [ROOT / "generated-artifacts.yaml"]
  assert not actual.declared("decoy")


@pytest.mark.parametrize("section", ["literal.with.dots", ["nested", "literal.with.dots"]])
@pytest.mark.parametrize("clause_field", [None, "spec"])
def test_complete_typed_records(monkeypatch, section, clause_field):
  native = yaml.safe_load(EDGE_YAML)
  owned = config_io.parse_doc_text(OWNED_EDGE_YAML)
  assert _typed(owned) == _typed(native)
  monkeypatch.setattr(kinemata_artifacts, "read_doc_text", lambda *a, **kw: OWNED_EDGE_YAML)
  expected = native
  for step in [section] if isinstance(section, str) else section:
    expected = expected[step]
  actual = ArtifactRegistry(section=section, clause_field=clause_field)
  _assert_same_registry(actual, MappingRegistry(expected, clause_field=clause_field))
  if isinstance(section, str):
    rows = {entry.id: entry for entry in actual.entries()}
    assert rows["scalar-null"].extra == {"value": None}
    assert rows["scalar-false"].extra == {"value": False}
    assert rows["quoted-none"].extra == {"value": "None"}
    assert at_path(rows["missing"].extra, ("default",)) is MISSING
    assert at_path(rows["null-default"].extra, ("default",)) is None
    assert rows["false-default"].clauses == (("clause.two", "17") if clause_field else ())


def test_where_is_public_loader_owned(tmp_path, monkeypatch):
  monkeypatch.setattr(kinemata_artifacts, "read_doc_text", lambda *a, **kw: OWNED_EDGE_YAML)
  options = 'where = { id_matches = "^scalar-" }\n'
  block = f'''[[registry]]
name = "selected-artifacts"
kind = "import"
target = "{TARGET}"
section = "literal.with.dots"
clause_field = "spec"
match_mode = "raw"
boundary = "name"
suffixes = [".py"]
machinery = ["fixture"]
{options}'''
  native = tmp_path / "native.yaml"
  native.write_text(EDGE_YAML, encoding="utf-8")
  old = block.replace('kind = "import"', 'kind = "yaml-mapping"').replace(
    f'target = "{TARGET}"', f"source = {json.dumps(str(native))}"
  )
  actual = _load_registry(tmp_path, block, "owned.toml")
  expected = _load_registry(tmp_path, old, "native.toml")
  _assert_same_registry(actual, expected)
  assert isinstance(actual, Selected)
  assert isinstance(actual.whole, ArtifactRegistry)
  assert not isinstance(actual.whole, Selected)
  assert len(population(actual)) > len(list(actual.entries())) > 0
  direct = ArtifactRegistry(section="literal.with.dots", where={"id_matches": "^scalar-"})
  assert len(list(direct.entries())) == len(population(actual))


@pytest.mark.parametrize("argument", ["source", "flatten", "separator", "unknown"])
def test_unknown_constructor_keys_fail(tmp_path, argument):
  with pytest.raises(TypeError, match=argument):
    ArtifactRegistry(section="artifacts", **{argument: "not-supported"})
  block = f'''[[registry]]
name = "bad-artifacts"
kind = "import"
target = "{TARGET}"
section = "artifacts"
{argument} = "not-supported"
'''
  with pytest.raises(ConfigError, match=argument):
    _load_registry(tmp_path, block)


@pytest.mark.parametrize("section", ["absent", ["artifacts", "absent"]])
def test_missing_sections_fail(section):
  with pytest.raises(ValueError, match="missing or is not a mapping"):
    ArtifactRegistry(section=section)


def test_owned_none_spelling_keeps_string_none(monkeypatch):
  text = 'artifacts:\n  "null": None\n  "string": "None"\n'
  monkeypatch.setattr(kinemata_artifacts, "read_doc_text", lambda *a, **kw: text)
  rows = list(ArtifactRegistry(section="artifacts").entries())
  assert [entry.id for entry in rows] == ["null", "string"]
  assert [entry.extra for entry in rows] == [{"value": None}, {"value": "None"}]


def test_descriptor_oracle_uses_owned_codec(monkeypatch, capsys):
  from kinemata_views import plugin_descriptors

  config = tomllib.loads((ROOT / "kinemata.toml").read_text(encoding="utf-8"))
  commands = [row["command"] for row in config["parity"] if row["registry"] == "cred-entry-owners"]
  assert len(commands) == 1
  texts = {path: (ROOT / path).read_text() for path in plugin_descriptors()}
  assert texts
  parsed = []
  parser = config_io.parse_doc_text

  def record(text, *, source=None):
    parsed.append((text, source))
    return parser(text, source=source)

  monkeypatch.setattr(config_io, "parse_doc_text", record)
  monkeypatch.chdir(ROOT)
  exec(compile(commands[0][-1], "cred-entry-owners-oracle", "exec"), {})
  assert all((text, source) in parsed for source, text in texts.items())
  lines = capsys.readouterr().out.splitlines()
  assert lines and all(line.startswith("kanibako-entry-owner: ") for line in lines)


@pytest.mark.parametrize("text", ["artifacts: False\n", "False\n", "None\n"])
def test_scalar_section_fails(monkeypatch, text):
  monkeypatch.setattr(kinemata_artifacts, "read_doc_text", lambda *a, **kw: text)
  with pytest.raises(ValueError, match="missing or is not a mapping"):
    ArtifactRegistry(section="artifacts")
