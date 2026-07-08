"""Tests for kanibako.commands.clean (purge subcommand)."""

from __future__ import annotations

import argparse


from kanibako.config import load_config
from kanibako.paths import WorksetSpec, load_std_paths, resolve_project, resolve_workset_project
from kanibako.workset import add_project, create_workset


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

    def test_purge_unregisters_primary(self, config_file, tmp_home, credentials_dir):
        """M2: purging a primary box drops its registry.projects entry."""
        from kanibako.commands.clean import run
        from kanibako.names import lookup_by_path, read_names

        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "registered_proj").mkdir()
        project_dir = str(tmp_home / "registered_proj")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Initialized → registered.
        assert lookup_by_path(std.registry, project_dir) is not None

        args = argparse.Namespace(path=project_dir, all_projects=False, force=True)
        assert run(args) == 0

        # No dangling name → path entry remains.
        assert lookup_by_path(std.registry, project_dir) is None
        assert project_dir not in read_names(std.registry)["projects"].values()

    def test_purge_also_unregisters_primary_boxes_membership(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Bug A source fix: purging a primary box ALSO drops its PRIMARY-workset
        ``boxes:`` membership — not just the global name — so the two registries
        do not drift (the drift that later tripped ``register_workset_box``'s
        uniqueness guard on a re-create).  Mutation guard: remove the membership
        unregister in clean.py and the ``boxes:`` entry survives → this reddens.
        """
        from kanibako import workset_registry
        from kanibako.commands.clean import run
        from kanibako.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "member_proj").mkdir()
        project_dir = str(tmp_home / "member_proj")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / "settings.yaml"),
        )
        assert proj.name in workset_registry.load_workset_boxes(prim_reg)

        args = argparse.Namespace(path=project_dir, all_projects=False, force=True)
        assert run(args) == 0

        # Membership dropped too — no stale boxes: entry left behind.
        assert proj.name not in workset_registry.load_workset_boxes(prim_reg)

    def test_purge_all_unregisters_primaries(self, config_file, tmp_home, credentials_dir):
        """M2 mirror: --all purge clears every primary registry entry."""
        from kanibako.commands.clean import run
        from kanibako.names import read_names

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

        projects = read_names(std.registry)["projects"]
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
        """Purge removes the in-tree artifacts (box_data/, root settings.yaml,
        vault/) for a standalone project, leaving the project root itself."""
        from kanibako.commands.clean import run

        project_dir = tmp_home / "project"
        kanibako_dir = project_dir / "box_data"
        kanibako_dir.mkdir(parents=True)
        # Standalone marker: box_data/ dir + ROOT settings.yaml (drift I).
        (project_dir / "settings.yaml").write_text('project:\n  mode: "standalone"\n')
        (kanibako_dir / "data.txt").write_text("session-data")

        args = argparse.Namespace(
            path=str(project_dir), all_projects=False, force=True,
        )
        rc = run(args)
        assert rc == 0
        assert not kanibako_dir.exists()
        assert not (project_dir / "settings.yaml").exists()
        # The project root itself is NOT deleted.
        assert project_dir.is_dir()

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
        (dec_dir / "box_data" / "settings.yaml").write_text(
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
