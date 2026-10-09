"""Tests for kanibako.commands.clean (purge subcommand)."""

from __future__ import annotations

import argparse

import pytest


from kanibako.settings.config import load_config
from kanibako.settings.paths import WorksetSpec, load_std_paths, resolve_project, resolve_workset_project
from kanibako.settings.paths import BoxMode, _early_scope
from kanibako.project.workset import add_project, create_workset


class TestClean:
    def test_force_removes_data(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.metadata_path.is_dir()

        args = argparse.Namespace(
            path=project_dir,
            all_projects=False,
            force=True,
        )
        rc = run(args)
        assert rc == 0
        assert not proj.metadata_path.exists()

    def test_force_removes_the_box_logs(self, config_file, tmp_home, credentials_dir):
        """Both per-box logs in ``workset.logs`` go with the session data — the helper log
        and the creds watcher's log, which sit outside the box dir the rmtree takes."""
        from kanibako.commands.clean import run
        from kanibako.settings.paths import creds_watcher_log_path, helper_log_path

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        logs = [helper_log_path(std, proj), creds_watcher_log_path(std, proj)]
        for log in logs:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("x")

        args = argparse.Namespace(path=project_dir, all_projects=False, force=True)
        assert run(args) == 0
        assert [log for log in logs if log.exists()] == []

    def test_purge_unregisters_primary(self, config_file, tmp_home, credentials_dir):
        """M2: purging a primary box drops its PRIMARY-membership entry."""
        from kanibako.commands.clean import run
        from kanibako.settings.paths import (
            load_primary_boxes,
            primary_box_name_for_workspace,
        )

        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "registered_proj").mkdir()
        project_dir = str(tmp_home / "registered_proj")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Initialized → registered.
        assert primary_box_name_for_workspace(
            std.primary_workset, project_dir, early=_early_scope(std, BoxMode.primary)) is not None

        args = argparse.Namespace(path=project_dir, all_projects=False, force=True)
        assert run(args) == 0

        # No dangling name → path entry remains.
        assert primary_box_name_for_workspace(
            std.primary_workset, project_dir, early=_early_scope(std, BoxMode.primary)) is None
        assert project_dir not in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary)).values()

    def test_purge_also_unregisters_primary_boxes_membership(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Bug A source fix: purging a primary box ALSO drops its PRIMARY-workset
        ``boxes:`` membership — not just the global name — so the two registries
        do not drift (the drift that later tripped ``register_workset_box``'s
        uniqueness guard on a re-create).  Mutation guard: remove the membership
        unregister in clean.py and the ``boxes:`` entry survives → this reddens.
        """
        from kanibako.project import workset_registry
        from kanibako.commands.clean import run
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "member_proj").mkdir()
        project_dir = str(tmp_home / "member_proj")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / "workset.yaml"),
            early=_early_scope(std, BoxMode.primary),
        )
        assert proj.name in workset_registry.load_workset_boxes(prim_reg)

        args = argparse.Namespace(path=project_dir, all_projects=False, force=True)
        assert run(args) == 0

        # Membership dropped too — no stale boxes: entry left behind.
        assert proj.name not in workset_registry.load_workset_boxes(prim_reg)

    def test_purge_all_unregisters_primaries(self, config_file, tmp_home, credentials_dir):
        """M2 mirror: --all purge clears every primary membership entry."""
        from kanibako.commands.clean import run
        from kanibako.settings.paths import load_primary_boxes

        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "reg_a").mkdir()
        (tmp_home / "reg_b").mkdir()
        a = str(tmp_home / "reg_a")
        b = str(tmp_home / "reg_b")
        resolve_project(std, config, project_dir=a, initialize=True)
        resolve_project(std, config, project_dir=b, initialize=True)

        args = argparse.Namespace(path=None, all_projects=True, force=True)
        assert run(args) == 0

        projects = load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary))
        assert a not in projects.values()
        assert b not in projects.values()

    def test_no_session_data(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        new_project = tmp_home / "empty_project"
        new_project.mkdir()

        args = argparse.Namespace(
            path=str(new_project),
            all_projects=False,
            force=True,
        )
        rc = run(args)
        assert rc == 0

    def test_no_path_no_all_returns_error(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        args = argparse.Namespace(
            path=None,
            all_projects=False,
            force=True,
        )
        rc = run(args)
        assert rc == 1

    def test_all_force_removes_all(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create two projects
        proj_a_dir = tmp_home / "proj_a"
        proj_a_dir.mkdir()
        proj_a = resolve_project(std, config, project_dir=str(proj_a_dir), initialize=True)

        proj_b_dir = tmp_home / "proj_b"
        proj_b_dir.mkdir()
        proj_b = resolve_project(std, config, project_dir=str(proj_b_dir), initialize=True)

        assert proj_a.metadata_path.is_dir()
        assert proj_b.metadata_path.is_dir()

        args = argparse.Namespace(
            path=None,
            all_projects=True,
            force=True,
        )
        rc = run(args)
        assert rc == 0
        assert not proj_a.metadata_path.exists()
        assert not proj_b.metadata_path.exists()

    def test_all_empty_returns_zero(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        args = argparse.Namespace(
            path=None,
            all_projects=True,
            force=True,
        )
        rc = run(args)
        assert rc == 0


class TestCleanExtended:
    def test_purge_standalone_project(self, config_file, tmp_home):
        """Purge removes the in-tree artifacts (box_data/, root workset.yaml,
        canon/, vault/) for a standalone project, leaving the project root itself."""
        from kanibako.commands.clean import run

        project_dir = tmp_home / "project"
        kanibako_dir = project_dir / "box_data"
        kanibako_dir.mkdir(parents=True)
        # Standalone marker: the ROOT workset.yaml's stored registry null.
        (project_dir / "workset.yaml").write_text('workset:\n  registry: null\n')
        (kanibako_dir / "data.txt").write_text("session-data")
        (project_dir / "canon" / "handbook").mkdir(parents=True)

        args = argparse.Namespace(
            path=str(project_dir), all_projects=False, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert not kanibako_dir.exists()
        assert not (project_dir / "workset.yaml").exists()
        assert not (project_dir / "canon").exists()
        # The project root itself is NOT deleted.
        assert project_dir.is_dir()

    def test_purge_standalone_unlinks_a_linked_vault(self, config_file, tmp_home):
        """A ``vault/`` symlink goes as a link; nothing behind it is touched."""
        from kanibako.commands.clean import run

        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "workset.yaml").write_text('workset:\n  registry: null\n')
        outside = tmp_home / "extv"
        (outside / "rw").mkdir(parents=True)
        (outside / "rw" / "canary.txt").write_text("keep me\n")
        (project_dir / "vault").symlink_to(outside)

        args = argparse.Namespace(path=str(project_dir), all_projects=False, force=True)
        assert run(args) == 0
        assert not (project_dir / "vault").is_symlink()
        assert (outside / "rw" / "canary.txt").read_text() == "keep me\n"

    @pytest.mark.parametrize("where", ["outside", "at-root", "linked-parent"])
    def test_purge_standalone_keeps_a_canon_not_strictly_inside(
        self, config_file, tmp_home, capsys, where,
    ):
        """A ``workset.canon`` outside the root, AT it, or reached out through a linked
        parent is kept and named; the rest of the purge still happens."""
        from kanibako.commands.clean import run

        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "mine.txt").write_text("mine\n")
        outside = tmp_home / "ext"
        (outside / "canon").mkdir(parents=True)
        (outside / "canon" / "canary.txt").write_text("keep me\n")
        (project_dir / "lnk").symlink_to(outside)
        canon, kept = {
            "outside": (outside / "canon", outside / "canon"),
            "at-root": (project_dir, project_dir),
            "linked-parent": (project_dir / "lnk" / "canon", outside / "canon"),
        }[where]
        (project_dir / "workset.yaml").write_text(
            f"workset:\n  registry: null\n  canon: '{canon}'\n")

        args = argparse.Namespace(path=str(project_dir), all_projects=False, force=True)
        assert run(args) == 0
        assert f"left the canon folder at {kept} in place" in capsys.readouterr().err
        assert not (project_dir / "box_data").exists()
        assert not (project_dir / "workset.yaml").exists()
        assert (project_dir / "mine.txt").read_text() == "mine\n"
        assert (project_dir / "lnk").is_symlink()
        assert (outside / "canon" / "canary.txt").read_text() == "keep me\n"

    def test_purge_all_skips_standalone(self, config_file, tmp_home, credentials_dir, capsys):
        """--all only covers default-mode projects, not standalone."""
        from kanibako.commands.clean import run

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create a default-mode project
        ac_dir = tmp_home / "ac_project"
        ac_dir.mkdir()
        proj = resolve_project(std, config, project_dir=str(ac_dir), initialize=True)

        # Create a standalone project
        dec_dir = tmp_home / "dec_project"
        dec_dir.mkdir()
        (dec_dir / "box_data").mkdir()
        (dec_dir / "box_data" / "box.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )
        (dec_dir / "box_data" / "data.txt").write_text("dec-data")

        args = argparse.Namespace(all_projects=True, force=True)
        rc = run(args)
        assert rc == 0

        # Local settings should be gone
        assert not proj.metadata_path.exists()
        # Standalone box_data/ should still exist (not covered by --all)
        assert (dec_dir / "box_data" / "data.txt").exists()


class TestCleanWorkset:
    def test_purge_all_includes_workset_projects(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.clean import run

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create a default-mode project
        ac_dir = tmp_home / "ac_purge"
        ac_dir.mkdir()
        ac_proj = resolve_project(std, config, project_dir=str(ac_dir), initialize=True)

        # Create a workset with an initialized project
        ws_root = tmp_home / "worksets" / "purge-ws"
        ws = create_workset("purge-ws", ws_root, std)
        source = tmp_home / "purge_src"
        source.mkdir()
        add_project(ws, "purge-proj", source)
        ws_proj = resolve_workset_project(WorksetSpec.from_workset(ws), "purge-proj", std, config, initialize=True)
        (ws_proj.metadata_path / "data.txt").write_text("ws-data")

        args = argparse.Namespace(all_projects=True, force=True)
        rc = run(args)
        assert rc == 0

        # Local settings should be gone
        assert not ac_proj.metadata_path.exists()
        # Workset settings should be gone
        assert not (ws.projects_dir / "purge-proj" / "data.txt").exists()

    def test_purge_all_removes_every_box_log(self, config_file, tmp_home, credentials_dir):
        """``--all`` deletes each box's log files — helper log and creds watcher log —
        for PRIMARY boxes and for NAMED members alike."""
        from kanibako.commands.clean import run
        from kanibako.settings.paths import box_log_files

        config = load_config(config_file)
        std = load_std_paths(config)
        primary_dir = tmp_home / "all_logs"
        primary_dir.mkdir()
        primary = resolve_project(std, config, project_dir=str(primary_dir), initialize=True)
        ws = create_workset("logs-ws", tmp_home / "worksets" / "logs-ws", std)
        source = tmp_home / "logs_src"
        source.mkdir()
        add_project(ws, "logs-proj", source)
        resolve_workset_project(
            WorksetSpec.from_workset(ws), "logs-proj", std, config, initialize=True,
        )
        logs = [
            *box_log_files(std.primary_logs, primary.metadata_path.name),
            *box_log_files(ws.logs_dir, "logs-proj"),
        ]
        for log in logs:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("x")

        assert run(argparse.Namespace(all_projects=True, force=True)) == 0
        assert [log for log in logs if log.exists()] == []

    def test_purge_workset_project_single(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.clean import run

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "single-purge-ws"
        ws = create_workset("single-purge-ws", ws_root, std)
        source = tmp_home / "single_purge_src"
        source.mkdir()
        add_project(ws, "single-purge-proj", source)
        ws_proj = resolve_workset_project(WorksetSpec.from_workset(ws), "single-purge-proj", std, config, initialize=True)
        (ws_proj.metadata_path / "data.txt").write_text("purge-data")

        # Use workspace path as path arg
        args = argparse.Namespace(
            path=str(ws.workspaces_dir / "single-purge-proj"),
            all_projects=False, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert not ws_proj.metadata_path.exists()
