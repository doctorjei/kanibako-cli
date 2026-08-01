"""Tests for kanibako.settings.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings.paths import (
    DetectionResult,
    BoxMode,
    WorksetSpec,
    _bootstrap_shell,
    _find_local_ancestor,
    _find_workset_for_path,
    _resolve_workset_or_connected,
    _upgrade_shell,
    detect_project_mode,
    load_primary_boxes,
    load_std_paths,
    register_primary_box_name,
    resolve_any_project,
    resolve_project,
    resolve_workset_project,
)
from kanibako.utils import project_hash


def _reg_primary(std, name: str, workspace) -> None:
    """Register a PRIMARY box (name → workspace) in the primary membership.

    The membership replacement for the retired ``register_name(..., "projects")``
    setup used across these tests.
    """
    register_primary_box_name(std.primary_workset, std.registry, name, str(workspace))


def _primary_names(std):
    """Return the PRIMARY box membership as ``{name: workspace_str}``."""
    return load_primary_boxes(std.primary_workset)


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

    def test_reverse_lookup_reuses_registered_name_dir_present(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Bug A durable fix: an already-registered workspace whose stored path
        string differs from the freshly-resolved one (symlink drift) is REUSED,
        not re-minted, when the box dir is already present.

        The membership reverse-lookup is resolved-path aware, so the symlink-vs-
        real difference matches and reuses the existing name — so NO duplicate
        membership entry and NO duplicate box dir are minted.
        """
        config = load_config(config_file)
        std = load_std_paths(config)

        real_ws = tmp_home / "realws"
        real_ws.mkdir()
        link_ws = tmp_home / "linkws"
        link_ws.symlink_to(real_ws)

        # Registered under the SYMLINK string (unresolved) → the stored value
        # differs from the resolved real path a fresh resolve computes.
        _reg_primary(std, "myproj", str(link_ws))
        (std.boxes / "myproj").mkdir(parents=True)  # box dir already present

        proj = resolve_project(
            std, config, project_dir=str(real_ws), initialize=True,
        )

        assert proj.name == "myproj"
        assert not proj.is_new  # reused, not created
        # Exactly ONE membership entry — no duplicate minted.
        assert list(_primary_names(std)) == ["myproj"]

    def test_reverse_lookup_reuses_registered_name_dir_missing(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Same as above but the box dir is MISSING: reuse the registered name
        and (re)create the dir UNDER that name — still no duplicate registry
        entry.  Exercises the ``elif project_name`` reuse branch of the create
        path (no second registration)."""
        config = load_config(config_file)
        std = load_std_paths(config)

        real_ws = tmp_home / "realws2"
        real_ws.mkdir()
        link_ws = tmp_home / "linkws2"
        link_ws.symlink_to(real_ws)

        _reg_primary(std, "keep", str(link_ws))

        proj = resolve_project(
            std, config, project_dir=str(real_ws), initialize=True,
        )

        assert proj.name == "keep"
        assert proj.is_new  # dir was (re)created
        assert (std.boxes / "keep").is_dir()
        # Still exactly ONE membership entry — reused, not re-registered.
        assert list(_primary_names(std)) == ["keep"]

    def test_distinct_workspaces_still_mint_distinct_names(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The reverse-lookup guard never collapses genuinely DISTINCT
        workspaces — two different paths get two different names."""
        config = load_config(config_file)
        std = load_std_paths(config)
        (tmp_home / "alpha").mkdir()
        (tmp_home / "beta").mkdir()

        a = resolve_project(
            std, config, project_dir=str(tmp_home / "alpha"), initialize=True,
        )
        b = resolve_project(
            std, config, project_dir=str(tmp_home / "beta"), initialize=True,
        )

        assert a.name != b.name
        assert set(_primary_names(std)) == {a.name, b.name}

    def test_reverse_lookup_reuses_primary_membership_on_registry_drift(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Guard 2 reuses a PRIMARY-workset ``boxes:`` member even when the GLOBAL
        name registry has dropped it (the purge-drift case) — so Guard 1 in
        ``register_workset_box`` never fires mid-create and strands a half-box.

        Setup: the workspace is registered in the PRIMARY-workset ``boxes:``
        membership under ``ghost``.  A re-create there must REUSE ``ghost`` (no
        fresh mint, no Guard 1 raise).  (Since the global ``projects:`` section
        retired, the membership is the sole store; this test still exercises the
        resolved-path reuse path so a re-create never strands a half-box.)
        """
        from kanibako import workset_registry
        from kanibako.settings.config_io import load_doc
        from kanibako.names import read_names

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "drifted"
        ws.mkdir()
        # Seed the primary-workset boxes: membership (the sole store).
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / "settings.yaml"),
        )
        workset_registry.register_workset_box(prim_reg, "ghost", ws)
        assert "ghost" not in read_names(std.registry)["worksets"]

        proj = resolve_project(
            std, config, project_dir=str(ws), initialize=True,
        )

        # Reused the membership name — no fresh mint, no crash.
        assert proj.name == "ghost"
        # The boxes: membership still has exactly one entry for this workspace.
        boxes = workset_registry.load_workset_boxes(prim_reg)
        assert [n for n, p in boxes.items()
                if Path(p).resolve() == ws.resolve()] == ["ghost"]

    def test_name_override_collision_unwinds_cleanly(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Belt-and-suspenders: when Guard 1 DOES refuse mid-create (an explicit
        ``--name`` for a workspace already a member under another name), the
        just-created box dir is UNWOUND — no stranded half-box.  Mutation guard:
        remove the try/except unwind and the ``forced`` dir survives after the
        raise → this reddens.
        """
        from kanibako import workset_registry
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "sharedws"
        ws.mkdir()
        # Workspace already a member under ``orig`` (primary boxes:), but its box
        # dir is missing (so the create branch runs).
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / "settings.yaml"),
        )
        workset_registry.register_workset_box(prim_reg, "orig", ws)

        with pytest.raises(ProjectError, match="already registered"):
            resolve_project(
                std, config, project_dir=str(ws), initialize=True,
                name_override="forced",
            )

        # Unwound: no stranded box dir, no orphan membership entry.
        assert not (std.boxes / "forced").exists()
        assert "forced" not in _primary_names(std)

    def test_name_override_collision_preserves_preexisting_dir(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Data-loss guard: a ``--name X`` collision unwind must NOT delete a
        PRE-EXISTING ``std.boxes/X`` (an orphan/half-created box carrying real
        ``home/`` credentials + session state) — only a dir THIS call created is
        rolled back.  Mutation guard: make the unwind ``rmtree`` unconditional
        (drop the ``_dir_existed`` gate) and the sentinel is deleted → reddens.
        """
        from kanibako import workset_registry
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "sharedws2"
        ws.mkdir()
        # /ws already a primary member under a DIFFERENT name → Guard 1 will raise
        # when the create tries to register "orphan" for the same workspace.
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / "settings.yaml"),
        )
        workset_registry.register_workset_box(prim_reg, "orig", ws)

        # A PRE-EXISTING orphan box dir "orphan" with precious user data — NOT
        # registered globally (so name_override reaches the create branch).
        orphan = std.boxes / "orphan"
        (orphan / "home").mkdir(parents=True)
        sentinel = orphan / "home" / "PRECIOUS_USER_DATA.txt"
        sentinel.write_text("credentials + session state")

        with pytest.raises(ProjectError, match="already registered"):
            resolve_project(
                std, config, project_dir=str(ws), initialize=True,
                name_override="orphan",
            )

        # The pre-existing orphan dir + its data SURVIVE — not rmtree'd.
        assert orphan.is_dir()
        assert sentinel.is_file()
        assert sentinel.read_text() == "credentials + session state"

    def test_reverse_lookup_is_exception_guarded(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        """The primary-membership reverse-lookup must not crash ``resolve_project``
        when a registry read is unresolvable (symlink cycle / permission) — both
        ``_resolve_local_dir`` and the registration-layer Guard 2 wrap it.  A
        raising ``_workset_box_name_for_workspace`` degrades to the fallback, and
        the create still succeeds."""
        import kanibako.settings.paths as paths_mod

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = tmp_home / "guarded"
        ws.mkdir()

        def boom(*_a, **_k):
            raise OSError("unresolvable path")

        monkeypatch.setattr(paths_mod, "_workset_box_name_for_workspace", boom)

        proj = resolve_project(
            std, config, project_dir=str(ws), initialize=True,
        )
        assert proj.is_new
        assert proj.name  # a name was minted; no crash


class TestProjectMeta:
    """Tests for project metadata storage in settings.yaml (Phase 1b)."""

    def test_init_sparse_no_project_meta(self, config_file, tmp_home, credentials_dir):
        """P8b/Option A: a default-vault PRIMARY create writes NO ``project:``/
        ``resolved:`` identity — identity lives in the registry, not on disk."""
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "settings.yaml"
        # Sparse create with default vault writes NOTHING to settings.yaml.
        assert not project_toml.exists()
        # No self-describing identity is recoverable from disk.
        assert "project" not in load_doc(project_toml)
        if project_toml.exists():  # (guards a future non-default write)
            doc = load_doc(project_toml)
            assert "project" not in doc
            assert "resolved" not in doc
        # Identity IS in the PRIMARY membership (name -> external workspace).
        boxes = _primary_names(std)
        assert proj.name in boxes
        assert boxes[proj.name] == str(proj.project_path)

    def test_disabled_vault_persists_sparsely_at_create(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A non-default ``box.enable_vault`` (disabled) is persisted sparsely at
        create — the ONLY thing the sparse create writes — with no ``project:``/
        ``resolved:`` section; default vault writes nothing at all."""
        from kanibako.settings.config import read_box_enable_vault
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)

        # Disabled vault → box.enable_vault: false is written, alone.
        (tmp_home / "voff").mkdir()
        off_dir = str(tmp_home / "voff")
        proj_off = resolve_project(
            std, config, project_dir=off_dir, initialize=True, enable_vault=False,
        )
        toml_off = proj_off.metadata_path / "settings.yaml"
        assert toml_off.is_file()
        doc = load_doc(toml_off)
        assert doc.get("box", {}).get("enable_vault") is False
        assert "project" not in doc
        assert "resolved" not in doc
        assert read_box_enable_vault(toml_off) is False

        # Default (enabled) vault → nothing written.
        (tmp_home / "von").mkdir()
        on_dir = str(tmp_home / "von")
        proj_on = resolve_project(
            std, config, project_dir=on_dir, initialize=True,
        )
        toml_on = proj_on.metadata_path / "settings.yaml"
        assert not toml_on.exists()
        assert read_box_enable_vault(toml_on) is True

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

        # Hand-editing a stale ``resolved.shell`` field into settings.yaml (as a
        # legacy/tampered file might carry)...
        custom_shell = tmp_home / "custom_shell"
        from kanibako.settings.config_io import dump_doc, load_doc
        toml = proj.metadata_path / "settings.yaml"
        doc = load_doc(toml)
        doc["resolved"] = {"shell": str(custom_shell)}
        dump_doc(toml, doc)

        # ...is now IGNORED for resolution: home stays the default location.
        proj2 = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert proj2.shell_path == default_shell
        assert proj2.shell_path != custom_shell

    def test_standalone_init_materializes_marker_sparsely(
        self, config_file, tmp_home, credentials_dir,
    ):
        """P8b/Option A: a standalone create STILL materializes ``settings.yaml``
        (the standalone marker) via the sparse ``workset.kuid`` write, but writes
        NO ``project:``/``resolved:`` identity — the name derives from the kuid +
        ``registry.standalone``."""
        from kanibako.settings.config import read_workset_kuid
        from kanibako.settings.config_io import load_doc
        from kanibako.kuid import SENTINEL
        from kanibako.settings.paths import resolve_standalone_project
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_standalone_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / "settings.yaml"
        # Marker file still exists (materialized by the sparse kuid write).
        assert project_toml.is_file()
        # But it carries only sparse settings — no identity/resolved sections.
        doc = load_doc(project_toml)
        assert "project" not in doc
        assert "resolved" not in doc
        # The kuid IS persisted sparsely (the stable cross-move identity handle).
        assert read_workset_kuid(project_toml) != SENTINEL

    def test_workset_init_sparse_no_project_meta(
        self, config_file, tmp_home, credentials_dir,
    ):
        """P8b/Option A: a default-vault NAMED create writes NO ``project:``/
        ``resolved:`` identity — the box's membership lives in the workset's
        per-workset ``boxes:`` registry."""
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.workset import add_project, create_workset
        config = load_config(config_file)
        std = load_std_paths(config)
        ws_root = tmp_home / "worksets" / "meta-ws"
        ws = create_workset("meta-ws", ws_root, std)
        add_project(ws, "metaproj", tmp_home / "project")

        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "metaproj", std, config, initialize=True)
        assert proj.mode == BoxMode.named
        assert proj.name == "metaproj"

        project_toml = proj.metadata_path / "settings.yaml"
        # Sparse create with default vault writes nothing to settings.yaml.
        assert not project_toml.exists()
        assert "project" not in load_doc(project_toml)
        if project_toml.exists():
            doc = load_doc(project_toml)
            assert "project" not in doc
            assert "resolved" not in doc

    def test_image_override_sparse_no_project_meta(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A ``box.image`` override coexists with sparse create: it is written to
        the ``box:`` table with NO ``project:``/``resolved:`` section alongside."""
        from kanibako.settings.config import (
            load_merged_config,
            write_project_config,
        )
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a container image override (sparse box-scope key).
        project_toml = proj.metadata_path / "settings.yaml"
        write_project_config(project_toml, "custom-image:v1")

        merged = load_merged_config(config_file, project_toml)
        assert merged.box_image == "custom-image:v1"

        # No identity section was ever written.
        doc = load_doc(project_toml)
        assert "project" not in doc
        assert "resolved" not in doc

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

    # --- B2b: in-place standalone marker OVERRIDES workset tree membership ---

    def test_nested_standalone_marker_overrides_workset_tree(
        self, config_file, tmp_home
    ):
        """A STANDALONE box physically INSIDE a workset's directory tree resolves
        as standalone (its own in-place marker wins over the enclosing workset).

        Regression for bug B2b: detect_project_mode used to check workset
        tree-membership (step 1) BEFORE the standalone marker, so a box under a
        workset root was wrongly claimed by the workset ("Inside workset ... but
        not in a specific project workspace") and NEVER resolved standalone. The
        in-place marker is the highest-precedence signal (spec D3-mode #1) and
        must OVERRIDE any workset determination, matching box_resolve.detect_box_mode.
        """
        from kanibako.workset import create_workset
        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "myws"
        create_workset("myws", ws_root, std)

        # A standalone box dropped INSIDE the workset tree (its own box_data/
        # marker dir + a ROOT settings.yaml).
        inner = ws_root / "innerstand"
        (inner / "box_data").mkdir(parents=True)
        (inner / "settings.yaml").write_text(
            'project:\n  mode: "standalone"\n'
        )

        result = detect_project_mode(inner.resolve(), std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == inner.resolve()

    def test_workset_box_without_marker_still_named_inside_tree(
        self, config_file, tmp_home
    ):
        """Regression guard for B2b: a REAL workset box (under the workset tree,
        NO box_data/ marker) STILL resolves as its named workset box — the new
        top-of-function standalone check keys on the box_data/ marker signal only,
        so a marker-less in-tree dir is unaffected.
        """
        from kanibako.workset import create_workset
        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "myws"
        create_workset("myws", ws_root, std)

        proj_dir = ws_root.resolve() / "workspaces" / "x"
        proj_dir.mkdir(parents=True)

        result = detect_project_mode(proj_dir, std, config)
        assert result.mode is BoxMode.named

    def test_connected_external_marker_stays_named_no_dual_registration(
        self, config_file, tmp_home
    ):
        """Anti-dual-registration regression (B2b coverage gap): a standalone box
        FORCE-CONNECTED into a workset keeps its on-disk marker, but is claimed by
        the live ``boxes:`` connection — it must resolve as its NAMED workset box,
        and the top-of-function standalone-marker check must NOT re-import it into
        the global ``standalone:`` registry (the single-registry invariant — a box
        lives in EXACTLY ONE registry).  The connected-external check runs BEFORE
        the marker check precisely so this holds.

        With the OLD order (marker before connected) this FAILS: the marker check
        fires first, re-registering the box in ``standalone:`` (dual registration)
        and returning ``standalone`` instead of ``named``.
        """
        from kanibako import registry_store
        from kanibako.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)

        # A standalone box at an EXTERNAL dir (outside the workset tree): the
        # in-place marker (box_data/ + root settings.yaml) plus a global
        # standalone: registration (its pre-connect resolved state).
        external = (tmp_home / "standalone_box").resolve()
        (external / "box_data").mkdir(parents=True)
        (external / "settings.yaml").write_text("project: {}\n")
        registry_store.register_standalone(
            std.registry, "kx_standalone_box", external
        )
        assert "kx_standalone_box" in registry_store.load_standalone(std.registry)

        # Force-connect: the standalone: entry is DROPPED and a per-workset boxes:
        # connection entry is written (registration MOVED, not duplicated).
        add_project(ws, "sb", external, std, force=True)
        assert (
            "kx_standalone_box"
            not in registry_store.load_standalone(std.registry)
        )

        # WHILE connected (marker still on disk): resolves as its workset box …
        result = detect_project_mode(external, std, config)
        assert result.mode is BoxMode.named
        # … and the marker check did NOT re-register it in standalone:.
        assert (
            registry_store.standalone_name_for_root(std.registry, external) is None
        )
        # The intrinsic on-disk marker was never removed.
        assert (external / "box_data").is_dir()


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
        _reg_primary(std, "outer", str(outer))
        (std.boxes / "outer").mkdir(parents=True)
        _reg_primary(std, "inner", str(inner))
        (std.boxes / "inner").mkdir(parents=True)

        # From a subdirectory of inner, the deeper match should win.
        target = inner / "src"
        target.mkdir()
        result = _find_local_ancestor(target.resolve(), std)
        assert result == inner.resolve()

    def test_name_scan_ignores_stale_entry_without_boxes_dir(
        self, config_file, tmp_home,
    ):
        """Name points to a path but no boxes/{name}/ dir exists → ignored."""
        config = load_config(config_file)
        std = load_std_paths(config)

        project = tmp_home / "myproject"
        project.mkdir()
        _reg_primary(std, "myproject", str(project))
        # Intentionally do NOT create boxes/myproject/

        result = _find_local_ancestor(project.resolve(), std)
        assert result is None

    def test_name_scan_exact_match(self, config_file, tmp_home, credentials_dir):
        """CWD equals registered path exactly → matches."""
        config = load_config(config_file)
        std = load_std_paths(config)

        project = tmp_home / "exact"
        project.mkdir()
        _reg_primary(std, "exact", str(project))
        (std.boxes / "exact").mkdir(parents=True)

        result = _find_local_ancestor(project.resolve(), std)
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
        from kanibako import workset_registry

        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # Pre-create the project via a DIRECT PRIMARY-membership write (bypassing
        # the $HOME guard, which register_primary_box_name enforces) — simulates a
        # box registered before the $HOME guard existed.
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, None,
        )
        workset_registry.register_workset_box(prim_reg, "home", home.resolve())
        boxes_dir = std.boxes / "home"
        boxes_dir.mkdir(parents=True)
        (boxes_dir / "shell").mkdir()
        # P8b/Option A: identity lives in the registry (written above); the box
        # dir + registration are the whole story — no on-disk project meta.

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
    """Resolution of EXTERNAL dirs connected to a workset (D10 per-workset registry).

    `add_project(ws, name, external, std)` records the connection in the
    workset's per-workset ``boxes:`` registry; launching from the external path
    (or a subdir, or the discoverability symlink) must resolve to the named
    workset with the external dir as the live workspace.
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


class TestP7ConnectRegistry:
    """P7/D10: connect registers into the per-workset registry; the global
    ``connected:`` index is GONE; resolution + the workspace override run purely
    off the per-workset ``boxes:`` scan (via box_resolve).
    """

    def _setup(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.workset import add_project, create_workset
        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        return config, std, ws, external

    def _boxes(self, ws):
        from kanibako import workset_registry
        from kanibako.settings.config_io import load_doc
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / "settings.yaml"),
        )
        return workset_registry.load_workset_boxes(registry_path)

    def test_connect_registers_external_in_per_workset_boxes(
        self, config_file, tmp_home
    ):
        """Test 1 — connect records the box in the workset's ``boxes:`` with the
        EXTERNAL path.  Mutation: skip the per-workset registration in add_project
        → this assert goes RED."""
        _config, _std, ws, external = self._setup(config_file, tmp_home)
        assert self._boxes(ws).get("extproj") == str(external)

    def test_connect_round_trip_resolves_to_external_workspace(
        self, config_file, tmp_home
    ):
        """Test 1 (cont.) — resolving FROM the external dir yields the named
        workset with the external dir as the live workspace, sourced ENTIRELY
        from the per-workset scan (no connected: index exists)."""
        config, std, _ws, external = self._setup(config_file, tmp_home)
        result = detect_project_mode(external, std, config)
        assert result.mode is BoxMode.named
        proj = resolve_any_project(std, config, project_dir=str(external))
        assert proj.group is not None and proj.group.name == "ext-set"
        assert proj.project_path == external

    def test_no_global_connected_section_after_connect(
        self, config_file, tmp_home
    ):
        """Test 2 — the global registry carries NO ``connected:`` section after a
        connect; resolution consults only the per-workset registries."""
        from kanibako import registry_store
        from kanibako.settings.config_io import load_doc
        _config, std, _ws, _external = self._setup(config_file, tmp_home)
        # The section is not part of the registry model at all.
        assert "connected" not in registry_store.load_registry(std.registry)
        # And nothing wrote a literal connected: key to the file.
        if std.registry.is_file():
            raw = load_doc(std.registry)
            assert "connected" not in (raw or {})

    def test_workspace_override_sourced_from_box_resolve(
        self, config_file, tmp_home
    ):
        """Test 3 — resolve_workset_project for a connected box returns
        project_path == the external dir, sourced from box_resolve (not
        read_project_meta).  Mutation: break the box_resolve source (item 2) →
        project_path falls back to workspaces/<name> → RED."""
        config, std, ws, external = self._setup(config_file, tmp_home)
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), "extproj", std, config,
            initialize=False,
        )
        assert proj.project_path == external
        # Prove it is NOT the in-tree layout path.
        assert proj.project_path != ws.workspaces_dir / "extproj"

    def test_already_connected_guard_still_errors(self, config_file, tmp_home):
        """Test 4 — connecting a dir already connected still errors, detected via
        the per-workset scan."""
        _config, std, _ws, external = self._setup(config_file, tmp_home)
        from kanibako.workset import add_project, create_workset
        ws_b = create_workset("set-b", tmp_home / "worksets" / "set-b", std)
        with pytest.raises(WorksetError, match="already connected"):
            add_project(ws_b, "dup", external, std)

    def test_find_connected_external_skips_in_tree_boxes(
        self, config_file, tmp_home
    ):
        """Test 5/6 — find_connected_external_box returns None for an IN-TREE box
        (registered in ``boxes:`` with an INTERNAL path), so an in-tree box is
        never mistaken for an external connection (e.g. duplicate's
        ``_source_is_external``).  Mutation: drop the external-only filter →
        this matches the in-tree box → RED."""
        from kanibako import workset_registry
        from kanibako.launch import box_resolve
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.workset import create_workset
        ws = create_workset("mix", tmp_home / "worksets" / "mix", std)
        internal = ws.workspaces_dir / "inbox"
        internal.mkdir(parents=True)
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / "settings.yaml"),
        )
        workset_registry.register_workset_box(registry_path, "inbox", internal)
        assert box_resolve.find_connected_external_box(internal, std) is None

    def test_in_tree_box_unaffected(self, config_file, tmp_home):
        """Test 6 — a normal INTERNAL workset box resolves to its layout
        workspace (regression guard: the per-workset scan does not hijack it)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.workset import add_project, create_workset
        ws = create_workset("in-set", tmp_home / "worksets" / "in-set", std)
        # Internal source (inside the workset root) → a real workspace dir, never
        # an external connection.
        internal = ws.root.resolve() / "workspaces" / "inproj"
        add_project(ws, "inproj", internal, std)
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), "inproj", std, config,
            initialize=False,
        )
        assert proj.project_path == ws.workspaces_dir / "inproj"


# ---------------------------------------------------------------------------
# P5a — create-then-resolve round-trip + create dual-register (new registries)
# ---------------------------------------------------------------------------

class TestP5aCreateThenResolve:
    """The core P5a contract: box create dual-registers into the new registries,
    and the new-model identity derivation (``box_resolve``) reads mode / name /
    workspace / registered back correctly for primary / named / standalone."""

    @staticmethod
    def _primary_registry(std):
        from kanibako import workset_registry
        from kanibako.settings.config_io import load_doc
        return workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / "settings.yaml"),
        )

    def test_primary_create_registers_and_resolves(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako import workset_registry
        from kanibako.launch import box_resolve
        from kanibako.settings.paths import resolve_standalone_project  # noqa: F401
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")

        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        # CREATE dual-register: the box lands in the PRIMARY per-workset registry
        # as name -> external workspace.  (Mutation target: drop the
        # register_workset_box call in resolve_project → this assert fails.)
        boxes = workset_registry.load_workset_boxes(self._primary_registry(std))
        assert proj.name in boxes
        assert Path(boxes[proj.name]).resolve() == proj.project_path.resolve()

        # READ: box_resolve derives the identity back from the registry.
        identity = box_resolve.resolve_box_identity(
            proj.project_path, std, config,
        )
        assert identity is not None
        assert identity["mode"] is BoxMode.primary
        assert identity["name"] == proj.name
        assert identity["workspace"].resolve() == proj.project_path.resolve()
        assert identity["registered"] is True

    def test_named_create_registers_and_resolves(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako import workset_registry
        from kanibako.launch import box_resolve
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.workset import add_project, create_workset
        config = load_config(config_file)
        std = load_std_paths(config)
        ws_root = tmp_home / "worksets" / "ws1"
        ws = create_workset("ws1", ws_root, std)
        add_project(ws, "boxa", tmp_home / "src")

        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws), "boxa", std, config, initialize=True,
        )

        # CREATE dual-register: the box lands in the WORKSET's per-workset
        # registry as name -> workspace.  (Mutation target: drop the
        # register_workset_box call in resolve_workset_project → fails.)
        reg = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / "settings.yaml"),
        )
        boxes = workset_registry.load_workset_boxes(reg)
        assert "boxa" in boxes
        assert Path(boxes["boxa"]).resolve() == proj.project_path.resolve()

        # READ: box_resolve derives named identity from the workset registry.
        identity = box_resolve.resolve_box_identity(
            proj.project_path, std, config,
        )
        assert identity is not None
        assert identity["mode"] is BoxMode.named
        assert identity["name"] == "boxa"
        assert identity["workspace"].resolve() == proj.project_path.resolve()
        assert identity["registered"] is True

    def test_standalone_create_registers_and_resolves(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako import registry_store
        from kanibako.launch import box_resolve
        from kanibako.settings.paths import resolve_standalone_project
        config = load_config(config_file)
        std = load_std_paths(config)
        sabox = tmp_home / "sabox"
        sabox.mkdir()
        project_dir = str(sabox)

        proj = resolve_standalone_project(
            std, config, project_dir=project_dir, initialize=True,
        )

        # The resolved name is the full ``<kuid>_<leaf>`` handle (used for the
        # container name + helper log), NOT the bare dir leaf — the prefix must
        # survive.  (The resolver sources this from the box's own settings.yaml,
        # authoritative even for an unregistered standalone — see 2053.)
        assert proj.name.endswith("_sabox")
        assert proj.name != "sabox"

        # Standalone stays on the GLOBAL standalone registry (no per-workset
        # registry — per the brief).  (Mutation target: skip register_standalone
        # in establish_standalone → not in registry AND box_resolve name falls
        # back to the dir leaf ≠ proj.name → both asserts below go RED.)
        assert proj.name in registry_store.load_standalone(std.registry)

        # READ: box_resolve detects standalone by in-place-settings PRESENCE and
        # sources the registered name (the ``standalone:`` KEY) back.
        identity = box_resolve.resolve_box_identity(
            proj.metadata_path, std, config,
        )
        assert identity is not None
        assert identity["mode"] is BoxMode.standalone
        assert identity["name"] == proj.name
        assert identity["registered"] is True

    def test_primary_enable_vault_false_round_trips_without_project_mode(
        self, config_file, tmp_home, credentials_dir
    ):
        """enable_vault is a box-scope read decoupled from the project: identity
        (P2/P5a).  A settings.yaml with box.enable_vault=False but NO project.mode
        still yields enable_vault=False on resolve — proving the read no longer
        goes through the old project.mode identity gate (it is a plain box-scope
        read via read_box_enable_vault)."""
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(
            std, config, project_dir=project_dir, initialize=True,
        )
        # Rewrite the box settings to hold ONLY box.enable_vault=False (no
        # project: section at all).
        toml = proj.metadata_path / BOX_META_FILE
        dump_doc(toml, {"box": {"enable_vault": False}})

        proj2 = resolve_project(
            std, config, project_dir=project_dir, initialize=False,
        )
        assert proj2.enable_vault is False

    def test_iter_projects_prefers_registry_over_settings(
        self, config_file, tmp_home, credentials_dir
    ):
        """iter_projects sources a box's workspace from the PRIMARY per-workset
        registry FIRST (the new-model source), only falling back to settings.yaml.
        (Mutation target: neuter the registry read → this returns the settings
        workspace instead → RED.  Covers the otherwise-vacuous new branch.)"""
        from kanibako import workset_registry
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import iter_projects
        config = load_config(config_file)
        std = load_std_paths(config)

        # A primary box dir whose settings.yaml workspace is path A (a legacy
        # ``resolved.workspace`` a re-added fallback would read — the mutation
        # target).
        box_dir = std.boxes / "mybox"
        box_dir.mkdir(parents=True)
        settings_ws = tmp_home / "settings_ws"
        dump_doc(box_dir / BOX_META_FILE, {
            "project": {"mode": "primary", "name": "mybox"},
            "resolved": {"workspace": str(settings_ws)},
        })
        # Register a DIFFERENT path B in the PRIMARY per-workset registry.
        registry_ws = tmp_home / "registry_ws"
        reg_path = workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / "settings.yaml"),
        )
        workset_registry.register_workset_box(reg_path, "mybox", registry_ws)

        results = dict(iter_projects(std, config))
        # The registry value WINS over the settings.yaml workspace.
        assert results[box_dir] == registry_ws
        assert results[box_dir] != settings_ws

    def test_iter_projects_unregistered_box_yields_none(
        self, config_file, tmp_home, credentials_dir
    ):
        """P8a: a box dir absent from the PRIMARY registry yields ``None`` — the
        transitional settings.yaml ``resolved.workspace`` + ``project-path.txt``
        breadcrumb fallbacks are DROPPED.  (Mutation target: re-add a
        settings.yaml-workspace fallback → this box would list ``settings_ws``
        instead of ``None`` → RED.)"""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import iter_projects
        config = load_config(config_file)
        std = load_std_paths(config)

        # A primary box dir with a legacy settings.yaml workspace but NO registry
        # entry (the mutation target: a re-added settings-workspace fallback would
        # read this and list ``settings_ws`` instead of ``None``).
        box_dir = std.boxes / "unregbox"
        box_dir.mkdir(parents=True)
        settings_ws = tmp_home / "settings_ws"
        dump_doc(box_dir / BOX_META_FILE, {
            "project": {"mode": "primary", "name": "unregbox"},
            "resolved": {"workspace": str(settings_ws)},
        })

        results = dict(iter_projects(std, config))
        # No registry membership → no resolvable workspace → None (NOT settings_ws).
        assert box_dir in results
        assert results[box_dir] is None


class TestP5aStandalonePresenceSwitch:
    """Mutation proof for the _is_standalone_meta_dir presence switch (site
    1306): detection is now by box_data/ + settings.yaml PRESENCE, no longer by
    a stored box.mode == "standalone" field."""

    def test_presence_detects_without_mode_field(self, tmp_home):
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import _STANDALONE_META_DIR, _is_standalone_meta_dir
        root = tmp_home / "box"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        # A settings.yaml with NO project.mode = "standalone" declaration.  The
        # OLD field-reading impl returned False here; the presence impl → True.
        dump_doc(root / BOX_META_FILE, {"box": {"image": "x"}})
        assert _is_standalone_meta_dir(root) is True

    def test_missing_settings_is_not_standalone(self, tmp_home):
        from kanibako.settings.paths import _STANDALONE_META_DIR, _is_standalone_meta_dir
        root = tmp_home / "box"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        # box_data/ present but NO settings.yaml → not a standalone marker.
        assert _is_standalone_meta_dir(root) is False

    def test_missing_box_data_is_not_standalone(self, tmp_home):
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import _is_standalone_meta_dir
        root = tmp_home / "box"
        root.mkdir()
        dump_doc(root / BOX_META_FILE, {"box": {"image": "x"}})
        # settings.yaml present but NO box_data/ → not a standalone marker.
        assert _is_standalone_meta_dir(root) is False


class TestBoxWorksetSettingsPaths:
    """P2/M-8: the mode-aware (box_tier, workset_tier) settings-file pair
    (``box_workset_settings_paths``) — the SINGLE SOURCE for READ, WRITE and the
    ``meta.box.settings`` ANCHOR.  ``meta.box.settings`` is the UNIFORM
    ``@meta.box.path/settings.yaml`` in EVERY mode (spec §2c ALL PROJECTS)."""

    def _proj(self, tmp_path: Path, *, mode: "BoxMode", group):
        from kanibako.settings.paths import ProjectPaths

        meta = tmp_path / "meta"
        return ProjectPaths(
            project_path=meta / "workspace",
            project_hash="h",
            metadata_path=meta,
            shell_path=meta / "boxes" / "b" / "home",
            vault_ro_path=meta / "vault" / "ro" / "b",
            vault_rw_path=meta / "vault" / "rw" / "b",
            mode=mode,
            group=group,
        )

    def test_standalone_box_tier_is_the_box_data_settings_file(self, tmp_path: Path):
        """STANDALONE gains a real BOX TIER at ``box_data/settings.yaml`` (spec §2c
        L817 + §5 L1407); the ROOT file keeps playing the WORKSET tier.  (Mutation:
        reverting the standalone arm to ``None`` → RED.)"""
        from kanibako.settings.paths import BoxMode, box_workset_settings_paths

        proj = self._proj(tmp_path, mode=BoxMode.standalone, group=None)
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert box_tier == proj.metadata_path / "box_data" / "settings.yaml"
        assert ws_tier == proj.metadata_path / "settings.yaml"

    def test_standalone_box_tier_lives_under_the_box_data_marker(self, tmp_path: Path):
        """The two tiers are the two SPEC positions, not two arbitrary files: the box
        tier sits inside ``box_data/`` (= ``@meta.box.path``) and the workset tier is
        the ROOT file (= the file §5 DETECTION reads).  (Mutation: swapping the
        returned pair → RED.)"""
        from kanibako.settings.paths import (
            _STANDALONE_META_DIR,
            BoxMode,
            box_workset_settings_paths,
        )

        proj = self._proj(tmp_path, mode=BoxMode.standalone, group=None)
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert box_tier.parent.name == _STANDALONE_META_DIR
        assert box_tier.parent.parent == proj.metadata_path
        assert ws_tier is not None and ws_tier.parent == proj.metadata_path

    def test_box_tier_is_never_none_in_any_mode(self, tmp_path: Path):
        """The UNIFORM anchor: every mode has a box-tier FILE PATH.  Absence of the
        file is an empty tier — it is NOT a ``None`` tier.  (Mutation: any
        re-introduction of a ``None`` box tier → RED, and mypy rejects it too, since
        the return type is ``tuple[Path, Path | None]``.)"""
        from kanibako.settings.paths import BoxMode, ProjectGroup, box_workset_settings_paths

        group = ProjectGroup(
            name="default", root=tmp_path / "pw", is_default=True,
            local_shared_base=tmp_path / "data",
        )
        for mode, grp in (
            (BoxMode.primary, group),
            (BoxMode.named, group),
            (BoxMode.standalone, None),
        ):
            box_tier, _ = box_workset_settings_paths(
                self._proj(tmp_path, mode=mode, group=grp)
            )
            assert box_tier is not None, mode
            assert box_tier.name == "settings.yaml", mode

    def test_primary_named_pair_unchanged_vs_pre_p6c(self, tmp_path: Path):
        # BYTE-IDENTITY (equivalence bar): for primary/named the pair MUST equal the
        # pre-P6c computation (box's own settings.yaml, workset_settings_path(group)).
        from kanibako.settings.paths import (
            BOX_META_FILE,
            BoxMode,
            ProjectGroup,
            box_workset_settings_paths,
            workset_settings_path,
        )

        # PRIMARY (default group).
        primary_group = ProjectGroup(
            name="default",
            root=tmp_path / "primary_workset",
            is_default=True,
            local_shared_base=tmp_path / "data",
        )
        proj_p = self._proj(tmp_path, mode=BoxMode.primary, group=primary_group)
        box_p, ws_p = box_workset_settings_paths(proj_p)
        assert box_p == proj_p.metadata_path / BOX_META_FILE
        assert ws_p == workset_settings_path(primary_group)

        # NAMED (non-default group).
        named_group = ProjectGroup(
            name="kento",
            root=tmp_path / "kento",
            is_default=False,
            local_shared_base=tmp_path / "kento",
        )
        proj_n = self._proj(tmp_path, mode=BoxMode.named, group=named_group)
        box_n, ws_n = box_workset_settings_paths(proj_n)
        assert box_n == proj_n.metadata_path / BOX_META_FILE
        assert ws_n == workset_settings_path(named_group)
        # Mutation-guard: box tier is a REAL file (not None) for primary/named —
        # swapping the standalone branch to cover these modes would make box_p None.
        assert box_p is not None and box_n is not None
        # And NEITHER primary nor named routes through ``box_data/`` — that leaf is
        # standalone's alone (mutation: making the box_data arm unconditional → RED).
        assert "box_data" not in box_p.parts
        assert "box_data" not in box_n.parts


class TestStandaloneDetectionIsRootFileOnly:
    """§5 L1422-1427: STANDALONE detection = the ``box_data/`` marker DIR + the ROOT
    ``settings.yaml`` (the WORKSET-tier file).  P2 introduces a BOX-tier file at
    ``box_data/settings.yaml``; detection must NOT come to depend on it, or the
    ancestor-walk that finds a standalone project at all would break."""

    def test_root_file_alone_detects_without_a_box_tier_file(self, tmp_home):
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import _STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / BOX_META_FILE, {"workset": {"kuid": "abcde"}})
        # No box_data/settings.yaml at all — the ABSENT-BY-DEFAULT shape.
        assert not (root / _STANDALONE_META_DIR / BOX_META_FILE).exists()
        assert _is_standalone_meta_dir(root) is True

    def test_box_tier_file_alone_is_not_a_standalone_marker(self, tmp_home):
        """⚑ THE mutation guard for "do not unify detection".  A Writer tidying the
        two settings paths into one would point detection at ``box_data/settings.yaml``
        — and this box, which has NO root file, would start being detected → RED."""
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import _STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / _STANDALONE_META_DIR / BOX_META_FILE, {"box": {"image": "x"}})
        assert not (root / BOX_META_FILE).exists()
        assert _is_standalone_meta_dir(root) is False

    def test_both_files_present_still_detects(self, tmp_home):
        """The new box tier does not DISTURB detection either — presence of both is
        the normal post-``config set`` shape."""
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import _STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / _STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / BOX_META_FILE, {"workset": {"kuid": "abcde"}})
        dump_doc(root / _STANDALONE_META_DIR / BOX_META_FILE, {"box": {"image": "x"}})
        assert _is_standalone_meta_dir(root) is True

    def test_kuid_is_read_from_the_root_file_not_the_box_tier(
        self, config_file, tmp_home, credentials_dir,
    ):
        """``workset.kuid`` is a WORKSET-scope key and MUST stay in the ROOT file: it
        is what materializes half the detection marker.  A later tidy-up that "moves
        the remaining box-ish keys" into ``box_data/`` would break detection in a way
        that looks unrelated — so pin the read side explicitly."""
        from kanibako.settings.config import BOX_META_FILE, read_workset_kuid
        from kanibako.settings.paths import (
            _STANDALONE_META_DIR,
            resolve_standalone_project,
        )

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)

        # create wrote the kuid to the ROOT file, and NOT to the box tier.
        assert read_workset_kuid(root / BOX_META_FILE) != "00000"
        assert read_workset_kuid(
            root / _STANDALONE_META_DIR / BOX_META_FILE
        ) == "00000"


class TestStandaloneEnableVaultTier:
    """``box.enable_vault`` is read DIRECTLY (not via the cascade), so P2's tier move
    has to be handled at the reader: box tier wins, the ROOT file is the R2
    downward-default that keeps a pre-P2 standalone box working with no migration."""

    def _standalone(self, config_file, tmp_home, *, box=None, root_extra=None):
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import _STANDALONE_META_DIR, resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)
        if root_extra is not None:
            doc = load_doc(root / BOX_META_FILE)
            doc.setdefault("box", {}).update(root_extra)
            dump_doc(root / BOX_META_FILE, doc)
        box_file = root / _STANDALONE_META_DIR / BOX_META_FILE
        if box is not None:
            doc = load_doc(box_file)
            doc.setdefault("box", {}).update(box)
            dump_doc(box_file, doc)
        else:
            box_file.unlink(missing_ok=True)
        return resolve_standalone_project(
            std, config, str(root), initialize=False,
        ).enable_vault

    def test_box_tier_wins_over_the_root_file(
        self, config_file, tmp_home, credentials_dir,
    ):
        """box tier is the LAST cascade level — it beats the workset tier."""
        assert self._standalone(
            config_file, tmp_home,
            root_extra={"enable_vault": True}, box={"enable_vault": False},
        ) is False

    def test_legacy_root_only_value_still_resolves(
        self, config_file, tmp_home, credentials_dir,
    ):
        """⚑ THE no-migration claim: a pre-P2 standalone box stored the value in its
        ROOT file (which was its box file then, and is its workset tier now).  It must
        keep resolving.  (Mutation: dropping ``default_from`` → True → RED.)"""
        assert self._standalone(
            config_file, tmp_home, root_extra={"enable_vault": False}, box=None,
        ) is False

    def test_absent_everywhere_is_the_builtin_default(
        self, config_file, tmp_home, credentials_dir,
    ):
        assert self._standalone(config_file, tmp_home, box=None) is True

    def test_primary_ignores_a_workset_tier_value(
        self, config_file, tmp_home, credentials_dir,
    ):
        """PRIMARY is UNCHANGED by P2: the R2 workset fallback is standalone-only, so
        a workset-tier ``box.enable_vault`` stays inert here (a real defect, tracked
        separately).  Pinned so extending the fallback is a DELIBERATE change, not an
        accidental one."""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
        )
        ws_file = std.primary_workset / BOX_META_FILE
        doc = load_doc(ws_file)
        doc.setdefault("box", {})["enable_vault"] = False
        dump_doc(ws_file, doc)
        (proj.metadata_path / BOX_META_FILE).unlink(missing_ok=True)

        proj2 = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=False,
        )
        assert proj2.enable_vault is True
