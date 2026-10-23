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


def _cure_lines(err, key):
  """The refusal's lines for *key*: its own line and the cure lines under it."""
  lines = err.splitlines()
  i = next(n for n, line in enumerate(lines) if line.startswith(f"  {key}: "))
  return [lines[i], *(line for line in lines[i + 1 :] if line.startswith("    "))]


def _run_cure(err, label):
  """Run the cure printed after *label*, as a user would: a shell line, or a CLI call."""
  import shlex
  import subprocess

  from kanibako.cli import main

  line = next(line for line in err.splitlines() if line.strip().startswith(label))
  command = line.split(label, 1)[1].strip()
  if command.startswith("kanibako "):
    try:
      main(shlex.split(command)[1:])
    except SystemExit as exc:
      assert not exc.code, command
  else:
    subprocess.run(["bash", "-c", command], check=True)


def _as_foreign_owner(monkeypatch):
  """Make every path ``roots`` asks about look owned by another uid (a host subuid)."""
  import os
  import types

  from kanibako.channels import roots

  monkeypatch.setattr(
    roots, "os", types.SimpleNamespace(lstat=os.lstat, getuid=lambda: os.getuid() + 1)
  )


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
    """Every root setup owns: the system's, the primary workset's, both partitions."""
    from kanibako.commands.setup_cmd import _make_channel_roots

    wch = _ch.workset_channels_at(std.primary_workset, early=_ch_early(std, WS_TOKEN_PRIMARY))
    workset_roots = [wch.common, wch.chat, wch.share]
    partitions = [std.channels_mailboxes / t for t in (WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE)]
    roots = [*_system_roots(std), *workset_roots, *partitions]
    assert all(path.is_dir() for path in roots)  # ``config_file`` ran setup's step
    shutil.rmtree(std.channels_common.parent)  # the system roots and both partitions
    for path in workset_roots:
      shutil.rmtree(path)
    assert not any(path.exists() for path in roots)
    _make_channel_roots()
    for path in roots:
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

  @pytest.mark.parametrize("leaf", ["sub/b.md", "b.md"])
  def test_a_broadcast_behind_a_planted_link_writes_nothing_outside(
    self, named, set_up, tmp_path, leaf, caplog
  ):
    """A box plants ``chat/evil -> /outside``; a broadcast under it, in a new subdir or
    in the linked dir itself, is skipped with a warning and nothing lands outside."""
    from kanibako.commands.start import _seed_channel_files

    ws, proj = named
    chat = ws.root / "channels" / "chat"
    outside = tmp_path / "outside"
    outside.mkdir()
    (chat / "evil").symlink_to(outside)
    self._repoint_broadcast(ws, chat / "evil" / leaf)
    with caplog.at_level(logging.WARNING):
      _seed_channel_files(set_up, proj)
    assert list(outside.rglob("*")) == []
    assert any("evil" in r.getMessage() for r in caplog.records)
    # The control: the scope's general log is still seeded.
    assert (chat / "general.md").is_file()

  def _nest_workset_chat_in_the_system_chat(self, ws, set_up, tmp_path):
    """``workset.channels.chat = {system.channels.chat}/nest``, and a box with the
    outer root rw has swapped ``nest`` for a link to *outside*."""
    from kanibako.settings.config import WORKSET_META_FILE
    from kanibako.settings.config_io import write_nested_key
    from kanibako.settings.config_keys import _KEY_ROUTES

    nest = set_up.channels_chat / "nest"
    sections, leaf = _KEY_ROUTES["workset.channels.chat"]
    write_nested_key(ws.root / WORKSET_META_FILE, sections, leaf, str(nest))
    outside = tmp_path / "outside"
    outside.mkdir()
    nest.symlink_to(outside)
    return nest, outside

  def test_a_nested_root_swapped_for_a_link_writes_nothing_outside(
    self, named, set_up, tmp_path, caplog
  ):
    from kanibako.commands.start import _seed_channel_files

    ws, proj = named
    _nest, outside = self._nest_workset_chat_in_the_system_chat(ws, set_up, tmp_path)
    with caplog.at_level(logging.WARNING):
      _seed_channel_files(set_up, proj)
    assert list(outside.rglob("*")) == []
    # The control: the outer root's own logs are still seeded.
    assert (set_up.channels_chat / "general.md").is_file()

  def test_a_nested_root_swapped_for_a_link_refuses_the_launch(self, named, set_up, tmp_path):
    ws, proj = named
    nest, outside = self._nest_workset_chat_in_the_system_chat(ws, set_up, tmp_path)
    err = missing_channel_root_error(set_up, proj)
    assert err is not None
    assert _cure_lines(err, "workset.channels.chat") == [
      f"  workset.channels.chat: {nest} is a link inside {set_up.channels_chat}, which a box can write.",
      f"    If a box made it: rm {nest} && mkdir -p {nest}",
      f"    If you made it: kanibako workset set my_set workset.channels.chat={outside}",
    ]

  def test_a_subuid_owned_parent_takes_the_cure_under_podman_unshare(
    self, named, set_up, tmp_path, monkeypatch
  ):
    ws, proj = named
    nest, _outside = self._nest_workset_chat_in_the_system_chat(ws, set_up, tmp_path)
    _as_foreign_owner(monkeypatch)
    err = missing_channel_root_error(set_up, proj)
    assert _cure_lines(err, "workset.channels.chat")[1] == (
      f"    If a box made it: podman unshare rm {nest} && podman unshare mkdir -p {nest}"
    )

  @pytest.mark.parametrize("cure", ["If a box made it:", "If you made it:"])
  def test_each_cure_runs_and_clears_the_refusal(self, named, set_up, tmp_path, cure):
    ws, proj = named
    self._nest_workset_chat_in_the_system_chat(ws, set_up, tmp_path)
    _run_cure(missing_channel_root_error(set_up, proj), cure)
    assert missing_channel_root_error(set_up, proj) is None

  @pytest.mark.parametrize("cure", ["If a box made it:", "If you made it:"])
  def test_a_primary_partition_link_names_the_default_workset_and_runs(
    self, set_up, config, project_dir, credentials_dir, tmp_path, cure
  ):
    proj = resolve_project(set_up, config, str(project_dir), initialize=True)
    part = set_up.channels_mailboxes / WS_TOKEN_PRIMARY
    outside = tmp_path / "nfs-mail"
    shutil.copytree(part, outside)
    shutil.rmtree(part)
    part.symlink_to(outside)
    err = missing_channel_root_error(set_up, proj)
    assert _cure_lines(err, "workset.channels.mailboxes")[1:] == [
      f"    If a box made it: rm {part} && mkdir -p {part}",
      f"    If you made it: kanibako workset set default workset.channels.mailboxes={outside}",
    ]
    _run_cure(err, cure)
    assert missing_channel_root_error(set_up, proj) is None

  def test_a_standalone_partition_link_offers_no_set_cure(
    self, set_up, config, project_dir, credentials_dir, tmp_path
  ):
    """A standalone box's workset has no set door, so only the first cure is printed."""
    proj = resolve_standalone_project(set_up, config, str(project_dir), initialize=True)
    part = set_up.channels_mailboxes / WS_TOKEN_STANDALONE
    outside = tmp_path / "nfs-mail"
    outside.mkdir()
    part.rmdir()
    part.symlink_to(outside)
    err = missing_channel_root_error(set_up, proj)
    assert _cure_lines(err, "workset.channels.mailboxes")[1:] == [
      f"    If a box made it: rm {part} && mkdir -p {part}",
    ]

  @pytest.mark.parametrize("cure", ["If a box made it:", "If you made it:"])
  def test_a_nested_system_root_names_system_set_and_runs(self, named, set_up, tmp_path, cure):
    from kanibako.settings.config_io import write_nested_key
    from kanibako.settings.config_keys import _KEY_ROUTES
    from kanibako.settings.paths import load_std_paths

    _ws, proj = named
    inner = set_up.channels_common / "share"
    sections, leaf = _KEY_ROUTES["system.channels.share"]
    write_nested_key(set_up.settings, sections, leaf, str(inner))
    std = load_std_paths()
    outside = tmp_path / "nfs-share"
    outside.mkdir()
    inner.symlink_to(outside)
    err = missing_channel_root_error(std, proj)
    assert _cure_lines(err, "system.channels.share")[2] == (
      f"    If you made it: kanibako system set system.channels.share={outside}"
    )
    _run_cure(err, cure)
    assert missing_channel_root_error(load_std_paths(), proj) is None

  def test_an_unreadable_dir_above_a_nested_root_is_named_unreadable(self, named, set_up):
    """``first_symlink`` fails closed on a dir it cannot open; the refusal says so, and
    its cure runs."""
    from kanibako.settings.config_io import write_nested_key
    from kanibako.settings.config_keys import _KEY_ROUTES
    from kanibako.settings.paths import load_std_paths

    _ws, proj = named
    locked = set_up.channels_common / "locked"
    inner = locked / "share"
    inner.mkdir(parents=True)
    sections, leaf = _KEY_ROUTES["system.channels.share"]
    write_nested_key(set_up.settings, sections, leaf, str(inner))
    std = load_std_paths()
    locked.chmod(0)
    try:
      err = missing_channel_root_error(std, proj)
      assert _cure_lines(err, "system.channels.share") == [
        f"  system.channels.share: {locked} inside {set_up.channels_common} cannot be read,"
        " so it cannot be checked for a link.",
        f"    If a box made it: chmod u+rwx {locked}",
      ]
      _run_cure(err, "If a box made it:")
      assert missing_channel_root_error(std, proj) is None
    finally:
      locked.chmod(0o755)

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
  def test_it_is_created_with_a_note_and_no_warning(self, std, tmp_path, capsys, caplog):
    from kanibako.commands.start import _emit_category_mounts
    from kanibako.settings.kb_store import BindEntry

    src = tmp_path / "user" / "share"
    with caplog.at_level(logging.WARNING):
      mounts = _emit_category_mounts(
        {"/home/agent/share": BindEntry(src=str(src), opts="rw")},
        std=std,
        proj=None,
        label="category",
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
    mounts = _emit_category_mounts(binds, std=set_up, proj=proj, label="category")
    assert "Note:" not in capsys.readouterr().err
    dests = {m.destination for m in mounts}
    assert "/home/agent/channels/inbox" in dests
    assert "/home/agent/vault/rw" in dests
    assert "/home/agent/.claude/plugins" in dests


class TestABindSourceBehindALinkInAChannelTree:
  """A box can swap anything under a channel root it has bound, such as another box's
  inbox, for a link; the launch refuses to bind through it."""

  _INBOX = "/home/agent/channels/inbox"

  @pytest.fixture
  def box(self, set_up, config_file, project_dir, credentials_dir):
    import argparse

    from kanibako.commands.box._parser import run_create
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
    return resolve_project(set_up, load_config(config_file), str(project_dir), initialize=True)

  def _emit(self, std, proj, extra=None):
    from kanibako.commands.start import (
      _emit_category_mounts,
      _launch_bind_map,
      _resolve_launch_snapshot,
    )
    from kanibako.plugins.claude.target import ClaudeTarget

    target = ClaudeTarget()
    snapshot, _ = _resolve_launch_snapshot(
      std=std,
      proj=proj,
      agent_name="claude",
      system_settings_path=std.settings,
      agent_cfg_path=None,
      desc=target.descriptor,
      install=None,
      target=target,
      cli_level=None,
    )
    binds = dict(_launch_bind_map(snapshot))
    binds.update(extra or {})
    return _emit_category_mounts(binds, std=std, proj=proj, label="category")

  @staticmethod
  def _swap(path, outside):
    """Replace *path* with a link to *outside*, as another box could."""
    outside.mkdir()
    (outside / "victim.txt").write_text("host data\n")
    path.rmdir()
    path.symlink_to(outside)

  def test_a_linked_inbox_refuses_and_mounts_nothing(self, set_up, box, tmp_path):
    from kanibako.channels.roots import ChannelLinkError
    from kanibako.errors import KanibakoError

    inbox = _ch.box_channel_addresses(box, set_up).inbox
    outside = tmp_path / "another-box-chose-this"
    self._swap(inbox, outside)
    with pytest.raises(ChannelLinkError) as exc:
      self._emit(set_up, box)
    assert isinstance(exc.value, KanibakoError)
    key = f"box.bindings.rw[{self._INBOX}]"
    assert _cure_lines(str(exc.value), key) == [
      f"  {key}: {inbox} is a link inside {set_up.channels_mailboxes}, which a box can write.",
      f"    If a box made it: rm {inbox}",
    ]
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]

  def test_the_cure_runs_and_the_launch_binds_a_new_inbox(self, set_up, box, tmp_path):
    from kanibako.channels.roots import ChannelLinkError

    inbox = _ch.box_channel_addresses(box, set_up).inbox
    outside = tmp_path / "another-box-chose-this"
    self._swap(inbox, outside)
    with pytest.raises(ChannelLinkError) as exc:
      self._emit(set_up, box)
    _run_cure(str(exc.value), "If a box made it:")
    mounts = {m.destination: m.source for m in self._emit(set_up, box)}
    assert mounts[self._INBOX] == inbox
    assert inbox.is_dir() and not inbox.is_symlink()
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]

  def test_a_linked_share_global_partition_refuses(self, set_up, box, tmp_path):
    from kanibako.channels.roots import ChannelLinkError
    from kanibako.settings.kb_store import BindEntry

    share_global = _ch.box_channel_addresses(box, set_up).share_global
    partition = share_global.parent
    partition.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "nfs-share-global"
    self._swap(partition, outside)
    extra = {"/home/agent/global": BindEntry(src=str(share_global), opts="rw")}
    with pytest.raises(ChannelLinkError) as exc:
      self._emit(set_up, box, extra)
    key = "box.bindings.rw[/home/agent/global]"
    assert _cure_lines(str(exc.value), key)[:2] == [
      f"  {key}: {partition} is a link inside {set_up.channels_share}, which a box can write.",
      f"    If a box made it: rm {partition}",
    ]
    assert sorted(p.name for p in outside.iterdir()) == ["victim.txt"]

  def test_an_unreadable_partition_is_named_unreadable_and_its_cure_runs(self, set_up, box):
    """A box can ``chmod 000`` the partition above a sibling's inbox: no ``rm`` cure."""
    from kanibako.channels.roots import ChannelLinkError

    part = _ch.box_channel_addresses(box, set_up).inbox.parent
    part.chmod(0)
    try:
      with pytest.raises(ChannelLinkError) as exc:
        self._emit(set_up, box)
      key = f"box.bindings.rw[{self._INBOX}]"
      assert _cure_lines(str(exc.value), key) == [
        f"  {key}: {part} inside {set_up.channels_mailboxes} cannot be read,"
        " so it cannot be checked for a link.",
        f"    If a box made it: chmod u+rwx {part}",
      ]
      _run_cure(str(exc.value), "If a box made it:")
      mounts = {m.destination: m.source for m in self._emit(set_up, box)}
      assert mounts[self._INBOX] == part / box.name
    finally:
      part.chmod(0o755)

  def test_a_subuid_owned_dir_takes_its_cures_under_podman_unshare(
    self, set_up, box, tmp_path, monkeypatch
  ):
    """A box with sudo makes subuid-owned dirs: ``rm`` and ``chmod`` need the namespace."""
    from kanibako.channels.roots import ChannelLinkError

    inbox = _ch.box_channel_addresses(box, set_up).inbox
    self._swap(inbox, tmp_path / "elsewhere")
    key = f"box.bindings.rw[{self._INBOX}]"
    with pytest.raises(ChannelLinkError) as plain:
      self._emit(set_up, box)
    assert _cure_lines(str(plain.value), key)[1] == f"    If a box made it: rm {inbox}"
    _as_foreign_owner(monkeypatch)
    with pytest.raises(ChannelLinkError) as foreign:
      self._emit(set_up, box)
    assert _cure_lines(str(foreign.value), key)[1] == (
      f"    If a box made it: podman unshare rm {inbox}"
    )
    inbox.unlink()
    inbox.mkdir()
    part = inbox.parent
    part.chmod(0)
    try:
      with pytest.raises(ChannelLinkError) as unreadable:
        self._emit(set_up, box)
    finally:
      part.chmod(0o755)
    assert _cure_lines(str(unreadable.value), key)[1] == (
      f"    If a box made it: podman unshare chmod u+rwx {part}"
    )

  def test_a_user_linked_top_level_root_still_binds(self, set_up, box, tmp_path):
    """The control: a channel root itself is the user's, and may be a link."""
    root = set_up.channels_mailboxes
    nfs = tmp_path / "nfs-mailboxes"
    shutil.copytree(root, nfs)
    shutil.rmtree(root)
    root.symlink_to(nfs)
    mounts = {m.destination: m.source for m in self._emit(set_up, box)}
    assert mounts[self._INBOX] == _ch.box_channel_addresses(box, set_up).inbox

  def test_a_missing_inbox_is_still_created_with_a_note(self, set_up, box, capsys):
    inbox = _ch.box_channel_addresses(box, set_up).inbox
    inbox.rmdir()
    capsys.readouterr()
    mounts = {m.destination: m.source for m in self._emit(set_up, box)}
    assert inbox.is_dir()
    assert mounts[self._INBOX] == inbox
    assert (
      f"Note: created the missing read-write source {inbox} (bound at {self._INBOX}).\n"
      in capsys.readouterr().err
    )

  def test_the_refusal_stops_the_launch_before_the_container_runs(self, start_mocks):
    """Wired into ``_run_container``: the refusal propagates (the CLI prints it as an
    ``Error:`` line, rc 1) and no container runs."""
    from unittest import mock

    from kanibako.channels.roots import ChannelLinkError
    from kanibako.commands import start as _start

    with (
      start_mocks() as m,
      mock.patch.object(
        _start, "refuse_linked_channel_sources", side_effect=ChannelLinkError("linked")
      ) as refuse,
    ):
      m.target.detect.return_value = None
      with pytest.raises(ChannelLinkError):
        _start._run_container(
          project_dir=None,
          entrypoint=None,
          image_override=None,
          new_session=False,
          safe_mode=False,
          resume_mode=False,
          extra_args=[],
        )
    refuse.assert_called()
    m.runtime.run.assert_not_called()
