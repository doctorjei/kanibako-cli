"""Tests for kanibako.commands.restore (extract command)."""

from __future__ import annotations

import argparse
import shutil
import tarfile
from unittest.mock import patch


from kanibako.config import load_config
from kanibako.errors import UserCancelled
from kanibako.paths import (
    load_std_paths,
    resolve_any_project,
    resolve_project,
    resolve_standalone_project,
)


class TestExtract:
    def test_round_trip(self, config_file, tmp_home, credentials_dir):
        """Archive then extract; verify data preserved."""
        from kanibako.commands.archive import run as archive_run
        from kanibako.commands.restore import run as extract_run

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Add test data
        (proj.metadata_path / "mydata.txt").write_text("important")

        archive_path = str(tmp_home / "roundtrip.txz")
        args = argparse.Namespace(
            path=project_dir,
            file=archive_path,
            all_projects=False,
            allow_uncommitted=True,
            allow_unpushed=True,
            force=True,
        )
        assert archive_run(args) == 0

        # Clean
        shutil.rmtree(proj.metadata_path)
        assert not proj.metadata_path.exists()

        # Extract
        args = argparse.Namespace(
            file=archive_path,
            path=project_dir,
            name=None,
            all_archives=False,
            force=True,
        )
        assert extract_run(args) == 0
        assert proj.metadata_path.is_dir()
        assert (proj.metadata_path / "mydata.txt").read_text() == "important"
        # Info file should be cleaned up
        assert not (proj.metadata_path / "kanibako-archive-info.txt").exists()

    def _archive_of(self, std, config, tmp_home, workspace, payload="payload"):
        """Create+archive a box for *workspace*; return the archive path."""
        from kanibako.commands.archive import run as archive_run

        proj = resolve_project(
            std, config, project_dir=str(workspace), initialize=True,
        )
        (proj.metadata_path / "mydata.txt").write_text(payload)
        archive_path = str(tmp_home / f"{workspace.name}.txz")
        assert archive_run(argparse.Namespace(
            path=str(workspace), file=archive_path, all_projects=False,
            allow_uncommitted=True, allow_unpushed=True, force=True,
        )) == 0
        return archive_path, proj

    @staticmethod
    def _extract(**kw):
        from kanibako.commands.restore import run as extract_run

        base = dict(file=None, path=None, name=None, all_archives=False, force=True)
        base.update(kw)
        return extract_run(argparse.Namespace(**base))

    def test_extract_never_writes_to_the_unregistered_placeholder(
        self, config_file, tmp_home, credentials_dir,
    ):
        """⚑⚑ THE BUG (proven on a real box, 2026-07-31). Extract resolved with
        ``initialize=False``; for a workspace with no registered box
        ``paths._resolve_local_dir`` returns the SENTINEL
        ``("", std.boxes / "__unregistered__")`` — a name-assignment placeholder that
        is only valid on the ``initialize=True`` path. Used as a destination it is a
        shared junk drawer: EVERY unregistered extract wrote into the SAME directory,
        clobbering the previous one, and no box was ever registered.

        RED if the destination resolve reverts to ``initialize=False``.
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        src = tmp_home / "srcws"
        src.mkdir()
        archive_path, _ = self._archive_of(std, config, tmp_home, src)

        # A DIFFERENT, unregistered workspace — the case that produced the junk dir.
        dest_ws = tmp_home / "destws"
        dest_ws.mkdir()
        assert self._extract(file=archive_path, path=str(dest_ws)) == 0

        assert not (std.boxes / "__unregistered__").exists(), (
            "the __unregistered__ sentinel must never be materialized as a real box"
        )
        proj = resolve_any_project(
            std, config, project_dir=str(dest_ws), initialize=False,
        )
        assert proj.name, "the extracted box must have a name"
        assert proj.metadata_path == std.boxes / proj.name
        assert (proj.metadata_path / "mydata.txt").read_text() == "payload"

    def test_extract_registers_the_box(self, config_file, tmp_home, credentials_dir):
        """An extracted box that is not REGISTERED is invisible to ``box list``,
        unreachable by name, and resolves straight back to the sentinel on the next
        command. Extract is a re-materialization: it registers like ``create``."""
        from kanibako.paths import primary_box_name_for_workspace

        config = load_config(config_file)
        std = load_std_paths(config)
        src = tmp_home / "regsrc"
        src.mkdir()
        archive_path, _ = self._archive_of(std, config, tmp_home, src)

        dest_ws = tmp_home / "regdest"
        dest_ws.mkdir()
        assert self._extract(file=archive_path, path=str(dest_ws)) == 0

        registered = primary_box_name_for_workspace(std.primary_workset, str(dest_ws))
        assert registered, f"{dest_ws} was not registered after extract"
        assert (std.boxes / registered / "mydata.txt").is_file()

    def test_extract_honours_name(self, config_file, tmp_home, credentials_dir):
        """``--name`` was declared in the parser and then never read — ``args.name``
        had zero references in the module."""
        config = load_config(config_file)
        std = load_std_paths(config)
        src = tmp_home / "namesrc"
        src.mkdir()
        archive_path, _ = self._archive_of(std, config, tmp_home, src)

        dest_ws = tmp_home / "namedest"
        dest_ws.mkdir()
        assert self._extract(
            file=archive_path, path=str(dest_ws), name="chosen",
        ) == 0

        assert (std.boxes / "chosen" / "mydata.txt").read_text() == "payload"

    def test_extract_into_an_already_registered_workspace_restores_in_place(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Re-extracting over your OWN box must restore in place, not fork a second
        box — the registry reverse-lookup reuses the existing name."""
        config = load_config(config_file)
        std = load_std_paths(config)
        ws = tmp_home / "samews"
        ws.mkdir()
        archive_path, proj = self._archive_of(std, config, tmp_home, ws)
        original_name, original_dir = proj.name, proj.metadata_path

        (original_dir / "mydata.txt").write_text("clobbered")
        assert self._extract(file=archive_path, path=str(ws)) == 0

        after = resolve_any_project(std, config, project_dir=str(ws), initialize=False)
        assert after.name == original_name
        assert after.metadata_path == original_dir
        assert (original_dir / "mydata.txt").read_text() == "payload"

    def test_name_into_its_OWN_workspace_restores_in_place(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ THE MOST NATURAL SPELLING OF THIS COMMAND: ``extract --name mybox`` into
        mybox's own workspace.

        It was REFUSED. ``check_primary_box_name_free`` refuses on MEMBERSHIP alone
        (it takes a workspace but uses it only for the $HOME guard), so asking it
        about a box's OWN name always says "taken" — and the cure text then told the
        user to delete the very box they were restoring.

        This escaped the first round because the existing in-place test passes no
        ``--name`` at all: the naming is the whole trigger.
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        ws = tmp_home / "ownws"
        ws.mkdir()
        proj = resolve_project(
            std, config, project_dir=str(ws), initialize=True, name_override="mine",
        )
        (proj.metadata_path / "mydata.txt").write_text("payload")
        archive_path = str(tmp_home / "own.txz")
        from kanibako.commands.archive import run as archive_run
        assert archive_run(argparse.Namespace(
            path=str(ws), file=archive_path, all_projects=False,
            allow_uncommitted=True, allow_unpushed=True, force=True,
        )) == 0
        (proj.metadata_path / "mydata.txt").write_text("clobbered")

        rc = self._extract(file=archive_path, path=str(ws), name="mine")
        assert rc == 0, capsys.readouterr().err
        assert (std.boxes / "mine" / "mydata.txt").read_text() == "payload"

    def test_name_colliding_with_another_workspace_is_refused_with_a_cure(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Guard-1 path-uniqueness: a name already claimed by a DIFFERENT workspace
        must refuse, and the message must name the cure rather than the rule."""
        config = load_config(config_file)
        std = load_std_paths(config)
        taken_ws = tmp_home / "takenws"
        taken_ws.mkdir()
        resolve_project(
            std, config, project_dir=str(taken_ws), initialize=True,
            name_override="taken",
        )

        src = tmp_home / "collsrc"
        src.mkdir()
        archive_path, _ = self._archive_of(std, config, tmp_home, src)
        dest_ws = tmp_home / "colldest"
        dest_ws.mkdir()

        assert self._extract(
            file=archive_path, path=str(dest_ws), name="taken",
        ) == 1
        err = capsys.readouterr().err
        assert "another --name" in err or "conflicting box" in err, err

    def test_name_with_all_is_refused(self, config_file, tmp_home, credentials_dir, capsys):
        """One name cannot address N boxes. Refusing beats silently dropping it."""
        assert self._extract(all_archives=True, name="x") == 1
        assert "--name cannot be combined with --all" in capsys.readouterr().err

    def test_missing_archive(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        args = argparse.Namespace(
            file="/nonexistent/archive.txz",
            path=str(tmp_home / "project"),
            name=None,
            all_archives=False,
            force=True,
        )
        assert run(args) == 1

    def test_no_archive_arg(self, config_file, tmp_home, credentials_dir):
        """Extract without archive file argument prints error."""
        from kanibako.commands.restore import run

        args = argparse.Namespace(
            file=None,
            path=None,
            name=None,
            all_archives=False,
            force=True,
        )
        assert run(args) == 1


class TestExtractExtended:
    def _create_archive(self, config_file, tmp_home, credentials_dir, archive_name="test.txz"):
        """Helper: create a valid archive from project."""
        from kanibako.commands.archive import run as archive_run

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        (proj.metadata_path / "data.txt").write_text("testdata")

        archive_path = str(tmp_home / archive_name)
        args = argparse.Namespace(
            path=project_dir, file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        rc = archive_run(args)
        assert rc == 0
        return archive_path, project_dir, proj

    def test_hash_mismatch_prompts(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        archive_path, _, _ = self._create_archive(config_file, tmp_home, credentials_dir)

        # Create a different project directory
        other = tmp_home / "other_project"
        other.mkdir()

        with patch("kanibako.commands.restore.confirm_prompt") as m_prompt:
            args = argparse.Namespace(
                file=archive_path, path=str(other), name=None,
                all_archives=False, force=False,
            )
            run(args)
            # confirm_prompt should have been called due to hash mismatch
            m_prompt.assert_called()

    def test_user_cancels_returns_2(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        archive_path, _, _ = self._create_archive(config_file, tmp_home, credentials_dir)

        other = tmp_home / "other_project"
        other.mkdir()

        with patch("kanibako.commands.restore.confirm_prompt", side_effect=UserCancelled("no")):
            args = argparse.Namespace(
                file=archive_path, path=str(other), name=None,
                all_archives=False, force=False,
            )
            rc = run(args)
            assert rc == 2

    def test_force_bypasses_mismatch(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        archive_path, _, _ = self._create_archive(config_file, tmp_home, credentials_dir)

        other = tmp_home / "other_project"
        other.mkdir()

        with patch("kanibako.commands.restore.confirm_prompt") as m_prompt:
            args = argparse.Namespace(
                file=archive_path, path=str(other), name=None,
                all_archives=False, force=True,
            )
            rc = run(args)
            assert rc == 0
            m_prompt.assert_not_called()

    def test_git_commit_mismatch(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.restore import run

        archive_path, project_dir, _ = self._create_archive(
            config_file, tmp_home, credentials_dir, "git.txz"
        )

        # The archive has git metadata. Current HEAD may differ.
        # We patch _validate_git_state to simulate a mismatch prompt
        with patch("kanibako.commands.restore.confirm_prompt"):
            args = argparse.Namespace(
                file=archive_path, path=project_dir, name=None,
                all_archives=False, force=False,
            )
            # This should work since hash matches (same project)
            run(args)

    def test_force_bypasses_git_mismatch(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        from kanibako.commands.restore import run

        archive_path, project_dir, _ = self._create_archive(
            config_file, tmp_home, credentials_dir, "git2.txz"
        )

        args = argparse.Namespace(
            file=archive_path, path=project_dir, name=None,
            all_archives=False, force=True,
        )
        rc = run(args)
        assert rc == 0

    def test_archive_from_git_workspace_not_git(self, config_file, tmp_home, credentials_dir, fake_git_repo):
        """Archive from a git repo, extract to a non-git workspace."""
        from kanibako.commands.archive import run as archive_run
        from kanibako.commands.restore import run as extract_run

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(fake_git_repo)
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        (proj.metadata_path / "data.txt").write_text("from-git")

        archive_path = str(tmp_home / "git-archive.txz")
        args = argparse.Namespace(
            path=project_dir, file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        assert archive_run(args) == 0

        # Extract to same path with force (same hash)
        args = argparse.Namespace(
            file=archive_path, path=project_dir, name=None,
            all_archives=False, force=True,
        )
        rc = extract_run(args)
        assert rc == 0

    def test_corrupt_archive_returns_1(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        corrupt = tmp_home / "corrupt.txz"
        corrupt.write_text("this is not a tar file")

        args = argparse.Namespace(
            file=str(corrupt), path=str(tmp_home / "project"), name=None,
            all_archives=False, force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_empty_archive_returns_1(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        empty_archive = tmp_home / "empty.txz"
        import lzma
        with lzma.open(str(empty_archive), "wb") as f:
            # Write a valid but empty tar
            with tarfile.open(fileobj=f, mode="w:"):
                pass

        args = argparse.Namespace(
            file=str(empty_archive), path=str(tmp_home / "project"), name=None,
            all_archives=False, force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_missing_info_file_returns_1(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.restore import run

        # Create a valid tar.xz with a directory but no info file
        archive_path = tmp_home / "no-info.txz"
        dummy_dir = tmp_home / "dummy_hash"
        dummy_dir.mkdir()
        (dummy_dir / "some_file.txt").write_text("data")
        with tarfile.open(str(archive_path), "w:xz") as tar:
            tar.add(str(dummy_dir), arcname="fakehash")

        args = argparse.Namespace(
            file=str(archive_path), path=str(tmp_home / "project"), name=None,
            all_archives=False, force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_extract_standalone_project(self, config_file, tmp_home, credentials_dir):
        """Extract an archive into a standalone project's kanibako/."""
        from kanibako.commands.archive import run as archive_run
        from kanibako.commands.restore import run as extract_run

        # Create a standalone project and archive it (the archive carries the
        # standalone settings.yaml, so extract preserves standalone mode).
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_standalone_project(
            std, config, project_dir=project_dir, initialize=True,
        )
        assert proj.mode.value == "standalone"
        (proj.metadata_path / "data.txt").write_text("restore-me")

        archive_path = str(tmp_home / "dec-restore.txz")
        args = argparse.Namespace(
            path=project_dir, file=archive_path,
            all_projects=False, allow_uncommitted=True, allow_unpushed=True, force=True,
        )
        assert archive_run(args) == 0

        # Remove the restorable payload but KEEP the standalone marker, so the
        # project still resolves as standalone (extract routes its destination
        # by detection; a real `settings.yaml` with mode=standalone is required
        # now that bare `.kanibako` dirs are no longer trusted as markers).
        (proj.metadata_path / "data.txt").unlink()

        args = argparse.Namespace(
            file=archive_path, path=project_dir, name=None,
            all_archives=False, force=True,
        )
        rc = extract_run(args)
        assert rc == 0

        # The project still resolves as standalone, and the payload is restored
        # into the standalone metadata path ({project}/.kanibako).
        dec_proj = resolve_any_project(std, config, project_dir=project_dir, initialize=False)
        assert dec_proj.mode.value == "standalone"
        assert (dec_proj.metadata_path / "data.txt").read_text() == "restore-me"
