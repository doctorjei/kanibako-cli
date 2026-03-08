"""Integration tests for archive / restore workflows.

Tests exercise real git repos and real tarball creation/extraction.
Run with::

    pytest -m integration tests/test_archive_restore_integration.py -v
"""

from __future__ import annotations

import argparse
import subprocess
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest_integration import requires_git


@pytest.mark.integration
class TestArchiveGitIntegration:
    """Archive creation against real git repos."""

    @requires_git
    def test_archive_clean_git_repo(self, real_git_repo, integration_config):
        """Archive from a clean git repo succeeds."""
        from kanibako.commands.archive import _archive_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=False,
            allow_unpushed=True,  # no remote, so skip unpushed check
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = str(Path(outdir) / "test-archive.txz")
            rc = _archive_one(std, config, proj, output_file=outfile, args=args)
            assert rc == 0
            assert Path(outfile).is_file()

    @requires_git
    def test_archive_detects_uncommitted_changes(self, real_git_repo, integration_config):
        """Real ``git diff-index`` detects uncommitted changes."""
        from kanibako.commands.archive import _archive_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        # Modify a tracked file without committing
        readme = real_git_repo / "README.md"
        readme.write_text("# modified\n")

        args = argparse.Namespace(
            allow_uncommitted=False,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = str(Path(outdir) / "test-archive.txz")
            rc = _archive_one(std, config, proj, output_file=outfile, args=args)
            assert rc == 1

    @requires_git
    def test_archive_detects_unpushed_commits(self, real_git_repo, integration_config):
        """Real ``git rev-list`` detects unpushed commits."""
        from kanibako.commands.archive import _archive_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)

        # Set up a bare remote and push to it
        bare_remote = real_git_repo.parent / "bare_remote.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare_remote)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(bare_remote)],
            cwd=real_git_repo, capture_output=True, check=True,
        )
        # Get the current branch name
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=real_git_repo, capture_output=True, text=True, check=True,
        )
        branch = branch_result.stdout.strip()
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=real_git_repo, capture_output=True, check=True,
        )

        # Make a local commit that is not pushed
        newfile = real_git_repo / "unpushed.txt"
        newfile.write_text("unpushed content\n")
        subprocess.run(
            ["git", "add", "unpushed.txt"],
            cwd=real_git_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "unpushed commit"],
            cwd=real_git_repo, capture_output=True, check=True,
        )

        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=False,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = str(Path(outdir) / "test-archive.txz")
            rc = _archive_one(std, config, proj, output_file=outfile, args=args)
            assert rc == 1

    @requires_git
    def test_archive_contains_git_metadata(self, real_git_repo, integration_config):
        """Archive info contains branch, commit, and remote fields."""
        from kanibako.commands.archive import _archive_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = str(Path(outdir) / "test-archive.txz")
            rc = _archive_one(std, config, proj, output_file=outfile, args=args)
            assert rc == 0

            # Extract and check the info file
            with tempfile.TemporaryDirectory() as extract_dir:
                with tarfile.open(outfile, "r:xz") as tar:
                    tar.extractall(extract_dir, filter="data")

                entries = list(Path(extract_dir).iterdir())
                assert len(entries) == 1
                info_file = entries[0] / "kanibako-archive-info.txt"
                # Info file is cleaned up after archive, but it's inside the tarball
                # Actually, archive.py writes info, creates tarball including it,
                # then deletes it from metadata_path. So we need to check inside tarball.
                # Let's re-extract and look for it.
                # The info file IS inside the archive since it's written before tar.add.
                assert info_file.is_file()
                info_text = info_file.read_text()
                assert "Git repository: yes" in info_text
                assert "Branch:" in info_text
                assert "Commit:" in info_text

    @requires_git
    def test_archive_non_git_project_includes_warning(
        self, integration_home, integration_config
    ):
        """Archiving a non-git project includes a warning in metadata."""
        from kanibako.commands.archive import _archive_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)

        # Use the integration_home/project dir which is NOT a git repo
        project = integration_home / "project"
        project.mkdir(exist_ok=True)
        proj = resolve_project(std, config, project_dir=str(project), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = str(Path(outdir) / "test-archive.txz")
            rc = _archive_one(std, config, proj, output_file=outfile, args=args)
            assert rc == 0

            with tempfile.TemporaryDirectory() as extract_dir:
                with tarfile.open(outfile, "r:xz") as tar:
                    tar.extractall(extract_dir, filter="data")

                entries = list(Path(extract_dir).iterdir())
                info_file = entries[0] / "kanibako-archive-info.txt"
                assert info_file.is_file()
                info_text = info_file.read_text()
                assert "Git repository: no" in info_text


@pytest.mark.integration
class TestRestoreGitIntegration:
    """Restore validation against real git state."""

    @requires_git
    def test_restore_validates_git_commit_match(self, real_git_repo, integration_config):
        """Same-commit restore proceeds without prompt."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        # Write session data (create .claude dir; init_home() isn't called here)
        session_file = proj.shell_path / ".claude" / "session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text('{"test": "data"}')

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "test-archive.txz"
            rc = _archive_one(std, config, proj, output_file=str(outfile), args=args)
            assert rc == 0

            # Restore at the same commit — should succeed without prompting
            rc = _restore_one(
                std, config,
                project_dir=real_git_repo,
                archive_file=outfile,
                force=False,
            )
            assert rc == 0

    @requires_git
    def test_restore_detects_git_commit_mismatch(self, real_git_repo, integration_config):
        """Different HEAD triggers abort via mocked confirm_prompt."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.errors import UserCancelled
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "test-archive.txz"
            rc = _archive_one(std, config, proj, output_file=str(outfile), args=args)
            assert rc == 0

            # Make a new commit to change HEAD
            newfile = real_git_repo / "new_after_archive.txt"
            newfile.write_text("new content\n")
            subprocess.run(
                ["git", "add", "new_after_archive.txt"],
                cwd=real_git_repo, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "post-archive commit"],
                cwd=real_git_repo, capture_output=True, check=True,
            )

            # Mock confirm_prompt to raise UserCancelled (user says "no")
            with patch(
                "kanibako.commands.restore.confirm_prompt",
                side_effect=UserCancelled("Aborted."),
            ):
                rc = _restore_one(
                    std, config,
                    project_dir=real_git_repo,
                    archive_file=outfile,
                    force=False,
                )
                assert rc == 2  # Aborted

    @requires_git
    def test_restore_to_non_git_workspace_from_git_archive(
        self, integration_home, integration_config
    ):
        """Restoring a git-based archive into a non-git workspace warns."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.errors import UserCancelled
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)

        # Create a git repo and archive from it
        git_project = integration_home / "git_project"
        git_project.mkdir()
        subprocess.run(["git", "init"], cwd=git_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=git_project, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=git_project, capture_output=True, check=True,
        )
        (git_project / "file.txt").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=git_project, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=git_project, capture_output=True, check=True,
        )

        proj_git = resolve_project(std, config, project_dir=str(git_project), initialize=True)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "git-archive.txz"
            rc = _archive_one(std, config, proj_git, output_file=str(outfile), args=args)
            assert rc == 0

            # Restore to a non-git workspace
            non_git = integration_home / "non_git_project"
            non_git.mkdir()

            with patch(
                "kanibako.commands.restore.confirm_prompt",
                side_effect=UserCancelled("Aborted."),
            ):
                rc = _restore_one(
                    std, config,
                    project_dir=non_git,
                    archive_file=outfile,
                    force=False,
                )
                assert rc == 2  # Aborted because non-git target


@pytest.mark.integration
class TestArchiveRestoreRoundTrip:
    """End-to-end archive → restore preservation."""

    @requires_git
    def test_full_round_trip_preserves_session_data(
        self, real_git_repo, integration_config, integration_credentials
    ):
        """Byte-for-byte preservation of session data through round trip."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        # Write session data (create .claude dir; init_home() isn't called here)
        session_data = '{"conversations": [{"id": "abc123"}], "count": 42}'
        session_file = proj.shell_path / ".claude" / "session-data.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(session_data)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "roundtrip.txz"
            rc = _archive_one(std, config, proj, output_file=str(outfile), args=args)
            assert rc == 0

            # Delete session data
            session_file.unlink()
            assert not session_file.exists()

            # Restore
            rc = _restore_one(
                std, config,
                project_dir=real_git_repo,
                archive_file=outfile,
                force=True,
            )
            assert rc == 0

            # Verify byte-for-byte match
            assert session_file.exists()
            assert session_file.read_text() == session_data

    @requires_git
    def test_round_trip_with_binary_data(
        self, real_git_repo, integration_config, integration_credentials
    ):
        """Binary data survives the archive / restore cycle."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        binary_data = bytes(range(256))
        binary_file = proj.shell_path / ".claude" / "binary_blob.bin"
        binary_file.parent.mkdir(parents=True, exist_ok=True)
        binary_file.write_bytes(binary_data)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "binary-roundtrip.txz"
            rc = _archive_one(std, config, proj, output_file=str(outfile), args=args)
            assert rc == 0

            binary_file.unlink()

            rc = _restore_one(
                std, config,
                project_dir=real_git_repo,
                archive_file=outfile,
                force=True,
            )
            assert rc == 0

            assert binary_file.exists()
            assert binary_file.read_bytes() == binary_data

    @requires_git
    def test_round_trip_to_different_project_path(
        self, real_git_repo, integration_config, integration_credentials
    ):
        """Cross-project restore works when project paths differ."""
        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import _restore_one
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(integration_config)
        std = load_std_paths(config)
        proj_a = resolve_project(std, config, project_dir=str(real_git_repo), initialize=True)

        # Write data in project A (create .claude dir; init_home() isn't called here)
        session_data = "project A session data"
        session_file_a = proj_a.shell_path / ".claude" / "session.txt"
        session_file_a.parent.mkdir(parents=True, exist_ok=True)
        session_file_a.write_text(session_data)

        args = argparse.Namespace(
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )

        with tempfile.TemporaryDirectory() as outdir:
            outfile = Path(outdir) / "cross-project.txz"
            rc = _archive_one(std, config, proj_a, output_file=str(outfile), args=args)
            assert rc == 0

            # Create project B (a different git repo)
            project_b = real_git_repo.parent / "project_b"
            project_b.mkdir()
            subprocess.run(["git", "init"], cwd=project_b, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_b, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_b, capture_output=True, check=True,
            )
            (project_b / "file.txt").write_text("project B\n")
            subprocess.run(["git", "add", "."], cwd=project_b, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init b"],
                cwd=project_b, capture_output=True, check=True,
            )

            # Restore archive from A into B with force=True
            rc = _restore_one(
                std, config,
                project_dir=project_b,
                archive_file=outfile,
                force=True,
            )
            assert rc == 0

            # Verify data landed in project B's settings
            proj_b = resolve_project(std, config, project_dir=str(project_b), initialize=False)
            session_file_b = proj_b.shell_path / ".claude" / "session.txt"
            assert session_file_b.exists()
            assert session_file_b.read_text() == session_data
