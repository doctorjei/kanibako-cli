"""The symlink-loop sites left after purgeloop r4: each names the loop, keeps it, and never
crashes.  On Python 3.11 ``Path.resolve()`` RAISES ``RuntimeError`` on a loop that 3.13
returns as an ordinary path, so a refusal is checked for its words, not only its code.
"""

from __future__ import annotations

import pytest

from tests.test_commands.test_purge_symlink_loop_r2 import _LOOP_WHY, _cli, _loop, _run_purge

_REMAP = "kanibako box remap"


def _primary(tmp_home, name="q"):
  workspace = tmp_home / "p" / name
  workspace.mkdir(parents=True)
  (workspace / "f.txt").write_text("keep")
  assert _cli("create", "--name", name, str(workspace)) == 0
  return workspace


def _standalone(tmp_home):
  root = tmp_home / "p" / "k"
  root.mkdir(parents=True)
  assert _cli("create", "--standalone", "--register", str(root)) == 0
  return root


def _standalone_name(std, root):
  from kanibako.project import registry_store

  return registry_store.standalone_name_for_root(std.registry, root)


def _std(config_file):
  from kanibako.settings.config import load_config
  from kanibako.settings.paths import load_std_paths

  return load_std_paths(load_config(config_file))


def _ran(argv, capsys):
  capsys.readouterr()
  rc, raised, text = _run_purge(argv, capsys)
  assert raised is None, text
  return rc, text


def _loop_aside(store):
  """Loop *store* after moving it aside: its protected canon books cannot be deleted."""
  store.rename(store.with_name(store.name + ".real"))
  store.symlink_to(store.name)


def _looped_store_note(store):
  return f"Note: left the box metadata at {store} in place — {_LOOP_WHY}"


class TestADestinationVaultOnALoop:
  """A convert whose destination vault loops names the loop and changes nothing."""

  @pytest.mark.parametrize("where", ["leaf", "arm"])
  def test_convert_to_default_names_the_loop(
    self, where, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _standalone(tmp_home)
    _primary(tmp_home, "other")
    arm = _std(config_file).primary_vault_rw
    _loop(arm / "k" if where == "leaf" else arm)
    rc, text = _ran(("box", "convert", "--force", str(root), "--default"), capsys)
    assert rc == 1, text
    assert (
      f"Error: the relocation failed: {arm / 'k'}: it is a symlink loop that "
      "kanibako cannot resolve. Remove or repair the link, then retry."
    ) in text, text
    assert "Errno" not in text, text
    assert (root / "box_data").is_dir() and (root / "workspace").is_dir()

  def test_an_absent_source_leaf_carries_nothing_so_converts(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    import shutil

    root = _standalone(tmp_home)
    _primary(tmp_home, "other")
    arm = _std(config_file).primary_vault_rw
    _loop(arm / "k")
    shutil.rmtree(root / "vault" / "ro")
    shutil.rmtree(root / "vault" / "rw")
    rc, text = _ran(("box", "convert", "--force", str(root), "--default"), capsys)
    assert rc == 0, text
    assert "Converted 'k' to primary" in text, text
    assert "symlink loop" not in text and "Symlink loop" not in text, text
    assert (arm / "k").is_symlink()


class TestTheCarrySourceCure:
  """``box remap`` records where a workspace is; it is no cure for a looped store."""

  def test_a_looped_workspace_keeps_the_remap_cure(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    ws = _primary(tmp_home)
    _loop(ws)
    rc, text = _ran(("box", "move", "--force", "q", str(tmp_home / "new")), capsys)
    assert rc == 1, text
    assert f"Cannot carry box 'q': {ws} is a symlink loop" in text, text
    assert _REMAP in text, text

  def test_the_store_refusal_offers_no_remap(self):
    from kanibako.settings.messages import ERR_LOOP_SOURCE

    assert _REMAP not in ERR_LOOP_SOURCE % ("k", "/r/box_data")


class TestALoopedStoreOnThePurgeDoors:
  """The looped store is kept, as before, and now named instead of called missing."""

  def test_box_purge_of_a_standalone(self, config_file, tmp_home, credentials_dir, capsys):
    root = _standalone(tmp_home)
    _loop_aside(root / "box_data")
    rc, text = _ran(("box", "purge", "--force", str(root)), capsys)
    assert rc == 0, text
    assert _looped_store_note(root / "box_data") in text, text
    assert "No session data" not in text, text
    assert (root / "box_data").is_symlink()

  def test_rm_purge_of_a_standalone(self, config_file, tmp_home, credentials_dir, capsys):
    root = _standalone(tmp_home)
    name = _standalone_name(_std(config_file), root)
    _loop_aside(root / "box_data")
    rc, text = _ran(("box", "rm", "--purge", "--force", name), capsys)
    assert rc == 0, text
    assert _looped_store_note(root / "box_data") in text, text
    assert "No metadata directory" not in text, text
    assert (root / "box_data").is_symlink()

  def test_rm_purge_of_a_primary(self, config_file, tmp_home, credentials_dir, capsys):
    _primary(tmp_home)
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    rc, text = _ran(("box", "rm", "--purge", "--force", "q"), capsys)
    assert rc == 0, text
    assert _looped_store_note(store) in text, text
    assert store.is_symlink()

  @pytest.mark.parametrize("force", [True, False])
  def test_the_running_warning_names_the_looped_lock(
    self, force, config_file, tmp_home, credentials_dir, capsys
  ):
    _primary(tmp_home)
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    argv = ("box", "rm", "--purge") + (("--force",) if force else ()) + ("q",)
    rc, text = _ran(argv, capsys)
    assert rc == (0 if force else 1), text
    assert (
      "this box may be running: kanibako cannot tell whether a session holds its "
      f"lock file {store / '.kanibako.lock'}: it is a symlink loop"
    ) in text, text
    assert "a kanibako session holds its lock file" not in text, text
    assert ("or pass --force" in text) is not force, text
    assert store.is_symlink()

  def test_register_of_a_parked_primary(self, config_file, tmp_home, credentials_dir, capsys):
    _primary(tmp_home)
    assert _cli("box", "rm", "--force", "q") == 0
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    rc, text = _ran(("box", "register", "q"), capsys)
    assert rc == 1, text
    assert f"{store}: it is a symlink loop" in text, text
    assert store.is_symlink()


class TestTheRunningReasonForAHeldLock:
  """Only a looped lock is called a loop; a lock a session holds keeps its words."""

  def test_a_held_lock_is_a_session(self, tmp_path):
    import fcntl

    from kanibako.commands.box._lifecycle import box_running_reason

    lock = tmp_path / ".kanibako.lock"
    lock.touch()
    with open(lock, "rb") as fd:
      fcntl.flock(fd, fcntl.LOCK_EX)
      assert box_running_reason(None, lock) == "a kanibako session holds its lock file"


class TestALoopedPrimaryStoreOnMoveAndConvert:
  """A primary whose store loops is named, not reported as having no data."""

  @pytest.mark.parametrize(
    "verb", [("move", "--force", "q", "{new}"), ("convert", "--force", "q", "--standalone")]
  )
  def test_the_loop_is_named(self, verb, config_file, tmp_home, credentials_dir, capsys):
    ws = _primary(tmp_home)
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    argv = ("box",) + tuple(a.format(new=tmp_home / "new") for a in verb)
    rc, text = _ran(argv, capsys)
    assert rc == 1, text
    assert (
      f"{store}: it is a symlink loop that kanibako cannot resolve. Remove or "
      "repair the link, then retry."
    ) in text, text
    assert "No project data" not in text, text
    assert store.is_symlink() and (ws / "f.txt").read_text() == "keep"


class TestAMoveOntoALoopedStore:
  """A retried move onto a box whose store loops still says the SOURCE has no data."""

  def test_the_source_is_named(self, config_file, tmp_home, credentials_dir, capsys):
    ws = _primary(tmp_home)
    _loop_aside(_std(config_file).boxes / "q")
    nobox = tmp_home / "nobox"
    nobox.mkdir()
    rc, text = _ran(("box", "move", "--force", str(nobox), str(ws)), capsys)
    assert rc == 1, text
    assert f"No project data found for {nobox}" in text, text
    assert "symlink loop" not in text, text


class TestInfoOnALoopedLock:
  """``box info`` reads a looped lock as a loop, not as a session holding it."""

  def test_the_lock_row_names_the_loop(self, config_file, tmp_home, credentials_dir, capsys):
    _primary(tmp_home)
    lock = _std(config_file).boxes / "q" / ".kanibako.lock"
    lock.unlink(missing_ok=True)
    lock.symlink_to(lock.name)
    rc, text = _ran(("box", "info", "q"), capsys)
    assert rc == 0, text
    assert (
      "kanibako cannot tell whether a session holds its lock file "
      f"{lock}: it is a symlink loop that kanibako cannot resolve"
    ) in text, text
    assert "ACTIVE" not in text, text


class TestDuplicateOfALoopedStore:
  """A duplicate whose source store loops names the loop, not "no project data"."""

  def test_the_loop_is_named(self, config_file, tmp_home, credentials_dir, capsys):
    ws = _primary(tmp_home)
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    rc, text = _ran(("box", "duplicate", "--force", str(ws), str(tmp_home / "dup")), capsys)
    assert rc == 1, text
    assert (
      f"Error: {store}: it is a symlink loop that kanibako cannot resolve. "
      "Remove or repair the link, then retry."
    ) in text, text
    assert "no project data" not in text, text
    assert not (tmp_home / "dup").exists()


class TestDuplicateReadsTheLoopFromItsOwner:
  """The duplicate door asks ``project.workset._symlink_loop``, the owner, not a re-export."""

  def test_the_owner_answers(self, tmp_path, monkeypatch, capsys):
    from kanibako.commands.box import _duplicate
    from kanibako.project import workset

    monkeypatch.setattr(workset, "_symlink_loop", lambda path: True)
    assert _duplicate._refuse_no_source_data(tmp_path / "gone", tmp_path / "src") == 1
    assert "it is a symlink loop" in capsys.readouterr().err


class TestBoxListBesideALoopedStore:
  """One looped primary store is listed and marked; every other box is still listed."""

  def test_the_rest_are_listed(self, config_file, tmp_home, credentials_dir, capsys):
    a = _primary(tmp_home, "a")
    q = _primary(tmp_home)
    store = _std(config_file).boxes / "q"
    _loop_aside(store)
    rc, text = _ran(("box", "list"), capsys)
    assert rc == 0, text
    assert (
      f"Warning: {store}: it is a symlink loop that kanibako cannot resolve. "
      "Remove or repair the link, then retry."
    ) in text, text
    rows = {
      line.split()[0]: line.split()[1:]
      for line in text.splitlines()
      if line.split() and line.split()[0] in ("a", "q")
    }
    assert rows["a"] == ["stopped", str(a)], text
    assert rows["q"] == ["looped", str(q)], text
    assert store.is_symlink()

  def test_quiet_and_orphan_lists_survive(self, config_file, tmp_home, credentials_dir, capsys):
    _primary(tmp_home, "a")
    _primary(tmp_home)
    _loop_aside(_std(config_file).boxes / "q")
    rc, text = _ran(("box", "list", "-q"), capsys)
    assert rc == 0, text
    assert {"a", "q"} <= set(text.split()), text
    rc, text = _ran(("box", "list", "--orphan"), capsys)
    assert rc == 0, text
    assert "it is a symlink loop" in text and "Error:" not in text, text


class TestAWorksetLandingOnALoop:
  """A convert into a workset whose vault leaf loops names the loop, not "already exists"."""

  @pytest.mark.parametrize("where", ["leaf", "arm"])
  def test_the_loop_is_named(self, where, config_file, tmp_home, credentials_dir, capsys):
    root = _standalone(tmp_home)
    name = _standalone_name(_std(config_file), root)
    assert _cli("workset", "create", "--name", "ws2", str(tmp_home / "ws2")) == 0
    arm = tmp_home / "ws2" / "vault" / "rw"
    arm.mkdir(parents=True, exist_ok=True)
    _loop(arm / name if where == "leaf" else arm)
    rc, text = _ran(("box", "convert", "--force", str(root), "--workset", "ws2"), capsys)
    assert rc == 1, text
    assert (
      f"Refusing to land '{name}' in workset 'ws2': {arm / name}: it is a symlink "
      "loop that kanibako cannot resolve. Remove or repair the link, then "
      "retry."
    ) in text, text
    assert "already exists" not in text, text
    assert (root / "box_data").is_dir() and (root / "workspace").is_dir()


class TestAMemberLeafUnderANullWorkspacesKey:
  """Under a null ``workset.workspaces`` the leaf is the recorded path, judged where it sits."""

  def _member(self, tmp_home):
    wsdir = tmp_home / "ws2"
    assert _cli("workset", "create", "--name", "ws2", str(wsdir)) == 0
    leaf = wsdir / "workspaces" / "m"
    leaf.mkdir(parents=True)
    (leaf / "f").write_text("keep")
    assert _cli("workset", "connect", "ws2", str(leaf)) == 0
    assert _cli("workset", "set", "--null", "ws2", "workset.workspaces") == 0
    return wsdir, leaf

  def _disconnect(self, capsys):
    return _ran(("workset", "disconnect", "--force", "ws2", "m", "--remove-files"), capsys)

  def test_a_looped_link_leaf_is_unlinked_as_with_the_key_set(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    _wsdir, leaf = self._member(tmp_home)
    _loop(leaf)
    rc, text = self._disconnect(capsys)
    assert rc == 0, text
    assert not leaf.is_symlink(), text

  def test_a_leaf_under_a_looped_dir_is_kept_and_named(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    wsdir, leaf = self._member(tmp_home)
    _loop(wsdir / "workspaces")
    rc, text = self._disconnect(capsys)
    assert rc == 0, text
    assert f"Note: left the workspace at {leaf} in place — {_LOOP_WHY}" in text, text
    assert (wsdir / "workspaces").is_symlink()


class TestARollbackBesideALoopedStoreBase:
  """A ws→ws rollback that replaces a remnant compares against every store base of the
  source workset; a looped one must not stop the box tree's restore.  The record step
  still fails there (it re-creates the vault leaf under the loop), and names the loop."""

  def test_the_box_tree_restores_whole(
    self, config_file, tmp_home, credentials_dir, monkeypatch, tmp_path, capsys
  ):
    import tempfile

    from kanibako.commands.box import _lifecycle as lc
    from kanibako.commands.box._lifecycle import (
      BARE_INTO_WS,
      TargetSpec,
      execute_lifecycle,
      resolve_lifecycle_target,
    )
    from kanibako.project.workset import add_project, create_workset
    from kanibako.runtime.container import remove_box_tree
    from kanibako.settings.config import load_config

    config = load_config(config_file)
    std = _std(config_file)
    ws1 = create_workset("ws1", tmp_home / "ws1_root", std)
    create_workset("ws2", tmp_home / "ws2_root", std)
    leaf = ws1.workspaces_dir / "alpha"
    leaf.mkdir(parents=True)
    add_project(ws1, "alpha", leaf, std)
    state = resolve_lifecycle_target(str(leaf), std, config)
    state.shell_path.mkdir(parents=True, exist_ok=True)
    (state.shell_path / "h.txt").write_text("home")
    _loop(state.vault_ro.parent)
    stash_root = tmp_path / "stash-root"
    stash_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(stash_root))

    def part_way(ws, name, *, bases=None):
      store = ws.projects_dir / name
      remove_box_tree(store)
      remnant = store / "home" / "h.txt"
      remnant.parent.mkdir(parents=True)
      remnant.write_text("stale")
      remnant.chmod(0o444)
      remnant.parent.chmod(0o555)
      raise KeyboardInterrupt

    monkeypatch.setattr(lc, "remove_member_store", part_way)
    with pytest.raises(KeyboardInterrupt):
      execute_lifecycle(
        state,
        TargetSpec(location=BARE_INTO_WS, ownership="ws2"),
        std,
        config,
        confirm=lambda: True,
      )
    err = capsys.readouterr().err
    assert "could not restore the box tree" not in err, err
    record = f"Note: could not restore the record of 'alpha' in workset 'ws1' at {leaf}: "
    assert record in err, err
    assert "Errno" not in err.split(record)[1].splitlines()[0], err
    assert f"{state.vault_ro.parent}" in err.split(record)[1].splitlines()[0], err
    assert "it is a symlink loop" in err.split(record)[1].splitlines()[0], err
    assert (state.shell_path / "h.txt").read_text() == "home"


class TestTheRelocationFailureLine:
  """Only a path that really loops is called a loop; boxfs refusing a link keeps its words."""

  def test_a_loop_is_named(self, tmp_path):
    import errno
    import os

    from kanibako.commands.box._lifecycle import _relocation_failure

    leaf = tmp_path / "rw"
    _loop(leaf)
    line = _relocation_failure(OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(leaf)))
    assert line == (
      f"Error: the relocation failed: {leaf}: it is a symlink loop that "
      "kanibako cannot resolve. Remove or repair the link, then retry."
    )

  def test_a_refused_link_is_not_called_a_loop(self, tmp_path):
    from kanibako.boxfs import SymlinkRefusedError
    from kanibako.commands.box._lifecycle import _relocation_failure

    (tmp_path / "real").mkdir()
    link = tmp_path / "link"
    link.symlink_to("real")
    err = SymlinkRefusedError(link / "f", link)
    assert _relocation_failure(err) == f"Error: the relocation failed: {err}"
    assert "symlink loop" not in _relocation_failure(err)
