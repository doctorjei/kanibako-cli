"""Named addressing is independent of installed-target launch eligibility."""

from pathlib import Path

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.errors import AgentNotInstalledError, AgentUnsetError, ConfigError
from kanibako.settings.config import resolve_agent_identity, resolve_named_agent_identity


@pytest.fixture
def no_identity_io(monkeypatch):
  def unexpected(*args, **kwargs):
    raise AssertionError("named identity must not discover plugins or read configuration/PG")

  monkeypatch.setattr("kanibako.targets.discover_targets", unexpected)
  monkeypatch.setattr("kanibako.settings.config.load_doc", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", unexpected)
  monkeypatch.setattr("kanibako.persona_store.locate_entry", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_persona_bundle", unexpected)


@pytest.mark.parametrize(
  "name,node,persona,harness",
  [
    ("Claude", "claude", "claude", "Claude"),
    ("  GOOSE  ", "goose", "goose", "GOOSE"),
    ("Navigator+CLAUDE", "Navigator℘claude", "Navigator", "CLAUDE"),
    ("Navigator℘Goose", "Navigator℘goose", "Navigator", "Goose"),
    ("Scout+Uninstalled", "Scout℘uninstalled", "Scout", "Uninstalled"),
    ("Uninstalled", "uninstalled", "uninstalled", "Uninstalled"),
  ],
)
def test_named_address_normalizes_node_without_identity_io(
  no_identity_io, name, node, persona, harness
):
  assert resolve_named_agent_identity(name) == AgentIdentity(
    node=node,
    registered_name=None,
    persona=persona,
    harness=harness,
    target_name=harness,
    entry_path=None,
  )


@pytest.mark.parametrize("name", ["shell", "Shell", "  SHELL  "])
def test_shell_needs_no_discovery_for_either_contract(no_identity_io, name):
  identity = AgentIdentity("shell", None, None, None, "shell", None)
  assert resolve_named_agent_identity(name) == identity
  assert resolve_agent_identity(explicit_agent=name) == identity


@pytest.mark.parametrize("name", ["", " \t\n", "default", "bad-name", "Scout+shell"])
def test_invalid_addresses_are_not_hidden_by_tolerant_resolution(no_identity_io, name):
  with pytest.raises(ConfigError) as tolerant:
    resolve_named_agent_identity(name)
  with pytest.raises(ConfigError) as strict:
    resolve_agent_identity(explicit_agent=name, requested="claude")
  assert str(strict.value) == str(tolerant.value)


@pytest.mark.parametrize("name", ["MiSsInG", "Navigator+MiSsInG", "Navigator℘MiSsInG"])
def test_missing_target_is_addressable_but_cannot_launch(no_identity_io, monkeypatch, name):
  identity = resolve_named_agent_identity(name)
  assert identity.target_name == "MiSsInG"
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  with pytest.raises(AgentNotInstalledError) as refusal:
    resolve_agent_identity(explicit_agent=name)
  text = str(refusal.value)
  assert "Agent 'MiSsInG' is not installed" in text
  assert "kanibako-agent-MiSsInG" in text
  assert "kanibako agent list" in text


@pytest.mark.parametrize(
  "name,node,persona,target",
  [
    ("CLAUDE", "claude", "claude", "claude"),
    ("Navigator+CLAUDE", "Navigator℘claude", "Navigator", "claude"),
    ("Navigator℘GOOSE", "Navigator℘goose", "Navigator", "goose"),
    ("Navigator+native", "Navigator℘Native", "Navigator", "Native"),
    ("NATIVE", "Native", "Native", "Native"),
  ],
)
def test_strict_resolution_retains_registry_case_and_persona_case(
  no_identity_io, monkeypatch, name, node, persona, target
):
  calls = []

  def discover():
    calls.append("discover")
    return {"claude": object, "goose": object, "Native": object}

  monkeypatch.setattr("kanibako.targets.discover_targets", discover)
  identity = resolve_agent_identity(explicit_agent=name, requested="not_selected")
  assert identity == AgentIdentity(node, None, persona, target, target, None)
  assert calls == ["discover"]


def test_target_discovery_failure_does_not_prevent_named_addressing(no_identity_io, monkeypatch):
  failure = RuntimeError("broken plugin discovery")

  def fail():
    raise failure

  monkeypatch.setattr("kanibako.targets.discover_targets", fail)
  assert resolve_named_agent_identity("Navigator+CLAUDE").node == "Navigator℘claude"
  with pytest.raises(RuntimeError) as strict:
    resolve_agent_identity(explicit_agent="Navigator+CLAUDE")
  assert strict.value is failure


def test_identity_failures_propagate_before_strict_target_discovery(no_identity_io, monkeypatch):
  failure = ConfigError("identity source refused")

  def fail(name):
    raise failure

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_identity", fail)
  with pytest.raises(ConfigError) as strict:
    resolve_agent_identity(explicit_agent="claude")
  assert strict.value is failure


def test_unset_selection_never_addresses_a_name_or_discovers_targets(no_identity_io, monkeypatch):
  def unexpected(name):
    raise AssertionError("unset selection has no name to address")

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_identity", unexpected)
  with pytest.raises(AgentUnsetError, match="system.agent is unset"):
    resolve_agent_identity(explicit_agent=None)


def test_strict_validation_uses_one_independent_address_identity(no_identity_io, monkeypatch):
  identity = AgentIdentity(
    node="scout",
    registered_name="Scout",
    persona="Navigator",
    harness="Goose",
    target_name="goose",
    entry_path=Path("/not/a/kanibako/settings/path"),
  )
  calls = []

  def address(name):
    calls.append(name)
    return identity

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_identity", address)
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"goose": object})
  resolved = resolve_agent_identity(explicit_agent="Scout", requested="claude")
  assert calls == ["Scout"]
  assert resolved.node == identity.node
  assert resolved.registered_name == identity.registered_name
  assert resolved.persona == identity.persona
  assert resolved.entry_path == identity.entry_path
  assert resolved.target_name == resolved.harness == "goose"


def test_strict_refusal_uses_addressed_target_not_the_typed_name(no_identity_io, monkeypatch):
  identity = AgentIdentity("scout", "Scout", "Navigator", "MiSsInG", "MiSsInG", None)
  monkeypatch.setattr(
    "kanibako.settings.config.resolve_named_agent_identity", lambda name: identity
  )
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  with pytest.raises(AgentNotInstalledError, match="Agent 'MiSsInG' is not installed"):
    resolve_agent_identity(explicit_agent="Scout")
