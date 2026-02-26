"""Tests for kanibako.names (names.toml I/O and resolution) and name wiring."""

from __future__ import annotations

import argparse

import pytest
from pathlib import Path

from kanibako.errors import ProjectError
from kanibako.names import (
    assign_name,
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

        # "other" is not in the workset but is a registered AC project.
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

class TestACNameAssignment:
    """Name assignment is wired into account-centric project creation."""

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
# Phase 4: Auto-migration of hash-based directories to name-based
# ---------------------------------------------------------------------------

class TestMigrateHashToName:
    """Auto-migration of old boxes/{hash}/ dirs to boxes/{name}/."""

    def _setup_hash_project(self, tmp_home, config_file, *, layout="default"):
        """Create an old-style hash-based project and return (std, config, project_dir, phash)."""
        from kanibako.config import load_config, write_project_meta
        from kanibako.paths import load_std_paths
        from kanibako.utils import project_hash

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        phash = project_hash(project_dir)

        # Create hash-based directory (simulates pre-naming project).
        hash_dir = std.data_path / "boxes" / phash
        hash_dir.mkdir(parents=True)
        (hash_dir / "project-path.txt").write_text(project_dir + "\n")
        shell_dir = hash_dir / "shell"
        shell_dir.mkdir()

        meta_path = str(hash_dir)
        if layout == "robust":
            vault_ro = str(hash_dir / "vault" / "share-ro")
            vault_rw = str(hash_dir / "vault" / "share-rw")
            # Create vault dirs for robust layout.
            (hash_dir / "vault" / "share-ro").mkdir(parents=True)
            (hash_dir / "vault" / "share-rw").mkdir(parents=True)
        else:
            vault_ro = str(Path(project_dir) / "vault" / "share-ro")
            vault_rw = str(Path(project_dir) / "vault" / "share-rw")

        write_project_meta(
            hash_dir / "project.toml",
            mode="account_centric",
            layout=layout,
            workspace=project_dir,
            shell=str(shell_dir),
            vault_ro=vault_ro,
            vault_rw=vault_rw,
            metadata=meta_path,
            project_hash=phash,
        )

        return std, config, project_dir, phash

    def test_hash_dir_renamed_to_name(self, config_file, tmp_home, credentials_dir):
        """Accessing a hash-based project migrates it to boxes/{name}/."""
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)

        proj = resolve_project(std, config, project_dir=project_dir)

        assert proj.name == "project"
        assert proj.metadata_path == std.data_path / "boxes" / "project"
        assert not (std.data_path / "boxes" / phash).exists()
        assert (std.data_path / "boxes" / "project").is_dir()

    def test_project_toml_name_set(self, config_file, tmp_home, credentials_dir):
        """Migration stores the name in project.toml."""
        from kanibako.config import read_project_meta
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)
        proj = resolve_project(std, config, project_dir=project_dir)

        meta = read_project_meta(proj.metadata_path / "project.toml")
        assert meta is not None
        assert meta["name"] == "project"

    def test_stored_paths_updated(self, config_file, tmp_home, credentials_dir):
        """Migration fixes stored paths that referenced boxes/{hash}/."""
        from kanibako.config import read_project_meta
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)
        proj = resolve_project(std, config, project_dir=project_dir)

        meta = read_project_meta(proj.metadata_path / "project.toml")
        name_dir = str(std.data_path / "boxes" / "project")
        assert meta["shell"].startswith(name_dir)
        assert meta["metadata"].startswith(name_dir)
        # Hash should not appear in stored paths.
        assert phash not in meta["shell"]
        assert phash not in meta["metadata"]

    def test_collision_numbering(self, config_file, tmp_home, credentials_dir):
        """Migration uses collision numbering when basename is taken."""
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)

        # Pre-register the basename so migration must use collision numbering.
        register_name(std.data_path, "project", "/some/other/project")

        proj = resolve_project(std, config, project_dir=project_dir)
        assert proj.name == "project2"
        assert (std.data_path / "boxes" / "project2").is_dir()

    def test_target_exists_raises(self, config_file, tmp_home, credentials_dir):
        """Migration raises if boxes/{name}/ already exists on disk as an orphan."""
        from kanibako.errors import ProjectError
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)

        # Create an orphan directory at boxes/project/ (not in names.toml).
        # assign_name() checks names.toml but not the filesystem, so it
        # assigns "project" — then _migrate_hash_to_name raises because
        # boxes/project/ already exists.
        (std.data_path / "boxes" / "project").mkdir(parents=True)

        with pytest.raises(ProjectError, match="Cannot migrate"):
            resolve_project(std, config, project_dir=project_dir)

    def test_second_access_uses_name_dir(self, config_file, tmp_home, credentials_dir):
        """After migration, re-resolving finds the name-based directory directly."""
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)

        # First access: triggers migration.
        proj1 = resolve_project(std, config, project_dir=project_dir)
        assert proj1.name == "project"

        # Second access: should find name-based dir directly (no migration).
        proj2 = resolve_project(std, config, project_dir=project_dir)
        assert proj2.name == "project"
        assert proj2.metadata_path == proj1.metadata_path

    def test_migration_with_preassigned_name(self, config_file, tmp_home, credentials_dir):
        """If name was set via 'box set name' but dir is still hash-based, migration uses it."""
        from kanibako.config import load_config, write_project_meta
        from kanibako.paths import load_std_paths, resolve_project
        from kanibako.utils import project_hash

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        phash = project_hash(project_dir)

        # Create hash-based dir with a name already in project.toml.
        hash_dir = std.data_path / "boxes" / phash
        hash_dir.mkdir(parents=True)
        (hash_dir / "project-path.txt").write_text(project_dir + "\n")
        shell_dir = hash_dir / "shell"
        shell_dir.mkdir()
        write_project_meta(
            hash_dir / "project.toml",
            mode="account_centric", layout="default",
            workspace=project_dir,
            shell=str(shell_dir),
            vault_ro=str(Path(project_dir) / "vault/share-ro"),
            vault_rw=str(Path(project_dir) / "vault/share-rw"),
            metadata=str(hash_dir),
            project_hash=phash,
            name="myapp",
        )
        # Register the name in names.toml.
        register_name(std.data_path, "myapp", project_dir)

        proj = resolve_project(std, config, project_dir=project_dir)
        assert proj.name == "myapp"
        assert (std.data_path / "boxes" / "myapp").is_dir()
        assert not hash_dir.exists()

    def test_migration_prints_notice(self, config_file, tmp_home, credentials_dir, capsys):
        """Migration prints a notice to stderr."""
        from kanibako.paths import resolve_project

        std, config, project_dir, phash = self._setup_hash_project(tmp_home, config_file)
        resolve_project(std, config, project_dir=project_dir)

        err = capsys.readouterr().err
        assert "Migrated:" in err
        assert "project" in err

    def test_robust_layout_vault_symlinks_updated(self, config_file, tmp_home, credentials_dir):
        """Migration updates vault symlinks for robust layout."""
        from kanibako.paths import resolve_project, _ensure_human_vault_symlink

        std, config, project_dir, phash = self._setup_hash_project(
            tmp_home, config_file, layout="robust",
        )

        # Create old human-friendly vault symlink pointing to hash dir.
        hash_dir = std.data_path / "boxes" / phash
        vault_dir = std.data_path / config.paths_vault
        _ensure_human_vault_symlink(vault_dir, Path(project_dir), hash_dir / "vault")

        # Create old project vault symlink.
        proj_vault_link = Path(project_dir) / "vault"
        proj_vault_link.symlink_to(hash_dir / "vault")

        # Migration should fix both symlinks.
        resolve_project(std, config, project_dir=project_dir)

        name_dir = std.data_path / "boxes" / "project"
        # Human-friendly symlink should point to new location.
        human_link = vault_dir / "project"
        assert human_link.is_symlink()
        assert human_link.resolve() == (name_dir / "vault").resolve()

        # Project vault symlink should point to new location.
        assert proj_vault_link.is_symlink()
        assert proj_vault_link.resolve() == (name_dir / "vault").resolve()
