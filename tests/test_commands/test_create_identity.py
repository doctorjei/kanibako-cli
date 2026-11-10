"""Create-time identity forwarding and captured input."""

import argparse
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import start
from kanibako.plugins.goose import GooseTarget
from kanibako.settings.agent_config import agent_settings_path
from kanibako.settings.agent_select import AgentSelection
from tests.test_commands.test_start import _focused_box, _focused_std


@pytest.fixture
def identity(tmp_path):
  return AgentIdentity("claude", "Claude", "Guide", "Goose", "goose", tmp_path / "PG" / "Claude")


@pytest.mark.usefixtures("config_file")
class TestCreateIdentity:
  @pytest.mark.parametrize("reader", ["verdict", "seed"])
  def test_registered_node_keeps_target_path_and_warn_only_preflight(
    self, tmp_path, identity, reader
  ):
    std = _focused_std()
    proj = _focused_box(tmp_path)
    selection = AgentSelection(identity, "settings")
    target = GooseTarget()
    auth = SimpleNamespace(creds_shared=False)
    calls = Mock()
    with (
      patch("kanibako.settings.agent_select.select_agent", return_value=selection),
      patch.object(start, "resolve_target", return_value=target) as resolve,
      patch.object(start, "_persona_bundle_for", side_effect=AssertionError("legacy read")),
      patch(
        "kanibako.persona_store.read_registered_persona", side_effect=AssertionError("PG read")
      ),
      patch.object(
        start, "_resolve_box_launch_decisions", return_value=(auth, None, "", {})
      ) as decisions,
      patch.object(start, "_preflight_persona_load", return_value=(None, None, None)) as preflight,
      patch.object(start.agent_file, "save") as save,
      patch.object(start, "ensure_persona_share_symlinks") as share,
      patch.object(start, "_seed_box_home") as seed,
      patch.object(start, "_sync_box_at_create") as sync,
      patch.object(target, "detect", return_value=None),
    ):
      calls.attach_mock(share, "share")
      calls.attach_mock(seed, "seed")
      calls.attach_mock(sync, "sync")
      if reader == "verdict":
        assert start.persona_create_verdict(std, None, proj) is None
        share.assert_not_called()
        save.assert_not_called()
        seed.assert_not_called()
        sync.assert_not_called()
      else:
        start.seed_new_box(std, None, proj)
        share.assert_called_once_with(std, "claude", target, agent_identity=identity)
        assert [call[0] for call in calls.mock_calls] == ["share", "seed", "sync"]
        assert save.call_args.args[0] == agent_settings_path(std.agents, "claude")
        assert seed.call_args.kwargs["agent_id"] == "claude"
        assert seed.call_args.kwargs["agent_identity"] is identity
        assert sync.call_args.kwargs["agent_name"] == "claude"
        assert sync.call_args.kwargs["agent_identity"] is identity
        assert sync.call_args.kwargs["persona_values"] is None
      resolve.assert_called_once_with("goose")
      args = decisions.call_args.kwargs
      assert args["agent_name"] == "claude"
      assert args["agent_cfg_path"] == agent_settings_path(std.agents, "claude")
      assert args["agent_identity"] is identity
      assert args["target"] is target
      assert args["selection_level"] == {"system.agent": "claude"}
      assert args["persona_values"] is None
      assert preflight.call_args.args[0] == "claude"
      assert preflight.call_args.kwargs["probe"] is False
      assert preflight.call_args.kwargs["bundle"] is None

  def test_seed_home_forwards_identity_without_changing_order(self, tmp_path, identity):
    std = _focused_std()
    proj = _focused_box(tmp_path)
    calls = Mock()
    with (
      patch.object(start, "_apply_init_seeds", return_value=object()) as apply,
      patch.object(start, "_install_box_handbook") as handbook,
    ):
      calls.attach_mock(apply, "apply")
      calls.attach_mock(handbook, "handbook")
      start._seed_box_home(
        std=std,
        proj=proj,
        target=None,
        desc=None,
        agent_id=identity.node,
        agent_identity=identity,
        agent_cfg_path=None,
        system_settings_path=None,
        auth_src=SimpleNamespace(creds_shared=False),
        logger=Mock(),
        selection_level={"system.agent": identity.node},
      )
      assert apply.call_args.kwargs["agent_identity"] is identity
      assert handbook.call_args.kwargs["snapshot"] is apply.return_value
      assert [call[0] for call in calls.mock_calls] == ["apply", "handbook"]

  @pytest.mark.parametrize("reader", ["seed", "sync"])
  def test_snapshot_receives_identity_and_keeps_resolve_width(self, tmp_path, identity, reader):
    std = _focused_std()
    proj = _focused_box(tmp_path)
    kwargs = dict(
      std=std,
      proj=proj,
      agent_name=identity.node,
      agent_identity=identity,
      target=GooseTarget(),
      global_config_path=None,
      agent_config_path=None,
      logger=Mock(),
      selection_level={"system.agent": identity.node},
    )
    with (
      patch.object(
        start, "_resolve_launch_snapshot", return_value=(object(), SimpleNamespace(declared_by={}))
      ) as resolve,
      patch.object(start, "_launch_seed_list", return_value=[]),
      patch.object(start, "_launch_bind_map", return_value={}),
      patch.object(start, "_apply_synced_copies") as sync,
    ):
      if reader == "seed":
        start._apply_init_seeds(**kwargs)
        assert resolve.call_args.kwargs["include_base_families"] is False
        assert "persona_values" not in resolve.call_args.kwargs
        sync.assert_not_called()
      else:
        start._sync_box_at_create(**kwargs)
        assert resolve.call_args.kwargs.get("include_base_families", True) is True
        assert sync.call_args.kwargs["skip_if"] is None
      assert resolve.call_args.kwargs["agent_identity"] is identity

  def test_node_less_selection_never_autodetects_target(self, tmp_path):
    std = _focused_std()
    proj = _focused_box(tmp_path)
    identity = AgentIdentity("", None, None, None, "", None)
    with (
      patch(
        "kanibako.settings.agent_select.select_agent",
        return_value=AgentSelection(identity, "settings"),
      ),
      patch.object(start, "resolve_target", side_effect=AssertionError("auto-detect")),
      patch.object(start, "_persona_bundle_for", return_value=None),
      patch.object(
        start,
        "_resolve_box_launch_decisions",
        return_value=(SimpleNamespace(creds_shared=False), None, None, {}),
      ),
      patch.object(start, "ensure_persona_share_symlinks"),
      patch.object(start, "_seed_box_home") as seed,
      patch.object(start, "_sync_box_at_create") as sync,
    ):
      assert start.persona_create_verdict(std, None, proj) is None
      start.seed_new_box(std, None, proj)
      assert seed.call_args.kwargs["agent_id"] == "shell"
      assert seed.call_args.kwargs["agent_identity"] is None
      assert sync.call_args.kwargs["target"] is None


def _create_args(path, *, agent="Scout", recover=False):
  return argparse.Namespace(
    path=str(path),
    standalone=False,
    no_vault=not recover,
    name=None,
    image=None,
    agent=agent,
    allow_home=False,
    recover=recover,
  )


def _public_persona(endpoint="https://first.invalid"):
  return {
    "agent": {
      "persona": {"name": "Guide"},
      "harness": {
        "name": "claude",
        "config_store": {"env": {"ANTHROPIC_BASE_URL": endpoint}},
        "auth_var": "ANTHROPIC_AUTH_TOKEN",
      },
      "secret": {"backend": "synthetic"},
    },
  }


@pytest.mark.usefixtures("config_file")
class TestPreparedCreateInput:
  def test_broken_public_input_refuses_before_create_writes(self, std, tmp_home):
    from kanibako.commands.box import _parser as box
    from kanibako.errors import ConfigError
    from kanibako.settings import config_io

    path = tmp_home / "broken-parent" / "broken-box"
    with (
      patch.object(config_io, "persona_store_location", return_value=tmp_home / "pg"),
      patch.object(config_io, "persona_store_agents", return_value={"Scout": tmp_home / "entry"}),
      patch.object(
        config_io, "load_persona_agent", side_effect=ConfigError("broken public config")
      ) as read,
      patch.object(
        box, "_claim_create_entry", side_effect=AssertionError("journal reached")
      ) as claim,
      patch.object(box, "_new_box_undo") as materialize,
      patch.object(start, "_register_new_box") as register,
      patch.object(start, "seed_new_box") as seed,
    ):
      with pytest.raises(ConfigError, match="broken public config"):
        box.run_create(_create_args(path))
    read.assert_called_once()
    claim.assert_not_called()
    materialize.assert_not_called()
    register.assert_not_called()
    seed.assert_not_called()
    assert not path.parent.exists()
    assert not std.journal.exists()
    assert not std.boxes.exists()
    assert not std.agents.exists()

  @pytest.mark.parametrize("recover", [False, True])
  def test_public_create_carries_one_projection_to_seed(self, std, tmp_home, recover):
    from kanibako import persona_store
    from kanibako.commands.box import _parser as box
    from kanibako.settings import config_io

    path = tmp_home / "captured-box"
    first = (tmp_home / "entry", _public_persona())
    second = (tmp_home / "entry", _public_persona("https://recovery.invalid"))
    real_project = persona_store.project_registered_persona
    real_seed = start.seed_new_box
    with (
      patch.object(config_io, "persona_store_location", return_value=tmp_home / "pg"),
      patch.object(config_io, "persona_store_agents", return_value={"Scout": tmp_home / "entry"}),
      patch.object(
        config_io,
        "load_persona_agent",
        side_effect=[first, second] if recover else [first],
      ) as read,
      patch.object(persona_store, "project_registered_persona", wraps=real_project) as project,
      patch.object(
        config_io, "fetch_persona_secret", side_effect=AssertionError("credential fetch")
      ),
      patch.object(
        start,
        "_seed_box_home",
        side_effect=[RuntimeError("interrupted"), None] if recover else None,
      ) as home,
      patch.object(start, "_sync_box_at_create") as sync,
      patch.object(start, "seed_new_box", wraps=real_seed) as seed,
    ):
      if recover:
        with pytest.raises(RuntimeError, match="interrupted"):
          box.run_create(_create_args(path))
        pending = start._pending_create_entry(std, seed.call_args.args[2])
        assert pending["state"] == {"agent": "Scout", "no_vault": True}
        assert box.run_create(_create_args(path, agent=None, recover=True)) == 0
      else:
        assert box.run_create(_create_args(path)) == 0
    expected_calls = 2 if recover else 1
    assert read.call_count == project.call_count == seed.call_count == expected_calls
    prepared = seed.call_args.kwargs["prepared"]
    assert prepared.selection.identity.registered_name == "Scout"
    assert prepared.selection.registered_persona.store_root == tmp_home / "pg"
    assert prepared.persona_bundle.endpoint == (
      "https://recovery.invalid" if recover else "https://first.invalid"
    )
    assert home.call_args.kwargs["agent_identity"] is prepared.selection.identity
    assert sync.call_args.kwargs["persona_values"]["endpoint"] == prepared.persona_bundle.endpoint
    assert "prepared" not in config_io.load_doc(std.journal)

  @pytest.mark.parametrize("failure", ["unset", "null"])
  def test_missing_default_is_the_only_template_only_failure(self, tmp_path, failure):
    from kanibako.errors import AgentNoDefaultError, AgentUnsetError, ConfigError

    absent = AgentUnsetError if failure == "unset" else AgentNoDefaultError
    with patch("kanibako.settings.agent_select.select_agent", side_effect=absent("no default")):
      prepared = start.prepare_create_agent(_focused_std(), _focused_box(tmp_path))
    assert prepared == (None, None, None)
    with patch(
      "kanibako.settings.agent_select.select_agent", side_effect=ConfigError("broken PG")
    ):
      with pytest.raises(ConfigError, match="broken PG"):
        start.prepare_create_agent(_focused_std(), _focused_box(tmp_path))
