"""Tests for kanibako.commands.box (list, info, and duplicate subcommands).

Lifecycle commands (remap / move / convert) are covered in
``test_lifecycle.py`` (engine) and ``test_lifecycle_cmd.py`` (CLI wrappers)."""

from __future__ import annotations

import argparse
import shutil
from unittest.mock import patch


from kanibako.settings.config import load_config
from kanibako.settings.paths import WorksetSpec, load_std_paths, resolve_standalone_project, resolve_project, resolve_workset_project
from kanibako.project.workset import add_project, create_workset, load_workset


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

    def _park_deregistered(self, std, tmp_home, name="gonebox"):
        """Create a deregistered box with a retained home dir under std.boxes."""
        from kanibako.project import registry_store

        meta = std.boxes / name
        meta.mkdir(parents=True)
        registry_store.register_deregistered(
            std.registry, name, kind="primary",
            workspace=str(tmp_home / name), metadata=str(meta),
        )
        return meta

    def test_list_shows_deregistered(self, config_file, tmp_home, credentials_dir, capsys):
        """box list surfaces a deregistered box + its register/purge recovery hint."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        self._park_deregistered(std, tmp_home)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deregistered boxes" in out
        assert "gonebox" in out
        assert "deregistered" in out  # the STATUS marker
        # Recovery is discoverable from the listing itself.
        assert "register" in out and "--purge" in out

    def test_list_without_deregistered_omits_section(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """A tree with NO deregistered boxes shows no deregistered section
        (the surfacing is additive — output otherwise unchanged)."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        resolve_project(std, config, project_dir=str(proj_dir), initialize=True)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "proj" in out
        assert "Deregistered" not in out

    def test_list_active_hides_deregistered(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """--active (and ps) never surface deregistered boxes — they aren't active."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        self._park_deregistered(std, tmp_home)

        args = argparse.Namespace(
            show_all=False, orphan=False, quiet=False, active=True,
        )
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Deregistered" not in out

    def test_list_quiet_deregistered_names_only(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Quiet mode lists a deregistered box's bare name (no header)."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        self._park_deregistered(std, tmp_home)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "gonebox" in out
        assert "Deregistered" not in out

    def test_list_only_deregistered_not_no_known_projects(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """A tree whose only records are deregistered still lists them (not the
        'No known projects.' early-out)."""
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        self._park_deregistered(std, tmp_home)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No known projects" not in out
        assert "gonebox" in out

    def test_list_name_column_widens_for_long_names(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """NAME column grows so a long name doesn't overflow (BUG-A cosmetic).

        A short name's row is padded to the LONGEST name's width, so its STATUS
        column aligns.  Mutation proof: reverting the width to the fixed ``<18``
        pads ``short`` to 18 (not the long width), reddening the assertion.
        """
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        long_name = "ai-java-course-materials"  # 24 chars > 18
        for base in ("short", long_name):
            d = tmp_home / base
            d.mkdir()
            resolve_project(std, config, project_dir=str(d), initialize=True)

        args = argparse.Namespace(show_all=True, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # name_width == len(long_name) == 24; the short row is ljust-padded to it.
        assert "short".ljust(len(long_name)) in out

    def test_list_dedups_identical_workset_rows(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Duplicate (name, path) workset rows collapse to one (BUG-A).

        Simulates the observed duplicate-membership state by duplicating the
        per-workset project entry ``iter_workset_projects`` yields; the printed
        output must show the member exactly once.  Mutation proof: removing the
        ``seen_rows`` dedup prints the name twice.
        """
        from kanibako.commands.box import run_list
        from kanibako.settings.paths import iter_workset_projects
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("cluster", tmp_home / "worksets" / "cluster", std)
        source = tmp_home / "cluster2-src"
        source.mkdir()
        add_project(ws, "cluster2", source)

        real = iter_workset_projects(std, config)
        # Duplicate every project entry within each workset (the dup-mint shape).
        dup = [(wn, w, plist + plist) for wn, w, plist in real]

        args = argparse.Namespace(show_all=True, orphan=False, quiet=False)
        with patch(
            "kanibako.commands.box._parser.iter_workset_projects",
            return_value=dup,
        ):
            rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        # The member name appears exactly once (the source path does not contain
        # the box name, so counting the name counts rows).
        assert out.count("cluster2 ") == 1


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
        """A BARE duplicate whose destination workspace is ALREADY a registered
        box refuses (one box per workspace path — the PRIMARY-membership Bug-A
        guard).  Since the global ``projects:`` section retired, the membership is
        the sole store and cannot hold a second box name for one workspace; the
        old global store minted ``fw_dst2`` for the same dir (the duplicate-``list``-
        rows bug).  ``--force`` overwrites metadata dirs, NOT the one-box invariant."""
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
        # Refused: dst_dir is already box "fw_dst"; a bare duplicate there would be
        # a second box for one workspace.
        assert rc == 1
        # No stray fw_dst2 box was minted.
        assert not (std.boxes / "fw_dst2").exists()

    def test_duplicate_force_refuses_deregistered_home(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """--force must NOT overwrite a std.boxes/<name> home occupied by a
        DEREGISTERED box (same I4 data-loss class as create).  Refuse with
        register/purge guidance; the retained home is untouched; no active
        membership is stranded for the refused name."""
        from kanibako.project import registry_store
        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        # Source primary box.
        src_dir = tmp_home / "src_box"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        # A deregistered box 'target' with a retained home + sentinel.
        home = std.boxes / "target"
        home.mkdir(parents=True)
        (home / "keep.txt").write_text("precious")
        registry_store.register_deregistered(
            std.registry, "target", kind="primary",
            workspace=str(tmp_home / "target"), metadata=str(home),
        )

        # Duplicating to a NEW path whose basename mints the name 'target' lands
        # on that deregistered home.
        dst_dir = tmp_home / "target"
        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 1
        # Home NOT overwritten — sentinel intact.
        assert (home / "keep.txt").read_text() == "precious"
        # No active membership stranded for the refused name.
        assert "target" not in load_primary_boxes(std.primary_workset)
        # Guidance is surfaced.
        err = capsys.readouterr().err
        assert "register" in err and "purge" in err

    def test_duplicate_force_refuses_orphaned_home(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """--force also refuses an ORPHANED std.boxes/<name> home (no active or
        deregistered registration) rather than clobbering it."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "src2"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        orphan = std.boxes / "orphan"
        orphan.mkdir(parents=True)
        (orphan / "keep.txt").write_text("precious")

        dst_dir = tmp_home / "orphan"
        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 1
        assert (orphan / "keep.txt").read_text() == "precious"

    def test_duplicate_fresh_name_unaffected_by_guard(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A genuinely-fresh dup name (no deregistered/orphaned home) still
        duplicates — the guard is a no-op for it."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "fresh_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("x = 1")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        # A deregistered box exists but under a DIFFERENT name, so the minted
        # 'fresh_dst' home is free.
        (std.boxes / "someoldbox").mkdir(parents=True)
        from kanibako.project import registry_store
        registry_store.register_deregistered(
            std.registry, "someoldbox", kind="primary",
            workspace=str(tmp_home / "someoldbox"),
            metadata=str(std.boxes / "someoldbox"),
        )

        dst_dir = tmp_home / "fresh_dst"
        rc = run_duplicate(self._make_args(src_dir, dst_dir, force=True))
        assert rc == 0
        assert (std.boxes / "fresh_dst").is_dir()

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
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "orphan_src"
        src_dir.mkdir()
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "orphan_dst"

        names_before = load_primary_boxes(std.primary_workset)

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
        names_after = load_primary_boxes(std.primary_workset)
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

    def test_info_by_box_name(self, config_file, tmp_home, credentials_dir, capsys):
        """`box info --box <registered-name>` resolves by NAME, not as a path.

        Regression: run_info used to do .is_dir() on the raw --box value before
        the path-or-name resolver, so a bare box name failed with "directory
        does not exist".
        """
        from kanibako.commands.box import run_info

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "named_sa"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        box_name = proj.name

        # --box carries a bare NAME (no path separator); positional path absent.
        args = argparse.Namespace(path=None, box=box_name)
        with patch(
            "kanibako.commands.box._parser._check_container_running",
            return_value=(False, "not running (kanibako-test)"),
        ):
            rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "standalone" in out
        # Resolved to the box's real root, not cwd/<name>.
        assert str(root) in out

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

        # Destination should have standalone layout: box_data/ marker dir holds
        # session data; settings.yaml at the ROOT (drift I); the workspace files
        # land in the workspace/ subdir (drift H).
        assert (dst_dir / "box_data").is_dir()
        assert (dst_dir / "box_data" / "marker.txt").read_text() == "ac-data"
        assert (dst_dir / "settings.yaml").is_file()
        assert not (dst_dir / "box_data" / "settings.yaml").exists()
        assert (dst_dir / "workspace" / "code.py").read_text() == "print('hello')"
        # No breadcrumb in standalone.
        assert not (dst_dir / "box_data" / "project-path.txt").exists()

    def test_duplicate_standalone_to_local(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "dup_dec_src"
        src_dir.mkdir()
        proj = resolve_standalone_project(
            std, config, project_dir=str(src_dir), initialize=True,
        )
        # Workspace files live in the workspace/ subdir; session data (marker)
        # lives in the box_data/ marker dir (drift H+I).
        (proj.project_path / "code.py").write_text("print('dec')")
        (proj.shell_path.parent / "marker.txt").write_text("dec-data")

        dst_dir = tmp_home / "dup_dec_dst"

        args = self._make_args(src_dir, dst_dir, "default")
        rc = run_duplicate(args)
        assert rc == 0

        # Destination should have local layout (box metadata under boxes/<name>,
        # workspace files at the destination root).
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
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "tlfail_src"
        src_dir.mkdir()
        proj = resolve_standalone_project(
            std, config, project_dir=str(src_dir), initialize=True,
        )
        (proj.metadata_path / "marker.txt").write_text("data")

        dst_dir = tmp_home / "tlfail_dst"

        names_before = load_primary_boxes(std.primary_workset)

        # bare=True isolates the metadata copytree inside _duplicate_to_local.
        with patch(
            "kanibako.commands.box._duplicate.shutil.copytree",
            side_effect=RuntimeError("boom"),
        ):
            try:
                run_duplicate(self._make_args(src_dir, dst_dir, "default", bare=True))
            except RuntimeError:
                pass

        names_after = load_primary_boxes(std.primary_workset)
        assert names_after == names_before
        assert "tlfail_dst" not in names_after
        assert not (std.boxes / "tlfail_dst").exists()

    def test_duplicate_cross_mode_to_registered_primary_refuses_cleanly(
        self, config_file, tmp_home, credentials_dir,
    ):
        """F2: a cross-mode ``--to-mode primary`` onto a dest workspace that is
        ALREADY a registered primary box hits Guard 1 (one box per workspace
        path).  The refusal must be CLEAN (rc=1, no traceback), leave the dest
        box's registration intact, and mint no stray box dir.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "xdup_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('src')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        # Dest is ALREADY a registered primary box (its workspace is a member).
        dst_dir = tmp_home / "xdup_dst"
        dst_dir.mkdir()
        resolve_project(std, config, project_dir=str(dst_dir), initialize=True)

        names_before = load_primary_boxes(std.primary_workset)

        rc = run_duplicate(
            self._make_args(src_dir, dst_dir, "default", force=True)
        )
        assert rc == 1

        # Dest box's pre-existing registration is intact; no second name minted.
        assert load_primary_boxes(std.primary_workset) == names_before
        assert not (std.boxes / "xdup_dst2").exists()

    def test_duplicate_cross_mode_to_orphan_registered_dest_rolls_back_copy(
        self, config_file, tmp_home, credentials_dir,
    ):
        """F2 rollback branch: the dest workspace is REGISTERED but its dir is
        ABSENT (orphan), so the cross-mode copytree CREATES ``new_path`` before
        Guard 1 refuses.  The rollback (gated on ``new_path_existed``) must
        remove the just-created workspace copy — no stranded partial state.
        """
        import shutil

        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "odup_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('src')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        # Register the dest, then orphan it: membership survives, dir gone.
        dst_dir = tmp_home / "odup_dst"
        dst_dir.mkdir()
        resolve_project(std, config, project_dir=str(dst_dir), initialize=True)
        shutil.rmtree(dst_dir)

        names_before = load_primary_boxes(std.primary_workset)

        rc = run_duplicate(self._make_args(src_dir, dst_dir, "default"))
        assert rc == 1

        # The copy this call created was rolled back; registration intact.
        assert not dst_dir.exists()
        assert load_primary_boxes(std.primary_workset) == names_before

    def test_duplicate_cross_mode_oserror_mid_copy_rolls_back_clean(
        self, config_file, tmp_home, credentials_dir,
    ):
        """F-3: an OSError mid workspace-copy on a FRESH dest is caught (rc=1, no
        traceback) and the partial dir THIS call created is rolled back.

        Pre-fix the workspace copytree ran BEFORE (and outside) the only catch,
        which caught ProjectError alone — so an OSError propagated UNCAUGHT and
        left the partial ``new_path`` behind.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "oserr_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('src')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "oserr_dst"  # fresh: absent + unregistered
        names_before = load_primary_boxes(std.primary_workset)

        def _copytree_oserror(src, dst, *a, **kw):
            # Materialize the destination dir, then fail — mimics a copy that
            # dies partway and strands residue.
            from pathlib import Path
            Path(dst).mkdir(parents=True, exist_ok=True)
            raise OSError("disk full")

        with patch(
            "kanibako.commands.box._duplicate.shutil.copytree",
            side_effect=_copytree_oserror,
        ):
            rc = run_duplicate(self._make_args(src_dir, dst_dir, "default"))

        assert rc == 1
        # No stray dir created by this call; no orphan name registered.
        assert not dst_dir.exists()
        assert load_primary_boxes(std.primary_workset) == names_before
        assert not (std.boxes / "oserr_dst").exists()

    def test_duplicate_cross_mode_registered_dest_no_force_refuses_clean(
        self, config_file, tmp_home, credentials_dir,
    ):
        """F-3 (guard-before-copy): a --to primary onto an ALREADY-registered
        primary workspace refuses CLEANLY (rc=1) even WITHOUT --force.

        Pre-fix, without --force the workspace copytree (dirs_exist_ok=False) ran
        first and raised FileExistsError (an OSError) UNCAUGHT — a traceback
        instead of the Guard-1 refusal.  The up-front guard now refuses before any
        copy/prompt.
        """
        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "regdst_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('src')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        dst_dir = tmp_home / "regdst_dst"
        dst_dir.mkdir()
        (dst_dir / "keep.txt").write_text("preexisting")
        resolve_project(std, config, project_dir=str(dst_dir), initialize=True)

        names_before = load_primary_boxes(std.primary_workset)

        # No --force → pre-fix would prompt then hit copytree's FileExistsError;
        # mock the prompt so the pre-fix path would reach the copy (post-fix the
        # guard refuses before the prompt, so this mock is simply unused).
        with patch(
            "kanibako.commands.box._duplicate.confirm_prompt",
            return_value=None,
        ):
            rc = run_duplicate(
                self._make_args(src_dir, dst_dir, "default", force=False)
            )

        assert rc == 1
        # Pre-existing registration + dir/content untouched; no stray box dir.
        assert load_primary_boxes(std.primary_workset) == names_before
        assert (dst_dir / "keep.txt").read_text() == "preexisting"
        assert not (std.boxes / "regdst_dst2").exists()

    def test_duplicate_cross_mode_preexisting_dir_no_force_friendly_msg(
        self, config_file, tmp_home, credentials_dir,
    ):
        """F-3 (NIT): duplicating onto a pre-existing UNREGISTERED dir without
        --force refuses with the friendly destination-exists message, NOT the raw
        ``[Errno 17] File exists`` errno from copytree's FileExistsError.

        The dest dir exists on disk but is NOT a registered primary box, so the
        up-front guard-before-copy does not fire; copytree(dirs_exist_ok=False)
        raises FileExistsError, which the caught-and-translated branch now renders
        as the run_duplicate-style message.  rc=1, dir untouched, no deletion.
        """
        import io
        from contextlib import redirect_stderr

        from kanibako.commands.box import run_duplicate
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "friendly_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('src')")
        resolve_project(std, config, project_dir=str(src_dir), initialize=True)

        # Pre-existing but UNREGISTERED destination dir (not a kanibako box).
        dst_dir = tmp_home / "friendly_dst"
        dst_dir.mkdir()
        (dst_dir / "keep.txt").write_text("preexisting")
        names_before = load_primary_boxes(std.primary_workset)

        # Unregistered dest → the guard-before-copy does NOT fire, so the confirm
        # prompt is reached (no --force); accept it so the copy runs and hits the
        # FileExistsError → friendly refusal.
        buf = io.StringIO()
        with redirect_stderr(buf), patch(
            "kanibako.commands.box._duplicate.confirm_prompt",
            return_value=None,
        ):
            rc = run_duplicate(
                self._make_args(src_dir, dst_dir, "default", force=False)
            )

        assert rc == 1
        err = buf.getvalue()
        # Friendly message (matches run_duplicate's non-cross-mode style); NOT the
        # raw errno traceback text.
        assert "destination already exists" in err
        assert "--force" in err
        assert "Errno 17" not in err
        # No deletion of the pre-existing dir / content; registry untouched.
        assert (dst_dir / "keep.txt").read_text() == "preexisting"
        assert load_primary_boxes(std.primary_workset) == names_before

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

        assert not (dst_dir / "box_data" / ".kanibako.lock").exists()

    def test_duplicate_primary_to_standalone_is_detectable_and_registered(
        self, config_file, tmp_home, credentials_dir,
    ):
        """BUG#3: duplicating a PRIMARY box --standalone must ESTABLISH a real
        standalone box — detected as standalone, registered in
        ``registry.standalone``, ``mode=standalone``, with a FRESH
        ``<kuid>_<leaf>`` name distinct from the source (which is unregistered
        as a standalone, since primary boxes use ``names.yaml``)."""
        from kanibako.commands.box import run_duplicate
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import BoxMode, detect_project_mode
        from kanibako.project.registry_store import load_standalone

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "b3_src"
        src_dir.mkdir()
        (src_dir / "code.py").write_text("print('b3')")
        src_proj = resolve_project(std, config, project_dir=str(src_dir), initialize=True)
        src_name = src_proj.name

        dst_dir = tmp_home / "b3_dst"

        args = self._make_args(src_dir, dst_dir, "standalone")
        rc = run_duplicate(args)
        assert rc == 0

        # (1) Detected as standalone via the on-disk box_data/ marker.
        result = detect_project_mode(dst_dir, std, config)
        assert result.mode == BoxMode.standalone

        # (2) P8b/Option A: no on-disk ``project:`` identity — the marker
        #     settings.yaml lives at the ROOT (drift I) and is materialized by the
        #     sparse kuid write; no ``project:`` section on disk.
        from kanibako.settings.config import read_workset_kuid
        from kanibako.kuid import SENTINEL
        assert "project" not in load_doc(dst_dir / BOX_META_FILE)
        assert (dst_dir / BOX_META_FILE).is_file()
        assert read_workset_kuid(dst_dir / BOX_META_FILE) != SENTINEL

        # (3) Registered in registry.standalone keyed by a fresh <kuid>_<leaf>
        #     name → dst root (NOT the source's name).
        standalone = load_standalone(std.registry)
        matches = [n for n, root in standalone.items() if root == str(dst_dir)]
        assert len(matches) == 1
        new_name = matches[0]
        assert new_name != src_name
        # <kuid>_<leaf>: 5-char Crockford base32 prefix + "_" + sanitized leaf.
        prefix, _, leaf = new_name.partition("_")
        assert len(prefix) == 5
        assert leaf == "b3_dst"

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


def _connected_index(std):
    """Reconstruct the ``{external_path: {workset, project}}`` connection view.

    The D10 replacement for the retired global ``connected:`` index: a connected
    box is a NAMED workset's per-workset ``boxes:`` entry whose path is EXTERNAL
    (outside that workset root).  Mirrors the old ``_load_connected`` return shape
    so equivalence assertions stay legible.
    """
    from pathlib import Path

    from kanibako.project import registry_store, workset_registry
    from kanibako.settings.config_io import load_doc

    out: dict[str, dict] = {}
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, load_doc(root / "settings.yaml"),
        )
        for box_name, box_path in workset_registry.load_workset_boxes(
            registry_path
        ).items():
            resolved = Path(box_path).resolve()
            try:
                resolved.relative_to(root.resolve())
                continue  # in-tree box: not an external connection
            except ValueError:
                out[str(resolved)] = {"workset": name, "project": box_name}
    return out


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
        from kanibako.settings.paths import BoxMode

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
        assert (ws.projects_dir / "int_src" / "home" / "custom.sh").exists()
        # No external wiring — duplicate is a copy, not a connection.
        assert _connected_index(std) == {}

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
        from kanibako.settings.paths import BoxMode
        from kanibako.project.workset import list_worksets

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

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext-ws2", proj_name="ext-proj2",
        )
        before = _connected_index(std)

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=True, force=True,
            workset="ext-ws2", project_name="dup-proj",
        )
        rc = run_duplicate(args)
        assert rc == 1
        assert "external-connected" in capsys.readouterr().err
        # No state changed by the refusal.
        assert _connected_index(std) == before
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
        assert _connected_index(std) == {}

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

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext2def-ws", proj_name="e2d",
        )
        before = _connected_index(std)
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
        assert _connected_index(std) == before
        assert (ws.projects_dir / proj_name).is_dir()

    def test_duplicate_external_to_standalone(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--to standalone`` of an external-connected source succeeds."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="ext2sa-ws", proj_name="e2s",
        )
        before = _connected_index(std)
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
        assert _connected_index(std) == before

    def test_bare_duplicate_external_to_default_allowed(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``--bare --to default`` of an external source succeeds (no aliasing).

        The bare result makes ``new_path`` itself the workspace, so the 1:1
        connected.yaml refusal does NOT apply.  Metadata only, no crash, no
        WorksetError.
        """
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="extbare-ws", proj_name="eb",
        )
        before = _connected_index(std)
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
        assert _connected_index(std) == before

    def test_bare_duplicate_external_to_workset_still_refused(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The narrowed guard still refuses ``--bare --to workset`` (aliasing case)."""
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)
        ws, ext_dir, proj_name = self._make_external_connected(
            tmp_home, std, config, ws_name="extbarews-ws", proj_name="ebw",
        )
        before = _connected_index(std)

        args = argparse.Namespace(
            source_path=str(ext_dir), new_path=str(tmp_home / "unused"),
            to_mode="workset", bare=True, force=True,
            workset="extbarews-ws", project_name="dup-proj",
        )
        rc = run_duplicate(args)
        assert rc == 1
        assert "external-connected" in capsys.readouterr().err
        assert _connected_index(std) == before


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


class TestBoxDuplicateNoToMode:
    """Bare `box duplicate <src> <dst>` (no --to) for non-primary sources (BUG-B)."""

    def _make_args(self, source, dest, bare=False, force=True):
        return argparse.Namespace(
            source_path=str(source), new_path=str(dest),
            bare=bare, force=force, to_mode=None,
            workset=None, project_name=None,
        )

    def test_standalone_source_without_to_duplicates(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A standalone source resolves without --to and lands a fresh standalone."""
        from kanibako.project import registry_store
        from kanibako.commands.box import run_duplicate

        config = load_config(config_file)
        std = load_std_paths(config)

        src_dir = tmp_home / "sa_src"
        src_dir.mkdir()
        src_proj = resolve_standalone_project(
            std, config, str(src_dir), initialize=True,
        )
        # The live workspace is the <root>/workspace subdir (drift H).
        (src_proj.project_path / "code.py").write_text("print('sa')")

        dst_dir = tmp_home / "sa_dst"
        rc = run_duplicate(self._make_args(src_dir, dst_dir))
        assert rc == 0

        # Workspace copied into the dst workspace/ subdir + a fresh standalone
        # box established at the dst root.
        assert (dst_dir / "workspace" / "code.py").read_text() == "print('sa')"
        assert (dst_dir / "box_data").is_dir()
        assert (dst_dir / "settings.yaml").is_file()
        sa = registry_store.load_standalone(std.registry)
        dst_names = [n for n, root in sa.items() if root == str(dst_dir.resolve())]
        assert len(dst_names) == 1
        # Fresh identity (not the source's box name).
        assert dst_names[0] != src_proj.name

    def test_named_source_without_to_duplicates_to_default(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A workset (named) source resolves without --to into a default box."""
        from kanibako.commands.box import run_duplicate
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "wset"
        ws = create_workset("wset", ws_root, std)
        source = tmp_home / "orig"
        source.mkdir()
        add_project(ws, "app", source)
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), "app", std, config, initialize=True,
        )
        (proj.project_path / "code.py").write_text("print('ws')")

        dst_dir = tmp_home / "ws_dst"
        rc = run_duplicate(self._make_args(proj.project_path, dst_dir))
        assert rc == 0
        assert (std.boxes / "ws_dst").is_dir()


class TestBoxRmStandalone:
    """`box rm` for standalone boxes (BUG-C)."""

    def _rm_args(self, target, purge=False, force=True):
        return argparse.Namespace(target=str(target), purge=purge, force=force)

    def _make_standalone(self, std, config, tmp_home, leaf="rm_sa"):
        root = tmp_home / leaf
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        return root, proj.name

    def test_rm_by_name_unregisters(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.project import registry_store
        from kanibako.commands.box import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        root, box_name = self._make_standalone(std, config, tmp_home)

        rc = run_rm(self._rm_args(box_name))
        assert rc == 0
        assert box_name not in registry_store.load_standalone(std.registry)
        # Metadata left in place without --purge.
        assert (root / "box_data").is_dir()

    def test_rm_by_path_unregisters(self, config_file, tmp_home, credentials_dir):
        from kanibako.project import registry_store
        from kanibako.commands.box import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        root, box_name = self._make_standalone(std, config, tmp_home, leaf="rm_sa_path")

        rc = run_rm(self._rm_args(root))
        assert rc == 0
        assert box_name not in registry_store.load_standalone(std.registry)

    def test_rm_purge_removes_metadata(self, config_file, tmp_home, credentials_dir):
        from kanibako.project import registry_store
        from kanibako.commands.box import run_rm

        config = load_config(config_file)
        std = load_std_paths(config)
        root, box_name = self._make_standalone(std, config, tmp_home, leaf="rm_sa_purge")

        rc = run_rm(self._rm_args(box_name, purge=True))
        assert rc == 0
        assert box_name not in registry_store.load_standalone(std.registry)
        assert not (root / "box_data").exists()

    def test_rm_unknown_target_errors(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box import run_rm

        rc = run_rm(self._rm_args("no-such-box"))
        assert rc == 1


class TestBoxListStandalone:
    """`box list` includes standalone boxes (BUG-E)."""

    def test_list_shows_standalone(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa_listed"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Standalone boxes:" in out
        assert proj.name in out
        assert str(root.resolve()) in out

    def test_list_quiet_includes_standalone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa_quiet"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out.splitlines()
        assert proj.name in out

    def test_list_hides_missing_standalone_by_default(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        import shutil

        from kanibako.commands.box import run_list

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa_gone"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        shutil.rmtree(root)

        args = argparse.Namespace(show_all=False, orphan=False, quiet=False)
        rc = run_list(args)
        assert rc == 0
        assert proj.name not in capsys.readouterr().out

        # --all surfaces the missing standalone box.
        args_all = argparse.Namespace(show_all=True, orphan=False, quiet=False)
        rc = run_list(args_all)
        assert rc == 0
        out = capsys.readouterr().out
        assert proj.name in out
        assert "missing" in out


class TestImportPersonaStoreForCreate:
    """The create-side persona-grata trigger (`create --agent <pid>+<hid>`).

    ``_import_persona_store_for_create`` registers + imports the node's
    settings when a store entry exists; bare/plain refs, absent entries, and
    uninstalled harnesses fall through (return None, write nothing) so create
    behaves exactly as today for them.
    """

    _ENDPOINT = "https://api.navigator.example/v1"
    _CODEX_TOML = (
        'model = "gemma4"\n'
        "[model_providers.navigator]\n"
        'name = "navigator"\n'
        'base_url = "https://api.navigator.example/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "NAVIGATOR_API_KEY"\n'
    )

    def _std(self, tmp_home):
        import types

        return types.SimpleNamespace(agents=tmp_home / "data" / "agents")

    def _store(self, tmp_home, *, config=True, pointer=True):
        persona_dir = tmp_home / "config" / "personas" / "navigator"
        (persona_dir / "codex").mkdir(parents=True)
        if config:
            (persona_dir / "codex" / "config.toml").write_text(self._CODEX_TOML)
        if pointer:
            (persona_dir / ".secret_path").write_text("./token\n")
        return persona_dir

    def _call(self, tmp_home, ref, monkeypatch, *, target="real", verdict=True):
        import kanibako.commands.box._parser as parser_mod
        from kanibako.commands.box._parser import _import_persona_store_for_create

        if target == "real":
            from kanibako.plugins.codex.target import CodexTarget

            resolved = CodexTarget()
            # Stub the network probe (instance-level): unit tests never do
            # real I/O; the wire itself is covered in test_persona_settings.
            resolved.verify_persona = (  # type: ignore[method-assign]
                lambda *a, **k: verdict
            )
        else:
            resolved = target
        monkeypatch.setattr(
            parser_mod, "resolve_target", lambda *a, **k: resolved,
        )
        return _import_persona_store_for_create(
            self._std(tmp_home), ref, tmp_home / "project",
        )

    def _settings_path(self, tmp_home):
        return (
            tmp_home / "data" / "agents" / "navigator℘codex" / "settings.yaml"
        )

    def test_bare_ref_falls_through(self, tmp_home, monkeypatch):
        self._store(tmp_home)
        assert self._call(tmp_home, "codex", monkeypatch) is None
        assert not (tmp_home / "data" / "agents").exists()

    def test_persona_without_store_falls_through(self, tmp_home, monkeypatch):
        assert self._call(tmp_home, "navigator+codex", monkeypatch) is None
        assert not (tmp_home / "data" / "agents").exists()

    def test_uninstalled_harness_falls_through(self, tmp_home, monkeypatch):
        self._store(tmp_home)
        assert (
            self._call(tmp_home, "navigator+codex", monkeypatch, target=None)
            is None
        )
        assert not (tmp_home / "data" / "agents").exists()

    def test_store_entry_registers_and_imports(self, tmp_home, monkeypatch, capsys):
        from kanibako.settings.config_io import load_doc

        persona_dir = self._store(tmp_home)
        err = self._call(tmp_home, "navigator+codex", monkeypatch)
        assert err is None
        data = load_doc(self._settings_path(tmp_home))
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert data["self"]["model"] == "gemma4"
        assert data["self"]["secret_path"]["NAVIGATOR_API_KEY"] == str(
            persona_dir / "token"
        )
        assert "Imported persona 'navigator+codex'" in capsys.readouterr().out

    def test_unusable_store_config_is_an_error(self, tmp_home, monkeypatch):
        self._store(tmp_home, config=False)  # entry dir, no config.toml
        err = self._call(tmp_home, "navigator+codex", monkeypatch)
        assert err is not None and err.startswith("Error:")
        assert not self._settings_path(tmp_home).exists()

    def test_malformed_ref_is_an_error(self, tmp_home, monkeypatch):
        err = self._call(tmp_home, "navi/gator+codex", monkeypatch)
        assert err is not None and err.startswith("Error:")

    def test_token_pointer_warning_still_imports(self, tmp_home, monkeypatch, capsys):
        from kanibako.settings.config_io import load_doc

        self._store(tmp_home, pointer=False)
        err = self._call(tmp_home, "navigator+codex", monkeypatch)
        assert err is None
        assert "Warning:" in capsys.readouterr().err
        data = load_doc(self._settings_path(tmp_home))
        assert data["self"]["endpoint"] == self._ENDPOINT
        assert "secret_path" not in data["self"]

    def test_corrupt_existing_settings_is_a_clean_error(self, tmp_home, monkeypatch):
        self._store(tmp_home)
        path = self._settings_path(tmp_home)
        path.parent.mkdir(parents=True)
        path.write_text("self: [unclosed\n")
        err = self._call(tmp_home, "navigator+codex", monkeypatch)
        assert err is not None and "corrupt" in err

    # --- the create-time WARN-ONLY probe (these values' ONE verify: the
    # --- start reconcile probes only on CHANGED store values) ---------------

    def test_verified_import_emits_no_warning(self, tmp_home, monkeypatch, capsys):
        self._store(tmp_home)
        err = self._call(tmp_home, "navigator+codex", monkeypatch, verdict=True)
        assert err is None
        assert "Warning" not in capsys.readouterr().err

    def test_rejected_token_warns_but_still_imports(
        self, tmp_home, monkeypatch, capsys,
    ):
        self._store(tmp_home)
        err = self._call(tmp_home, "navigator+codex", monkeypatch, verdict=False)
        assert err is None  # warn-only: create still succeeds
        assert "rejected the token" in capsys.readouterr().err
        assert self._settings_path(tmp_home).exists()

    def test_unverifiable_endpoint_warns_but_still_imports(
        self, tmp_home, monkeypatch, capsys,
    ):
        self._store(tmp_home)
        err = self._call(tmp_home, "navigator+codex", monkeypatch, verdict=None)
        assert err is None
        assert "could not verify" in capsys.readouterr().err
        assert self._settings_path(tmp_home).exists()

    def test_probe_raise_is_held_to_the_contract(self, tmp_home, monkeypatch, capsys):
        from kanibako.plugins.codex.target import CodexTarget

        self._store(tmp_home)

        def _boom(*a, **k):
            raise RuntimeError("misbehaving plugin probe")

        resolved = CodexTarget()
        resolved.verify_persona = _boom  # type: ignore[method-assign]
        err = self._call(tmp_home, "navigator+codex", monkeypatch, target=resolved)
        assert err is None  # never crashes a create
        assert "could not verify" in capsys.readouterr().err


class TestCreatePersistsAgentSelection:
    """`create --agent <ref>` persists the §2h REQUEST ``pref.system.agent``.

    Real-path ``run_create`` runs: the selection lands in the box settings file
    through the sanctioned settings write, so a PLAIN ``start`` resolves the
    selected agent (system.agent < workset pref < box pref < --agent) instead of
    falling through to the system default.  ``start --agent <other>`` stays an
    ephemeral override (never persisted).

    ⮕ **P7:** the persisted key was ``box.agent_name``, RETIRED by spec §2b — a
    box no longer names its agent with a key of its own; it REQUESTS one at the
    key that resolves earlier.
    """

    def _create(self, tmp_home, *, agent=None):
        import argparse

        from kanibako.commands.box._parser import run_create

        ns = argparse.Namespace(
            path=str(tmp_home / "project"), standalone=False, no_vault=True,
            name=None, image=None, agent=agent, allow_home=False,
        )
        return run_create(ns)

    def _box_settings(self, config_file, tmp_home):
        from kanibako.commands.start import _resolve_existing_box
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import BOX_META_FILE, load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = _resolve_existing_box(std, config, str(tmp_home / "project"))
        assert proj is not None
        return proj.metadata_path / BOX_META_FILE

    def test_create_with_agent_persists_the_pref_request(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.settings.config_io import load_doc

        assert self._create(tmp_home, agent="claude") == 0
        settings = self._box_settings(config_file, tmp_home)
        data = load_doc(settings)
        # The NESTED pref table (never a dotted literal — P6 rejects that
        # spelling, because a bind-shaped request would not be bind-parsed).
        assert data["pref"]["system"]["agent"] == "claude"
        # …and NOT the retired key.
        assert "agent_name" not in data.get("box", {})

    def test_plain_create_writes_no_agent_selection(
        self, config_file, tmp_home, credentials_dir,
    ):
        from kanibako.settings.config_io import load_doc

        assert self._create(tmp_home) == 0
        settings = self._box_settings(config_file, tmp_home)
        data = load_doc(settings)
        assert "agent" not in data.get("pref", {}).get("system", {})
        assert "agent_name" not in data.get("box", {})

    def test_persisted_selection_drives_plain_start_resolution(
        self, config_file, tmp_home, credentials_dir,
    ):
        """⚑ THE CREATE→START ROUND TRIP — the single most important regression
        test of this phase: what ``create --agent`` writes must be what a plain
        ``start`` resolves. INVERT: write the retired ``box.agent_name`` at create
        (or read it at start) and the box silently launches a different agent."""
        from kanibako.settings.config import load_config, resolve_agent
        from kanibako.settings.paths import load_std_paths
        from kanibako.settings.settings_launch import resolve_selected_agent
        from kanibako.settings.agent_select import launch_resolve_ctx
        from kanibako.commands.start import _resolve_existing_box

        assert self._create(tmp_home, agent="claude") == 0
        config = load_config(config_file)
        std = load_std_paths(config)
        proj = _resolve_existing_box(std, config, str(tmp_home / "project"))
        assert proj is not None
        settings = self._box_settings(config_file, tmp_home)
        # ⚑ THE WHOLE SEAM, not a re-implementation of it: ``select_agent`` is what
        # the launch calls, so this drives the create write, the refusal loop, the
        # narrow selection resolve AND the arbiter in one step. (A test that only
        # called ``resolve_selected_agent`` would leave the seam itself unpinned —
        # deleting the refusal loop, or the ``select_agent`` call in start.py, would
        # redden nothing.)
        from kanibako.settings.agent_select import select_agent

        sel = select_agent(std=std, proj=proj, explicit_agent=None)
        assert (sel.node, sel.source) == ("claude", "settings")
        # …and the value the launch installs at the §1A level is that same node.
        assert sel.selection_level == {"system.agent": "claude"}
        # The narrow resolve underneath reads the REQUEST from the box file.
        requested = resolve_selected_agent(
            ctx=launch_resolve_ctx(std, proj, None),
            system_path=std.settings, workset_path=None, box_path=settings,
            valid_agents=frozenset({"claude", "goose"}),
        )
        assert requested == "claude"
        assert resolve_agent(
            explicit_agent=None, requested=str(requested),
            project_path=tmp_home / "project",
        ) == "claude"
        # Ephemeral override on top: explicit wins, nothing re-persisted.
        assert resolve_agent(
            explicit_agent="goose", requested=str(requested),
            project_path=tmp_home / "project",
        ) == "goose"

    def test_persona_ref_is_persisted_raw(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        """A persona selector persists as TYPED (`+` form; the read side
        canonicalizes) — and the persona store import still ran (settings.yaml
        for the node was registered from the store)."""
        from kanibako.settings.config_io import load_doc

        # Lay down a conforming store entry + a real token file so the persona
        # create verdict (codex: usable token + model) passes on the real path.
        persona_dir = tmp_home / "config" / "personas" / "navigator"
        (persona_dir / "codex").mkdir(parents=True)
        (persona_dir / "codex" / "config.toml").write_text(
            'model = "gemma4"\n'
            "[model_providers.navigator]\n"
            'name = "navigator"\n'
            'base_url = "https://api.navigator.example/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "NAVIGATOR_API_KEY"\n'
        )
        (persona_dir / ".secret_path").write_text("./token\n")
        (persona_dir / "token").write_text("sk-test\n")

        assert self._create(tmp_home, agent="navigator+codex") == 0
        settings = self._box_settings(config_file, tmp_home)
        assert load_doc(settings)["pref"]["system"]["agent"] == "navigator+codex"
        # The store import registered the agent node as part of the create.
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        node_settings = load_doc(std.agents / "navigator℘codex" / "settings.yaml")
        assert node_settings["self"]["endpoint"] == "https://api.navigator.example/v1"

    def test_failing_verdict_leaves_no_box_and_no_agent_selection(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """FAILURE-PATH RESIDUE: a create refused by the persona verdict must
        leave NO box (no meta, no ``pref.system.agent`` anywhere).  The verdict
        runs BEFORE box materialisation and before the agent-selection write —
        pin that ordering.  The agents/<node>/settings.yaml written by the
        store import is the ONE documented exception (STORE-OWNED, reconciled
        every start — not a create artifact)."""
        from kanibako.commands.start import _resolve_existing_box
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        # Conforming store entry whose token POINTER resolves but whose token
        # FILE does not exist: the import succeeds; the create verdict's
        # usable-token gate then refuses the create.
        persona_dir = tmp_home / "config" / "personas" / "navigator"
        (persona_dir / "codex").mkdir(parents=True)
        (persona_dir / "codex" / "config.toml").write_text(
            'model = "gemma4"\n'
            "[model_providers.navigator]\n"
            'name = "navigator"\n'
            'base_url = "https://api.navigator.example/v1"\n'
            'wire_api = "responses"\n'
            'env_key = "NAVIGATOR_API_KEY"\n'
        )
        (persona_dir / ".secret_path").write_text("./token\n")
        # NO token file written -> pointer unusable -> verdict refuses.

        assert self._create(tmp_home, agent="navigator+codex") == 1
        assert "Error" in capsys.readouterr().err

        config = load_config(config_file)
        std = load_std_paths(config)
        # No box was materialised: nothing resolves, no settings file exists,
        # so pref.system.agent cannot have been written anywhere.
        assert _resolve_existing_box(std, config, str(tmp_home / "project")) is None
        # The documented store-import exception: the agent node registration
        # remains (by design; idempotently re-reconciled at every start).
        assert (std.agents / "navigator℘codex" / "settings.yaml").exists()
