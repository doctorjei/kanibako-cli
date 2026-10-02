"""``core_defaults`` parses ``core-defaults.yaml`` once per process and copies out per call (P8)."""

from __future__ import annotations

import pytest
import yaml

from kanibako.settings import core_defaults
from kanibako.settings.settings_launch import load_merged_config
from kanibako.settings.settings_resolve import GUEST_HOME


@pytest.fixture
def cold_parse():
  """A cold parse cache on both sides, so no test inherits or leaves a parse."""
  core_defaults._parse_doc.cache_clear()
  yield
  core_defaults._parse_doc.cache_clear()


def test_repeated_resolves_parse_the_shipped_file_once(cold_parse, tmp_path, monkeypatch):
  """Three resolves, each reading the core defaults, parse the file ONCE between them.

  MUTATION: bypass the cache (``_parse_doc.__wrapped__()``) and the count is 3.
  """
  calls: list[str] = []

  def counting_parse(text: str) -> object:
    calls.append(text)
    return yaml.safe_load(text)

  monkeypatch.setattr(core_defaults, "parse_packaged", counting_parse)
  for _ in range(3):
    load_merged_config(None)
  assert len(calls) == 1


def test_mutating_a_returned_doc_does_not_reach_the_next(cold_parse):
  """A caller that clobbers a NESTED table of its copy leaves the next reader's intact."""
  first = core_defaults._load_doc()
  assert core_defaults._load_doc() is not first
  row = first["core"][0]
  original = dict(row)
  row["box_dest"] = "/clobbered"
  first["core"].clear()
  first["agent_default"] = {}

  second = core_defaults._load_doc()
  assert second["core"][0] == original
  assert second["agent_default"]
  assert core_defaults._parse_doc()["core"][0] == original


def test_env_floor_expands_a_guest_home_value_and_leaves_the_rest(monkeypatch):
  """A ``$GUEST_HOME/…`` env value comes back as a guest path; a ``$VAR`` value is left for the snapshot."""
  doc = {"env": {"agent.shell": {"FINAL": "$GUEST_HOME/AGENTS.md", "TERM": "$TERM"}}}
  monkeypatch.setattr(core_defaults, "_load_doc", lambda: doc)
  assert core_defaults.env_default_categories() == {
    "agent.shell.env.FINAL": f"{GUEST_HOME}/AGENTS.md",
    "agent.shell.env.TERM": "$TERM",
  }


def test_the_shipped_shell_final_comes_back_as_agents_md():
  """The shell tier's FINAL slot is declared ``$GUEST_HOME/AGENTS.md`` and emitted expanded."""
  raw = core_defaults._load_doc()["env"]
  assert raw["agent.shell"]["KANIBAKO_DIRECTIVE_FINAL"] == "$GUEST_HOME/AGENTS.md"
  emitted = core_defaults.env_default_categories()
  assert emitted["agent.shell.env.KANIBAKO_DIRECTIVE_FINAL"] == "/home/agent/AGENTS.md"
  # It is the one shipped ``$GUEST_HOME`` row; every other value passes through.
  for scope, entries in raw.items():
    for var, value in entries.items():
      if var != "KANIBAKO_DIRECTIVE_FINAL":
        assert emitted[f"{scope}.env.{var}"] == value
