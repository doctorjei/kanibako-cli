"""Tests for kanibako.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.config import load_config
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.paths import (
    DetectionResult,
    BoxMode,
    _bootstrap_shell,
    _find_local_ancestor,
    _find_workset_for_path,
    _resolve_workset_or_connected,
    _upgrade_shell,
    detect_project_mode,
    load_std_paths,
    resolve_any_project,
    resolve_project,
)
from kanibako.names import register_name
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

    def test_mode_is_local(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.mode is BoxMode.primary

    def test_group_auth_defaults_true_without_workset_config(
        self, config_file, tmp_home, credentials_dir
    ):
        """No param, no project-meta, no config.yaml -> both carriers True (no-op).

        Block #2: the flat side-channel is RETIRED. ``proj.group_auth`` is now the
        BOX-level on-disk CHOICE carrier (read-compat); ``proj.workset_group_auth``
        is the WORKSET-level policy carrier. Both default True (shared/on); the
        EFFECTIVE bool is resolved through the capability chain at launch.
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        assert not (std.data_path / "config.yaml").exists()
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.group_auth is True
        assert proj.workset_group_auth is True

    def test_group_auth_from_default_workset_config(
        self, config_file, tmp_home, credentials_dir
    ):
        """config.yaml [project] group_auth=false (OLD on-disk key) read-compat maps
        to the WORKSET policy carrier (JC-3)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        std.data_path.mkdir(parents=True, exist_ok=True)
        (std.data_path / "config.yaml").write_text(
            "project:\n  group_auth: false\n"
        )
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Read-compat: old on-disk default-workset group_auth=false → the WORKSET
        # policy carrier (workset.group_auth_enabled), NOT the box choice.
        assert proj.workset_group_auth is False
        assert proj.group_auth is True

    def test_group_auth_new_key_from_default_workset_config(
        self, config_file, tmp_home, credentials_dir
    ):
        """config.yaml [project] group_auth_enabled=false (NEW key) maps to the
        workset policy carrier, new-wins-old."""
        config = load_config(config_file)
        std = load_std_paths(config)
        std.data_path.mkdir(parents=True, exist_ok=True)
        (std.data_path / "config.yaml").write_text(
            "project:\n  group_auth_enabled: false\n"
        )
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.workset_group_auth is False

    def test_group_auth_default_workset_applies_to_existing_project(
        self, config_file, tmp_home, credentials_dir
    ):
        """Re-resolving an already-initialized default-mode project still honors the
        default workset's policy (read-compat old key) — the default-workset value
        is the base so `workset config default group_auth=false` reaches existing
        projects via the WORKSET policy carrier."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        # First resolve initializes the project.
        resolve_project(std, config, project_dir=project_dir, initialize=True)
        # Now set the default workset to distinct creds (old on-disk key).
        std.data_path.mkdir(parents=True, exist_ok=True)
        (std.data_path / "config.yaml").write_text("project:\n  group_auth: false\n")
        # Second resolve of the existing project must reflect it (not frozen meta).
        proj = resolve_project(std, config, project_dir=project_dir)

        assert proj.workset_group_auth is False


class TestProjectMeta:
    """Tests for project metadata storage in settings.yaml (Phase 1b)."""

    def test_init_writes_project_toml(self, config_file, tmp_home, credentials_dir):
        """resolve_project(initialize=True) writes metadata to settings.yaml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "settings.yaml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "primary"
        assert meta["workspace"] == str(proj.project_path)
        assert meta["shell"] == str(proj.shell_path)
        assert meta["vault_ro"] == str(proj.vault_ro_path)
        assert meta["vault_rw"] == str(proj.vault_rw_path)

    def test_no_meta_without_initialize(self, config_file, tmp_home):
        """resolve_project(initialize=False) does not write settings.yaml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        project_toml = proj.metadata_path / "settings.yaml"
        assert not project_toml.exists()

    def test_stored_paths_used_on_subsequent_access(self, config_file, tmp_home, credentials_dir):
        """Subsequent resolve reads stored paths from settings.yaml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj1 = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Resolve again (not new)
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.shell_path == proj1.shell_path
        assert proj2.vault_ro_path == proj1.vault_ro_path
        assert proj2.vault_rw_path == proj1.vault_rw_path

    def test_stored_shell_override_is_dropped(self, config_file, tmp_home, credentials_dir):
        """B2b (Option A, Jei-ruled): the per-box meta["shell"] custom-path OVERRIDE
        is DROPPED.  Editing the stored ``shell`` field in settings.yaml NO LONGER
        moves the resolved home — home is SOLELY the spec-derived default location
        (boxes/<name>/home).  A user customizing home now sets the
        ``box.bindings.rw.home`` CASCADE override (a launch-bind concern, covered in
        the categories tests), NOT a stored shell path."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        default_shell = proj.shell_path

        # Editing the stored ``shell`` field to a custom path...
        custom_shell = tmp_home / "custom_shell"
        from kanibako.config import write_project_meta
        write_project_meta(
            proj.metadata_path / "settings.yaml",
            mode="primary",
            workspace=str(proj.project_path),
            shell=str(custom_shell),
            vault_ro=str(proj.vault_ro_path),
            vault_rw=str(proj.vault_rw_path),
        )

        # ...is now IGNORED for resolution: home stays the default location.
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.shell_path == default_shell
        assert proj2.shell_path != custom_shell

    def test_standalone_init_writes_meta(self, config_file, tmp_home, credentials_dir):
        """resolve_standalone_project(initialize=True) writes metadata."""
        from kanibako.paths import resolve_standalone_project
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_standalone_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "settings.yaml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "standalone"
        assert meta["workspace"] == str(proj.project_path)

    def test_workset_init_writes_meta(self, config_file, tmp_home, credentials_dir):
        """resolve_workset_project(initialize=True) writes metadata."""
        from kanibako.paths import WorksetSpec, resolve_workset_project
        from kanibako.workset import add_project, create_workset
        config = load_config(config_file)
        std = load_std_paths(config)
        ws_root = tmp_home / "worksets" / "meta-ws"
        ws = create_workset("meta-ws", ws_root, std)
        add_project(ws, "metaproj", tmp_home / "project")

        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "metaproj", std, config, initialize=True)

        project_toml = proj.metadata_path / "settings.yaml"
        assert project_toml.is_file()

        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "named"

    def test_meta_preserves_existing_config(self, config_file, tmp_home, credentials_dir):
        """write_project_meta preserves existing [box] section."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a container image override
        project_toml = proj.metadata_path / "settings.yaml"
        from kanibako.config import write_project_config
        write_project_config(project_toml, "custom-image:v1")

        # Re-read — image should be there alongside metadata
        from kanibako.config import load_merged_config
        merged = load_merged_config(config_file, project_toml)
        assert merged.box_image == "custom-image:v1"

        # Metadata should also be intact
        from kanibako.config import read_project_meta
        meta = read_project_meta(project_toml)
        assert meta is not None
        assert meta["mode"] == "primary"

    # The stored/computed global_shared/local_shared paths were removed in
    # 1.6.0 (Part 4): no ``shared/`` dir exists in the target tree, so the
    # shared-path persistence/fallback tests are deleted.


class TestDetectBoxMode:
    def test_returns_detection_result(self, config_file, tmp_home):
        """detect_project_mode returns a DetectionResult namedtuple."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert isinstance(result, DetectionResult)
        assert hasattr(result, "mode")
        assert hasattr(result, "project_root")

    def test_local_when_projects_dir_exists(self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # Initialize to create projects/{hash}/
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_standalone_when_box_data_dir_exists(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # Drift I marker: a box_data/ dir + a ROOT settings.yaml (mode=standalone).
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == project_dir.resolve()

    def test_default_local_for_new_project(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # No projects dir, no kanibako dir -> default
        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_local_takes_priority_over_standalone(
        self, config_file, tmp_home, credentials_dir
    ):
        """When both settings/{hash}/ and box_data/ exist, local wins."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)
        (project_dir / "box_data").mkdir(exist_ok=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary

    def test_box_data_file_not_dir_is_not_standalone(self, config_file, tmp_home):
        """A box_data *file* (not directory) should not trigger standalone mode."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "box_data").write_text("not a directory")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary

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
        assert result.mode is BoxMode.named

    def test_workset_takes_priority_over_all(self, config_file, tmp_home, credentials_dir):
        """Workset detection (step 1) beats local (step 2)."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        proj_dir = ws_root.resolve() / "workspaces" / "my-proj"
        proj_dir.mkdir(parents=True)
        # Also create default-mode projects dir for the same path
        resolve_project(std, config, project_dir=str(proj_dir), initialize=True)

        result = detect_project_mode(proj_dir, std, config)
        assert result.mode is BoxMode.named

    # --- Ancestor walk tests ---

    def test_ancestor_walk_finds_local_marker_from_subdirectory(
        self, config_file, tmp_home, credentials_dir
    ):
        """Local marker in parent is found when CWD is a subdirectory."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        subdir = project_dir / "src" / "lib"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_ancestor_walk_finds_standalone_marker_from_subdirectory(
        self, config_file, tmp_home
    ):
        """Standalone marker in parent is found from a subdirectory."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        subdir = project_dir / "src" / "deep" / "nested"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir.resolve(), std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == project_dir.resolve()

    def test_ancestor_walk_innermost_marker_wins(
        self, config_file, tmp_home
    ):
        """When markers exist at multiple levels, the innermost (child) wins."""
        config = load_config(config_file)
        std = load_std_paths(config)

        # Outer project has box_data marker + root settings.yaml
        outer = tmp_home / "project"
        (outer / "box_data").mkdir(parents=True)
        (outer / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        # Inner project also has box_data marker + root settings.yaml
        inner = outer / "subproject"
        inner.mkdir()
        (inner / "box_data").mkdir()
        (inner / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        # Detection from inner/ should find inner's marker
        result = detect_project_mode(inner.resolve(), std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == inner.resolve()

    def test_box_data_dir_without_toml_ignored(self, config_file, tmp_home):
        """A `box_data/` directory without settings.yaml is NOT a marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary

    # --- Bare-marker rejection tests (regression: empty box_data) ---

    def test_empty_box_data_dir_is_local(self, config_file, tmp_home):
        """An empty box_data/ (no settings.yaml) is NOT a standalone marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_malformed_project_toml_is_local_and_does_not_raise(
        self, config_file, tmp_home
    ):
        """A malformed box_data/settings.yaml must not raise; falls to local."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()
        (project_dir / "box_data" / "settings.yaml").write_text("not valid yaml: {{{")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_non_standalone_mode_toml_is_not_standalone(self, config_file, tmp_home):
        """A box_data/settings.yaml declaring a non-standalone mode is not a marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()
        (project_dir / "box_data" / "settings.yaml").write_text(
            'project:\n  mode: "primary"\n'
        )

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    # --- Depth cap tests ---

    def test_walk_stops_at_home(self, config_file, tmp_home):
        """Walk does not ascend above $HOME — marker above home is ignored."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # Place a marker ABOVE home (at tmp_home level)
        (tmp_home / "box_data").mkdir(exist_ok=True)
        (tmp_home / "box_data" / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        # project_dir is under home
        project_dir = home / "myproject"
        project_dir.mkdir(parents=True)

        result = detect_project_mode(project_dir.resolve(), std, config)
        # Should NOT find the marker above $HOME
        assert result.mode is BoxMode.primary
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
        assert result.mode is BoxMode.named

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
        assert result.mode is BoxMode.named


class TestFindLocalAncestor:
    """Tests for _find_local_ancestor() one-pass name scan."""

    def test_name_scan_finds_deepest_local_match(self, config_file, tmp_home, credentials_dir):
        """Two nested registered projects — deeper one wins."""
        config = load_config(config_file)
        std = load_std_paths(config)

        outer = tmp_home / "projects" / "outer"
        outer.mkdir(parents=True)
        inner = outer / "inner"
        inner.mkdir()

        # Register both and create their boxes dirs.
        register_name(std.registry, "outer", str(outer))
        (std.boxes / "outer").mkdir(parents=True)
        register_name(std.registry, "inner", str(inner))
        (std.boxes / "inner").mkdir(parents=True)

        # From a subdirectory of inner, the deeper match should win.
        target = inner / "src"
        target.mkdir()
        result = _find_local_ancestor(target.resolve(), std.registry, std.boxes)
        assert result == inner.resolve()

    def test_name_scan_ignores_stale_entry_without_boxes_dir(
        self, config_file, tmp_home,
    ):
        """Name points to a path but no boxes/{name}/ dir exists → ignored."""
        config = load_config(config_file)
        std = load_std_paths(config)

        project = tmp_home / "myproject"
        project.mkdir()
        register_name(std.registry, "myproject", str(project))
        # Intentionally do NOT create boxes/myproject/

        result = _find_local_ancestor(project.resolve(), std.registry, std.boxes)
        assert result is None

    def test_name_scan_exact_match(self, config_file, tmp_home, credentials_dir):
        """CWD equals registered path exactly → matches."""
        config = load_config(config_file)
        std = load_std_paths(config)

        project = tmp_home / "exact"
        project.mkdir()
        register_name(std.registry, "exact", str(project))
        (std.boxes / "exact").mkdir(parents=True)

        result = _find_local_ancestor(project.resolve(), std.registry, std.boxes)
        assert result == project.resolve()



class TestResolveProjectHomeGuard:
    """$HOME guard in resolve_project() blocks implicit creation."""

    def test_home_guard_blocks_implicit_creation(self, config_file, tmp_home):
        """resolve_project(initialize=True) at $HOME with no existing dir → ProjectError."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"  # This is set as $HOME by the fixture

        with pytest.raises(ProjectError, match="Refusing to create a project rooted at .HOME"):
            resolve_project(std, config, project_dir=str(home), initialize=True)

    def test_home_guard_allows_existing_project(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Pre-created project at $HOME → no error (project_dir_path.is_dir() is True)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # Pre-create the project via direct registry write (simulates
        # a project registered before the $HOME guard existed).
        names_path = std.registry
        names_path.parent.mkdir(parents=True, exist_ok=True)
        names_path.write_text(
            f'projects:\n  home: "{home.resolve()}"\nworksets: {{}}\n'
        )
        boxes_dir = std.boxes / "home"
        boxes_dir.mkdir(parents=True)
        (boxes_dir / "shell").mkdir()
        # Write a minimal settings.yaml so resolve_project reads stored paths.
        from kanibako.config import write_project_meta
        write_project_meta(
            boxes_dir / "settings.yaml",
            mode="primary",
            workspace=str(home.resolve()),
            shell=str(boxes_dir / "shell"),
            vault_ro=str(home / "vault" / "ro"),
            vault_rw=str(home / "vault" / "rw"),
            enable_vault=True,
            metadata=str(boxes_dir),
            project_hash=project_hash(str(home.resolve())),
            name="home",
        )

        # Should not raise — project already exists.
        proj = resolve_project(std, config, project_dir=str(home), initialize=True)
        assert proj.project_path == home.resolve()

    def test_home_guard_allows_non_init(self, config_file, tmp_home):
        """resolve_project(initialize=False) at $HOME → no error."""
        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # initialize=False just computes paths, no guard needed.
        proj = resolve_project(std, config, project_dir=str(home), initialize=False)
        assert proj.project_path == home.resolve()


class TestResolveAnyProject:
    def test_resolve_any_project_local(self, config_file, tmp_home, credentials_dir):
        """Falls through to resolve_project for normal dirs."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_any_project(std, config, project_dir=project_dir, initialize=True)

        assert proj.mode is BoxMode.primary
        assert proj.metadata_path.is_dir()

    def test_resolve_any_project_standalone(self, config_file, tmp_home):
        """Dispatches to resolve_standalone_project when box_data/ exists."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        proj = resolve_any_project(std, config, project_dir=str(project_dir), initialize=False)

        assert proj.mode is BoxMode.standalone
        # Drift I: metadata_path is the ROOT (settings.yaml lives there).
        assert proj.metadata_path == project_dir.resolve()

    def test_resolve_any_project_default_cwd(self, config_file, tmp_home, credentials_dir):
        """Uses cwd when project_dir is None."""
        config = load_config(config_file)
        std = load_std_paths(config)

        proj = resolve_any_project(std, config, initialize=True)

        # cwd is tmp_home/project (set by tmp_home fixture)
        assert proj.project_path == (tmp_home / "project").resolve()
        assert proj.mode is BoxMode.primary

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

        assert proj.mode is BoxMode.named
        assert proj.metadata_path == ws.projects_dir / "myproj"
        assert proj.shell_path == ws.projects_dir / "myproj" / "home"

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

        assert proj.mode is BoxMode.named
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

        assert proj.mode is BoxMode.named
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

    def test_resolve_any_project_from_subdirectory_local(self, config_file, tmp_home, credentials_dir):
        """resolve_any_project from a subdirectory finds default-mode project root."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        resolve_project(std, config, project_dir=str(project_dir), initialize=True)

        subdir = project_dir / "src" / "lib"
        subdir.mkdir(parents=True)

        proj = resolve_any_project(std, config, project_dir=str(subdir), initialize=False)
        assert proj.mode is BoxMode.primary
        assert proj.project_path == project_dir.resolve()

    def test_resolve_any_project_from_subdirectory_standalone(self, config_file, tmp_home):
        """resolve_any_project from a subdirectory finds standalone project root."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        subdir = project_dir / "src"
        subdir.mkdir()

        proj = resolve_any_project(std, config, project_dir=str(subdir), initialize=False)
        assert proj.mode is BoxMode.standalone
        # Drift H: project_path is the <root>/workspace subdir; metadata is root.
        assert proj.metadata_path == project_dir.resolve()
        assert proj.project_path == project_dir.resolve() / "workspace"


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


class TestResolveWorksetOrConnected:
    """The shared workset-or-connected fallback resolver."""

    def test_internal_path_resolves_in_tree(self, config_file, tmp_home):
        """An in-tree workspace path resolves directly (no fallback needed)."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "in-set"
        ws = create_workset("in-set", ws_root, std)
        add_project(ws, "myproj", tmp_home / "project")

        proj_dir = (ws.workspaces_dir / "myproj").resolve()
        found_ws, found_name = _resolve_workset_or_connected(proj_dir, std)
        assert found_ws.name == "in-set"
        assert found_name == "myproj"

    def test_external_connected_path_resolves_via_fallback(self, config_file, tmp_home):
        """An external-connected source (outside any tree) resolves to (ws, proj)."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "ext-set"
        ws = create_workset("ext-set", ws_root, std)
        external = tmp_home / "external_repo"
        external.mkdir()
        add_project(ws, "extproj", external, std)

        found_ws, found_name = _resolve_workset_or_connected(external.resolve(), std)
        assert found_ws.name == "ext-set"
        assert found_name == "extproj"

    def test_unknown_path_raises(self, config_file, tmp_home):
        """A path belonging to no workset (in-tree or connected) raises."""
        config = load_config(config_file)
        std = load_std_paths(config)

        with pytest.raises(WorksetError, match="No workset found"):
            _resolve_workset_or_connected(tmp_home / "nowhere", std)


class TestPrimaryVaultLocation:
    """Phase 5: PRIMARY vault lives under @system.primary_workset, NOT in the
    workspace, and no discovery symlink is created (A7 deleted that machinery).
    """

    def test_primary_vault_under_primary_workset(
        self, config_file, tmp_home, credentials_dir,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        # Vault dirs are under the PRIMARY workset, keyed by box name.
        assert proj.vault_ro_path == std.primary_vault_ro / proj.name
        assert proj.vault_rw_path == std.primary_vault_rw / proj.name
        assert proj.vault_ro_path.is_dir()

        # No vault dir or discovery symlink is created inside the workspace.
        assert not (proj.project_path / "vault").exists()
        assert not (proj.project_path / "vault").is_symlink()

    def test_primary_shell_under_box_metadata(
        self, config_file, tmp_home, credentials_dir,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
        )
        assert proj.shell_path == proj.metadata_path / "home"
        assert proj.metadata_path == std.boxes / proj.name


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


# The global_shared_path / local_shared_path fields on ProjectPaths were removed
# in 1.6.0 (Part 4): the target tree has no top-level ``shared/`` dir (claude
# shared dirs live under ``agents/<agent>/``).  Their tests are deleted.


class TestConnectedExternal:
    """Resolution of EXTERNAL dirs connected to a workset (connected.yaml).

    `add_project(ws, name, external, std)` records the redirect; launching from
    the external path (or a subdir, or the discoverability symlink) must resolve
    to the named workset with the external dir as the live workspace.
    """

    def _setup(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.workset import add_project, create_workset
        ws_root = tmp_home / "worksets" / "ext-set"
        ws = create_workset("ext-set", ws_root, std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        return config, std, ws, external

    def test_resolve_from_external_path(self, config_file, tmp_home):
        """Resolve from the external path → named workset, external workspace."""
        config, std, ws, external = self._setup(config_file, tmp_home)

        result = detect_project_mode(external, std, config)
        assert result.mode is BoxMode.named

        proj = resolve_any_project(std, config, project_dir=str(external))
        assert proj.group is not None
        assert proj.group.is_default is False
        assert proj.group.name == "ext-set"
        assert proj.project_path == external

    def test_resolve_from_external_subdir(self, config_file, tmp_home):
        """Resolve from a SUBDIR of the external repo → same workset (ancestor)."""
        config, std, ws, external = self._setup(config_file, tmp_home)
        subdir = external / "src" / "nested"
        subdir.mkdir(parents=True)

        result = detect_project_mode(subdir, std, config)
        assert result.mode is BoxMode.named

        proj = resolve_any_project(std, config, project_dir=str(subdir))
        assert proj.group is not None
        assert proj.group.is_default is False
        assert proj.group.name == "ext-set"
        assert proj.project_path == external

    def test_resolve_from_symlink_matches_external(self, config_file, tmp_home):
        """Resolve from the workspaces/{name} symlink → identical to external."""
        config, std, ws, external = self._setup(config_file, tmp_home)
        link = ws.workspaces_dir / "extproj"
        assert link.is_symlink()

        proj = resolve_any_project(std, config, project_dir=str(link))
        assert proj.group is not None
        assert proj.group.is_default is False
        assert proj.group.name == "ext-set"
        assert proj.project_path == external
