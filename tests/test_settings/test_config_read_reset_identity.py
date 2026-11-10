from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.errors import ConfigError
from kanibako.settings import config_interface as ci
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.config import AgentResolution
from kanibako.settings.config_io import dump_doc, load_doc, render_stored_scalar
from kanibako.settings.config_keys import ConfigLevel, agent_node_of, scope_read_key_error
from kanibako.settings.settings_prefs import AgentNames
from kanibako.settings.settings_resolve import SettingsError


@pytest.fixture
def vocabulary(monkeypatch):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
  )
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", lambda: names)
  return names


@pytest.fixture
def identity(tmp_path):
  return AgentIdentity("claude", "Claude", "Guide", "Goose", "goose", tmp_path / "PG" / "Claude")


@pytest.fixture(autouse=True)
def no_pg(monkeypatch):
  for name in ("read_registered_persona", "read_persona_bundle", "resolve_secret_path"):
    monkeypatch.setattr(
      f"kanibako.persona_store.{name}", Mock(side_effect=AssertionError("PG is inactive"))
    )
  for name in ("configure", "commit", "create", "update", "rename", "unregister"):
    monkeypatch.setattr(
      f"persona_grata.AgentStore.{name}", Mock(side_effect=AssertionError("PG write"))
    )


def _stores(tmp_path, value, *, leaf="provider", poisoned=False):
  agents = tmp_path / "agents"
  (agents / "claude").mkdir(parents=True)
  node = agent_settings_path(agents, "claude")
  own = {leaf: value}
  if poisoned:
    own["invalid_for_any_harness"] = "repairable"
  dump_doc(node, {"self": own})
  noun = tmp_path / "settings.yaml"
  dump_doc(noun, {"agent": {"claude": {leaf: "noun-value"}}})
  return agents, node, noun


def _get(tmp_path, agents, noun, key, identity=None, **kwargs):
  return ci.get_config_value(
    key,
    global_config_path=tmp_path / "kanibako.cfg",
    system_settings_path=noun,
    agents_root=agents,
    command_scope=ConfigLevel.system,
    active_agent="claude",
    cascade_agent_identity=identity,
    **kwargs,
  )


def _reset(tmp_path, agents, noun, key, identity=None, **kwargs):
  return ci.reset_config_value(
    key,
    config_path=tmp_path / "kanibako.cfg",
    system_settings_path=noun,
    agents_root=agents,
    command_scope=ConfigLevel.system,
    cascade_agent_name="claude",
    cascade_agent_identity=identity,
    **kwargs,
  )


@pytest.mark.parametrize("value", ["ollama", "", None])
@pytest.mark.parametrize("node_store", [False, True])
@pytest.mark.parametrize("poisoned", [False, True])
def test_registered_get_reset_preserve_file_placement_and_repair(
  tmp_path, identity, vocabulary, value, node_store, poisoned
):
  agents, node, noun = _stores(tmp_path, value, poisoned=poisoned)
  node_before, noun_before = node.read_bytes(), noun.read_bytes()
  expected = render_stored_scalar(value if node_store else "noun-value")
  assert (
    _get(tmp_path, agents, noun, "agent.ClAuDe.provider", identity, node_store=node_store)
    == expected
  )
  assert node.read_bytes() == node_before
  assert noun.read_bytes() == noun_before
  message = _reset(
    tmp_path, agents, noun, "agent.ClAuDe.provider", identity, node_store=node_store
  )
  assert message.startswith("Cleared agent.claude.provider"), message
  assert "provider" not in (
    load_doc(node).get("self", {})
    if node_store
    else load_doc(noun).get("agent", {}).get("claude", {})
  )
  assert (noun if node_store else node).read_bytes() == (
    noun_before if node_store else node_before
  )
  if poisoned:
    assert load_doc(node)["self"]["invalid_for_any_harness"] == "repairable"
  assert vocabulary.leaf_map.agent_harnesses == {}
  assert not identity.entry_path.exists()


@pytest.mark.parametrize("value", ["ollama", "", None])
@pytest.mark.parametrize("scope", [ConfigLevel.workset, ConfigLevel.box])
def test_pref_get_reset_keep_request_emptiness_and_noun_file(
  tmp_path, identity, vocabulary, value, scope
):
  path = tmp_path / "settings.yaml"
  dump_doc(path, {"pref": {"agent": {"claude": {"provider": value}}}})
  assert ci.get_config_value(
    "pref.agent.ClAuDe.provider",
    global_config_path=path,
    project_toml=path,
    command_scope=scope,
    cascade_agent_identity=identity,
  ) == render_stored_scalar(value)
  message = ci.reset_config_value(
    "pref.agent.ClAuDe.provider",
    config_path=path,
    command_scope=scope,
    cascade_agent_identity=identity,
  )
  assert message == "Cleared pref.agent.claude.provider"
  assert load_doc(path) == {}
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.parametrize("pref", [False, True])
def test_foreign_leaf_reset_refuses_without_removal(tmp_path, identity, vocabulary, pref):
  path = tmp_path / "settings.yaml"
  doc = {"agent": {"claude": {"claude_only": "keep"}}}
  dump_doc(path, {"pref": doc} if pref else doc)
  before = path.read_bytes()
  message = ci.reset_config_value(
    ("pref." if pref else "") + "agent.claude.claude_only",
    config_path=path,
    system_settings_path=None if pref else path,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    agents_root=tmp_path / "agents",
    node_store=False,
    cascade_agent_identity=identity,
  )
  assert message.startswith("Error:"), message
  assert "claude_only" in message
  assert path.read_bytes() == before


@pytest.mark.parametrize("pref", [False, True])
def test_explicit_empty_vocabulary_does_not_rediscover(tmp_path, identity, monkeypatch, pref):
  names = AgentNames(())
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("discovery")),
  )
  monkeypatch.setattr(
    "kanibako.settings.config_keys.plugin_declared_leaf_map",
    Mock(side_effect=AssertionError("leaf discovery")),
  )
  path = tmp_path / "settings.yaml"
  dump_doc(path, {"box": {"env": {"KEEP": "raw"}}})
  before = path.read_bytes()
  key = ("pref." if pref else "") + "agent.claude.provider"
  assert (
    ci.get_config_value(
      key,
      global_config_path=path,
      project_toml=path,
      command_scope=ConfigLevel.box if pref else ConfigLevel.system,
      cascade_agent_identity=identity,
    )
    is None
  )
  message = ci.reset_config_value(
    key,
    config_path=path,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    cascade_agent_identity=identity,
  )
  assert message.startswith("Error:"), message
  assert "provider" in message
  assert path.read_bytes() == before


@pytest.mark.parametrize("pref", [False, True])
def test_missing_plugin_concedes_its_leaf_for_get_reset(tmp_path, identity, monkeypatch, pref):
  names = AgentNames((), unreadable={"missing"}, agent_harnesses={"claude": "missing"})
  missing = replace(identity, harness="Missing", target_name="missing")
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  agents, node, noun = _stores(tmp_path, "kept", leaf="unknown_leaf")
  if pref:
    dump_doc(noun, {"pref": {"agent": {"claude": {"unknown_leaf": None}}}})
    value = ci.get_config_value(
      "pref.agent.claude.unknown_leaf",
      global_config_path=noun,
      project_toml=noun,
      command_scope=ConfigLevel.box,
      cascade_agent_identity=missing,
    )
    message = ci.reset_config_value(
      "pref.agent.claude.unknown_leaf",
      config_path=noun,
      command_scope=ConfigLevel.box,
      cascade_agent_identity=missing,
    )
    assert value == "null"
    assert message == "Cleared pref.agent.claude.unknown_leaf"
    assert load_doc(noun) == {}
  else:
    assert _get(tmp_path, agents, noun, "agent.claude.unknown_leaf", missing) == "kept"
    message = _reset(tmp_path, agents, noun, "agent.claude.unknown_leaf", missing)
    assert message.startswith("Cleared agent.claude.unknown_leaf"), message
    assert load_doc(node) == {}


@pytest.mark.parametrize("legacy", [False, True])
def test_native_and_legacy_keep_none_vocabulary(tmp_path, identity, legacy):
  native = replace(identity, registered_name=None, target_name="claude", harness="Claude")
  agents, node, noun = _stores(tmp_path, "model-value", leaf="model")
  picked = native if legacy else None
  assert ci.identity_valid_agents(picked) is None
  assert _get(tmp_path, agents, noun, "agent.claude.model", picked) == "model-value"
  assert _reset(tmp_path, agents, noun, "agent.claude.model", picked).startswith(
    "Cleared agent.claude.model"
  )
  assert load_doc(node) == {}


def test_get_forwards_one_vocabulary_and_identity_to_defect_judges(
  tmp_path, identity, vocabulary, monkeypatch
):
  names = ci.identity_valid_agents(identity)
  supplier = Mock(return_value=names)
  monkeypatch.setattr(ci, "identity_valid_agents", supplier)
  target = object()
  target_builder = Mock(return_value=target)
  defect_judge = Mock(return_value={})
  monkeypatch.setattr(ci, "_set_time_target", target_builder)
  monkeypatch.setattr(ci, "_set_time_defects", defect_judge)
  scans = []

  def scan(*args, **kwargs):
    scans.append(kwargs)
    assert kwargs["defects"]() == {}
    return ci._BadEntries([], [], lambda _key: None, [])

  monkeypatch.setattr(ci, "_cascade_bad_entries", scan)
  path = tmp_path / "settings.yaml"
  dump_doc(path, {"system": {"env": {"KEEP": "raw"}}})
  assert (
    ci.get_config_value(
      "system.env.KEEP",
      global_config_path=tmp_path / "kanibako.cfg",
      system_settings_path=path,
      command_scope=ConfigLevel.system,
      active_agent=identity.node,
      std=SimpleNamespace(agents=tmp_path / "agents"),
      cascade_agent_identity=identity,
    )
    == "raw"
  )
  supplier.assert_called_once_with(identity)
  assert scans[0]["valid_agents"] is names
  assert target_builder.call_args.kwargs["agent_identity"] is identity
  assert defect_judge.call_args.kwargs["target"] is target
  assert defect_judge.call_args.kwargs["valid_agents"] is names


@pytest.mark.parametrize("failed", [False, True])
def test_reset_confirmation_receives_vocabulary_after_removal(
  tmp_path, identity, vocabulary, monkeypatch, failed
):
  names = ci.identity_valid_agents(identity)
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  path = tmp_path / "box.yaml"
  dump_doc(path, {"box": {"image": "old-image"}})

  def effective(*args, **kwargs):
    assert load_doc(path) == {}
    assert kwargs["valid_agents"] is names
    if failed:
      raise SettingsError("synthetic broken cascade")
    return "lower-image", "workset"

  confirmation = Mock(side_effect=effective)
  monkeypatch.setattr(ci, "effective_value", confirmation)
  message = ci.reset_config_value(
    "box.image",
    config_path=path,
    command_scope=ConfigLevel.box,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    cascade_box_path=path,
  )
  confirmation.assert_called_once()
  assert message.startswith("Cleared box.image"), message
  if failed:
    assert "falls back through the cascade" in message
  else:
    assert "effective is now lower-image (workset)" in message
  assert load_doc(path) == {}


@pytest.mark.parametrize("pref", [False, True])
@pytest.mark.parametrize("name", ["ClAuDe", "Case+GOOSE", "Case℘GOOSE", "Registered", "Shell"])
def test_addressed_identity_resolves_raw_node_once(identity, monkeypatch, pref, name):
  resolver = Mock(return_value=AgentResolution(identity))
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("discovery")),
  )
  key = ("pref." if pref else "") + f"agent.{name}.provider"
  assert ci.addressed_agent_identity(key) is identity
  resolver.assert_called_once_with(name)


@pytest.mark.parametrize(
  "key",
  [
    "agent.default.model",
    "agent.Default.model",
    "agent.self.model",
    "agent..model",
    "agent.bad-name.model",
    "agent.1bad.model",
    "agent.Case++GOOSE.model",
    "agent.claude",
    "agent",
    "pref.agent.claude",
    "pref.pref.agent.claude.model",
    "pref.meta.agent.claude.name",
    "meta.agent.claude.name",
    "system.agent",
    "pref.system.agent",
    "box.agent.model",
    "model",
    "",
  ],
)
def test_addressed_identity_leaves_unroutable_key_to_gate(monkeypatch, key):
  resolver = Mock(side_effect=AssertionError("unroutable identity lookup"))
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  assert ci.addressed_agent_identity(key) is None
  resolver.assert_not_called()


def test_addressed_identity_preserves_resolver_error(monkeypatch):
  error = ConfigError("synthetic unreadable registration")
  resolver = Mock(side_effect=error)
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  with pytest.raises(ConfigError) as caught:
    ci.addressed_agent_identity("pref.agent.Registered.provider")
  assert caught.value is error
  resolver.assert_called_once_with("Registered")


@pytest.mark.parametrize("pref", [False, True])
@pytest.mark.parametrize("scope", [ConfigLevel.system, ConfigLevel.workset])
@pytest.mark.parametrize("kind", ["readable", "empty", "conceding"])
@pytest.mark.parametrize("leaf", ["provider", "foreign_leaf", "self.model", ""])
def test_addressed_identity_never_certifies_leaf(identity, monkeypatch, pref, scope, kind, leaf):
  registered = replace(identity, node="registered", registered_name="Registered")
  names = AgentNames(
    {"goose"} if kind == "readable" else (),
    leaf_map={"goose": {"provider"}} if kind == "readable" else {},
    unreadable={"goose"} if kind == "conceding" else (),
  )
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", lambda: names)
  resolver = Mock(return_value=AgentResolution(registered))
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  key = ("pref." if pref else "") + f"agent.Registered.{leaf}"
  addressed = ci.addressed_agent_identity(key)
  assert addressed is registered
  resolver.assert_called_once_with("Registered")
  local = ci.identity_valid_agents(addressed)
  assert local is not None
  assert names.leaf_map.agent_harnesses == {}
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("rediscovery")),
  )
  error = scope_read_key_error(key, scope, valid_agents=local)
  allowed = leaf in {"provider", "foreign_leaf"} and (
    kind == "conceding" or (kind == "readable" and leaf == "provider")
  )
  assert (error is None) is allowed, error
  target = key.removeprefix("pref.")
  if leaf in {"provider", "foreign_leaf"}:
    assert bool(agent_node_of(target, valid_agents=local)) is allowed
  if error is not None:
    assert key in error


@pytest.mark.parametrize("scope", [ConfigLevel.system, ConfigLevel.workset])
def test_addressed_identity_preserves_retired_bind_read_gate(
  identity, vocabulary, monkeypatch, scope
):
  resolver = Mock(return_value=AgentResolution(identity))
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  key = "agent.ClAuDe.bindings.ro./tmp/a.b"
  addressed = ci.addressed_agent_identity(key)
  assert addressed is identity
  resolver.assert_called_once_with("ClAuDe")
  local = ci.identity_valid_agents(addressed)
  error = scope_read_key_error(key, scope, valid_agents=local)
  assert (error is None) is (scope is ConfigLevel.system), error
  if error is not None:
    assert "TERMINAL" in error


@pytest.mark.parametrize(
  "key",
  [
    pytest.param("agent..model", marks=pytest.mark.writes_undeclared("agent.", "agent..model")),
    "agent.bad-name.model",
    pytest.param(
      "agent.claude.self.model",
      marks=pytest.mark.writes_undeclared("agent.claude.self", "agent.claude.self.model"),
    ),
    "agent.claude.foreign_leaf",
    "meta.agent.claude.name",
    "pref.pref.agent.claude.model",
    "pref.meta.agent.claude.name",
  ],
)
def test_addressed_identity_does_not_authorize_write(
  tmp_path, identity, vocabulary, monkeypatch, key
):
  resolver = Mock(return_value=AgentResolution(identity))
  monkeypatch.setattr(ci, "resolve_named_agent_input", resolver)
  path = tmp_path / "settings.yaml"
  addressed = ci.addressed_agent_identity(key)
  message = ci.set_config_value(
    key,
    None,
    config_path=path,
    command_scope=ConfigLevel.system,
    cascade_agent_identity=addressed,
  )
  assert message.startswith("Error:"), message
  assert not path.exists()
