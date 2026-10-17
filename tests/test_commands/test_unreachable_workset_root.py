"""A workset root kanibako cannot reach (EACCES, a link loop) is named, never a traceback.

Unreadable ⇒ a named refusal; a loop ⇒ its own cure; missing ⇒ the old wording.  A
listing names the entry and keeps sweeping.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

needs_non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 directory")

_LOOP = (
  ": it is a symlink loop that kanibako cannot resolve. Remove or repair the link, then retry."
)
_EACCES = (
  " could not be read (Permission denied). Make it and its parent directories readable, "
  "then retry."
)


def _cli(capsys, *argv: str) -> tuple[int, str, str]:
  from kanibako.cli import main

  capsys.readouterr()
  with pytest.raises(SystemExit) as info:
    main(list(argv))
  out, err = capsys.readouterr()
  assert "Traceback" not in err
  return info.value.code, out, err


@pytest.fixture
def boxes(config_file, tmp_home, credentials_dir, capsys):
  """Primary box ``q`` and workset ``ws3`` (member ``n``) under ``tmp_home/hide``."""
  q = (tmp_home / "p" / "q").resolve()
  q.mkdir(parents=True)
  (q / "f.txt").write_text("keep\n")
  hide = (tmp_home / "hide").resolve()
  hide.mkdir()
  n = (tmp_home / "src" / "n").resolve()
  n.mkdir(parents=True)
  assert _cli(capsys, "create", "--name", "q", str(q))[0] == 0
  assert _cli(capsys, "workset", "create", "--name", "ws3", str(hide / "ws3"))[0] == 0
  assert _cli(capsys, "workset", "connect", "--name", "n", "ws3", str(n))[0] == 0
  yield q, hide / "ws3"
  hide.chmod(0o755)


def _break(root: Path, kind: str) -> str:
  """Make *root* unreachable as *kind*; return the refusal that names it."""
  if kind == "loop":
    root.rename(root.with_name("ws3.real"))
    os.symlink("ws3", root)
    return f"{root}{_LOOP}"
  root.parent.chmod(0)
  return f"{root}{_EACCES}"


KINDS = [pytest.param("loop"), pytest.param("eacces", marks=needs_non_root)]


@pytest.mark.parametrize("kind", KINDS)
def test_both_listings_name_an_unreachable_root_and_keep_sweeping(boxes, capsys, kind):
  _q, root = boxes
  why = _break(root, kind)
  rc, out, err = _cli(capsys, "workset", "list")
  assert rc == 0
  assert err == f"Warning: working set 'ws3' could not be read: {why}\n"
  assert f"ws3{' ' * 21}ERROR  {root}" in out
  rc, out, err = _cli(capsys, "box", "list")
  assert rc == 0
  assert err == f"Warning: failed to load workset 'ws3': {why}\n"
  assert "q " in out


@pytest.mark.parametrize("kind", KINDS)
def test_naming_an_unreachable_workset_refuses_by_name(boxes, capsys, kind):
  q, root = boxes
  why = _break(root, kind)
  # ⚑ ``box move``/``convert`` read the root's workset.yaml before loading the workset.
  file_why = why if kind == "loop" else f"{root / 'workset.yaml'}{_EACCES}"
  for argv, expected in (
    (("box", "move", "--force", "q", str(q.parent / "m"), "--workset", "ws3"), file_why),
    (("box", "convert", "--force", "q", "--workset", "ws3"), file_why),
    (("workset", "info", "ws3"), why),
  ):
    rc, _out, err = _cli(capsys, *argv)
    assert rc == 1, argv
    assert err == f"Error: {expected}\n", argv
  assert (q / "f.txt").is_file()


@pytest.mark.parametrize("purge", [(), ("--purge", "--force")], ids=["rm", "purge"])
@pytest.mark.parametrize("kind", KINDS)
def test_rm_of_a_qualified_member_refuses_by_its_workset(boxes, capsys, kind, purge):
  _q, root = boxes
  _break(root, kind)
  rc, _out, err = _cli(capsys, "box", "rm", "ws3/n", *purge)
  assert rc == 1
  file_why = f"{root / 'workset.yaml'}" + (_LOOP if kind == "loop" else _EACCES)
  assert err == f"Error: {file_why}\n"


@needs_non_root
def test_rm_purge_refuses_before_its_progress_line(boxes, capsys):
  q, root = boxes
  _break(root, "eacces")
  rc, out, err = _cli(capsys, "box", "rm", "q", "--purge", "--force")
  assert rc == 1
  assert "Removing project" not in out
  assert err == f"Error: {root / 'workset.yaml'}{_EACCES}\n"
  root.parent.chmod(0o755)
  rc, out, _err = _cli(capsys, "box", "list")
  assert rc == 0 and "q " in out and "deregistered" not in out
  assert (q / "f.txt").is_file()


def test_a_missing_root_keeps_the_old_wording(boxes, capsys):
  _q, root = boxes
  shutil.rmtree(root)
  rc, _out, err = _cli(capsys, "workset", "list")
  assert rc == 0
  assert err == (
    f"Warning: working set 'ws3' could not be read: Workset root does not exist: {root}\n"
  )
  rc, _out, err = _cli(capsys, "box", "list")
  assert rc == 0
  assert err == f"Warning: workset 'ws3' root missing: {root}\n"
  rc, _out, err = _cli(capsys, "workset", "info", "ws3")
  assert rc == 1
  assert err == f"Error: Workset root does not exist: {root}\n"


@needs_non_root
def test_box_list_names_a_member_it_cannot_see_and_counts_it_present(boxes, capsys, tmp_home):
  src = (tmp_home / "src").resolve()
  src.chmod(0)
  try:
    rc, out, err = _cli(capsys, "box", "list")
  finally:
    src.chmod(0o755)
  assert rc == 0
  assert err == f"Warning: {src / 'n'}{_EACCES}\n"
  assert "  n " in out


@pytest.mark.parametrize(
  "flags", [(), ("--force",), ("--purge", "--force")], ids=["rm", "force", "purge"]
)
def test_workset_rm_of_a_looped_root_refuses_and_keeps_it(boxes, capsys, flags):
  _q, root = boxes
  held = sorted(p.name for p in root.iterdir())
  why = _break(root, "loop")
  rc, _out, err = _cli(capsys, "workset", "rm", "ws3", *flags)
  assert rc == 1
  assert err == f"Error: {why}\n"
  assert (
    root.is_symlink()
    and held
    and sorted(p.name for p in root.with_name("ws3.real").iterdir()) == held
  )
  rc, out, _err = _cli(capsys, "workset", "list")
  assert rc == 0 and f"ERROR  {root}" in out


def test_a_looped_reserved_alias_entry_is_kept_and_named(boxes, capsys, tmp_home):
  from kanibako.project.names import register_name
  from kanibako.settings.paths import load_std_paths

  alias = (tmp_home / "dl").resolve()
  alias.mkdir()
  register_name(load_std_paths().registry, "Default", str(alias))
  alias.rmdir()
  os.symlink("dl", alias)
  rc, _out, err = _cli(capsys, "workset", "list")
  assert rc == 0
  assert f"Warning: working set 'Default' could not be read: {alias}{_LOOP}\n" in err
  assert "Note: removed" not in err


def test_connect_walks_past_another_looped_root(boxes, capsys, tmp_home):
  _q, root = boxes
  other = (tmp_home / "ws4").resolve()
  assert _cli(capsys, "workset", "create", "--name", "ws4", str(other))[0] == 0
  _break(other, "loop")
  n2 = (tmp_home / "src" / "n2").resolve()
  n2.mkdir()
  rc, _out, err = _cli(capsys, "workset", "connect", "--name", "n2", "ws3", str(n2))
  assert rc == 0, err


@pytest.mark.parametrize("leaf", ["", "ws5"], ids=["target", "below"])
def test_create_at_a_looped_target_refuses_by_name(boxes, capsys, tmp_home, leaf):
  link = (tmp_home / "lp").resolve()
  os.symlink("lp", link)
  target = link / leaf if leaf else link
  rc, _out, err = _cli(capsys, "workset", "create", "--name", "ws5", str(target))
  assert rc == 1
  # ⚑ Refused at the first read under it, the target's workset.yaml, naming that path.
  assert err == f"Error: {target / 'workset.yaml'}{_LOOP}\n"
  rc, out, _err = _cli(capsys, "workset", "list")
  assert rc == 0 and "ws5" not in out


def test_a_looped_repointed_workspaces_dir_holds_no_cwd(boxes, capsys, tmp_home, monkeypatch):
  q, root = boxes
  wl = (tmp_home / "wl").resolve()
  os.symlink("wl", wl)
  (root / "workset.yaml").write_text(f"workset:\n  workspaces: {wl}\n")
  monkeypatch.chdir(q)
  rc, out, err = _cli(capsys, "box", "info", "q")
  assert rc == 0, err
  assert str(q) in out
