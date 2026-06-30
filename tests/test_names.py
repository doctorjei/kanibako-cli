"""Tests for kanibako.names (names.yaml I/O and resolution) and name wiring."""

from __future__ import annotations

import argparse

import pytest
from pathlib import Path

from kanibako.errors import ProjectError
from kanibako.names import (
    assign_name,
    lookup_by_path,
    read_names,
    register_name,
    resolve_name,
    resolve_qualified_name,
    unregister_name,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """Return the resolved ``config.registry`` file path under a temp tree.

    The names API is path-based: every function takes ``std.registry`` (the
    ``{data_path}/global/registry.yaml`` file), not the data root.
    """
    dp = tmp_path / "data"
    dp.mkdir()
    return dp / "global" / "registry.yaml"


# ---------------------------------------------------------------------------
# read_names
# ---------------------------------------------------------------------------

class TestReadNames:
    def test_empty_when_no_file(self, registry: Path) -> None:
        result = read_names(registry)
        assert result == {"projects": {}, "worksets": {}}

    def test_round_trip(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        register_name(registry, "client", "/home/user/ws/client", section="worksets")
        result = read_names(registry)
        assert result["projects"] == {"myapp": "/home/user/myapp"}
        assert result["worksets"] == {"client": "/home/user/ws/client"}

    def test_preserves_both_sections(self, registry: Path) -> None:
        register_name(registry, "a", "/a")
        register_name(registry, "b", "/b")
        register_name(registry, "ws1", "/ws1", section="worksets")
        result = read_names(registry)
        assert len(result["projects"]) == 2
        assert len(result["worksets"]) == 1


# ---------------------------------------------------------------------------
# register_name
# ---------------------------------------------------------------------------

class TestRegisterName:
    def test_register_project(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        names = read_names(registry)
        assert names["projects"]["myapp"] == "/home/user/myapp"

    def test_register_workset(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws/root", section="worksets")
        names = read_names(registry)
        assert names["worksets"]["ws1"] == "/ws/root"

    def test_duplicate_name_same_section(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        with pytest.raises(ProjectError, match="already registered"):
            register_name(registry, "myapp", "/other/path")

    def test_duplicate_name_cross_section(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        with pytest.raises(ProjectError, match="already registered"):
            register_name(registry, "myapp", "/ws/root", section="worksets")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        reg = tmp_path / "a" / "b" / "c" / "global" / "registry.yaml"
        register_name(reg, "x", "/x")
        assert reg.is_file()


# ---------------------------------------------------------------------------
# unregister_name
# ---------------------------------------------------------------------------

class TestUnregisterName:
    def test_unregister_existing(self, registry: Path) -> None:
        register_name(registry, "myapp", "/myapp")
        assert unregister_name(registry, "myapp") is True
        names = read_names(registry)
        assert "myapp" not in names["projects"]

    def test_unregister_nonexistent(self, registry: Path) -> None:
        assert unregister_name(registry, "nope") is False

    def test_unregister_wrong_section(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws1", section="worksets")
        assert unregister_name(registry, "ws1", section="projects") is False
        # Still exists in worksets.
        assert read_names(registry)["worksets"]["ws1"] == "/ws1"

    def test_unregister_workset(self, registry: Path) -> None:
        register_name(registry, "ws1", "/ws1", section="worksets")
        assert unregister_name(registry, "ws1", section="worksets") is True
        assert "ws1" not in read_names(registry)["worksets"]


# ---------------------------------------------------------------------------
# resolve_name
# ---------------------------------------------------------------------------

class TestResolveName:
    def test_resolve_project(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        path, kind = resolve_name(registry, "myapp")
        assert path == "/home/user/myapp"
        assert kind == "project"

    def test_resolve_workset(self, registry: Path) -> None:
        register_name(registry, "ws1", "/home/user/ws", section="worksets")
        path, kind = resolve_name(registry, "ws1")
        assert path == "/home/user/ws"
        assert kind == "workset"

    def test_project_takes_precedence_over_workset(self, registry: Path) -> None:
        """If somehow both exist, project wins (checked first)."""
        # Register a project and workset with different names.
        register_name(registry, "proj", "/proj")
        register_name(registry, "ws1", "/ws", section="worksets")
        # Project is found first.
        path, kind = resolve_name(registry, "proj")
        assert kind == "project"

    def test_unknown_name_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown project"):
            resolve_name(registry, "nope")

    def test_cwd_context_finds_workset_project(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """When cwd is inside a workset, check its workspace dirs first."""
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        path, kind = resolve_name(
            registry, "api", cwd=ws_root / "workspaces" / "api"
        )
        assert path == str(ws_root / "workspaces" / "api")
        assert kind == "project"

    def test_cwd_context_falls_through_when_no_match(
        self, registry: Path, tmp_path: Path
    ) -> None:
        """cwd inside a workset but name doesn't match any project there."""
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces").mkdir()
        register_name(registry, "myws", str(ws_root), section="worksets")
        register_name(registry, "other", "/other/path")

        # "other" is not in the workset but is a registered default-mode project.
        path, kind = resolve_name(
            registry, "other", cwd=ws_root / "workspaces"
        )
        assert path == "/other/path"
        assert kind == "project"


# ---------------------------------------------------------------------------
# resolve_qualified_name
# ---------------------------------------------------------------------------

class TestResolveQualifiedName:
    def test_resolve_qualified(self, registry: Path, tmp_path: Path) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        path, ws_name = resolve_qualified_name(registry, "myws/api")
        assert path == str(ws_root / "workspaces" / "api")
        assert ws_name == "myws"

    def test_unknown_workset_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown workset"):
            resolve_qualified_name(registry, "nope/api")

    def test_unknown_project_in_workset_raises(
        self, registry: Path, tmp_path: Path
    ) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces").mkdir(parents=True)
        register_name(registry, "myws", str(ws_root), section="worksets")

        with pytest.raises(ProjectError, match="not found in workset"):
            resolve_qualified_name(registry, "myws/nope")

    def test_not_qualified_raises(self, registry: Path) -> None:
        with pytest.raises(ProjectError, match="Not a qualified name"):
            resolve_qualified_name(registry, "bare-name")


# ---------------------------------------------------------------------------
# assign_name
# ---------------------------------------------------------------------------

class TestAssignName:
    def test_assigns_basename(self, registry: Path) -> None:
        name = assign_name(registry, "/home/user/projects/myapp")
        assert name == "myapp"
        names = read_names(registry)
        assert names["projects"]["myapp"] == "/home/user/projects/myapp"

    def test_collision_numbering(self, registry: Path) -> None:
        register_name(registry, "myapp", "/first")
        name = assign_name(registry, "/second/myapp")
        assert name == "myapp2"

    def test_multiple_collisions(self, registry: Path) -> None:
        register_name(registry, "myapp", "/first")
        register_name(registry, "myapp2", "/second")
        name = assign_name(registry, "/third/myapp")
        assert name == "myapp3"

    def test_cross_section_collision(self, registry: Path) -> None:
        """A workset name prevents using the same project name."""
        register_name(registry, "myapp", "/ws", section="worksets")
        name = assign_name(registry, "/proj/myapp")
        assert name == "myapp2"

    def test_assigns_to_worksets_section(self, registry: Path) -> None:
        name = assign_name(registry, "/ws/root", section="worksets")
        assert name == "root"
        names = read_names(registry)
        assert names["worksets"]["root"] == "/ws/root"

    def test_empty_basename_fallback(self, registry: Path) -> None:
        """Path with no basename (e.g. '/') gets 'project' as default."""
        name = assign_name(registry, "/")
        assert name == "project"


# ---------------------------------------------------------------------------
# Phase 2: Name assignment wiring into project/workset creation
# ---------------------------------------------------------------------------

class TestLocalNameAssignment:
    """Name assignment is wired into default-mode project creation."""

    def test_new_project_gets_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.name == "project"

    def test_name_stored_in_project_toml(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config, read_project_meta
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        meta = read_project_meta(proj.metadata_path / "settings.yaml")
        assert meta is not None
        assert meta["name"] == "project"

    def test_name_registered_in_names_toml(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        names = read_names(std.registry)
        assert "project" in names["projects"]
        assert names["projects"]["project"] == project_dir

    def test_name_collision_on_second_project(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create first project named "mydir"
        dir1 = tmp_home / "mydir"
        dir1.mkdir()
        proj1 = resolve_project(std, config, project_dir=str(dir1), initialize=True)
        assert proj1.name == "mydir"

        # Create second project with same basename in different location
        parent2 = tmp_home / "other"
        parent2.mkdir()
        dir2 = parent2 / "mydir"
        dir2.mkdir()
        proj2 = resolve_project(std, config, project_dir=str(dir2), initialize=True)
        assert proj2.name == "mydir2"

    def test_existing_project_preserves_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj1 = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj1.name == "project"

        # Re-resolve same project — name should persist, not re-assign.
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=True)
        assert proj2.name == "project"


class TestWorksetNameRegistration:
    """Workset creation registers the name in names.yaml."""

    def test_create_workset_registers_name(self, std, tmp_home):
        from kanibako.workset import create_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)

        names = read_names(std.registry)
        assert "myworkset" in names["worksets"]
        assert names["worksets"]["myworkset"] == str(root.resolve())

    def test_delete_workset_unregisters_name(self, std, tmp_home):
        from kanibako.workset import create_workset, delete_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)
        assert "myworkset" in read_names(std.registry)["worksets"]

        delete_workset("myworkset", std, remove_files=True)
        assert "myworkset" not in read_names(std.registry)["worksets"]


class TestNameRegistration:
    """Name uniqueness and update operations on names.yaml."""

    def test_register_and_read_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Project should be auto-registered under its directory name
        names = read_names(std.registry)
        assert "project" in names["projects"]

    def test_duplicate_name_rejected(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project
        from kanibako.names import register_name

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create two projects
        dir1 = tmp_home / "proj1"
        dir1.mkdir()
        resolve_project(std, config, project_dir=str(dir1), initialize=True)

        # Trying to register a duplicate name should raise
        import pytest
        with pytest.raises(Exception):
            register_name(std.registry, "proj1", str(tmp_home / "other"))

    def test_unregister_name(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert "project" in read_names(std.registry)["projects"]
        unregister_name(std.registry, "project")
        assert "project" not in read_names(std.registry)["projects"]

    def test_read_name_after_creation(self, config_file, tmp_home, credentials_dir):
        """Project name is readable from settings.yaml metadata after creation."""
        from kanibako.config import load_config, read_project_meta
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        meta = read_project_meta(proj.metadata_path / "settings.yaml")
        assert meta["name"] == "project"


class TestBoxListName:
    """box list shows NAME column."""

    def test_list_shows_name_column(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_list
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace()
        rc = run_list(args)
        assert rc == 0
        output = capsys.readouterr().out
        assert "NAME" in output
        assert "project" in output



# ---------------------------------------------------------------------------
# $HOME guard in register_name
# ---------------------------------------------------------------------------

class TestRegisterNameHomeGuard:
    def test_refuses_home_as_project_path(self, registry: Path, monkeypatch) -> None:
        home = registry.parent.parent.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_name(registry, "bad", str(home))

    def test_refuses_home_resolved(self, registry: Path, monkeypatch) -> None:
        """Symlinks to $HOME are also caught."""
        home = registry.parent.parent.parent / "realhome"
        home.mkdir()
        link = registry.parent.parent.parent / "linkhome"
        link.symlink_to(home)
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_name(registry, "bad", str(link))

    def test_allows_subdirectory_of_home(self, registry: Path, monkeypatch) -> None:
        home = registry.parent.parent.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        subdir = home / "projects" / "myapp"
        subdir.mkdir(parents=True)
        register_name(registry, "myapp", str(subdir))
        assert read_names(registry)["projects"]["myapp"] == str(subdir)

    def test_assign_name_inherits_guard(self, registry: Path, monkeypatch) -> None:
        """assign_name delegates to register_name, so the guard applies."""
        home = registry.parent.parent.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            assign_name(registry, str(home))


# ---------------------------------------------------------------------------
# lookup_by_path
# ---------------------------------------------------------------------------

class TestLookupByPath:
    def test_finds_project_by_path(self, registry: Path) -> None:
        register_name(registry, "myapp", "/home/user/myapp")
        result = lookup_by_path(registry, "/home/user/myapp")
        assert result == ("myapp", "projects")

    def test_finds_workset_by_path(self, registry: Path) -> None:
        register_name(registry, "ws1", "/home/user/ws", section="worksets")
        result = lookup_by_path(registry, "/home/user/ws")
        assert result == ("ws1", "worksets")

    def test_returns_none_for_unknown(self, registry: Path) -> None:
        assert lookup_by_path(registry, "/nope") is None

    def test_resolves_symlinks(self, registry: Path, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        register_name(registry, "proj", str(real))
        result = lookup_by_path(registry, str(link))
        assert result == ("proj", "projects")


# ---------------------------------------------------------------------------
# box rm (was: box forget)
# ---------------------------------------------------------------------------

class TestBoxRm:
    def test_rm_by_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target="project", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        names = read_names(std.registry)
        assert "project" not in names["projects"]

        out = capsys.readouterr().out
        assert "Removed 'project' from the registry" in out

    def test_rm_shows_purge_hint(self, config_file, tmp_home, credentials_dir, capsys):
        """Without --purge, rm shows a hint about metadata still present."""
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target="project", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Metadata still present" in out
        assert "box rm" in out
        assert "--purge" in out

    def test_rm_by_path(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target=project_dir, purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0

        names = read_names(std.registry)
        assert "project" not in names["projects"]

    def test_rm_unknown_target(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm

        args = argparse.Namespace(target="nonexistent", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 1
        assert "not a registered" in capsys.readouterr().err

    def test_rm_purge_deletes_metadata(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        metadata_dir = proj.metadata_path
        assert metadata_dir.is_dir()

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0

        assert not metadata_dir.is_dir()
        assert "Removed metadata" in capsys.readouterr().out

    def test_rm_purge_removes_logs(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Create a fake per-box helper log (PRIMARY → primary_workset/logs/<box>.jsonl).
        std.primary_logs.mkdir(parents=True, exist_ok=True)
        log_file = std.primary_logs / "project.jsonl"
        log_file.write_text("test")

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_rm(args)
        assert rc == 0
        assert not log_file.exists()

    def test_rm_preserves_workspace(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Create a file in the workspace to verify it survives.
        (project_dir / "important.txt").write_text("keep me")

        args = argparse.Namespace(target="project", purge=True, force=True)
        run_rm(args)

        assert project_dir.is_dir()
        assert (project_dir / "important.txt").read_text() == "keep me"

    def test_rm_workset(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "ws_root"
        create_workset("myws", ws_root, std)
        assert "myws" in read_names(std.registry)["worksets"]

        args = argparse.Namespace(target="myws", purge=False, force=False)
        rc = run_rm(args)
        assert rc == 0
        assert "myws" not in read_names(std.registry)["worksets"]
