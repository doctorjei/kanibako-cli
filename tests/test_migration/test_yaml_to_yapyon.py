"""Portable conversion, selected legacy dialect, and owned-profile readback."""

import builtins
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
import yapyon
from yapyon import yamlplus

from kanibako.migration import yaml_to_yapyon
from kanibako.migration.yaml_to_yapyon import ConversionError, from_yaml
from kanibako.settings.config_io import (
  _UniqueKeyLoader,
  parse_doc_text,
  parse_template_yaml,
)


@pytest.mark.parametrize(
  "source, expected",
  [
    ("42\n", 42),
    ("true\n", True),
    ("null\n", None),
    ("None\n", None),
    ("'None'\n", "None"),
    ("[]\n", []),
    ("{}\n", {}),
    (
      "a: [1, true, null, 2.5]\nb: {nested: yes}\n",
      {"a": [1, True, None, 2.5], "b": {"nested": True}},
    ),
  ],
)
def test_selected_legacy_values_survive_owned_profile(source, expected):
  assert parse_template_yaml(source) == expected
  result = from_yaml(source, Loader=_UniqueKeyLoader)
  assert result.data == expected
  assert result.verified is True
  assert result.yaml_verified is None
  normalized = yamlplus.normalize(result.text)
  assert parse_doc_text(normalized) == expected


def test_actual_none_dialect_and_generic_default_remain_distinct():
  source = "plain: None\nquoted: 'None'\n'null': null\nlower: none\nupper: NONE\n"
  expected = {"plain": None, "quoted": "None", "null": None, "lower": "none", "upper": "NONE"}
  result = from_yaml(source, Loader=_UniqueKeyLoader, yaml_compatible=True)
  assert result.data == parse_template_yaml(source) == expected
  assert result.yaml_verified is True
  assert yaml.load(result.text, Loader=_UniqueKeyLoader) == expected
  assert parse_doc_text(yamlplus.normalize(result.text)) == expected
  assert from_yaml("None\n").data == "None"
  assert yaml.safe_load("None") == "None"


def test_quoted_dates_unresolved_strings_and_mapping_order():
  source = (
    "z: '2026-11-02'\n"
    "a: '2026-11-02T00:00:00Z'\n"
    "offset: '2026-11-02T12:34:56+02:00'\n"
    "space: '2026-11-02 12:34:56+00:00'\n"
    "nested:\n"
    "  second: '@{meta.box.path}/$HOME/~'\n"
    "  first: '{$XDG_DATA_HOME}/literal'\n"
  )
  expected = parse_template_yaml(source)
  result = from_yaml(source, Loader=_UniqueKeyLoader)
  actual = parse_doc_text(yamlplus.normalize(result.text))
  assert result.data == actual == expected
  assert list(actual) == ["z", "a", "offset", "space", "nested"]
  assert list(actual["nested"]) == ["second", "first"]
  assert all(isinstance(actual[key], str) for key in ("z", "a", "offset", "space"))


@pytest.mark.parametrize(
  "source, expected",
  [
    (
      '# header\na: "# same" # same\n# same suffix\nb: [1, # same\n'
      "    # before second\n    2]\n# footer\n",
      [
        ("# header", (), "header", False),
        ("# same", ("a",), "value", False),
        ("# same suffix", ("b",), "value", False),
        ("# same", ("b", 0), "value", False),
        ("# before second", ("b", 1), "value", False),
        ("# footer", (), "footer", False),
      ],
    ),
    (
      "base: &b\n  x: 1 # definition\ncopy: *b # use\n",
      [("# definition", ("base", "x"), "value", False), ("# use", ("copy",), "value", False)],
    ),
    (
      "base: &b {x: 1}\nresult:\n  <<: *b # merge\n  y: 2 # direct\n",
      [("# merge", ("result",), "value", True), ("# direct", ("result", "y"), "value", False)],
    ),
    (
      "result:\n  <<:\n    x: 1 # inherited\n  x: 2 # override\n",
      [
        ("# inherited", ("result",), "value", True),
        ("# override", ("result", "x"), "value", False),
      ],
    ),
    (
      "text: | # header\n  body # content\n  # still content\nnext: 2 # next\n",
      [("# header", ("text",), "value", False), ("# next", ("next",), "value", False)],
    ),
    (
      "- # item\n  a: 1 # leaf\n- # next\n  a: 2\n",
      [
        ("# item", (0,), "value", False),
        ("# leaf", (0, "a"), "value", False),
        ("# next", (1,), "value", False),
      ],
    ),
  ],
)
def test_comment_occurrences_paths_and_relocations_through_normalize(source, expected):
  result = from_yaml(source, Loader=_UniqueKeyLoader, yaml_compatible=True)
  assert result.yaml_verified is True
  assert [(c.text, c.path, c.placement, c.relocated) for c in result.comments] == expected
  assert len({(c.line, c.column) for c in result.comments}) == len(expected)
  normalized = yamlplus.normalize(result.text)
  assert parse_doc_text(normalized) == parse_template_yaml(source) == result.data
  # YAML rediscovery can choose a child after emission; verify Yapyon destinations directly.
  tree = yapyon.parse(normalized)
  lines = normalized.splitlines(keepends=True)
  destinations = {}
  for comment in result.comments:
    assert normalized.count(comment.text) == result.text.count(comment.text)
    if comment.placement == "header":
      assert normalized.startswith(comment.text)
      continue
    if comment.placement == "footer":
      assert normalized.endswith(comment.text + "\n")
      continue
    handle = tree.root
    for step in comment.path:
      handle = handle[step]
    _, line, column = handle.where
    end = sum(map(len, lines[: line - 1])) + column
    destinations.setdefault(end, []).append(comment.text)
  occurrences = set()
  for end, comments in destinations.items():
    cursor = end
    for text in reversed(comments):
      start = normalized.rfind(text, 0, cursor)
      assert start >= 0
      assert not normalized[start + len(text) : cursor].strip()
      assert start not in occurrences
      occurrences.add(start)
      cursor = start


def test_alias_and_merge_notes_describe_materialized_values():
  result = from_yaml(
    "base: &b {x: 1}\ncopy: *b\nmerged: {<<: *b, y: 2}\n", Loader=_UniqueKeyLoader
  )
  actual = parse_doc_text(yamlplus.normalize(result.text))
  assert actual == {"base": {"x": 1}, "copy": {"x": 1}, "merged": {"x": 1, "y": 2}}
  assert actual["base"] is not actual["copy"]
  assert any(n.kind == "alias" and n.path == ("copy",) for n in result.notes)
  assert any(n.kind == "merge" and n.path == ("merged",) for n in result.notes)


@pytest.mark.parametrize("loader", [object, yaml.Loader, lambda value: value])
def test_unsafe_or_nonclass_loaders_are_refused(loader):
  with pytest.raises(TypeError, match="SafeLoader subclass"):
    from_yaml("a: 1\n", Loader=loader)


def test_selected_loader_duplicate_policy_is_not_bypassed():
  with pytest.raises(ConversionError, match="synthetic.yaml:.*sets 'a' twice"):
    from_yaml("a: 1\na: 2\n", Loader=_UniqueKeyLoader, source="synthetic.yaml")


def test_typed_order_verification_cannot_report_false_success(monkeypatch):
  monkeypatch.setattr(yapyon, "loads_record", lambda *args, **kwargs: {"b": 2, "a": 1})
  with pytest.raises(ConversionError, match="order-sensitive Yapyon verification"):
    from_yaml("a: 1\nb: 2\n")


def test_selected_yaml_verification_cannot_report_false_success(monkeypatch):
  original = yaml.load
  calls = 0

  def wrong_second_load(*args, **kwargs):
    nonlocal calls
    calls += 1
    return True if calls == 2 else original(*args, **kwargs)

  monkeypatch.setattr(yaml, "load", wrong_second_load)
  with pytest.raises(ConversionError, match="YAML verification"):
    from_yaml("1\n", Loader=_UniqueKeyLoader, yaml_compatible=True)


def test_conversion_does_not_open_files(monkeypatch):
  def forbidden(*args, **kwargs):
    raise AssertionError("filesystem access during text conversion")

  monkeypatch.setattr(builtins, "open", forbidden)
  monkeypatch.setattr(os, "open", forbidden)
  monkeypatch.setattr(Path, "open", forbidden)
  result = from_yaml("a: 1 # retained\n", source="never-opened.yaml")
  assert result.verified
  assert result.comments[0].text == "# retained"


def test_import_and_conversion_work_without_kanibako(tmp_path):
  script = """
import importlib.abc
import importlib.util
import sys

class NoKanibako(importlib.abc.MetaPathFinder):
  def find_spec(self, fullname, path=None, target=None):
    if fullname == 'kanibako' or fullname.startswith('kanibako.'):
      raise AssertionError('Kanibako import from portable unit')

sys.meta_path.insert(0, NoKanibako())
spec = importlib.util.spec_from_file_location('portable_converter', sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert module.from_yaml('a: 1 # kept\\n').verified
assert not any(n == 'kanibako' or n.startswith('kanibako.') for n in sys.modules)
print('standalone conversion passed')
"""
  completed = subprocess.run(
    [sys.executable, "-I", "-c", script, str(Path(yaml_to_yapyon.__file__).resolve())],
    cwd=tmp_path,
    capture_output=True,
    text=True,
    check=False,
  )
  assert completed.returncode == 0, completed.stderr
  assert completed.stdout == "standalone conversion passed\n"


def test_imported_worktree_origin():
  assert Path(yaml_to_yapyon.__file__).resolve() == (
    Path(__file__).resolve().parents[2] / "src/kanibako/migration/yaml_to_yapyon.py"
  )
