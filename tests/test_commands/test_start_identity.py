"""Disconnected registered identities in focused launch reads, not selector activation."""

import json
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import start
from kanibako.errors import ConfigError
from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.goose import GooseTarget
from kanibako.settings.settings_keyspace import KeyClass, key_class
from kanibako.settings.settings_keyspace_probe import keyspace_verdict
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.agent_select import AgentSelection
from kanibako.settings.config_io import dump_doc
from tests.test_commands.test_start import _focused_box, _focused_std


@pytest.fixture
def registered(tmp_path):
  return AgentSelection(
    identity=AgentIdentity(
      node="navigator",
      registered_name="Navigator",
      persona="Guide",
      harness="Claude",
      target_name="claude",
      entry_path=tmp_path / "PG" / "Navigator",
    ),
    source="settings",
  )


@pytest.fixture
def no_persona_read():
  with (
    patch(
      "kanibako.persona_store.read_registered_persona",
      side_effect=AssertionError("focused reads cannot open PG"),
    ) as registered,
    patch(
      "kanibako.commands.start._persona_bundle_for",
      side_effect=AssertionError("focused reads cannot open the legacy store"),
    ) as legacy,
  ):
    yield
    registered.assert_not_called()
    legacy.assert_not_called()


@pytest.mark.usefixtures("config_file", "no_persona_read")
class TestFocusedRegisteredIdentity:
  @pytest.mark.parametrize("reader", ["focused", "scalars", "snapshot", "narrow"])
  @pytest.mark.parametrize("source", ["agent", "system", "pref"])
  @pytest.mark.parametrize(
    "node,target_name",
    [
      ("navigator", "claude"),
      pytest.param(
        "claude",
        "goose",
        marks=pytest.mark.writes_undeclared(
          "agent.claude.provider",
          "pref.agent.claude.provider",
          reason="Disconnected registered spelling uses Goose; the process-wide census stays legacy-only",
        ),
      ),
    ],
  )
  def test_floors_and_plugin_vocabulary_use_resolved_identity(
    self, tmp_path, registered, reader, source, node, target_name, monkeypatch
  ):
    std = _focused_std()
    proj = _focused_box(tmp_path)
    identity = replace(
      registered.identity, node=node, harness=target_name.title(), target_name=target_name
    )
    selection = replace(registered, identity=identity)
    target = GooseTarget() if target_name == "goose" else ClaudeTarget()
    agent_path = agent_settings_path(std.agents, node)
    vocabulary = {"provider": "openai"} if target_name == "goose" else {"transform": "tweakcc"}
    dump_doc(
      agent_path,
      {
        "self": vocabulary if source == "agent" else {},
        "box": {"image": f"{{meta.agent.{node}.name}}"},
      },
    )
    system_path = tmp_path / "system.yaml" if source == "system" else None
    if system_path is not None:
      dump_doc(system_path, {"agent": {node: vocabulary}})
    if source == "pref":
      dump_doc(proj.metadata_path / "box.yaml", {"pref": {"agent": {node: vocabulary}}})
    probe_path = tmp_path / "keyspace.jsonl"
    monkeypatch.setenv("KANI_KEYSPACE_PROBE", "1")
    monkeypatch.setenv("KANI_KEYSPACE_PROBE_FILE", str(probe_path))
    before = keyspace_verdict("agent.claude.provider")
    assert before.cls is KeyClass.UNDECLARED
    if reader == "scalars":
      merged = start._box_scalars(std, proj, node, system_path, {}, agent_identity=identity)
      assert merged.box_image == "Navigator"
      assert json.loads(probe_path.read_text().splitlines()[-1])["count"] == 0
      assert keyspace_verdict("agent.claude.provider") == before
      return
    if reader == "focused":
      snapshot = start._focused_agent_snapshot(
        proj,
        system_path,
        node,
        std=std,
        selection_level=selection.selection_level,
        key="bootstrap",
        floor="tmux",
        agent_path=agent_path,
        agent_identity=identity,
      )
    else:
      snapshot, _ = start._resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name=node,
        agent_identity=identity,
        system_settings_path=system_path,
        agent_cfg_path=agent_path,
        desc=None,
        install=None,
        target=target if reader == "snapshot" else None,
        include_base_families=reader == "snapshot",
        cli_level=selection.selection_level,
      )
    assert json.loads(probe_path.read_text().splitlines()[-1])["count"] == 0
    assert keyspace_verdict("agent.claude.provider") == before
    active = snapshot["meta"]["agent"][node]
    assert active["name"] == "Navigator"
    assert active["path"] == str(std.agents / node)
    assert active["settings"] == str(agent_path)
    assert snapshot["meta"]["agent"][target_name]["path"] == str(std.agents / target_name)
    assert active["auth"]["share_support"] is True
    assert active["exec"] == list(target.descriptor.operations["exec"].fragment)
    assert snapshot["system"]["agent"] == node
    assert snapshot["agent"][node]["provider" if target_name == "goose" else "transform"] == (
      "openai" if target_name == "goose" else "tweakcc"
    )
    from kanibako.settings.settings_assemble import dotted_partial
    from kanibako.settings.settings_launch import _path_key_leaves, _refuse_ambiguous_path_values

    path_store = dotted_partial({"agent.claude.template": "relative"})
    deny_agent = partial(key_class, valid_agents=())
    assert _path_key_leaves(path_store) == [("agent.claude.template", "relative")]
    assert _path_key_leaves(path_store, oracle=deny_agent) == []
    ctx = ResolveCtx(agent_name="claude", workset_name=None, host_home=str(tmp_path), xdg={})
    _refuse_ambiguous_path_values(
      [(path_store, agent_path, None)], snapshot, ctx=ctx, oracle=deny_agent
    )
    with pytest.raises(SettingsError, match=r"agent\.claude\.template"):
      _refuse_ambiguous_path_values([(path_store, agent_path, None)], snapshot, ctx=ctx)

  @pytest.mark.parametrize("source", ["agent", "pref", "default"])
  def test_bootstrap_keeps_its_answering_slot_and_origin(self, tmp_path, registered, source):
    proj = _focused_box(tmp_path)
    system_path = tmp_path / "system.yaml"
    dump_doc(system_path, {"agent": {"default": {"bootstrap": "zellij"}}})
    agent_path = agent_settings_path(_focused_std().agents, registered.node)
    if source == "agent":
      dump_doc(agent_path, {"self": {"bootstrap": "none"}})
    elif source == "pref":
      dump_doc(
        proj.metadata_path / "box.yaml",
        {
          "pref": {"agent": {registered.node: {"bootstrap": "none"}}},
        },
      )
    choice = start._bootstrap_choice(
      proj,
      system_path,
      registered.node,
      std=_focused_std(),
      selection_level=registered.selection_level,
      agent_path=agent_path,
      agent_identity=registered.identity,
    )
    assert choice.node == "navigator"
    assert choice.program == ("zellij" if source == "default" else "none")
    assert choice.from_default is (source == "default")
    assert (choice.pref is not None) is (source == "pref")
    assert start._bootstrap_setting(choice).startswith(
      "pref.agent.navigator.bootstrap"
      if source == "pref"
      else "agent.default.bootstrap"
      if source == "default"
      else "agent.navigator.bootstrap"
    )

  @pytest.mark.parametrize("reader", ["scalars", "snapshot"])
  @pytest.mark.parametrize("source", ["file", "pref", "system"])
  def test_transform_reads_the_selected_plugins_keys(self, tmp_path, registered, reader, source):
    proj = _focused_box(tmp_path)
    path = agent_settings_path(_focused_std().agents, registered.node)
    dump_doc(
      path,
      {
        "self": {
          "transform": "tweakcc",
          "transform_settings": {"theme": "dark"},
        }
      },
    )
    kwargs = dict(
      std=_focused_std(),
      selection_level=registered.selection_level,
      agent_cfg_path=path,
      agent_identity=registered.identity,
    )
    assert (
      start._effective_transform(
        proj,
        None,
        registered.node,
        ClaudeTarget(),
        **kwargs,
      )
      == "tweakcc"
    )
    assert start._effective_transform_settings(
      proj,
      None,
      registered.node,
      **kwargs,
    ) == {"theme": "dark"}

    identity = replace(registered.identity, node="goose", harness="Claude", target_name="claude")
    agent_path = agent_settings_path(_focused_std().agents, "goose")
    system_path = tmp_path / "system.yaml" if source == "system" else None
    if source == "file":
      dump_doc(agent_path, {"self": {"provider": "openai"}})
    elif source == "system":
      dump_doc(system_path, {"agent": {"goose": {"provider": "openai"}}})
    else:
      dump_doc(
        proj.metadata_path / "box.yaml", {"pref": {"agent": {"goose": {"provider": "openai"}}}}
      )
    with pytest.raises(SettingsError, match=r"agent\.goose\.provider"):
      if reader == "scalars":
        start._box_scalars(_focused_std(), proj, "goose", system_path, {}, agent_identity=identity)
      else:
        start._resolve_launch_snapshot(
          std=_focused_std(),
          proj=proj,
          agent_name="goose",
          agent_identity=identity,
          system_settings_path=system_path,
          agent_cfg_path=agent_path,
          desc=None,
          install=None,
          include_base_families=False,
          cli_level={"system.agent": "goose"},
        )

  def test_registered_pref_null_is_named_before_launch(self, tmp_path, registered):
    proj = _focused_box(tmp_path)
    path = proj.metadata_path / "box.yaml"
    dump_doc(path, {"pref": {"agent": {registered.node: {"access": None}}}})
    kwargs = dict(
      std=_focused_std(),
      rules=[start._access_null("access")],
      agent_identity=registered.identity,
    )
    refusal = start._null_behavior_refusal(
      proj,
      None,
      registered.node,
      ClaudeTarget(),
      level=registered.selection_level,
      **kwargs,
    )
    assert str(path) in refusal
    assert "pref.agent.navigator.access" in refusal
    assert "navigator+claude" not in refusal
    assert (
      start._null_behavior_refusal(
        proj,
        None,
        registered.node,
        ClaudeTarget(),
        level={**registered.selection_level, "agent.navigator.access": "restricted"},
        **kwargs,
      )
      is None
    )

  @pytest.mark.parametrize("fail", [False, True])
  def test_host_heuristic_keeps_registered_node_and_read_errors(
    self,
    std,
    project_dir,
    registered,
    fail,
  ):
    path = agent_settings_path(std.agents, registered.node)
    if fail:
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text("self:\n  bootstrap: [unclosed\n")
    else:
      dump_doc(path, {"self": {"bootstrap": "zellij"}})
    with (
      patch("kanibako.settings.agent_select.select_agent", return_value=registered),
      patch("kanibako.commands.start.resolve_target", return_value=ClaudeTarget()) as target,
    ):
      if fail:
        with pytest.raises(
          ConfigError, match="not valid Yapyon:.*unclosed bracket at end of file"
        ) as exc:
          start._resolve_bootstrap_program(str(project_dir))
        assert str(path) in str(exc.value)
        assert path.read_bytes() == b"self:\n  bootstrap: [unclosed\n"
      else:
        choice = start._resolve_bootstrap_program(str(project_dir))
        assert (choice.program, choice.node) == ("zellij", "navigator")
    target.assert_called_once_with("claude")


class TestMainLaunchIdentity:
  @pytest.mark.parametrize("stop_at", ["bootstrap_choice", "null_behavior_refusal"])
  def test_launch_reaches_focused_reads_with_identity(self, start_mocks, registered, stop_at):
    with start_mocks() as m:
      m.load_std_paths.return_value.agents = (
        registered.identity.entry_path.parent / "kanibako-agents"
      )
      m.resolve_agent.return_value = registered
      seam = getattr(m, stop_at)
      seam.side_effect = ConfigError("stop at focused read")
      with pytest.raises(ConfigError, match="stop at focused read"):
        start._run_container(
          project_dir=None,
          entrypoint=None,
          image_override=None,
          new_session=False,
          safe_mode=False,
          resume_mode=False,
          extra_args=[],
        )
      call = seam.call_args
      assert call.args[2] == "navigator"
      assert call.kwargs["agent_identity"] is registered.identity
      level_key = "selection_level" if stop_at == "bootstrap_choice" else "level"
      assert call.kwargs[level_key] == {"system.agent": "navigator"}
      assert Path(call.kwargs["agent_path"]).parent.name == "navigator"
      m.resolve_target.assert_called_once_with("claude")
      m.runtime.run.assert_not_called()

  @pytest.mark.parametrize("use_registered", [False, True])
  def test_main_transform_reads_carry_the_same_identity(
    self, start_mocks, registered, use_registered
  ):
    with (
      start_mocks() as m,
      patch(
        "kanibako.commands.start._effective_transform_settings",
        return_value={},
      ) as settings,
      patch.object(start, "ensure_persona_share_symlinks") as share,
    ):
      if use_registered:
        m.resolve_agent.return_value = registered
        m.load_std_paths.return_value.agents = registered.identity.entry_path.parent / "agents"
      identity = m.resolve_agent.return_value.identity
      assert (
        start._run_container(
          project_dir=None,
          entrypoint=None,
          image_override=None,
          new_session=False,
          safe_mode=False,
          resume_mode=False,
          extra_args=[],
        )
        == 0
      )
      assert share.call_args.kwargs["agent_identity"] is identity
      assert m.effective_transform.call_args.kwargs["agent_identity"] is identity
      assert settings.call_args.kwargs["agent_identity"] is identity
      assert m.load_merged_config.call_args.kwargs["inputs"].agent_identity is identity
      main_calls = [
        call for call in m.resolve_launch_snapshot.call_args_list if call.kwargs.get("realize")
      ]
      assert len(main_calls) == 1
      assert main_calls[0].kwargs["agent_identity"] is identity
      narrow_calls = [
        call for call in m.resolve_launch_snapshot.call_args_list if call.kwargs.get("budget_path")
      ]
      assert len(narrow_calls) == 1
      assert narrow_calls[0].kwargs["agent_identity"] is identity

  def test_heuristic_never_autodetects_a_nodeless_selection(self, config_file, project_dir):
    from tests.support.agent_selection import agent_selection

    with (
      patch(
        "kanibako.settings.agent_select.select_agent",
        return_value=agent_selection(
          node="",
          source="suppressed",
        ),
      ),
      patch("kanibako.commands.start.resolve_target") as target,
    ):
      choice = start._resolve_bootstrap_program(str(project_dir))
    target.assert_not_called()
    assert choice.node == "shell"
    assert choice.program == "tmux"
