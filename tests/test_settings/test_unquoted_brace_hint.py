"""An unquoted ``{``-leading value is a YAML flow mapping: the refusal says to quote it.

``key: {$HOME}/x`` fails to parse; ``key: {$HOME}`` parses as the map ``{"$HOME": None}``
and fails a later shape check. Both name the file and key, and show the quoted form.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.settings.config import system_table_set_values
from kanibako.settings.config_io import load_doc
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
  path = tmp_path / "settings.yaml"
  path.write_text(text)
  with pytest.raises(ConfigError) as ei:
    load_doc(path)
  msg = str(ei.value)
  assert str(path) in msg and "(line " in msg, msg
  assert f"a value starting with '{{' must be quoted: {cure}." in msg, msg


def test_a_yaml_error_elsewhere_gets_no_brace_hint(tmp_path) -> None:
  path = tmp_path / "settings.yaml"
  path.write_text("a: b: c\n")
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
