"""Tests for kanibako.commands.box (list, info, and duplicate subcommands).

Lifecycle commands (remap / move / convert) are covered in
``test_lifecycle.py`` (engine) and ``test_lifecycle_cmd.py`` (CLI wrappers)."""

from __future__ import annotations

import argparse
import shutil
from unittest.mock import patch


from kanibako.config import load_config
from kanibako.paths import WorksetSpec, load_std_paths, resolve_standalone_project, resolve_project, resolve_workset_project
from kanibako.workset import add_project, create_workset, load_workset


class TestBoxList:
    def test_list_empty(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        assert "No known projects" in capsys.readouterr().out

    def test_list_shows_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "stopped" in out
        assert str(tmp_home / "project") in out

    def test_list_hides_orphans_by_default(self, config_file, tmp_home, credentials_dir, capsys):
        """By default, orphaned (missing) projects are not shown."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        # One healthy, one orphaned.
        ok_dir = tmp_home / "alive_proj"
        ok_dir.mkdir()
        resolve_project(std, config, project_dir=str(ok_dir), initialize=True)

        gone_dir = tmp_home / "gone_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "alive_proj" in out
        assert "missing" not in out

    def test_list_all_includes_orphans(self, config_file, tmp_home, credentials_dir, capsys):
        """--all flag includes orphaned projects in the listing."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "gone_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=True, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing" in out

    def test_list_quiet_names_only(self, config_file, tmp_home, credentials_dir, capsys):
        """-q flag outputs names only, one per line."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Quiet mode: just the name, no header, no status columns.
        lines = out.strip().split("\n")
        assert len(lines) == 1
        assert "NAME" not in out
        assert "STATUS" not in out

    def test_list_quiet_empty(self, config_file, tmp_home, credentials_dir, capsys):
        """Quiet mode on empty list produces no output."""
        from kanibako.commands.box import run_list

        args = argparse.Namespace(show_all=False, orphan=False, quiet=True)
        rc = run_list(args)
        assert rc == 0
        assert capsys.readouterr().out == ""


class TestBoxListOrphan:
    """Tests for box list --orphan (replaces old box orphan subcommand)."""

    def test_orphan_no_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        rc = run_list(args)
        assert rc == 0
        assert "No orphaned projects found" in capsys.readouterr().out

    def test_orphan_no_orphans(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        rc = run_list(args)
        assert rc == 0
        assert "No orphaned projects found" in capsys.readouterr().out

    def test_orphan_detects_missing_path(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "gone_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "gone_project" in out
        assert "1 orphaned project(s)" in out

    def test_orphan_skips_healthy_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        # One healthy, one orphaned.
        ok_dir = tmp_home / "alive_proj"
        ok_dir.mkdir()
        resolve_project(std, config, project_dir=str(ok_dir), initialize=True)

        gone_dir = tmp_home / "vanished_proj"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "vanished_proj" in out
        assert "alive_proj" not in out
        assert "1 orphaned project(s)" in out

    def test_orphan_detects_workset_missing_workspace(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "orphan-ws")
        source = tmp_home / "orphan_src"
        source.mkdir()
        add_project(ws, "orphan-proj", source)
        # Remove the workspace dir.
        shutil.rmtree(ws.workspaces_dir / "orphan-proj")

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "orphan-ws" in out
        assert "orphan-proj" in out
        assert "1 orphaned project(s)" in out

    def test_orphan_shows_hint(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "hint_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=False, orphan=True, quiet=False)
        run_list(args)
        out = capsys.readouterr().out
        assert "remap" in out
        assert "box rm" in out

    def test_orphan_quiet(self, config_file, tmp_home, credentials_dir, capsys):
        """--orphan -q outputs orphan names only."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "quiet_orphan"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace(show_all=False, orphan=True, quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert len(lines) == 1
        assert "NAME" not in out
        assert "orphaned project" not in out


class TestBoxDuplicate:
    def _make_args(self, source, dest, bare=False, force=False):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            bare=bare, force=force, to_mode=None,
        )

    def test_duplicate_success(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create source project with workspace content and metadata.
        src_dir = tmp_home / "src_project"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('hello')")
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / "marker.txt").write_text("session-data")

        dst_dir = tmp_home / "dst_project"

        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 0

        # Workspace copied.
        assert (dst_dir / "code.py").read_text() == "print('hello')"

        # Metadata copied.
        projects_base = std.boxes
        new_project = projects_base / "dst_project"
        assert (new_project / "marker.txt").read_text() == "session-data"

        # Source is intact.
        assert (src_dir / "code.py").read_text() == "print('hello')"
        assert proj.metadata_path.is_dir()

    def test_duplicate_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "bare_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "bare_dst"

        rc = run_duplicate(self._make_args(src_dir, dst_dir, bare=True, force=True))
        assert rc == 0

        # Workspace NOT created.
        assert not dst_dir.exists()

        # Metadata exists.
        projects_base = std.boxes
        assert (projects_base / "bare_dst").is_dir()

    def test_duplicate_source_not_dir_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        rc = run_duplicate(self._make_args(
            tmp_home / "nonexistent", tmp_home / "dst", force=True,
        ))
        assert rc == 1

    def test_duplicate_source_no_metadata_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        src_dir = tmp_home / "no_meta"
        src_dir.mkdir()

        rc = run_duplicate(self._make_args(src_dir, tmp_home / "dst", force=True))
        assert rc == 1

    def test_duplicate_dst_exists_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "dup_dst"
        dst_dir.mkdir()

        # Without --force, should fail because dst exists.
        rc = run_duplicate(self._make_args(src_dir, dst_dir))
        assert rc == 1

    def test_duplicate_dst_metadata_exists_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "meta_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "meta_dst"
        dst_dir.mkdir()
        resolve_project(std, config, project_dir=str(dst_dir), initialize=True)

        # Without --force, should fail because dst metadata exists.
        rc = run_duplicate(self._make_args(src_dir, dst_dir, bare=True))
        assert rc == 1

    def test_duplicate_lock_file_aborts_without_force(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "locked_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        dst_dir = tmp_home / "locked_dst"

        rc = run_duplicate(self._make_args(src_dir, dst_dir))
        assert rc == 2

    def test_duplicate_lock_file_skipped_with_force(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "lockforce_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        dst_dir = tmp_home / "lockforce_dst"

        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 0

    def test_duplicate_excludes_lock_file(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "excl_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        dst_dir = tmp_home / "excl_dst"

        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 0

        projects_base = std.boxes
        new_project = projects_base / "excl_dst"
        assert not (new_project / ".kanibako.lock").exists()

    def test_duplicate_force_overwrites_metadata(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "fw_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / "fresh.txt").write_text("new")

        dst_dir = tmp_home / "fw_dst"
        dst_dir.mkdir()
        dst_proj = resolve_project(std, config, project_dir=str(dst_dir), initialize=True)
        (dst_proj.metadata_path / "stale.txt").write_text("old")

        rc = run_duplicate(self._make_args(src_dir, dst_dir, bare=True, force=True))
        assert rc == 0

        projects_base = std.boxes
        # Force duplicate re-registers and gets a deduplicated name since
        # "fw_dst" is already taken by the pre-existing project.
        new_project = projects_base / "fw_dst2"

        # Fresh data present, stale data gone.
        assert (new_project / "fresh.txt").read_text() == "new"
        assert not (new_project / "stale.txt").exists()

    def test_duplicate_force_overwrites_workspace(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "ws_src"
        src_dir.mkdir()
        (src_dir / "new_file.txt").write_text("new")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "ws_dst"
        dst_dir.mkdir()
        (dst_dir / "existing.txt").write_text("keep")

        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 0

        # New file merged in.
        assert (dst_dir / "new_file.txt").read_text() == "new"
        # Existing file preserved (dirs_exist_ok merges).
        assert (dst_dir / "existing.txt").read_text() == "keep"

    def test_duplicate_metadata_copy_failure_leaves_no_orphan(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A crash during the metadata copy must NOT strand a registered name.

        Failure-injection (A2): ``run_duplicate`` registers a name via
        ``assign_name`` before copying metadata.  If the copy raises, the name
        must be unregistered and no partial dest dir left behind — duplicate
        either fully succeeds or leaves no trace.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.names import read_names

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "orphan_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "orphan_dst"

        names_before = read_names(std.data_path)["projects"]

        # bare=True so only the metadata copytree runs (no prior workspace copy).
        with patch(
            "kanibako.commands.box._duplicate.shutil.copytree",
            side_effect=RuntimeError("boom"),
        ):
            try:
                run_duplicate(self._make_args(src_dir, dst_dir, bare=True, force=True))
            except RuntimeError:
                pass

        # Name NOT left registered.
        names_after = read_names(std.data_path)["projects"]
        assert names_after == names_before
        assert "orphan_dst" not in names_after
        # No partial dest metadata dir.
        assert not (std.boxes / "orphan_dst").exists()


class TestBoxInfo:
    def test_info_local(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(path=project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "primary" in out
        assert str(tmp_home / "project") in out
        assert "Image:" in out
        assert "Container:" in out

    def test_info_standalone(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_standalone_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(path=project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "standalone" in out

    def test_info_no_data(self, config_file, tmp_home, capsys):
        from kanibako.commands.box import run_info

        args = argparse.Namespace(path=str(tmp_home / "project"))
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "No project data found" in out

    def test_info_lock_status(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        args = argparse.Namespace(path=project_dir)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ACTIVE" in out


class TestBoxDuplicateCrossMode:
    """Tests for cross-mode duplication (kanibako box duplicate --to)."""

    def _make_args(self, source, dest, to_mode, bare=False, force=True,
                    workset=None, project_name=None):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            to_mode=to_mode, bare=bare, force=force,
            workset=workset, project_name=project_name,
        )

    def test_duplicate_local_to_standalone(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_ac_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('hello')")
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / "marker.txt").write_text("ac-data")

        dst_dir = tmp_home / "dup_ac_dst"

        args = self._make_args(src_dir, dst_dir, "standalone")
        rc = run_duplicate(args)
        assert rc == 0

        # Destination should have standalone layout.
        assert (dst_dir / "box_data").is_dir()
        assert (dst_dir / "box_data" / "marker.txt").read_text() == "ac-data"
        assert (dst_dir / "code.py").read_text() == "print('hello')"
        # No breadcrumb in standalone.
        assert not (dst_dir / "box_data" / "project-path.txt").exists()

    def test_duplicate_standalone_to_local(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_dec_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('dec')")
        proj = resolve_standalone_project(
            std, config, project_dir=str(src_dir), initialize=True,
        )
        (proj.metadata_path / "marker.txt").write_text("dec-data")

        dst_dir = tmp_home / "dup_dec_dst"

        args = self._make_args(src_dir, dst_dir, "default")
        rc = run_duplicate(args)
        assert rc == 0

        # Destination should have local layout.
        projects_base = std.boxes
        ac_project = projects_base / "dup_dec_dst"
        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "dec-data"
        assert not (ac_project / "project-path.txt").exists()
        assert (dst_dir / "code.py").read_text() == "print('dec')"

    def test_duplicate_to_local_copy_failure_leaves_no_orphan(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A crash inside ``_duplicate_to_local`` must not strand a name.

        Failure-injection (A2): the cross-mode standalone->default path routes
        through ``_duplicate_to_local``, which calls ``assign_name`` (registers)
        before copying metadata.  A copy failure must unwind the registration +
        any partial dest dir.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.names import read_names

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "tlfail_src"
        src_dir.mkdir()
        proj = resolve_standalone_project(
            std, config, project_dir=str(src_dir), initialize=True,
        )
        (proj.metadata_path / "marker.txt").write_text("data")

        dst_dir = tmp_home / "tlfail_dst"

        names_before = read_names(std.data_path)["projects"]

        # bare=True isolates the metadata copytree inside _duplicate_to_local.
        with patch(
            "kanibako.commands.box._duplicate.shutil.copytree",
            side_effect=RuntimeError("boom"),
        ):
            try:
                run_duplicate(self._make_args(src_dir, dst_dir, "default", bare=True))
            except RuntimeError:
                pass

        names_after = read_names(std.data_path)["projects"]
        assert names_after == names_before
        assert "tlfail_dst" not in names_after
        assert not (std.boxes / "tlfail_dst").exists()

    def test_duplicate_cross_mode_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_bare_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('bare')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "dup_bare_dst"

        args = self._make_args(src_dir, dst_dir, "standalone", bare=True)
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists but workspace content not copied.
        assert (dst_dir / "box_data").is_dir()
        assert not (dst_dir / "code.py").exists()

    def test_duplicate_cross_mode_preserves_source(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_preserve_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / "marker.txt").write_text("original")

        dst_dir = tmp_home / "dup_preserve_dst"

        args = self._make_args(src_dir, dst_dir, "standalone")
        rc = run_duplicate(args)
        assert rc == 0

        # Source should be unchanged.
        assert proj.metadata_path.is_dir()
        assert (proj.metadata_path / "marker.txt").read_text() == "original"

    def test_duplicate_cross_mode_excludes_lock(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_lock_src"
        src_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        dst_dir = tmp_home / "dup_lock_dst"

        args = self._make_args(src_dir, dst_dir, "standalone", force=True)
        rc = run_duplicate(args)
        assert rc == 0

        assert not (dst_dir / ".kanibako" / ".kanibako.lock").exists()

    def test_duplicate_cross_mode_to_workset_requires_workset_flag(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_ws_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "dup_ws_dst"

        # No --workset flag → error
        args = self._make_args(src_dir, dst_dir, "workset")
        rc = run_duplicate(args)
        assert rc == 1


# ---------------------------------------------------------------------------
# Helpers for workset-aware tests
# ---------------------------------------------------------------------------

def _make_workset(tmp_home, std, ws_name="testws"):
    """Create a workset and return (ws, ws_root)."""
    ws_root = tmp_home / "worksets" / ws_name
    ws = create_workset(ws_name, ws_root, std)
    return ws, ws_root


def _make_local_project(tmp_home, std, config, name="myproj"):
    """Create a default-mode project with a marker file, return (proj, project_dir)."""
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "code.py").write_text("print('hello')")
    proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
    (proj.metadata_path / "marker.txt").write_text("ac-marker")
    (proj.shell_path / "custom.sh").write_text("echo hello")
    return proj, project_dir


def _make_standalone_project(tmp_home, std, config, name="myproj"):
    """Create a standalone project with a marker file, return (proj, project_dir)."""
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "code.py").write_text("print('dec')")
    proj = resolve_standalone_project(
        std, config, project_dir=str(project_dir), initialize=True,
    )
    (proj.metadata_path / "marker.txt").write_text("dec-marker")
    (proj.shell_path / "custom.sh").write_text("echo dec")
    return proj, project_dir


# ---------------------------------------------------------------------------
# TestBoxListWorkset
# ---------------------------------------------------------------------------

class TestBoxListWorkset:
    def test_list_shows_workset_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "myws")
        source = tmp_home / "src_proj"
        source.mkdir()
        add_project(ws, "cool-app", source)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "myws" in out
        assert "cool-app" in out

    def test_list_mixed_local_and_workset(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        # Default-mode project
        ac_dir = tmp_home / "ac_proj"
        ac_dir.mkdir()
        resolve_project(std, config, project_dir=str(ac_dir), initialize=True)

        # Workset project
        ws, _ = _make_workset(tmp_home, std, "mixed-ws")
        source = tmp_home / "ws_src"
        source.mkdir()
        add_project(ws, "ws-proj", source)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "NAME" in out  # local table header
        assert str(ac_dir) in out
        assert "mixed-ws" in out
        assert "ws-proj" in out

    def test_list_workset_missing_workspace(self, config_file, tmp_home, credentials_dir, capsys):
        """Workset missing workspace is hidden by default (orphan)."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "miss-ws")
        source = tmp_home / "miss_src"
        source.mkdir()
        add_project(ws, "miss-proj", source)
        # Remove the workspace dir
        shutil.rmtree(ws.workspaces_dir / "miss-proj")

        args = argparse.Namespace(show_all=True, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing" in out

    def test_list_workset_no_settings(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "nodata-ws")
        source = tmp_home / "nodata_src"
        source.mkdir()
        add_project(ws, "nodata-proj", source)
        # Remove the projects dir
        shutil.rmtree(ws.projects_dir / "nodata-proj")

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no-data" in out

    def test_list_workset_root_missing(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, ws_root = _make_workset(tmp_home, std, "gone-ws")
        # Remove the workset root entirely
        shutil.rmtree(ws_root)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Warning" in err


# ---------------------------------------------------------------------------
# TestBoxConvertToWorkset
# ---------------------------------------------------------------------------

class TestBoxDuplicateToWorkset:
    def _make_args(self, source, dest, workset=None, project_name=None,
                    bare=False, force=True):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            to_mode="workset", bare=bare, force=force,
            workset=workset, project_name=project_name,
        )

    def test_duplicate_local_to_workset(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_local_project(tmp_home, std, config, "dup_ac_src")
        ws, _ = _make_workset(tmp_home, std, "dup-ws")

        args = self._make_args(project_dir, tmp_home / "unused", workset="dup-ws")
        rc = run_duplicate(args)
        assert rc == 0

        # Workset copy exists
        assert (ws.projects_dir / "dup_ac_src" / "marker.txt").read_text() == "ac-marker"
        assert (ws.workspaces_dir / "dup_ac_src" / "code.py").read_text() == "print('hello')"
        # Source untouched
        assert proj.metadata_path.is_dir()
        assert (proj.metadata_path / "marker.txt").read_text() == "ac-marker"

    def test_duplicate_to_workset_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_local_project(tmp_home, std, config, "dup_bare_src")
        ws, _ = _make_workset(tmp_home, std, "bare-ws")

        args = self._make_args(project_dir, tmp_home / "unused", workset="bare-ws", bare=True)
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists
        assert (ws.projects_dir / "dup_bare_src" / "marker.txt").read_text() == "ac-marker"
        # Workspace NOT copied (skeleton dir exists from add_project but no code.py)
        assert not (ws.workspaces_dir / "dup_bare_src" / "code.py").exists()

    def test_duplicate_to_workset_requires_workset_flag(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        _, project_dir = _make_local_project(tmp_home, std, config, "dup_noflag_src")

        args = self._make_args(project_dir, tmp_home / "unused")
        rc = run_duplicate(args)
        assert rc == 1

    def test_duplicate_to_workset_preserves_source(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_local_project(tmp_home, std, config, "dup_pres_src")
        ws, _ = _make_workset(tmp_home, std, "pres-dup-ws")

        args = self._make_args(project_dir, tmp_home / "unused", workset="pres-dup-ws")
        rc = run_duplicate(args)
        assert rc == 0

        # Source untouched
        assert proj.metadata_path.is_dir()
        assert (project_dir / "code.py").read_text() == "print('hello')"
        assert (proj.metadata_path / "marker.txt").read_text() == "ac-marker"


# ---------------------------------------------------------------------------
# TestBoxDuplicateExternal — std-aware copy + refuse-bare-external (Phase 3)
# ---------------------------------------------------------------------------

class TestBoxDuplicateExternal:
    """Duplicate behavior around external-connected sources.

    Phase 3 repointed ``_duplicate_to_workset`` at the std-aware
    ``copy_into_workset`` helper and added the refuse-``--bare``-external policy
    (connected.yaml is 1:1).
    """

    def _make_external_connected(self, tmp_home, std, config,
                                  ws_name="ext-ws", proj_name="ext-proj"):
        """Create a workset with an EXTERNAL-connected project, return paths.

        Returns ``(ws, ext_dir, proj_name)`` where *ext_dir* lives outside the
        workset root and is registered in connected.yaml.
        """
        ws, _ = _make_workset(tmp_home, std, ws_name)
        ext_dir = tmp_home / f"{proj_name}_external"
        ext_dir.mkdir()
        (ext_dir / "code.py").write_text("print('external')")
        # Outside the workset root + std -> external wiring (connected.yaml etc.).
        add_project(ws, proj_name, ext_dir, std)
        return ws, ext_dir, proj_name

    def test_copy_into_workset_lands_internal_std_aware(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The std-aware helper lands a real INTERNAL workspace, no connection.

        Even when the source lives outside the workset root, a duplicate is a
        copy (not a connection): ``workspaces/<name>`` is a real directory, not a
        symlink, and no ``connected.yaml`` entry is written.
        """
        from kanibako.commands.box._lifecycle import copy_into_workset
        from kanibako.paths import BoxMode
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)

        proj, project_dir = _make_local_project(tmp_home, std, config, "int_src")
        ws, _ = _make_workset(tmp_home, std, "marker-ws")

        copy_into_workset(
            ws, "int_src", proj.metadata_path, proj.shell_path,
            project_dir, BoxMode.primary, copy_workspace=True, std=std,
        )

        # Real internal workspace dir with the copied tree (not a symlink).
        dup_ws = ws.workspaces_dir / "int_src"
        assert dup_ws.is_dir()
        assert not dup_ws.is_symlink()
        assert (dup_ws / "code.py").read_text() == "print('hello')"
        # Metadata + shell copied.
        assert (ws.projects_dir / "int_src" / "marker.txt").read_text() == "ac-marker"
        assert (ws.projects_dir / "int_src" / "shell" / "custom.sh").exists()
        # No external wiring — duplicate is a copy, not a connection.
        assert _load_connected(std) == {}

    def test_copy_into_workset_copy_failure_leaves_no_orphan(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A crash during the copies must roll back the add_project registration.

        Failure-injection (Tier B): ``copy_into_workset`` calls ``add_project``
        (registers in workset.yaml + creates per-project dirs) before the
        metadata/shell/workspace copytrees.  A copy failure must call
        ``remove_project`` to undo the registration + partial dirs, then re-raise
        — no registered-but-incomplete project is left behind.
        """
        from kanibako.commands.box._lifecycle import copy_into_workset
        from kanibako.paths import BoxMode
        from kanibako.workset import list_worksets

        config = load_config(config_file)
        std = load_std_paths(config)

        proj, project_dir = _make_local_project(tmp_home, std, config, "cf_src")
        ws, ws_root = _make_workset(tmp_home, std, "cf-ws")

        with patch(
            "kanibako.commands.box._lifecycle.shutil.copytree",
            side_effect=RuntimeError("boom"),
        ):
            try:
                copy_into_workset(
                    ws, "cf_proj", proj.metadata_path, proj.shell_path,
                    project_dir, BoxMode.primary, copy_workspace=True, std=std,
                )
            except RuntimeError:
                pass

        # Project NOT left registered in the workset (reload from disk).
        registry = list_worksets(std)
        reloaded = load_workset(registry["cf-ws"])
        assert all(p.name != "cf_proj" for p in reloaded.projects)
        # No partial per-project dirs left behind.
        assert not (ws.projects_dir / "cf_proj").exists()
        assert not (ws.workspaces_dir / "cf_proj").exists()

    def test_bare_duplicate_external_source_refused(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """``--bare`` on an external-connected source is refused (1:1)."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config,
        )

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=True, force=True,
            workset="ext-ws", project_name="dup-proj",
        )
        rc = run_duplicate(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "external-connected" in err
        # The duplicate was NOT registered.
        assert not (ws.projects_dir / "dup-proj").exists()

    def test_bare_duplicate_non_external_still_allowed(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A bare duplicate of an ordinary (non-external) source still works."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        src_dir = tmp_home / "plain_bare_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        args = argparse.Namespace(
            source_path=str(src_dir), new_path=str(tmp_home / "plain_bare_dst"),
            to_mode=None, bare=True, force=True,
        )
        rc = run_duplicate(args)
        assert rc == 0
        assert (std.boxes / "plain_bare_dst").is_dir()

    def test_bare_refusal_runs_before_workset_resolution(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The bare-external refusal fires first, before the to-workset checks.

        A connected external source also resolves as a workset project, but the
        1:1 refusal gives the clearer, earlier error and never touches state.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext-ws2", proj_name="ext-proj2",
        )
        before = _load_connected(std)

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=True, force=True,
            workset="ext-ws2", project_name="dup-proj",
        )
        rc = run_duplicate(args)
        assert rc == 1
        assert "external-connected" in capsys.readouterr().err
        # No state changed by the refusal.
        assert _load_connected(std) == before
        assert (ext_dir / "code.py").read_text() == "print('external')"

    def test_non_bare_duplicate_local_to_workset_internal(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Regression: a normal non-bare default->workset duplicate stays internal.

        The std-aware helper must keep producing a real ``workspaces/<name>``
        directory (not an external symlink) for an ordinary out-of-workset
        source, with no connected.yaml entry.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_local_project(tmp_home, std, config, "plain_ws_src")
        ws, _ = _make_workset(tmp_home, std, "plain-ws")

        args = argparse.Namespace(
            source_path=str(project_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=False, force=True,
            workset="plain-ws", project_name=None,
        )
        rc = run_duplicate(args)
        assert rc == 0

        dup_ws = ws.workspaces_dir / "plain_ws_src"
        assert dup_ws.is_dir()
        assert not dup_ws.is_symlink()
        assert (dup_ws / "code.py").read_text() == "print('hello')"
        # No external wiring for an internal (copied) workspace.
        assert _load_connected(std) == {}

    def test_duplicate_external_to_default(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--to default`` of an external-connected source succeeds.

        The destination gets the EXTERNAL dir's contents (copied from the live
        workspace, not the discoverability symlink) plus default-mode metadata.
        The source dir and its connection are untouched.  No WorksetError
        escapes.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext2def-ws", proj_name="e2d",
        )
        before = _load_connected(std)
        dest = tmp_home / "e2d_dst"

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(dest),
            to_mode="default", bare=False, force=True,
            workset=None, project_name=None,
        )
        rc = run_duplicate(args)
        assert rc == 0

        # Destination has the external dir's CONTENTS + default-mode metadata.
        assert (dest / "code.py").read_text() == "print('external')"
        ac_project = std.boxes / "e2d_dst"
        assert ac_project.is_dir()
        # Source + connection untouched.
        assert (ext_dir / "code.py").read_text() == "print('external')"
        assert _load_connected(std) == before
        assert (ws.projects_dir / proj_name).is_dir()

    def test_duplicate_external_to_standalone(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--to standalone`` of an external-connected source succeeds."""
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext2sa-ws", proj_name="e2s",
        )
        before = _load_connected(std)
        dest = tmp_home / "e2s_dst"

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(dest),
            to_mode="standalone", bare=False, force=True,
            workset=None, project_name=None,
        )
        rc = run_duplicate(args)
        assert rc == 0

        # Standalone layout at destination with the external contents.
        assert (dest / "box_data").is_dir()
        assert (dest / "code.py").read_text() == "print('external')"
        # Source + connection untouched.
        assert (ext_dir / "code.py").read_text() == "print('external')"
        assert _load_connected(std) == before

    def test_bare_duplicate_external_to_default_allowed(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--bare --to default`` of an external source succeeds (no aliasing).

        The bare result makes ``new_path`` itself the workspace, so the 1:1
        connected.yaml refusal does NOT apply.  Metadata only, no crash, no
        WorksetError.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="extbare-ws", proj_name="eb",
        )
        before = _load_connected(std)
        dest = tmp_home / "eb_dst"

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(dest),
            to_mode="default", bare=True, force=True,
            workset=None, project_name=None,
        )
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists; workspace content NOT copied (bare).
        assert (std.boxes / "eb_dst").is_dir()
        assert not (dest / "code.py").exists()
        # Source + connection untouched.
        assert (ext_dir / "code.py").read_text() == "print('external')"
        assert _load_connected(std) == before

    def test_bare_duplicate_external_to_workset_still_refused(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The narrowed guard still refuses ``--bare --to workset`` (aliasing case)."""
        from kanibako.commands.box import run_duplicate
        from kanibako.workset import _load_connected

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="extbarews-ws", proj_name="ebw",
        )
        before = _load_connected(std)

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=True, force=True,
            workset="extbarews-ws", project_name="dup-proj",
        )
        rc = run_duplicate(args)
        assert rc == 1
        assert "external-connected" in capsys.readouterr().err
        assert _load_connected(std) == before


# ---------------------------------------------------------------------------
# TestBoxDuplicateFromWorkset
# ---------------------------------------------------------------------------

class TestBoxDuplicateFromWorkset:
    def _make_workset_proj(self, tmp_home, std, config, ws_name="dfrom-ws", proj_name="ws-proj"):
        ws, _ = _make_workset(tmp_home, std, ws_name)
        source = tmp_home / f"{proj_name}_src"
        source.mkdir()
        add_project(ws, proj_name, source)
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), proj_name, std, config, initialize=True)
        (proj.metadata_path / "marker.txt").write_text("ws-dup-marker")
        (proj.shell_path / "custom.sh").write_text("echo ws-dup")
        (ws.workspaces_dir / proj_name / "code.py").write_text("print('ws-dup')")
        return ws, proj

    def _make_args(self, source, dest, to_mode, bare=False, force=True):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            to_mode=to_mode, bare=bare, force=force,
            workset=None, project_name=None,
        )

    def test_duplicate_workset_to_local(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config)
        workspace_path = ws.workspaces_dir / "ws-proj"
        dest = tmp_home / "dup_ws_ac_dst"

        args = self._make_args(workspace_path, dest, "default")
        rc = run_duplicate(args)
        assert rc == 0

        # Local layout at destination
        projects_base = std.boxes
        ac_project = projects_base / "dup_ws_ac_dst"
        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "ws-dup-marker"
        assert (dest / "code.py").read_text() == "print('ws-dup')"

    def test_duplicate_workset_to_standalone(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-dec", "dec-proj")
        workspace_path = ws.workspaces_dir / "dec-proj"
        dest = tmp_home / "dup_ws_dec_dst"

        args = self._make_args(workspace_path, dest, "standalone")
        rc = run_duplicate(args)
        assert rc == 0

        assert (dest / "box_data").is_dir()
        assert (dest / "box_data" / "marker.txt").read_text() == "ws-dup-marker"
        assert (dest / "code.py").read_text() == "print('ws-dup')"

    def test_duplicate_workset_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-bare", "bare-proj")
        workspace_path = ws.workspaces_dir / "bare-proj"
        dest = tmp_home / "dup_ws_bare_dst"

        args = self._make_args(workspace_path, dest, "standalone", bare=True)
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists but workspace not copied
        assert (dest / "box_data" / "marker.txt").read_text() == "ws-dup-marker"
        assert not (dest / "code.py").exists()

    def test_duplicate_workset_preserves_source(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-pres", "pres-proj")
        workspace_path = ws.workspaces_dir / "pres-proj"
        dest = tmp_home / "dup_ws_pres_dst"

        args = self._make_args(workspace_path, dest, "default")
        rc = run_duplicate(args)
        assert rc == 0

        # Source untouched
        assert proj.metadata_path.is_dir()
        assert (proj.metadata_path / "marker.txt").read_text() == "ws-dup-marker"
        assert (workspace_path / "code.py").read_text() == "print('ws-dup')"
