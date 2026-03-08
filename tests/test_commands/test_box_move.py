"""Tests for kanibako box move command."""

from __future__ import annotations

import argparse

from kanibako.commands.box._parser import run_move
from kanibako.config import load_config
from kanibako.names import read_names
from kanibako.paths import load_std_paths, resolve_project


class TestBoxMove:
    def test_move_project(self, config_file, tmp_home, credentials_dir):
        """Move a project workspace to a new location."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "myproject"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "newlocation"
        args = argparse.Namespace(
            args=[str(project_dir), str(dest)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 0
        assert dest.is_dir()
        assert not project_dir.exists()

        # Verify names.toml was updated.
        names = read_names(std.data_path)
        found = False
        for name, path in names["projects"].items():
            if path == str(dest):
                found = True
                break
        assert found, "names.toml should contain the new path"

    def test_move_same_path_errors(self, config_file, tmp_home, credentials_dir):
        """Moving to the same location returns an error."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        args = argparse.Namespace(
            args=[str(project_dir), str(project_dir)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 1

    def test_move_dest_exists_errors(self, config_file, tmp_home, credentials_dir):
        """Moving to an existing destination returns an error."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "existing"
        dest.mkdir()
        args = argparse.Namespace(
            args=[str(project_dir), str(dest)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 1

    def test_move_no_metadata_errors(self, config_file, tmp_home, credentials_dir):
        """Moving a non-kanibako directory returns an error."""
        project_dir = tmp_home / "plain"
        project_dir.mkdir()

        dest = tmp_home / "newplace"
        args = argparse.Namespace(
            args=[str(project_dir), str(dest)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 1

    def test_move_locked_project_errors(self, config_file, tmp_home, credentials_dir):
        """Moving a project with an active lock file returns an error."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "locked"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Create a lock file.
        (proj.metadata_path / ".kanibako.lock").write_text("locked")

        dest = tmp_home / "newloc"
        args = argparse.Namespace(
            args=[str(project_dir), str(dest)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 1

    def test_move_single_arg_uses_cwd(self, config_file, tmp_home, credentials_dir):
        """With a single positional arg, the cwd project is moved."""
        import os
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "cwd_proj"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        dest = tmp_home / "moved"
        # When only one arg, project_dir is None (uses cwd).
        os.chdir(str(project_dir))
        args = argparse.Namespace(
            args=[str(dest)],
            force=True,
        )
        rc = run_move(args)
        assert rc == 0
        assert dest.is_dir()
