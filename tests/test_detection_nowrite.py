"""T-nowrite: the pure detection core writes nothing; ``reconcile`` holds its writes.

``box_resolve.walk_box_mode`` runs on a tree made read-only end to end (the workspace,
the registry, the journal), so any write it attempts fails, and every file's bytes and
mtime are compared before and after.  ``detect_box_mode`` is the walk plus
``import_reconcile.reconcile``: the workset import pass, then a registered standalone's
stale J2 clear.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.launch import box_resolve, journal
from kanibako.project import registry_store
from kanibako.project.import_reconcile import ImportConflictError
from kanibako.project.workset import create_workset
from kanibako.settings.config import write_standalone_mark
from kanibako.settings.config_io import dump_doc
from kanibako.settings.paths import (
  BoxMode,
  DetectionResult,
  Treewalk,
  detect_project_mode,
  resolve_standalone_project,
)

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


def _walk_read_only(
  tree: Path, subject: Path, std, config, *, locked: Path | None = None
) -> DetectionResult | Treewalk[DetectionResult | None] | None:
  """``walk_box_mode`` on *subject* with *tree* read-only (*locked* unreadable too);
  asserts nothing under it changed."""
  before = _snapshot(tree)
  _set_tree_mode(tree, writable=False)
  if locked is not None:
    locked.chmod(0)
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

  walked = _walk_read_only(tmp_home, subject, std, config)

  result = DetectionResult(BoxMode.standalone, project_dir)
  assert walked == (result if where == "root" else Treewalk(subject, result))
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

  assert _walk_read_only(tmp_home, plain, std, config) == Treewalk(plain, None)
  assert box_resolve.detect_box_mode(plain, std, config) is None


@pytest.fixture(params=["mark", "unreadable", "legacy"])
def blocked(request, std, tmp_home, credentials_dir):
  """``(subject, root, locked)``: an unregistered workset *root* below a level that stops
  the treewalk (a standalone mark, an unreadable registry file, a legacy root)."""
  outer = tmp_home / "outer"
  outer.mkdir()
  locked = None
  if request.param == "mark":
    write_standalone_mark(outer)
  elif request.param == "unreadable":
    locked = outer / "registry.yaml"
    locked.write_text("{}\n")
  else:
    dump_doc(outer / "workset.yaml", {"workset": {"meta": {"name": "legacyws"}}})
  root = outer / "ws"
  create_workset("ws", root, std)
  registry_store.save_section(std.registry, "worksets", {})
  subject = root / "workspaces" / "proj"
  subject.mkdir()
  yield subject, root, locked
  if locked is not None:
    locked.chmod(0o644)


def test_an_import_below_a_stopping_level_wins(std, config, tmp_home, blocked, capsys):
  subject, root, locked = blocked

  walked = _walk_read_only(tmp_home, subject, std, config, locked=locked)

  assert isinstance(walked, Treewalk)
  assert registry_store.load_section(std.registry, "worksets") == {}
  if locked is not None:
    assert isinstance(walked.held, ConfigError)
    locked.chmod(0)
  capsys.readouterr()
  assert box_resolve.detect_box_mode(subject, std, config) == DetectionResult(
    BoxMode.named, subject
  )
  assert registry_store.load_section(std.registry, "worksets") == {"ws": str(root)}
  assert "Imported workset 'ws'" in capsys.readouterr().err


@pytest.mark.parametrize("detect", [box_resolve.detect_box_mode, detect_project_mode])
def test_an_importing_run_answers_as_the_next_run_does(std, config, blocked, capsys, detect):
  subject, _, locked = blocked
  if locked is not None:
    locked.chmod(0)

  first = detect(subject, std, config)
  assert "Imported workset 'ws'" in capsys.readouterr().err
  assert detect(subject, std, config) == first
  assert "Imported" not in capsys.readouterr().err


def test_a_legacy_root_with_no_import_below_still_refuses(std, config, tmp_home, credentials_dir):
  from kanibako.errors import LegacyWorksetIdentityError

  outer = tmp_home / "outer"
  subject = outer / "proj"
  subject.mkdir(parents=True)
  dump_doc(outer / "workset.yaml", {"workset": {"meta": {"name": "legacyws"}}})

  walked = _walk_read_only(tmp_home, subject, std, config)

  assert walked == Treewalk(subject, None)
  with pytest.raises(LegacyWorksetIdentityError):
    box_resolve.detect_box_mode(subject, std, config)
