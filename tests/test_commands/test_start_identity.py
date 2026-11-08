"""Disconnected registered identities in focused launch reads, not selector activation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import start
from kanibako.errors import ConfigError
from kanibako.plugins.claude import ClaudeTarget
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
  def test_floors_and_plugin_vocabulary_use_resolved_identity(self, tmp_path, registered):
    std = _focused_std()
    snapshot = start._focused_agent_snapshot(
      _focused_box(tmp_path),
      None,
      registered.node,
      std=std,
      selection_level=registered.selection_level,
      key="bootstrap",
      floor="tmux",
      agent_identity=registered.identity,
    )
    active = snapshot["meta"]["agent"]["navigator"]
    assert active["name"] == "Navigator"
    assert active["path"] == str(std.agents / "navigator")
    assert active["settings"] == str(agent_settings_path(std.agents, "navigator"))
    assert snapshot["meta"]["agent"]["claude"]["path"] == str(std.agents / "claude")
    assert active["auth"]["share_support"] is True
    assert active["exec"] == list(ClaudeTarget().descriptor.operations["exec"].fragment)
    assert snapshot["system"]["agent"] == "navigator"

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

  def test_transform_reads_the_selected_plugins_keys(self, tmp_path, registered):
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
        with pytest.raises(ConfigError, match="not valid YAML"):
          start._resolve_bootstrap_program(str(project_dir))
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

  def test_main_transform_reads_carry_the_same_identity(self, start_mocks):
    with (
      start_mocks() as m,
      patch(
        "kanibako.commands.start._effective_transform_settings",
        return_value={},
      ) as settings,
    ):
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
      assert m.effective_transform.call_args.kwargs["agent_identity"] is identity
      assert settings.call_args.kwargs["agent_identity"] is identity

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
