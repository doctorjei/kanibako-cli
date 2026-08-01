"""Tests for kanibako.workset -- working set data model and persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import WorksetError
from kanibako.paths import BoxMode
from kanibako.workset import (
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
        assert ws.toml_path.is_file()

    def test_writes_workset_toml(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        text = ws.toml_path.read_text()
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
        from kanibako.paths import register_primary_box_name

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
        from kanibako.paths import register_primary_box_name

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

    def test_missing_toml_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "no-toml"
        root.mkdir(parents=True)

        with pytest.raises(WorksetError, match="No settings.yaml"):
            load_workset(root)

    def test_toml_without_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "bad-toml"
        root.mkdir(parents=True)
        (root / "settings.yaml").write_text(
            'workset:\n  meta:\n    created: "2026-01-01"\n'
        )

        with pytest.raises(WorksetError, match="no 'name' key"):
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
        from kanibako.config import BOX_META_FILE
        from kanibako.paths import _STANDALONE_META_DIR

        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / _STANDALONE_META_DIR).mkdir()
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
        from kanibako import registry_store
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
        from kanibako import registry_store
        from kanibako.paths import BoxMode, detect_project_mode

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
        from kanibako.workset import WorksetProject

        assert not hasattr(WorksetProject("p", Path("/p")), "seeded")

    def test_set_project_seeded_removed(self):
        import kanibako.workset as ws_mod

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
        """The on-disk project entry has exactly {name, source_path}; no seeded."""
        from kanibako.config_io import load_doc

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        data = load_doc(ws.toml_path)
        entries = data["workset"]["meta"]["projects"]
        assert entries == [
            {"name": "proj", "source_path": str(tmp_home / "project")}
        ]

    def test_legacy_entry_with_seeded_loads_and_drops_it(self, std, tmp_home):
        """A legacy on-disk entry carrying `seeded` loads (ignored) + drops it."""
        from kanibako.config_io import dump_doc, load_doc

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        # Inject a legacy 'seeded' key onto the on-disk entry.
        data = load_doc(ws.toml_path)
        for entry in data["workset"]["meta"]["projects"]:
            entry["seeded"] = True
        dump_doc(ws.toml_path, data)

        # Loads fine (seeded ignored); a re-write drops the legacy key.
        loaded = load_workset(root)
        assert not hasattr(loaded.projects[0], "seeded")
        add_project(loaded, "proj2", tmp_home / "project2")
        rewritten = load_doc(loaded.toml_path)
        for entry in rewritten["workset"]["meta"]["projects"]:
            assert "seeded" not in entry


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
    from kanibako import workset_registry
    from kanibako.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws.root, load_doc(ws.root / "settings.yaml"),
    )
    return workset_registry.load_workset_boxes(registry_path)


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
        assert ws.toml_path == resolved / "settings.yaml"


class TestWorksetSettingsFile:
    def test_identity_under_workset_meta(self, std, tmp_home):
        """create_workset writes identity into settings.yaml's workset.meta."""
        from kanibako.config_io import load_doc

        root = tmp_home / "worksets" / "mset"
        create_workset("mset", root, std)

        settings = root.resolve() / "settings.yaml"
        assert settings.is_file()
        assert not (root.resolve() / "workset.yaml").exists()
        data = load_doc(settings)
        assert data["workset"]["meta"]["name"] == "mset"

    def test_identity_and_cascade_coexist(self, std, tmp_home):
        """Cascade settings + the workset.meta identity share one file without
        clobbering each other across writes."""
        from kanibako.config_io import dump_doc, load_doc

        root = tmp_home / "worksets" / "coexist"
        ws = create_workset("coexist", root, std)
        settings = ws.toml_path

        # Write a cascade share key into the same file.
        data = load_doc(settings)
        data.setdefault("workset", {}).setdefault("bindings", {}).setdefault(
            "rw", {}
        )["data"] = "/host:/guest"
        dump_doc(settings, data)

        # Re-writing the workset identity must preserve the cascade key.
        from kanibako.workset import _write_workset_toml
        _write_workset_toml(ws)

        # Both survive: the binding key is intact on disk, identity reload works.
        reloaded_data = load_doc(settings)
        assert (
            reloaded_data["workset"]["bindings"]["rw"]["data"] == "/host:/guest"
        )
        reloaded = load_workset(root)
        assert reloaded.name == "coexist"

    def test_detection_marker_is_workset_meta(self, std, tmp_home, config):
        """A dir with a settings.yaml carrying workset.meta is detected NAMED;
        a box-style settings.yaml (box.meta only) is not."""
        from kanibako.config_io import dump_doc
        from kanibako.paths import detect_project_mode
        from kanibako.workset import read_workset_meta

        root = tmp_home / "worksets" / "marker"
        create_workset("marker", root, std)
        assert read_workset_meta(root / "settings.yaml") is not None

        # A box-style settings.yaml (no workset.meta) is NOT a workset marker.
        boxlike = tmp_home / "boxlike"
        boxlike.mkdir()
        dump_doc(boxlike / "settings.yaml", {"box": {"meta": {"name": "b"}}})
        assert read_workset_meta(boxlike / "settings.yaml") is None
        # Detection from inside a registered workset resolves to NAMED.
        result = detect_project_mode(root, std, config)
        assert result.mode is BoxMode.named


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
        import kanibako.workset as ws_mod

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
        from kanibako.names import read_names
        assert "my-set" not in read_names(std.registry).get("worksets", {})


class TestDeleteWorksetSelfHealing:
    def test_irreversible_rmtree_after_registries_clean(self, std, tmp_home, monkeypatch):
        import kanibako.workset as ws_mod

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
        import kanibako.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        proj_src = tmp_home / "project"

        # workset.yaml write is the LAST step; failing it must roll back the
        # per-project dirs and leave no project in the in-memory list / on disk.
        def boom(*a, **k):
            raise _Boom("workset.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_toml", boom)
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
        import kanibako.workset as ws_mod

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "keep.txt").write_text("keep me")

        # The per-workset boxes: connection record is written BEFORE the final
        # workset.yaml write.  If that final write fails, the record + symlink
        # must be rolled back so the external path is NOT locked out.
        def boom(*a, **k):
            raise _Boom("workset.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_toml", boom)
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
        import kanibako.paths as paths_mod

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
        # If the final workset.yaml write fails during removal, the project must
        # remain registered (re-runnable) rather than being dropped from the
        # registry while leaving a dangling per-workset connection record.
        import kanibako.workset as ws_mod
        from kanibako.workset import remove_project

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        assert _workset_boxes(ws).get("extproj") == str(external)

        def boom(*a, **k):
            raise _Boom("workset.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_toml", boom)
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
