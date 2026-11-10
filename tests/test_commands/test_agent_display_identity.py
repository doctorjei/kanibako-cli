"""Named display readers share the selected identity before validating the file."""

import argparse
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import agent_cmd
from kanibako.errors import AgentNotInstalledError, ConfigError
from kanibako.settings import config as selection
from kanibako.settings import config_interface as ci
from kanibako.settings import settings_assemble as assembly
from kanibako.settings import settings_launch as launch
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.config_io import dump_doc
from kanibako.settings.settings_prefs import default_valid_agents
from kanibako.settings.settings_resolve import SettingsError


@pytest.fixture(autouse=True)
def no_pg(monkeypatch):
  readers = []
  for name in (
    "locate_entry",
    "read_registered_persona",
    "read_persona_bundle",
    "resolve_secret_path",
  ):
    reader = Mock(side_effect=AssertionError("named display must not reread PG"))
    monkeypatch.setattr(f"kanibako.persona_store.{name}", reader)
    readers.append(reader)
  yield
  for reader in readers:
    reader.assert_not_called()


@pytest.fixture(params=["run_info", "run_show"])
def display(request, std, monkeypatch):
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)

  def run(node):
    return getattr(agent_cmd, request.param)(argparse.Namespace(agent_id=node, effective=False))

  return run


@pytest.fixture(params=[("claude", "goose"), ("scout", "claude")])
def identity(request, tmp_path, monkeypatch):
  node, harness = request.param
  identity = AgentIdentity(node, "TrailGuide", "Guide", harness.title(), harness, tmp_path / "PG")
  resolver = Mock(return_value=selection.AgentResolution(identity))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  monkeypatch.setattr(ci, "project_agent_input", Mock(return_value=(None, None)))
  return identity


@pytest.fixture
def vocabulary(monkeypatch):
  names = default_valid_agents()
  before = (tuple(names), dict(names.leaf_map), dict(names.leaf_map.agent_harnesses))
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", lambda: names)
  yield names
  assert (tuple(names), dict(names.leaf_map), dict(names.leaf_map.agent_harnesses)) == before


@pytest.mark.writes_undeclared(
  "agent.claude.provider", reason="Injected registered Claude uses Goose's vocabulary"
)
def test_selected_harness_leaf_is_judged_before_record(display, std, identity, vocabulary, capsys):
  leaf, value = ("provider", "ollama") if identity.target_name == "goose" else ("model", "chosen")
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": {leaf: value}})
  before = path.read_bytes()
  with (
    patch(
      "kanibako.settings.settings_assemble.agent_record", wraps=assembly.agent_record
    ) as record,
    patch.object(launch, "build_launch_snapshot", wraps=launch.build_launch_snapshot) as snapshot,
    patch.object(ci, "effective_value", wraps=ci.effective_value) as effective,
    patch.object(launch, "resolve_inputs", wraps=launch.resolve_inputs) as inputs,
  ):
    assert display(identity.node) == 0
  output = capsys.readouterr()
  assert value in output.out and not output.err
  assert {"goose": "Goose Harness", "claude": "Claude Code"}[identity.target_name] in output.out
  local = record.call_args.kwargs["valid_agents"]
  assert local.leaf_map.agent_harnesses == {identity.node: identity.target_name}
  assert snapshot.call_args.kwargs["valid_agents"] is local
  assert effective.call_args.kwargs["valid_agents"] is local
  assert all(call.kwargs["agent_identity"] is identity for call in inputs.call_args_list)
  selection.resolve_named_agent_input.assert_called_once_with(identity.node)
  assert path.read_bytes() == before
  assert not identity.entry_path.exists()


def test_foreign_harness_leaf_refuses_before_label(display, std, identity, vocabulary):
  if identity.target_name != "claude":
    identity = replace(identity, node="scout", harness="Claude", target_name="claude")
    selection.resolve_named_agent_input.return_value = selection.AgentResolution(identity)
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {"provider": "foreign"}})
  with (
    patch.object(
      agent_cmd, "_agent_label", side_effect=AssertionError("record must refuse first")
    ),
    pytest.raises(SettingsError, match="provider"),
  ):
    display(identity.node)


@pytest.mark.parametrize("anchor", ["name", "path", "settings"])
def test_system_label_metadata_uses_identity_not_pg_path(
  display, std, identity, vocabulary, anchor, capsys
):
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": {}})
  dump_doc(
    std.settings, {"agent": {identity.node: {"label": f"{{meta.agent.{identity.node}.{anchor}}}"}}}
  )
  assert display(identity.node) == 0
  expected = {"name": "TrailGuide", "path": str(path.parent), "settings": str(path)}[anchor]
  output = capsys.readouterr()
  assert expected in output.out and not output.err
  assert not identity.entry_path.exists()


def test_stored_label_skips_null_and_cascade(display, std, identity, vocabulary, capsys):
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {"label": '""'}})
  with (
    patch.object(agent_cmd, "_refuse_null_label", side_effect=AssertionError("eager null check")),
    patch.object(launch, "resolve_inputs", side_effect=AssertionError("eager cascade")),
  ):
    assert display(identity.node) == 0
  assert '""' in capsys.readouterr().out


def test_empty_label_omits_agent_file_for_later_doors(display, std, identity, vocabulary, capsys):
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {"label": ""}})
  dump_doc(std.settings, {"agent": {identity.node: {"label": "System label"}}})
  with patch.object(ci, "effective_value", wraps=ci.effective_value) as effective:
    assert display(identity.node) == 0
  assert "System label" in capsys.readouterr().out
  assert effective.call_args.kwargs["agent_path"] is None


def test_null_label_still_refuses(display, std, identity, vocabulary):
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {"label": None}})
  with pytest.raises(ConfigError, match="label"):
    display(identity.node)


@pytest.mark.writes_undeclared(
  "agent.claude.unknown_leaf", reason="Injected missing harness explicitly concedes its leaves"
)
def test_missing_plugin_display_concedes_but_launch_refuses(
  display, std, identity, vocabulary, monkeypatch, capsys
):
  identity = replace(identity, harness="missing", target_name="missing")
  selection.resolve_named_agent_input.return_value = selection.AgentResolution(
    identity, unavailable=AgentNotInstalledError("missing")
  )
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {"unknown_leaf": "kept"}})
  monkeypatch.setattr("kanibako.targets.get_target", Mock(side_effect=KeyError("missing")))
  assert display(identity.node) == 0
  output = capsys.readouterr()
  assert "TrailGuide" in output.out and "kept" in output.out and not output.err
  with pytest.raises(AgentNotInstalledError, match="missing"):
    selection.resolve_agent_identity(explicit_agent=identity.node)


def test_unreadable_settings_warn_once_and_use_registered_fallback(
  display, std, identity, vocabulary, monkeypatch, capsys
):
  dump_doc(agent_settings_path(std.agents, identity.node), {"self": {}})
  monkeypatch.setattr(ci, "effective_value", Mock(side_effect=SettingsError("unreadable\nsecond")))
  monkeypatch.setattr("kanibako.targets.get_target", Mock(side_effect=KeyError("unreadable")))
  assert display(identity.node) == 0
  output = capsys.readouterr()
  assert "TrailGuide" in output.out
  assert output.err.count("Label: showing the built-in default") == 1


@pytest.mark.parametrize("node", ["default", "self"])
def test_present_nonagent_store_keeps_the_record_owners_cure(display, std, node):
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {}})
  before = path.read_bytes()
  with pytest.raises(SettingsError) as owner:
    assembly.agent_record(path, node=node, purpose=assembly.ReadPurpose.RESOLVE)
  with pytest.raises((ConfigError, SettingsError)) as displayed:
    display(node)
  message = str(displayed.value)
  assert message == str(owner.value)
  assert isinstance(displayed.value, SettingsError)
  assert f"{path.parent} is not an agent store" in message
  if node == "default":
    assert "move" in message and "or delete it" in message
    assert "system file's 'agent: default:' table" in message
  else:
    assert "'self' is not an agent" in message
  assert path.read_bytes() == before


def test_missing_store_does_not_resolve_identity(display, std, monkeypatch, capsys):
  resolver = Mock(side_effect=AssertionError("missing store must route first"))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  assert display("absent") == 1
  assert "agent" in capsys.readouterr().err
  resolver.assert_not_called()
