"""T-nowrite: the pure detection core writes nothing; ``reconcile`` holds its writes.

``box_resolve.walk_box_mode`` runs on a tree made read-only end to end (the workspace,
the registry, the journal), so any write it attempts fails, and every file's bytes and
mtime are compared before and after.  ``detect_box_mode`` is the walk plus
``import_reconcile.reconcile``, which clears a registered standalone's stale J2 entry.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kanibako.launch import box_resolve, journal
from kanibako.project.import_reconcile import ImportConflictError
from kanibako.settings.paths import BoxMode, DetectionResult, resolve_standalone_project

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only mode")


def _snapshot(root: Path) -> dict[str, tuple[int, int, bytes | str | None]]:
  """Every path under *root*: inode, mtime, and content (a link by its text)."""
  snap: dict[str, tuple[int, int, bytes | str | None]] = {}
  for dirpath, dirnames, filenames in os.walk(root):
    for leaf in dirnames + filenames:
      p = Path(dirpath, leaf)
      st = p.lstat()
      if p.is_symlink():
        body: bytes | str | None = os.readlink(p)
      elif stat.S_ISREG(st.st_mode):
        body = p.read_bytes()
      else:
        body = None
      snap[str(p)] = (st.st_ino, st.st_mtime_ns, body)
  return snap


def _set_tree_mode(root: Path, *, writable: bool) -> None:
  """Every directory and file under *root* (and *root*) read-only, or writable again."""
  for dirpath, dirnames, filenames in os.walk(root):
    for leaf in filenames:
      p = Path(dirpath, leaf)
      if not p.is_symlink():
        p.chmod(0o644 if writable else 0o444)
    for leaf in dirnames:
      p = Path(dirpath, leaf)
      if not p.is_symlink():
        p.chmod(0o755 if writable else 0o555)
  root.chmod(0o755 if writable else 0o555)


def _walk_read_only(tree: Path, subject: Path, std, config) -> DetectionResult | None:
  """``walk_box_mode`` on *subject* with *tree* read-only; asserts nothing under it changed."""
  before = _snapshot(tree)
  _set_tree_mode(tree, writable=False)
  try:
    return box_resolve.walk_box_mode(subject, std, config)
  finally:
    _set_tree_mode(tree, writable=True)
    assert _snapshot(tree) == before


@pytest.fixture
def stale_standalone(std, config, project_dir, credentials_dir):
  """A registered standalone box at *project_dir* with a stale ``import`` J2 entry."""
  proj = resolve_standalone_project(std, config, str(project_dir), initialize=True)
  box_key = str((project_dir / "box_data").resolve())
  journal.write_entry(std.journal, box_key, op="import", name=proj.name, mode="standalone")
  (project_dir / "src").mkdir()
  return box_key


@pytest.mark.parametrize("where", ["root", "subdir"])
def test_the_walk_leaves_a_stale_entry_and_reconcile_clears_it(
  std, config, project_dir, tmp_home, stale_standalone, where
):
  subject = project_dir if where == "root" else project_dir / "src"

  result = _walk_read_only(tmp_home, subject, std, config)

  assert result == DetectionResult(BoxMode.standalone, project_dir)
  assert journal.pending_import(std.journal, stale_standalone) is not None
  assert box_resolve.detect_box_mode(subject, std, config) == result
  assert journal.pending_import(std.journal, stale_standalone) is None


def test_the_walk_refuses_a_second_spelling_on_a_read_only_tree(
  std, config, project_dir, tmp_home, stale_standalone
):
  second = tmp_home / "second"
  second.symlink_to(project_dir)

  with pytest.raises(ImportConflictError, match=f"Work from {project_dir}"):
    _walk_read_only(tmp_home, second, std, config)
  assert journal.pending_import(std.journal, stale_standalone) is not None


def test_the_walk_answers_not_a_box_on_a_read_only_tree(std, config, tmp_home, credentials_dir):
  plain = tmp_home / "home" / "plain"
  plain.mkdir()

  assert _walk_read_only(tmp_home, plain, std, config) is None
