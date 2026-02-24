"""Tests for kanibako.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.config import load_config
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.paths import (
    DetectionResult,
    ProjectLayout,
    ProjectMode,
    _bootstrap_shell,
    _ensure_human_vault_symlink,
    _ensure_vault_symlink,
    _find_workset_for_path,
    _remove_human_vault_symlink,
    _remove_project_vault_symlink,
    _upgrade_shell,
    detect_project_mode,
    load_std_paths,
    resolve_any_project,
    resolve_project,
)
from kanibako.utils import project_hash


class TestLoadStdPaths:
    def test_creates_directories(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)

        assert std.data_path.is_dir()
        assert std.state_path.is_dir()
        assert std.cache_path.is_dir()

    def test_uses_xdg_dirs(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)

        assert str(std.data_home) == str(tmp_home / "data")
        assert str(std.config_home) == str(tmp_home / "config")

    def test_missing_config_raises(self, tmp_home):
        with pytest.raises(ConfigError, match="missing"):
            load_std_paths()


class TestResolveProject:
    def test_computes_hash(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        expected = project_hash(str(Path(project_dir).resolve()))
        assert proj.project_hash == expected

    def test_initialize_creates_dirs(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.metadata_path.is_dir()
        assert proj.shell_path.is_dir()
        assert proj.is_new

    def test_nonexistent_path_raises(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        with pytest.raises(ProjectError, match="does not exist"):
            resolve_project(
                std, config, project_dir="/nonexistent/path", initialize=False
            )

    def test_not_initialize_skips_creation(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        assert not proj.metadata_path.exists()
        assert not proj.is_new

    def test_mode_is_account_centric(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.mode is ProjectMode.account_centric


class TestProjectMeta:
    """Tests for project metadata storage in project.toml (Phase 1b)."""

    def test_init_writes_project_toml(self, config_file, tmp_home, credentials_dir):
        """resolve_project(initialize=True) writes metadata to project.toml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "project.toml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "account_centric"
        assert meta["workspace"] == str(proj.project_path)
        assert meta["shell"] == str(proj.shell_path)
        assert meta["vault_ro"] == str(proj.vault_ro_path)
        assert meta["vault_rw"] == str(proj.vault_rw_path)

    def test_no_meta_without_initialize(self, config_file, tmp_home):
        """resolve_project(initialize=False) does not write project.toml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        project_toml = proj.metadata_path / "project.toml"
        assert not project_toml.exists()

    def test_stored_paths_used_on_subsequent_access(self, config_file, tmp_home, credentials_dir):
        """Subsequent resolve reads stored paths from project.toml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj1 = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Resolve again (not new)
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.shell_path == proj1.shell_path
        assert proj2.vault_ro_path == proj1.vault_ro_path
        assert proj2.vault_rw_path == proj1.vault_rw_path

    def test_stored_path_override(self, config_file, tmp_home, credentials_dir):
        """User can override shell_path by editing project.toml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Override shell path in project.toml
        custom_shell = tmp_home / "custom_shell"
        from kanibako.config import write_project_meta
        write_project_meta(
            proj.metadata_path / "project.toml",
            mode="account_centric",
            layout="default",
            workspace=str(proj.project_path),
            shell=str(custom_shell),
            vault_ro=str(proj.vault_ro_path),
            vault_rw=str(proj.vault_rw_path),
        )

        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.shell_path == custom_shell

    def test_decentralized_init_writes_meta(self, config_file, tmp_home, credentials_dir):
        """resolve_decentralized_project(initialize=True) writes metadata."""
        from kanibako.paths import resolve_decentralized_project
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_decentralized_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "project.toml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "decentralized"
        assert meta["workspace"] == str(proj.project_path)

    def test_workset_init_writes_meta(self, config_file, tmp_home, credentials_dir):
        """resolve_workset_project(initialize=True) writes metadata."""
        from kanibako.paths import resolve_workset_project
        from kanibako.workset import add_project, create_workset
        config = load_config(config_file)
        std = load_std_paths(config)
        ws_root = tmp_home / "worksets" / "meta-ws"
        ws = create_workset("meta-ws", ws_root, std)
        add_project(ws, "metaproj", tmp_home / "project")

        proj = resolve_workset_project(ws, "metaproj", std, config, initialize=True)

        project_toml = proj.metadata_path / "project.toml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "workset"

    def test_meta_preserves_existing_config(self, config_file, tmp_home, credentials_dir):
        """write_project_meta preserves existing [container] section."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a container image override
        project_toml = proj.metadata_path / "project.toml"
        from kanibako.config import write_project_config
        write_project_config(project_toml, "custom-image:v1")

        # Re-read — image should be there alongside metadata
        from kanibako.config import load_merged_config
        merged = load_merged_config(config_file, project_toml)
        assert merged.container_image == "custom-image:v1"

        # Metadata should also be intact
        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "account_centric"


class TestDetectProjectMode:
    def test_returns_detection_result(self, config_file, tmp_home):
        """detect_project_mode returns a DetectionResult namedtuple."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert isinstance(result, DetectionResult)
        assert hasattr(result, "mode")
        assert hasattr(result, "project_root")

    def test_account_centric_when_projects_dir_exists(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # Initialize to create projects/{hash}/
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric
        assert result.project_root == project_dir.resolve()

    def test_decentralized_when_kanibako_dir_exists(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        assert result.project_root == project_dir.resolve()

    def test_default_account_centric_for_new_project(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # No projects dir, no kanibako dir -> default
        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric
        assert result.project_root == project_dir.resolve()

    def test_account_centric_takes_priority_over_decentralized(
        self, config_file, tmp_home, credentials_dir
    ):
        """When both settings/{hash}/ and .kanibako exist, account-centric wins."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (project_dir / ".kanibako").mkdir(exist_ok=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric

    def test_kanibako_file_not_dir_is_not_decentralized(self, config_file, tmp_home):
        """A .kanibako *file* (not directory) should not trigger decentralized mode."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").write_text("not a directory")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric

    def test_workset_when_inside_workspaces_dir(self, config_file, tmp_home):
        """Project inside a registered workset's workspaces/ -> workset mode."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        # Create a project dir inside the workset's workspaces/
        proj_dir = ws_root.resolve() / "workspaces" / "my-proj"
        proj_dir.mkdir(parents=True)

        result = detect_project_mode(proj_dir, std, config)
        assert result.mode is ProjectMode.workset

    def test_workset_takes_priority_over_all(self, config_file, tmp_home, credentials_dir):
        """Workset detection (step 1) beats account-centric (step 2)."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        proj_dir = ws_root.resolve() / "workspaces" / "my-proj"
        proj_dir.mkdir(parents=True)
        # Also create account-centric projects dir for the same path
        resolve_project(std, config, project_dir=str(proj_dir), initialize=True)

        result = detect_project_mode(proj_dir, std, config)
        assert result.mode is ProjectMode.workset

    # --- Ancestor walk tests ---

    def test_ancestor_walk_finds_ac_marker_from_subdirectory(
        self, config_file, tmp_home, credentials_dir
    ):
        """AC marker in parent is found when CWD is a subdirectory."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        subdir = project_dir / "src" / "lib"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric
        assert result.project_root == project_dir.resolve()

    def test_ancestor_walk_finds_decentralized_marker_from_subdirectory(
        self, config_file, tmp_home
    ):
        """Decentralized marker in parent is found from a subdirectory."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()

        subdir = project_dir / "src" / "deep" / "nested"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        assert result.project_root == project_dir.resolve()

    def test_ancestor_walk_innermost_marker_wins(
        self, config_file, tmp_home
    ):
        """When markers exist at multiple levels, the innermost (child) wins."""
        config = load_config(config_file)
        std = load_std_paths(config)

        # Outer project has .kanibako marker
        outer = tmp_home / "project"
        (outer / ".kanibako").mkdir()

        # Inner project also has .kanibako marker
        inner = outer / "subproject"
        inner.mkdir()
        (inner / ".kanibako").mkdir()

        # Detection from inner/ should find inner's marker
        result = detect_project_mode(inner.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        assert result.project_root == inner.resolve()

    # --- Dotless kanibako/ marker tests ---

    def test_dotless_kanibako_dir_triggers_decentralized(self, config_file, tmp_home):
        """A `kanibako/` directory (no dot) triggers decentralized mode."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "kanibako").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        assert result.project_root == project_dir.resolve()

    def test_dot_kanibako_preferred_over_dotless(self, config_file, tmp_home):
        """.kanibako/ is preferred when both .kanibako/ and kanibako/ exist."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()
        (project_dir / "kanibako").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        # Both trigger decentralized; the function returns on .kanibako first
        assert result.project_root == project_dir.resolve()

    def test_kanibako_file_not_dir_ignored_for_dotless(self, config_file, tmp_home):
        """A `kanibako` *file* (not directory) should not trigger decentralized."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "kanibako").write_text("not a directory")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is ProjectMode.account_centric

    def test_dotless_marker_found_from_subdirectory(self, config_file, tmp_home):
        """Ancestor walk finds dotless kanibako/ in parent from subdirectory."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "kanibako").mkdir()

        subdir = project_dir / "src"
        subdir.mkdir()

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is ProjectMode.decentralized
        assert result.project_root == project_dir.resolve()

    # --- Depth cap tests ---

    def test_walk_stops_at_home(self, config_file, tmp_home):
        """Walk does not ascend above $HOME — marker above home is ignored."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # Place a marker ABOVE home (at tmp_home level)
        (tmp_home / ".kanibako").mkdir(exist_ok=True)

        # project_dir is under home
        project_dir = home / "myproject"
        project_dir.mkdir(parents=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        # Should NOT find the marker above $HOME
        assert result.mode is ProjectMode.account_centric
        assert result.project_root == project_dir.resolve()

    # --- Workset root detection tests ---

    def test_workset_root_detected_from_root_itself(self, config_file, tmp_home):
        """Detection from the workset root (not workspaces/) → workset mode."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        result = detect_project_mode(ws_root.resolve(), std, config)
        assert result.mode is ProjectMode.workset

    def test_workset_detected_from_subdirectory_of_root(self, config_file, tmp_home):
        """Detection from a subdirectory of workset root → workset mode."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        subdir = ws_root / "some" / "subdir"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is ProjectMode.workset


class TestResolveAnyProject:
    def test_resolve_any_project_account_centric(self, config_file, tmp_home, credentials_dir):
        """Falls through to resolve_project for normal dirs."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.mode is ProjectMode.account_centric
        assert proj.metadata_path.is_dir()

    def test_resolve_any_project_decentralized(self, config_file, tmp_home):
        """Dispatches to resolve_decentralized_project when .kanibako/ exists."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()

        proj = resolve_any_project(std, config, project_dir=str(project_dir), initialize=False)

        assert proj.mode is ProjectMode.decentralized
        assert proj.metadata_path == project_dir.resolve() / ".kanibako"

    def test_resolve_any_project_default_cwd(self, config_file, tmp_home, credentials_dir):
        """Uses cwd when project_dir is None."""
        config = load_config(config_file)
        std = load_std_paths(config)

        proj = resolve_any_project(std, config, initialize=True)

        # cwd is tmp_home/project (set by tmp_home fixture)
        assert proj.project_path == (tmp_home / "project").resolve()
        assert proj.mode is ProjectMode.account_centric

    def test_resolve_any_project_workset_mode(self, config_file, tmp_home):
        """Dispatches to resolve_workset_project when inside a workset workspace."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", ws_root, std)
        add_project(ws, "myproj", tmp_home / "project")

        proj_dir = ws.workspaces_dir / "myproj"
        proj = resolve_any_project(std, config, project_dir=str(proj_dir), initialize=False)

        assert proj.mode is ProjectMode.workset
        assert proj.metadata_path == ws.projects_dir / "myproj"
        assert proj.shell_path == ws.projects_dir / "myproj" / "shell"

    def test_resolve_any_project_workset_subdirectory(self, config_file, tmp_home):
        """cwd is workspaces/proj/src/, still resolves correctly."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", ws_root, std)
        add_project(ws, "myproj", tmp_home / "project")

        subdir = ws.workspaces_dir / "myproj" / "src"
        subdir.mkdir(parents=True, exist_ok=True)
        proj = resolve_any_project(std, config, project_dir=str(subdir), initialize=False)

        assert proj.mode is ProjectMode.workset
        assert proj.project_path == ws.workspaces_dir / "myproj"

    def test_resolve_any_project_workset_initializes(self, config_file, tmp_home, credentials_dir):
        """initialize=True creates shell_path etc. for workset project."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", ws_root, std)
        add_project(ws, "myproj", tmp_home / "project")

        proj_dir = ws.workspaces_dir / "myproj"
        proj = resolve_any_project(std, config, project_dir=str(proj_dir), initialize=True)

        assert proj.mode is ProjectMode.workset
        assert proj.shell_path.is_dir()

    def test_resolve_any_project_workset_no_project_raises(self, config_file, tmp_home):
        """Inside workset root but not in a workspace → WorksetError."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        with pytest.raises(WorksetError, match="not in a specific project"):
            resolve_any_project(std, config, project_dir=str(ws_root), initialize=False)

    def test_resolve_any_project_from_subdirectory_ac(self, config_file, tmp_home, credentials_dir):
        """resolve_any_project from a subdirectory finds AC project root."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        subdir = project_dir / "src" / "lib"
        subdir.mkdir(parents=True)

        proj = resolve_any_project(std, config, project_dir=str(subdir), initialize=False)
        assert proj.mode is ProjectMode.account_centric
        assert proj.project_path == project_dir.resolve()

    def test_resolve_any_project_from_subdirectory_decentralized(self, config_file, tmp_home):
        """resolve_any_project from a subdirectory finds decentralized project root."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / ".kanibako").mkdir()

        subdir = project_dir / "src"
        subdir.mkdir()

        proj = resolve_any_project(std, config, project_dir=str(subdir), initialize=False)
        assert proj.mode is ProjectMode.decentralized
        assert proj.project_path == project_dir.resolve()


class TestFindWorksetForPath:
    def test_find_workset_for_path_success(self, config_file, tmp_home):
        """Correct workset + name returned for a workspace path."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", ws_root, std)
        add_project(ws, "myproj", tmp_home / "project")

        proj_dir = (ws.workspaces_dir / "myproj").resolve()
        found_ws, found_name = _find_workset_for_path(proj_dir, std)

        assert found_ws.name == "my-set"
        assert found_name == "myproj"

    def test_find_workset_for_path_no_match_raises(self, config_file, tmp_home):
        """Path not in any workset raises WorksetError."""
        config = load_config(config_file)
        std = load_std_paths(config)

        with pytest.raises(WorksetError, match="No workset found"):
            _find_workset_for_path(tmp_home / "random" / "dir", std)

    def test_find_workset_for_path_root_returns_none_project(self, config_file, tmp_home):
        """Path at workset root (not workspaces/) returns None project name."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        found_ws, found_name = _find_workset_for_path(ws_root.resolve(), std)
        assert found_ws.name == "my-set"
        assert found_name is None

    def test_find_workset_for_path_subdir_of_root_returns_none_project(self, config_file, tmp_home):
        """Path in a non-workspaces subdirectory of workset root returns None."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        subdir = ws_root / "vault" / "stuff"
        subdir.mkdir(parents=True)

        found_ws, found_name = _find_workset_for_path(subdir.resolve(), std)
        assert found_ws.name == "my-set"
        assert found_name is None


class TestEnsureVaultSymlink:
    """Tests for _ensure_vault_symlink convenience symlink."""

    def test_creates_symlink_when_vault_outside_project(self, tmp_path):
        """Symlink created when vault lives outside the project."""
        project = tmp_path / "project"
        project.mkdir()
        remote_vault = tmp_path / "settings" / "abc" / "vault"
        vault_ro = remote_vault / "share-ro"
        vault_ro.mkdir(parents=True)

        _ensure_vault_symlink(project, vault_ro)

        link = project / "vault"
        assert link.is_symlink()
        assert link.resolve() == remote_vault.resolve()

    def test_noop_when_vault_inside_project(self, tmp_path):
        """No symlink created when vault is already under project_path."""
        project = tmp_path / "project"
        vault = project / "vault"
        vault_ro = vault / "share-ro"
        vault_ro.mkdir(parents=True)

        _ensure_vault_symlink(project, vault_ro)

        assert not (project / "vault").is_symlink()
        assert (project / "vault").is_dir()

    def test_noop_when_vault_dir_exists(self, tmp_path):
        """Existing real vault/ directory is not overwritten."""
        project = tmp_path / "project"
        project.mkdir()
        existing_vault = project / "vault"
        existing_vault.mkdir()
        (existing_vault / "my-data").touch()

        remote_vault = tmp_path / "remote" / "vault"
        vault_ro = remote_vault / "share-ro"
        vault_ro.mkdir(parents=True)

        _ensure_vault_symlink(project, vault_ro)

        assert not existing_vault.is_symlink()
        assert (existing_vault / "my-data").exists()

    def test_updates_stale_symlink(self, tmp_path):
        """Stale symlink is updated to point to new target."""
        project = tmp_path / "project"
        project.mkdir()
        old_target = tmp_path / "old" / "vault"
        old_target.mkdir(parents=True)
        link = project / "vault"
        link.symlink_to(old_target)

        new_target = tmp_path / "new" / "vault"
        vault_ro = new_target / "share-ro"
        vault_ro.mkdir(parents=True)

        _ensure_vault_symlink(project, vault_ro)

        assert link.is_symlink()
        assert link.resolve() == new_target.resolve()

    def test_idempotent_when_symlink_matches(self, tmp_path):
        """No change when symlink already points to correct target."""
        project = tmp_path / "project"
        project.mkdir()
        remote_vault = tmp_path / "settings" / "vault"
        vault_ro = remote_vault / "share-ro"
        vault_ro.mkdir(parents=True)

        link = project / "vault"
        link.symlink_to(remote_vault)

        _ensure_vault_symlink(project, vault_ro)

        assert link.is_symlink()
        assert link.resolve() == remote_vault.resolve()

    def test_ac_tree_layout_creates_symlink(self, config_file, tmp_home, credentials_dir):
        """resolve_project with tree layout creates vault symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.robust,
        )

        # Vault dirs should be under metadata_path, not project_path.
        assert "boxes" in str(proj.vault_ro_path)

        # Symlink should exist at project_path/vault.
        link = proj.project_path / "vault"
        assert link.is_symlink()
        assert (link / "share-ro").is_dir()

    def test_ac_default_layout_no_symlink(self, config_file, tmp_home, credentials_dir):
        """resolve_project with default layout does not create symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        link = proj.project_path / "vault"
        assert not link.is_symlink()
        assert link.is_dir()


class TestBootstrapShell:
    """Tests for _bootstrap_shell() shell.d support."""

    def test_creates_shell_d_directory(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _bootstrap_shell(shell)
        assert (shell / ".shell.d").is_dir()

    def test_bashrc_contains_shell_d_source(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _bootstrap_shell(shell)
        content = (shell / ".bashrc").read_text()
        assert ".shell.d/" in content
        assert "for _f in" in content

    def test_bashrc_uses_kanibako_ps1_envvar(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _bootstrap_shell(shell)
        content = (shell / ".bashrc").read_text()
        assert "KANIBAKO_PS1" in content

    def test_creates_profile(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _bootstrap_shell(shell)
        assert (shell / ".profile").is_file()

    def test_idempotent(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _bootstrap_shell(shell)
        content1 = (shell / ".bashrc").read_text()
        _bootstrap_shell(shell)
        content2 = (shell / ".bashrc").read_text()
        assert content1 == content2


class TestUpgradeShell:
    """Tests for _upgrade_shell() patching existing shells."""

    def test_creates_shell_d_if_missing(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".bashrc").write_text("# old bashrc\n")
        _upgrade_shell(shell)
        assert (shell / ".shell.d").is_dir()

    def test_appends_source_line_to_old_bashrc(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".bashrc").write_text(
            '# kanibako shell environment\n'
            '[ -f /etc/bashrc ] && . /etc/bashrc\n'
            'export PS1="(kanibako) \\u@\\h:\\w\\$ "\n'
        )
        _upgrade_shell(shell)
        content = (shell / ".bashrc").read_text()
        assert ".shell.d/" in content
        assert "for _f in" in content

    def test_idempotent_does_not_duplicate(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".bashrc").write_text("# old\n")
        _upgrade_shell(shell)
        content1 = (shell / ".bashrc").read_text()
        _upgrade_shell(shell)
        content2 = (shell / ".bashrc").read_text()
        assert content1 == content2
        assert content2.count(".shell.d/") == 1

    def test_no_bashrc_is_noop(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        _upgrade_shell(shell)
        # Should still create .shell.d but not a .bashrc
        assert (shell / ".shell.d").is_dir()
        assert not (shell / ".bashrc").exists()

    def test_handles_missing_trailing_newline(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".bashrc").write_text("# no trailing newline")
        _upgrade_shell(shell)
        content = (shell / ".bashrc").read_text()
        assert ".shell.d/" in content
        lines = content.splitlines()
        assert lines[0] == "# no trailing newline"


class TestGlobalSharedPath:
    """Tests for global_shared_path on ProjectPaths."""

    def test_ac_has_global_shared_path(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        expected = std.data_path / config.paths_shared / "global"
        assert proj.global_shared_path == expected

    def test_ac_no_init_has_global_shared_path(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        assert proj.global_shared_path is not None
        assert "shared" in str(proj.global_shared_path)
        assert str(proj.global_shared_path).endswith("/global")


class TestLocalSharedPath:
    """Tests for local_shared_path on ProjectPaths."""

    def test_ac_has_local_shared_path(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        expected = std.data_path / config.paths_shared
        assert proj.local_shared_path == expected


class TestHumanFriendlyVaultSymlink:
    """Tests for _ensure_human_vault_symlink."""

    def test_creates_symlink_for_robust_layout(self, tmp_path):
        """Creates {vault_dir}/{basename} → vault_parent."""
        vault_dir = tmp_path / "vault"
        project_path = tmp_path / "my-project"
        project_path.mkdir()
        vault_parent = tmp_path / "boxes" / "abc123" / "vault"
        vault_parent.mkdir(parents=True)

        result = _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)

        assert result is not None
        link = vault_dir / "my-project"
        assert link.is_symlink()
        assert link.resolve() == vault_parent.resolve()

    def test_collision_handling(self, tmp_path):
        """When basename exists pointing elsewhere, tries name1, name2, etc."""
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        project_path = tmp_path / "proj"
        project_path.mkdir()

        # Create two vault_parents for the same project basename.
        vp1 = tmp_path / "boxes" / "aaa" / "vault"
        vp1.mkdir(parents=True)
        vp2 = tmp_path / "boxes" / "bbb" / "vault"
        vp2.mkdir(parents=True)

        # First call takes "proj".
        r1 = _ensure_human_vault_symlink(vault_dir, project_path, vp1)
        assert r1 is not None
        assert r1.name == "proj"

        # Second call with different target gets "proj1".
        r2 = _ensure_human_vault_symlink(vault_dir, project_path, vp2)
        assert r2 is not None
        assert r2.name == "proj1"

    def test_idempotent_on_reinit(self, tmp_path):
        """Re-calling with same target is a no-op."""
        vault_dir = tmp_path / "vault"
        project_path = tmp_path / "proj"
        project_path.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        r1 = _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)
        r2 = _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)

        assert r1 == r2
        # Only one symlink should exist.
        links = list(vault_dir.iterdir())
        assert len(links) == 1

    def test_no_symlink_when_vault_parent_missing(self, tmp_path):
        """Returns None when vault_parent doesn't exist."""
        vault_dir = tmp_path / "vault"
        project_path = tmp_path / "proj"
        project_path.mkdir()
        vault_parent = tmp_path / "nonexistent" / "vault"

        result = _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)
        assert result is None

    def test_creates_vault_dir_if_missing(self, tmp_path):
        """vault_dir is created if it doesn't exist."""
        vault_dir = tmp_path / "deep" / "vault"
        project_path = tmp_path / "proj"
        project_path.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)

        assert vault_dir.is_dir()
        assert (vault_dir / "proj").is_symlink()

    def test_skips_real_directory(self, tmp_path):
        """If basename is a real directory, tries next candidate."""
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        # Create a real directory with the project name.
        (vault_dir / "proj").mkdir()
        project_path = tmp_path / "proj"
        project_path.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        result = _ensure_human_vault_symlink(vault_dir, project_path, vault_parent)
        assert result is not None
        assert result.name == "proj1"

    def test_robust_layout_integration(self, config_file, tmp_home, credentials_dir):
        """resolve_project with robust layout creates human-friendly symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.robust,
        )

        human_vault_dir = std.data_path / config.paths_vault
        link = human_vault_dir / "project"
        assert link.is_symlink()
        assert link.resolve() == (proj.metadata_path / "vault").resolve()

    def test_default_layout_no_human_symlink(self, config_file, tmp_home, credentials_dir):
        """resolve_project with default layout does NOT create human-friendly symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        human_vault_dir = std.data_path / config.paths_vault
        assert not human_vault_dir.exists()

    def test_simple_layout_no_human_symlink(self, config_file, tmp_home, credentials_dir):
        """resolve_project with simple layout does NOT create human-friendly symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.simple,
        )

        human_vault_dir = std.data_path / config.paths_vault
        assert not human_vault_dir.exists()

    def test_vault_disabled_no_human_symlink(self, config_file, tmp_home, credentials_dir):
        """No human-friendly symlink when vault is disabled."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.robust, vault_enabled=False,
        )

        human_vault_dir = std.data_path / config.paths_vault
        assert not human_vault_dir.exists()


class TestRemoveHumanVaultSymlink:
    """Tests for _remove_human_vault_symlink."""

    def test_removes_matching_symlink(self, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        link = vault_dir / "proj"
        link.symlink_to(vault_parent)

        result = _remove_human_vault_symlink(vault_dir, vault_parent)
        assert result is True
        assert not link.exists()

    def test_cleans_up_empty_vault_dir(self, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        link = vault_dir / "proj"
        link.symlink_to(vault_parent)

        _remove_human_vault_symlink(vault_dir, vault_parent)
        assert not vault_dir.exists()

    def test_keeps_vault_dir_when_not_empty(self, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        link = vault_dir / "proj"
        link.symlink_to(vault_parent)
        (vault_dir / "other").touch()  # Extra file keeps dir alive.

        _remove_human_vault_symlink(vault_dir, vault_parent)
        assert not link.exists()
        assert vault_dir.is_dir()

    def test_noop_when_no_match(self, tmp_path):
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        other_target = tmp_path / "other"
        other_target.mkdir()
        (vault_dir / "proj").symlink_to(other_target)

        vault_parent = tmp_path / "boxes" / "abc" / "vault"
        vault_parent.mkdir(parents=True)

        result = _remove_human_vault_symlink(vault_dir, vault_parent)
        assert result is False
        assert (vault_dir / "proj").is_symlink()

    def test_noop_when_dir_missing(self, tmp_path):
        vault_dir = tmp_path / "vault"  # Does not exist.
        vault_parent = tmp_path / "boxes" / "abc" / "vault"

        result = _remove_human_vault_symlink(vault_dir, vault_parent)
        assert result is False


class TestRemoveProjectVaultSymlink:
    """Tests for _remove_project_vault_symlink."""

    def test_removes_vault_symlink(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        target = tmp_path / "boxes" / "abc" / "vault"
        target.mkdir(parents=True)
        (project / "vault").symlink_to(target)

        result = _remove_project_vault_symlink(project)
        assert result is True
        assert not (project / "vault").exists()

    def test_noop_for_real_directory(self, tmp_path):
        project = tmp_path / "project"
        (project / "vault").mkdir(parents=True)

        result = _remove_project_vault_symlink(project)
        assert result is False
        assert (project / "vault").is_dir()

    def test_noop_when_no_vault(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()

        result = _remove_project_vault_symlink(project)
        assert result is False


class TestPurgeVaultSymlinkCleanup:
    """Tests that purge removes vault symlinks."""

    def test_purge_removes_human_friendly_symlink(self, config_file, tmp_home, credentials_dir):
        """Purging a robust-layout project removes the human-friendly symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.robust,
        )

        human_vault_dir = std.data_path / config.paths_vault
        assert (human_vault_dir / "project").is_symlink()

        # Simulate purge cleanup.
        from kanibako.paths import _remove_human_vault_symlink
        _remove_human_vault_symlink(human_vault_dir, proj.metadata_path / "vault")
        assert not (human_vault_dir / "project").exists()

    def test_purge_removes_project_level_symlink(self, config_file, tmp_home, credentials_dir):
        """Purging a robust-layout project removes the project-level vault symlink."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir,
            initialize=True, layout=ProjectLayout.robust,
        )

        project_path = proj.project_path
        assert (project_path / "vault").is_symlink()

        from kanibako.paths import _remove_project_vault_symlink
        _remove_project_vault_symlink(project_path)
        assert not (project_path / "vault").exists()
