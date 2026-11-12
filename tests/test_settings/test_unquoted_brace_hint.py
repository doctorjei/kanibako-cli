"""Brace-leading parse failures retain quoted repairs; reference-shaped maps get shape help."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from yapyon import AkanError

from kanibako.errors import ConfigError
from kanibako.settings.config import system_table_set_values
from kanibako.settings.config_io import load_doc, parse_template_yaml, yaml_problem
from kanibako.settings.settings_assemble import _refuse_table_at_scalar_leaf
from kanibako.settings.settings_categories import refuse_non_scalar_family_value
from kanibako.settings.settings_resolve import SettingsError, unquoted_reference_hint
from kanibako.settings.workset_dirkeys import early_tier


@pytest.mark.parametrize(
  ("text", "cure"),
  [
    ("system:\n  env:\n    FOO: {$HOME}/x\n", "FOO: '{$HOME}/x'"),
    ("box:\n  canon: {box.image}/x  # note\n", "canon: '{box.image}/x'"),
    ("a:\n  - {$XDG_DATA_HOME}/x\n", "- '{$XDG_DATA_HOME}/x'"),
  ],
)
def test_a_yaml_error_at_an_unquoted_brace_says_to_quote_it(tmp_path, text, cure) -> None:
  with pytest.raises(yaml.MarkedYAMLError) as native:
    parse_template_yaml(text)
  native_message = yaml_problem(native.value)
  assert "(line " in native_message
  assert f"a value starting with '{{' must be quoted: {cure}" in native_message
  path = tmp_path / "settings.yaml"
  path.write_text(text)
  with pytest.raises(ConfigError) as ei:
    load_doc(path)
  msg = str(ei.value)
  cause = ei.value.__cause__
  assert isinstance(cause, AkanError)
  assert cause.source == str(path)
  assert f"{path}:{cause.line}:{cause.col}:" in msg, msg
  assert str(cause) in msg
  assert f"a value starting with '{{' must be quoted: {cure}." in msg, msg
  assert path.read_text() == text
  repaired = (
    "\n".join(
      line[: len(line) - len(line.lstrip())] + cure if "{" in line else line
      for line in text.splitlines()
    )
    + "\n"
  )
  path.write_text(repaired)
  assert load_doc(path) == parse_template_yaml(repaired)


@pytest.mark.parametrize(
  ("text", "column", "cure"),
  [
    ("a: 1\n{a: 2}\n", 0, "'{a: 2}'"),
    ("a: 1\n  {a: 2}\n", 2, "'{a: 2}'"),
    ("- {a: 2}\n{b: 3}\n", 0, "'{b: 3}'"),
  ],
)
def test_real_owned_error_at_opening_brace_keeps_zero_based_column(tmp_path, text, column, cure):
  path = tmp_path / "settings.yaml"
  path.write_text(text)
  with pytest.raises(ConfigError) as caught:
    load_doc(path)
  cause = caught.value.__cause__
  assert isinstance(cause, AkanError)
  assert (cause.line, cause.col) == (2, column)
  assert text.splitlines()[cause.line - 1][cause.col] == "{"
  message = str(caught.value)
  assert f"{path}:2:{column}:" in message
  assert str(cause) in message
  assert "a value starting with '{' must be quoted:" in message
  assert cure in message
  assert path.read_text() == text


@pytest.mark.parametrize("text", ["a: b: c\n", "safe: '{$HOME}/x'\nbad: ]\n"])
def test_a_yaml_error_elsewhere_gets_no_brace_hint(tmp_path, text) -> None:
  path = tmp_path / "settings.yaml"
  path.write_text(text)
  with pytest.raises(ConfigError) as ei:
    load_doc(path)
  assert "must be quoted" not in str(ei.value)


@pytest.mark.parametrize(
  ("value", "hint"),
  [
    ({"$HOME": None}, "write '{$HOME}'"),
    ({"box.image": None}, "write '{box.image}'"),
    ({"a b": None}, ""),
    ({"$HOME": "x"}, ""),
    ({"$A": None, "$B": None}, ""),
  ],
)
def test_the_hint_is_given_only_for_a_reference_read_as_a_map(value, hint) -> None:
  got = unquoted_reference_hint(value)
  assert (hint in got) if hint else got == "", got


@pytest.mark.parametrize("category", ["env", "secret_path", "path"])
def test_a_family_value_read_as_a_map_says_it_was_unquoted(category) -> None:
  with pytest.raises(SettingsError) as ei:
    refuse_non_scalar_family_value("system.env.FOO", category, {"$HOME": None})
  msg = str(ei.value)
  assert msg.startswith("system.env.FOO holds a map"), msg
  assert "read as a map because it was unquoted: write '{$HOME}'." in msg, msg


def test_a_path_key_read_as_a_map_names_file_key_and_quoted_form(tmp_path) -> None:
  path = tmp_path / "settings.yaml"
  with pytest.raises(ConfigError) as ei:
    system_table_set_values(path, {"system": {"canon": {"$XDG_DATA_HOME": None}}})
  msg = str(ei.value)
  assert msg.startswith("system.canon holds a map"), msg
  assert "write '{$XDG_DATA_HOME}'" in msg and msg.endswith(f"(in {path})"), msg


def test_an_agent_scalar_leaf_read_as_a_map_says_it_was_unquoted() -> None:
  path = Path("/x/settings.yaml")
  with pytest.raises(SettingsError) as ei:
    _refuse_table_at_scalar_leaf(
      {"default": {"model": {"$AGENT": None}}}, prefix="agent", path=path
    )
  msg = str(ei.value)
  assert str(path) in msg, msg
  assert "  - agent.default.model — It was read as a map because it was unquoted: " in msg, msg


def test_a_workset_early_key_read_as_a_map_names_its_file() -> None:
  path = Path("/x/settings.yaml")
  with pytest.raises(SettingsError) as ei:
    early_tier({"workset": {"logs": {"meta.workset.path": None}}}, where=path)
  msg = str(ei.value)
  assert "write '{meta.workset.path}'" in msg and msg.endswith(f"(in {path})"), msg
