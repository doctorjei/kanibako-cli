import argparse
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import agent_cmd
from kanibako.persona_store import read_registered_persona as _registered_reader
from kanibako.settings import config as selection
from kanibako.settings import config_interface as ci
from kanibako.settings import settings_assemble as assembly
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.config_io import dump_doc, load_doc, render_stored_scalar
from kanibako.settings.settings_prefs import AgentNames
from kanibako.settings.settings_resolve import SettingsError


@pytest.fixture(autouse=True)
def no_pg(monkeypatch):
  traps = []
  for name in (
    "locate_entry",
    "read_registered_persona",
    "read_persona_bundle",
    "resolve_secret_path",
  ):
    trap = Mock(side_effect=AssertionError("PG is inactive"))
    monkeypatch.setattr(f"kanibako.persona_store.{name}", trap)
    traps.append(trap)
  monkeypatch.setattr(
    selection, "resolve_agent_identity", Mock(side_effect=AssertionError("launch selection"))
  )
  yield
  for trap in traps:
    trap.assert_not_called()


@pytest.fixture
def vocabulary(monkeypatch):
  names = AgentNames(
    {"claude", "goose"},
    leaf_map={"claude": {"claude_only"}, "goose": {"provider"}},
  )
  monkeypatch.setattr("kanibako.settings.settings_prefs.default_valid_agents", lambda: names)
  yield names
  assert names.leaf_map.agent_harnesses == {}


@pytest.fixture(params=[("ClAuDe", "claude"), ("Guide+Goose", "Guide℘goose")])
def addressed(request, std, tmp_path, monkeypatch):
  typed, node = request.param
  identity = AgentIdentity(node, "TrailGuide", "Guide", "Goose", "goose", tmp_path / "PG")
  resolver = Mock(return_value=selection.AgentResolution(identity))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  monkeypatch.setattr(ci, "project_agent_input", Mock(return_value=(None, None)))
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {}})
  return typed, identity, path, resolver


def _run(verb, node, key, **kwargs):
  args = argparse.Namespace(agent_id=node, key=key, key_value=key, all_keys=False, **kwargs)
  return getattr(agent_cmd, f"run_{verb}")(args)


@pytest.mark.writes_undeclared(
  "agent.claude.provider", reason="Injected registered Claude uses Goose's vocabulary"
)
@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_keyed_verbs_share_addressed_identity(addressed, vocabulary, verb, capsys):
  typed, identity, path, resolver = addressed
  dump_doc(path, {"self": {"provider": "old"}})
  engine_name = "set_config_value" if verb == "set" else "reset_config_value"
  with (
    patch.object(assembly, "agent_record", wraps=assembly.agent_record) as record,
    patch.object(ci, engine_name, wraps=getattr(ci, engine_name)) as engine,
    patch.object(ci, "identity_valid_agents", wraps=ci.identity_valid_agents) as local,
  ):
    assert _run(verb, typed, "provider=new" if verb == "set" else "provider") == 0
  output = capsys.readouterr()
  assert not output.err
  assert {"set": "Set provider=new", "get": "old", "reset": "Cleared provider"}[verb] in output.out
  resolver.assert_called_once_with(identity.node)
  names = local.call_args_list[0]
  assert names.args == (identity,)
  if verb == "get":
    engine.assert_not_called()
  else:
    assert engine.call_args.kwargs["cascade_agent_identity"] is identity
    assert engine.call_args.kwargs["cascade_agent_name"] == identity.node
    assert "cascade_agent_path" not in engine.call_args.kwargs
  if verb != "reset":
    record.assert_called_once()
    forwarded = record.call_args.kwargs["valid_agents"]
    assert forwarded.leaf_map.agent_harnesses == {identity.node: "goose"}
  assert load_doc(path) == (
    {"self": {"provider": "new" if verb == "set" else "old"}} if verb != "reset" else {}
  )
  assert not identity.entry_path.exists()


@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_foreign_leaf_refuses_without_changing_file(addressed, vocabulary, verb, capsys):
  typed, identity, path, resolver = addressed
  dump_doc(path, {"self": {"claude_only": "keep"}})
  before = path.read_bytes()
  with (
    patch.object(ci, "set_config_value", side_effect=AssertionError("gate first")),
    patch.object(ci, "reset_config_value", side_effect=AssertionError("gate first")),
    patch.object(assembly, "agent_record", side_effect=AssertionError("gate first")),
  ):
    assert _run(verb, typed, "claude_only=new" if verb == "set" else "claude_only") == 1
  assert "claude_only" in capsys.readouterr().err
  resolver.assert_called_once_with(identity.node)
  assert path.read_bytes() == before


@pytest.mark.parametrize("value", [None, ""])
def test_get_renders_present_empty_category_values(addressed, vocabulary, value, capsys):
  typed, identity, path, resolver = addressed
  dump_doc(path, {"self": {"env": {"EXAMPLE": value}}})
  before = path.read_bytes()
  assert _run("get", typed, "env.EXAMPLE") == 0
  output = capsys.readouterr()
  assert output.out.strip() == render_stored_scalar(value)
  assert not output.err
  assert path.read_bytes() == before
  resolver.assert_called_once_with(identity.node)


@pytest.mark.parametrize("value", [None, ""])
def test_set_model_empty_idioms_keep_identity(addressed, vocabulary, value, capsys):
  typed, identity, path, resolver = addressed
  with patch.object(ci, "set_config_value", wraps=ci.set_config_value) as setter:
    assert _run("set", typed, "model" if value is None else "model=", null=value is None) == 0
  output = capsys.readouterr()
  assert not output.err
  assert load_doc(path)["self"]["model"] == value
  assert setter.call_args.kwargs["cascade_agent_identity"] is identity
  resolver.assert_called_once_with(identity.node)


@pytest.mark.writes_undeclared(
  "agent.claude.unknown_leaf",
  "agent.Guide℘goose.unknown_leaf",
  reason="Injected missing harness concedes its unreadable leaves",
)
@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_missing_plugin_keeps_noun_concession(addressed, vocabulary, monkeypatch, verb, capsys):
  typed, identity, path, resolver = addressed
  resolver.return_value = selection.AgentResolution(
    replace(identity, harness="Missing", target_name="missing")
  )
  missing_names = AgentNames(
    {"claude", "goose"}, leaf_map=vocabulary.leaf_map, unreadable={"missing"}
  )
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents", lambda: missing_names
  )
  dump_doc(path, {"self": {"unknown_leaf": "kept"}})
  monkeypatch.setattr("kanibako.targets.get_target", Mock(side_effect=KeyError("missing")))
  assert _run(verb, typed, "unknown_leaf=new" if verb == "set" else "unknown_leaf") == 0
  assert not capsys.readouterr().err
  resolver.assert_called_once_with(identity.node)
  assert load_doc(path) == (
    {"self": {"unknown_leaf": "new" if verb == "set" else "kept"}} if verb != "reset" else {}
  )


@pytest.mark.writes_undeclared(
  "agent.claude.provider", reason="Injected registered Claude uses Goose's vocabulary"
)
def test_candidate_repair_uses_local_reader(addressed, vocabulary, capsys):
  typed, identity, path, resolver = addressed
  dump_doc(path, {"self": {"provider": {"bad": "shape"}}})
  with patch.object(assembly, "agent_record", wraps=assembly.agent_record) as reader:
    assert _run("set", typed, "provider=fixed") == 0
  assert not capsys.readouterr().err
  assert load_doc(path) == {"self": {"provider": "fixed"}}
  assert reader.call_args.kwargs["valid_agents"].leaf_map.agent_harnesses == {
    identity.node: "goose"
  }
  resolver.assert_called_once_with(identity.node)


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="Injected harness and deliberate poisoned-file repair fixture",
)
def test_poison_force_keeps_real_path_and_other_entries(addressed, vocabulary, capsys):
  typed, identity, path, resolver = addressed
  dump_doc(path, {"self": {"invalid_leaf": "keep"}})
  before = path.read_bytes()
  assert _run("set", typed, "provider=new") == 1
  output = capsys.readouterr()
  assert str(path) in output.err and "--force" in output.err
  assert "kanibako-agentset-" not in output.err
  assert path.read_bytes() == before
  resolver.reset_mock()
  assert _run("set", typed, "provider=new", force=True) == 0
  assert load_doc(path) == {"self": {"invalid_leaf": "keep", "provider": "new"}}
  resolver.assert_called_once_with(identity.node)


@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_explicit_empty_vocabulary_is_forwarded(addressed, monkeypatch, verb, capsys):
  typed, identity, path, resolver = addressed
  empty = AgentNames(())
  local = Mock(return_value=empty)
  monkeypatch.setattr(ci, "identity_valid_agents", local)
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("empty vocabulary must not rediscover")),
  )
  before = path.read_bytes()
  assert _run(verb, typed, "unknown_leaf=new" if verb == "set" else "unknown_leaf") == 1
  assert capsys.readouterr().err
  local.assert_called_once_with(identity)
  resolver.assert_called_once_with(identity.node)
  assert path.read_bytes() == before


@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_missing_store_precedes_identity(std, monkeypatch, verb, capsys):
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  resolver = Mock(side_effect=AssertionError("missing store first"))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  assert _run(verb, "absent", "model=new" if verb == "set" else "model") == 1
  assert "not found" in capsys.readouterr().err
  resolver.assert_not_called()


@pytest.mark.parametrize("node", ["default", "self"])
def test_reset_all_repair_does_not_resolve_identity(std, monkeypatch, node, capsys):
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  resolver = Mock(side_effect=AssertionError("reset-all repair"))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {"model": "old"}})
  assert agent_cmd.run_reset(argparse.Namespace(agent_id=node, all_keys=True, force=True)) == 0
  assert not capsys.readouterr().err
  assert load_doc(path) == {}
  resolver.assert_not_called()


@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_reserved_tier_keeps_existing_gate(std, monkeypatch, verb, capsys):
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  resolver = Mock(side_effect=AssertionError("reserved store has no identity"))
  monkeypatch.setattr(selection, "resolve_named_agent_input", resolver)
  path = agent_settings_path(std.agents, "default")
  dump_doc(path, {"self": {"model": "old"}})
  before = path.read_bytes()
  if verb == "get":
    with pytest.raises(SettingsError, match="not an agent store"):
      _run(verb, "default", "model")
  else:
    assert _run(verb, "default", "model=new" if verb == "set" else "model") == 1
    assert "default" in capsys.readouterr().err
  assert path.read_bytes() == before
  resolver.assert_not_called()


@pytest.mark.parametrize("typed", ["ClAuDe", "Guide+Goose"])
def test_legacy_address_uses_named_not_launch_resolution(std, monkeypatch, typed, capsys):
  from kanibako.settings.config_keys import agent_key_node

  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  node = agent_key_node(typed)
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {"model": "legacy"}})
  with patch.object(
    selection, "resolve_named_agent_input", wraps=selection.resolve_named_agent_input
  ) as resolver:
    assert _run("get", typed, "model") == 0
  assert capsys.readouterr().out.strip() == "legacy"
  resolver.assert_called_once_with(node)


@pytest.fixture
def registered_input(std, tmp_path, monkeypatch):
  from kanibako import persona_store
  from kanibako.plugins.claude.target import ClaudeTarget
  from kanibako.settings import config_io

  config = {"model": "live-model", "env": {"ANTHROPIC_BASE_URL": "https://live.invalid"}}
  data = {
    "agent": {
      "persona": {"name": "Guide"},
      "harness": {"name": "claude", "config_store": config, "auth_var": "TOKEN"},
      "secret": "synthetic-manager",
    }
  }
  root = tmp_path / "captured-PG"
  listing = Mock(return_value={"Scout": root / "Scout"})
  loading = Mock(side_effect=lambda *_a, **_k: (root / "Scout", data))
  project = Mock(wraps=persona_store.project_registered_persona)
  monkeypatch.setattr(persona_store, "read_registered_persona", _registered_reader)
  monkeypatch.setattr(persona_store, "project_registered_persona", project)
  monkeypatch.setattr(config_io, "persona_store_location", lambda: root)
  monkeypatch.setattr(config_io, "persona_store_agents", listing)
  monkeypatch.setattr(config_io, "load_persona_agent", loading)
  monkeypatch.setattr(config_io, "fetch_persona_secret", Mock(side_effect=AssertionError("fetch")))
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": ClaudeTarget})
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  path = agent_settings_path(std.agents, "scout")
  dump_doc(path, {"self": {}})
  yield config, root, path, listing, loading, project
  assert not root.exists()


def test_registered_set_keeps_live_reference_raw_and_refreshes(registered_input, capsys):
  from kanibako.settings.settings_launch import snapshot_leaf

  config, root, path, listing, loading, project = registered_input
  snapshots = []
  original_snapshot = ci._set_time_snapshot

  def capture_snapshot(*args, **kwargs):
    result = original_snapshot(*args, **kwargs)
    snapshots.append(result[0])
    return result

  with (
    patch.object(ci, "set_config_value", wraps=ci.set_config_value) as setter,
    patch.object(ci, "_set_time_snapshot", side_effect=capture_snapshot),
  ):
    assert _run("set", "Scout", "env.MODEL={agent.scout.model}") == 0
  assert snapshots and all(
    snapshot_leaf(s, "agent.scout.model") == "live-model" for s in snapshots
  )
  assert not capsys.readouterr().err
  assert load_doc(path) == {"self": {"env": {"MODEL": "{agent.scout.model}"}}}
  kwargs = setter.call_args.kwargs
  assert kwargs["cascade_agent_identity"].registered_name == "Scout"
  assert kwargs["cascade_persona_values"] == {
    "model": "live-model",
    "endpoint": "https://live.invalid",
  }
  credential = kwargs["cascade_persona_credential"]
  assert credential.registered_name == "Scout" and credential.store_root == root
  assert credential.var == "TOKEN" and not credential.keyless
  assert "cascade_agent_path" not in kwargs
  assert kwargs["target_error"] is None
  listing.assert_called_once_with(root=root)
  loading.assert_called_once_with("Scout", root=root)
  project.assert_called_once()
  config["model"] = "fresh-model"
  with patch.object(ci, "set_config_value", wraps=ci.set_config_value) as setter:
    assert _run("set", "Scout", "env.NEXT={agent.scout.model}") == 0
  assert setter.call_args.kwargs["cascade_persona_values"]["model"] == "fresh-model"
  assert listing.call_count == loading.call_count == project.call_count == 2
  assert load_doc(path) == {
    "self": {"env": {"MODEL": "{agent.scout.model}", "NEXT": "{agent.scout.model}"}}
  }


@pytest.mark.parametrize("value", ["file-model", "", None, "absent"])
def test_registered_get_stays_stored_and_does_not_project(
  registered_input, monkeypatch, value, capsys
):
  _, _, path, listing, loading, project = registered_input
  dump_doc(path, {"self": {} if value == "absent" else {"model": value}})
  before = path.read_bytes()
  monkeypatch.setattr(ci, "project_agent_input", Mock(side_effect=AssertionError("stored read")))
  assert _run("get", "Scout", "model") == 0
  output = capsys.readouterr()
  if value == "absent":
    assert output.out == "" and "(not set)" in output.err
  else:
    assert output.out.strip() == render_stored_scalar(value) and not output.err
  assert path.read_bytes() == before
  assert listing.call_count == loading.call_count == 1
  project.assert_not_called()


@pytest.mark.parametrize("verb", ["run_show", "run_info"])
def test_registered_label_uses_live_model_file_precedence_and_fresh_input(
  registered_input, std, verb, capsys
):
  from kanibako.settings import settings_launch as launch

  config, _, path, listing, loading, project = registered_input
  dump_doc(std.settings, {"agent": {"scout": {"label": "{agent.scout.model}"}}})
  run = getattr(agent_cmd, verb)
  with (
    patch.object(launch, "build_launch_snapshot", wraps=launch.build_launch_snapshot) as snapshot,
    patch.object(ci, "effective_value", wraps=ci.effective_value) as effective,
  ):
    assert run(argparse.Namespace(agent_id="Scout", effective=True)) == 0
  output = capsys.readouterr()
  assert "live-model" in output.out and not output.err
  assert snapshot.call_args.kwargs["persona_values"]["model"] == "live-model"
  assert (
    effective.call_args.kwargs["persona_values"] is snapshot.call_args.kwargs["persona_values"]
  )
  assert (
    effective.call_args.kwargs["persona_credential"]
    is snapshot.call_args.kwargs["persona_credential"]
  )
  assert listing.call_count == loading.call_count == project.call_count == 1
  config["model"] = "fresh-model"
  assert run(argparse.Namespace(agent_id="Scout", effective=False)) == 0
  assert "fresh-model" in capsys.readouterr().out
  assert listing.call_count == loading.call_count == project.call_count == 2
  dump_doc(path, {"self": {"model": "file-model"}})
  before = path.read_bytes()
  assert run(argparse.Namespace(agent_id="Scout", effective=False)) == 0
  output = capsys.readouterr()
  assert "file-model" in output.out and "fresh-model" not in output.out and not output.err
  assert path.read_bytes() == before
  assert listing.call_count == loading.call_count == project.call_count == 3


@pytest.mark.parametrize("model", ["live-model", "", None, "absent"])
def test_registered_label_preserves_live_model_states(registered_input, std, model, capsys):
  from kanibako.errors import ConfigError

  config, _, path, listing, loading, project = registered_input
  if model == "absent":
    config.pop("model")
  else:
    config["model"] = model
  dump_doc(
    std.settings,
    {"agent": {"default": {"model": "default-model"}, "scout": {"label": "{agent.scout.model}"}}},
  )
  before = path.read_bytes()
  args = argparse.Namespace(agent_id="Scout", effective=False)
  if model is None:
    with pytest.raises(ConfigError, match="label"):
      agent_cmd.run_show(args)
  else:
    assert agent_cmd.run_show(args) == 0
    output = capsys.readouterr()
    expected = model if model not in ("", "absent") else "Scout"
    assert expected in output.out and not output.err
  assert listing.call_count == loading.call_count == project.call_count == 1
  assert path.read_bytes() == before


def test_registered_reset_forwards_captured_values_without_adding_an_agent_tier(
  registered_input, capsys
):
  _, _, path, listing, loading, project = registered_input
  dump_doc(path, {"self": {"model": "file-model", "env": {"KEEP": "raw"}}})
  with patch.object(ci, "reset_config_value", wraps=ci.reset_config_value) as reset:
    assert _run("reset", "Scout", "model") == 0
  output = capsys.readouterr()
  assert output.out.strip() == (
    "Cleared model set on the agent scope; it now falls back through the cascade."
  )
  assert not output.err
  kwargs = reset.call_args.kwargs
  assert kwargs["cascade_agent_identity"].registered_name == "Scout"
  assert kwargs["cascade_persona_values"]["model"] == "live-model"
  assert kwargs["cascade_persona_credential"].registered_name == "Scout"
  assert kwargs["target_error"] is None and "cascade_agent_path" not in kwargs
  assert load_doc(path) == {"self": {"env": {"KEEP": "raw"}}}
  assert listing.call_count == loading.call_count == project.call_count == 1


@pytest.mark.parametrize("verb", ["run_show", "run_info"])
def test_registered_broken_display_warns_once_but_stored_label_skips_resolution(
  registered_input, verb, capsys
):
  from kanibako.errors import ConfigError

  _, _, path, listing, loading, project = registered_input
  loading.side_effect = ConfigError("broken PG configuration")
  run = getattr(agent_cmd, verb)
  assert run(argparse.Namespace(agent_id="Scout", effective=False)) == 0
  output = capsys.readouterr()
  assert output.err.count("Label: showing the built-in default") == 1
  assert "broken PG configuration" in output.err
  dump_doc(path, {"self": {"label": "Stored label"}})
  assert run(argparse.Namespace(agent_id="Scout", effective=False)) == 0
  output = capsys.readouterr()
  assert "Stored label" in output.out and not output.err
  dump_doc(path, {"self": {"label": None}})
  with pytest.raises(ConfigError, match="label"):
    run(argparse.Namespace(agent_id="Scout", effective=False))
  assert listing.call_count == loading.call_count == 3
  project.assert_not_called()


@pytest.mark.parametrize("verb", ["set", "get", "reset"])
def test_registered_foreign_leaf_refuses_before_projection(registered_input, verb, capsys):
  _, _, path, listing, loading, project = registered_input
  before = path.read_bytes()
  assert (
    _run(
      "set" if verb == "set" else verb,
      "Scout",
      "provider=foreign" if verb == "set" else "provider",
    )
    == 1
  )
  assert "provider" in capsys.readouterr().err
  assert path.read_bytes() == before
  assert listing.call_count == loading.call_count == 1
  project.assert_not_called()


@pytest.mark.parametrize("null", [False, True])
def test_registered_broken_store_keeps_repair_and_null(registered_input, null, capsys):
  from kanibako.errors import ConfigError

  _, _, path, listing, loading, project = registered_input
  loading.side_effect = ConfigError("broken PG configuration")
  dump_doc(path, {"self": {"model": {"broken": "shape"}}})
  with patch.object(ci, "set_config_value", wraps=ci.set_config_value) as setter:
    assert _run("set", "Scout", "model" if null else "model=repaired", null=null) == 0
  assert load_doc(path) == {"self": {"model": None if null else "repaired"}}
  assert not capsys.readouterr().err
  assert setter.call_args.kwargs["target_error"] == "broken PG configuration"
  assert setter.call_args.kwargs["cascade_persona_values"] is None
  assert listing.call_count == loading.call_count == 1
  project.assert_not_called()


def test_registered_broken_store_preserves_poison_force_and_reset(
  registered_input, capsys, caplog
):
  from kanibako.errors import ConfigError

  _, _, path, listing, loading, project = registered_input
  loading.side_effect = ConfigError("broken PG configuration")
  dump_doc(path, {"self": {"model": "old"}, "bogus_stray": "keep"})
  before = path.read_bytes()
  assert _run("set", "Scout", "model=new") == 1
  assert "--force" in capsys.readouterr().err and path.read_bytes() == before
  assert _run("set", "Scout", "model=new", force=True) == 0
  assert load_doc(path) == {"self": {"model": "new"}, "bogus_stray": "keep"}
  assert _run("reset", "Scout", "model") == 0
  assert load_doc(path) == {"bogus_stray": "keep"}
  assert "broken PG configuration" in caplog.text
  assert listing.call_count == loading.call_count == 3
  project.assert_not_called()


@pytest.mark.parametrize("verb", ["run_show", "run_info"])
def test_legacy_label_projects_once_without_registered_lookup(std, monkeypatch, verb, capsys):
  from kanibako import persona_store

  node = "Guide℘claude"
  reader = Mock(return_value=persona_store.PersonaBundle(model="legacy-model"))
  monkeypatch.setattr(persona_store, "read_persona_bundle", reader)
  monkeypatch.setattr(agent_cmd, "_load_std", lambda: std)
  path = agent_settings_path(std.agents, node)
  dump_doc(path, {"self": {}})
  dump_doc(std.settings, {"agent": {node: {"label": "{agent." + node + ".model}"}}})
  before = path.read_bytes()
  with (
    patch.object(
      selection, "resolve_named_agent_input", wraps=selection.resolve_named_agent_input
    ) as resolve,
    patch.object(ci, "project_agent_input", wraps=ci.project_agent_input) as project,
  ):
    assert (
      getattr(agent_cmd, verb)(argparse.Namespace(agent_id="Guide+Claude", effective=False)) == 0
    )
  output = capsys.readouterr()
  assert "legacy-model" in output.out and not output.err
  resolve.assert_called_once_with(node)
  project.assert_called_once()
  reader.assert_called_once()
  assert reader.call_args.args[0] == node and reader.call_args.args[1].name == "claude"
  assert path.read_bytes() == before
