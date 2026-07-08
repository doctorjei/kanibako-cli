"""Tests for the redesigned kanibako box move command."""

from __future__ import annotations

import argparse

from kanibako.commands.box._lifecycle import run_move
from kanibako.config import load_config
from kanibako.config_io import load_doc
from kanibako.paths import load_std_paths, resolve_project
from kanibako.utils import project_hash


def _move_args(old, new, *, force=True, to_default=False, to_standalone=False,
               to_workset=None, name=None):
    return argparse.Namespace(
        old=str(old) if old is not None else None,
        new=str(new) if new is not None else None,
        force=force,
        to_default=to_default,
        to_standalone=to_standalone,
        to_workset=to_workset,
        name=name,
    )


class TestBoxMove:
    def test_move_project(self, config_file, tmp_home, credentials_dir):
        """Move a project workspace to a new location (records rewritten)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        (project_dir / "f.txt").write_text("data")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "newlocation"
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 0
        assert dest.is_dir()
        assert (dest / "f.txt").read_text() == "data"
        assert not project_dir.exists()

        # PRIMARY membership updated to the new path.
        from kanibako.paths import load_primary_boxes
        boxes = load_primary_boxes(std.primary_workset)
        assert str(dest) in boxes.values()
        assert str(project_dir) not in boxes.values()

        # P8b/Option A: the moved box resolves at the new workspace from the
        # membership (above), not an on-disk ``resolved.workspace``.
        proj = resolve_project(std, config, project_dir=str(dest), initialize=False)
        # The primary move re-derives the box name from the new workspace basename.
        assert boxes.get(proj.name) == str(dest)
        assert proj.project_path == dest.resolve()
        assert proj.project_hash == project_hash(str(dest.resolve()))
        assert "project" not in load_doc(proj.metadata_path / "settings.yaml")

    def test_move_requires_both_paths(self, config_file, tmp_home, credentials_dir):
        """move with a missing path returns an error (no cwd fallback)."""
        rc = run_move(_move_args(str(tmp_home / "dest"), None))
        assert rc == 1

    def test_move_same_path_errors(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        rc = run_move(_move_args(project_dir, project_dir))
        assert rc == 1

    def test_move_dest_exists_errors(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "existing"
        dest.mkdir()
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 1

    def test_move_no_metadata_errors(self, config_file, tmp_home, credentials_dir):
        project_dir = tmp_home / "plain"
        project_dir.mkdir()
        dest = tmp_home / "newplace"
        rc = run_move(_move_args(project_dir, dest))
        assert rc == 1

    def test_move_with_workset_target(self, config_file, tmp_home, credentials_dir):
        """move + --workset relocates AND changes ownership."""
        from kanibako.workset import create_workset, load_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("ws", tmp_home / "ws_root", std)
        project_dir = tmp_home / "movable"
        project_dir.mkdir()
        (project_dir / "f.txt").write_text("x")
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Land outside the workset (external member of ws).
        dest = tmp_home / "moved_ext"
        rc = run_move(_move_args(project_dir, dest, to_workset="ws"))
        assert rc == 0
        assert dest.is_dir()
        ws2 = load_workset(ws.root)
        assert any(p.name == "movable" for p in ws2.projects)
