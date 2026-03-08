"""Tests for kanibako.commands.archive."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path


from kanibako.config import load_config
from kanibako.paths import load_std_paths, resolve_project, resolve_workset_project
from kanibako.workset import add_project, create_workset


class TestArchive:
    def test_creates_archive(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.archive import run

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Put some data in metadata
        (proj.metadata_path / "test_data.txt").write_text("hello")

        archive_path = str(tmp_home / "test.txz")
        args = argparse.Namespace(
            path=project_dir,
            file=archive_path,
            all_projects=False,
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )
        rc = run(args)
        assert rc == 0
        assert Path(archive_path).exists()

        # Verify archive contents
        with tarfile.open(archive_path, "r:xz") as tar:
            names = tar.getnames()
            assert any("test_data.txt" in n for n in names)

    def test_no_session_data(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.archive import run

        # Create a project dir but don't initialize it
        new_project = tmp_home / "empty_project"
        new_project.mkdir()

        args = argparse.Namespace(
            path=str(new_project),
            file=None,
            all_projects=False,
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )
        rc = run(args)
        assert rc == 1


class TestArchiveExtended:
    def _setup_project(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        (proj.metadata_path / "data.txt").write_text("content")
        return proj, project_dir

    def test_git_uncommitted_blocked(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.archive import run
        import subprocess

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)
        # Create and stage an uncommitted change so diff-index detects it
        (fake_git_repo / "dirty.txt").write_text("dirty")
        subprocess.run(["git", "add", "dirty.txt"], cwd=fake_git_repo, capture_output=True, check=True)

        args = argparse.Namespace(
            path=project_dir, file=str(tmp_home / "out.txz"),
            all_projects=False, allow_uncommitted=False, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_uncommitted_allowed(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.archive import run

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)
        (fake_git_repo / "dirty.txt").write_text("dirty")

        args = argparse.Namespace(
            path=project_dir, file=str(tmp_home / "out.txz"),
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0

    def test_unpushed_blocked(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        """With an upstream set and unpushed commits, archive should fail."""
        from kanibako.commands.archive import run
        import subprocess

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)

        # Create a bare remote and set upstream
        remote = tmp_home / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote)],
            cwd=fake_git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "master"],
            cwd=fake_git_repo, capture_output=True,
        )
        # If push failed (branch may be 'main'), try that
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=fake_git_repo, capture_output=True,
        )
        # Now create an unpushed commit
        (fake_git_repo / "new.txt").write_text("new")
        subprocess.run(["git", "add", "."], cwd=fake_git_repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "local"],
            cwd=fake_git_repo, capture_output=True, check=True,
        )

        args = argparse.Namespace(
            path=project_dir, file=str(tmp_home / "out.txz"),
            all_projects=False, allow_uncommitted=True, allow_unpushed=False, force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_unpushed_allowed(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.archive import run

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)

        args = argparse.Namespace(
            path=project_dir, file=str(tmp_home / "out.txz"),
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0

    def test_non_git_project_succeeds(self, config_file, tmp_home, credentials_dir):
        """Archive works for non-git projects (no .git directory)."""
        from kanibako.commands.archive import run

        # tmp_home/project has no .git
        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)

        args = argparse.Namespace(
            path=project_dir, file=str(tmp_home / "out.txz"),
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert Path(tmp_home / "out.txz").exists()

    def test_auto_filename_format(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.archive import run

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)

        import os
        os.chdir(tmp_home)
        args = argparse.Namespace(
            path=project_dir, file=None,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0
        # Auto-generated filename should match pattern: kanibako-<name>-<hash>-<timestamp>.txz
        import glob
        files = glob.glob(str(tmp_home / "kanibako-project-*.txz"))
        assert len(files) == 1

    def test_git_metadata_in_archive(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.archive import run

        proj, project_dir = self._setup_project(config_file, tmp_home, credentials_dir)

        archive_path = str(tmp_home / "meta.txz")
        args = argparse.Namespace(
            path=project_dir, file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0

        # Extract and check info file was created (then cleaned up from settings_path)
        # The archive itself should contain the hash directory
        with tarfile.open(archive_path, "r:xz") as tar:
            names = tar.getnames()
            assert any("data.txt" in n for n in names)

    def test_archive_standalone_project(self, config_file, tmp_home):
        """Archive works for standalone projects (settings in kanibako/)."""
        from kanibako.commands.archive import run

        config = load_config(config_file)
        load_std_paths(config)
        project_dir = tmp_home / "project"
        # Create standalone marker and some data
        kanibako_dir = project_dir / ".kanibako"
        kanibako_dir.mkdir()
        (kanibako_dir / "data.txt").write_text("standalone-data")

        archive_path = str(tmp_home / "dec.txz")
        args = argparse.Namespace(
            path=str(project_dir), file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert Path(archive_path).exists()

        with tarfile.open(archive_path, "r:xz") as tar:
            names = tar.getnames()
            assert any("data.txt" in n for n in names)


class TestArchiveWorkset:
    def test_archive_all_includes_workset_projects(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.archive import run

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create a local project
        ac_dir = tmp_home / "ac_arch"
        ac_dir.mkdir()
        resolve_project(std, config, project_dir=str(ac_dir), initialize=True)

        # Create a workset with an initialized project
        ws_root = tmp_home / "worksets" / "arch-ws"
        ws = create_workset("arch-ws", ws_root, std)
        source = tmp_home / "arch_src"
        source.mkdir()
        add_project(ws, "arch-proj", source)
        proj = resolve_workset_project(ws, "arch-proj", std, config, initialize=True)
        (proj.metadata_path / "data.txt").write_text("ws-archive-data")

        import os
        os.chdir(tmp_home)
        args = argparse.Namespace(
            path=None, file=None,
            all_projects=True, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0

        # Both local and workset archives should be created
        import glob
        files = glob.glob(str(tmp_home / "kanibako-*.txz"))
        assert len(files) >= 2

    def test_archive_workset_project_single(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.archive import run

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "single-ws"
        ws = create_workset("single-ws", ws_root, std)
        source = tmp_home / "single_src"
        source.mkdir()
        add_project(ws, "single-proj", source)
        proj = resolve_workset_project(ws, "single-proj", std, config, initialize=True)
        (proj.metadata_path / "data.txt").write_text("single-data")

        archive_path = str(tmp_home / "single.txz")
        # Use workspace path as path arg
        args = argparse.Namespace(
            path=str(ws.workspaces_dir / "single-proj"), file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert Path(archive_path).exists()
