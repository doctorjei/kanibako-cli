"""Public-engine contracts: optional in unit jobs, required by CI conformance."""

from __future__ import annotations

import copy
import importlib
import json
import os
import re
import tomllib
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

if os.environ.get("KANI_REQUIRE_KINEMATA") == "1":
  importlib.import_module("kinemata")
else:
  pytest.importorskip("kinemata")

from kinemata.adapters.mapping import MappingRegistry
from kinemata.config import ConfigError, load
from kinemata.contract import MISSING, NAMED_BOUNDARIES, Selected, at_path, population

import kanibako
import kinemata_manifest
from kanibako.settings import keyspace_manifest
from kanibako.settings.config_io import parse_packaged
from kanibako.settings.core_defaults import PACKAGED_SETTINGS_PARTS, packaged_data_dir
from kinemata_manifest import ManifestRegistry

ROOT = Path(__file__).resolve().parents[1]
TARGET = "kinemata_manifest:ManifestRegistry"


def _manifest_declarations():
  declarations = []
  for filename in ("kinemata.toml", "kinemata-spec.toml"):
    text = (ROOT / filename).read_text(encoding="utf-8")
    registries = tomllib.loads(text)["registry"]
    assert not any(
      str(row.get("source", "")).endswith(keyspace_manifest.KEYSPACE_MANIFEST_FILENAME)
      for row in registries
    ), f"{filename} still reads manifest bytes through the YAML builder"
    blocks = re.findall(r"(?ms)^\[\[registry\]\]\n.*?(?=^\[|\Z)", text)
    found = []
    for block in blocks:
      row = tomllib.loads(block)["registry"][0]
      if row.get("target") == TARGET:
        assert row["kind"] == "import"
        found.append((filename, row, block))
    assert found, f"{filename} has no manifest declarations"
    assert len(blocks) == len(registries)
    declarations.extend(found)
  return declarations


DECLARATIONS = _manifest_declarations()


def _typed(value):
  if isinstance(value, dict):
    return type(value), tuple((_typed(key), _typed(cell)) for key, cell in value.items())
  if isinstance(value, (list, tuple)):
    return type(value), tuple(_typed(cell) for cell in value)
  return type(value), value


def _assert_same_registry(actual, expected):
  entries = list(expected.entries())
  assert entries
  assert [_typed(asdict(entry)) for entry in actual.entries()] == [
    _typed(asdict(entry)) for entry in entries
  ]
  assert [_typed(asdict(entry)) for entry in population(actual)] == [
    _typed(asdict(entry)) for entry in population(expected)
  ]
  for attribute in (
    "name",
    "closed",
    "budget",
    "line_budget",
    "boundary",
    "match_mode",
    "suffixes",
    "machinery",
    "mentions_are_uses",
    "defer_to",
    "only",
  ):
    assert getattr(actual, attribute) == getattr(expected, attribute), attribute
  for entry in population(expected):
    assert actual.declared(entry.id) == expected.declared(entry.id)
    assert actual.resolve(entry.id) == expected.resolve(entry.id)
    assert actual.line(entry) == expected.line(entry)
  absent = "__manifest_absent_probe__"
  assert not expected.declared(absent)
  assert not actual.declared(absent)
  assert actual.resolve(absent) == expected.resolve(absent) == ()
  text = "\n".join(f"'{entry.id}' prefix{entry.id}suffix" for entry in population(expected))
  assert actual.detect(text) == expected.detect(text)
  for registry in (actual, expected):
    with pytest.raises(NotImplementedError):
      registry.candidates(text)


def _load_registry(tmp_path, block, filename="registry.toml"):
  path = tmp_path / filename
  path.write_text('[project]\nroot = "."\n' + block, encoding="utf-8")
  settings = load(path)
  assert len(settings.registries) == 1
  return settings.registries[0]


@pytest.fixture(scope="module")
def native_manifest(tmp_path_factory):
  path = tmp_path_factory.mktemp("manifest-native") / "manifest.yaml"
  path.write_text(
    yaml.safe_dump(keyspace_manifest.manifest_doc(), sort_keys=False), encoding="utf-8"
  )
  return path


@pytest.mark.parametrize(
  "filename,row,block",
  DECLARATIONS,
  ids=[f"{file}:{row['name']}" for file, row, _ in DECLARATIONS],
)
def test_every_declaration_matches_public_yaml_loader(
  tmp_path, native_manifest, filename, row, block
):
  old = block.replace('kind = "import"', 'kind = "yaml-mapping"').replace(
    f'target = "{TARGET}"', f"source = {json.dumps(str(native_manifest))}"
  )
  actual = _load_registry(tmp_path, block, "owned.toml")
  expected = _load_registry(tmp_path, old, "native.toml")
  _assert_same_registry(actual, expected)
  assert actual.name == row["name"], filename
  assert isinstance(actual, Selected) == ("where" in row)
  whole = actual.whole if isinstance(actual, Selected) else actual
  assert isinstance(whole, ManifestRegistry)


def test_source_identity():
  assert Path(kinemata_manifest.__file__).resolve() == ROOT / "src/kinemata_manifest.py"
  assert Path(kanibako.__file__).resolve() == ROOT / "src/kanibako/__init__.py"
  assert (
    Path(keyspace_manifest.__file__).resolve()
    == ROOT / "src/kanibako/settings/keyspace_manifest.py"
  )
  resource = packaged_data_dir(
    *PACKAGED_SETTINGS_PARTS, keyspace_manifest.KEYSPACE_MANIFEST_FILENAME
  )
  checkout = ROOT / "src/kanibako/data" / Path(*PACKAGED_SETTINGS_PARTS) / resource.name
  assert Path(str(resource)).resolve() == checkout
  assert resource.read_bytes() == checkout.read_bytes()
  assert _typed(keyspace_manifest.manifest_doc()) == _typed(
    parse_packaged(checkout.read_text(encoding="utf-8"))
  )


EDGE_YAML = """\
literal.with.dots:
  scalar-null: null
  scalar-false: false
  quoted-none: 'None'
  missing: {spec: null}
  null-default: {default: null, spec: 'clause.one'}
  false-default: {default: false, spec: ['clause.two', 17]}
  modes:
    default: {primary: null, named: false}
    value: {standalone: null}
    extra-field: {nested: [null, false, 'None', {}, []]}
  empty-list: []
  empty-map: {}
nested:
  literal.with.dots:
    other: {value: 'nested table'}
"""


@pytest.mark.parametrize("section", ["literal.with.dots", ["nested", "literal.with.dots"]])
@pytest.mark.parametrize("clause_field", [None, "spec"])
def test_independent_typed_rows_and_literal_section_keys(monkeypatch, section, clause_field):
  native = yaml.safe_load(EDGE_YAML)
  owned = parse_packaged(EDGE_YAML)
  assert _typed(owned) == _typed(native)
  monkeypatch.setattr(kinemata_manifest, "manifest_doc", lambda: copy.deepcopy(owned))
  steps = [section] if isinstance(section, str) else section
  expected = native
  for step in steps:
    expected = expected[step]
  actual = ManifestRegistry(section=section, clause_field=clause_field)
  _assert_same_registry(actual, MappingRegistry(expected, clause_field=clause_field))
  if isinstance(section, str):
    rows = {entry.id: entry for entry in actual.entries()}
    assert rows["scalar-null"].extra["value"] is None
    assert rows["scalar-false"].extra["value"] is False
    assert rows["quoted-none"].extra["value"] == "None"
    assert at_path(rows["missing"].extra, ("default",)) is MISSING
    assert at_path(rows["null-default"].extra, ("default",)) is None
    assert at_path(rows["false-default"].extra, ("default",)) is False
    assert at_path(rows["modes"].extra, ("default", "standalone")) is MISSING
    assert at_path(rows["modes"].extra, ("default", "primary")) is None
    assert rows["false-default"].clauses == (("clause.two", "17") if clause_field else ())


def test_where_is_loader_owned_and_applied_once(tmp_path, monkeypatch):
  doc = yaml.safe_load(EDGE_YAML)
  monkeypatch.setattr(kinemata_manifest, "manifest_doc", lambda: copy.deepcopy(doc))
  where = {"id_matches": "^scalar-"}
  direct = ManifestRegistry(section="literal.with.dots", where=where)
  assert len(list(direct.entries())) == len(doc["literal.with.dots"])
  block = f'''[[registry]]
name = "loader-name"
kind = "import"
target = "{TARGET}"
section = "literal.with.dots"
where = {{ id_matches = "^scalar-" }}
match_mode = "raw"
boundary = "name"
suffixes = [".py"]
machinery = ["fixture"]
'''
  selected = _load_registry(tmp_path, block)
  assert isinstance(selected, Selected)
  assert isinstance(selected.whole, ManifestRegistry)
  assert not isinstance(selected.whole, Selected)
  assert selected.name == selected.whole.name == "loader-name"
  assert [entry.id for entry in selected.entries()] == ["scalar-null", "scalar-false"]
  assert len(population(selected)) == len(doc["literal.with.dots"])
  for entry in population(selected):
    assert selected.declared(entry.id) == entry.id.startswith("scalar-")
  assert selected.detect("'quoted-none' 'scalar-null'") == ["scalar-null"]
  assert selected.match_mode == "raw"
  assert selected.boundary == selected.whole.boundary == NAMED_BOUNDARIES["name"]
  assert selected.suffixes == (".py",)
  assert selected.machinery == ("fixture",)


@pytest.mark.parametrize("argument", ["source", "flatten", "separator", "unknown"])
def test_unknown_constructor_keys_fail_directly_and_through_loader(tmp_path, argument):
  with pytest.raises(TypeError, match=argument):
    ManifestRegistry(section="keys", **{argument: "not-supported"})
  block = f'''[[registry]]
name = "bad-constructor"
kind = "import"
target = "{TARGET}"
section = "keys"
{argument} = "not-supported"
'''
  with pytest.raises(ConfigError, match=argument):
    _load_registry(tmp_path, block)


@pytest.mark.parametrize(
  "section", ["missing", ["keys", "missing"], ["keys", "config.data", "type"]]
)
def test_missing_or_scalar_sections_fail(section):
  with pytest.raises(ValueError, match="missing or is not a mapping"):
    ManifestRegistry(section=section)
