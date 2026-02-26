"""Tests for kanibako.commands.box (list, migrate, duplicate, and convert subcommands)."""

from __future__ import annotations

import argparse
import shutil


from kanibako.config import load_config
from kanibako.paths import load_std_paths, resolve_decentralized_project, resolve_project, resolve_workset_project
from kanibako.workset import add_project, create_workset, load_workset


class TestBoxList:
    def test_list_empty(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        assert "No known projects" in capsys.readouterr().out

    def test_list_shows_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ok" in out
        assert str(tmp_home / "project") in out

    def test_list_shows_missing_status(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create a project, then remove the directory.
        gone_dir = tmp_home / "gone_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "missing" in out


class TestBoxOrphan:
    def test_orphan_no_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

        args = argparse.Namespace()
        rc = run_orphan(args)
        assert rc == 0
        assert "No orphaned projects found" in capsys.readouterr().out

    def test_orphan_no_orphans(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace()
        rc = run_orphan(args)
        assert rc == 0
        assert "No orphaned projects found" in capsys.readouterr().out

    def test_orphan_detects_missing_path(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "gone_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace()
        rc = run_orphan(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "gone_project" in out
        assert "1 orphaned project(s)" in out

    def test_orphan_skips_healthy_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

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

        args = argparse.Namespace()
        rc = run_orphan(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "vanished_proj" in out
        assert "alive_proj" not in out
        assert "1 orphaned project(s)" in out

    def test_orphan_detects_workset_missing_workspace(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "orphan-ws")
        source = tmp_home / "orphan_src"
        source.mkdir()
        add_project(ws, "orphan-proj", source)
        # Remove the workspace dir.
        shutil.rmtree(ws.workspaces_dir / "orphan-proj")

        args = argparse.Namespace()
        rc = run_orphan(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "orphan-ws" in out
        assert "orphan-proj" in out
        assert "1 orphaned project(s)" in out

    def test_orphan_shows_hint(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_orphan

        config = load_config(config_file)
        std = load_std_paths(config)

        gone_dir = tmp_home / "hint_project"
        gone_dir.mkdir()
        resolve_project(std, config, project_dir=str(gone_dir), initialize=True)
        shutil.rmtree(gone_dir)

        args = argparse.Namespace()
        run_orphan(args)
        out = capsys.readouterr().out
        assert "migrate" in out
        assert "purge" in out


class TestBoxMigrate:
    def test_migrate_success(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        # Initialize old project.
        old_dir = tmp_home / "old_project"
        old_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        # Create a marker file to verify data is preserved.
        marker = proj.metadata_path / "marker.txt"
        marker.write_text("hello")

        # Create new project directory.
        new_dir = tmp_home / "new_project"
        new_dir.mkdir()

        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=str(new_dir),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 0

        # Old settings should be gone.
        assert not proj.metadata_path.exists()

        # New settings should exist with preserved data (name-based directory).
        projects_base = std.data_path / "boxes"
        new_project = projects_base / "new_project"
        assert new_project.is_dir()
        assert (new_project / "marker.txt").read_text() == "hello"

        # Workspace path stored in project.toml (no more project-path.txt).
        from kanibako.config import read_project_meta
        meta = read_project_meta(new_project / "project.toml")
        assert meta is not None
        assert meta["workspace"] == str(new_dir.resolve())

    def test_migrate_same_path_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        project_dir = tmp_home / "project"

        args = argparse.Namespace(
            old_path=str(project_dir),
            new_path=str(project_dir),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 1

    def test_migrate_no_old_data_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        old_dir = tmp_home / "nonexistent_old"
        new_dir = tmp_home / "project"  # exists (created by tmp_home)

        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=str(new_dir),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 1

    def test_migrate_new_path_not_dir_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        old_dir = tmp_home / "old_proj"
        old_dir.mkdir()
        resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=str(tmp_home / "does_not_exist"),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 1

    def test_migrate_new_data_exists_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        # Initialize both projects.
        old_dir = tmp_home / "old_proj2"
        old_dir.mkdir()
        resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        new_dir = tmp_home / "new_proj2"
        new_dir.mkdir()
        resolve_project(std, config, project_dir=str(new_dir), initialize=True)

        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=str(new_dir),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 1

    def test_migrate_defaults_to_cwd(self, config_file, tmp_home, credentials_dir, monkeypatch):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        old_dir = tmp_home / "old_cwd_proj"
        old_dir.mkdir()
        resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        # CWD is tmp_home/project (set by tmp_home fixture).
        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=None,
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 0

        # Verify data landed under CWD's name-based directory.
        projects_base = std.data_path / "boxes"
        new_project = projects_base / "project"
        assert new_project.is_dir()

    def test_migrate_warns_on_lock_file(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        old_dir = tmp_home / "locked_proj"
        old_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        # Create a lock file.
        (proj.metadata_path / ".kanibako.lock").touch()

        new_dir = tmp_home / "new_locked_proj"
        new_dir.mkdir()

        # With --force, should still succeed despite lock file.
        args = argparse.Namespace(
            old_path=str(old_dir),
            new_path=str(new_dir),
            to_mode=None,
            force=True,
        )
        rc = run_migrate(args)
        assert rc == 0


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
        projects_base = std.data_path / "boxes"
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
        projects_base = std.data_path / "boxes"
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

        projects_base = std.data_path / "boxes"
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

        projects_base = std.data_path / "boxes"
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


class TestBoxInfo:
    def test_info_account_centric(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(path=project_dir)
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "account_centric" in out
        assert str(tmp_home / "project") in out

    def test_info_decentralized(self, config_file, tmp_home, capsys):
        from kanibako.commands.box import run_info

        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()

        args = argparse.Namespace(path=str(project_dir))
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "decentralized" in out

    def test_info_no_data(self, config_file, tmp_home, capsys):
        from kanibako.commands.box import run_info

        args = argparse.Namespace(path=str(tmp_home / "project"))
        rc = run_info(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "No project data found" in err

    def test_info_lock_status(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        args = argparse.Namespace(path=project_dir)
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "ACTIVE" in out


class TestBoxMigrateShell:
    """Test that same-mode migrate also moves shell/ (inside projects/{hash}/)."""

    def test_migrate_moves_home_data(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        old_dir = tmp_home / "shell_old"
        old_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(old_dir), initialize=True)

        # Add a marker in the home directory.
        (proj.shell_path / "shell_marker.txt").write_text("shell-data")

        new_dir = tmp_home / "shell_new"
        new_dir.mkdir()

        args = argparse.Namespace(
            old_path=str(old_dir), new_path=str(new_dir),
            to_mode=None, force=True,
        )
        rc = run_migrate(args)
        assert rc == 0

        # Old home should be gone (parent metadata_path is renamed).
        assert not proj.shell_path.exists()

        # New home should exist with marker (inside projects/{name}/home/).
        new_home = std.data_path / "boxes" / "shell_new" / "shell"
        assert new_home.is_dir()
        assert (new_home / "shell_marker.txt").read_text() == "shell-data"


class TestBoxConvert:
    """Tests for cross-mode conversion (kanibako box migrate --to)."""

    def _convert_args(self, project_path=None, to_mode="decentralized", force=True,
                       workset=None, project_name=None, in_place=False):
        return argparse.Namespace(
            old_path=str(project_path) if project_path else None,
            new_path=None,
            to_mode=to_mode,
            force=force,
            workset=workset,
            project_name=project_name,
            in_place=in_place,
        )

    def test_convert_ac_to_decentralized(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_ac"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (proj.metadata_path / "marker.txt").write_text("settings-data")
        (proj.shell_path / "custom.sh").write_text("echo hello")

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        # Decentralized layout should exist.
        assert (project_dir / ".kanibako").is_dir()
        assert (project_dir / ".kanibako" / "shell").is_dir()
        assert (project_dir / ".kanibako" / "marker.txt").read_text() == "settings-data"
        assert (project_dir / ".kanibako" / "shell" / "custom.sh").read_text() == "echo hello"

        # Old AC data should be gone.
        assert not proj.metadata_path.exists()

    def test_convert_decentralized_to_ac(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_dec"
        project_dir.mkdir()
        proj = resolve_decentralized_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        (proj.metadata_path / "marker.txt").write_text("dec-settings")
        (proj.shell_path / "custom.sh").write_text("echo dec")

        args = self._convert_args(project_dir, "account-centric")
        rc = run_migrate(args)
        assert rc == 0

        # AC layout should exist.
        projects_base = std.data_path / "boxes"
        ac_project = projects_base / "conv_dec"
        ac_home = ac_project / "shell"

        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "dec-settings"
        assert ac_home.is_dir()
        assert (ac_home / "custom.sh").read_text() == "echo dec"

        # Old decentralized data should be gone.
        assert not (project_dir / ".kanibako").exists()
        assert not (project_dir / ".kanibako" / "shell").exists()

    def test_convert_same_mode_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_same"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        args = self._convert_args(project_dir, "account-centric")
        rc = run_migrate(args)
        assert rc == 1

    def test_convert_to_workset_requires_workset_flag(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        project_dir = tmp_home / "conv_ws"
        project_dir.mkdir()

        # No --workset flag → error
        args = self._convert_args(project_dir, "workset")
        rc = run_migrate(args)
        assert rc == 1

    def test_convert_preserves_credentials(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_creds"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Seed credentials manually (init no longer copies them; target.init_home does).
        import json
        creds_file = proj.shell_path / ".claude" / ".credentials.json"
        creds_file.parent.mkdir(parents=True, exist_ok=True)
        creds_file.write_text(json.dumps({"claudeAiOauth": {"token": "test-token"}}))
        original_creds = creds_file.read_text()

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        # Credentials should survive in new location (home/.claude/).
        new_creds = project_dir / ".kanibako" / "shell" / ".claude" / ".credentials.json"
        assert new_creds.exists()
        assert new_creds.read_text() == original_creds

    def test_convert_preserves_shell_contents(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_shell"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (proj.shell_path / "custom_tool").write_text("#!/bin/bash\necho tool")

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        assert (project_dir / ".kanibako" / "shell" / "custom_tool").read_text() == "#!/bin/bash\necho tool"

    def test_convert_removes_breadcrumb_for_decentralized(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_bc"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        # Decentralized should NOT have project-path.txt.
        assert not (project_dir / ".kanibako" / "project-path.txt").exists()

    def test_convert_stores_workspace_in_toml_for_ac(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_bc_ac"
        project_dir.mkdir()
        resolve_decentralized_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )

        args = self._convert_args(project_dir, "account-centric")
        rc = run_migrate(args)
        assert rc == 0

        # Workspace should be stored in project.toml, not project-path.txt.
        from kanibako.config import read_project_meta
        projects_base = std.data_path / "boxes"
        ac_dir = projects_base / "conv_bc_ac"
        assert not (ac_dir / "project-path.txt").exists()
        meta = read_project_meta(ac_dir / "project.toml")
        assert meta is not None

    def test_convert_excludes_lock_file(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_lock_excl"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        args = self._convert_args(project_dir, "decentralized", force=True)
        rc = run_migrate(args)
        assert rc == 0

        # Lock file should NOT be copied to the new location.
        assert not (project_dir / ".kanibako" / ".kanibako.lock").exists()

    def test_convert_warns_on_lock_file(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_lock_warn"
        project_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (proj.metadata_path / ".kanibako.lock").touch()

        # Without --force, should abort on lock file.
        args = self._convert_args(project_dir, "decentralized", force=False)
        rc = run_migrate(args)
        assert rc == 2

    def test_convert_force_skips_confirmation(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_force"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # With --force, should succeed without prompting.
        args = self._convert_args(project_dir, "decentralized", force=True)
        rc = run_migrate(args)
        assert rc == 0
        assert (project_dir / ".kanibako").is_dir()

    def test_convert_creates_vault_gitignore(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_vault"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # vault/ should already exist from AC init. Remove the gitignore to test creation.
        vault_gitignore = project_dir / "vault" / ".gitignore"
        if vault_gitignore.exists():
            vault_gitignore.unlink()

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        assert vault_gitignore.exists()
        assert "share-rw/" in vault_gitignore.read_text()

    def test_convert_writes_root_gitignore(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        project_dir = tmp_home / "conv_gi"
        project_dir.mkdir()
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        args = self._convert_args(project_dir, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        gitignore = project_dir / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert ".kanibako/" in content

    def test_convert_defaults_to_cwd(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)

        # CWD is tmp_home/project (set by tmp_home fixture).
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # No project path argument → should use cwd.
        args = argparse.Namespace(
            old_path=None, new_path=None,
            to_mode="decentralized", force=True,
        )
        rc = run_migrate(args)
        assert rc == 0

        assert (project_dir / ".kanibako").is_dir()


class TestBoxDuplicateCrossMode:
    """Tests for cross-mode duplication (kanibako box duplicate --to)."""

    def _make_args(self, source, dest, to_mode, bare=False, force=True,
                    workset=None, project_name=None):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            to_mode=to_mode, bare=bare, force=force,
            workset=workset, project_name=project_name,
        )

    def test_duplicate_ac_to_decentralized(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_ac_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('hello')")
        proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        (proj.metadata_path / "marker.txt").write_text("ac-data")

        dst_dir = tmp_home / "dup_ac_dst"

        args = self._make_args(src_dir, dst_dir, "decentralized")
        rc = run_duplicate(args)
        assert rc == 0

        # Destination should have decentralized layout.
        assert (dst_dir / ".kanibako").is_dir()
        assert (dst_dir / ".kanibako" / "marker.txt").read_text() == "ac-data"
        assert (dst_dir / "code.py").read_text() == "print('hello')"
        # No breadcrumb in decentralized.
        assert not (dst_dir / ".kanibako" / "project-path.txt").exists()

    def test_duplicate_decentralized_to_ac(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_dec_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('dec')")
        proj = resolve_decentralized_project(
            std, config, project_dir=str(src_dir), initialize=True,
        )
        (proj.metadata_path / "marker.txt").write_text("dec-data")

        dst_dir = tmp_home / "dup_dec_dst"

        args = self._make_args(src_dir, dst_dir, "account-centric")
        rc = run_duplicate(args)
        assert rc == 0

        # Destination should have AC layout.
        projects_base = std.data_path / "boxes"
        ac_project = projects_base / "dup_dec_dst"
        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "dec-data"
        assert not (ac_project / "project-path.txt").exists()
        assert (dst_dir / "code.py").read_text() == "print('dec')"

    def test_duplicate_cross_mode_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_bare_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('bare')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "dup_bare_dst"

        args = self._make_args(src_dir, dst_dir, "decentralized", bare=True)
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists but workspace content not copied.
        assert (dst_dir / ".kanibako").is_dir()
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

        args = self._make_args(src_dir, dst_dir, "decentralized")
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

        args = self._make_args(src_dir, dst_dir, "decentralized", force=True)
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


def _make_ac_project(tmp_home, std, config, name="myproj"):
    """Create an AC project with a marker file, return (proj, project_dir)."""
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "code.py").write_text("print('hello')")
    proj = resolve_project(std, config, project_dir=str(project_dir), initialize=True)
    (proj.metadata_path / "marker.txt").write_text("ac-marker")
    (proj.shell_path / "custom.sh").write_text("echo hello")
    return proj, project_dir


def _make_decentral_project(tmp_home, std, config, name="myproj"):
    """Create a decentralized project with a marker file, return (proj, project_dir)."""
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "code.py").write_text("print('dec')")
    proj = resolve_decentralized_project(
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

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "myws" in out
        assert "cool-app" in out

    def test_list_mixed_ac_and_workset(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        # AC project
        ac_dir = tmp_home / "ac_proj"
        ac_dir.mkdir()
        resolve_project(std, config, project_dir=str(ac_dir), initialize=True)

        # Workset project
        ws, _ = _make_workset(tmp_home, std, "mixed-ws")
        source = tmp_home / "ws_src"
        source.mkdir()
        add_project(ws, "ws-proj", source)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "NAME" in out  # AC table header
        assert str(ac_dir) in out
        assert "mixed-ws" in out
        assert "ws-proj" in out

    def test_list_workset_missing_workspace(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)

        ws, _ = _make_workset(tmp_home, std, "miss-ws")
        source = tmp_home / "miss_src"
        source.mkdir()
        add_project(ws, "miss-proj", source)
        # Remove the workspace dir
        shutil.rmtree(ws.workspaces_dir / "miss-proj")

        args = argparse.Namespace()
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

        args = argparse.Namespace()
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

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "Warning" in err


# ---------------------------------------------------------------------------
# TestBoxConvertToWorkset
# ---------------------------------------------------------------------------

class TestBoxConvertToWorkset:
    def _convert_args(self, project_path=None, workset=None, project_name=None,
                       in_place=False, force=True):
        return argparse.Namespace(
            old_path=str(project_path) if project_path else None,
            new_path=None,
            to_mode="workset",
            force=force,
            workset=workset,
            project_name=project_name,
            in_place=in_place,
        )

    def test_convert_ac_to_workset(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "conv_ac_ws")
        ws, _ = _make_workset(tmp_home, std, "target-ws")

        args = self._convert_args(project_dir, workset="target-ws")
        rc = run_migrate(args)
        assert rc == 0

        # Settings in workset
        assert (ws.projects_dir / "conv_ac_ws" / "marker.txt").read_text() == "ac-marker"
        # Home in workset
        assert (ws.projects_dir / "conv_ac_ws" / "shell" / "custom.sh").read_text() == "echo hello"
        # Workspace moved
        assert (ws.workspaces_dir / "conv_ac_ws" / "code.py").read_text() == "print('hello')"
        # Old AC data gone
        assert not proj.metadata_path.exists()

    def test_convert_decentralized_to_workset(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_decentral_project(tmp_home, std, config, "conv_dec_ws")
        ws, _ = _make_workset(tmp_home, std, "dec-ws")

        args = self._convert_args(project_dir, workset="dec-ws")
        rc = run_migrate(args)
        assert rc == 0

        # Settings in workset
        assert (ws.projects_dir / "conv_dec_ws" / "marker.txt").read_text() == "dec-marker"
        # Home in workset
        assert (ws.projects_dir / "conv_dec_ws" / "shell" / "custom.sh").read_text() == "echo dec"
        # Old decentralized data gone
        assert not (project_dir / ".kanibako").exists()
        assert not (project_dir / ".kanibako" / "shell").exists()

    def test_convert_to_workset_in_place(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "conv_ip")
        ws, _ = _make_workset(tmp_home, std, "ip-ws")

        args = self._convert_args(project_dir, workset="ip-ws", in_place=True)
        rc = run_migrate(args)
        assert rc == 0

        # Workspace stays at original location → workset workspace dir is empty
        ws_workspace = ws.workspaces_dir / "conv_ip"
        assert not any(ws_workspace.iterdir())
        # Original workspace still has files
        assert (project_dir / "code.py").read_text() == "print('hello')"

    def test_convert_to_workset_requires_workset_flag(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        project_dir = tmp_home / "conv_noflag"
        project_dir.mkdir()

        args = self._convert_args(project_dir, workset=None)
        rc = run_migrate(args)
        assert rc == 1

    def test_convert_to_workset_nonexistent_workset(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        _, project_dir = _make_ac_project(tmp_home, std, config, "conv_noexist")

        args = self._convert_args(project_dir, workset="nonexistent")
        rc = run_migrate(args)
        assert rc == 1

    def test_convert_to_workset_name_collision(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        _, project_dir = _make_ac_project(tmp_home, std, config, "conv_collision")
        ws, _ = _make_workset(tmp_home, std, "coll-ws")
        # Pre-register a project with the same name
        source = tmp_home / "coll_src"
        source.mkdir()
        add_project(ws, "conv_collision", source)

        args = self._convert_args(project_dir, workset="coll-ws")
        rc = run_migrate(args)
        assert rc == 1

    def test_convert_to_workset_custom_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "conv_custom")
        ws, _ = _make_workset(tmp_home, std, "custom-ws")

        args = self._convert_args(project_dir, workset="custom-ws", project_name="my-fancy-name")
        rc = run_migrate(args)
        assert rc == 0

        # Uses custom name, not directory basename
        assert (ws.projects_dir / "my-fancy-name").is_dir()
        assert (ws.projects_dir / "my-fancy-name" / "marker.txt").read_text() == "ac-marker"

    def test_convert_to_workset_preserves_settings(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "conv_pres")
        ws, _ = _make_workset(tmp_home, std, "pres-ws")

        args = self._convert_args(project_dir, workset="pres-ws")
        rc = run_migrate(args)
        assert rc == 0
        assert (ws.projects_dir / "conv_pres" / "marker.txt").read_text() == "ac-marker"

    def test_convert_to_workset_preserves_workspace(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        _, project_dir = _make_ac_project(tmp_home, std, config, "conv_ws_files")
        ws, _ = _make_workset(tmp_home, std, "wfiles-ws")

        args = self._convert_args(project_dir, workset="wfiles-ws")
        rc = run_migrate(args)
        assert rc == 0
        assert (ws.workspaces_dir / "conv_ws_files" / "code.py").read_text() == "print('hello')"

    def test_convert_to_workset_excludes_lock(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "conv_lock")
        (proj.metadata_path / ".kanibako.lock").touch()
        ws, _ = _make_workset(tmp_home, std, "lock-ws")

        args = self._convert_args(project_dir, workset="lock-ws", force=True)
        rc = run_migrate(args)
        assert rc == 0
        assert not (ws.projects_dir / "conv_lock" / ".kanibako.lock").exists()


# ---------------------------------------------------------------------------
# TestBoxConvertFromWorkset
# ---------------------------------------------------------------------------

class TestBoxConvertFromWorkset:
    def _make_workset_proj(self, tmp_home, std, config, ws_name="from-ws", proj_name="ws-proj"):
        """Create a workset with an initialized project, return (ws, proj)."""
        ws, _ = _make_workset(tmp_home, std, ws_name)
        source = tmp_home / f"{proj_name}_src"
        source.mkdir()
        add_project(ws, proj_name, source)
        proj = resolve_workset_project(ws, proj_name, std, config, initialize=True)
        (proj.metadata_path / "marker.txt").write_text("ws-marker")
        (proj.shell_path / "custom.sh").write_text("echo ws")
        # Put some content in workspace
        (ws.workspaces_dir / proj_name / "code.py").write_text("print('ws')")
        return ws, proj

    def _convert_args(self, project_path, to_mode, force=True, in_place=False):
        return argparse.Namespace(
            old_path=str(project_path),
            new_path=None,
            to_mode=to_mode,
            force=force,
            workset=None,
            project_name=None,
            in_place=in_place,
        )

    def test_convert_workset_to_ac(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config)
        workspace_path = ws.workspaces_dir / "ws-proj"

        args = self._convert_args(workspace_path, "account-centric")
        rc = run_migrate(args)
        assert rc == 0

        # AC layout at the source_path recorded in the workset project
        projects_base = std.data_path / "boxes"
        ac_project = projects_base / "ws-proj_src"
        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "ws-marker"
        # No breadcrumb file (workspace stored in project.toml).
        assert not (ac_project / "project-path.txt").exists()

    def test_convert_workset_to_decentralized(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "from-dec-ws", "dec-proj")
        workspace_path = ws.workspaces_dir / "dec-proj"
        source = tmp_home / "dec-proj_src"

        args = self._convert_args(workspace_path, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        # Decentralized layout at source_path
        assert (source / ".kanibako").is_dir()
        assert (source / ".kanibako" / "marker.txt").read_text() == "ws-marker"
        assert (source / ".gitignore").exists()

    def test_convert_workset_preserves_settings(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "pres-ws2", "pres-proj")
        workspace_path = ws.workspaces_dir / "pres-proj"

        args = self._convert_args(workspace_path, "decentralized")
        rc = run_migrate(args)
        assert rc == 0

        dest = tmp_home / "pres-proj_src"
        assert (dest / ".kanibako" / "marker.txt").read_text() == "ws-marker"

    def test_convert_workset_excludes_lock(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "lock-ws2", "lock-proj")
        (proj.metadata_path / ".kanibako.lock").touch()
        workspace_path = ws.workspaces_dir / "lock-proj"

        args = self._convert_args(workspace_path, "decentralized", force=True)
        rc = run_migrate(args)
        assert rc == 0

        dest = tmp_home / "lock-proj_src"
        assert not (dest / ".kanibako" / ".kanibako.lock").exists()

    def test_convert_workset_removes_registration(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_migrate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "unreg-ws", "unreg-proj")
        workspace_path = ws.workspaces_dir / "unreg-proj"

        args = self._convert_args(workspace_path, "account-centric")
        rc = run_migrate(args)
        assert rc == 0

        # Project should be removed from workset
        ws_reloaded = load_workset(ws.root)
        assert not any(p.name == "unreg-proj" for p in ws_reloaded.projects)


# ---------------------------------------------------------------------------
# TestBoxDuplicateToWorkset
# ---------------------------------------------------------------------------

class TestBoxDuplicateToWorkset:
    def _make_args(self, source, dest, workset=None, project_name=None,
                    bare=False, force=True):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            to_mode="workset", bare=bare, force=force,
            workset=workset, project_name=project_name,
        )

    def test_duplicate_ac_to_workset(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "dup_ac_src")
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
        proj, project_dir = _make_ac_project(tmp_home, std, config, "dup_bare_src")
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
        _, project_dir = _make_ac_project(tmp_home, std, config, "dup_noflag_src")

        args = self._make_args(project_dir, tmp_home / "unused")
        rc = run_duplicate(args)
        assert rc == 1

    def test_duplicate_to_workset_preserves_source(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        proj, project_dir = _make_ac_project(tmp_home, std, config, "dup_pres_src")
        ws, _ = _make_workset(tmp_home, std, "pres-dup-ws")

        args = self._make_args(project_dir, tmp_home / "unused", workset="pres-dup-ws")
        rc = run_duplicate(args)
        assert rc == 0

        # Source untouched
        assert proj.metadata_path.is_dir()
        assert (project_dir / "code.py").read_text() == "print('hello')"
        assert (proj.metadata_path / "marker.txt").read_text() == "ac-marker"


# ---------------------------------------------------------------------------
# TestBoxDuplicateFromWorkset
# ---------------------------------------------------------------------------

class TestBoxDuplicateFromWorkset:
    def _make_workset_proj(self, tmp_home, std, config, ws_name="dfrom-ws", proj_name="ws-proj"):
        ws, _ = _make_workset(tmp_home, std, ws_name)
        source = tmp_home / f"{proj_name}_src"
        source.mkdir()
        add_project(ws, proj_name, source)
        proj = resolve_workset_project(ws, proj_name, std, config, initialize=True)
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

    def test_duplicate_workset_to_ac(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config)
        workspace_path = ws.workspaces_dir / "ws-proj"
        dest = tmp_home / "dup_ws_ac_dst"

        args = self._make_args(workspace_path, dest, "account-centric")
        rc = run_duplicate(args)
        assert rc == 0

        # AC layout at destination
        projects_base = std.data_path / "boxes"
        ac_project = projects_base / "dup_ws_ac_dst"
        assert ac_project.is_dir()
        assert (ac_project / "marker.txt").read_text() == "ws-dup-marker"
        assert (dest / "code.py").read_text() == "print('ws-dup')"

    def test_duplicate_workset_to_decentralized(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-dec", "dec-proj")
        workspace_path = ws.workspaces_dir / "dec-proj"
        dest = tmp_home / "dup_ws_dec_dst"

        args = self._make_args(workspace_path, dest, "decentralized")
        rc = run_duplicate(args)
        assert rc == 0

        assert (dest / ".kanibako").is_dir()
        assert (dest / ".kanibako" / "marker.txt").read_text() == "ws-dup-marker"
        assert (dest / "code.py").read_text() == "print('ws-dup')"

    def test_duplicate_workset_bare(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-bare", "bare-proj")
        workspace_path = ws.workspaces_dir / "bare-proj"
        dest = tmp_home / "dup_ws_bare_dst"

        args = self._make_args(workspace_path, dest, "decentralized", bare=True)
        rc = run_duplicate(args)
        assert rc == 0

        # Metadata exists but workspace not copied
        assert (dest / ".kanibako" / "marker.txt").read_text() == "ws-dup-marker"
        assert not (dest / "code.py").exists()

    def test_duplicate_workset_preserves_source(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, proj = self._make_workset_proj(tmp_home, std, config, "dfrom-pres", "pres-proj")
        workspace_path = ws.workspaces_dir / "pres-proj"
        dest = tmp_home / "dup_ws_pres_dst"

        args = self._make_args(workspace_path, dest, "account-centric")
        rc = run_duplicate(args)
        assert rc == 0

        # Source untouched
        assert proj.metadata_path.is_dir()
        assert (proj.metadata_path / "marker.txt").read_text() == "ws-dup-marker"
        assert (workspace_path / "code.py").read_text() == "print('ws-dup')"
