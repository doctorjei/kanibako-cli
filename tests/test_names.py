"""Tests for kanibako.names (names.toml I/O and resolution) and name wiring."""

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
def data_path(tmp_path: Path) -> Path:
    """Return a temporary data directory for names.toml."""
    dp = tmp_path / "data"
    dp.mkdir()
    return dp


# ---------------------------------------------------------------------------
# read_names
# ---------------------------------------------------------------------------

class TestReadNames:
    def test_empty_when_no_file(self, data_path: Path) -> None:
        result = read_names(data_path)
        assert result == {"projects": {}, "worksets": {}}

    def test_round_trip(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        register_name(data_path, "client", "/home/user/ws/client", section="worksets")
        result = read_names(data_path)
        assert result["projects"] == {"myapp": "/home/user/myapp"}
        assert result["worksets"] == {"client": "/home/user/ws/client"}

    def test_preserves_both_sections(self, data_path: Path) -> None:
        register_name(data_path, "a", "/a")
        register_name(data_path, "b", "/b")
        register_name(data_path, "ws1", "/ws1", section="worksets")
        result = read_names(data_path)
        assert len(result["projects"]) == 2
        assert len(result["worksets"]) == 1


# ---------------------------------------------------------------------------
# register_name
# ---------------------------------------------------------------------------

class TestRegisterName:
    def test_register_project(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        names = read_names(data_path)
        assert names["projects"]["myapp"] == "/home/user/myapp"

    def test_register_workset(self, data_path: Path) -> None:
        register_name(data_path, "ws1", "/ws/root", section="worksets")
        names = read_names(data_path)
        assert names["worksets"]["ws1"] == "/ws/root"

    def test_duplicate_name_same_section(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        with pytest.raises(ProjectError, match="already registered"):
            register_name(data_path, "myapp", "/other/path")

    def test_duplicate_name_cross_section(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        with pytest.raises(ProjectError, match="already registered"):
            register_name(data_path, "myapp", "/ws/root", section="worksets")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c"
        register_name(deep, "x", "/x")
        assert (deep / "names.toml").is_file()


# ---------------------------------------------------------------------------
# unregister_name
# ---------------------------------------------------------------------------

class TestUnregisterName:
    def test_unregister_existing(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/myapp")
        assert unregister_name(data_path, "myapp") is True
        names = read_names(data_path)
        assert "myapp" not in names["projects"]

    def test_unregister_nonexistent(self, data_path: Path) -> None:
        assert unregister_name(data_path, "nope") is False

    def test_unregister_wrong_section(self, data_path: Path) -> None:
        register_name(data_path, "ws1", "/ws1", section="worksets")
        assert unregister_name(data_path, "ws1", section="projects") is False
        # Still exists in worksets.
        assert read_names(data_path)["worksets"]["ws1"] == "/ws1"

    def test_unregister_workset(self, data_path: Path) -> None:
        register_name(data_path, "ws1", "/ws1", section="worksets")
        assert unregister_name(data_path, "ws1", section="worksets") is True
        assert "ws1" not in read_names(data_path)["worksets"]


# ---------------------------------------------------------------------------
# resolve_name
# ---------------------------------------------------------------------------

class TestResolveName:
    def test_resolve_project(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        path, kind = resolve_name(data_path, "myapp")
        assert path == "/home/user/myapp"
        assert kind == "project"

    def test_resolve_workset(self, data_path: Path) -> None:
        register_name(data_path, "ws1", "/home/user/ws", section="worksets")
        path, kind = resolve_name(data_path, "ws1")
        assert path == "/home/user/ws"
        assert kind == "workset"

    def test_project_takes_precedence_over_workset(self, data_path: Path) -> None:
        """If somehow both exist, project wins (checked first)."""
        # Register a project and workset with different names.
        register_name(data_path, "proj", "/proj")
        register_name(data_path, "ws1", "/ws", section="worksets")
        # Project is found first.
        path, kind = resolve_name(data_path, "proj")
        assert kind == "project"

    def test_unknown_name_raises(self, data_path: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown project"):
            resolve_name(data_path, "nope")

    def test_cwd_context_finds_workset_project(
        self, data_path: Path, tmp_path: Path
    ) -> None:
        """When cwd is inside a workset, check its workspace dirs first."""
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(data_path, "myws", str(ws_root), section="worksets")

        path, kind = resolve_name(
            data_path, "api", cwd=ws_root / "workspaces" / "api"
        )
        assert path == str(ws_root / "workspaces" / "api")
        assert kind == "project"

    def test_cwd_context_falls_through_when_no_match(
        self, data_path: Path, tmp_path: Path
    ) -> None:
        """cwd inside a workset but name doesn't match any project there."""
        ws_root = tmp_path / "ws"
        ws_root.mkdir()
        (ws_root / "workspaces").mkdir()
        register_name(data_path, "myws", str(ws_root), section="worksets")
        register_name(data_path, "other", "/other/path")

        # "other" is not in the workset but is a registered local project.
        path, kind = resolve_name(
            data_path, "other", cwd=ws_root / "workspaces"
        )
        assert path == "/other/path"
        assert kind == "project"


# ---------------------------------------------------------------------------
# resolve_qualified_name
# ---------------------------------------------------------------------------

class TestResolveQualifiedName:
    def test_resolve_qualified(self, data_path: Path, tmp_path: Path) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces" / "api").mkdir(parents=True)
        register_name(data_path, "myws", str(ws_root), section="worksets")

        path, ws_name = resolve_qualified_name(data_path, "myws/api")
        assert path == str(ws_root / "workspaces" / "api")
        assert ws_name == "myws"

    def test_unknown_workset_raises(self, data_path: Path) -> None:
        with pytest.raises(ProjectError, match="Unknown workset"):
            resolve_qualified_name(data_path, "nope/api")

    def test_unknown_project_in_workset_raises(
        self, data_path: Path, tmp_path: Path
    ) -> None:
        ws_root = tmp_path / "ws"
        (ws_root / "workspaces").mkdir(parents=True)
        register_name(data_path, "myws", str(ws_root), section="worksets")

        with pytest.raises(ProjectError, match="not found in workset"):
            resolve_qualified_name(data_path, "myws/nope")

    def test_not_qualified_raises(self, data_path: Path) -> None:
        with pytest.raises(ProjectError, match="Not a qualified name"):
            resolve_qualified_name(data_path, "bare-name")


# ---------------------------------------------------------------------------
# assign_name
# ---------------------------------------------------------------------------

class TestAssignName:
    def test_assigns_basename(self, data_path: Path) -> None:
        name = assign_name(data_path, "/home/user/projects/myapp")
        assert name == "myapp"
        names = read_names(data_path)
        assert names["projects"]["myapp"] == "/home/user/projects/myapp"

    def test_collision_numbering(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/first")
        name = assign_name(data_path, "/second/myapp")
        assert name == "myapp2"

    def test_multiple_collisions(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/first")
        register_name(data_path, "myapp2", "/second")
        name = assign_name(data_path, "/third/myapp")
        assert name == "myapp3"

    def test_cross_section_collision(self, data_path: Path) -> None:
        """A workset name prevents using the same project name."""
        register_name(data_path, "myapp", "/ws", section="worksets")
        name = assign_name(data_path, "/proj/myapp")
        assert name == "myapp2"

    def test_assigns_to_worksets_section(self, data_path: Path) -> None:
        name = assign_name(data_path, "/ws/root", section="worksets")
        assert name == "root"
        names = read_names(data_path)
        assert names["worksets"]["root"] == "/ws/root"

    def test_empty_basename_fallback(self, data_path: Path) -> None:
        """Path with no basename (e.g. '/') gets 'project' as default."""
        name = assign_name(data_path, "/")
        assert name == "project"


# ---------------------------------------------------------------------------
# Phase 2: Name assignment wiring into project/workset creation
# ---------------------------------------------------------------------------

class TestLocalNameAssignment:
    """Name assignment is wired into local project creation."""

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

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta is not None
        assert meta["name"] == "project"

    def test_name_registered_in_names_toml(self, config_file, tmp_home, credentials_dir):
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        names = read_names(std.data_path)
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
    """Workset creation registers the name in names.toml."""

    def test_create_workset_registers_name(self, std, tmp_home):
        from kanibako.workset import create_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)

        names = read_names(std.data_path)
        assert "myworkset" in names["worksets"]
        assert names["worksets"]["myworkset"] == str(root.resolve())

    def test_delete_workset_unregisters_name(self, std, tmp_home):
        from kanibako.workset import create_workset, delete_workset

        root = tmp_home / "ws_root"
        create_workset("myworkset", root, std)
        assert "myworkset" in read_names(std.data_path)["worksets"]

        delete_workset("myworkset", std, remove_files=True)
        assert "myworkset" not in read_names(std.data_path)["worksets"]


class TestBoxSetName:
    """box set name validates uniqueness and updates names.toml + project.toml."""

    def test_set_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set
        from kanibako.config import load_config, read_project_meta
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="name", value="myapp", project=project_dir)
        rc = run_set(args)
        assert rc == 0

        # Verify names.toml updated
        names = read_names(std.data_path)
        assert "myapp" in names["projects"]
        assert "project" not in names["projects"]  # old name removed

        # Verify project.toml updated
        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta["name"] == "myapp"

    def test_set_name_duplicate_rejected(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)

        # Create two projects
        dir1 = tmp_home / "proj1"
        dir1.mkdir()
        resolve_project(std, config, project_dir=str(dir1), initialize=True)

        dir2 = tmp_home / "proj2"
        dir2.mkdir()
        resolve_project(std, config, project_dir=str(dir2), initialize=True)

        # Try to rename proj2 to proj1's name
        args = argparse.Namespace(key="name", value="proj1", project=str(dir2))
        rc = run_set(args)
        assert rc == 1
        assert "already in use" in capsys.readouterr().err

    def test_set_same_name_noop(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="name", value="project", project=project_dir)
        rc = run_set(args)
        assert rc == 0
        assert "unchanged" in capsys.readouterr().out


class TestBoxGetName:
    """box get name returns the current project name."""

    def test_get_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(key="name", project=project_dir)
        rc = run_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "project"


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
    def test_refuses_home_as_project_path(self, data_path: Path, monkeypatch) -> None:
        home = data_path.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_name(data_path, "bad", str(home))

    def test_refuses_home_resolved(self, data_path: Path, monkeypatch) -> None:
        """Symlinks to $HOME are also caught."""
        home = data_path.parent / "realhome"
        home.mkdir()
        link = data_path.parent / "linkhome"
        link.symlink_to(home)
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            register_name(data_path, "bad", str(link))

    def test_allows_subdirectory_of_home(self, data_path: Path, monkeypatch) -> None:
        home = data_path.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        subdir = home / "projects" / "myapp"
        subdir.mkdir(parents=True)
        register_name(data_path, "myapp", str(subdir))
        assert read_names(data_path)["projects"]["myapp"] == str(subdir)

    def test_assign_name_inherits_guard(self, data_path: Path, monkeypatch) -> None:
        """assign_name delegates to register_name, so the guard applies."""
        home = data_path.parent / "fakehome"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        with pytest.raises(ProjectError, match="Refusing to register \\$HOME"):
            assign_name(data_path, str(home))


# ---------------------------------------------------------------------------
# lookup_by_path
# ---------------------------------------------------------------------------

class TestLookupByPath:
    def test_finds_project_by_path(self, data_path: Path) -> None:
        register_name(data_path, "myapp", "/home/user/myapp")
        result = lookup_by_path(data_path, "/home/user/myapp")
        assert result == ("myapp", "projects")

    def test_finds_workset_by_path(self, data_path: Path) -> None:
        register_name(data_path, "ws1", "/home/user/ws", section="worksets")
        result = lookup_by_path(data_path, "/home/user/ws")
        assert result == ("ws1", "worksets")

    def test_returns_none_for_unknown(self, data_path: Path) -> None:
        assert lookup_by_path(data_path, "/nope") is None

    def test_resolves_symlinks(self, data_path: Path, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        register_name(data_path, "proj", str(real))
        result = lookup_by_path(data_path, str(link))
        assert result == ("proj", "projects")


# ---------------------------------------------------------------------------
# box forget
# ---------------------------------------------------------------------------

class TestBoxForget:
    def test_forget_by_name(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target="project", purge=False, force=False)
        rc = run_forget(args)
        assert rc == 0

        names = read_names(std.data_path)
        assert "project" not in names["projects"]

        out = capsys.readouterr().out
        assert "Removed 'project' from names.toml" in out

    def test_forget_by_path(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(target=project_dir, purge=False, force=False)
        rc = run_forget(args)
        assert rc == 0

        names = read_names(std.data_path)
        assert "project" not in names["projects"]

    def test_forget_unknown_target(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget

        args = argparse.Namespace(target="nonexistent", purge=False, force=False)
        rc = run_forget(args)
        assert rc == 1
        assert "not a registered" in capsys.readouterr().err

    def test_forget_purge_deletes_metadata(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        metadata_dir = proj.metadata_path
        assert metadata_dir.is_dir()

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_forget(args)
        assert rc == 0

        assert not metadata_dir.is_dir()
        assert "Removed metadata" in capsys.readouterr().out

    def test_forget_purge_removes_logs(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Create a fake log directory.
        log_dir = std.data_path / "logs" / "project"
        log_dir.mkdir(parents=True)
        (log_dir / "helper.jsonl").write_text("test")

        args = argparse.Namespace(target="project", purge=True, force=True)
        rc = run_forget(args)
        assert rc == 0
        assert not log_dir.is_dir()

    def test_forget_preserves_workspace(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        # Create a file in the workspace to verify it survives.
        (project_dir / "important.txt").write_text("keep me")

        args = argparse.Namespace(target="project", purge=True, force=True)
        run_forget(args)

        assert project_dir.is_dir()
        assert (project_dir / "important.txt").read_text() == "keep me"

    def test_forget_workset(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_forget
        from kanibako.config import load_config
        from kanibako.paths import load_std_paths
        from kanibako.workset import create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "ws_root"
        create_workset("myws", ws_root, std)
        assert "myws" in read_names(std.data_path)["worksets"]

        args = argparse.Namespace(target="myws", purge=False, force=False)
        rc = run_forget(args)
        assert rc == 0
        assert "myws" not in read_names(std.data_path)["worksets"]
