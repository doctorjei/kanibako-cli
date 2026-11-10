"""Classified named input retains its unavailable-live diagnosis."""

from pathlib import Path

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.errors import AgentNotInstalledError, AgentUnsetError, ConfigError
from kanibako.settings.config import (
  AgentResolution,
  resolve_agent_identity,
  resolve_agent_input,
  resolve_named_agent_identity,
  resolve_named_agent_input,
)


@pytest.fixture
def no_identity_io(monkeypatch):
  def unexpected(*args, **kwargs):
    raise AssertionError("identity classification must not read files or fetch credentials")

  monkeypatch.setattr(
    "kanibako.targets.discover_targets", lambda: {"claude": object, "goose": object}
  )
  monkeypatch.setattr("kanibako.settings.config.load_doc", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", lambda name: None)
  monkeypatch.setattr("kanibako.persona_store.locate_entry", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_persona_bundle", unexpected)
  monkeypatch.setattr("kanibako.settings.config_io.fetch_persona_secret", unexpected)


@pytest.mark.parametrize(
  "name,node,persona,harness",
  [
    ("Claude", "claude", "claude", "claude"),
    ("  GOOSE  ", "goose", "goose", "goose"),
    ("Navigator+CLAUDE", "Navigator℘claude", "Navigator", "claude"),
    ("Navigator℘Goose", "Navigator℘goose", "Navigator", "goose"),
    ("Scout+Uninstalled", "Scout℘uninstalled", "Scout", "Uninstalled"),
    ("Uninstalled", "uninstalled", "uninstalled", "Uninstalled"),
  ],
)
def test_named_address_retains_legacy_identity(no_identity_io, name, node, persona, harness):
  assert resolve_named_agent_identity(name) == AgentIdentity(
    node=node,
    registered_name=None,
    persona=persona,
    harness=harness,
    target_name=harness,
    entry_path=None,
  )


@pytest.mark.parametrize("name", ["shell", "Shell", "  SHELL  "])
def test_shell_needs_no_discovery_for_either_contract(no_identity_io, monkeypatch, name):
  def unexpected(*args, **kwargs):
    raise AssertionError("shell must not discover plugins or PG")

  monkeypatch.setattr("kanibako.targets.discover_targets", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", unexpected)
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


def test_target_discovery_failure_is_carried_without_pg_access(no_identity_io, monkeypatch):
  failure = RuntimeError("broken plugin discovery")

  def fail():
    raise failure

  def unexpected(name):
    raise AssertionError("unavailable discovery cannot establish plugin precedence")

  monkeypatch.setattr("kanibako.targets.discover_targets", fail)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", unexpected)
  addressed = resolve_named_agent_input("Navigator+CLAUDE")
  assert addressed.identity.node == "Navigator℘claude"
  assert addressed.unavailable is failure
  with pytest.raises(RuntimeError) as strict:
    resolve_agent_identity(explicit_agent="Navigator+CLAUDE")
  assert strict.value is failure


def test_identity_failures_propagate_before_strict_target_discovery(no_identity_io, monkeypatch):
  failure = ConfigError("identity source refused")

  def fail(name):
    raise failure

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_input", fail)
  with pytest.raises(ConfigError) as strict:
    resolve_agent_identity(explicit_agent="claude")
  assert strict.value is failure


def test_unset_selection_never_addresses_a_name_or_discovers_targets(no_identity_io, monkeypatch):
  def unexpected(name):
    raise AssertionError("unset selection has no name to address")

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_input", unexpected)
  with pytest.raises(AgentUnsetError, match="system.agent is unset"):
    resolve_agent_identity(explicit_agent=None)


def test_strict_projection_uses_one_rich_addressed_result(no_identity_io, monkeypatch):
  identity = AgentIdentity("scout", "Scout", "Navigator", "goose", "goose", Path("/PG/Scout"))
  calls = []

  def address(name):
    calls.append(name)
    return AgentResolution(identity)

  monkeypatch.setattr("kanibako.settings.config.resolve_named_agent_input", address)
  resolved = resolve_agent_identity(explicit_agent="Scout", requested="claude")
  assert calls == ["Scout"]
  assert resolved is identity


def test_strict_projection_raises_the_carried_diagnosis(no_identity_io, monkeypatch):
  identity = AgentIdentity("scout", "Scout", "Navigator", "missing", "missing", None)
  failure = AgentNotInstalledError("PG agent 'Scout' requires harness 'missing'")
  monkeypatch.setattr(
    "kanibako.settings.config.resolve_named_agent_input",
    lambda name: AgentResolution(identity, unavailable=failure),
  )
  with pytest.raises(AgentNotInstalledError) as raised:
    resolve_agent_identity(explicit_agent="Scout")
  assert raised.value is failure


def _public_registration(monkeypatch, *, harness="claude"):
  from unittest.mock import Mock

  root, entry = Path("/PG/CapturedRoot"), Path("/External/ScoutEntry")
  resolved = {
    "agent": {
      "persona": {"name": "Guide"},
      "harness": {
        "name": harness,
        "config_store": {"env": {"ANTHROPIC_BASE_URL": "https://endpoint.invalid"}},
        "auth_var": "ANTHROPIC_AUTH_TOKEN",
      },
      "secret": {"backend": "synthetic"},
    }
  }
  listing = Mock(return_value={"Scout": entry})
  reader = Mock(return_value=(entry, resolved))
  monkeypatch.setattr("kanibako.settings.config_io.persona_store_location", lambda: root)
  monkeypatch.setattr("kanibako.settings.config_io.persona_store_agents", listing)
  monkeypatch.setattr("kanibako.settings.config_io.load_persona_agent", reader)
  monkeypatch.setattr(
    "kanibako.settings.config_io.fetch_persona_secret",
    Mock(side_effect=AssertionError("identity and projection cannot fetch credentials")),
  )
  return root, entry, listing, reader


def test_registered_address_captures_one_public_input_and_projects_without_lookup(monkeypatch):
  from kanibako.persona_store import project_registered_persona
  from kanibako.plugins.claude import ClaudeTarget

  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": ClaudeTarget})
  root, entry, listing, reader = _public_registration(monkeypatch)
  addressed = resolve_named_agent_input("  sCoUt  ")
  assert addressed.identity == AgentIdentity("scout", "Scout", "Guide", "claude", "claude", entry)
  assert addressed.unavailable is None
  assert addressed.registered_persona.store_root == root
  listing.assert_called_once_with(root=root)
  reader.assert_called_once_with("Scout", root=root)
  monkeypatch.setenv("AGENT_STORE_DIR", "/Wrong/Root")
  bundle = project_registered_persona(addressed.registered_persona, ClaudeTarget())
  assert bundle.to_persona_values()["endpoint"] == "https://endpoint.invalid"
  assert addressed.registered_persona.store_root == root
  assert listing.call_count == reader.call_count == 1


def test_installed_plugin_collision_never_opens_broken_pg(monkeypatch):
  def unexpected(*args, **kwargs):
    raise AssertionError("installed plugin must win without any PG access")

  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  monkeypatch.setattr("kanibako.settings.config_io.persona_store_location", unexpected)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", unexpected)
  addressed = resolve_named_agent_input("  CLAUDE  ")
  assert addressed.identity.node == "claude"
  assert addressed.registered_persona is addressed.unavailable is None


def test_broken_pg_remains_addressable_with_its_diagnosis(monkeypatch):
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  _root, _entry, listing, reader = _public_registration(monkeypatch)
  failure = ConfigError("synthetic broken PG configuration")
  reader.side_effect = failure
  addressed = resolve_named_agent_input("Scout")
  assert addressed.identity.node == "scout"
  assert addressed.registered_persona is None
  assert addressed.unavailable is failure
  assert listing.call_count == reader.call_count == 1
  with pytest.raises(ConfigError) as raised:
    resolve_agent_input(explicit_agent="Scout")
  assert raised.value is failure
  assert listing.call_count == reader.call_count == 2


def test_registered_missing_plugin_retains_identity_root_and_diagnosis(monkeypatch):
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  root, entry, listing, reader = _public_registration(monkeypatch, harness="Goose")
  addressed = resolve_named_agent_input("Scout")
  assert addressed.identity == AgentIdentity("scout", "Scout", "Guide", "Goose", "goose", entry)
  assert addressed.registered_persona.store_root == root
  assert addressed.registered_persona.identity == addressed.identity
  assert isinstance(addressed.unavailable, AgentNotInstalledError)
  assert "requires harness 'goose'" in str(addressed.unavailable)
  assert listing.call_count == reader.call_count == 1


def test_registry_case_normalizes_payload_and_scalar_collector_together(monkeypatch):
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"Claude": object})
  root, entry, listing, reader = _public_registration(monkeypatch)
  captured = []
  identity = resolve_agent_identity(explicit_agent="Scout", registered_persona_out=captured)
  assert identity == AgentIdentity("scout", "Scout", "Guide", "Claude", "Claude", entry)
  assert captured[0].identity == identity
  assert captured[0].store_root == root
  assert listing.call_count == reader.call_count == 1


def test_rich_result_rejects_mismatched_registered_identity(monkeypatch):
  from dataclasses import replace

  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  _public_registration(monkeypatch)
  addressed = resolve_named_agent_input("Scout")
  with pytest.raises(ValueError, match="does not own"):
    AgentResolution(replace(addressed.identity, node="other"), addressed.registered_persona)


def test_actual_root_discovery_failure_is_carried_and_still_refuses_launch(monkeypatch):
  import pwd
  from unittest.mock import Mock

  from kanibako.settings import config_io

  user = "264F_editor_no_such_os_user"
  with pytest.raises(KeyError):
    pwd.getpwnam(user)
  monkeypatch.setenv("AGENT_STORE_DIR", f"~{user}")
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  location = Mock(wraps=config_io.persona_store_location)
  monkeypatch.setattr(config_io, "persona_store_location", location)
  forbidden = Mock(side_effect=AssertionError("failed root cannot list, read, or fetch"))
  for name in ("persona_store_agents", "load_persona_agent", "fetch_persona_secret"):
    monkeypatch.setattr(config_io, name, forbidden)
  addressed = resolve_named_agent_input("Scout")
  assert addressed.identity == AgentIdentity("scout", None, "scout", "Scout", "Scout", None)
  assert addressed.registered_persona is None
  assert isinstance(addressed.unavailable, RuntimeError)
  assert "home directory" in str(addressed.unavailable)
  location.assert_called_once_with()
  with pytest.raises(RuntimeError, match="home directory"):
    resolve_agent_input(explicit_agent="Scout")
  assert location.call_count == 2
  forbidden.assert_not_called()


@pytest.mark.parametrize("name", ["CLAUDE", "Guide+claude", "shell"])
def test_actual_broken_root_does_not_affect_pg_free_inputs(monkeypatch, name):
  from unittest.mock import Mock

  from kanibako.settings import config_io

  monkeypatch.setenv("AGENT_STORE_DIR", "~264F_editor_no_such_os_user")
  discovery = Mock(return_value={"claude": object})
  monkeypatch.setattr("kanibako.targets.discover_targets", discovery)
  location = Mock(wraps=config_io.persona_store_location)
  monkeypatch.setattr(config_io, "persona_store_location", location)
  forbidden = Mock(side_effect=AssertionError("PG-free input must not access the store"))
  for method in ("persona_store_agents", "load_persona_agent", "fetch_persona_secret"):
    monkeypatch.setattr(config_io, method, forbidden)
  addressed = resolve_named_agent_input(name)
  assert addressed.unavailable is None
  assert addressed.registered_persona is None
  location.assert_not_called()
  forbidden.assert_not_called()
  assert discovery.call_count == (0 if name == "shell" else 1)


@pytest.mark.parametrize("name", ["", "bad-name", "default", "Scout+shell"])
def test_grammar_refusal_precedes_actual_broken_root(monkeypatch, name):
  from unittest.mock import Mock

  monkeypatch.setenv("AGENT_STORE_DIR", "~264F_editor_no_such_os_user")
  forbidden = Mock(side_effect=AssertionError("grammar must precede identity I/O"))
  monkeypatch.setattr("kanibako.targets.discover_targets", forbidden)
  monkeypatch.setattr("kanibako.settings.config_io.persona_store_location", forbidden)
  with pytest.raises(ConfigError):
    resolve_named_agent_input(name)
  with pytest.raises(ConfigError):
    resolve_agent_input(explicit_agent=name)
  forbidden.assert_not_called()


@pytest.mark.parametrize("failure", [RuntimeError("read failed"), OSError("read failed")])
def test_public_read_boundary_carries_non_config_errors(no_identity_io, monkeypatch, failure):
  from unittest.mock import Mock

  reader = Mock(side_effect=failure)
  monkeypatch.setattr("kanibako.persona_store.read_registered_persona", reader)
  addressed = resolve_named_agent_input("Scout")
  assert addressed.identity == AgentIdentity("scout", None, "scout", "Scout", "Scout", None)
  assert addressed.unavailable is failure
  assert addressed.registered_persona is None
  with pytest.raises(type(failure)) as strict:
    resolve_agent_input(explicit_agent="Scout")
  assert strict.value is failure
  assert reader.call_count == 2
