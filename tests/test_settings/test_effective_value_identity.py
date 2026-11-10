"""Effective scalar display keeps the selected identity's vocabulary local."""

from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.errors import ConfigError
from kanibako.settings import config_interface as ci
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
    reader = Mock(side_effect=AssertionError("effective display must not read PG"))
    monkeypatch.setattr(f"kanibako.persona_store.{name}", reader)
    readers.append(reader)
  yield
  for reader in readers:
    reader.assert_not_called()


@pytest.fixture
def vocabulary(monkeypatch):
  names = default_valid_agents()
  before = (tuple(names), dict(names.leaf_map), dict(names.leaf_map.agent_harnesses))
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", lambda: names)
  yield names
  assert (tuple(names), dict(names.leaf_map), dict(names.leaf_map.agent_harnesses)) == before


@pytest.fixture(params=[("claude", "goose"), ("scout", "claude")])
def identity(request, tmp_path):
  node, harness = request.param
  return AgentIdentity(
    node=node,
    registered_name="TrailGuide",
    persona="Guide",
    harness=harness.title(),
    target_name=harness,
    entry_path=tmp_path / "PG" / "TrailGuide",
  )


def _inputs(std, identity):
  return launch.resolve_inputs(
    subject=launch.ResolveSubject.SYSTEM,
    std=std,
    agent_name=identity.node,
    agent_identity=identity,
    system_path=std.settings,
  )


def _effective(std, node, key, *, inputs=None, agent_path=None, floor=None):
  *sections, leaf = key.split(".")
  return ci.effective_value(
    key,
    tuple(sections),
    leaf,
    agent_name=node,
    system_path=std.settings,
    agent_path=agent_path,
    workset_path=None,
    box_path=None,
    floor=floor,
    inputs=inputs,
  )


@pytest.mark.parametrize("anchor", ["name", "path", "settings"])
def test_registered_identity_metadata_resolves_with_local_vocabulary(
  std, identity, vocabulary, anchor
):
  node = identity.node
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {"label": f"{{meta.agent.{node}.{anchor}}}"}})
  original = path.read_bytes()
  inputs = _inputs(std, identity)
  with (
    patch.object(ci, "_set_time_valid_agents", wraps=ci._set_time_valid_agents) as adapt,
    patch.object(launch, "assemble_cascade", wraps=launch.assemble_cascade) as assemble,
    patch.object(ci, "_lenient_expand", wraps=ci._lenient_expand) as expand,
  ):
    result = _effective(std, node, f"agent.{node}.label", inputs=inputs, agent_path=path)
  assert result == (
    {"name": "TrailGuide", "path": str(std.agents / node), "settings": str(path)}[anchor],
    "agent",
  )
  adapt.assert_called_once_with(inputs)
  local = assemble.call_args.kwargs["valid_agents"]
  assert local is expand.call_args.kwargs["valid_agents"]
  assert local is not vocabulary
  assert local.leaf_map.agent_harnesses == {node: identity.target_name}
  assert path.read_bytes() == original
  assert not identity.entry_path.exists()


@pytest.mark.parametrize(
  "node,harness,leaf,value",
  [
    pytest.param(
      "claude",
      "goose",
      "provider",
      "openai",
      marks=pytest.mark.writes_undeclared(
        "agent.claude.provider",
        reason="Registered Claude uses Goose's provider leaf, not native Claude's vocabulary",
      ),
    ),
    ("scout", "claude", "model", "chosen-model"),
  ],
)
def test_selected_harness_leaf_and_reference_resolve(
  std, tmp_path, vocabulary, node, harness, leaf, value
):
  identity = AgentIdentity(node, "TrailGuide", "Guide", harness.title(), harness, tmp_path / "PG")
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {leaf: value, "label": f"{{agent.{node}.{leaf}}}"}})
  inputs = _inputs(std, identity)
  assert _effective(std, node, f"agent.{node}.{leaf}", inputs=inputs, agent_path=path) == (
    value,
    "agent",
  )
  assert _effective(std, node, f"agent.{node}.label", inputs=inputs, agent_path=path) == (
    value,
    "agent",
  )


def test_foreign_harness_leaf_does_not_resolve(std, tmp_path, vocabulary):
  identity = AgentIdentity("scout", "TrailGuide", "Guide", "Claude", "claude", tmp_path / "PG")
  path = agent_settings_path(std.agents, "scout")
  dump_doc(path, {"self": {"label": "{agent.scout.provider}"}})
  assert (
    _effective(std, "scout", "agent.scout.label", inputs=_inputs(std, identity), agent_path=path)
    is None
  )


@pytest.mark.parametrize("node", ["claude", "Guide℘claude", "shell"])
@pytest.mark.parametrize("with_inputs", [False, True])
def test_native_legacy_and_shell_keep_none_vocabulary(std, tmp_path, node, with_inputs):
  identity = AgentIdentity(
    node=node,
    registered_name=None,
    persona="Guide" if "℘" in node else None,
    harness=None if node == "shell" else "claude",
    target_name="shell" if node == "shell" else "claude",
    entry_path=None,
  )
  inputs = _inputs(std, identity) if with_inputs else None
  dump_doc(std.settings, {"agent": {node: {"label": "legacy-label"}}})
  with (
    patch.object(launch, "assemble_cascade", wraps=launch.assemble_cascade) as assemble,
    patch.object(ci, "_lenient_expand", wraps=ci._lenient_expand) as expand,
  ):
    assert _effective(std, node, f"agent.{node}.label", inputs=inputs) == (
      "legacy-label",
      "system",
    )
  assert assemble.call_args.kwargs["valid_agents"] is None
  assert expand.call_args.kwargs["valid_agents"] is None


@pytest.mark.parametrize("value", [None, "", [], "{meta.agent.scout.missing}"])
def test_non_scalar_or_unresolved_value_has_no_effective_label(std, identity, vocabulary, value):
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": {"label": value}})
  assert (
    _effective(
      std,
      identity.node,
      f"agent.{identity.node}.label",
      inputs=_inputs(std, identity),
      agent_path=path,
    )
    is None
  )


def test_floor_override_and_source_tier_remain_distinct(std, identity, vocabulary):
  node = identity.node
  key = f"agent.{node}.label"
  inputs = _inputs(std, identity)
  dump_doc(std.settings, {})
  assert _effective(std, node, key, inputs=inputs, floor={key: "floor-label"}) == (
    "floor-label",
    ci.FLOOR_TIER,
  )
  dump_doc(std.settings, {"agent": {node: {"label": "system-label"}}})
  assert _effective(std, node, key, inputs=inputs, floor={key: "floor-label"}) == (
    "system-label",
    "system",
  )
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {"label": "agent-label"}})
  assert _effective(
    std, node, key, inputs=inputs, agent_path=path, floor={key: "floor-label"}
  ) == ("agent-label", "agent")


def test_absent_identity_keeps_unadapted_vocabulary(std, identity):
  inputs = replace(_inputs(std, identity), agent_identity=None)
  dump_doc(std.settings, {"box": {"image": "example/rig"}})
  with (
    patch.object(ci, "_set_time_valid_agents", wraps=ci._set_time_valid_agents) as adapt,
    patch.object(launch, "assemble_cascade", wraps=launch.assemble_cascade) as assemble,
    patch.object(ci, "_lenient_expand", wraps=ci._lenient_expand) as expand,
  ):
    assert _effective(std, identity.node, "box.image", inputs=inputs) == ("example/rig", "system")
  adapt.assert_called_once_with(inputs)
  assert assemble.call_args.kwargs["valid_agents"] is None
  assert expand.call_args.kwargs["valid_agents"] is None


def test_malformed_file_error_still_propagates(std, identity, vocabulary):
  inputs = _inputs(std, identity)
  std.settings.parent.mkdir(parents=True, exist_ok=True)
  std.settings.write_text("box: [unterminated\n")
  with pytest.raises(ConfigError, match="not valid YAML") as error:
    _effective(std, identity.node, "box.image", inputs=inputs)
  assert str(std.settings) in str(error.value)


def test_assembly_settings_error_still_propagates(std, identity, vocabulary):
  inputs = _inputs(std, identity)
  failure = SettingsError("settings unavailable")
  with patch.object(launch, "assemble_cascade", side_effect=failure):
    with pytest.raises(SettingsError) as error:
      _effective(std, identity.node, "box.image", inputs=inputs)
  assert error.value is failure


def test_all_paths_absent_returns_before_vocabulary_lookup(monkeypatch):
  monkeypatch.setattr(ci, "_set_time_valid_agents", Mock(side_effect=AssertionError("no paths")))
  assert (
    ci.effective_value(
      "box.image",
      ("box",),
      "image",
      agent_name="",
      system_path=None,
      agent_path=None,
      workset_path=None,
      box_path=None,
    )
    is None
  )
