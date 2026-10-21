"""No-follow at the launch-time host sites that touch a box-writable tree (174 lane 2).

Plan ``174-nofollow-patch`` sites H2, H3, H6, H7, H8: a symlink the box planted, at the
LEAF or at a PARENT, is never followed -- the host file it points at keeps its hash, the
site warns naming the path, and the call returns normally.  A link pointing back INSIDE
the tree is skipped the same way, and a tree with no link behaves as before.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanibako.boxfs import SymlinkRefusedError
from kanibako.commands import start
from kanibako.log import rotate_if_over
from kanibako.settings import core_defaults
from kanibako.settings.paths import _bootstrap_shell, _upgrade_shell
from kanibako.vscode.vscode_config import seed_codex_config, seed_session_start_hook

_BOXFS = "kanibako.boxfs"


def _digest(path: Path) -> tuple[str, int]:
  st = path.stat()
  return hashlib.sha256(path.read_bytes()).hexdigest(), st.st_mode


@pytest.fixture
def outside(tmp_path):
  """A host directory outside every box tree, holding a sentinel file."""
  d = tmp_path / "host"
  d.mkdir()
  (d / "sentinel").write_text("host secret\n")
  os.chmod(d / "sentinel", 0o640)
  return d


@pytest.fixture
def home(tmp_path):
  d = tmp_path / "box" / "home"
  d.mkdir(parents=True)
  return d


def _sentinel_untouched(outside: Path, before: dict[str, tuple[str, int]]) -> None:
  after = {p.name: _digest(p) for p in outside.iterdir()}
  assert after == before


def _snapshot(outside: Path) -> dict[str, tuple[str, int]]:
  return {p.name: _digest(p) for p in outside.iterdir()}


def _warned(caplog, path: Path) -> bool:
  return any(r.levelno == logging.WARNING and str(path) in r.getMessage() for r in caplog.records)


# --- H3: the shell bootstrap / upgrade ---------------------------------------------


class TestShellSeam:
  def test_leaf_link_bashrc_outside_is_not_written(self, home, outside, caplog):
    (home / ".bashrc").symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      _upgrade_shell(home)
      _bootstrap_shell(home)
    _sentinel_untouched(outside, before)
    assert (home / ".bashrc").is_symlink()
    assert _warned(caplog, home / ".bashrc")
    # The rest of the skeleton still lands.
    assert (home / ".profile").is_file()
    assert (home / ".shell.d").is_dir()

  def test_leaf_link_dangling_profile_is_not_created(self, home, outside, caplog):
    (home / ".profile").symlink_to(outside / "planted")
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      _bootstrap_shell(home)
    assert not (outside / "planted").exists()
    assert _warned(caplog, home / ".profile")

  def test_shell_d_link_outside_is_not_followed(self, home, outside, caplog):
    (home / ".shell.d").symlink_to(outside)
    (home / ".bashrc").write_text("# user\n")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      _upgrade_shell(home)
    _sentinel_untouched(outside, before)
    assert _warned(caplog, home / ".shell.d")
    # The bashrc is a plain file, so its seam line is still appended.
    assert ".shell.d/" in (home / ".bashrc").read_text()

  def test_link_inside_the_tree_is_skipped_too(self, home, caplog):
    real = home / "real-bashrc"
    real.write_text("# mine\n")
    (home / ".bashrc").symlink_to(real)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      _upgrade_shell(home)
    assert real.read_text() == "# mine\n"
    assert _warned(caplog, home / ".bashrc")

  def test_no_link_control(self, home, caplog):
    _bootstrap_shell(home)
    (home / ".bashrc").write_text("# user\n")
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      _upgrade_shell(home)
    assert ".shell.d/" in (home / ".bashrc").read_text()
    assert (home / ".profile").is_file()
    assert not caplog.records


# --- H6: the launch-time ``synced`` copy -------------------------------------------


class TestSyncedCopy:
  @pytest.fixture
  def src_file(self, tmp_path):
    p = tmp_path / "hostsrc" / "token"
    p.parent.mkdir()
    p.write_text("new token\n")
    return p

  @pytest.fixture
  def src_dir(self, tmp_path):
    d = tmp_path / "hostsrc-dir"
    (d / "sub").mkdir(parents=True)
    (d / "a").write_text("A\n")
    (d / "sub" / "b").write_text("B\n")
    return d

  def _copy(self, src, dest, root):
    start._apply_shell_copy(
      src,
      dest,
      label="synced",
      name=str(dest),
      host_src=str(src),
      logger=logging.getLogger("kanibako.test"),
      if_absent=False,
      skip_if=None,
      dest_root=root,
    )

  def test_leaf_link_outside(self, home, outside, src_file, caplog):
    dest = home / ".tok"
    dest.symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src_file, dest, home)
    _sentinel_untouched(outside, before)
    assert dest.is_symlink()
    assert _warned(caplog, dest)

  def test_parent_link_outside(self, home, outside, src_file, caplog):
    (home / ".config").symlink_to(outside)
    dest = home / ".config" / "sentinel"
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src_file, dest, home)
    _sentinel_untouched(outside, before)
    assert _warned(caplog, dest)

  def test_dir_source_with_a_link_inside_the_dest_tree(
    self,
    home,
    outside,
    src_dir,
    caplog,
  ):
    dest = home / ".cfg"
    dest.mkdir()
    (dest / "sub").symlink_to(outside)
    (dest / "a").symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src_dir, dest, home)
    _sentinel_untouched(outside, before)
    assert not (outside / "b").exists()
    assert _warned(caplog, dest / "sub")
    assert _warned(caplog, dest / "a")

  def test_link_inside_the_tree(self, home, src_file, caplog):
    target = home / "elsewhere"
    target.write_text("box file\n")
    dest = home / ".tok"
    dest.symlink_to(target)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src_file, dest, home)
    assert target.read_text() == "box file\n"
    assert _warned(caplog, dest)

  def test_no_link_control_file_and_dir(self, home, src_file, src_dir, caplog):
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src_file, home / ".x" / "tok", home)
      self._copy(src_dir, home / ".cfg", home)
      # An overwrite: the sync owns its destination.
      src_file.write_text("newer\n")
      self._copy(src_file, home / ".x" / "tok", home)
    assert (home / ".x" / "tok").read_text() == "newer\n"
    assert (home / ".cfg" / "a").read_text() == "A\n"
    assert (home / ".cfg" / "sub" / "b").read_text() == "B\n"
    assert not caplog.records

  def test_host_side_source_links_are_followed_as_before(self, home, tmp_path):
    """The HOST source is the user's own: a stow-style link there still copies
    its content, as ``copytree`` did; only the box side is no-follow."""
    real = tmp_path / "dotfiles" / "tok"
    real.parent.mkdir()
    real.write_text("from stow\n")
    src = tmp_path / "hostsrc-stow"
    src.mkdir()
    (src / "tok").symlink_to(real)
    self._copy(src, home / ".cfg", home)
    out = home / ".cfg" / "tok"
    assert not out.is_symlink()
    assert out.read_text() == "from stow\n"

  @staticmethod
  def _dir_meta(top: Path) -> dict[str, tuple[int, int]]:
    """(mode bits, mtime_ns) of every directory under *top*, *top* included."""
    out = {}
    for d in [top, *sorted(p for p in top.rglob("*") if p.is_dir())]:
      st = d.stat()
      out[str(d.relative_to(top))] = (st.st_mode & 0o7777, st.st_mtime_ns)
    return out

  @pytest.fixture
  def gnupg(self, tmp_path):
    """A host ~/.gnupg-like tree: private dir modes and old dir times."""
    d = tmp_path / "hostsrc-gnupg"
    (d / "private-keys-v1.d" / "deep").mkdir(parents=True)
    (d / "pubring.kbx").write_text("ring\n")
    (d / "private-keys-v1.d" / "deep" / "k").write_text("key\n")
    for sub, mode, t in (
      ("private-keys-v1.d/deep", 0o750, 1_000_000_000),
      ("private-keys-v1.d", 0o700, 1_100_000_000),
      (".", 0o700, 1_200_000_000),
    ):
      os.chmod(d / sub, mode)
      os.utime(d / sub, ns=(t * 10**9, t * 10**9))
    return d

  @pytest.mark.parametrize("dest_exists", [False, True], ids=["new-dest", "existing-dest"])
  def test_dir_modes_and_times_match_copytree(self, home, tmp_path, gnupg, dest_exists):
    """A synced directory keeps its directories' modes and times, as the
    ``copytree(dirs_exist_ok=True)`` it replaces did, into a new or existing dest."""
    dest = home / ".gnupg"
    ref = tmp_path / "ref" / ".gnupg"
    if dest_exists:
      for top in (dest, ref):
        (top / "private-keys-v1.d" / "deep").mkdir(parents=True, mode=0o755)
        os.chmod(top, 0o755)
    shutil.copytree(gnupg, ref, dirs_exist_ok=True)
    self._copy(gnupg, dest, home)
    assert self._dir_meta(dest) == self._dir_meta(ref)
    assert self._dir_meta(dest)["."][0] == 0o700
    assert (dest / "private-keys-v1.d" / "deep" / "k").read_text() == "key\n"

  def test_host_side_dir_links_and_a_linked_root_are_followed(self, home, tmp_path):
    """A stow-managed source: the synced root itself and a directory inside it are
    links on the HOST; their content lands as real directories in the box."""
    real = tmp_path / "dotfiles" / "gnupg"
    (real / "sub").mkdir(parents=True)
    (real / "sub" / "f").write_text("deep\n")
    os.chmod(real / "sub", 0o700)
    linked_root = tmp_path / "stow-gnupg"
    linked_root.symlink_to(real)
    (real / "alias").symlink_to(real / "sub")
    dest = home / ".gnupg"
    self._copy(linked_root, dest, home)
    for name in ("sub", "alias"):
      assert (dest / name).is_dir() and not (dest / name).is_symlink()
      assert (dest / name / "f").read_text() == "deep\n"
      assert (dest / name).stat().st_mode & 0o777 == 0o700
    assert not dest.is_symlink()

  def test_host_side_link_loop_is_not_entered(self, home, tmp_path, caplog):
    src = tmp_path / "hostsrc-loop"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f").write_text("x\n")
    (src / "d" / "up").symlink_to(src)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src, home / ".loop", home)
    assert (home / ".loop" / "d" / "f").read_text() == "x\n"
    assert not (home / ".loop" / "d" / "up").exists()
    assert _warned(caplog, src / "d" / "up")

  def test_host_link_meeting_a_box_dest_link_is_not_followed(
    self,
    home,
    tmp_path,
    outside,
    caplog,
  ):
    """A followed host-source directory link never makes the BOX side follow:
    a link the box planted at the same name stays, and its target is untouched."""
    real = tmp_path / "dotfiles" / "sub"
    real.mkdir(parents=True)
    (real / "sentinel").write_text("from host\n")
    src = tmp_path / "hostsrc-meet"
    src.mkdir()
    (src / "sub").symlink_to(real)
    dest = home / ".meet"
    dest.mkdir()
    (dest / "sub").symlink_to(outside)
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      self._copy(src, dest, home)
    _sentinel_untouched(outside, before)
    assert (dest / "sub").is_symlink()
    assert _warned(caplog, dest / "sub")


# --- H7: the chat logs -------------------------------------------------------------


class TestChannelLogs:
  @pytest.fixture
  def chat(self, tmp_path, monkeypatch):
    from kanibako.channels import channels as ch

    monkeypatch.setattr(ch, "workset_channel_paths", lambda proj, std: None)
    d = tmp_path / "channels" / "chat"
    d.mkdir(parents=True)
    return d

  def _std(self, chat):
    return SimpleNamespace(channels_chat=chat, channels_broadcast=chat / "broadcast.md")

  def test_dangling_leaf_link_is_not_created(self, chat, outside, caplog):
    (chat / "general.md").symlink_to(outside / "planted")
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      start._seed_channel_files(self._std(chat), None)
    assert not (outside / "planted").exists()
    assert _warned(caplog, chat / "general.md")
    assert (chat / "broadcast.md").is_file()

  def test_broadcast_link_is_not_rotated(self, chat, outside, caplog, monkeypatch):
    monkeypatch.setattr(start, "ROTATE_MAX_BYTES", 1)
    (chat / "broadcast.md").symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      start._seed_channel_files(self._std(chat), None)
    _sentinel_untouched(outside, before)
    assert (chat / "broadcast.md").is_symlink()
    assert not (chat / "broadcast.md.1").exists()
    assert _warned(caplog, chat / "broadcast.md")

  def test_no_link_control_rotates(self, chat, monkeypatch, caplog):
    monkeypatch.setattr(start, "ROTATE_MAX_BYTES", 4)
    (chat / "broadcast.md").write_text("over the limit\n")
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      start._seed_channel_files(self._std(chat), None)
    assert (chat / "general.md").is_file()
    assert (chat / "broadcast.md.1").read_text() == "over the limit\n"
    assert (chat / "broadcast.md").read_text() == ""
    assert not caplog.records


class TestRotateIfOverRoot:
  def test_leaf_link_is_not_rotated(self, tmp_path, outside):
    log = tmp_path / "b.md"
    log.symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    assert rotate_if_over(log, 1, root=tmp_path) is False
    _sentinel_untouched(outside, before)
    assert log.is_symlink()

  def test_parent_link_is_not_rotated(self, tmp_path, outside):
    (tmp_path / "chat").symlink_to(outside)
    before = _snapshot(outside)
    assert rotate_if_over(tmp_path / "chat" / "sentinel", 1, root=tmp_path) is False
    _sentinel_untouched(outside, before)

  def test_control(self, tmp_path):
    log = tmp_path / "b.md"
    log.write_text("12345")
    assert rotate_if_over(log, 3, root=tmp_path) is True
    assert (tmp_path / "b.md.1").read_text() == "12345"
    assert log.read_text() == ""


# --- the supervisor fallback log (box home read) -----------------------------------


class TestSupervisorFallback:
  def test_leaf_link_is_not_read(self, home, outside, capsys, caplog):
    (outside / "sentinel").write_text(start.SUPERVISOR_FALLBACK_WARNING + "\nSECRET\n")
    log = home / start.SUPERVISOR_FALLBACK_RELPATH
    log.parent.mkdir(parents=True)
    log.symlink_to(outside / "sentinel")
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      start._print_supervisor_fallback(home)
    assert "SECRET" not in capsys.readouterr().err
    assert _warned(caplog, log)

  def test_parent_link_is_not_read(self, home, outside, capsys, caplog):
    (outside / Path(start.SUPERVISOR_FALLBACK_RELPATH).name).write_text(
      start.SUPERVISOR_FALLBACK_WARNING + "\nSECRET\n",
    )
    (home / Path(start.SUPERVISOR_FALLBACK_RELPATH).parent).symlink_to(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      start._print_supervisor_fallback(home)
    assert "SECRET" not in capsys.readouterr().err
    assert _warned(caplog, home / start.SUPERVISOR_FALLBACK_RELPATH)

  def test_control(self, home, capsys):
    log = home / start.SUPERVISOR_FALLBACK_RELPATH
    log.parent.mkdir(parents=True)
    log.write_text(start.SUPERVISOR_FALLBACK_WARNING + "\ntrace line\n")
    start._print_supervisor_fallback(home)
    assert "trace line" in capsys.readouterr().err


# --- H8: the canon skeleton --------------------------------------------------------


class TestCanonSkeleton:
  def test_parent_link_outside(self, home, outside, caplog):
    (home / "canon").symlink_to(outside)
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      core_defaults.materialize_canon_skeleton(home)
    _sentinel_untouched(outside, before)
    assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and str(home / "canon") in warned[0].getMessage()

  def test_leaf_link_outside(self, home, outside, caplog):
    rels = core_defaults.canon_skeleton_rels()
    file_rel = next(rel for rel, is_dir in rels if not is_dir)
    leaf = home / file_rel
    leaf.parent.mkdir(parents=True)
    leaf.symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      core_defaults.materialize_canon_skeleton(home)
    _sentinel_untouched(outside, before)
    assert leaf.is_symlink()
    assert _warned(caplog, leaf)
    # Everything else is still built.
    assert (home / "canon" / "charter" / "general").is_dir()

  def test_link_inside_the_tree(self, home, caplog):
    inside = home / "mine"
    inside.mkdir()
    os.chmod(inside, 0o750)
    (home / "canon").symlink_to(inside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      core_defaults.materialize_canon_skeleton(home)
    assert list(inside.iterdir()) == []
    assert (inside.stat().st_mode & 0o777) == 0o750
    assert _warned(caplog, home / "canon")

  def test_no_link_control(self, home, caplog):
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      core_defaults.materialize_canon_skeleton(home)
    expected = [home / rel for rel, _ in core_defaults.canon_skeleton_rels()]
    assert all(p.exists() for p in expected)
    assert not caplog.records


# --- H2: the in-box agent config seeds ---------------------------------------------


class TestAgentConfigSeeds:
  def _codex(self, path, root):
    return seed_codex_config(
      path,
      root=root,
      box_config_path="/home/agent/.codex/config.toml",
      codex_cwd="/home/agent/workspace",
    )

  def test_claude_settings_leaf_link_outside(self, home, outside, caplog):
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
    _sentinel_untouched(outside, before)
    assert _warned(caplog, home / ".claude" / "settings.json")

  def test_claude_settings_parent_link_outside(self, home, outside, caplog):
    (home / ".claude").symlink_to(outside)
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
    _sentinel_untouched(outside, before)
    assert not (outside / "settings.json").exists()

  def test_codex_config_leaf_and_parent_links(self, home, outside, caplog):
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").symlink_to(outside / "sentinel")
    before = _snapshot(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert self._codex(home / ".codex" / "config.toml", home) is False
    _sentinel_untouched(outside, before)
    (home / ".codex" / "config.toml").unlink()
    (home / ".codex").rmdir()
    (home / ".codex").symlink_to(outside)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert self._codex(home / ".codex" / "config.toml", home) is False
    _sentinel_untouched(outside, before)
    assert not (outside / "config.toml").exists()

  def test_link_inside_the_tree(self, home, caplog):
    real = home / "real.json"
    real.write_text("{}\n")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").symlink_to(real)
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
    assert real.read_text() == "{}\n"

  def test_a_missing_box_home_is_created(self, tmp_path):
    """The seeds have always created a missing config dir, box home included."""
    claude_home = tmp_path / "box" / "claude-home"
    codex_home = tmp_path / "box" / "codex-home"
    assert (
      seed_session_start_hook(
        claude_home / ".claude" / "settings.json",
        root=claude_home,
      )
      is True
    )
    assert self._codex(codex_home / ".codex" / "config.toml", codex_home) is True
    assert "SessionStart" in (claude_home / ".claude" / "settings.json").read_text()
    assert (codex_home / ".codex" / "config.toml").is_file()

  def test_a_dangling_link_at_the_box_home_is_not_followed(self, tmp_path, outside):
    """Creating a missing home never makes the directory a link points at."""
    home = tmp_path / "box" / "home"
    home.parent.mkdir()
    home.symlink_to(outside / "made")
    with pytest.raises(SymlinkRefusedError):
      seed_session_start_hook(home / ".claude" / "settings.json", root=home)
    with pytest.raises(SymlinkRefusedError):
      self._codex(home / ".codex" / "config.toml", home)
    assert not (outside / "made").exists()

  def test_no_link_control(self, home, caplog):
    with caplog.at_level(logging.WARNING, logger=_BOXFS):
      assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is True
      assert self._codex(home / ".codex" / "config.toml", home) is True
      assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
    assert "SessionStart" in (home / ".claude" / "settings.json").read_text()
    assert not caplog.records
