"""Missing read-write bind sources (system-design § Delivery at launch, "Missing read-write
sources"): each root is made with its owner, a launch refuses a missing workspace or
channel root, and any other missing rw source is created and reported as information.
"""

from __future__ import annotations

import logging
import shutil

import pytest

from kanibako.channels import channels as _ch
from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.channels.roots import missing_channel_root_error
from kanibako.project.workset import (
  _workset_skeleton_dirs,
  add_project,
  create_workset,
  is_workset_skeleton,
)
from kanibako.settings.paths import (
  WorksetSpec,
  resolve_project,
  resolve_standalone_project,
  resolve_workset_project,
)


@pytest.fixture
def set_up(std):
  """A host after ``setup``: the ``config_file`` fixture made the roots."""
  assert std.channels_common.is_dir()
  return std


@pytest.fixture
def named(set_up, config, tmp_home):
  ws = create_workset("my_set", tmp_home / "worksets" / "my_set", set_up)
  source = tmp_home / "original-project"
  source.mkdir()
  add_project(ws, "cool_app", source)
  proj = resolve_workset_project(
    WorksetSpec.from_workset(ws), "cool_app", set_up, config, initialize=True
  )
  return ws, proj


def _system_roots(std):
  return [std.channels_common, std.channels_chat, std.channels_share, std.channels_mailboxes]


class TestSetupMakesTheGlobalRoots:
  def test_first_run_init_makes_system_primary_and_reserved_partition_roots(
    self, tmp_path, monkeypatch
  ):
    from kanibako.cli import _ensure_initialized
    from kanibako.settings.paths import load_std_paths

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    _ensure_initialized()
    std = load_std_paths()
    primary = _ch.workset_channels_at(std.primary_workset, early=_ch_early(std, WS_TOKEN_PRIMARY))
    assert primary is not None
    for path in [
      *_system_roots(std),
      primary.common,
      primary.chat,
      primary.share,
      std.channels_mailboxes / WS_TOKEN_PRIMARY,
      std.channels_mailboxes / WS_TOKEN_STANDALONE,
    ]:
      assert path.is_dir(), path

  def test_setup_makes_them_on_an_existing_host(self, std):
    shutil.rmtree(std.channels_common.parent)  # ``config_file`` ran setup's step
    from kanibako.commands.setup_cmd import _make_channel_roots

    _make_channel_roots()
    for path in _system_roots(std):
      assert path.is_dir(), path


def _ch_early(std, token):
  from kanibako.settings.workset_dirkeys import EarlyScope

  return EarlyScope(std.early_system, token)


class TestWorksetCreateMakesItsRoots:
  def test_roots_and_partition_are_made(self, set_up, tmp_home):
    root = tmp_home / "ws"
    create_workset("ws", root, set_up)
    for leaf in ("common", "chat", "share"):
      assert (root / "channels" / leaf).is_dir(), leaf
    assert (set_up.channels_mailboxes / "ws").is_dir()

  def test_the_roots_are_not_part_of_the_detected_skeleton(self, set_up, tmp_home):
    """An older workset, made before its channel roots, is still a workset."""
    root = tmp_home / "ws"
    ws = create_workset("ws", root, set_up)
    assert not any(
      "channels" in p.parts for p in _workset_skeleton_dirs(root, early=ws.early_scope)
    )
    shutil.rmtree(root / "channels")
    assert is_workset_skeleton(root, early=ws.early_scope)

  def test_a_failed_create_removes_the_partition_it_made(self, set_up, tmp_home, monkeypatch):
    import kanibako.project.workset as workset_mod

    def boom(*_a, **_k):
      raise RuntimeError("registry write failed")

    import kanibako.channels.roots as roots_mod

    made: list = []
    real = roots_mod.make_workset_channel_roots

    def spy(*a, **k):
      made.extend(real(*a, **k))
      return made

    monkeypatch.setattr(roots_mod, "make_workset_channel_roots", spy)
    monkeypatch.setattr(workset_mod, "register_name", boom)
    with pytest.raises(RuntimeError):
      create_workset("ws", tmp_home / "ws", set_up)
    assert set_up.channels_mailboxes / "ws" in made  # the control: it was made
    assert not (set_up.channels_mailboxes / "ws").exists()


class TestBoxCreateMakesItsInbox:
  def test_a_primary_create_makes_the_inbox(
    self, set_up, config_file, project_dir, credentials_dir
  ):
    import argparse

    from kanibako.commands.box._parser import run_create
    from kanibako.settings.config import load_config

    args = argparse.Namespace(
      path=str(project_dir),
      standalone=False,
      no_vault=True,
      name=None,
      image=None,
      agent=None,
      allow_home=False,
      recover=False,
    )
    assert run_create(args) == 0
    proj = resolve_project(set_up, load_config(config_file), str(project_dir))
    assert _ch.box_channel_addresses(proj, set_up).inbox.is_dir()


class TestTheLaunchRefusesAMissingRoot:
  def test_nothing_missing_is_no_refusal(self, named, set_up):
    assert missing_channel_root_error(set_up, named[1]) is None

  def test_a_missing_system_root_names_it_and_setup(self, named, set_up):
    set_up.channels_share.rmdir()
    err = missing_channel_root_error(set_up, named[1])
    assert err is not None
    assert f"system.channels.share: {set_up.channels_share}" in err
    assert "run 'kanibako setup'" in err

  def test_a_missing_workset_root_names_it_and_mkdir(self, named, set_up):
    ws, proj = named
    shutil.rmtree(ws.root / "channels" / "common")
    err = missing_channel_root_error(set_up, proj)
    assert err is not None
    assert f"workset.channels.common: {ws.root / 'channels' / 'common'}" in err
    assert f"mkdir -p {ws.root / 'channels' / 'common'}" in err

  def test_a_missing_partition_refuses_and_a_missing_inbox_does_not(self, named, set_up):
    ws, proj = named
    inbox = _ch.box_channel_addresses(proj, set_up).inbox
    if inbox.exists():
      inbox.rmdir()
    assert missing_channel_root_error(set_up, proj) is None
    (set_up.channels_mailboxes / "my_set").rmdir()
    err = missing_channel_root_error(set_up, proj)
    assert err is not None and "workset.channels.mailboxes" in err

  def _repoint_broadcast(self, ws, path):
    from kanibako.settings.config import WORKSET_META_FILE
    from kanibako.settings.config_io import write_nested_key
    from kanibako.settings.config_keys import _KEY_ROUTES

    sections, leaf = _KEY_ROUTES["workset.channels.broadcast"]
    write_nested_key(ws.root / WORKSET_META_FILE, sections, leaf, str(path))

  def test_a_broadcast_repointed_into_a_missing_dir_is_created_with_a_note(
    self, named, set_up, capsys
  ):
    """The log is a FILE under the channel family, not a root: no refusal, and the
    seeder makes its directory and says so."""
    from kanibako.commands.start import _seed_channel_files

    ws, proj = named
    loud = ws.root / "loud"
    self._repoint_broadcast(ws, loud / "b.md")
    assert missing_channel_root_error(set_up, proj) is None
    capsys.readouterr()
    _seed_channel_files(set_up, proj)
    assert (loud / "b.md").is_file()
    assert capsys.readouterr().err == (
      f"Note: created the missing directory {loud} for the broadcast log {loud / 'b.md'}.\n"
    )

  def test_a_broadcast_repointed_into_a_missing_root_makes_no_root(self, named, set_up):
    from kanibako.commands.start import _seed_channel_files

    ws, proj = named
    share = ws.root / "channels" / "share"
    share.rmdir()
    self._repoint_broadcast(ws, share / "b.md")
    _seed_channel_files(set_up, proj)
    assert not share.exists()
    assert "workset.channels.share" in missing_channel_root_error(set_up, proj)

  def test_a_standalone_box_is_checked_without_workset_roots(
    self, set_up, config, project_dir, credentials_dir
  ):
    proj = resolve_standalone_project(set_up, config, str(project_dir), initialize=True)
    assert missing_channel_root_error(set_up, proj) is None
    set_up.channels_chat.rmdir()
    assert "system.channels.chat" in missing_channel_root_error(set_up, proj)

  def test_the_launch_returns_1_on_the_refusal(self, start_mocks, capsys):
    """The check is wired into ``_run_container``: rc 1 before the container runs."""
    from kanibako.commands.start import _run_container

    with start_mocks() as m:
      m.missing_channel_root_error.return_value = "Error: a channel directory is missing"
      rc = _run_container(
        project_dir=None,
        entrypoint=None,
        image_override=None,
        new_session=False,
        safe_mode=False,
        resume_mode=False,
        extra_args=[],
      )
    assert rc == 1
    assert "a channel directory is missing" in capsys.readouterr().err
    m.missing_channel_root_error.assert_called_once()
    m.runtime.run.assert_not_called()
    m.seed_channel_files.assert_not_called()


class TestTheWorkspaceIsNeverRecreated:
  def test_a_standalone_resolve_leaves_a_missing_workspace_missing(
    self, set_up, config, project_dir, credentials_dir
  ):
    from kanibako.commands.start import _check_box_components

    proj = resolve_standalone_project(set_up, config, str(project_dir), initialize=True)
    shutil.rmtree(proj.project_path)
    again = resolve_standalone_project(set_up, config, str(project_dir), initialize=True)
    assert not again.project_path.exists()
    assert "the workspace for box" in _check_box_components(again)


class TestTheChatSeederMakesNoRoot:
  def test_a_missing_chat_root_is_not_recreated(self, named, set_up):
    from kanibako.commands.start import _seed_channel_files

    shutil.rmtree(set_up.channels_chat)
    _seed_channel_files(set_up, named[1])
    assert not set_up.channels_chat.exists()


class TestAnyOtherMissingRwSourceIsInformation:
  def test_it_is_created_with_a_note_and_no_warning(self, tmp_path, capsys, caplog):
    from kanibako.commands.start import _emit_category_mounts
    from kanibako.settings.kb_store import BindEntry

    src = tmp_path / "user" / "share"
    with caplog.at_level(logging.WARNING):
      mounts = _emit_category_mounts(
        {"/home/agent/share": BindEntry(src=str(src), opts="rw")}, label="category"
      )
    assert src.is_dir()
    assert [m.destination for m in mounts] == ["/home/agent/share"]
    assert capsys.readouterr().err == (
      f"Note: created the missing read-write source {src} (bound at /home/agent/share).\n"
    )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

  def test_a_fresh_create_then_first_start_reports_nothing(
    self, set_up, config_file, project_dir, credentials_dir, capsys
  ):
    """Every rw source of a new box is made by its owner, so the launch makes none."""
    import argparse

    from kanibako.commands.box._parser import run_create
    from kanibako.commands.start import (
      _emit_category_mounts,
      _launch_bind_map,
      _resolve_launch_snapshot,
    )
    from kanibako.launch.templates import ensure_agent_stores
    from kanibako.settings.config import load_config

    ensure_agent_stores(set_up, ["claude"])
    args = argparse.Namespace(
      path=str(project_dir),
      standalone=False,
      no_vault=False,
      name=None,
      image=None,
      agent=None,
      allow_home=False,
      recover=False,
    )
    assert run_create(args) == 0
    proj = resolve_project(set_up, load_config(config_file), str(project_dir), initialize=True)
    from kanibako.plugins.claude.target import ClaudeTarget

    target = ClaudeTarget()
    capsys.readouterr()
    snapshot, _ = _resolve_launch_snapshot(
      std=set_up,
      proj=proj,
      agent_name="claude",
      system_settings_path=set_up.settings,
      agent_cfg_path=None,
      desc=target.descriptor,
      install=None,
      target=target,
      cli_level=None,
    )
    from pathlib import Path

    from kanibako.settings.settings_categories import is_read_only
    from kanibako.settings.store_collapse import is_mask

    binds = _launch_bind_map(snapshot)
    unmade = [
      bind.src
      for bind in binds.values()
      if not is_mask(bind) and not is_read_only(bind.opts) and not Path(bind.src).is_dir()
    ]
    assert unmade == [], "an rw source no owner made"
    mounts = _emit_category_mounts(binds, label="category")
    assert "Note:" not in capsys.readouterr().err
    dests = {m.destination for m in mounts}
    assert "/home/agent/channels/inbox" in dests
    assert "/home/agent/vault/rw" in dests
    assert "/home/agent/.claude/plugins" in dests
