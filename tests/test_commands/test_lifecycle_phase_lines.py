"""A long relocation phase names itself on stderr as it starts.

``move``, ``convert``, and ``duplicate`` copy whole trees and remove the old ones; each
phase prints one line before it starts (never one per file), and a failure prints one
line before its undo, so a large copy is never mistaken for a hang.  The copies are
wrapped to print a marker, which pins each phase line BEFORE the copy it announces.
"""

from __future__ import annotations

import argparse
import sys

import pytest

from kanibako.commands.box import _duplicate, _lifecycle
from kanibako.commands.box._lifecycle import (
  UNCHANGED,
  TargetSpec,
  execute_lifecycle,
  resolve_lifecycle_target,
)
from kanibako.project.workset import add_project, create_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths, resolve_project
from kanibako.tree_copy import copy_tree_keeping_links


@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
  config = load_config(config_file)
  return config, load_std_paths(config), tmp_home


@pytest.fixture
def marked_copies(monkeypatch):
  """Wrap both modules' tree copy so each copy prints ``COPY <dst>`` as it starts."""

  def copy(src, dst, *args, **kwargs):
    print(f"COPY {dst}", file=sys.stderr)
    return copy_tree_keeping_links(src, dst, *args, **kwargs)

  monkeypatch.setattr(_lifecycle, "copy_tree_keeping_links", copy)
  monkeypatch.setattr(_duplicate, "copy_tree_keeping_links", copy)


def _primary(env, name="proj"):
  config, std, tmp_home = env
  pdir = tmp_home / name
  pdir.mkdir()
  (pdir / "code.py").write_text("print('hi')\n")
  resolve_project(std, config, project_dir=str(pdir), initialize=True)
  return pdir


def _move(env, pdir):
  config, std, tmp_home = env
  state = resolve_lifecycle_target(str(pdir), std, config)
  return execute_lifecycle(
    state,
    TargetSpec(location=tmp_home / "proj2", ownership=UNCHANGED, name="proj2", verb="move"),
    std,
    config,
    confirm=lambda: True,
  )


def _phase_lines(err):
  return [ln for ln in err.splitlines() if ln.endswith("...") or ln.startswith("COPY ")]


@pytest.mark.usefixtures("marked_copies")
class TestMove:
  def test_each_phase_is_named_before_it_runs(self, env, capsys):
    pdir = _primary(env)
    std = env[1]
    dest = env[2] / "proj2"
    _move(env, pdir)
    store = std.primary_workset
    lines = _phase_lines(capsys.readouterr().err)
    assert lines == [
      f"Copying the workspace to {dest}...",
      f"COPY {dest}",
      f"Copying the box data to {store / 'boxes' / 'proj2'}...",
      f"COPY {store / 'boxes' / 'proj2'}",
      f"COPY {store / 'boxes' / 'proj2' / 'home'}",
      f"Copying the vault to {store / 'vault' / 'ro' / 'proj2'}...",
      f"COPY {store / 'vault' / 'ro' / 'proj2'}",
      f"Copying the vault to {store / 'vault' / 'rw' / 'proj2'}...",
      f"COPY {store / 'vault' / 'rw' / 'proj2'}",
      f"Removing {store / 'boxes' / 'proj'}...",
      f"Removing {store / 'vault' / 'ro' / 'proj'}...",
      f"Removing {store / 'vault' / 'rw' / 'proj'}...",
      f"Removing the old workspace {pdir}...",
    ], "\n".join(lines)

  def test_a_failed_move_names_its_undo(self, env, capsys, monkeypatch):
    pdir = _primary(env)

    def boom(*args, **kwargs):
      raise OSError("boom")

    monkeypatch.setattr(_lifecycle, "_apply_ownership_and_markers", boom)
    with pytest.raises(OSError, match="boom"):
      _move(env, pdir)
    assert _phase_lines(capsys.readouterr().err)[-1] == "Undoing the move..."
    assert (pdir / "code.py").is_file()


@pytest.mark.usefixtures("marked_copies")
def test_a_convert_between_worksets_names_each_phase(env, capsys):
  """The workset-to-workset path: stash the store, release it, copy it forward."""
  config, std, tmp_home = env
  ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
  ws_b = create_workset("wsb", tmp_home / "wsb_root", std)
  internal = ws_a.workspaces_dir / "b1"
  internal.mkdir(parents=True)
  (internal / "code.py").write_text("print('hi')\n")
  add_project(ws_a, "b1", internal, std)
  state = resolve_lifecycle_target(str(internal), std, config)
  dest = ws_b.workspaces_dir / "b1"
  execute_lifecycle(
    state,
    TargetSpec(location=dest, ownership="wsb", verb="convert"),
    std,
    config,
    confirm=lambda: True,
  )
  lines = _phase_lines(capsys.readouterr().err)
  stash = next(ln for ln in lines if "kanibako-unwind-" in ln and ln.endswith("..."))
  assert stash.startswith("Copying the box data to "), "\n".join(lines)
  box = ws_b.projects_dir / "b1"
  wanted = [
    f"Copying the workspace to {dest}...",
    f"COPY {dest}",
    stash,
    "Removing the old box data of 'b1'...",
    f"Copying the box data to {box}...",
    f"COPY {box}",
    f"Removing the old workspace {internal}...",
  ]
  assert [ln for ln in lines if ln in wanted] == wanted, "\n".join(lines)


@pytest.fixture
def confirmed(monkeypatch):
  monkeypatch.setattr(_duplicate, "confirm_or_abort", lambda *args: True)


@pytest.mark.usefixtures("marked_copies", "confirmed")
class TestDuplicate:
  def _args(self, src, dst):
    return argparse.Namespace(
      source_path=str(src), new_path=str(dst), bare=False, force=False, to_mode=None
    )

  def test_each_copy_is_named_before_it_runs(self, env, capsys):
    config, std, tmp_home = env
    pdir = _primary(env)
    dst = tmp_home / "dup"
    assert _duplicate.run_duplicate(self._args(pdir, dst)) == 0
    assert _phase_lines(capsys.readouterr().err) == [
      f"Copying the workspace to {dst}...",
      f"COPY {dst}",
      f"Copying the box data to {std.boxes / 'dup'}...",
      f"COPY {std.boxes / 'dup'}",
    ]

  def test_a_failed_duplicate_names_its_undo(self, env, capsys, monkeypatch):
    config, std, tmp_home = env
    pdir = _primary(env)
    dst = tmp_home / "dup"

    def boom(*args, **kwargs):
      raise RuntimeError("boom")

    monkeypatch.setattr(_duplicate, "copy_tree_keeping_links", boom)
    with pytest.raises(RuntimeError, match="boom"):
      _duplicate.run_duplicate(self._args(pdir, dst))
    lines = _phase_lines(capsys.readouterr().err)
    assert lines == [f"Copying the workspace to {dst}...", "Undoing the duplicate..."]
