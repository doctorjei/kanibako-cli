"""Local vocabulary reaches both agent-file judgments without discovery."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from kanibako.settings.agent_file import _undeclared_entries, level_table, record
from kanibako.settings.config_keys import agent_key_reason
from kanibako.settings.settings_prefs import AgentNames
from kanibako.settings.settings_resolve import SettingsError


@pytest.fixture
def vocabulary(monkeypatch):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
  ).with_harnesses({"claude": "goose"})
  for supplier in (
    "kanibako.settings.settings_prefs.default_valid_agents",
    "kanibako.settings.settings_keyspace_probe.plugin_agent_leaf_map",
  ):
    monkeypatch.setattr(supplier, Mock(side_effect=AssertionError("unexpected discovery")))
  return names


@pytest.mark.parametrize("spelling", ("self", "agent"))
def test_registered_node_uses_its_harness_under_either_spelling(spelling, vocabulary):
  table = {"provider": "ollama", "model": None}
  raw = {"self": table} if spelling == "self" else {"agent": {"claude": table}}
  before = deepcopy(raw)
  level = level_table(raw, sub_key="claude", node="claude")
  cfg = record(level, node="claude", valid_agents=vocabulary)
  assert cfg.state == table
  cfg.state["provider"] = "changed"
  assert raw == before
  assert vocabulary.leaf_map.agent_harnesses == {"claude": "goose"}
  assert vocabulary.leaf_map["claude"] == frozenset({"claude_only"})


@pytest.mark.parametrize("spelling", ("self", "agent"))
def test_foreign_plugin_leaf_is_refused_in_each_pass(spelling, vocabulary):
  table = {"claude_only": "foreign"}
  raw = {"self": table} if spelling == "self" else {"agent": {"claude": table}}
  level = level_table(raw, sub_key="claude", node="claude")
  entries = list(
    _undeclared_entries(level.state, level.scope, node="claude", valid_agents=vocabulary)
  )
  spelled = "self.claude_only" if spelling == "self" else "agent.claude.claude_only"
  # The scalar is visited once by each existing pass, before reporting deduplicates it.
  assert [entry[1] for entry in entries] == [spelled, spelled]
  assert all("not a declared agent key" in entry[2] for entry in entries)
  with pytest.raises(SettingsError) as exc:
    record(level, node="claude", valid_agents=vocabulary)
  message = str(exc.value)
  assert "claude_only" in message
  assert f"remove `{spelled}`" in message
  assert "kanibako agent reset claude --all" in message
  assert "has 2 entries" not in message


@pytest.mark.parametrize("node", ("bad.name", "self"))
def test_malformed_or_alias_scope_node_refuses(node, vocabulary):
  level = level_table({"agent": {node: {"model": "x"}}}, sub_key="claude", node="claude")
  with pytest.raises(SettingsError) as exc:
    record(level, node="claude", valid_agents=vocabulary)
  assert node in str(exc.value)
  assert "not a settings key" in str(exc.value)


@pytest.mark.parametrize("tail", ("self.model", "model.extra", "provider.extra"))
def test_dotted_root_leaf_refuses(tail, vocabulary):
  level = level_table({"self": {tail: "x"}}, sub_key="claude", node="claude")
  with pytest.raises(SettingsError) as exc:
    record(level, node="claude", valid_agents=vocabulary)
  assert tail in str(exc.value)
  assert f"`self.{tail}`" in str(exc.value)


@pytest.mark.parametrize("category", ("env", "secret_path"))
@pytest.mark.parametrize("var", ("", "1BAD", "BAD-NAME", "get"))
def test_invalid_var_refuses_in_whole_path_pass(category, var, vocabulary):
  level = level_table({"self": {category: {var: "/x"}}}, sub_key="claude", node="claude")
  with pytest.raises(SettingsError) as exc:
    record(level, node="claude", valid_agents=vocabulary)
  message = str(exc.value)
  assert category in message
  assert f"`self.{category}.{var}`" in message
  assert "not a settings key" in message


def test_terminal_category_destinations_are_data(vocabulary):
  tables = {
    "bindings": {"ro": {"~/.claude/config.json": ["/host/config.json"]}},
    "caches": {"~/.cache/a.b": ["/host/cache"]},
    "masks": {"~/.hidden.with.dots": True},
    "transform_settings": {"foreign.plugin.leaf": {"anything": True}},
  }
  level = level_table({"self": tables}, sub_key="claude", node="claude")
  cfg = record(level, node="claude", valid_agents=vocabulary)
  assert cfg.category_tables == {k: v for k, v in tables.items() if k != "transform_settings"}
  assert cfg.transform_settings == tables["transform_settings"]


def test_sibling_nodes_keep_their_own_harness_vocabulary(vocabulary):
  level = level_table(
    {"self": {"provider": "own"}, "agent": {"nav℘claude": {"claude_only": "sibling"}}},
    sub_key="claude",
    node="claude",
  )
  assert record(level, node="claude", valid_agents=vocabulary).state == {"provider": "own"}
  level = level_table(
    {"agent": {"nav℘claude": {"provider": "foreign"}}}, sub_key="claude", node="claude"
  )
  with pytest.raises(SettingsError) as exc:
    record(level, node="claude", valid_agents=vocabulary)
  assert "carries 'agent.nav℘claude.provider'" in str(exc.value)
  assert "of 'agent.nav+claude'" in str(exc.value)


def test_empty_local_discovery_does_not_fall_back_or_rejudge_node(vocabulary):
  names = AgentNames(())
  level = level_table({"self": {"model": "x"}}, sub_key="claude", node="claude")
  assert record(level, node="claude", valid_agents=names).state == {"model": "x"}
  assert agent_key_reason("claude", "provider", valid_agents=names) is not None
  level = level_table({"self": {"provider": "x"}}, sub_key="claude", node="claude")
  with pytest.raises(SettingsError, match="provider"):
    record(level, node="claude", valid_agents=names)


@pytest.mark.parametrize("node", ("claude", "nav℘missing"))
def test_unreadable_harness_concedes_only_leaf_vocabulary(node, vocabulary):
  names = AgentNames((), unreadable={"missing"}, agent_harnesses={"claude": "missing"})
  level = level_table({"self": {"unknown_leaf": "x"}}, sub_key=node, node=node)
  assert record(level, node=node, valid_agents=names).state == {"unknown_leaf": "x"}
  bad = level_table({"self": {"env": {"1BAD": "x"}}}, sub_key=node, node=node)
  with pytest.raises(SettingsError, match="1BAD"):
    record(bad, node=node, valid_agents=names)


@pytest.mark.parametrize("node", ("bad-name", "1bad"))
def test_known_good_node_membership_matches_legacy(node, vocabulary):
  # The caller vouches for the file node; this judge does not validate a typed name.
  level = level_table({"agent": {node: {"model": "x"}}}, sub_key="claude", node="claude")
  assert record(level, node="claude").state == {}
  assert record(level, node="claude", valid_agents=vocabulary).state == {}


@pytest.mark.parametrize("category", ("env", "secret_path"))
def test_deferred_dotted_var_matches_legacy(category, vocabulary):
  # Dotted VAR treatment is deliberately deferred by the whole-path owner.
  level = level_table({"self": {category: {"BAD.NAME": "/x"}}}, sub_key="claude", node="claude")
  legacy = record(level, node="claude")
  local = record(level, node="claude", valid_agents=vocabulary)
  assert getattr(legacy, category) == {"BAD.NAME": "/x"}
  assert getattr(local, category) == {"BAD.NAME": "/x"}
