"""Explicit legacy conversion uses application policy, never a runtime fallback."""

import builtins
from datetime import date, datetime, timezone
import io
import os
from pathlib import Path

import pytest
import yapyon
from yapyon import yamlplus

from kanibako.migration import yaml_to_yapyon
from kanibako.migration.yaml_to_yapyon import ConversionError
from kanibako.settings import config_io


@pytest.mark.parametrize(
  "text, expected",
  [
    ("None\n", None),
    ("'None'\n", "None"),
    ("42\n", 42),
    ("true\n", True),
    ("null\n", None),
    ("[]\n", []),
    ("{}\n", {}),
    ("__STANDALONE__\n", "__STANDALONE__"),
    (
      "plain: None\nquoted: 'None'\nlower: none\nupper: NONE\nvalues: [yes, 2, 2.5, null]\n",
      {
        "plain": None,
        "quoted": "None",
        "lower": "none",
        "upper": "NONE",
        "values": [True, 2, 2.5, None],
      },
    ),
    ("2026-11-02\n", "2026-11-02"),
    ("2026-11-02T12:34:56Z\n", "2026-11-02 12:34:56+00:00"),
    ("!!binary YWJj\n", "b'abc'"),
  ],
)
def test_legacy_values_reach_the_actual_owned_reader(text, expected):
  converted = config_io.convert_legacy_doc_text(text, source="legacy.yaml")
  assert config_io.parse_doc_text(converted) == expected
  assert yamlplus.normalize(converted) == converted
  if expected == "__STANDALONE__":
    assert config_io.parse_standalone_marker(converted.encode()) == expected


@pytest.mark.parametrize("text", ["", "\n \t\n", "# bootstrap\n", "\r\n  # empty\r\n\t\r\n"])
def test_blank_and_comment_only_bootstraps_remain_exact(text):
  assert config_io.convert_legacy_doc_text(text) == text
  assert config_io.parse_doc_text(text) is None


def test_dates_bytes_references_and_key_order_match_owned_output():
  text = (
    "z: 2026-11-02\n"
    "a: 2026-11-02T12:34:56Z\n"
    "offset: 2026-11-02T12:34:56+02:00\n"
    "space: '2026-11-02 12:34:56+00:00'\n"
    "bytes: !!binary YWJj\n"
    "nested:\n  second: '@{meta.box.path}/$HOME/~'\n  first: '{$XDG_DATA_HOME}/literal'\n"
  )
  expected = {
    "z": "2026-11-02",
    "a": "2026-11-02 12:34:56+00:00",
    "offset": "2026-11-02 12:34:56+02:00",
    "space": "2026-11-02 12:34:56+00:00",
    "bytes": "b'abc'",
    "nested": {"second": "@{meta.box.path}/$HOME/~", "first": "{$XDG_DATA_HOME}/literal"},
  }
  actual = config_io.parse_doc_text(config_io.convert_legacy_doc_text(text))
  assert actual == expected
  assert list(actual) == list(expected)
  assert list(actual["nested"]) == ["second", "first"]
  assert (
    config_io.parse_doc_text(config_io._dump_text(config_io.parse_template_yaml(text))) == actual
  )


def test_dump_and_migration_share_detached_output_normalization(monkeypatch):
  original = {"items": (date(2026, 11, 2), datetime(2026, 11, 2, tzinfo=timezone.utc), b"abc")}
  normalized = config_io._owned_output_data(original)
  assert normalized == {"items": ["2026-11-02", "2026-11-02 00:00:00+00:00", "b'abc'"]}
  assert isinstance(original["items"], tuple)
  normalized["items"].append("detached")
  assert len(original["items"]) == 3
  operation = config_io._owned_output_data
  calls = []

  def capture(data):
    calls.append(data)
    return operation(data)

  monkeypatch.setattr(config_io, "_owned_output_data", capture)
  config_io._dump_text(original)
  assert len(calls) == 1
  config_io.convert_legacy_doc_text("a: None\n")
  assert len(calls) == 3  # The selected Loader also verifies the intermediate YAML.


@pytest.mark.parametrize(
  "text, expected",
  [
    (
      "# header\na: 1 # same\nb: [2, # same\n    # next\n    3]\n# footer\n",
      [
        ("# header", (), "header"),
        ("# same", ("a",), "value"),
        ("# same", ("b", 0), "value"),
        ("# next", ("b", 1), "value"),
        ("# footer", (), "footer"),
      ],
    ),
    (
      "base: &b\n  x: None # definition\ncopy: *b # alias\n",
      [("# definition", ("base", "x"), "value"), ("# alias", ("copy",), "value")],
    ),
    (
      "base: &b {x: 1}\nresult:\n  <<: *b # merge\n  y: 2 # direct\n",
      [("# merge", ("result",), "value"), ("# direct", ("result", "y"), "value")],
    ),
    (
      "result:\n  <<:\n    x: 1 # inherited\n  x: 2 # override\n",
      [("# inherited", ("result",), "value"), ("# override", ("result", "x"), "value")],
    ),
  ],
)
def test_comment_paths_occurrences_and_merge_relocation(text, expected):
  converted = config_io.convert_legacy_doc_text(text)
  actual = config_io.parse_doc_text(converted)
  assert actual == config_io.parse_template_yaml(text)
  assert list(actual) == list(config_io.parse_template_yaml(text))
  tree = yapyon.parse(converted)
  lines = converted.splitlines(keepends=True)
  destinations = {}
  for comment, path, placement in expected:
    assert converted.count(comment) == text.count(comment)
    if placement == "header":
      assert converted.startswith(comment)
      continue
    if placement == "footer":
      assert converted.endswith(comment + "\n")
      continue
    handle = tree.root
    for step in path:
      handle = handle[step]
    _, line, column = handle.where
    end = sum(map(len, lines[: line - 1])) + column
    destinations.setdefault(end, []).append(comment)
  occurrences = set()
  for end, comments in destinations.items():
    cursor = end
    for comment in reversed(comments):
      start = converted.rfind(comment, 0, cursor)
      assert start >= 0
      assert not converted[start + len(comment) : cursor].strip()
      assert start not in occurrences
      occurrences.add(start)
      cursor = start


@pytest.mark.parametrize(
  "text, problem",
  [
    ("a: 1\na: 2\n", "sets 'a' twice"),
    ("base: &b {x: 1, x: 2}\ncopy: {<<: *b}\n", "sets 'base.x' twice"),
    ("a: &a [*a]\n", "refers to itself"),
    ("a: &a {child: *a}\n", "refers to itself"),
    ("[" * 65 + "0" + "]" * 65, "nests deeper than 64 levels"),
    ("a: [\n", "expected"),
    ("!!python/object/apply:os.system ['false']\n", "could not determine a constructor"),
  ],
)
def test_guarded_legacy_errors_keep_source_and_cause(text, problem):
  with pytest.raises(ConversionError) as caught:
    config_io.convert_legacy_doc_text(text, source="never-opened-legacy.yaml")
  assert str(caught.value).startswith("never-opened-legacy.yaml: ")
  assert problem in str(caught.value)
  assert caught.value.__cause__ is not None


def test_document_at_depth_limit_and_shared_siblings_are_accepted():
  text = "[" * 64 + "0" + "]" * 64
  assert config_io.parse_doc_text(
    config_io.convert_legacy_doc_text(text)
  ) == config_io.parse_template_yaml(text)
  actual = config_io.parse_doc_text(config_io.convert_legacy_doc_text("a: &a {x: 1}\nb: *a\n"))
  assert actual == {"a": {"x": 1}, "b": {"x": 1}}
  assert actual["a"] is not actual["b"]


def test_text_conversion_opens_no_files_and_uses_public_unit_and_profile(monkeypatch):
  calls = []
  from_yaml = yaml_to_yapyon.from_yaml
  normalize = yamlplus.normalize

  def convert(text, **kwargs):
    assert issubclass(kwargs["Loader"], config_io._UniqueKeyLoader)
    assert kwargs["yaml_compatible"] is True
    calls.append("unit")
    return from_yaml(text, **kwargs)

  def profile(text, **kwargs):
    assert kwargs == {"source": "never-opened.yaml"}
    calls.append("profile")
    return normalize(text, **kwargs)

  def forbidden(*args, **kwargs):
    pytest.fail("file I/O during explicit text conversion")

  monkeypatch.setattr(yaml_to_yapyon, "from_yaml", convert)
  monkeypatch.setattr(yamlplus, "normalize", profile)
  monkeypatch.setattr(builtins, "open", forbidden)
  monkeypatch.setattr(io, "open", forbidden)
  monkeypatch.setattr(os, "open", forbidden)
  monkeypatch.setattr(Path, "open", forbidden)
  text = "a: None # retained\ndate: 2026-11-02\n"
  converted = config_io.convert_legacy_doc_text(text, source="never-opened.yaml")
  assert config_io.parse_doc_text(converted) == {"a": None, "date": "2026-11-02"}
  assert calls == ["unit", "profile"]


def test_explicit_conversion_is_not_an_owned_reader_fallback(monkeypatch):
  def forbidden(*args, **kwargs):
    pytest.fail("owned reader called migration")

  monkeypatch.setattr(config_io, "convert_legacy_doc_text", forbidden)
  assert config_io.parse_doc_text("a: None\n") == {"a": None}
  with pytest.raises(yapyon.AkanError):
    config_io.parse_doc_text("a: &legacy {x: 1}\nb: *legacy\n")


def test_core_and_portable_unit_are_from_this_worktree():
  root = Path(__file__).resolve().parents[2]
  assert Path(config_io.__file__).resolve() == root / "src/kanibako/settings/config_io.py"
  assert (
    Path(yaml_to_yapyon.__file__).resolve() == root / "src/kanibako/migration/yaml_to_yapyon.py"
  )
