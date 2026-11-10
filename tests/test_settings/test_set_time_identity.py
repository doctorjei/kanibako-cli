"""Injected registered identity stays local to narrow set-time resolution."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.persona_store import read_registered_persona as _public_registered_reader
from kanibako.project.workset import create_workset
from kanibako.settings import config_interface as ci
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.config import load_config, user_config_file
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.keystore import KeyStore
from kanibako.settings.paths import resolve_project
from kanibako.settings.settings_launch import snapshot_leaf
from kanibako.settings.settings_prefs import AgentNames, collect_prefs


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
  return AgentIdentity(
    node="claude",
    registered_name="Claude",
    persona="Guide",
    harness="Goose",
    target_name="goose",
    entry_path=tmp_path / "PG" / "Claude",
  )


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


def _target(subject, std, tmp_path, identity):
  ws = create_workset("identityws", tmp_path / "identityws", std) if subject == "workset" else None
  proj = (
    resolve_project(
      std, load_config(user_config_file()), str(tmp_path / "project"), initialize=True
    )
    if subject == "box"
    else None
  )
  return ci._set_time_target(
    std=std,
    proj=proj,
    ws=ws,
    agent_name=identity.node,
    agent_identity=identity,
    system_path=std.settings,
  )


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  "pref.agent.claude.provider",
  reason="Disconnected registered Claude uses Goose; the process-wide census remains legacy-only",
)
@pytest.mark.parametrize("subject", ["box", "workset", "system"])
def test_snapshot_and_probes_share_resolved_harness(subject, std, tmp_path, identity, vocabulary):
  target = _target(subject, std, tmp_path, identity)
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": {"provider": "openai", "model": "{meta.agent.claude.name}"}})
  if subject != "system":
    pref_path = target.cascade_box_path if subject == "box" else target.cascade_workset_path
    dump_doc(pref_path, {"pref": {"agent": {"claude": {"provider": "anthropic"}}}})
    target = replace(
      target, prefs=tuple(collect_prefs(target.cascade_workset_path, target.cascade_box_path))
    )
  original = path.read_bytes()
  snapshot, _ = ci._set_time_snapshot(target=target, agent_name="claude", agent_path=path)
  assert snapshot_leaf(snapshot, "agent.claude.provider") == (
    "openai" if subject == "system" else "anthropic"
  )
  assert snapshot_leaf(snapshot, "agent.claude.model") == "{meta.agent.claude.name}"
  resolves, _ = ci._category_set_lookups(
    std.settings,
    canonical="box.env.TEST",
    target=target,
    agent_name="claude",
    agent_path=path,
    command_scope=ConfigLevel(subject),
  )
  assert resolves("box.env.TEST", "{agent.claude.provider}") is None
  assert resolves("box.env.TEST", "{meta.box.agent.provider}") is None
  assert resolves("box.env.TEST", "{agent.claude.claude_only}") is not None
  defects = ci._set_time_defects(
    std.settings,
    edit=("box.env.TEST", "{meta.box.agent.provider}"),
    command_scope=ConfigLevel(subject),
    system_settings_path=None,
    system_path=std.settings,
    workset_path=None,
    box_path=None,
    agent_name="claude",
    agent_path=path,
    target=target,
  )
  assert "box.env.TEST" not in defects
  assert path.read_bytes() == original
  assert not identity.entry_path.exists()
  assert vocabulary.leaf_map.agent_harnesses == {}
  if subject != "box":
    assert "meta.box.path" not in ci._target_scope_anchors(
      target, agent_path=path, agent_name="claude"
    )


def test_bad_entry_scan_uses_local_vocabulary(std, tmp_path, identity, vocabulary):
  target = _target("system", std, tmp_path, identity)
  names = ci._set_time_valid_agents(target)
  data = {"agent": {"claude": {"provider": "openai", "claude_only": "foreign"}}}
  dump_doc(std.settings, data)
  bad = ci._cascade_bad_entries(
    std.settings,
    ConfigLevel.system,
    system_path=std.settings,
    workset_path=None,
    box_path=None,
    valid_agents=names,
  )
  assert bad.names == ["agent.claude.claude_only"]
  assert ci._undeclared_stored_entries(data, valid_agents=names) == {
    ("agent", "claude", "claude_only"): ("agent.claude.claude_only", "foreign")
  }
  assert ("agent", "claude", "provider") in ci._undeclared_stored_entries(data)
  assert load_doc(std.settings) == data


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="The blindness probe supplies a registered Goose leaf absent from native Claude's census",
)
def test_downward_blindness_uses_same_plugin_vocabulary(identity, vocabulary):
  from types import SimpleNamespace

  names = ci._set_time_valid_agents(SimpleNamespace(agent_identity=identity))
  key, value = "box.env.TEST", "{agent.claude.provider}"
  candidate = KeyStore()
  candidate.insert_segments(key.split("."), value)
  unseen = ci._floor_blind_referents(key, value, candidate, ConfigLevel.system, valid_agents=names)
  assert unseen == ["agent.claude.provider"]
  ctx = ci._set_time_ctx()
  errors = ci._lenient_expand(candidate, ctx, "claude", valid_agents=names)[1]
  assert key in errors
  assert ci._blindness_forgiven(
    errors, candidate, ctx, "claude", ConfigLevel.system, valid_agents=names
  ) == {key}
  assert (
    ci._floor_blind_referents(
      key, "{agent.claude.claude_only}", KeyStore(), ConfigLevel.system, valid_agents=names
    )
    == []
  )


@pytest.mark.parametrize("registered", [False, True])
def test_native_identity_and_legacy_discovery_laziness(
  registered, std, tmp_path, identity, monkeypatch
):
  native = replace(
    identity, node="goose", registered_name="Goose" if registered else None, persona=None
  )
  target = _target("system", std, tmp_path, native)
  if not registered:
    monkeypatch.setattr(
      "kanibako.settings.settings_prefs.default_valid_agents",
      Mock(side_effect=AssertionError("legacy helper must remain lazy")),
    )
    assert ci._set_time_valid_agents(target) is None
    return
  snapshot, _ = ci._set_time_snapshot(target=target, agent_name="goose", agent_path=None)
  assert snapshot_leaf(snapshot, "meta.agent.goose.name") == "Goose"
  assert not identity.entry_path.exists()


def test_targetless_snapshot_keeps_raw_file_and_no_box_anchor(std, tmp_path):
  dump_doc(std.settings, {"box": {"image": "{system.image}"}})
  snapshot, _ = ci._set_time_snapshot(
    target=None,
    agent_name="",
    agent_path=None,
    config_path=user_config_file(),
    command_scope=ConfigLevel.system,
    system_settings_path=std.settings,
    system_path=std.settings,
  )
  assert snapshot_leaf(snapshot, "box.image") == "{system.image}"
  assert ci._set_time_valid_agents(None) is None
  assert ci._target_scope_anchors(None, agent_path=None, agent_name="") == {}


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="Registered Claude reads Goose's provider while the global census stays legacy-only",
)
@pytest.mark.parametrize("supplied", ("derived", "provided", "empty"))
def test_reader_snapshot_shares_one_vocabulary(
  std, tmp_path, identity, vocabulary, monkeypatch, supplied
):
  from kanibako.settings import settings_assemble as assembly
  from kanibako.settings import settings_launch as launch

  target = _target("system", std, tmp_path, identity)
  names = AgentNames(()) if supplied == "empty" else ci._set_time_valid_agents(target)
  state = {"model": "{meta.agent.claude.name}"}
  if supplied != "empty":
    state["provider"] = "ollama"
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": state, "box": {"env": {"READER": "{system.image}"}}})
  before = path.read_bytes()
  supplier = Mock(return_value=names)
  tier = Mock(wraps=ci._set_time_agent_tier)
  record = Mock(wraps=assembly.agent_record)
  assemble = Mock(wraps=launch.assemble_cascade)
  monkeypatch.setattr(ci, "_set_time_valid_agents", supplier)
  monkeypatch.setattr(ci, "_set_time_agent_tier", tier)
  monkeypatch.setattr(assembly, "agent_record", record)
  monkeypatch.setattr(launch, "assemble_cascade", assemble)
  for discovery in (
    "kanibako.settings.settings_prefs.default_valid_agents",
    "kanibako.settings.settings_keyspace_probe.plugin_agent_leaf_map",
  ):
    monkeypatch.setattr(discovery, Mock(side_effect=AssertionError("unexpected rediscovery")))
  snapshot, ctx = ci._set_time_snapshot(
    target=target,
    agent_name="claude",
    agent_path=path,
    valid_agents=None if supplied == "derived" else names,
  )
  assert supplier.call_count == (1 if supplied == "derived" else 0)
  assert tier.call_args.kwargs["valid_agents"] is names
  assert record.call_args.kwargs["valid_agents"] is names
  assert assemble.call_args.kwargs["valid_agents"] is names
  assert next(f.path for f in assemble.call_args.kwargs["files"] if f.level == "agent") == path
  assert assemble.call_args.kwargs["persona_values"] is None
  assert snapshot_leaf(snapshot, "agent.claude.model") == state["model"]
  assert snapshot_leaf(snapshot, "box.env.READER") == "{system.image}"
  if supplied != "empty":
    assert snapshot_leaf(snapshot, "agent.claude.provider") == "ollama"
  assert ctx is target.ctx
  snapshot.insert_segments(["agent", "claude", "model"], "candidate")
  assert path.read_bytes() == before
  assert not identity.entry_path.exists()
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="Registered Claude uses Goose's descriptor floor; the global census remains legacy-only",
)
@pytest.mark.parametrize("contents", (b"self: [\n", b"\xff", b"self:\n  claude_only: foreign\n"))
def test_reader_omits_invalid_file_so_unrelated_repair_can_resolve(
  std, tmp_path, identity, vocabulary, contents
):
  from kanibako.settings.settings_assemble import ReadPurpose, agent_record
  from kanibako.settings.settings_resolve import SettingsError
  from kanibako.errors import ConfigError

  target = _target("system", std, tmp_path, identity)
  names = ci._set_time_valid_agents(target)
  path = agent_settings_path(std.agents, identity.node)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(contents)
  with pytest.raises((ConfigError, SettingsError)):
    agent_record(path, node="claude", purpose=ReadPurpose.RESOLVE, valid_agents=names)
  assert ci._set_time_agent_tier("claude", path, identity=identity, valid_agents=names)[0] is None
  resolves, _ = ci._category_set_lookups(
    std.settings,
    canonical="box.env.REPAIR",
    command_scope=ConfigLevel.system,
    target=target,
    agent_name="claude",
    agent_path=path,
  )
  assert resolves("box.env.REPAIR", "repaired") is None
  assert path.read_bytes() == contents
  assert not identity.entry_path.exists()


@pytest.mark.writes_undeclared(
  "agent.claude.unknown_leaf",
  reason="Missing injected harness concedes its vocabulary; the global census sees native Claude",
)
def test_reader_missing_plugin_retains_file_and_unreadable_leaf_concession(
  std, tmp_path, identity, vocabulary, monkeypatch
):
  from kanibako.errors import ConfigError

  missing = replace(identity, harness="Missing", target_name="missing")
  target = _target("system", std, tmp_path, missing)
  names = AgentNames((), unreadable={"missing"}, agent_harnesses={"claude": "missing"})
  path = agent_settings_path(std.agents, identity.node)
  dump_doc(path, {"self": {"unknown_leaf": "raw"}})
  before = path.read_bytes()
  monkeypatch.setattr(
    "kanibako.targets.resolve_target", Mock(side_effect=ConfigError("plugin missing"))
  )
  assert ci._set_time_agent_tier("claude", path, identity=missing, valid_agents=names) == (
    path,
    None,
    None,
  )
  snapshot, _ = ci._set_time_snapshot(
    target=target, agent_name="claude", agent_path=path, valid_agents=names
  )
  assert snapshot_leaf(snapshot, "agent.claude.unknown_leaf") == "raw"
  assert path.read_bytes() == before
  assert not identity.entry_path.exists()


def test_reader_no_file_and_floor_only_do_not_discover_or_read(identity, monkeypatch):
  from kanibako.settings import settings_assemble as assembly

  monkeypatch.setattr(
    assembly, "agent_record", Mock(side_effect=AssertionError("unexpected file read"))
  )
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("unexpected discovery")),
  )
  assert ci._set_time_agent_tier("claude", None, identity=identity) == (None, None, None)
  path, behavior, plugin = ci._set_time_agent_tier(
    "claude", None, floor_only=True, identity=identity
  )
  assert path is None
  assert behavior is not None
  assert "provider" in plugin


@pytest.mark.parametrize("empty", [False, True])
def test_lenient_expander_receives_exact_supplied_vocabulary(empty, vocabulary, monkeypatch):
  from kanibako.settings import settings_expand

  names = AgentNames(()) if empty else vocabulary.with_harnesses({"claude": "goose"})
  candidate = KeyStore()
  candidate.insert_segments(["box", "env", "RESULT"], "{meta.box.agent.model}")
  candidate.insert_segments(["agent", "default", "model"], "fallback")
  expand = Mock(wraps=settings_expand.expand)
  monkeypatch.setattr(settings_expand, "expand", expand)
  for discovery in (
    "kanibako.settings.settings_prefs.default_valid_agents",
    "kanibako.settings.settings_keyspace_probe.plugin_agent_leaf_map",
  ):
    monkeypatch.setattr(discovery, Mock(side_effect=AssertionError("unexpected discovery")))
  result, errors = ci._lenient_expand(candidate, ci._set_time_ctx(), "claude", valid_agents=names)
  assert expand.call_args.kwargs["valid_agents"] is names
  assert errors == {}
  assert snapshot_leaf(result, "box.env.RESULT") == "fallback"
  assert snapshot_leaf(candidate, "box.env.RESULT") == "{meta.box.agent.model}"


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="Injected Goose or unreadable harness accepts a leaf native Claude's census cannot judge",
)
@pytest.mark.parametrize("missing", [False, True])
def test_lenient_mirror_uses_registered_or_missing_harness(missing, vocabulary, monkeypatch):
  names = (
    AgentNames((), unreadable={"missing"}, agent_harnesses={"claude": "missing"})
    if missing
    else vocabulary.with_harnesses({"claude": "goose"})
  )
  candidate = KeyStore()
  candidate.insert_segments(["agent", "claude", "provider"], "local")
  candidate.insert_segments(
    ["box", "env", "RESULT"], "{agent.claude.provider}" if missing else "{meta.box.agent.provider}"
  )
  if missing:
    candidate.insert_segments(["box", "env", "MIRROR"], "{meta.box.agent.provider}")
  candidate.insert_segments(["box", "env", "FOREIGN"], "{agent.claude.claude_only}")
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("unexpected discovery")),
  )
  result, errors = ci._lenient_expand(candidate, ci._set_time_ctx(), "claude", valid_agents=names)
  assert snapshot_leaf(result, "box.env.RESULT") == "local"
  from kanibako.settings.settings_expand import is_cascade_blindness

  assert "box.env.FOREIGN" in errors
  assert is_cascade_blindness(errors["box.env.FOREIGN"]) is missing
  assert "claude_only" in errors["box.env.FOREIGN"]
  if missing:
    from kanibako.settings.kb_store import __MISSING__

    assert snapshot_leaf(result, "box.env.MIRROR") is __MISSING__
    assert "box.env.MIRROR" in errors
    assert not is_cascade_blindness(errors["box.env.MIRROR"])


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  reason="Public setter receives registered Goose-at-Claude vocabulary without global mutation",
)
@pytest.mark.parametrize("subject", ["box", "workset", "system"])
def test_public_setter_forwards_identity_into_full_target(
  subject, std, tmp_path, identity, vocabulary, monkeypatch
):
  build = Mock(wraps=ci._set_time_target)
  monkeypatch.setattr(ci, "_set_time_target", build)
  target = _target(subject, std, tmp_path, identity)
  inputs = build.call_args.kwargs
  build.reset_mock()
  agent_path = agent_settings_path(std.agents, identity.node)
  dump_doc(agent_path, {"self": {"provider": "openai"}})
  before = agent_path.read_bytes()
  path = {
    "box": target.cascade_box_path,
    "workset": target.cascade_workset_path,
    "system": std.settings,
  }[subject]
  kwargs = dict(
    config_path=path,
    command_scope=ConfigLevel(subject),
    std=std,
    proj=inputs["proj"],
    ws=inputs["ws"],
    cascade_system_path=std.settings,
    cascade_workset_path=target.cascade_workset_path,
    cascade_box_path=target.cascade_box_path,
    cascade_agent_path=agent_path,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
  )
  message = ci.set_config_value("box.env.SELECTED", "{agent.claude.provider}", **kwargs)
  assert message.startswith("Set "), message
  assert build.call_count == 1
  assert build.call_args.kwargs["agent_identity"] is identity
  assert load_doc(path)["box"]["env"]["SELECTED"] == "{agent.claude.provider}"
  assert agent_path.read_bytes() == before
  assert not identity.entry_path.exists()
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.parametrize(
  ("node", "key", "expected"),
  [
    ("claude", "transform", None),
    ("default", "transform", None),
    ("shell", "bootstrap", "tmux"),
    ("shell", "continue_mode", None),
    ("other℘claude", "transform", "tweakcc"),
  ],
)
def test_null_default_identity_is_matching_node_only(identity, node, key, expected):
  assert ci.effective_behavior_default(node, key, identity=identity) == expected
  canonical = f"agent.{node}.{key}"
  assert ci.refuses_null_behavior_key(canonical, identity=identity) is (expected is not None)
  assert ci.refuses_null_behavior_key(f"pref.{canonical}", identity=identity) is (
    expected is not None
  )
  assert (ci._set_value_error(canonical, None, identity=identity) is not None) is (
    expected is not None
  )


@pytest.mark.parametrize("registered", [False, True])
def test_public_null_set_uses_identity_without_building_target(
  std, tmp_path, identity, vocabulary, monkeypatch, registered
):
  path = tmp_path / "box.yaml"
  dump_doc(path, {"box": {"env": {"KEEP": "unchanged"}}})
  before = path.read_bytes()
  monkeypatch.setattr(
    ci, "_set_time_target", Mock(side_effect=AssertionError("null builds no target"))
  )
  message = ci.set_config_value(
    "pref.agent.claude.transform",
    None,
    config_path=path,
    command_scope=ConfigLevel.box,
    cascade_agent_name="claude",
    cascade_agent_identity=identity if registered else None,
    std=std,
  )
  if registered:
    assert message == "Set pref.agent.claude.transform=null"
    assert load_doc(path) == {
      "box": {"env": {"KEEP": "unchanged"}},
      "pref": {"agent": {"claude": {"transform": None}}},
    }
  else:
    assert message.startswith("Error:"), message
    assert path.read_bytes() == before
  assert ci.effective_behavior_default("claude", "transform") == "tweakcc"
  assert not identity.entry_path.exists()


@pytest.mark.parametrize("force", [False, True])
def test_public_setter_identity_preserves_upstream_refusal_and_raw_file(
  std, tmp_path, identity, vocabulary, force
):
  target = _target("box", std, tmp_path, identity)
  path = target.cascade_box_path
  dump_doc(path, {"box": {"env": {"BROKEN": "{system.no_such_key}"}}})
  before = path.read_bytes()
  message = ci.set_config_value(
    "box.env.EDITED",
    "{box.env.BROKEN}",
    config_path=path,
    command_scope=ConfigLevel.box,
    std=std,
    proj=resolve_project(
      std, load_config(user_config_file()), str(tmp_path / "project"), initialize=False
    ),
    cascade_system_path=std.settings,
    cascade_workset_path=target.cascade_workset_path,
    cascade_box_path=path,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    force=force,
  )
  assert message.startswith("Error:"), message
  assert "BROKEN" in message or "no_such_key" in message
  assert path.read_bytes() == before


@pytest.mark.parametrize("force", [False, True])
def test_public_setter_identity_preserves_unrelated_defect_force(
  std, tmp_path, identity, vocabulary, force
):
  proj = resolve_project(
    std, load_config(user_config_file()), str(tmp_path / "project"), initialize=True
  )
  from kanibako.settings.paths import box_workset_settings_paths

  path, workset_path = box_workset_settings_paths(proj)
  dump_doc(path, {"box": {"env": {"BROKEN": "{system.no_such_key}"}}})
  before = path.read_bytes()
  message = ci.set_config_value(
    "box.env.EDITED",
    "fixed",
    config_path=path,
    command_scope=ConfigLevel.box,
    std=std,
    proj=proj,
    cascade_system_path=std.settings,
    cascade_workset_path=workset_path,
    cascade_box_path=path,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    force=force,
  )
  if force:
    assert "Set box.env.EDITED=fixed" in message, message
    assert load_doc(path)["box"]["env"] == {"BROKEN": "{system.no_such_key}", "EDITED": "fixed"}
  else:
    assert message.startswith("Error:"), message
    assert "force" in message
    assert path.read_bytes() == before


@pytest.mark.writes_undeclared(
  "agent.claude.provider",
  "pref.agent.claude.provider",
  reason="Synthetic registered Claude uses Goose; native discovery remains unchanged",
)
@pytest.mark.parametrize("pref", [False, True])
@pytest.mark.parametrize("value", ["ollama", "", None])
@pytest.mark.parametrize("repair", [False, True])
def test_public_write_name_gates_use_identity(
  std, tmp_path, identity, vocabulary, monkeypatch, pref, value, repair
):
  from kanibako.settings.config_keys import _pref_target_error

  target = _target("box" if pref else "system", std, tmp_path, identity)
  path = target.cascade_box_path if pref else std.settings
  names = ci.identity_valid_agents(identity)
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  gate = Mock(wraps=_pref_target_error)
  monkeypatch.setattr(ci, "_pref_target_error", gate)
  key = ("pref." if pref else "") + "agent.ClAuDe.provider"
  message = ci.set_config_value(
    key,
    value,
    config_path=path,
    system_settings_path=None if pref else std.settings,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    agents_root=std.agents,
    node_store=False,
    cascade_system_path=std.settings,
    cascade_workset_path=target.cascade_workset_path,
    cascade_box_path=target.cascade_box_path,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    std=std,
    proj=None
    if not pref
    else resolve_project(
      std, load_config(user_config_file()), str(tmp_path / "project"), initialize=False
    ),
    target_error="synthetic target failure" if repair else None,
  )
  assert (
    message
    == f"Set {'pref.' if pref else ''}agent.claude.provider={value if value is not None else 'null'}"
  )
  stored = load_doc(path)
  if pref:
    stored = stored["pref"]
    assert gate.call_count == 1
    assert gate.call_args.kwargs["valid_agents"] is names
  assert stored["agent"]["claude"]["provider"] == value
  assert not identity.entry_path.exists()
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.parametrize("pref", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_public_name_gate_refuses_foreign_leaf_even_with_force(
  std, tmp_path, identity, vocabulary, pref, force
):
  path = tmp_path / "settings.yaml"
  dump_doc(path, {"box": {"env": {"KEEP": "raw"}}})
  before = path.read_bytes()
  key = ("pref." if pref else "") + "agent.claude.claude_only"
  message = ci.set_config_value(
    key,
    "foreign",
    config_path=path,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    agents_root=std.agents,
    force=force,
  )
  assert message.startswith("Error:"), message
  assert "claude_only" in message
  assert path.read_bytes() == before
  assert not identity.entry_path.exists()


@pytest.mark.writes_undeclared(
  "agent.claude.unknown_leaf",
  reason="An unreadable injected harness retains its declared missing-plugin concession",
)
@pytest.mark.parametrize("pref", [False, True])
def test_public_name_gates_concede_missing_plugin(std, tmp_path, identity, monkeypatch, pref):
  names = AgentNames((), unreadable={"missing"}, agent_harnesses={"claude": "missing"})
  missing = replace(identity, harness="Missing", target_name="missing")
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  path = tmp_path / "settings.yaml"
  key = ("pref." if pref else "") + "agent.claude.unknown_leaf"
  message = ci.set_config_value(
    key,
    None,
    config_path=path,
    system_settings_path=None if pref else path,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    cascade_agent_name=missing.node,
    cascade_agent_identity=missing,
    agents_root=std.agents,
    node_store=False,
  )
  assert message == f"Set {key}=null"
  stored = load_doc(path)
  if pref:
    stored = stored["pref"]
  assert stored["agent"]["claude"]["unknown_leaf"] is None
  assert not identity.entry_path.exists()


@pytest.mark.parametrize("pref", [False, True])
def test_public_name_gates_preserve_explicit_empty_vocabulary(
  std, tmp_path, identity, vocabulary, monkeypatch, pref
):
  names = AgentNames(())
  monkeypatch.setattr(ci, "identity_valid_agents", lambda _identity: names)
  monkeypatch.setattr(
    "kanibako.settings.settings_prefs.default_valid_agents",
    Mock(side_effect=AssertionError("unexpected discovery")),
  )
  monkeypatch.setattr(
    "kanibako.settings.config_keys.plugin_declared_leaf_map",
    Mock(side_effect=AssertionError("unexpected leaf discovery")),
  )
  path = tmp_path / "settings.yaml"
  message = ci.set_config_value(
    ("pref." if pref else "") + "agent.claude.provider",
    None,
    config_path=path,
    command_scope=ConfigLevel.box if pref else ConfigLevel.system,
    cascade_agent_name=identity.node,
    cascade_agent_identity=identity,
    agents_root=std.agents,
  )
  assert message.startswith("Error:"), message
  assert "provider" in message
  assert not path.exists()
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.parametrize(("raw", "node"), [("ClAuDe", "claude"), ("Nav+GoOsE", "Nav℘goose")])
def test_typed_key_folds_before_leaf_judgment_but_stored_key_stays_strict(
  raw, node, identity, vocabulary, monkeypatch, caplog
):
  from kanibako.settings import config_keys
  from kanibako.settings.settings_assemble import reset_drop_warnings

  reset_drop_warnings()
  names = ci.identity_valid_agents(identity)
  monkeypatch.setattr(
    config_keys, "plugin_declared_leaf_map", Mock(side_effect=AssertionError("rediscovery"))
  )
  key = f"agent.{raw}.provider"
  assert config_keys._parse_persona_agent_key(key, valid_agents=names) == (raw, "provider")
  assert config_keys.resolve_key(key, valid_agents=names) == f"agent.{node}.provider"
  assert (
    config_keys.resolve_key(f"pref.{key}", valid_agents=names) == f"pref.agent.{node}.provider"
  )
  assert config_keys.scope_key_reason(key, valid_agents=names) is not None
  assert config_keys.scope_key_reason(f"agent.{node}.provider", valid_agents=names) is None
  assert raw in caplog.text
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.fixture
def public_input(std, tmp_path, monkeypatch):
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
  lists = Mock(return_value={"Scout": root / "Scout"})
  loads = Mock(side_effect=lambda *_a, **_k: (root / "Scout", data))
  monkeypatch.setattr(persona_store, "read_registered_persona", _public_registered_reader)
  monkeypatch.setattr(config_io, "persona_store_location", lambda: root)
  monkeypatch.setattr(config_io, "persona_store_agents", lists)
  monkeypatch.setattr(config_io, "load_persona_agent", loads)
  monkeypatch.setattr(config_io, "fetch_persona_secret", Mock(side_effect=AssertionError("fetch")))
  monkeypatch.setattr("kanibako.targets.discover_targets", lambda: {"claude": object})
  project = Mock(wraps=persona_store.project_registered_persona)
  monkeypatch.setattr(persona_store, "project_registered_persona", project)
  return config, root, lists, loads, project, ClaudeTarget()


def _operation(public_input):
  resolved = ci.addressed_agent_input("pref.agent.Scout.model")
  values, credential = ci.project_agent_input(resolved, public_input[-1])
  return resolved, values, credential


def test_addressed_input_projects_once_without_credential_fetch(public_input):
  config, root, lists, loads, project, _ = public_input
  resolved, values, credential = _operation(public_input)
  assert resolved.identity.registered_name == "Scout"
  assert resolved.identity.node == "scout"
  assert values == {"endpoint": "https://live.invalid", "model": "live-model"}
  assert credential.registered_name == "Scout"
  assert credential.store_root == root
  assert credential.var == "TOKEN"
  assert not credential.keyless
  lists.assert_called_once_with(root=root)
  loads.assert_called_once_with("Scout", root=root)
  project.assert_called_once()
  assert config == {"model": "live-model", "env": {"ANTHROPIC_BASE_URL": "https://live.invalid"}}
  assert not root.exists()


@pytest.mark.parametrize("subject", ["box", "workset", "system"])
def test_live_input_reaches_real_setter_and_stays_raw(
  subject, std, tmp_path, public_input, vocabulary
):
  resolved, values, credential = _operation(public_input)
  target = _target(subject, std, tmp_path, resolved.identity)
  proj = (
    resolve_project(
      std, load_config(user_config_file()), str(tmp_path / "project"), initialize=False
    )
    if subject == "box"
    else None
  )
  ws = create_workset("livews", tmp_path / "livews", std) if subject == "workset" else None
  if ws is not None:
    target = ci._set_time_target(
      std=std,
      proj=None,
      ws=ws,
      agent_name="scout",
      agent_identity=resolved.identity,
      system_path=std.settings,
    )
  path = target.cascade_box_path or target.cascade_workset_path or std.settings
  agent_path = agent_settings_path(std.agents, "scout")
  kwargs = dict(
    config_path=path,
    command_scope=ConfigLevel(subject),
    std=std,
    proj=proj,
    ws=ws,
    cascade_system_path=std.settings,
    cascade_agent_path=agent_path,
    cascade_workset_path=target.cascade_workset_path,
    cascade_box_path=target.cascade_box_path,
    cascade_agent_name="scout",
    cascade_agent_identity=resolved.identity,
    cascade_persona_values=values,
    cascade_persona_credential=credential,
  )
  message = ci.set_config_value("box.env.MODEL", "{agent.scout.model}", **kwargs)
  assert message == "Set box.env.MODEL={agent.scout.model}"
  assert load_doc(path)["box"]["env"]["MODEL"] == "{agent.scout.model}"
  assert not agent_path.exists()
  assert (
    public_input[2].call_count == public_input[3].call_count == public_input[4].call_count == 1
  )
  assert vocabulary.leaf_map.agent_harnesses == {}


@pytest.mark.parametrize("model", ["new-model", "", None, "absent"])
def test_live_model_states_file_precedence_and_fresh_operations(
  model, std, tmp_path, public_input, vocabulary
):
  public_input[0]["model"] = model
  if model == "absent":
    public_input[0].pop("model")
  resolved, values, credential = _operation(public_input)
  target = _target("system", std, tmp_path, resolved.identity)
  path = agent_settings_path(std.agents, "scout")
  dump_doc(std.settings, {"agent": {"default": {"model": "default-model"}}})
  names = ci.identity_valid_agents(resolved.identity)
  snapshot, _ = ci._set_time_snapshot(
    target=target,
    agent_name="scout",
    agent_path=path,
    valid_agents=names,
    persona_values=values,
    persona_credential=credential,
  )
  expected = "default-model" if model == "absent" else model
  expanded, errors = ci._lenient_expand(snapshot, target.ctx, "scout", valid_agents=names)
  assert not errors
  from kanibako.settings.settings_launch import _agent_pick_node

  assert snapshot_leaf(_agent_pick_node(expanded, "scout"), "model") == expected
  dump_doc(path, {"self": {"model": "file-model"}})
  before = path.read_bytes()
  snapshot, _ = ci._set_time_snapshot(
    target=target,
    agent_name="scout",
    agent_path=path,
    valid_agents=names,
    persona_values=values,
    persona_credential=credential,
  )
  assert snapshot_leaf(snapshot, "agent.scout.model") == "file-model"
  public_input[0]["model"] = "fresh-model"
  fresh, new_values, new_credential = _operation(public_input)
  assert new_values["model"] == "fresh-model"
  assert fresh is not resolved and new_values is not values and new_credential is not credential
  assert (
    public_input[2].call_count == public_input[3].call_count == public_input[4].call_count == 2
  )
  assert path.read_bytes() == before
  assert not public_input[1].exists()


def test_live_fallback_keeps_tiers_force_and_installed_vocabulary(
  std, tmp_path, public_input, vocabulary
):
  resolved, values, credential = _operation(public_input)
  agent_path = agent_settings_path(std.agents, "scout")
  workset_path = tmp_path / "workset.yaml"
  box_path = tmp_path / "box.yaml"
  dump_doc(agent_path, {"self": {"model": "file-model"}})
  dump_doc(workset_path, {"box": {"env": {"UPSTREAM": "{agent.scout.model}"}}})
  dump_doc(box_path, {"box": {"env": {"BROKEN": "{system.no_such_key}"}}})
  kwargs = dict(
    config_path=box_path,
    command_scope=ConfigLevel.box,
    std=std,
    cascade_system_path=std.settings,
    cascade_agent_path=agent_path,
    cascade_workset_path=workset_path,
    cascade_box_path=box_path,
    cascade_agent_name="scout",
    cascade_agent_identity=resolved.identity,
    cascade_persona_values=values,
    cascade_persona_credential=credential,
    target_error="synthetic unavailable input",
  )
  before = box_path.read_bytes()
  rejected = ci.set_config_value("box.env.MODEL", "{box.env.UPSTREAM}", **kwargs)
  assert rejected.startswith("Error:") and "force" in rejected
  assert box_path.read_bytes() == before
  accepted = ci.set_config_value("box.env.MODEL", "{box.env.UPSTREAM}", force=True, **kwargs)
  assert accepted == "Set box.env.MODEL={box.env.UPSTREAM}"
  snapshot, ctx = ci._set_time_snapshot(
    target=None,
    config_path=box_path,
    command_scope=ConfigLevel.box,
    system_path=std.settings,
    agent_path=agent_path,
    workset_path=workset_path,
    box_path=box_path,
    agent_name="scout",
    persona_values=values,
    persona_credential=credential,
    valid_agents=ci.identity_valid_agents(resolved.identity),
  )
  expanded, errors = ci._lenient_expand(
    snapshot, ctx, "scout", valid_agents=ci.identity_valid_agents(resolved.identity)
  )
  assert snapshot_leaf(expanded, "box.env.MODEL") == "file-model"
  assert "box.env.BROKEN" in errors
  before = box_path.read_bytes()
  for force in (False, True):
    message = ci.set_config_value("box.env.EDITED", "{box.env.BROKEN}", force=force, **kwargs)
    assert message.startswith("Error:") and "synthetic unavailable input" in message
    assert box_path.read_bytes() == before
  message = ci.set_config_value("pref.agent.scout.foreign_leaf", None, force=True, **kwargs)
  assert message.startswith("Error:") and "foreign_leaf" in message
  assert box_path.read_bytes() == before
  assert (
    ci.set_config_value("box.env.MODEL", None, force=True, **kwargs) == "Set box.env.MODEL=null"
  )


def test_get_and_reset_keep_live_input_and_unavailable_repair(
  std, tmp_path, public_input, vocabulary, caplog
):
  resolved, values, credential = _operation(public_input)
  target = _target("system", std, tmp_path, resolved.identity)
  agent_path = agent_settings_path(std.agents, "scout")
  dump_doc(std.settings, {"box": {"image": "{agent.scout.model}"}})
  box = tmp_path / "box.yaml"
  dump_doc(box, {"box": {"image": "box-image"}})
  kwargs = dict(
    cascade_system_path=std.settings,
    cascade_agent_path=agent_path,
    cascade_agent_name="scout",
    cascade_agent_identity=resolved.identity,
    cascade_persona_values=values,
    cascade_persona_credential=credential,
  )
  get_kwargs = {
    key: value
    for key, value in kwargs.items()
    if key not in ("cascade_agent_path", "cascade_agent_name")
  }
  value = ci.get_config_value(
    "box.image",
    global_config_path=user_config_file(),
    system_settings_path=std.settings,
    command_scope=ConfigLevel.system,
    active_agent="scout",
    std=std,
    **get_kwargs,
  )
  assert value == "{agent.scout.model}"
  assert "dangling" not in caplog.text
  assert ci.effective_value(
    "agent.scout.model",
    ("agent", "scout"),
    "model",
    agent_name="scout",
    system_path=std.settings,
    agent_path=agent_path,
    workset_path=None,
    box_path=None,
    inputs=target,
    valid_agents=ci.identity_valid_agents(resolved.identity),
    persona_values=values,
    persona_credential=credential,
  ) == ("live-model", "persona store")
  message = ci.reset_config_value(
    "box.image",
    config_path=box,
    command_scope=ConfigLevel.box,
    cascade_box_path=box,
    **kwargs,
  )
  assert "effective is now live-model (system)" in message
  assert load_doc(box) == {}
  dump_doc(box, {"box": {"image": "again"}})
  assert (
    ci.get_config_value(
      "box.image",
      global_config_path=user_config_file(),
      project_toml=box,
      command_scope=ConfigLevel.box,
      target_error="broken PG",
      **get_kwargs,
    )
    == "again"
  )
  message = ci.reset_config_value(
    "box.image",
    config_path=box,
    command_scope=ConfigLevel.box,
    cascade_box_path=box,
    target_error="broken PG",
    **kwargs,
  )
  assert "falls back through the cascade" in message
  assert "effective is now" not in message
  assert "broken PG" in caplog.text
  assert (
    public_input[2].call_count == public_input[3].call_count == public_input[4].call_count == 1
  )


def test_unavailable_input_does_not_project_or_fetch(tmp_path, monkeypatch):
  from kanibako.settings.config import AgentResolution
  from kanibako.plugins.claude.target import ClaudeTarget

  failure = RuntimeError("broken root")
  identity = AgentIdentity("scout", None, "scout", "Scout", "Scout", None)
  resolved = AgentResolution(identity, unavailable=failure)
  monkeypatch.setattr(ci, "resolve_named_agent_input", lambda _name: resolved)
  project = Mock(side_effect=AssertionError("unavailable input cannot project"))
  monkeypatch.setattr("kanibako.persona_store.project_registered_persona", project)
  assert ci.addressed_agent_input("agent.Scout.model") is resolved
  assert ci.addressed_agent_identity("agent.Scout.model") is identity
  assert ci.project_agent_input(resolved, ClaudeTarget()) == (None, None)
  project.assert_not_called()
  assert resolved.unavailable is failure


@pytest.mark.parametrize("kind", ["native", "legacy", "missing", "rejected"])
def test_projection_keeps_native_and_legacy_contracts(kind, monkeypatch):
  from kanibako.errors import ConfigError
  from kanibako.persona_store import PersonaBundle
  from kanibako.plugins.claude.target import ClaudeTarget
  from kanibako.settings.config import AgentResolution

  node = "claude" if kind == "native" else "Guide℘claude"
  identity = AgentIdentity(node, None, "Guide", "claude", "claude", None)
  bundle = (
    None
    if kind == "missing"
    else PersonaBundle(reject_reason="bad legacy config")
    if kind == "rejected"
    else PersonaBundle(model="legacy-model")
  )
  reader = Mock(return_value=bundle)
  monkeypatch.setattr("kanibako.persona_store.read_persona_bundle", reader)
  if kind == "rejected":
    with pytest.raises(ConfigError, match="bad legacy config"):
      ci.project_agent_input(AgentResolution(identity), ClaudeTarget())
  else:
    values, credential = ci.project_agent_input(AgentResolution(identity), ClaudeTarget())
    assert values == ({"model": "legacy-model"} if kind == "legacy" else None)
    assert credential is None
  assert reader.call_count == (0 if kind == "native" else 1)


def test_projection_keeps_keyless_metadata_without_fetch(public_input):
  from kanibako.settings.config import AgentResolution

  resolved = ci.addressed_agent_input("agent.Scout.model")
  keyless = replace(resolved.registered_persona, keyless=True, auth_env=None)
  values, credential = ci.project_agent_input(
    AgentResolution(resolved.identity, keyless), public_input[-1]
  )
  assert values["model"] == "live-model"
  assert credential.keyless and credential.var is None
  assert credential.store_root == public_input[1]
  assert (
    public_input[2].call_count == public_input[3].call_count == public_input[4].call_count == 1
  )
