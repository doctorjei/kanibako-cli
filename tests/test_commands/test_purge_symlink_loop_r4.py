"""purgeloop r4: the ``box rm --purge`` store beside a looped leaf, and the relocation verbs on
Python 3.11, where ``Path.resolve()`` RAISES ``RuntimeError`` on a loop that 3.13 returns as an
ordinary path.  Every refusal here must leave the box where it was.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_commands.test_purge_symlink_loop_r2 import _LOOP_WHY, _cli, _loop, _run_purge


def _primary_box(tmp_home, config_file, name):
  """A registered primary box *name*; returns ``(workspace, std)``."""
  from kanibako.settings.config import load_config
  from kanibako.settings.paths import load_std_paths

  workspace = tmp_home / "p" / name
  workspace.mkdir(parents=True)
  (workspace / "f.txt").write_text("keep")
  assert _cli("create", "--name", name, str(workspace)) == 0
  return workspace, load_std_paths(load_config(config_file))


def _standalone(tmp_home):
  root = tmp_home / "p" / "k"
  root.mkdir(parents=True)
  assert _cli("create", "--standalone", "--register", str(root)) == 0
  return root


def _refused(argv, capsys) -> str:
  capsys.readouterr()
  rc, raised, text = _run_purge(argv, capsys)
  assert raised is None, text
  assert rc == 1, text
  return text


class TestRmPurgeStore:
  """The store hangs beside the leaf, so only a loop on the STORE's own path keeps it."""

  def test_a_populated_store_beside_a_looped_leaf_is_deleted(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    _ws, std = _primary_box(tmp_home, config_file, "pl")
    leaf = std.primary_vault_rw / "pl"
    store = std.primary_vault_rw / ".versions" / "pl"
    (store / "snap1").mkdir(parents=True)
    (store / "snap1" / "f").write_text("s")
    _loop(leaf)
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "rm", "--purge", "--force", "pl"), capsys)
    assert (raised, rc) == (None, 0), text
    assert not store.exists(), text
    assert leaf.is_symlink(), "the looped leaf must survive"
    assert f"Note: left the vault at {leaf} in place — {_LOOP_WHY}" in text, text

  def test_a_store_under_a_looped_arm_is_kept_listed_and_named(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    _ws, std = _primary_box(tmp_home, config_file, "pa")
    arm = std.primary_vault_rw
    _loop(arm)
    store = arm / ".versions" / "pa"
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "rm", "--purge", "--force", "pa"), capsys)
    assert (raised, rc) == (None, 0), text
    assert f"kept box store: {store}" in text, text
    assert f"Note: left the box store at {store} in place — {_LOOP_WHY}" in text, text
    assert arm.is_symlink()


class TestRelocationOnALoop:
  """Each of these raised ``RuntimeError`` out of the CLI on 3.11."""

  def test_convert_move_to_a_looped_path(self, config_file, tmp_home, credentials_dir, capsys):
    root = _standalone(tmp_home)
    (tmp_home / "L").symlink_to("L")
    landing = tmp_home / "L" / "x"
    text = _refused(
      ("box", "convert", "--force", str(root), "--default", "--move", str(landing)), capsys
    )
    assert f"at {landing}: that path runs through a symlink loop" in text, text
    assert (root / "box_data").is_dir()

  def test_bare_move_into_a_looped_workspaces_dir(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _standalone(tmp_home)
    wsdir = tmp_home / "ws2"
    assert _cli("workset", "create", "--name", "ws2", str(wsdir)) == 0
    _loop(wsdir / "workspaces")
    text = _refused(("box", "convert", "--force", str(root), "--workset", "ws2", "--move"), capsys)
    assert f"at {wsdir / 'workspaces'}/" in text, text
    assert "that path runs through a symlink loop" in text, text
    assert (root / "box_data").is_dir()

  def test_move_of_a_box_whose_workspace_loops(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    ws, _std = _primary_box(tmp_home, config_file, "q")
    _loop(ws)
    text = _refused(("box", "move", "--force", "q", str(tmp_home / "new")), capsys)
    assert f"Cannot carry box 'q': {ws} is a symlink loop" in text, text
    assert ws.is_symlink() and not (tmp_home / "new").exists()

  def test_in_place_convert_of_a_box_whose_workspace_loops(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    ws, _std = _primary_box(tmp_home, config_file, "q")
    _loop(ws)
    text = _refused(("box", "convert", "--force", "q", "--standalone"), capsys)
    assert f"Refusing to land 'q' at {ws}: that path runs through a symlink loop" in text, text

  def test_a_looped_standalone_store_refuses_the_move(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _standalone(tmp_home)
    store = root / "box_data"
    store.rename(root / "box_data.real")
    store.symlink_to("box_data")
    text = _refused(("box", "move", "--force", str(root), str(tmp_home / "d")), capsys)
    # Refused by name when the store's ``box.yaml`` is read through the loop.
    assert f"{store}/" in text and "is a symlink loop" in text, text
    assert not (tmp_home / "d").exists()

  def test_remap_recovers_a_box_whose_workspace_loops(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    ws, _std = _primary_box(tmp_home, config_file, "q")
    real = tmp_home / "p" / "q.real"
    ws.rename(real)
    ws.symlink_to("q")
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "remap", "--force", "q", str(real)), capsys)
    assert (raised, rc) == (None, 0), text
    assert f"Remapped 'q' to {real}" in text, text

  def test_another_worksets_looped_workspaces_dir_is_no_obstacle(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _standalone(tmp_home)
    ws3 = tmp_home / "ws3"
    assert _cli("workset", "create", "--name", "ws3", str(ws3)) == 0
    _loop(ws3 / "workspaces")
    capsys.readouterr()
    dest = tmp_home / "dest"
    rc, raised, text = _run_purge(("box", "move", "--force", str(root), str(dest)), capsys)
    assert (raised, rc) == (None, 0), text
    assert (dest / "box_data").is_dir(), text
    assert Path(ws3 / "workspaces").is_symlink()

  def test_joining_a_workset_past_another_worksets_looped_root(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    root = _standalone(tmp_home)
    for name in ("ws2", "ws3"):
      assert _cli("workset", "create", "--name", name, str(tmp_home / name)) == 0
    ws3 = tmp_home / "ws3"
    ws3.rename(tmp_home / "ws3.real")
    ws3.symlink_to("ws3")
    capsys.readouterr()
    dest = tmp_home / "dest"
    rc, raised, text = _run_purge(
      ("box", "move", "--force", str(root), str(dest), "--workset", "ws2"), capsys
    )
    assert (raised, rc) == (None, 0), text
    assert dest.is_dir() and ws3.is_symlink(), text


class TestALoopedWorksetRootElsewhere:
  """Detection and the logs walk resolved EVERY registered workset root (3.11 raised)."""

  @staticmethod
  def _loop_ws3(tmp_home):
    ws3 = tmp_home / "ws3"
    assert _cli("workset", "create", "--name", "ws3", str(ws3)) == 0
    ws3.rename(tmp_home / "ws3.real")
    ws3.symlink_to("ws3")

  def test_move_a_primary_box_by_path(self, config_file, tmp_home, credentials_dir, capsys):
    ws, _std = _primary_box(tmp_home, config_file, "q")
    self._loop_ws3(tmp_home)
    capsys.readouterr()
    new = tmp_home / "newp"
    rc, raised, text = _run_purge(("box", "move", "--force", str(ws), str(new)), capsys)
    assert (raised, rc) == (None, 0), text
    assert (new / "f.txt").is_file(), text

  def test_a_looped_workset_file_in_a_reachable_root_still_refuses(
    self, config_file, tmp_home, credentials_dir, capsys
  ):
    """Only a looped ROOT is skipped: a reachable root's looped ``workset.yaml`` may
    hold a repoint, so the walk cannot tell and refuses it by name."""
    ws, _std = _primary_box(tmp_home, config_file, "q")
    ws3 = tmp_home / "ws3"
    assert _cli("workset", "create", "--name", "ws3", str(ws3)) == 0
    _loop(ws3 / "workset.yaml")
    new = tmp_home / "newp"
    text = _refused(("box", "move", "--force", str(ws), str(new)), capsys)
    assert f"{ws3 / 'workset.yaml'}: it is a symlink loop" in text, text
    assert (ws / "f.txt").is_file() and not new.exists(), text

  def test_rm_purge(self, config_file, tmp_home, credentials_dir, capsys):
    _ws, std = _primary_box(tmp_home, config_file, "q")
    self._loop_ws3(tmp_home)
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "rm", "--purge", "--force", "q"), capsys)
    assert (raised, rc) == (None, 0), text
    assert not (std.boxes / "q").exists(), text


class TestALoopedLogsDirOnThePartnerWalk:
  """``_logs_share_partners`` caught ``OSError`` around ``resolve()``; 3.11 raises
  ``RuntimeError`` on a loop, both for the purged box's own logs dir and for a walked
  workset's."""

  def test_own_logs_dir_loops(self, config_file, tmp_home, credentials_dir, capsys):
    _ws, std = _primary_box(tmp_home, config_file, "q")
    logs = std.primary_logs
    assert logs is not None
    if logs.exists():
      _loop(logs)
    else:
      logs.symlink_to(logs.name)
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "rm", "--purge", "--force", "q"), capsys)
    assert (raised, rc) == (None, 0), text
    assert not (std.boxes / "q").exists() and logs.is_symlink(), text

  def test_another_worksets_logs_dir_loops(self, config_file, tmp_home, credentials_dir, capsys):
    _ws, std = _primary_box(tmp_home, config_file, "q")
    ws3 = tmp_home / "ws3"
    assert _cli("workset", "create", "--name", "ws3", str(ws3)) == 0
    _loop(ws3 / "logs")
    capsys.readouterr()
    rc, raised, text = _run_purge(("box", "rm", "--purge", "--force", "q"), capsys)
    assert (raised, rc) == (None, 0), text
    assert not (std.boxes / "q").exists(), text
