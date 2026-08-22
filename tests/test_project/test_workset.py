"""Tests for kanibako.project.workset -- working set data model and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import LegacyWorksetIdentityError, WorksetError
from kanibako.settings.paths import BoxMode
from kanibako.project.workset import (
    DEFAULT_WORKSET_ALIAS,
    DEFAULT_WORKSET_ID,
    add_project,
    create_workset,
    default_workset,
    delete_workset,
    list_worksets,
    load_workset,
    remove_project,
    resolve_workset_name,
)


# ---------------------------------------------------------------------------
# create_workset
# ---------------------------------------------------------------------------

class TestCreateWorkset:
    def test_creates_directory_structure(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        assert ws.name == "my-set"
        assert ws.root == root.resolve()
        assert ws.root.is_dir()
        assert (ws.root / "boxes").is_dir()
        assert (ws.root / "workspaces").is_dir()
        assert (ws.root / "vault").is_dir()
        assert ws.registry_path.is_file()
        # ⚑ A fresh workset root has NO settings.yaml — identity is registry-borne
        # and the settings file carries settings only, so it is created only if
        # something is actually SET.
        assert not ws.settings_path.exists()

    def test_writes_identity_to_the_registry(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        text = ws.registry_path.read_text()
        assert "name: my-set" in text
        assert "created:" in text

    def test_registers_globally(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        registry = list_worksets(std)
        assert "my-set" in registry
        assert registry["my-set"] == root.resolve()

    def test_sets_created_timestamp(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        assert ws.created  # non-empty
        assert "T" in ws.created  # looks like ISO 8601

    def test_duplicate_name_raises(self, std, tmp_home):
        root1 = tmp_home / "worksets" / "set1"
        create_workset("same-name", root1, std)

        root2 = tmp_home / "worksets" / "set2"
        with pytest.raises(WorksetError, match="already in use"):
            create_workset("same-name", root2, std)

    def test_duplicate_name_raises_even_with_force(self, std, tmp_home):
        # force overrides the CROSS-KIND check only; same-kind workset
        # uniqueness stays hard (spec §5 cross-kind name semantics).
        create_workset("same-name", tmp_home / "worksets" / "set1", std)

        with pytest.raises(WorksetError, match="already in use"):
            create_workset(
                "same-name", tmp_home / "worksets" / "set2", std, force=True,
            )

    def test_duplicate_name_message_explains_uniqueness(self, std, tmp_home):
        # The collision refusal must be explicit about the clash + uniqueness.
        create_workset("dup", tmp_home / "worksets" / "a", std)
        with pytest.raises(WorksetError, match="must be unique"):
            create_workset("dup", tmp_home / "worksets" / "b", std)

    def test_existing_root_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "existing"
        root.mkdir(parents=True)

        with pytest.raises(WorksetError, match="already exists"):
            create_workset("existing", root, std)

    def test_empty_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "empty-name"
        with pytest.raises(WorksetError, match="must not be empty"):
            create_workset("", root, std)

    def test_reserved_alias_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "default"
        with pytest.raises(WorksetError, match="reserved"):
            create_workset("default", root, std)

    def test_reserved_id_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "default-id"
        with pytest.raises(WorksetError, match="reserved"):
            create_workset("__default__", root, std)

    def test_reserved_primary_sentinel_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "primary-sentinel"
        with pytest.raises(WorksetError, match="reserved"):
            create_workset("__PRIMARY__", root, std)

    def test_reserved_standalone_sentinel_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "standalone-sentinel"
        with pytest.raises(WorksetError, match="reserved"):
            create_workset("__STANDALONE__", root, std)


# ---------------------------------------------------------------------------
# default_workset / resolve_workset_name
# ---------------------------------------------------------------------------

class TestDefaultWorkset:
    def test_synthesized_identity(self, std):
        ws = default_workset(std)
        assert ws.is_default is True
        assert ws.name == DEFAULT_WORKSET_ID
        # F4 (spec §2c): the PRIMARY workset roots at @config.primary_workset,
        # so its settings/env files derive from root like any named workset's.
        assert ws.root == std.primary_workset

    def test_mirrors_names_projects(self, std, tmp_home):
        from kanibako.settings.paths import register_primary_box_name

        proj_a = tmp_home / "proj_a"
        proj_b = tmp_home / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        # default_workset synthesizes members from the PRIMARY membership (the
        # sole store since the global ``projects:`` section retired).
        register_primary_box_name(std.primary_workset, std.registry, "alpha", str(proj_a))
        register_primary_box_name(std.primary_workset, std.registry, "beta", str(proj_b))

        ws = default_workset(std)
        by_name = {p.name: p.source_path for p in ws.projects}
        assert by_name == {"alpha": proj_a, "beta": proj_b}

    def test_not_persisted(self, std):
        # Synthesizing the default workset must not create a registry or
        # a workset.yaml.
        default_workset(std)
        assert not (std.data_path / "worksets.yaml").exists()
        assert not (std.data_path / "workset.yaml").exists()
        assert DEFAULT_WORKSET_ID not in list_worksets(std)
        assert DEFAULT_WORKSET_ALIAS not in list_worksets(std)


class TestResolveWorksetName:
    def test_alias_resolves_to_default(self, std):
        ws = resolve_workset_name(DEFAULT_WORKSET_ALIAS, std)
        assert ws.is_default and ws.name == DEFAULT_WORKSET_ID

    def test_id_resolves_to_default(self, std):
        ws = resolve_workset_name(DEFAULT_WORKSET_ID, std)
        assert ws.is_default and ws.name == DEFAULT_WORKSET_ID

    def test_named_workset_resolves(self, std, tmp_home):
        root = tmp_home / "worksets" / "real"
        create_workset("real", root, std)
        ws = resolve_workset_name("real", std)
        assert ws.name == "real"
        assert ws.is_default is False

    def test_unknown_name_raises(self, std):
        with pytest.raises(WorksetError, match="not registered"):
            resolve_workset_name("nope", std)

    def test_noun_scoped_lookup_of_shadowed_name_returns_workset_no_warn(
        self, std, tmp_home, caplog: pytest.LogCaptureFixture,
    ):
        """Per-kind name policy: a workset name that is ALSO a primary box name
        (a bare-name shadow) is still reachable via the NOUN-scoped workset
        lookup — which returns the WORKSET and never emits the bare-name shadow
        warning (that warning is bare-name resolution only)."""
        from kanibako.settings.paths import register_primary_box_name

        proj = tmp_home / "proj"
        proj.mkdir()
        register_primary_box_name(std.primary_workset, std.registry, "proj", str(proj))
        # --force: the workset shares the shadowed name deliberately.
        create_workset("proj", tmp_home / "worksets" / "proj", std, force=True)

        with caplog.at_level("WARNING"):
            ws = resolve_workset_name("proj", std)
        assert ws.name == "proj" and ws.is_default is False
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []


class TestListWorksetsExcludesDefault:
    def test_default_not_in_registry(self, std, tmp_home):
        root = tmp_home / "worksets" / "real"
        create_workset("real", root, std)
        registry = list_worksets(std)
        assert DEFAULT_WORKSET_ID not in registry
        assert DEFAULT_WORKSET_ALIAS not in registry
        assert "real" in registry


# ---------------------------------------------------------------------------
# load_workset
# ---------------------------------------------------------------------------

class TestLoadWorkset:
    def test_roundtrip(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        loaded = load_workset(root)
        assert loaded.name == ws.name
        assert loaded.root == ws.root
        assert loaded.created == ws.created
        assert loaded.projects == []

    def test_roundtrip_with_projects(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj-a", tmp_home / "project")

        loaded = load_workset(root)
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "proj-a"

    def test_missing_root_raises(self, std, tmp_home):
        with pytest.raises(WorksetError, match="does not exist"):
            load_workset(tmp_home / "nonexistent")

    def test_missing_registry_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "no-registry"
        root.mkdir(parents=True)

        with pytest.raises(WorksetError, match="no 'workset' identity table"):
            load_workset(root)

    def test_identity_without_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "bad-identity"
        root.mkdir(parents=True)
        (root / "registry.yaml").write_text(
            'workset:\n  created: "2026-01-01"\n'
        )

        with pytest.raises(WorksetError, match="has no 'name' key"):
            load_workset(root)


# ---------------------------------------------------------------------------
# list_worksets
# ---------------------------------------------------------------------------

class TestListWorksets:
    def test_empty_when_no_registry(self, std):
        assert list_worksets(std) == {}

    def test_lists_all_registered(self, std, tmp_home):
        r1 = tmp_home / "worksets" / "alpha"
        r2 = tmp_home / "worksets" / "beta"
        create_workset("alpha", r1, std)
        create_workset("beta", r2, std)

        registry = list_worksets(std)
        assert len(registry) == 2
        assert "alpha" in registry
        assert "beta" in registry


# ---------------------------------------------------------------------------
# delete_workset
# ---------------------------------------------------------------------------

class TestDeleteWorkset:
    def test_unregisters(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)
        assert "my-set" in list_worksets(std)

        ret = delete_workset("my-set", std)
        assert ret == root.resolve()
        assert "my-set" not in list_worksets(std)

    def test_keeps_files_by_default(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        delete_workset("my-set", std)
        assert root.resolve().is_dir()

    def test_removes_files_when_requested(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        delete_workset("my-set", std, remove_files=True)
        assert not root.resolve().exists()

    def test_unknown_name_raises(self, std):
        with pytest.raises(WorksetError, match="not registered"):
            delete_workset("nope", std)


# ---------------------------------------------------------------------------
# add_project / remove_project
# ---------------------------------------------------------------------------

class TestAddProject:
    def test_creates_subdirectories(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "cool-app", tmp_home / "project")

        resolved = root.resolve()
        assert (resolved / "boxes" / "cool-app").is_dir()
        assert (resolved / "workspaces" / "cool-app").is_dir()
        assert (resolved / "vault" / "ro" / "cool-app").is_dir()
        assert (resolved / "vault" / "rw" / "cool-app").is_dir()

    def test_persists_to_toml(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "cool-app", tmp_home / "project")

        loaded = load_workset(root)
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "cool-app"
        assert loaded.projects[0].source_path == (tmp_home / "project").resolve()

    def test_duplicate_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "dup", tmp_home / "project")

        with pytest.raises(WorksetError, match="already exists"):
            add_project(ws, "dup", tmp_home / "project")

    def test_multiple_projects(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        proj_a = tmp_home / "proj_a"
        proj_b = tmp_home / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        add_project(ws, "alpha", proj_a)
        add_project(ws, "beta", proj_b)

        loaded = load_workset(root)
        assert len(loaded.projects) == 2
        names = {p.name for p in loaded.projects}
        assert names == {"alpha", "beta"}


class TestAddProjectConnectGuard:
    """add_project refuses external sources that would mis-resolve."""

    def test_refuses_source_inside_another_workset(self, std, tmp_home):
        # other-set lives at tmp_home/worksets/other-set; a dir inside it must
        # not be connectable to a different workset (would be shadowed).
        other = create_workset("other-set", tmp_home / "worksets" / "other-set", std)
        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        inside_other = other.root.resolve() / "some" / "repo"
        inside_other.mkdir(parents=True)

        with pytest.raises(WorksetError, match="inside workset 'other-set'"):
            add_project(ws, "x", inside_other, std)

        # No partial state: nothing created for the project.
        assert not (ws.projects_dir / "x").exists()
        assert not (ws.workspaces_dir / "x").exists()
        assert len(ws.projects) == 0

    def test_refuses_already_connected_source(self, std, tmp_home):
        ws_a = create_workset("set-a", tmp_home / "worksets" / "set-a", std)
        ws_b = create_workset("set-b", tmp_home / "worksets" / "set-b", std)
        external = (tmp_home / "ext_repo").resolve()
        external.mkdir()
        add_project(ws_a, "proj", external, std)

        with pytest.raises(WorksetError, match="already connected"):
            add_project(ws_b, "proj2", external, std)

    def test_refuses_source_nested_under_connected(self, std, tmp_home):
        ws_a = create_workset("set-a", tmp_home / "worksets" / "set-a", std)
        ws_b = create_workset("set-b", tmp_home / "worksets" / "set-b", std)
        external = (tmp_home / "ext_repo").resolve()
        external.mkdir()
        add_project(ws_a, "proj", external, std)
        nested = external / "sub"
        nested.mkdir()

        with pytest.raises(WorksetError, match="already connected"):
            add_project(ws_b, "proj2", nested, std)

    def test_internal_source_unaffected(self, std, tmp_home):
        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        internal = ws.root.resolve() / "workspaces" / "in-tree"
        # A source inside the target workset is fine even though it is "inside a
        # workset" — it is the target's own tree.
        add_project(ws, "in-tree", internal, std)
        assert len(ws.projects) == 1

    def test_no_std_caller_unaffected(self, std, tmp_home):
        # std=None callers (e.g. migrate) bypass the guard entirely.
        other = create_workset("other-set", tmp_home / "worksets" / "other-set", std)
        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        inside_other = other.root.resolve() / "repo"
        inside_other.mkdir(parents=True)
        add_project(ws, "x", inside_other)  # no std → no guard
        assert len(ws.projects) == 1

    # --- D3-mode #1: standalone-marker steal guard (B2a) ---

    @staticmethod
    def _make_standalone(dir_path: Path) -> None:
        """Stamp *dir_path* with the in-place standalone MARKER (box_data/ +
        settings.yaml), matching box_resolve.standalone_settings_present."""
        from kanibako.launch.box_resolve import standalone_settings_present
        from kanibako.settings.config import BOX_META_FILE
        from kanibako.settings.paths import STANDALONE_META_DIR

        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / STANDALONE_META_DIR).mkdir()
        (dir_path / BOX_META_FILE).write_text("project: {}\n")
        assert standalone_settings_present(dir_path)  # marker is real

    def test_refuses_standalone_marked_external_source(self, std, tmp_home):
        # Connecting a dir that declares itself standalone (in-place marker) must
        # be REFUSED by default — a silent absorb/"steal" (D3-mode #1).
        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        external = (tmp_home / "standalone_box").resolve()
        self._make_standalone(external)

        with pytest.raises(WorksetError, match="standalone box"):
            add_project(ws, "sb", external, std)

        # No partial state: nothing registered for the project.
        assert not (ws.projects_dir / "sb").exists()
        assert len(ws.projects) == 0

    def test_force_moves_standalone_registration_to_workset(self, std, tmp_home):
        # With force=True the deliberate absorb MOVES the registration: the box
        # leaves the global standalone: index and becomes SOLELY a workset box
        # (exactly-one-registry — no dual registration).
        from kanibako.project import registry_store
        from kanibako.launch import box_resolve

        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        external = (tmp_home / "standalone_box").resolve()
        self._make_standalone(external)
        # Pre-register it in the global standalone: index (the pre-connect state
        # of a box that has been resolved/imported at least once).
        registry_store.register_standalone(std.registry, "kx_standalone_box", external)
        assert "kx_standalone_box" in registry_store.load_standalone(std.registry)

        proj = add_project(ws, "sb", external, std, force=True)

        assert proj.name == "sb"
        assert len(ws.projects) == 1
        # The boxes: connection record now exists (it resolves as a workset box).
        owned = box_resolve.find_connected_external_box(external, std)
        assert owned is not None
        assert owned.box_name == "sb"
        # And the global standalone: registration is GONE — NOT dual-registered.
        assert "kx_standalone_box" not in registry_store.load_standalone(std.registry)

    def test_force_roundtrip_disconnect_reimports_standalone(
        self, std, tmp_home, config
    ):
        # --force connect (standalone: dropped, boxes: added) → disconnect (boxes:
        # removed) → a resolve re-imports the box back to standalone: (clean
        # round-trip; the box_data/ marker is untouched throughout).
        from kanibako.project import registry_store
        from kanibako.settings.paths import BoxMode, detect_project_mode

        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        external = (tmp_home / "standalone_box").resolve()
        self._make_standalone(external)
        registry_store.register_standalone(std.registry, "kx_standalone_box", external)

        add_project(ws, "sb", external, std, force=True)
        assert "kx_standalone_box" not in registry_store.load_standalone(std.registry)

        # Disconnect removes the boxes: entry.
        remove_project(ws, "sb", std=std)

        # A resolve now walks to the marker and re-imports it as standalone.
        result = detect_project_mode(external, std, config)
        assert result.mode is BoxMode.standalone
        assert (
            registry_store.standalone_name_for_root(std.registry, external)
            is not None
        )
        # The intrinsic marker was never removed.
        assert (external / "box_data").is_dir()

    def test_non_standalone_external_source_unaffected(self, std, tmp_home):
        # A plain external dir (no marker) still connects without --force.
        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        external = (tmp_home / "plain_repo").resolve()
        external.mkdir()

        add_project(ws, "pr", external, std)
        assert len(ws.projects) == 1


class TestUnifiedProjectRecord:
    """The unified per-project record (B7): identity + path ONLY, no `seeded`.

    Registry MEMBERSHIP is the seed signal — the per-project `seeded` field and
    `set_project_seeded` are GONE (keyspace spec §0/§5).
    """

    def test_record_has_no_seeded_field(self, std, tmp_home):
        from kanibako.project.workset import WorksetProject

        assert not hasattr(WorksetProject("p", Path("/p")), "seeded")

    def test_set_project_seeded_removed(self):
        import kanibako.project.workset as ws_mod

        assert not hasattr(ws_mod, "set_project_seeded")

    def test_record_round_trips_name_and_path(self, std, tmp_home):
        """A project record persists name + source_path with no extra state."""
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        src = tmp_home / "project"
        add_project(ws, "proj", src)

        loaded = load_workset(root)
        assert len(loaded.projects) == 1
        rec = loaded.projects[0]
        assert rec.name == "proj"
        assert rec.source_path == src

    def test_persisted_entry_carries_only_identity_and_path(self, std, tmp_home):
        """The on-disk project record is keyed by name and holds only source_path."""
        from kanibako.settings.config_io import load_doc

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        data = load_doc(ws.registry_path)
        assert data["projects"] == {
            "proj": {"source_path": str(tmp_home / "project")}
        }

    def test_legacy_entry_with_seeded_loads_and_drops_it(self, std, tmp_home):
        """A legacy on-disk record carrying `seeded` loads (ignored) + drops it."""
        from kanibako.settings.config_io import dump_doc, load_doc

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        # Inject a legacy 'seeded' key onto the on-disk record.
        data = load_doc(ws.registry_path)
        for record in data["projects"].values():
            record["seeded"] = True
        dump_doc(ws.registry_path, data)

        # Loads fine (seeded ignored); a re-write drops the legacy key.
        loaded = load_workset(root)
        assert not hasattr(loaded.projects[0], "seeded")
        add_project(loaded, "proj2", tmp_home / "project2")
        rewritten = load_doc(loaded.registry_path)
        for record in rewritten["projects"].values():
            assert "seeded" not in record


class TestRemoveProject:
    def test_removes_from_toml(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")
        assert len(ws.projects) == 1

        removed = remove_project(ws, "proj")
        assert removed.name == "proj"
        assert len(ws.projects) == 0

        loaded = load_workset(root)
        assert len(loaded.projects) == 0

    def test_keeps_files_by_default(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        remove_project(ws, "proj")
        resolved = root.resolve()
        assert (resolved / "boxes" / "proj").is_dir()

    def test_removes_files_when_requested(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        remove_project(ws, "proj", remove_files=True)
        resolved = root.resolve()
        assert not (resolved / "boxes" / "proj").exists()
        assert not (resolved / "workspaces" / "proj").exists()
        assert not (resolved / "vault" / "ro" / "proj").exists()
        assert not (resolved / "vault" / "rw" / "proj").exists()

    def test_unknown_project_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        with pytest.raises(WorksetError, match="not found"):
            remove_project(ws, "nonexistent")


def _workset_boxes(ws):
    """Read *ws*'s per-workset ``boxes:`` membership (the D10 connection index)."""
    from kanibako.project import workset_registry

    return workset_registry.load_workset_boxes(ws.registry_path)


class TestRemoveExternalProject:
    """disconnect symmetric cleanup for external-connected projects.

    Regression guard for the connect-external work: removing an external project
    must drop its per-workset ``boxes:`` connection record (D10) and the
    workspaces/{name} symlink, and must NEVER delete the user's external source
    directory.
    """

    def _connect_external(self, std, tmp_home):
        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "file.txt").write_text("keep me")
        add_project(ws, "extproj", external, std)

        # Sanity: markers exist after connect — the per-workset boxes: entry maps
        # the box name to the EXTERNAL path (the connection record).
        assert (ws.workspaces_dir / "extproj").is_symlink()
        assert _workset_boxes(ws).get("extproj") == str(external)
        return ws, external

    def test_disconnect_clears_markers_keeps_source(self, std, tmp_home):
        ws, external = self._connect_external(std, tmp_home)

        remove_project(ws, "extproj", std=std)

        # boxes: connection record gone, symlink gone, external source intact.
        assert "extproj" not in _workset_boxes(ws)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert external.is_dir()
        assert (external / "file.txt").read_text() == "keep me"

    def test_disconnect_remove_files_keeps_source(self, std, tmp_home):
        ws, external = self._connect_external(std, tmp_home)

        # Must not crash on the symlink (rmtree refuses symlinks) and must not
        # delete the external source.
        remove_project(ws, "extproj", remove_files=True, std=std)

        assert "extproj" not in _workset_boxes(ws)
        assert not (ws.workspaces_dir / "extproj").exists()
        assert not (ws.projects_dir / "extproj").exists()
        assert not (ws.vault_dir / "extproj").exists()
        # External source dir survives.
        assert external.is_dir()
        assert (external / "file.txt").read_text() == "keep me"


# ---------------------------------------------------------------------------
# Workset properties
# ---------------------------------------------------------------------------

class TestWorksetProperties:
    def test_convenience_paths(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        resolved = root.resolve()
        assert ws.projects_dir == resolved / "boxes"
        assert ws.workspaces_dir == resolved / "workspaces"
        assert ws.vault_dir == resolved / "vault"
        assert ws.settings_path == resolved / "settings.yaml"
        assert ws.registry_path == resolved / "registry.yaml"


class TestWorksetWorkspacesResolved:
    """B2 (§3.3 ruling: ``workset.{workspaces,channelroot}`` "need to be real
    and USED — not hard-coded"): the composing sites route through the resolved
    key value — a ``workset: {workspaces: …}`` / ``{channelroot: …}`` repoint in
    the workset's settings.yaml is honored, and the default is the spec's
    per-mode formula spelled once in the resolver, never a per-site literal."""

    # -- the shared resolvers (pure units) ---------------------------------

    def test_resolver_default_is_the_spec_formula(self, tmp_path):
        from kanibako.project.workset import (
            resolve_workset_channelroot,
            resolve_workset_workspaces,
        )

        assert resolve_workset_workspaces(tmp_path, None) == tmp_path / "workspaces"
        assert resolve_workset_channelroot(tmp_path, None) == tmp_path / "channels"

    def test_resolver_relative_repoint_anchors_under_root(self, tmp_path):
        from kanibako.project.workset import (
            resolve_workset_channelroot,
            resolve_workset_workspaces,
        )

        doc = {"workset": {"workspaces": "pods", "channelroot": "comms"}}
        assert resolve_workset_workspaces(tmp_path, doc) == tmp_path / "pods"
        assert resolve_workset_channelroot(tmp_path, doc) == tmp_path / "comms"

    def test_resolver_absolute_repoint_used_as_is(self, tmp_path):
        from kanibako.project.workset import resolve_workset_workspaces

        doc = {"workset": {"workspaces": "/srv/pods"}}
        assert resolve_workset_workspaces(tmp_path, doc) == Path("/srv/pods")

    def test_resolver_ignores_malformed_or_empty_slots(self, tmp_path):
        from kanibako.project.workset import resolve_workset_workspaces

        for doc in (None, {}, {"workset": "oops"}, {"workset": {"workspaces": ""}}):
            assert (
                resolve_workset_workspaces(tmp_path, doc)
                == tmp_path / "workspaces"
            )

    # -- NAMED: load_workset captures the repoint --------------------------

    def test_named_workspaces_dir_follows_repoint(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc, load_doc

        root = tmp_home / "worksets" / "repointed"
        create_workset("repointed", root, std)

        # Merge the repoint into the root settings.yaml (identity preserved).
        settings = root.resolve() / "settings.yaml"
        data = load_doc(settings)
        data.setdefault("workset", {})["workspaces"] = "pods"
        dump_doc(settings, data)

        ws = load_workset(root)
        assert ws.workspaces_dir == root.resolve() / "pods"
        # Unset → the default composition, unchanged.
        ws_default = load_workset(
            create_workset("plain", tmp_home / "worksets" / "plain", std).root
        )
        assert (
            ws_default.workspaces_dir
            == (tmp_home / "worksets" / "plain").resolve() / "workspaces"
        )

    # -- PRIMARY: default_workset honors the primary settings repoint ------

    def test_primary_workspaces_dir_follows_repoint(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc

        std.primary_workset.mkdir(parents=True, exist_ok=True)
        dump_doc(
            std.primary_workset / "settings.yaml",
            {"workset": {"workspaces": "pods"}},
        )
        ws = default_workset(std)
        assert ws.workspaces_dir == std.primary_workset / "pods"

    def test_primary_workspaces_dir_default_unchanged(self, std):
        ws = default_workset(std)
        assert ws.workspaces_dir == std.primary_workset / "workspaces"

    # -- detection + reverse lookup follow the repoint (paths.py sites) ----

    def test_detection_follows_workspaces_repoint(self, std, tmp_home, config):
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "worksets" / "det"
        create_workset("det", root, std)
        settings = root.resolve() / "settings.yaml"
        data = load_doc(settings)
        data.setdefault("workset", {})["workspaces"] = "pods"
        dump_doc(settings, data)

        app = root.resolve() / "pods" / "app"
        app.mkdir(parents=True)
        result = detect_project_mode(app, std, config)
        assert result.mode is BoxMode.named

    # -- tripwire: the literal joins must not come back --------------------

    def test_no_workspaces_literal_join_remains_at_the_sites(self):
        """Pins B2: the six ``"workspaces"`` composition sites (paths.py ×2,
        project/workset.py ×2, project/names.py ×2) and the channels.py
        ``"channels"`` root join route through the resolvers.  The ONLY allowed
        spelling of each leaf is the resolver-module constant — a join-form
        literal reappearing in these files is the hard-coding the §3.3 ruling
        retired."""
        import re

        from tests.support.repo import REPO_ROOT

        src = REPO_ROOT / "src" / "kanibako"
        join_re = re.compile(r'/\s*"workspaces"|"workspaces"\s*/')
        for rel in (
            "settings/paths.py",
            "project/workset.py",
            "project/names.py",
        ):
            text = (src / rel).read_text(encoding="utf-8")
            assert not join_re.search(text), (
                f"literal workspaces join in {rel}; route it through "
                f"resolve_workset_workspaces"
            )
        channels_text = (src / "channels" / "channels.py").read_text(
            encoding="utf-8"
        )
        assert not re.search(r'/\s*"channels"', channels_text), (
            "literal channelroot join in channels/channels.py; route it "
            "through resolve_workset_channelroot"
        )

    def test_no_standalone_workspace_literal_join_remains_at_the_sites(self):
        """Pins the ruled-10 follow-up (2026-08-02): the two STANDALONE
        ``"workspace"`` composition sites — ``resolve_standalone_project``
        (settings/paths.py) and the duplicate-to-standalone copy destination
        (commands/box/_duplicate.py) — route through
        ``resolve_workset_workspaces(standalone=True)``.  The ONLY allowed
        spelling of the singular leaf is the resolver-module constant
        ``_STANDALONE_WORKSPACE_LEAF``.  (A third hardcode remains in
        commands/box/_lifecycle.py's convert-to-standalone consolidation —
        outside the ruled two sites; not pinned here.)"""
        import re

        from tests.support.repo import REPO_ROOT

        src = REPO_ROOT / "src" / "kanibako"
        join_re = re.compile(r'/\s*"workspace"|"workspace"\s*/')
        for rel in (
            "settings/paths.py",
            "commands/box/_duplicate.py",
        ):
            text = (src / rel).read_text(encoding="utf-8")
            assert not join_re.search(text), (
                f"literal standalone workspace join in {rel}; route it "
                f"through resolve_workset_workspaces(standalone=True)"
            )


class TestWorksetIdentityFile:
    """⚑⚑ IDENTITY AND MEMBERSHIP ARE REGISTRY-BORNE (system-design §Detect): the
    ``workset:`` table lives in the root ``registry.yaml`` beside ``boxes:`` and
    ``projects:``, and the root ``settings.yaml`` carries SETTINGS ONLY — sparse,
    optional, and ABSENT on a freshly created workset."""

    def test_identity_is_in_the_registry_not_the_settings_file(self, std, tmp_home):
        from kanibako.settings.config_io import load_doc

        root = tmp_home / "worksets" / "mset"
        create_workset("mset", root, std)

        registry = root.resolve() / "registry.yaml"
        assert registry.is_file()
        assert not (root.resolve() / "settings.yaml").exists()
        assert not (root.resolve() / "workset.yaml").exists()
        data = load_doc(registry)
        assert data["workset"]["name"] == "mset"

    def test_identity_write_preserves_box_membership(self, std, tmp_home):
        """The identity + projects write is a read-modify-write: ``boxes:`` rides through."""
        from kanibako.project import workset_registry
        from kanibako.project.workset import _write_workset_identity

        root = tmp_home / "worksets" / "coexist"
        ws = create_workset("coexist", root, std)
        workset_registry.register_workset_box(
            ws.registry_path, "boxa", tmp_home / "elsewhere",
        )

        _write_workset_identity(ws)

        assert workset_registry.load_workset_boxes(ws.registry_path) == {
            "boxa": str(tmp_home / "elsewhere")
        }
        assert load_workset(root).name == "coexist"

    def test_sections_are_written_in_canonical_order(self, std, tmp_home):
        """Identity, then membership, then projects — one stable on-disk order."""
        from kanibako.project import workset_registry

        root = tmp_home / "worksets" / "ordered"
        ws = create_workset("ordered", root, std)
        workset_registry.register_workset_box(
            ws.registry_path, "boxa", tmp_home / "elsewhere",
        )
        add_project(ws, "proj", tmp_home / "project")

        text = ws.registry_path.read_text()
        assert (
            text.index("workset:") < text.index("boxes:") < text.index("projects:")
        )

    def test_detection_marker_is_the_registry_identity(self, std, tmp_home, config):
        """A dir whose registry.yaml carries ``workset:`` is detected NAMED; one without
        it is not — and neither is a bare settings.yaml of any shape."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import detect_project_mode
        from kanibako.project.workset import read_workset_identity

        root = tmp_home / "worksets" / "marker"
        create_workset("marker", root, std)
        assert read_workset_identity(root) is not None

        # A registry.yaml with membership but NO identity is not a workset root —
        # that is exactly the shape of the PRIMARY workset's own registry.
        boxes_only = tmp_home / "boxes-only"
        boxes_only.mkdir()
        dump_doc(boxes_only / "registry.yaml", {"boxes": {"b": "/somewhere"}})
        assert read_workset_identity(boxes_only) is None

        # A cascade-only settings.yaml is NOT a workset marker.
        boxlike = tmp_home / "boxlike"
        boxlike.mkdir()
        dump_doc(boxlike / "settings.yaml", {"box": {"image": "img"}})
        assert read_workset_identity(boxlike) is None

        # Detection from inside a registered workset resolves to NAMED.
        result = detect_project_mode(root, std, config)
        assert result.mode is BoxMode.named

    def test_a_settings_file_is_optional_end_to_end(self, std, tmp_home, config):
        """⚑ THE POINT OF THE MOVE: with no settings.yaml at all, the root still
        detects, loads, imports and lists."""
        from kanibako.project import import_reconcile, registry_store
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "worksets" / "sparse"
        ws = create_workset("sparse", root, std)
        add_project(ws, "proj", ws.workspaces_dir / "proj")
        assert not ws.settings_path.exists()

        assert load_workset(root).name == "sparse"
        assert [p.name for p in load_workset(root).projects] == ["proj"]
        assert detect_project_mode(root, std, config).mode is BoxMode.named

        # Drop the global registration: the drop-in import re-registers from disk.
        registry_store.save_section(std.registry, "worksets", {})
        assert import_reconcile.import_named_workset(std.registry, root) == "sparse"


# ---------------------------------------------------------------------------
# The RETIRED settings.yaml identity location (v1.6.0/v1.7.x wrote ``workset.meta``
# there; the unreleased v1.8.0 tree briefly wrote ``meta.workset`` there).
#
# v1.8.0 is a clean break: identity is REGISTRY-BORNE, there is no compat read and no
# auto-migration.  The legacy table is DETECTED only so it can be DIAGNOSED — a hard
# refusal naming the hand move to registry.yaml, in place of the silent fall-through
# that would otherwise stop the directory being a workset at all.  ⚑ The distinction
# that carries the whole design: a root with NEITHER identity, and an unreadable one,
# both still say "not a workset root" without refusing.
# ---------------------------------------------------------------------------

#: A workset root exactly as v1.7.2 wrote it (``src/kanibako/workset.py`` line 214).
_LEGACY_IDENTITY_DOC = {
    "workset": {
        "meta": {
            "name": "legacyws",
            "created": "2026-01-02T03:04:05+00:00",
            "projects": [{"name": "proj", "source_path": "/somewhere/proj"}],
        },
        "bindings": {"rw": {"~/data": "/host/data"}},
    },
}

#: The same identity as the UNRELEASED v1.8.0 tree spelled it, still in settings.yaml.
_UNRELEASED_IDENTITY_DOC = {
    "meta": {
        "workset": {
            "name": "legacyws",
            "created": "2026-01-02T03:04:05+00:00",
            "projects": [{"name": "proj", "source_path": "/somewhere/proj"}],
        },
    },
    "workset": {"bindings": {"rw": {"~/data": "/host/data"}}},
}


def _write_legacy_root(root: Path, doc: dict | None = None) -> Path:
    """Materialize a legacy (settings.yaml-identity) workset root; return its settings file."""
    from kanibako.settings.config_io import dump_doc

    root.mkdir(parents=True, exist_ok=True)
    settings = root / "settings.yaml"
    dump_doc(settings, _LEGACY_IDENTITY_DOC if doc is None else doc)
    return settings


class TestRetiredWorksetIdentityLocation:
    @pytest.mark.parametrize(
        "doc_name", ["_LEGACY_IDENTITY_DOC", "_UNRELEASED_IDENTITY_DOC"],
    )
    def test_legacy_root_refuses(self, tmp_home, doc_name):
        """⚑ EITHER retired spelling RAISES out of the detection primitive — never ``None``."""
        from kanibako.project.workset import read_workset_identity

        root = tmp_home / "legacyws"
        _write_legacy_root(root, globals()[doc_name])
        with pytest.raises(LegacyWorksetIdentityError):
            read_workset_identity(root)

    def test_cure_names_the_file_the_registry_and_the_move(self, tmp_home):
        """The refusal is actionable: the file, the retired location, the destination."""
        from kanibako.project.workset import read_workset_identity

        root = tmp_home / "legacyws"
        settings = _write_legacy_root(root)
        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            read_workset_identity(root)
        message = str(excinfo.value)

        assert str(settings) in message
        assert "workset.meta" in message              # the retired location, named
        assert str(root / "registry.yaml") in message  # the DESTINATION, named
        # The target shape itself, shown rather than described.
        assert "workset:\n      name:" in message
        for entry in ("created:", "projects:", "source_path:"):
            assert entry in message
        # No auto-migration, and the shipped guide is cited BY FILENAME.
        assert "MIGRATION.md §2.43" in message

    def test_cure_names_the_unreleased_spelling_when_that_is_what_is_there(
        self, tmp_home,
    ):
        """The message names the spelling actually on disk, not a generic pair."""
        from kanibako.project.workset import read_workset_identity

        root = tmp_home / "legacyws"
        _write_legacy_root(root, _UNRELEASED_IDENTITY_DOC)
        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            read_workset_identity(root)
        assert "'meta.workset' is a RETIRED location" in str(excinfo.value)

    def test_refusal_is_a_workset_error(self, tmp_home):
        """``LegacyWorksetIdentityError`` is a ``WorksetError``, so ``cli.py`` prints it."""
        from kanibako.errors import KanibakoError

        assert issubclass(LegacyWorksetIdentityError, WorksetError)
        assert issubclass(LegacyWorksetIdentityError, KanibakoError)

    def test_non_workset_root_still_returns_none(self, tmp_home):
        """⚑ A root with NEITHER identity is genuinely not a workset root: ``None``."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.project.workset import read_workset_identity

        # A cascade-only ``workset:`` table — present, but with no ``meta:`` under it.
        cascade_only = tmp_home / "cascade-only"
        cascade_only.mkdir()
        dump_doc(
            cascade_only / "settings.yaml",
            {"workset": {"bindings": {"rw": {"~/data": "/host/data"}}}},
        )
        assert read_workset_identity(cascade_only) is None

        # An ordinary box settings file.
        boxlike = tmp_home / "boxlike"
        boxlike.mkdir()
        dump_doc(boxlike / "settings.yaml", {"box": {"image": "img"}})
        assert read_workset_identity(boxlike) is None

        # A PLAIN directory — nothing on disk at all.
        plain = tmp_home / "plain-dir"
        plain.mkdir()
        assert read_workset_identity(plain) is None

        # A missing directory.
        assert read_workset_identity(tmp_home / "nothing-here") is None

    def test_workset_meta_scalar_is_not_the_legacy_table(self, tmp_home):
        """``workset.meta`` must be a MAPPING to be the retired identity — a scalar is not."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.project.workset import read_workset_identity

        scalar = tmp_home / "scalar-meta"
        scalar.mkdir()
        dump_doc(scalar / "settings.yaml", {"workset": {"meta": "not-a-table"}})
        assert read_workset_identity(scalar) is None

    def test_corrupt_file_still_returns_none(self, tmp_home):
        """⚑ The load guard stays: an unparseable file is ``None``, not a refusal."""
        from kanibako.project.workset import read_workset_identity

        corrupt = tmp_home / "corrupt"
        corrupt.mkdir()
        (corrupt / "settings.yaml").write_text("workset:\n  meta:\n   - [broken: :\n")
        assert read_workset_identity(corrupt) is None

        # And an unparseable REGISTRY is a miss too, not a crash.
        bad_registry = tmp_home / "bad-registry"
        bad_registry.mkdir()
        (bad_registry / "registry.yaml").write_text("workset:\n   - [broken: :\n")
        assert read_workset_identity(bad_registry) is None

    def test_live_location_still_works(self, std, tmp_home):
        """The live registry.yaml identity is untouched by the refusal."""
        from kanibako.project.workset import read_workset_identity

        root = tmp_home / "worksets" / "goodws"
        create_workset("goodws", root, std)
        identity = read_workset_identity(root.resolve())
        assert identity is not None
        assert identity["name"] == "goodws"
        assert load_workset(root).name == "goodws"

    def test_load_workset_refuses_legacy_root(self, tmp_home):
        """The LOAD path gets the named cure too, not the generic 'no identity' message."""
        root = tmp_home / "legacyws"
        _write_legacy_root(root)
        with pytest.raises(LegacyWorksetIdentityError):
            load_workset(root)

    def test_refusal_survives_the_ancestor_walk(self, std, tmp_home, config):
        """⚑⚑ THE POINT: ``detect_project_mode``'s upward walk propagates it, from a SUBDIR."""
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "legacyws"
        _write_legacy_root(root)
        deep = root / "workspaces" / "proj"
        deep.mkdir(parents=True)

        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            detect_project_mode(deep, std, config)
        assert str(root / "settings.yaml") in str(excinfo.value)

    def test_registered_legacy_workset_refuses_at_the_load_seam(self, std, tmp_home, config):
        """⚑ THE REALISTIC UPGRADE CASE: a v1.7.2 install already has the registry entry, so
        ``detect_project_mode`` short-circuits on the registry (``_check_workset``) and never
        reads the identity — the refusal lands on the LOAD path instead, and still lands."""
        from kanibako.project.names import register_name
        from kanibako.settings.paths import detect_project_mode
        from kanibako.project.workset import resolve_workset_name

        root = tmp_home / "worksets" / "legacyws"
        _write_legacy_root(root)
        deep = root / "workspaces" / "notes"
        deep.mkdir(parents=True)
        register_name(std.registry, "legacyws", str(root), section="worksets")

        # The registry match resolves NAMED without ever touching the identity table…
        assert detect_project_mode(deep, std, config).mode is BoxMode.named
        # …and the very next step, reading the identity, refuses by name.
        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            resolve_workset_name("legacyws", std)
        assert str(root / "settings.yaml") in str(excinfo.value)

    def test_walk_still_falls_through_for_a_non_workset_ancestor(
        self, std, tmp_home, config,
    ):
        """The same walk over a NON-workset tree still resolves, silently, as before."""
        from kanibako.settings.paths import detect_project_mode

        plain = tmp_home / "plain" / "sub"
        plain.mkdir(parents=True)
        assert detect_project_mode(plain, std, config).mode is BoxMode.primary

    def test_box_rm_path_target_does_not_swallow_the_refusal(
        self, std, tmp_home, config,
    ):
        """⚑ ``box rm <path>``'s blanket 'a non-project path is simply a miss' catch must
        re-raise THIS error — a legacy root is a named thing to fix, not a miss."""
        from kanibako.commands.box._parser import _resolve_standalone_target

        root = tmp_home / "legacyws"
        _write_legacy_root(root)
        with pytest.raises(LegacyWorksetIdentityError):
            _resolve_standalone_target(std, config, str(root))

        # …and an ordinary non-project path is still a plain miss.
        plain = tmp_home / "plain"
        plain.mkdir()
        assert _resolve_standalone_target(std, config, str(plain)) == (None, None)


# ---------------------------------------------------------------------------
# Failure-consistency: multi-step mutations must leave NO half-applied state.
#
# Each test injects a failure at a mid-sequence write/operation and asserts the
# op either fully applied or fully rolled back (no orphan dirs, no dangling
# per-workset connection record / symlink, no registry-vs-index mismatch,
# external path never locked out).  Mirrors the lifecycle family's
# failure-injection style (patch a forward step to raise; assert consistent state).
# ---------------------------------------------------------------------------

class _Boom(Exception):
    """Sentinel for injected mid-sequence failures."""


class TestCreateWorksetFailConsistent:
    def test_name_index_write_failure_unwinds(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"

        # register_name is the LAST step (after the dirs).  It is the SOLE writer
        # of the single ``worksets`` registry section.  If it blows up, the whole
        # create must roll back: no orphan dirs, nothing in the registry.
        def boom(*a, **k):
            raise _Boom("register_name failed")

        monkeypatch.setattr(ws_mod, "register_name", boom)

        with pytest.raises(_Boom):
            create_workset("my-set", root, std)

        assert not root.exists(), "orphan dirs left behind"
        assert "my-set" not in list_worksets(std), "stale worksets entry"
        from kanibako.project.names import read_names
        assert "my-set" not in read_names(std.registry).get("worksets", {})


class TestDeleteWorksetSelfHealing:
    def test_irreversible_rmtree_after_registries_clean(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        # If unregister_name (a registry step) fails, rmtree (irreversible) must
        # NOT have run — the dir survives, so nothing is lost.
        def boom(*a, **k):
            raise _Boom("name index write failed")

        monkeypatch.setattr(ws_mod, "unregister_name", boom)
        with pytest.raises(_Boom):
            delete_workset("my-set", std, remove_files=True)

        assert root.resolve().is_dir(), "rmtree ran before registries were clean"


class TestAddProjectFailConsistent:
    def test_internal_workset_write_failure_unwinds(self, std, tmp_home, monkeypatch):
        import kanibako.project.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        proj_src = tmp_home / "project"

        # The registry identity write is the LAST step; failing it must roll back the
        # per-project dirs and leave no project in the in-memory list / on disk.
        def boom(*a, **k):
            raise _Boom("registry.yaml identity write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_identity", boom)
        with pytest.raises(_Boom):
            add_project(ws, "proj", proj_src)

        resolved = root.resolve()
        assert not (resolved / "boxes" / "proj").exists()
        assert not (resolved / "workspaces" / "proj").exists()
        assert not (resolved / "vault" / "ro" / "proj").exists()
        assert not (resolved / "vault" / "rw" / "proj").exists()
        assert all(p.name != "proj" for p in ws.projects)
        assert all(p.name != "proj" for p in load_workset(root).projects)

    def test_external_workset_write_failure_unwinds_redirect(
        self, std, tmp_home, monkeypatch
    ):
        import kanibako.project.workset as ws_mod

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "keep.txt").write_text("keep me")

        # The per-workset boxes: connection record is written BEFORE the final
        # registry identity write.  If that final write fails, the record + symlink
        # must be rolled back so the external path is NOT locked out.
        def boom(*a, **k):
            raise _Boom("registry.yaml identity write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_identity", boom)
        with pytest.raises(_Boom):
            add_project(ws, "extproj", external, std)

        # No dangling connection record, no orphan symlink, external source
        # untouched.
        assert "extproj" not in _workset_boxes(ws)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert all(p.name != "extproj" for p in ws.projects)
        assert external.is_dir()
        assert (external / "keep.txt").read_text() == "keep me"

    def test_external_connected_write_failure_unwinds_symlink(
        self, std, tmp_home, monkeypatch
    ):
        import kanibako.settings.paths as paths_mod

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()

        # Fail the per-workset boxes: registration (after the symlink is created).
        # The symlink must be rolled back so no orphan link survives.
        def boom(*a, **k):
            raise _Boom("boxes: registration failed")

        monkeypatch.setattr(
            paths_mod, "_register_workset_box_membership", boom,
        )
        with pytest.raises(_Boom):
            add_project(ws, "extproj", external, std)

        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert "extproj" not in _workset_boxes(ws)
        assert all(p.name != "extproj" for p in ws.projects)
        assert external.is_dir()


class TestRemoveProjectFailConsistent:
    def test_registry_removal_is_last_durable_step(self, std, tmp_home, monkeypatch):
        # If the final registry identity write fails during removal, the project must
        # remain registered (re-runnable) rather than being dropped from the
        # registry while leaving a dangling per-workset connection record.
        import kanibako.project.workset as ws_mod
        from kanibako.project.workset import remove_project

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        assert _workset_boxes(ws).get("extproj") == str(external)

        def boom(*a, **k):
            raise _Boom("registry.yaml identity write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_identity", boom)
        with pytest.raises(_Boom):
            remove_project(ws, "extproj", std=std)

        # The boxes: record + symlink were cleaned (idempotent re-run safe), but
        # the registry still lists the project — so it is NOT half-gone with a
        # dangling record (the external path is never locked out).
        assert "extproj" in {p.name for p in load_workset(ws.root).projects}
        # The connection record was cleared before the failed registry write; a
        # re-run of remove_project will complete the removal cleanly.
        assert "extproj" not in _workset_boxes(ws)
        assert external.is_dir()
