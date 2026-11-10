import pytest
from unittest.mock import Mock

from kanibako.settings import config_keys
from kanibako.settings.agent_config import DEFAULT_ROOT_LABEL
from kanibako.settings.settings_prefs import AgentNames


@pytest.fixture(autouse=True)
def no_pg(monkeypatch):
  for name in ("read_registered_persona", "read_persona_bundle", "resolve_secret_path"):
    monkeypatch.setattr(
      f"kanibako.persona_store.{name}", Mock(side_effect=AssertionError("PG read"))
    )
  for name in ("configure", "commit", "create", "update", "rename", "unregister"):
    monkeypatch.setattr(
      f"persona_grata.AgentStore.{name}", Mock(side_effect=AssertionError("PG write"))
    )


@pytest.mark.parametrize(
  "key,expected,path",
  [
    ("agent.claude.provider", ("claude", "provider"), False),
    ("agent.claude.claude_only", None, False),
    ("agent.claude.unknown", None, False),
    ("agent.claude.template", ("claude", "template"), True),
    ("agent.Nav+goose.provider", ("Nav+goose", "provider"), False),
    ("agent.claude.env.LOCAL", ("claude", "env.LOCAL"), False),
    ("agent.claude.env.1BAD", None, False),
    ("agent.claude.env.", None, False),
    ("agent.bad+ref+claude.provider", None, False),
  ],
)
def test_local_vocabulary_judges_persona_paths(monkeypatch, key, expected, path):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
    agent_harnesses={"claude": "goose"},
  )
  monkeypatch.setattr(
    config_keys, "plugin_declared_leaf_map", Mock(side_effect=AssertionError("rediscovery"))
  )
  judge = Mock(wraps=config_keys.agent_leaf_is_declared)
  monkeypatch.setattr(config_keys, "agent_leaf_is_declared", judge)
  assert config_keys._parse_persona_agent_key(key, valid_agents=names) == expected
  assert config_keys.is_path_valued_key(key, valid_agents=names) is path
  if expected is not None and ".env." not in key:
    anchor, label = config_keys.path_key_anchor(key, valid_agents=names)
    assert anchor == "{meta.agent." + expected[0] + ".path}"
    assert label == DEFAULT_ROOT_LABEL
  assert all(call.args[2] is names.leaf_map for call in judge.call_args_list)


@pytest.mark.parametrize("missing", [False, True])
def test_empty_and_unreadable_vocabularies(monkeypatch, missing):
  names = AgentNames(
    (), unreadable={"missing"} if missing else (), agent_harnesses={"claude": "missing"}
  )
  monkeypatch.setattr(
    config_keys, "plugin_declared_leaf_map", Mock(side_effect=AssertionError("rediscovery"))
  )
  key = "agent.claude.unknown"
  assert config_keys._parse_persona_agent_key(key, valid_agents=names) == (
    ("claude", "unknown") if missing else None
  )
  assert config_keys.is_path_valued_key("agent.claude.template", valid_agents=names)
  assert not config_keys.is_path_valued_key(key, valid_agents=names)
  if missing:
    assert config_keys.path_key_anchor(key, valid_agents=names)[0] == "{meta.agent.claude.path}"


def test_omission_keeps_discovery_lazy(monkeypatch):
  names = AgentNames({"goose"}, leaf_map={"goose": {"provider"}})
  discovery = Mock(return_value=names)
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", discovery)
  assert config_keys.is_path_valued_key("agent.goose.template")
  assert config_keys.path_key_anchor("agent.goose.template")[0] == "{meta.agent.goose.path}"
  assert config_keys._parse_persona_agent_key("agent.goose.model") == ("goose", "model")
  discovery.assert_not_called()
  assert config_keys._parse_persona_agent_key("agent.goose.provider") == ("goose", "provider")
  discovery.assert_called()


@pytest.mark.parametrize("verb", ["set", "reset", "read"])
@pytest.mark.parametrize("node", ["claude", "fleet", "Nav℘goose"])
def test_agent_noun_gates_forward_local_vocabulary(monkeypatch, verb, node):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
    agent_harnesses={"claude": "goose", "fleet": "goose"},
  )
  monkeypatch.setattr(
    config_keys, "plugin_declared_leaf_map", Mock(side_effect=AssertionError("rediscovery"))
  )
  judge = Mock(wraps=config_keys.agent_key_reason)
  monkeypatch.setattr(config_keys, "agent_key_reason", judge)
  for leaf, accepted in (("provider", True), ("claude_only", False), ("unknown", False)):
    if verb == "read":
      error = config_keys.agent_read_key_error(node, leaf, valid_agents=names)
    else:
      error = config_keys.agent_write_key_error(node, leaf, verb=verb, valid_agents=names)
    assert (error is None) is accepted
    if error is not None:
      assert f"cannot be {verb}" in error
      assert leaf in error
  assert all(call.kwargs["valid_agents"] is names for call in judge.call_args_list)


@pytest.mark.parametrize("missing", [False, True])
def test_agent_noun_gates_keep_empty_and_unreadable_maps(monkeypatch, missing):
  names = AgentNames(
    (), unreadable={"missing"} if missing else (), agent_harnesses={"claude": "missing"}
  )
  monkeypatch.setattr(
    config_keys, "plugin_declared_leaf_map", Mock(side_effect=AssertionError("rediscovery"))
  )
  assert (
    config_keys.agent_read_key_error("claude", "unknown", valid_agents=names) is None
  ) is missing
  assert (
    config_keys.agent_write_key_error("claude", "unknown", verb="set", valid_agents=names) is None
  ) is missing
  assert config_keys.agent_read_key_error("claude", "model", valid_agents=names) is None
  assert config_keys.agent_read_key_error("claude", "self.model", valid_agents=names) is not None
  empty = AgentNames(())
  assert config_keys.agent_read_key_error("claude", "unknown", valid_agents=empty) is not None
  assert (
    config_keys.agent_write_key_error("claude", "unknown", verb="set", valid_agents=empty)
    is not None
  )
  assert config_keys.agent_read_key_error("claude", "model", valid_agents=empty) is None


@pytest.mark.parametrize(
  "key,scope",
  [
    ("agent.claude.provider", config_keys.ConfigLevel.system),
    ("agent.CLAUDE.provider", config_keys.ConfigLevel.system),
    ("agent.Fleet.provider", config_keys.ConfigLevel.system),
    ("agent.Nav+GOOSE.provider", config_keys.ConfigLevel.system),
    ("pref.agent.claude.provider", config_keys.ConfigLevel.box),
  ],
)
def test_scope_read_gate_forwards_one_vocabulary(monkeypatch, key, scope):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
    agent_harnesses={"claude": "goose", "fleet": "goose"},
  )
  rediscovery = Mock(side_effect=AssertionError("rediscovery"))
  monkeypatch.setattr(config_keys, "plugin_declared_leaf_map", rediscovery)
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", rediscovery)
  canonicalize = Mock(wraps=config_keys.resolve_key)
  judge = Mock(wraps=config_keys.scope_key_reason)
  monkeypatch.setattr(config_keys, "resolve_key", canonicalize)
  monkeypatch.setattr(config_keys, "scope_key_reason", judge)
  assert config_keys.scope_read_key_error(key, scope, valid_agents=names) is None
  assert canonicalize.call_args.kwargs["valid_agents"] is names
  assert judge.call_args.kwargs["valid_agents"] is names
  refused = key.removesuffix("provider") + "claude_only"
  assert config_keys.scope_read_key_error(refused, scope, valid_agents=names) is not None


@pytest.mark.parametrize("missing", [False, True])
def test_scope_read_gate_keeps_empty_and_unreadable_maps(monkeypatch, missing):
  names = AgentNames(
    (), unreadable={"missing"} if missing else (), agent_harnesses={"claude": "missing"}
  )
  rediscovery = Mock(side_effect=AssertionError("rediscovery"))
  monkeypatch.setattr(config_keys, "plugin_declared_leaf_map", rediscovery)
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", rediscovery)
  assert (
    config_keys.scope_read_key_error(
      "agent.claude.unknown", config_keys.ConfigLevel.system, valid_agents=names
    )
    is None
  ) is missing
  empty = AgentNames(())
  assert (
    config_keys.scope_read_key_error(
      "agent.claude.model", config_keys.ConfigLevel.system, valid_agents=empty
    )
    is not None
  )


@pytest.mark.parametrize("supplied", [False, True])
def test_gate_omission_and_read_cures_remain_unchanged(monkeypatch, supplied):
  names = AgentNames({"goose"}, leaf_map={"goose": {"provider"}})
  discovery = Mock(return_value=names)
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", discovery)
  kwargs = {"valid_agents": names} if supplied else {}
  assert config_keys.agent_read_key_error("goose", "model", **kwargs) is None
  assert (
    config_keys.scope_read_key_error("model", config_keys.ConfigLevel.system, **kwargs) is None
  )
  discovery.assert_not_called()
  assert config_keys.agent_read_key_error("goose", "provider", **kwargs) is None
  assert (
    config_keys.scope_read_key_error(
      "agent.goose.provider", config_keys.ConfigLevel.system, **kwargs
    )
    is None
  )
  assert bool(discovery.call_count) is (not supplied)
  assert config_keys.agent_read_key_error("goose", "bindings.ro.old", **kwargs) is None
  assert (
    config_keys.scope_read_key_error(
      "agent.goose.bindings.ro.old", config_keys.ConfigLevel.system, **kwargs
    )
    is None
  )
  assert (
    config_keys.agent_write_key_error("goose", "bindings.ro.old", verb="set", **kwargs) is not None
  )
  table_error = config_keys.scope_read_key_error(
    "transform_settings", config_keys.ConfigLevel.system, active_agent="goose", **kwargs
  )
  assert table_error is not None
  assert "kanibako agent get goose transform_settings" in table_error
  category_error = config_keys.scope_read_key_error(
    "agent.goose.caches", config_keys.ConfigLevel.system, **kwargs
  )
  assert category_error is not None
  assert "kanibako agent get goose caches" in category_error
