"""Tests for kanibako.settings.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config import BOX_META_FILE, WORKSET_META_FILE, load_config
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings.paths import (
    DetectionResult,
    BoxMode,
    WorksetSpec,
    _bootstrap_shell,
    _find_local_ancestor,
    _find_workset_for_path,
    _resolve_workset_or_connected,
    _early_scope,
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
from kanibako.settings import bootstrap


def _reg_primary(std, name: str, workspace) -> None:
    """Register a PRIMARY box (name → workspace) in the primary membership.

    The membership replacement for the retired ``register_name(..., "projects")``
    setup used across these tests.
    """
    register_primary_box_name(
        std.primary_workset, name, str(workspace),
        early=_early_scope(std, BoxMode.primary),
    )


def _primary_names(std):
    """Return the PRIMARY box membership as ``{name: workspace_str}``."""
    return load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))


class TestLoadStdPaths:
    def test_resolves_without_creating(self, config_file, tmp_home):
        """``load_std_paths`` RESOLVES ONLY — it creates nothing.

        The eager-``mkdir`` block stood here until the set-door brick repair: one
        stored-but-unusable value then bricked EVERY command (including the
        ``reset`` that would have un-stored it) behind a raw ``OSError``.  Every
        store materializes at its own point of use, so the resolve answers the
        same paths and leaves the filesystem alone.
        """
        config = load_config(config_file)
        std = load_std_paths(config)

        # Anti-vacuity: the resolve really answered, so the absences below say
        # something (a resolve that returned nothing would also "create nothing").
        assert std.data_path == tmp_home / "data" / "kanibako"
        assert std.state == tmp_home / "state" / "kanibako"
        assert std.cache == tmp_home / "cache" / "kanibako"
        assert not std.data_path.exists()
        assert not std.state.exists()
        assert not std.cache.exists()

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
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        from kanibako.project.names import read_names

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "drifted"
        ws.mkdir()
        # Seed the primary-workset boxes: membership (the sole store).
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.primary),
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
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "sharedws"
        ws.mkdir()
        # Workspace already a member under ``orig`` (primary boxes:), but its box
        # dir is missing (so the create branch runs).
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.primary),
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
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = tmp_home / "sharedws2"
        ws.mkdir()
        # /ws already a primary member under a DIFFERENT name → Guard 1 will raise
        # when the create tries to register "orphan" for the same workspace.
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, load_doc(std.primary_workset / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.primary),
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
    """Tests for project metadata storage in box.yaml (Phase 1b)."""

    def test_init_sparse_no_project_meta(self, config_file, tmp_home, credentials_dir):
        """P8b/Option A: a default-vault PRIMARY create writes NO ``project:``/
        ``resolved:`` identity — identity lives in the registry, not on disk."""
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        project_toml = proj.metadata_path / BOX_META_FILE
        # Sparse create with default vault writes NOTHING to box.yaml.
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
        toml_off = proj_off.metadata_path / BOX_META_FILE
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
        toml_on = proj_on.metadata_path / BOX_META_FILE
        assert not toml_on.exists()
        assert read_box_enable_vault(toml_on) is True

    def test_no_meta_without_initialize(self, config_file, tmp_home):
        """resolve_project(initialize=False) does not write box.yaml."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)

        project_toml = proj.metadata_path / BOX_META_FILE
        assert not project_toml.exists()

    def test_stored_paths_used_on_subsequent_access(self, config_file, tmp_home, credentials_dir):
        """Subsequent resolve reads stored paths from box.yaml."""
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
        is DROPPED.  Editing the stored ``shell`` field in box.yaml NO LONGER
        moves the resolved home — home is SOLELY the spec-derived default location
        (boxes/<name>/home).  A user customizing home now sets the
        ``box.bindings.rw.home`` CASCADE override (a launch-bind concern, covered in
        the categories tests), NOT a stored shell path."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        default_shell = proj.shell_path

        # Hand-editing a stale ``resolved.shell`` field into box.yaml (as a
        # legacy/tampered file might carry)...
        custom_shell = tmp_home / "custom_shell"
        from kanibako.settings.config_io import dump_doc, load_doc
        toml = proj.metadata_path / BOX_META_FILE
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
        """P8b/Option A: a standalone create STILL materializes ``workset.yaml``
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

        project_toml = proj.metadata_path / WORKSET_META_FILE
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
        from kanibako.project.workset import add_project, create_workset
        config = load_config(config_file)
        std = load_std_paths(config)
        ws_root = tmp_home / "worksets" / "meta-ws"
        ws = create_workset("meta-ws", ws_root, std)
        add_project(ws, "metaproj", tmp_home / "project")

        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "metaproj", std, config, initialize=True)
        assert proj.mode == BoxMode.named
        assert proj.name == "metaproj"

        project_toml = proj.metadata_path / BOX_META_FILE
        # Sparse create with default vault writes nothing to box.yaml.
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
        from kanibako.settings.config import persist_creation_flags
        from kanibako.settings.settings_launch import load_merged_config
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a container image override (sparse box-scope key).
        project_toml = proj.metadata_path / BOX_META_FILE
        persist_creation_flags(project_toml, materializing=True, image="custom-image:v1")

        merged = load_merged_config(project_toml)
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

    def test_standalone_when_the_root_file_stores_the_registry_null(self, config_file, tmp_home):
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        # Marker: a ROOT workset.yaml storing workset.registry as null.
        (project_dir / "box_data").mkdir(parents=True)
        (project_dir / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
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

        from kanibako.project.workset import create_workset
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

        from kanibako.project.workset import create_workset
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
        (project_dir / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
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

        # Outer project has the root workset.yaml registry-null marker
        outer = tmp_home / "project"
        (outer / "box_data").mkdir(parents=True)
        (outer / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
        )

        # Inner project also has the registry-null marker
        inner = outer / "subproject"
        inner.mkdir()
        (inner / "box_data").mkdir()
        (inner / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
        )

        # Detection from inner/ should find inner's marker
        result = detect_project_mode(inner.resolve(), std, config)
        assert result.mode is BoxMode.standalone
        assert result.project_root == inner.resolve()

    def test_box_data_dir_without_toml_ignored(self, config_file, tmp_home):
        """A `box_data/` directory without a root workset.yaml is NOT a marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary

    # --- Bare-marker rejection tests (regression: empty box_data) ---

    def test_empty_box_data_dir_is_local(self, config_file, tmp_home):
        """An empty box_data/ (no root workset.yaml) is NOT a standalone marker."""
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
        """A malformed box_data/box.yaml must not raise; falls to local."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()
        (project_dir / "box_data" / BOX_META_FILE).write_text("not valid yaml: {{{")

        result = detect_project_mode(project_dir.resolve(), std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == project_dir.resolve()

    def test_non_standalone_mode_toml_is_not_standalone(self, config_file, tmp_home):
        """A box_data/box.yaml declaring a non-standalone mode is not a marker."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = tmp_home / "project"
        (project_dir / "box_data").mkdir()
        (project_dir / "box_data" / BOX_META_FILE).write_text(
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
        (tmp_home / "box_data" / BOX_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
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

        from kanibako.project.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        result = detect_project_mode(ws_root.resolve(), std, config)
        assert result.mode is BoxMode.named

    def test_workset_detected_from_subdirectory_of_root(self, config_file, tmp_home):
        """Detection from a subdirectory of workset root → workset mode."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.project.workset import create_workset
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
        from kanibako.project.workset import create_workset
        config = load_config(config_file)
        std = load_std_paths(config)

        ws_root = tmp_home / "worksets" / "myws"
        create_workset("myws", ws_root, std)

        # A standalone box dropped INSIDE the workset tree (its own box_data/
        # marker dir + a ROOT workset.yaml).
        inner = ws_root / "innerstand"
        (inner / "box_data").mkdir(parents=True)
        (inner / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
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
        from kanibako.project.workset import create_workset
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
        from kanibako.project import registry_store
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)

        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)

        # A standalone box at an EXTERNAL dir (outside the workset tree): the
        # in-place marker (the root workset.yaml registry null) plus a global
        # standalone: registration (its pre-connect resolved state).
        external = (tmp_home / "standalone_box").resolve()
        (external / "box_data").mkdir(parents=True)
        (external / WORKSET_META_FILE).write_text("workset:\n  registry: null\n")
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

        with pytest.raises(ProjectError, match="Refusing to create project rooted at .HOME"):
            resolve_project(std, config, project_dir=str(home), initialize=True)

    def test_home_guard_allows_existing_project(
        self, config_file, tmp_home, credentials_dir,
    ):
        """Pre-created project at $HOME → no error (project_dir_path.is_dir() is True)."""
        from kanibako.project import workset_registry

        config = load_config(config_file)
        std = load_std_paths(config)
        home = tmp_home / "home"

        # Pre-create the project via a DIRECT PRIMARY-membership write (bypassing
        # the $HOME guard, which register_primary_box_name enforces) — simulates a
        # box registered before the $HOME guard existed.
        prim_reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, None,
            early=_early_scope(std, BoxMode.primary),
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
        (project_dir / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
        )

        proj = resolve_any_project(std, config, project_dir=str(project_dir), initialize=False)

        assert proj.mode is BoxMode.standalone
        # Drift I: metadata_path is the ROOT (workset.yaml lives there).
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

        from kanibako.project.workset import add_project, create_workset
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

        from kanibako.project.workset import add_project, create_workset
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

        from kanibako.project.workset import add_project, create_workset
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

        from kanibako.project.workset import create_workset
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
        (project_dir / WORKSET_META_FILE).write_text(
            'project:\n  mode: "standalone"\nworkset:\n  registry: null\n'
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

        from kanibako.project.workset import add_project, create_workset
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

        from kanibako.project.workset import create_workset
        ws_root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", ws_root, std)

        found_ws, found_name = _find_workset_for_path(ws_root.resolve(), std)
        assert found_ws.name == "my-set"
        assert found_name is None

    def test_find_workset_for_path_subdir_of_root_returns_none_project(self, config_file, tmp_home):
        """Path in a non-workspaces subdirectory of workset root returns None."""
        config = load_config(config_file)
        std = load_std_paths(config)

        from kanibako.project.workset import create_workset
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

        from kanibako.project.workset import add_project, create_workset
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

        from kanibako.project.workset import add_project, create_workset
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
        from kanibako.project.workset import add_project, create_workset
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
        from kanibako.project.workset import add_project, create_workset
        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        return config, std, ws, external

    def _boxes(self, ws, std):
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.named, ws.name),
        )
        return workset_registry.load_workset_boxes(registry_path)

    def test_connect_registers_external_in_per_workset_boxes(
        self, config_file, tmp_home
    ):
        """Test 1 — connect records the box in the workset's ``boxes:`` with the
        EXTERNAL path.  Mutation: skip the per-workset registration in add_project
        → this assert goes RED."""
        _config, std, ws, external = self._setup(config_file, tmp_home)
        assert self._boxes(ws, std).get("extproj") == str(external)

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
        from kanibako.project import registry_store
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
        from kanibako.project.workset import add_project, create_workset
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
        from kanibako.project import workset_registry
        from kanibako.launch import box_resolve
        from kanibako.settings.config_io import load_doc
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.project.workset import create_workset
        ws = create_workset("mix", tmp_home / "worksets" / "mix", std)
        internal = ws.workspaces_dir / "inbox"
        internal.mkdir(parents=True)
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.named, ws.name),
        )
        workset_registry.register_workset_box(registry_path, "inbox", internal)
        assert box_resolve.find_connected_external_box(internal, std) is None

    def test_in_tree_box_unaffected(self, config_file, tmp_home):
        """Test 6 — a normal INTERNAL workset box resolves to its layout
        workspace (regression guard: the per-workset scan does not hijack it)."""
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.project.workset import add_project, create_workset
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


class TestA0RepointStrandedMembers:
    """Bifrost A0 (2026-08-02, B2-ii-b): an absolute ``workset.workspaces``
    repoint must NOT strand members registered under the OLD composition.

    The per-workset registry's ``boxes:`` membership is the SOLE authoritative
    name → workspace store: resolution trusts the REGISTERED path wherever a
    composition epoch put it, never re-deriving membership from the CURRENT
    composition.  The live defect: after the repoint, a pre-repoint in-tree
    member vanished from ``list``, was unresolvable by bare name, and
    unresolvable from INSIDE its own workspace dir ("Inside workset ... but not
    in a specific project workspace") — data intact, box invisible.
    """

    def _setup(self, config_file, tmp_home):
        """Named workset + IN-TREE member under the DEFAULT composition,
        registered in ``boxes:`` (the first-start registration), THEN an
        absolute ``workset.workspaces`` repoint to a dir outside the root
        (the bifrost B2-ii shape)."""
        from kanibako.project import workset_registry
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config_io import dump_doc, load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("b2ws", tmp_home / "worksets" / "b2ws", std)
        internal = (ws.root / "workspaces" / "boxa").resolve()
        add_project(ws, "boxa", internal, std)
        # The first-start membership registration (in-tree members register at
        # first launch; connect registers external ones at connect time).
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.named, ws.name),
        )
        workset_registry.register_workset_box(registry_path, "boxa", internal)
        # Absolute repoint AFTER the member exists (workset.yaml is created here
        # for the first time — a workset root has none until something is set).
        pods = (tmp_home / "b2pods").resolve()
        pods.mkdir()
        doc = load_doc(ws.root / WORKSET_META_FILE)
        doc.setdefault("workset", {})["workspaces"] = str(pods)
        dump_doc(ws.root / WORKSET_META_FILE, doc)
        return config, std, ws, internal

    def test_list_still_shows_stranded_member(self, config_file, tmp_home):
        """Surface 1 (``kanibako list``): workspace presence is checked at the
        REGISTERED path, so the member stays "ok" — not "missing" (which plain
        ``list`` hides).  Mutation: re-derive from ``ws.workspaces_dir`` → RED."""
        from kanibako.settings.paths import iter_workset_projects

        config, std, _ws, _internal = self._setup(config_file, tmp_home)
        rows = {name: plist for name, _w, plist in iter_workset_projects(std, config)}
        assert ("boxa", "ok") in rows["b2ws"]

    def test_bare_name_resolves_stranded_member(
        self, config_file, tmp_home, monkeypatch
    ):
        """Surface 2 (bare name from outside): ``box info boxa``-shape resolution
        lands on the REGISTERED workspace (pre-fix: the name resolved but the
        location attribution died on "not in a specific project workspace")."""
        config, std, _ws, internal = self._setup(config_file, tmp_home)
        monkeypatch.chdir(tmp_home)  # outside any workset
        proj = resolve_any_project(std, config, project_dir="boxa")
        assert proj.mode is BoxMode.named
        assert proj.name == "boxa"
        assert proj.project_path.resolve() == internal
        assert proj.group is not None and proj.group.name == "b2ws"

    def test_resolve_from_inside_stranded_workspace(self, config_file, tmp_home):
        """Surface 3 (cwd/location detection): resolving FROM the member's own
        workspace dir — the live failure was this exact call erroring with
        "Inside workset 'b2ws' but not in a specific project workspace"."""
        config, std, _ws, internal = self._setup(config_file, tmp_home)
        result = detect_project_mode(internal, std, config)
        assert result.mode is BoxMode.named
        proj = resolve_any_project(std, config, project_dir=str(internal))
        assert proj.name == "boxa"
        assert proj.project_path.resolve() == internal
        # A SUBDIR of the stranded workspace resolves too (ancestor walk).
        subdir = internal / "src"
        subdir.mkdir()
        proj2 = resolve_any_project(std, config, project_dir=str(subdir))
        assert proj2.name == "boxa"
        assert proj2.project_path.resolve() == internal

    def test_find_connected_external_box_matches_stranded_member(
        self, config_file, tmp_home
    ):
        """The narrowed skip: ONLY members under the CURRENT resolved
        workspaces dir are excluded from the registry reverse index; a stranded
        in-root member matches by its registered path.  Mutation: restore the
        root-wide skip → ``None`` → RED."""
        from kanibako.launch import box_resolve

        _config, std, _ws, internal = self._setup(config_file, tmp_home)
        owned = box_resolve.find_connected_external_box(internal, std)
        assert owned is not None
        assert owned.box_name == "boxa"
        assert owned.workset_name == "b2ws"
        assert Path(owned.box_path).resolve() == internal

    def test_resolve_workset_project_uses_registered_path(
        self, config_file, tmp_home
    ):
        """The workspace override sources the REGISTERED path by NAME — never
        the CURRENT composition (``<pods>/boxa``)."""
        from kanibako.project.workset import load_workset

        config, std, ws, internal = self._setup(config_file, tmp_home)
        ws_reloaded = load_workset(ws.root, ws.name, early_system=std.early_system)  # captures the repoint
        proj = resolve_workset_project(
            WorksetSpec.from_workset(ws_reloaded), "boxa", std, config,
            initialize=False,
        )
        assert proj.project_path.resolve() == internal
        assert proj.project_path != ws_reloaded.workspaces_dir / "boxa"

    def test_current_composition_member_still_skipped_by_external_index(
        self, config_file, tmp_home
    ):
        """Keep-green guard: a member under the CURRENT resolved workspaces dir
        stays owned by ordinary location detection — the narrowed skip still
        excludes it from the reverse index (Test 5/6 semantics, post-repoint)."""
        from kanibako.launch import box_resolve
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        _config, std, ws, _internal = self._setup(config_file, tmp_home)
        pods_member = (tmp_home / "b2pods" / "boxb").resolve()
        pods_member.mkdir()
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.named, ws.name),
        )
        workset_registry.register_workset_box(registry_path, "boxb", pods_member)
        assert box_resolve.find_connected_external_box(pods_member, std) is None


# ---------------------------------------------------------------------------
# P5a — create-then-resolve round-trip + create dual-register (new registries)
# ---------------------------------------------------------------------------

class TestP5aCreateThenResolve:
    """The core P5a contract: box create dual-registers into the new registries,
    and the new-model identity derivation (``box_resolve``) reads mode / name /
    workspace / registered back correctly for primary / named / standalone."""

    @staticmethod
    def _primary_registry(std):
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        return workset_registry.resolve_workset_registry_path(
            std.primary_workset,
            load_doc(std.primary_workset / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.primary),
        )

    def test_primary_create_registers_and_resolves(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.project import workset_registry
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
        from kanibako.project import workset_registry
        from kanibako.launch import box_resolve
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import WorksetSpec, resolve_workset_project
        from kanibako.project.workset import add_project, create_workset
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
            ws.root, load_doc(ws.root / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.named, ws.name),
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
        from kanibako.project import registry_store
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
        # survive.  (The resolver sources this from the box's own box.yaml,
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
        (P2/P5a).  A box.yaml with box.enable_vault=False but NO project.mode
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
        assert proj2.vault_enabled() is False

    def test_resolution_defers_and_caches_the_vault_cascade(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        from kanibako.settings import paths as paths_mod

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)
        calls = []
        monkeypatch.setattr(
            paths_mod,
            "resolve_box_enable_vault",
            lambda *args, **kwargs: calls.append((args, kwargs)) or False,
        )

        proj = resolve_project(std, config, project_dir=project_dir, initialize=False)
        assert calls == []
        assert proj.vault_enabled() is False
        assert len(calls) == 1
        assert proj.vault_enabled() is False
        assert len(calls) == 1

    def test_an_explicit_vault_value_skips_the_cascade_at_create(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        from kanibako.settings import paths as paths_mod

        config = load_config(config_file)
        std = load_std_paths(config)
        monkeypatch.setattr(
            paths_mod,
            "resolve_box_enable_vault",
            lambda *args, **kwargs: pytest.fail("explicit create read the cascade"),
        )

        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
            enable_vault=False,
        )
        assert proj.vault_enabled() is False

    def test_iter_projects_prefers_registry_over_settings(
        self, config_file, tmp_home, credentials_dir
    ):
        """iter_projects sources a box's workspace from the PRIMARY per-workset
        registry FIRST (the new-model source), only falling back to box.yaml.
        (Mutation target: neuter the registry read → this returns the settings
        workspace instead → RED.  Covers the otherwise-vacuous new branch.)"""
        from kanibako.project import workset_registry
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import iter_projects
        config = load_config(config_file)
        std = load_std_paths(config)

        # A primary box dir whose box.yaml workspace is path A (a legacy
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
            load_doc(std.primary_workset / WORKSET_META_FILE),
            early=_early_scope(std, BoxMode.primary),
        )
        workset_registry.register_workset_box(reg_path, "mybox", registry_ws)

        results = dict(iter_projects(std, config))
        # The registry value WINS over the box.yaml workspace.
        assert results[box_dir] == registry_ws
        assert results[box_dir] != settings_ws

    def test_iter_projects_unregistered_box_yields_none(
        self, config_file, tmp_home, credentials_dir
    ):
        """P8a: a box dir absent from the PRIMARY registry yields ``None`` — the
        transitional box.yaml ``resolved.workspace`` + ``project-path.txt``
        breadcrumb fallbacks are DROPPED.  (Mutation target: re-add a
        box.yaml-workspace fallback → this box would list ``settings_ws``
        instead of ``None`` → RED.)"""
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import iter_projects
        config = load_config(config_file)
        std = load_std_paths(config)

        # A primary box dir with a legacy box.yaml workspace but NO registry
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


class TestMissingVaultAdvisoryIsGuarded:
    """2R / N-b — the missing-vault advisory is LAZY and GUARDED, never skipped.

    "Only warn when the value is already known" would DELETE the advisory: at
    ``resolve_box_target`` time nothing has computed the vault flag yet, so such a rule
    could never fire — a capability removed to satisfy a design.  So the advisory asks,
    and if asking raises the §0 refusal it prints nothing, because it has no answer to
    advise on.  Whether the VERB stops is the verb's own read, never this one's.
    """

    def test_a_refusing_cascade_does_not_make_the_advisory_fatal(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        from kanibako.settings import paths as paths_mod
        from kanibako.settings.config import load_config
        from kanibako.settings.settings_resolve import SettingsError

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        def _refuse(*args, **kwargs):
            raise SettingsError("§0: undeclared key in this box's files")

        monkeypatch.setattr(paths_mod, "resolve_box_enable_vault", _refuse)

        # warn=True is the advisory path — every one of the 12 src callers passes it.
        assert paths_mod.resolve_box_target(
            std, config, project_dir, initialize=False,
        ) is not None

    def test_a_quiet_resolve_runs_no_advisory_and_so_no_cascade(
        self, config_file, tmp_home, credentials_dir, monkeypatch,
    ):
        from kanibako.settings import paths as paths_mod
        from kanibako.settings.config import load_config

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        calls: list = []
        monkeypatch.setattr(
            paths_mod, "resolve_box_enable_vault",
            lambda *a, **k: calls.append((a, k)) or False,
        )
        assert paths_mod.resolve_box_target(
            std, config, project_dir, initialize=False, warn=False,
        ) is not None
        assert calls == [], f"a warn=False resolve still ran the advisory ({len(calls)}x)"


class TestP5aStandalonePresenceSwitch:
    """Mutation proof for the _is_standalone_meta_dir presence switch (site
    1306): detection is by the root workset.yaml's own stored ``workset.registry`` null,
    no longer by a stored box.mode == "standalone" field."""

    def test_presence_detects_without_mode_field(self, tmp_home):
        from kanibako.settings.config import dump_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, _is_standalone_meta_dir
        root = tmp_home / "box"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        # A workset.yaml with NO project.mode = "standalone" declaration.  The
        # OLD field-reading impl returned False here; the presence impl → True.
        dump_doc(root / WORKSET_META_FILE, {"box": {"image": "x"}, "workset": {"registry": None}})
        assert _is_standalone_meta_dir(root) is True

    def test_missing_settings_is_not_standalone(self, tmp_home):
        from kanibako.settings.paths import STANDALONE_META_DIR, _is_standalone_meta_dir
        root = tmp_home / "box"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        # box_data/ present but NO workset.yaml → not a standalone marker.
        assert _is_standalone_meta_dir(root) is False

    def test_settings_without_the_registry_null_is_not_standalone(self, tmp_home):
        from kanibako.settings.config import dump_doc
        from kanibako.settings.paths import _is_standalone_meta_dir
        root = tmp_home / "box"
        root.mkdir()
        dump_doc(root / WORKSET_META_FILE, {"box": {"image": "x"}})
        # workset.yaml present but NO stored ``workset.registry`` null → not standalone.
        assert _is_standalone_meta_dir(root) is False


class TestBoxWorksetSettingsPaths:
    """P2/M-8: the mode-aware (box_tier, workset_tier) settings-file pair
    (``box_workset_settings_paths``) — the SINGLE SOURCE for READ, WRITE and the
    ``meta.box.settings`` ANCHOR.  ``meta.box.settings`` is the UNIFORM
    ``@meta.box.path/box.yaml`` in EVERY mode (spec §2c ALL PROJECTS)."""

    def _proj(self, tmp_path: Path, *, mode: "BoxMode", group):
        from kanibako.settings.paths import ProjectPaths
        from kanibako.settings.workset_dirkeys import EarlyScope, EarlySystem
        from kanibako.channels.channels import (
            WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE,
        )

        meta = tmp_path / "meta"
        # ⚑ The scope a resolver would have held: the STANDALONE box tier is answered
        # through ``workset.boxes``, so a hand-built ``ProjectPaths`` carries one too.
        token = WS_TOKEN_STANDALONE if mode is BoxMode.standalone else WS_TOKEN_PRIMARY
        return ProjectPaths(
            project_path=meta / "workspace",
            project_hash="h",
            metadata_path=meta,
            shell_path=meta / "boxes" / "b" / "home",
            vault_ro_path=meta / "vault" / "ro" / "b",
            vault_rw_path=meta / "vault" / "rw" / "b",
            mode=mode,
            group=group,
            _early=EarlyScope(
                EarlySystem(tier={}, file=meta / "workset.yaml", system_paths={}),
                token,
            ),
        )

    def test_standalone_box_tier_is_the_box_data_settings_file(self, tmp_path: Path):
        """STANDALONE gains a real BOX TIER at ``box_data/box.yaml``
        (``settings-keyspace-1.8.0.md`` §2c ALL PROJECTS + ``system-design-1.8.0.md``
        § "Detection & import"); the ROOT file keeps playing the WORKSET tier.  (Mutation:
        reverting the standalone arm to ``None`` → RED.)"""
        from kanibako.settings.paths import BoxMode, box_workset_settings_paths

        proj = self._proj(tmp_path, mode=BoxMode.standalone, group=None)
        box_tier, ws_tier = box_workset_settings_paths(proj)
        # GOLDEN: the two on-disk names are spelled as literals here on purpose —
        # this pair must go RED if either tier's filename ever moves again.
        assert box_tier == proj.metadata_path / "box_data" / "box.yaml"
        assert ws_tier == proj.metadata_path / "workset.yaml"

    def test_standalone_box_tier_lives_under_the_box_data_marker(self, tmp_path: Path):
        """The two tiers are the two SPEC positions, not two arbitrary files: the box
        tier sits inside ``box_data/`` (= ``@meta.box.path``) and the workset tier is
        the ROOT file (= the file DETECTION reads, ``system-design-1.8.0.md``
        § "Detection & import").  (Mutation: swapping the
        returned pair → RED.)"""
        from kanibako.settings.paths import (
            STANDALONE_META_DIR,
            BoxMode,
            box_workset_settings_paths,
        )

        proj = self._proj(tmp_path, mode=BoxMode.standalone, group=None)
        box_tier, ws_tier = box_workset_settings_paths(proj)
        assert box_tier.parent.name == STANDALONE_META_DIR
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
            assert box_tier.name == "box.yaml", mode

    def test_primary_named_pair_unchanged_vs_pre_p6c(self, tmp_path: Path):
        # BYTE-IDENTITY (equivalence bar): for primary/named the pair MUST equal the
        # pre-P6c computation (box's own box.yaml, workset_settings_path(group)).
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
    """``system-design-1.8.0.md`` § "Detection & import": STANDALONE detection =
    the stored ``workset.registry`` null in the ROOT
    ``workset.yaml`` (the WORKSET-tier file).  P2 introduces a BOX-tier file at
    ``box_data/box.yaml``; detection must NOT come to depend on it, or the
    ancestor-walk that finds a standalone project at all would break."""

    def test_root_file_alone_detects_without_a_box_tier_file(self, tmp_home):
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / WORKSET_META_FILE, {"workset": {"kuid": "abcde", "registry": None}})
        # No box_data/box.yaml at all — the ABSENT-BY-DEFAULT shape.
        assert not (root / STANDALONE_META_DIR / BOX_META_FILE).exists()
        assert _is_standalone_meta_dir(root) is True

    def test_box_tier_file_alone_is_not_a_standalone_marker(self, tmp_home):
        """⚑ THE mutation guard for "do not unify detection".  A Writer tidying the
        two settings paths into one would point detection at ``box_data/box.yaml``
        — and this box, which has NO root file, would start being detected → RED."""
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / STANDALONE_META_DIR / BOX_META_FILE, {"box": {"image": "x"}})
        assert not (root / WORKSET_META_FILE).exists()
        assert _is_standalone_meta_dir(root) is False

    def test_both_files_present_still_detects(self, tmp_home):
        """The new box tier does not DISTURB detection either — presence of both is
        the normal post-``config set`` shape."""
        from kanibako.settings.config import BOX_META_FILE, dump_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, _is_standalone_meta_dir

        root = tmp_home / "sa"
        (root / STANDALONE_META_DIR).mkdir(parents=True)
        dump_doc(root / WORKSET_META_FILE, {"workset": {"kuid": "abcde", "registry": None}})
        dump_doc(root / STANDALONE_META_DIR / BOX_META_FILE, {"box": {"image": "x"}})
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
            STANDALONE_META_DIR,
            resolve_standalone_project,
        )

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)

        # create wrote the kuid to the ROOT file, and NOT to the box tier.
        assert read_workset_kuid(root / WORKSET_META_FILE) != "00000"
        assert read_workset_kuid(
            root / STANDALONE_META_DIR / BOX_META_FILE
        ) == "00000"


class TestStandaloneEnableVaultTier:
    """``box.enable_vault`` resolves through the CASCADE — base < system < workset < box.

    ⚑ IT DID NOT UNTIL 2026-08-29.  The value came from ``config.read_box_enable_vault``,
    which opened exactly two files (the box tier, then the workset tier as an R2
    downward-default) — so P2's tier move was handled at the READER, and the BASE and
    SYSTEM levels were dropped without a word.  The cases below keep every answer that
    arrangement gave, because those answers were right; what is added is the two levels it
    could not see.
    """

    def _standalone(self, config_file, tmp_home, *, box=None, root_extra=None):
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)
        if root_extra is not None:
            doc = load_doc(root / WORKSET_META_FILE)
            doc.setdefault("box", {}).update(root_extra)
            dump_doc(root / WORKSET_META_FILE, doc)
        box_file = root / STANDALONE_META_DIR / BOX_META_FILE
        if box is not None:
            doc = load_doc(box_file)
            doc.setdefault("box", {}).update(box)
            dump_doc(box_file, doc)
        else:
            box_file.unlink(missing_ok=True)
        return resolve_standalone_project(
            std, config, str(root), initialize=False,
        ).vault_enabled()

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

    def test_primary_inherits_a_workset_tier_value(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The R2 workset fallback reaches PRIMARY too (2026-08-26).

        ⚑ This test formerly pinned the OPPOSITE — a workset-tier ``box.enable_vault``
        was inert for primary — and said so explicitly: "a real defect, tracked
        separately.  Pinned so extending the fallback is a DELIBERATE change, not an
        accidental one."  This IS that deliberate change.  The keyspec settles it:
        §2c gives PRIMARY and NAMED the same ``meta.workset.settings``, and §0
        "Directional view/set across CONTAINMENT levels" makes a ``box.*`` key stored
        at a containing scope an OVERRIDABLE DEFAULT.  The tripwire is kept, pointed
        the other way: reverting the fallback must be deliberate too.
        """
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
        )
        ws_file = std.primary_workset / WORKSET_META_FILE
        doc = load_doc(ws_file)
        doc.setdefault("box", {})["enable_vault"] = False
        dump_doc(ws_file, doc)
        (proj.metadata_path / BOX_META_FILE).unlink(missing_ok=True)

        proj2 = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=False,
        )
        assert proj2.vault_enabled() is False

    @staticmethod
    def _write_system_tier(std, value: bool) -> None:
        """Store ``box.enable_vault`` where ``kanibako system set`` stores it."""
        from kanibako.settings.config_io import dump_doc

        std.settings.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(std.settings, {"box": {"enable_vault": value}})

    def test_a_system_tier_value_reaches_a_primary_box(
        self, config_file, tmp_home, credentials_dir,
    ):
        """⚑⚑ THE CASE THAT DID NOT EXIST WHILE THE BUG DID (2026-08-29).

        ``kanibako system set box.enable_vault=false`` returns 0, writes
        ``@config.settings``, and ``system get`` echoes it back — and until this date
        every box still came up with the vault created and mounted, because the value was
        read from two files that are not the system tier.  Two carriers disagreeing at a
        level the spec sanctions (``config_keys`` lets SYSTEM write ``box.*``).

        ⚑ ANTI-VACUITY: the box tier is deliberately left unwritten.  A sparse create
        persists nothing for a default-valued ``box.enable_vault``, so the system tier
        below is the ONLY carrier of the ``False`` — there is no second file that could
        be supplying it.
        (Mutation: restore the two-file read in ``resolve_box_enable_vault`` → True → RED.)
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_system_tier(std, False)

        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
        )
        assert proj.vault_enabled() is False
        assert not proj.vault_rw_path.is_dir(), (
            "the vault was materialized for a box whose resolved box.enable_vault is False"
        )

    def test_the_box_tier_still_beats_the_system_tier(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The new level is a LEVEL, not an override: ``… < system < workset < box``."""
        from kanibako.settings.config_io import dump_doc

        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_system_tier(std, False)

        proj = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=True,
        )
        dump_doc(proj.metadata_path / BOX_META_FILE, {"box": {"enable_vault": True}})

        proj2 = resolve_project(
            std, config, project_dir=str(tmp_home / "project"), initialize=False,
        )
        assert proj2.vault_enabled() is True

    def test_a_system_tier_value_reaches_a_standalone_box(
        self, config_file, tmp_home, credentials_dir,
    ):
        """A lone box has no box tier written either, and the system level still reaches it.

        ⚑ The standalone arm is asserted separately BECAUSE its two files are different
        ones (``box_data/box.yaml`` + the ROOT ``workset.yaml``), and the defect was in
        which files got opened.
        """
        from kanibako.settings.paths import resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        self._write_system_tier(std, False)

        root = tmp_home / "sa-system-tier"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        assert proj.vault_enabled() is False


def _code_string_literals(path: Path):
    """Yield ``(lineno, value)`` for every CODE string literal in *path*.

    Comments do not exist in the AST at all; a docstring is identified positionally, as
    the first statement of a module, class or function.  Both are therefore excluded BY
    CONSTRUCTION rather than by name — prose carries no value, so only what this yields
    can make a second carrier of one.

    ⚑ Shared by the source tripwires below so this module's copies are ONE: a second walk
    here would be a second place to get the positional test subtly wrong.  (Two more live
    in ``test_plugin_store_isolation`` and ``test_agent_file_boundary``; consolidating all
    three is its own change.)
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef)) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.lineno, node.value


class TestPathLeafDefaultsHaveOneCarrier:
    """⚑⚑ A default is materialized in EXACTLY ONE place.  ``project/workset.py``
    used to re-spell four of these as its own literals, which is two carriers of one
    value and a drift waiting to happen: change ``BOXES_PATH`` and the workset
    module would have kept stamping the old leaf.

    ⚑ ONE carrier per VALUE — not one carrier FILE.  There are two designated
    carriers and the split is structural, so do not "fix" it by merging them:

    * ``settings/bootstrap.py`` — the path LITERALS, leaf and absolute alike.  Like
      ``messages`` beside it (pinned by :meth:`test_messages_imports_only_the_terminal_leaf`)
      it must import nothing at all:
      that is what lets ``project/workset.py`` import them while ``settings/paths.py``
      imports ``project/workset.py``.  Moving anything into either that needs an import
      closes that documented cycle.
    * ``settings/config.py`` — the per-tier settings FILENAMES (``box.yaml``,
      ``workset.yaml``, ``agent.yaml``), which sit beside the readers and writers
      that use them and which reach ``config_io``.  Relocating them into the leaf
      would either drag that import in or strand them from their callers.

    Each value still has exactly one home; the tripwires below enforce that per
    carrier."""

    def test_workset_module_agrees_with_the_defaults_file(self):
        """The workset module's names carry the defaults file's VALUES.

        ⚑ This pins AGREEMENT, not single-carrier-ness, and it cannot pin the latter:
        CPython INTERNS these short identifier-like strings, so ``is`` is True even for
        two separately-written literals — an identity assertion here would pass against
        the very duplication it looks like it forbids.  The source tripwire below is
        what actually detects a second spelling.
        """
        from kanibako.settings import config as config_mod
        from kanibako.project import workset as ws_mod

        # The workset settings FILENAME is no longer a path-leaf default: the
        # per-tier rename gave it one carrier in ``settings/config.py``, which the
        # workset module imports rather than re-spelling.
        assert ws_mod.WORKSET_META_FILE is config_mod.WORKSET_META_FILE
        assert ws_mod.BOXES_DIR_NAME == bootstrap.BOXES_PATH
        assert ws_mod._LOGS_LEAF == bootstrap.LOGS_PATH
        assert ws_mod._VAULT_LEAF == bootstrap.VAULT_PATH
        assert ws_mod._WORKSPACES_LEAF == bootstrap.WORKSPACES_PATH
        assert ws_mod._STANDALONE_WORKSPACE_LEAF == bootstrap.WORKSPACE_PATH
        assert ws_mod._CHANNELROOT_LEAF == bootstrap.CHANNELS_PATH

    def test_no_second_spelling_of_a_leaf_in_the_workset_module(self):
        """Tripwire: re-introducing any of these as a literal in ``project/workset.py``
        recreates the second carrier.  The defaults file is the ONLY place the string
        may appear."""
        import re

        from tests.support.repo import REPO_ROOT

        text = (REPO_ROOT / "src" / "kanibako" / "project" / "workset.py").read_text(
            encoding="utf-8",
        )
        # ⚑ A prose mention in a comment or docstring is fine — only a CODE literal makes
        # a carrier, so match the assignment and join forms, not every occurrence.
        for leaf in ("boxes", "logs", "vault", "workspaces", "workspace", "channels",
                     "workset.yaml"):
            bad = re.compile(r'=\s*"%s"|/\s*"%s"|"%s"\s*/' % (leaf, leaf, leaf))
            assert not bad.search(text), (
                f'literal "{leaf}" in project/workset.py; draw it from '
                f"settings.bootstrap instead"
            )

    def test_no_second_spelling_of_a_tier_settings_filename(self):
        """Tripwire: each per-tier settings FILENAME has ONE carrier —
        ``settings/config.py`` — and no other module may spell it in a path.

        ⚑ Scope is the WHOLE of ``src/kanibako``, not a module list.  The sibling
        tripwire above scans ``project/workset.py`` alone, and so could not see the
        seven literals that had accumulated in ``project/names.py``,
        ``launch/box_resolve.py`` and ``commands/box/_lifecycle.py``: a guard
        narrower than the defect class it is named for catches nothing new.  There is
        no exempt-module list here and there must not be one — the ONE file skipped
        is the carrier itself, located from where the constants are DEFINED, so the
        rule and its exclusion cannot drift apart (P13).

        Three things go unflagged, all BY CONSTRUCTION rather than by name — a
        name-keyed allowlist would hide the next finding behind whatever is in it:

        * COMMENTS AND DOCSTRINGS.  The scan walks the AST, where comments do not
          exist at all and a docstring is identified positionally.  Prose carries no
          value; only a code literal makes a carrier.
        * A literal that is not PATH-SHAPED.  The filename must be the final
          ``/``-segment, so ``"...(shell config, box.yaml, vault symlinks)"`` — help
          text naming the file in a sentence — is a mention, not a second carrier.
        * A literal containing WHITESPACE.  This currently excludes NOTHING (stated
          so it is not mistaken for a live exemption); it is here because the next
          error message that happens to END in a path would otherwise red this test
          and pressure the reader into the allowlist the paragraph above forbids.

        ⚑ The rule catches the filename ANYWHERE in a path, not only alone: it was
        ``settings/settings_launch.py``'s ``"@meta.workset.path/workset.yaml"`` — a
        spec formula with the leaf typed by hand — that a weaker equality-only form
        let through, while line 384 of that same module had been composing the
        agent-tier formula off the constant all along.

        ⚑ The CARRIER is checked to spell every needle before the tree is swept — the
        P15 reason the sibling below gives for its own check, which this case went
        without until 2026-09-19.  What it catches is the carrier keeping the CONSTANT
        while losing the LITERAL: composing ``BOX_META_FILE`` out of parts instead of
        spelling ``"box.yaml"`` leaves the sweep hunting a needle no file can match —
        measured green before this assertion and red after it.

        ⚑ RESIDUAL, AND THE SIBLING SHARES IT: the needles ARE the constants' current
        values, so RENAMING one moves the needle and the carrier's literal together.
        Both checks pass, and any module still spelling the OLD name goes unswept.
        Nothing here can see that; the renaming commit has to.
        """
        import inspect

        from kanibako.settings import config as config_mod
        from tests.support.repo import REPO_ROOT

        filenames = {config_mod.BOX_META_FILE, config_mod.WORKSET_META_FILE,
                     config_mod.AGENT_META_FILE}
        carrier = Path(inspect.getfile(config_mod)).resolve()
        src = REPO_ROOT / "src" / "kanibako"

        in_carrier = {value.rsplit("/", 1)[-1]
                      for _, value in _code_string_literals(carrier)
                      if value.split() == [value]}
        assert filenames <= in_carrier, (
            f"{carrier.name} no longer spells "
            f"{sorted(filenames - in_carrier)} as a code literal; this test's needles "
            f"come from it, so the sweep below would pass vacuously"
        )

        for path in sorted(src.rglob("*.py")):
            if path.resolve() == carrier:
                continue
            for lineno, value in _code_string_literals(path):
                if value.split() != [value]:
                    continue
                assert value.rsplit("/", 1)[-1] not in filenames, (
                    f"{path.relative_to(src.parent)}:{lineno} spells a per-tier "
                    f'settings filename in the literal "{value}"; compose it from '
                    f"settings.config ({carrier.name}) instead — that constant is "
                    f"its ONE carrier"
                )

    def test_no_second_spelling_of_a_bootstrap_path_literal(self):
        """Tripwire: the BOOTSTRAP FILES — the user config file, and the site directory
        with its two bases — have ONE carrier, ``settings/bootstrap.py``, and no other
        module may spell any of them.

        ⚑ Scope, the carrier skip and the three by-construction exclusions are the sibling
        tripwire's above, deliberately shared rather than restated: read
        :meth:`test_no_second_spelling_of_a_tier_settings_filename` for why there is no
        exempt-module list and why prose goes unflagged.  ONE thing differs here, and the
        values force it:

        * The three FILENAMES match on the final ``/``-segment, exactly as over there.
        * ``SITE_CONFIG_DIR`` CANNOT.  Its own final segment is the bare word
          ``kanibako``, so a segment test would either miss the directory outright or
          fire on every unrelated path that ends in it.  It matches as a SUBSTRING —
          which is also what catches it inside a composed ``/etc/kanibako/<file>``.

        ⚑ THIS IS OWED, NOT THEORETICAL.  ``runtime/baseline.py`` composed its overlay
        from a hand-typed ``"/etc/kanibako"`` for as long as the constant existed — the
        third of the three host-side spellings `[R157]` measures as one defect — and
        ``bootstrap``'s own promise that these live "here and NOWHERE ELSE" was prose
        until this assertion.

        ⚑ THE REMEDY DEPENDS ON THE SUBJECT, AND AN EXEMPTION IS NOT IT.  A HOST-side hit
        imports ``SITE_CONFIG_DIR``.  A GUEST-side one — an IN-IMAGE path that merely
        shares the spelling, as ``commands/image.py``'s ``/etc/kanibako/rig.yaml`` does —
        must NOT: `[R157]` holds the two INDEPENDENT, and an import would couple a
        container path to a host path.  🛑 Naming it in place does not work either — a
        guest constant is still a code literal outside the carrier, so THIS TEST REDS IT.
        The guest side needs its own DESIGNATED CARRIER, found the way the host one is:
        from where its constants are defined, never from a module list.  (Today every
        guest-side mention is prose, so no second carrier exists and nothing is exempted.)

        ⚑ The CARRIER is checked to trip every needle before the tree is swept, so a
        green run means the carrier was skipped and nothing else matched — never that the
        scan matched nothing at all (P15).
        """
        import inspect

        from tests.support.repo import REPO_ROOT

        carrier = Path(inspect.getfile(bootstrap)).resolve()
        filenames = {bootstrap.CONFIG_FILE, bootstrap.SITE_CONFIG_FILE,
                     bootstrap.SITE_SETTINGS_FILE}
        site_dir = bootstrap.SITE_CONFIG_DIR
        src = REPO_ROOT / "src" / "kanibako"

        def spellings(path):
            """The ``(lineno, value)`` literals in *path* that re-spell a bootstrap value."""
            return [(lineno, value) for lineno, value in _code_string_literals(path)
                    if value.split() == [value]
                    and (value.rsplit("/", 1)[-1] in filenames or site_dir in value)]

        assert filenames | {site_dir} <= {value for _, value in spellings(carrier)}, (
            f"{carrier.name} no longer spells all four bootstrap path literals as code; "
            f"this test's needles came from it, so the sweep below would pass vacuously"
        )

        offenders = [(path, lineno, value)
                     for path in sorted(src.rglob("*.py")) if path.resolve() != carrier
                     for lineno, value in spellings(path)]
        assert not offenders, "\n".join(
            f'{path.relative_to(src.parent)}:{lineno} hand-types a bootstrap path '
            f'literal in "{value}"; the HOST-side carrier is settings.bootstrap '
            f"({carrier.name}) — read this test's docstring if the path is GUEST-side"
            for path, lineno, value in offenders
        )

    def test_bootstrap_is_import_free(self):
        """⚑ The path-literal file must stay a LEAF, for the same reason its sibling does:
        ``settings/paths.py`` imports it and ``project/workset.py`` imports that, so any
        import added here can close the tree's documented cycle.  Its own docstring
        promises this; without an assertion the promise is prose only."""
        import re

        from tests.support.repo import REPO_ROOT

        text = (REPO_ROOT / "src" / "kanibako" / "settings" / "bootstrap.py"
                ).read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*(import|from)\s", text), (
            "settings/bootstrap.py grew an import; it is a pure-constant leaf"
        )

    def test_messages_imports_only_the_terminal_leaf(self):
        """⚑ ``settings/messages.py`` may import ``bootstrap`` AND NOTHING ELSE.

        ⚑⚑ THE INVARIANT IS NOT "no imports" — it is "nothing that can reach back".
        ``project/workset.py`` imports this file and ``settings/paths.py`` imports
        ``project/workset.py``, so an import here that leads anywhere closes the tree's
        documented cycle.  ``bootstrap`` cannot: it imports nothing at all, pinned by
        :meth:`test_bootstrap_is_import_free`, so it is a TERMINAL leaf and no path
        through it returns.  It carries ``RUN_USER_UID_PATH`` for the
        ``WARN_RUNDIR_UNUSABLE`` splice.
        🛑 Widening this to a second module needs that module proved terminal too.
        """
        import re

        from tests.support.repo import REPO_ROOT

        text = (REPO_ROOT / "src" / "kanibako" / "settings" / "messages.py"
                ).read_text(encoding="utf-8")
        imports = re.findall(r"(?m)^\s*(?:import|from)\s+(\S+)", text)
        assert imports == ["kanibako.settings.bootstrap"], (
            "settings/messages.py may import ONLY kanibako.settings.bootstrap "
            f"(a terminal leaf); found {imports}"
        )


class TestEarlySystemTierIsData:
    """S1 (plan decision 4): the EARLY SYSTEM TIER is read ONCE and carried as a record.

    Before S1, ``_path_tier_set_values`` read the settings file for the ``system:`` table and
    ``early_repoint`` opened it AGAIN for the ``workset.*`` keys, under ONE ``try`` that gave
    both reads the SAME tolerance answer.  S1 loads the document once, takes the early tier
    from it BEFORE the table read, and splits tolerance into the two arms that must never be
    confused:

      * a document that does NOT load drops BOTH the ``system:`` table AND the early tier;
      * a document that loads but whose ``system:`` table is refused drops ONLY the
        ``system:`` set-values, KEEPS the early tier, and records the refusal text.

    MUTATION: collapse the two arms back into one ``try`` and the null-``system.canon`` cases
    red, because the early tier would vanish along with the table.
    """

    # ── helpers ──────────────────────────────────────────────────────────────

    def _paths(self):
        """The user CONFIG file and the SYSTEM SETTINGS file Layer 1 points at."""
        from kanibako.settings.config import config_file_path, system_settings_path
        from kanibako.settings.paths import user_config_home, xdg

        return (
            config_file_path(user_config_home()),
            system_settings_path(),
            xdg("XDG_DATA_HOME", ".local/share"),
        )

    def _settings(self, text: str) -> Path:
        """Write the SYSTEM SETTINGS file where Layer 1 points, and return its path."""
        from kanibako.settings.config import system_settings_path

        sp = system_settings_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(text)
        return sp

    def _tier(self, *, tolerate: bool = False):
        """``load_system_tier`` against the isolated Layer-1 file."""
        from kanibako.settings.paths import load_system_tier

        cfg, _sp, data_home = self._paths()
        return load_system_tier(cfg, data_home=data_home, home=Path.home(),
                               tolerate_bad_settings=tolerate)

    def _std(self):
        """A real ``StandardPaths`` off an initialized (empty) Layer-1 file."""
        from kanibako.settings.config import config_file_path, load_config, write_global_config
        from kanibako.settings.paths import load_std_paths, user_config_home

        cfg = config_file_path(user_config_home())
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if not cfg.exists():
            write_global_config(cfg)
        return load_std_paths(load_config(cfg))

    def _count_reads(self, monkeypatch, target: Path) -> dict:
        """Count reads of *target*; ``load_doc`` reaches the file through ``Path.read_text``."""
        counts = {"n": 0}
        real = Path.read_text

        def spy(self, *a, **kw):
            if Path(self) == target:
                counts["n"] += 1
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", spy)
        return counts

    # ── the two tolerance arms ───────────────────────────────────────────────

    @pytest.mark.parametrize("text, arm", [
        ("- a\n- b\n", "the document does not load"),
        ("system:\n  cache: null\n", "it loads, the system: table is refused"),
    ])
    def test_strict_raises_in_both_arms(self, tmp_home, text, arm):
        """Neither arm is a silent anything on the strict read: both raise ``ConfigError``."""
        self._settings(text)
        with pytest.raises(ConfigError):
            self._tier()

    def test_a_document_that_does_not_load_empties_the_tier(self, tmp_home):
        """ARM 1 at the tier build: the file is indistinguishable from NO file at all.

        Nothing in it can be read, so BOTH the ``system:`` table AND the early tier are
        dropped, and there is no refusal text to carry.  This is the only arm that empties
        the tier, and it is what E2 review finding 2 pins.

        ⚑ Asserted on ``_path_tier_set_values``, the arm's OWN seam.  Through
        ``load_system_tier`` this still raises, because the four primary-root reads inside
        ``resolve_system_paths`` re-open the file unguarded -- that is exactly the second
        open S2a removes, and why the malformed-settings ``test_stop.py`` cases stay red
        until S2b rather than turning green here.
        """
        from kanibako.settings.paths import _path_tier_set_values, host_xdg_map

        cfg, _s, data_home = self._paths()
        kw = dict(data_home=data_home, home=Path.home(), xdg_vars=host_xdg_map(data_home))
        baseline, baseline_refusal = _path_tier_set_values(
            cfg, **kw, tolerate_bad_settings=True,
        )

        self._settings("- a\n- b\n")
        values, refusal = _path_tier_set_values(cfg, **kw, tolerate_bad_settings=True)

        assert refusal is None
        assert baseline_refusal is None
        assert values == baseline
        assert not any(k.startswith(("workset.", "system.")) for k in values)

    def test_a_refused_system_table_keeps_the_early_tier(self, tmp_home):
        """ARM 2 under tolerance: the table goes, the EARLY TIER STAYS, the refusal is kept.

        A null ``system.cache`` refuses the ``system:`` table (``system.canon`` would not: it
        sources a standard bind, so its null is admitted).  The early keys were read from
        the loaded document BEFORE that read, so they survive -- which is what lets the
        per-owner check (decision 9) still run, and a valid ``workset.registry`` still be read.
        """
        baseline, _ = self._tier(tolerate=True)   # NO settings file yet: the declared defaults
        self._settings(
            "system:\n  cache: null\n"
            "workset:\n  boxes: /srv/kb/{meta.workset.path}\n"
            "  registry: /srv/reg/@meta.workset.path/r.yaml\n",
        )

        resolved, rec = self._tier(tolerate=True)
        assert rec.tier["workset.boxes"] == "/srv/kb/{meta.workset.path}"
        assert rec.tier["workset.registry"] == "/srv/reg/@meta.workset.path/r.yaml"
        assert rec.system_refusal is not None
        assert "cache" in rec.system_refusal
        # The resolved-system carrier is EMPTY when the refusal is set.
        assert rec.system_paths == {}
        # The dropped table really is gone: system.cache falls back to its declared default.
        assert resolved["system.cache"] == baseline["system.cache"]
        # ...while the early tier the file DID state is live.
        assert resolved["_primary_boxes"] == Path(f"/srv/kb/{resolved['config.primary_workset']}")

    # ── the record's contents ────────────────────────────────────────────────

    def test_the_record_holds_the_raw_early_values_including_a_present_null(self, tmp_home):
        """``tier`` is the file's raw ``workset.*``: a present null is ``None``, an ABSENT
        key is not in the mapping at all, and ``file`` is the path a refusal names."""
        sp = self._settings("workset:\n  boxes: /srv/kb/{meta.workset.path}\n  logs: null\n")

        _resolved, rec = self._tier()
        assert rec.tier["workset.boxes"] == "/srv/kb/{meta.workset.path}"
        assert rec.tier["workset.logs"] is None
        assert "workset.registry" not in rec.tier
        assert rec.file == sp
        assert rec.system_refusal is None

    def test_system_paths_are_the_resolved_tier_built_after_the_resolve(self, tmp_home):
        """The record carries the RESOLVED ``system.*``, which is only possible because it is
        built AFTER the resolve -- not a second read of the stored expression."""
        self._settings(
            "system:\n  canon: '{config.data}/global/canon'\n"
            "workset:\n  boxes: /srv/kb/@meta.workset.path\n",
        )

        resolved, rec = self._tier()
        assert rec.system_paths["system.canon"].endswith("/kanibako/global/canon")
        assert "@" not in rec.system_paths["system.canon"]
        # The primary root came from the early tier, resolved after system.*.
        assert resolved["_primary_boxes"] == Path(f"/srv/kb/{resolved['config.primary_workset']}")

    def test_the_record_matches_system_path_floor_for_a_repointed_key(self, tmp_home):
        """``std.early_system.system_paths == system_path_floor(std)``, with one ``system.*``
        key repointed and no refusal -- the two carriers are ONE carrier."""
        self._settings("system:\n  canon: /srv/canon\n")
        std = self._std()

        from kanibako.settings.paths import system_path_floor

        assert std.early_system.system_refusal is None
        assert std.early_system.system_paths == system_path_floor(std)
        assert std.early_system.system_paths["system.canon"] == "/srv/canon"

    # ── one open ─────────────────────────────────────────────────────────────

    def test_the_tier_build_opens_the_settings_file_once(self, tmp_home, monkeypatch):
        """``_path_tier_set_values`` reads the settings document ONCE and feeds BOTH the
        ``system:`` table and the early tier from that one load.

        ⚑ Scoped to the tier build deliberately.  The four primary-root reads inside
        ``resolve_system_paths`` still open the file for themselves; taking the record there
        is S2a's change (decision 5), not this step's.
        """
        from kanibako.settings.paths import _path_tier_set_values, host_xdg_map

        sp = self._settings("workset:\n  boxes: /srv/kb\nsystem:\n  canon: /srv/canon\n")
        cfg, _s, data_home = self._paths()
        counts = self._count_reads(monkeypatch, sp)

        _values, _refusal = _path_tier_set_values(
            cfg, data_home=data_home, home=Path.home(), xdg_vars=host_xdg_map(data_home),
        )
        assert counts["n"] == 1, (
            f"the system settings file was read {counts['n']} times by the tier build; it "
            f"must open ONCE and feed both the system: table and the early tier"
        )

    # ── the one loader ───────────────────────────────────────────────────────

    def test_total_standalone_early_degrades_without_a_config(self, tmp_home, monkeypatch):
        """The STANDALONE scope for a reader that holds no ``StandardPaths`` NEVER raises.

        The plugin scan runs where the config is unavailable, so this arm must hand back a
        scope even when the tier load fails outright, and it must name the file whose load
        failed so a refusal it produced points at a real one.
        """
        import kanibako.settings.paths as paths_mod

        def boom(*_args, **_kwargs):
            raise OSError("no config here")

        monkeypatch.setattr(paths_mod, "load_system_tier", boom)
        scope = paths_mod.total_standalone_early()
        cfg, _settings, _data_home = self._paths()

        assert scope.system.file == cfg, "the refusal must name the file that failed"
        assert scope.system.tier == {}, "an unreadable config states no early tier"
        assert scope.system.system_paths == {}, "and resolves no system.* value"

    def test_load_system_config_is_load_system_tier_projected(self, tmp_home):
        """``load_system_config`` IS ``load_system_tier(...)[0]`` -- one loader, so the two
        can never disagree about what the files said."""
        from kanibako.settings.paths import load_system_config, load_system_tier

        self._settings(
            "workset:\n  boxes: /srv/kb/{meta.workset.path}\nsystem:\n  canon: /srv/canon\n",
        )
        cfg, _s, data_home = self._paths()
        kw = dict(data_home=data_home, home=Path.home())

        assert load_system_config(cfg, **kw) == load_system_tier(cfg, **kw)[0]
        for tolerate in (False, True):
            assert (
                load_system_config(cfg, tolerate_bad_settings=tolerate, **kw)
                == load_system_tier(cfg, tolerate_bad_settings=tolerate, **kw)[0]
            )


# ---------------------------------------------------------------------------
# A box-tier SHAPE refusal must land BEFORE the first-time setup at both create
# doors, so a refusal leaves nothing for the retry to trip over.
# ---------------------------------------------------------------------------


def _scalar_box_tier(metadata_path):
    """Write a scalar ``box`` into *metadata_path*'s own box tier, by hand.

    A settings file is a hand-edit surface, so a scalar section is a state the
    shape rule exists to judge; nothing sanctioned writes one.
    """
    metadata_path.mkdir(parents=True, exist_ok=True)
    toml = metadata_path / BOX_META_FILE
    toml.write_text("box: 42\n")
    return toml


def _spy_setup_and_shape_read(monkeypatch, setup_name, calls):
    """Record which of the shape read / the setup runs first, through both.

    *calls* collects one entry per call, in call order, and each wrapper calls
    through to the real function so the door behaves as it does in production.
    """
    import kanibako.settings.paths as paths_mod

    real_setup = getattr(paths_mod, setup_name)
    real_read = paths_mod.read_box_enable_vault

    def _setup(*args, **kwargs):
        calls.append(setup_name)
        return real_setup(*args, **kwargs)

    def _read(*args, **kwargs):
        calls.append("read_box_enable_vault")
        return real_read(*args, **kwargs)

    monkeypatch.setattr(paths_mod, setup_name, _setup)
    monkeypatch.setattr(paths_mod, "read_box_enable_vault", _read)


def _named_member_with_scalar_box(config_file, tmp_home):
    """A workset member whose box dir carries a scalar ``box`` and NO ``home/``.

    The half-built state the named door's own guard admits: it asks only
    whether ``home/`` is there, so the box tier may already exist.
    """
    from kanibako.project.workset import add_project, create_workset

    config = load_config(config_file)
    std = load_std_paths(config)
    ws = create_workset("door-ws", tmp_home / "worksets" / "door-ws", std)
    add_project(ws, "doormem", tmp_home / "doormem-ws")
    toml = _scalar_box_tier(ws.projects_dir / "doormem")
    return config, std, ws, toml


class TestBoxShapeRefusalPrecedesSetup:
    """Both create doors judge the box tier's SHAPE before they build anything."""

    def test_a_scalar_box_tier_refuses_the_named_door_with_no_shell_left_behind(
        self, config_file, tmp_home,
    ):
        """The named door refuses a scalar ``box`` with NOTHING of the setup on disk.

        ⚑ THE DOOR, not the reader called directly: ``resolve_workset_project`` is what
        ``box create`` and a launch both reach for a named member, and its guard admits a
        box tier that already exists while ``home/`` does not.  Leaving a bootstrapped
        ``home/`` behind is what makes the refusal unrecoverable by a plain retry.
        """
        from kanibako.errors import ConfigError

        config, std, ws, toml = _named_member_with_scalar_box(config_file, tmp_home)
        metadata_path = ws.projects_dir / "doormem"

        with pytest.raises(ConfigError) as exc:
            resolve_workset_project(WorksetSpec.from_workset(ws), "doormem", std, config,
                                    initialize=True)
        assert "holds 42 at 'box'" in str(exc.value)
        assert not (metadata_path / "home").exists()
        assert toml.read_text() == "box: 42\n"

    def test_the_shape_read_runs_before_the_named_door_setup(
        self, config_file, tmp_home, monkeypatch,
    ):
        """The named door asks the shape question FIRST, so a refusal skips the setup."""
        from kanibako.errors import ConfigError

        config, std, ws, _toml = _named_member_with_scalar_box(config_file, tmp_home)
        calls: list = []
        _spy_setup_and_shape_read(monkeypatch, "_init_workset_project", calls)

        with pytest.raises(ConfigError):
            resolve_workset_project(WorksetSpec.from_workset(ws), "doormem", std, config,
                                    initialize=True)
        # MUTATION: move ``_init_workset_project`` back above the read and this reds.
        assert calls == ["read_box_enable_vault"]

    def test_the_shape_read_runs_before_the_primary_door_setup(
        self, config_file, tmp_home, monkeypatch,
    ):
        """The primary door asks the same question first, through the same seam."""
        config = load_config(config_file)
        std = load_std_paths(config)
        project = tmp_home / "primarydoor"
        project.mkdir()
        calls: list = []
        _spy_setup_and_shape_read(monkeypatch, "_init_project", calls)

        proj = resolve_project(std, config, project_dir=str(project), initialize=True)

        assert proj.is_new is True
        # MUTATION: move ``_init_project`` back above the read and this reds.
        assert calls == ["read_box_enable_vault", "_init_project"]

    def test_the_primary_door_reads_a_box_tier_its_own_guard_proved_absent(
        self, config_file, tmp_home, monkeypatch,
    ):
        """The primary door's read is handed an ABSENT box tier, so it cannot refuse.

        ⚑ WHY THIS DOOR'S REFUSAL IS UNREACHABLE: the body runs only while
        ``project_dir_path`` does not exist, and the box tier is a file INSIDE it — so the
        read short-circuits on its absent-file arm, and now also sees that the parent it
        would have to be inside is absent too.  Ordering the read first is what makes both
        facts true at the moment of the read; a future widening of that guard is what
        would revive the refusal here.
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        project = tmp_home / "primarydoor2"
        project.mkdir()
        seen: list = []

        import kanibako.settings.paths as paths_mod

        real_read = paths_mod.read_box_enable_vault

        def _read(path, *args, **kwargs):
            seen.append((path, path.exists(), path.parent.is_dir()))
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(paths_mod, "read_box_enable_vault", _read)

        resolve_project(std, config, project_dir=str(project), initialize=True)

        assert len(seen) == 1
        toml, toml_exists, parent_exists = seen[0]
        assert toml.name == BOX_META_FILE
        assert toml_exists is False
        assert parent_exists is False

    def test_a_table_box_tier_still_completes_the_named_door_setup(
        self, config_file, tmp_home,
    ):
        """Anti-over-refusal at the same door: a real ``box`` table still builds the box.

        ⚑ Without this, a guard that refused every ``box.yaml`` would also satisfy the
        tests above, so the pair pins the refusal to the SHAPE.
        """
        config = load_config(config_file)
        std = load_std_paths(config)
        from kanibako.project.workset import add_project, create_workset

        ws = create_workset("table-ws", tmp_home / "worksets" / "table-ws", std)
        add_project(ws, "tablemem", tmp_home / "tablemem-ws")
        metadata_path = ws.projects_dir / "tablemem"
        metadata_path.mkdir(parents=True, exist_ok=True)
        (metadata_path / BOX_META_FILE).write_text("box:\n  enable_vault: false\n")

        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "tablemem", std,
                                       config, initialize=True)

        assert proj.is_new is True
        assert (metadata_path / "home").is_dir()
        # The read's answer is what the door PERSISTS, so pin the file it left.
        from kanibako.settings.config_io import load_doc

        assert load_doc(metadata_path / BOX_META_FILE)["box"]["enable_vault"] is False
