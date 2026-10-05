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
        # ⚑ A fresh workset root holds NO FILES AT ALL: no workset.yaml (the file
        # carries settings only, so it appears when something is SET) and no
        # registry.yaml (a workset with no members has no membership to record).
        assert not ws.settings_path.exists()
        assert not ws.registry_path.exists()

    def test_the_global_registration_is_the_only_record_of_the_name(self, std, tmp_home):
        """⚑⚑ THE IDENTITY: name → root in the global registry, and nowhere else."""
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        assert list_worksets(std)["my-set"] == root.resolve()
        on_disk = [p for p in root.resolve().rglob("*") if p.is_file()]
        assert on_disk == [], on_disk

    def test_registers_globally(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        registry = list_worksets(std)
        assert "my-set" in registry
        assert registry["my-set"] == root.resolve()

    def test_there_is_no_created_stamp(self, std, tmp_home):
        """⚑ ``created`` is GONE — not moved to the global registry, dropped."""
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        from kanibako.project import registry_store
        from kanibako.settings.config_io import load_doc

        assert not hasattr(ws, "created")
        # Not in the membership file, and not in the global registry either: the
        # ``worksets:`` value is a bare path string, not a record with fields.
        assert load_doc(ws.registry_path) == {
            "boxes": {"proj": str(ws.workspaces_dir / "proj")}
        }
        section = registry_store.load_section(std.registry, "worksets")
        assert section["my-set"] == str(root.resolve())

    def test_duplicate_name_raises(self, std, tmp_home):
        root1 = tmp_home / "worksets" / "set1"
        create_workset("same-name", root1, std)

        root2 = tmp_home / "worksets" / "set2"
        with pytest.raises(WorksetError, match="already in use"):
            create_workset("same-name", root2, std)

    def test_duplicate_name_raises_even_with_force(self, std, tmp_home):
        # force overrides the CROSS-KIND check only; same-kind workset
        # uniqueness stays hard (system-design-1.8.0.md § "Detection &
        # import", "Cross-kind name semantics").
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

    def test_cross_kind_guard_catches_a_CASE_VARIANT_primary_box(self, std, tmp_home):
        """⚑ THE ASYMMETRIC COMPARE — a live defect before the §0 naming rules landed.

        The cross-kind guard compared a RAW workset name against primary box keys that
        were only folded because ``box create`` folded them on the way in.  So a workset
        named ``Foo`` walked straight past a box named ``foo``, was registered, and then
        lost every bare-name lookup to the box it was supposed to have been refused for.

        Under spec §0 (``⚑ NAMING RULES``) ``Foo`` and ``foo`` ARE the same name, so
        this is the collision the guard already means to refuse — it simply could not be
        seen while one side of the compare was folded and the other was not.
        """
        from kanibako.settings.paths import register_primary_box_name

        proj = tmp_home / "proj"
        proj.mkdir()
        register_primary_box_name(std.primary_workset, std.registry, "foo", str(proj))

        root = tmp_home / "worksets" / "Foo"
        with pytest.raises(WorksetError, match="already in use by a primary box"):
            create_workset("Foo", root, std)
        # Refused BEFORE any on-disk side effect, like the exact-case refusal.
        assert not root.exists()

    def test_cross_kind_case_refusal_names_the_box_as_STORED(self, std, tmp_home):
        """A user refused for a name they cannot find in ``list`` learns nothing."""
        from kanibako.settings.paths import register_primary_box_name

        proj = tmp_home / "proj"
        proj.mkdir()
        register_primary_box_name(std.primary_workset, std.registry, "foo", str(proj))

        with pytest.raises(WorksetError, match="the box is named 'foo'"):
            create_workset("Foo", tmp_home / "worksets" / "Foo", std)

    def test_same_kind_duplicate_is_case_blind(self, std, tmp_home):
        """§0: workset names collide without regard to case, and the refusal says which."""
        create_workset("same-name", tmp_home / "worksets" / "set1", std)

        with pytest.raises(WorksetError, match="already in use as 'same-name'"):
            create_workset("SAME-NAME", tmp_home / "worksets" / "set2", std)

    def test_a_partition_token_is_refused_in_any_case(self, std, tmp_home):
        """🛑 ``__primary__`` is REFUSED, and the filesystem is why.

        A named workset's channel token is its own name emitted verbatim as the
        ``mailboxes/``/``share/`` directory (``channels.channels.workset_name_token``).
        On a case-INSENSITIVE filesystem — macOS, the platform whose ``agents/Shell/``
        collision opened this arc — ``channels/mailboxes/__primary__/`` IS
        ``channels/mailboxes/__PRIMARY__/``, so accepting the name is cross-partition
        channel leakage rather than a harmless distinct segment.

        ⚑ Folding the REFUSAL is not folding a path: the tokens are still emitted
        exactly wherever they are emitted.  This only reserves their case variants.
        """
        for spelling in ("__primary__", "__Primary__", "__standalone__"):
            with pytest.raises(WorksetError, match="is reserved"):
                create_workset(spelling, tmp_home / "worksets" / "p", std)

    def test_reserved_identifier_is_refused_in_any_case(self, std, tmp_home):
        """The other half: ``default`` is a user-typed bare name, so ``Default`` hits it."""
        for spelling in ("Default", "DEFAULT", "__DEFAULT__"):
            with pytest.raises(WorksetError, match="is reserved"):
                create_workset(spelling, tmp_home / "worksets" / spelling, std)

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

        loaded = load_workset(root, "my-set")
        assert loaded.name == ws.name
        assert loaded.root == ws.root
        assert loaded.projects == []

    def test_roundtrip_with_projects(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj-a", tmp_home / "project")

        loaded = load_workset(root, "my-set")
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "proj-a"

    def test_missing_root_raises(self, std, tmp_home):
        with pytest.raises(WorksetError, match="does not exist"):
            load_workset(tmp_home / "nonexistent", "nonexistent")

    def test_the_name_comes_from_the_caller_not_from_disk(self, std, tmp_home):
        """⚑⚑ Nothing under the root records a name, so the caller's is the only one."""
        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)

        assert load_workset(root, "whatever-the-registry-said").name == (
            "whatever-the-registry-said"
        )

    def test_a_bare_directory_loads_as_an_empty_workset(self, std, tmp_home):
        """There is no marker to be missing: membership is absent, not the workset."""
        root = tmp_home / "worksets" / "no-registry"
        root.mkdir(parents=True)

        loaded = load_workset(root, "no-registry")
        assert loaded.name == "no-registry"
        assert loaded.projects == []


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

    @pytest.mark.parametrize("malformed", [
        pytest.param("workset:\n  meta:\n   - [broken: :\n", id="invalid-yaml"),
        pytest.param("- one\n- two\n", id="top-level-list"),
        pytest.param("just-a-scalar\n", id="top-level-scalar"),
    ])
    def test_purge_removes_a_root_whose_settings_file_is_malformed(
        self, std, tmp_home, malformed,
    ):
        """⚑ A DELETION PATH NEVER GAINS A REFUSAL: purge clears a root it cannot read.

        Every other reader refuses a malformed ``workset.yaml``, which is right — but the
        user may be deleting the root BECAUSE the file is broken, so a refusal here would
        make them hand-edit a file they are throwing away.
        """
        root = tmp_home / "worksets" / "brokenws"
        create_workset("brokenws", root, std)
        (root / "workset.yaml").write_text(malformed)

        delete_workset("brokenws", std, remove_files=True)

        assert not root.resolve().exists()
        assert "brokenws" not in list_worksets(std)


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

    def test_persists_to_the_membership(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "cool-app", tmp_home / "project")

        loaded = load_workset(root, "my-set")
        assert len(loaded.projects) == 1
        assert loaded.projects[0].name == "cool-app"
        # ⚑ The REAL workspace, not the caller's source: an in-tree member runs on
        # ``workspaces/<name>`` and that is the one path recorded.
        assert loaded.projects[0].source_path == ws.workspaces_dir / "cool-app"

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

        loaded = load_workset(root, "my-set")
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
        workset.yaml), matching box_resolve.standalone_settings_present."""
        from kanibako.launch.box_resolve import standalone_settings_present
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.paths import STANDALONE_META_DIR

        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / STANDALONE_META_DIR).mkdir()
        (dir_path / WORKSET_META_FILE).write_text("project: {}\n")
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
    `set_project_seeded` are GONE (keyspace spec §0 "Seed-time vs cascade";
    system-design-1.8.0.md § "Detection & import", "One per-project record").
    """

    def test_record_has_no_seeded_field(self, std, tmp_home):
        from kanibako.project.workset import WorksetProject

        assert not hasattr(WorksetProject("p", Path("/p")), "seeded")

    def test_set_project_seeded_removed(self):
        import kanibako.project.workset as ws_mod

        assert not hasattr(ws_mod, "set_project_seeded")

    def test_record_round_trips_name_and_path(self, std, tmp_home):
        """A project record persists name + workspace path with no extra state."""
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        loaded = load_workset(root, "my-set")
        assert len(loaded.projects) == 1
        rec = loaded.projects[0]
        assert rec.name == "proj"
        assert rec.source_path == ws.workspaces_dir / "proj"

    def test_the_whole_registry_is_one_flat_boxes_section(self, std, tmp_home):
        """⚑⚑ ONE SECTION, FLAT ``name: path`` — no identity table, no ``projects:`` map."""
        from kanibako.settings.config_io import load_doc

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")

        assert load_doc(ws.registry_path) == {
            "boxes": {"proj": str(ws.workspaces_dir / "proj")}
        }

    def test_the_path_is_recorded_exactly_once(self, std, tmp_home):
        """The member's path appears ONCE in the whole file — no second copy to drift."""
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        external = (tmp_home / "ext").resolve()
        external.mkdir()
        add_project(ws, "proj", external, std)

        assert ws.registry_path.read_text().count(str(external)) == 1


class TestRemoveProject:
    def test_removes_from_toml(self, std, tmp_home):
        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", tmp_home / "project")
        assert len(ws.projects) == 1

        removed = remove_project(ws, "proj")
        assert removed.name == "proj"
        assert len(ws.projects) == 0

        loaded = load_workset(root, "my-set")
        assert len(loaded.projects) == 0

    def test_drops_the_membership_row_for_an_in_tree_member(self, std, tmp_home):
        """⚑⚑ THE DEFECT FIX: the drop is UNCONDITIONAL, in-tree as well as external.

        The drop used to fire only when the RECORDED path was external, so an in-tree
        disconnect orphaned its ``boxes:`` row.
        """
        from kanibako.project import workset_registry

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        add_project(ws, "proj", ws.workspaces_dir / "proj", std)
        assert workset_registry.load_workset_boxes(ws.registry_path) == {
            "proj": str(ws.workspaces_dir / "proj")
        }

        remove_project(ws, "proj", std=std)
        assert workset_registry.load_workset_boxes(ws.registry_path) == {}

    def test_disconnected_workspace_is_registrable_again_under_a_new_name(
        self, std, tmp_home,
    ):
        """⚑ THE SYMPTOM the orphan caused: workspace-path uniqueness locked it out.

        With the stale row still there, re-registering the SAME workspace under any
        other box name hit ``register_workset_box``'s one-box-per-workspace refusal,
        and nothing short of hand-editing registry.yaml could clear it.
        """
        from kanibako.project import workset_registry

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)
        workspace = ws.workspaces_dir / "proj"
        add_project(ws, "proj", workspace, std)

        remove_project(ws, "proj", std=std)
        # The box-create path re-registers by workspace path; this is what refused.
        workset_registry.register_workset_box(ws.registry_path, "renamed", workspace)
        assert workset_registry.load_workset_boxes(ws.registry_path) == {
            "renamed": str(workspace)
        }

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


class TestReleaseKeepsTheWorkspaceLeaf:
    """The relocation deleters: ``release_project`` + ``remove_member_store`` never touch
    ``workspaces/<name>``, whether it is a real dir or a link the user put there."""

    @pytest.mark.parametrize("linked", [False, True], ids=["dir", "symlink"])
    def test_leaf_survives_release_and_store_removal(self, std, tmp_home, linked):
        from kanibako.project.workset import release_project, remove_member_store

        ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
        leaf = ws.workspaces_dir / "proj"
        add_project(ws, "proj", leaf)
        content = leaf
        if linked:
            content = tmp_home / "real"
            content.mkdir()
            leaf.rmdir()
            leaf.symlink_to(content)
        (content / "f.txt").write_text("mine")

        release_project(ws, "proj")
        remove_member_store(ws, "proj")

        assert leaf.is_symlink() is linked
        assert (leaf / "f.txt").read_text() == "mine"
        assert not (ws.projects_dir / "proj").exists()
        assert not (ws.vault_dir / "ro" / "proj").exists()
        assert _workset_boxes(ws) == {}


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
        assert ws.settings_path == resolved / "workset.yaml"
        assert ws.registry_path == resolved / "registry.yaml"


class TestWorksetWorkspacesResolved:
    """B2 (§3.3 ruling: ``workset.{workspaces,channelroot}`` "need to be real
    and USED — not hard-coded"): the composing sites route through the resolved
    key value — a ``workset: {workspaces: …}`` / ``{channelroot: …}`` repoint in
    the workset's workset.yaml is honored, and the default is the spec's
    per-mode formula spelled once in the resolver, never a per-site literal."""

    # -- the shared resolvers (pure units) ---------------------------------

    def test_resolver_default_is_the_spec_formula(self, tmp_path):
        from kanibako.project.workset import (
            resolve_workset_channelroot,
            resolve_workset_workspaces,
        )

        assert resolve_workset_workspaces(tmp_path, None) == tmp_path / "workspaces"
        assert resolve_workset_channelroot(tmp_path, None) == tmp_path / "channels"

    def test_resolver_root_relative_repoint_is_spelled_with_the_ref(self, tmp_path):
        from kanibako.project.workset import (
            resolve_workset_channelroot,
            resolve_workset_workspaces,
        )

        # ⚑ INVERTED BY [R147]: this pair used to be spelled ``"pods"`` / ``"comms"``
        # and to assert that a BARE relative anchored under the root.  Keeping the
        # directory with the workset is still expressible — it just has to be SAID.
        doc = {"workset": {"workspaces": "@meta.workset.path/pods",
                           "channelroot": "@meta.workset.path/comms"}}
        assert resolve_workset_workspaces(tmp_path, doc) == tmp_path / "pods"
        assert resolve_workset_channelroot(tmp_path, doc) == tmp_path / "comms"

    def test_resolver_bare_relative_repoint_is_refused(self, tmp_path):
        """[R147]: the anchor is ~50/50 between the workset root and the cwd, so the
        seam refuses and NAMES BOTH readings rather than creating a data directory in
        one of them."""
        from kanibako.settings.settings_resolve import SettingsError
        from kanibako.project.workset import resolve_workset_channelroot

        doc = {"workset": {"channelroot": "comms"}}
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_channelroot(tmp_path, doc)
        message = str(excinfo.value)
        assert str(tmp_path / "comms") in message
        assert str(Path.cwd() / "comms") in message

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

    def test_a_null_workspaces_resolves_to_none_in_both_modes(self, tmp_path):
        """[R177]: a present ``<None>`` is no dir, never the default leaf (Q106 review)."""
        from kanibako.project.workset import resolve_workset_workspaces

        doc = {"workset": {"workspaces": None}}
        assert resolve_workset_workspaces(tmp_path, doc) is None
        assert resolve_workset_workspaces(tmp_path, doc, standalone=True) is None

    def test_the_locator_finds_members_at_the_default_under_a_null(self, tmp_path):
        """Detection still looks where pre-null in-tree members sit; nothing else reads it."""
        from kanibako.project.workset import resolve_workspaces_locator

        assert (resolve_workspaces_locator(tmp_path, {"workset": {"workspaces": None}})
                == tmp_path / "workspaces")
        doc = {"workset": {"workspaces": "/srv/pods"}}
        assert resolve_workspaces_locator(tmp_path, doc) == Path("/srv/pods")

    def test_boxes_and_logs_resolvers_default_to_the_spec_formula(self, tmp_path):
        """⚑ ``workset.boxes``/``workset.logs`` are declared keys and resolve exactly
        like ``workspaces``: default ``@meta.workset.path/<leaf>``, an ``@``-ref
        repoint resolved through the one route, absolute repoint used as-is."""
        from kanibako.project.workset import (
            resolve_workset_boxes,
            resolve_workset_logs,
        )

        assert resolve_workset_boxes(tmp_path, None) == tmp_path / "boxes"
        assert resolve_workset_logs(tmp_path, None) == tmp_path / "logs"

        doc = {"workset": {"boxes": "@meta.workset.path/trees", "logs": "/var/log/kani"}}
        assert resolve_workset_boxes(tmp_path, doc) == tmp_path / "trees"
        assert resolve_workset_logs(tmp_path, doc) == Path("/var/log/kani")

    def test_boxes_and_logs_resolvers_ignore_malformed_or_empty_slots(self, tmp_path):
        from kanibako.project.workset import (
            resolve_workset_boxes,
            resolve_workset_logs,
        )

        for doc in (None, {}, {"workset": "oops"}, {"workset": {"boxes": "", "logs": ""}}):
            assert resolve_workset_boxes(tmp_path, doc) == tmp_path / "boxes"
            assert resolve_workset_logs(tmp_path, doc) == tmp_path / "logs"

    # -- NAMED: load_workset captures the repoint --------------------------

    def test_named_workspaces_dir_follows_repoint(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc, load_doc

        root = tmp_home / "worksets" / "repointed"
        create_workset("repointed", root, std)

        # Merge the repoint into the root workset.yaml (identity preserved).
        settings = root.resolve() / "workset.yaml"
        data = load_doc(settings)
        data.setdefault("workset", {})["workspaces"] = "@meta.workset.path/pods"
        dump_doc(settings, data)

        ws = load_workset(root, "repointed")
        assert ws.workspaces_dir == root.resolve() / "pods"
        # Unset → the default composition, unchanged.
        ws_default = load_workset(
            create_workset("plain", tmp_home / "worksets" / "plain", std).root, "plain",
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
            std.primary_workset / "workset.yaml",
            {"workset": {"workspaces": "@meta.workset.path/pods"}},
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
        settings = root.resolve() / "workset.yaml"
        data = load_doc(settings)
        data.setdefault("workset", {})["workspaces"] = "@meta.workset.path/pods"
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


class TestInTreeWorkspacePredicate:
    """``is_in_tree_workspace``: the ONE test for "is this the workset's own
    workspace?" — at or under ``ws.root``, or at or under the resolved
    ``workset.workspaces`` dir, each tested with the leaf followed AND with only
    the parent resolved.  External is the negation."""

    def _repointed(self, std, tmp_home, name="ws1"):
        """A NAMED workset whose ``workset.workspaces`` points OUTSIDE its root."""
        from kanibako.settings.config_io import dump_doc, load_doc

        ws = create_workset(name, tmp_home / "worksets" / name, std)
        data = load_doc(ws.root / "workset.yaml")
        data.setdefault("workset", {})["workspaces"] = str(tmp_home / "wsdata1")
        dump_doc(ws.root / "workset.yaml", data)
        return load_workset(ws.root, name)

    def test_a_path_under_the_root_is_in_tree(self, std, tmp_home):
        from kanibako.project.workset import is_in_tree_workspace

        ws = create_workset("ws1", tmp_home / "worksets" / "ws1", std)
        assert is_in_tree_workspace(ws, ws.root / "workspaces" / "alpha")
        assert is_in_tree_workspace(ws, ws.root)

    def test_a_repointed_member_leaf_is_in_tree(self, std, tmp_home):
        """The repoint puts the leaf outside ``ws.root``, so a root-only test read
        the box's own workspace as EXTERNAL and a relocation deleted it."""
        from kanibako.project.workset import is_in_tree_workspace

        ws = self._repointed(std, tmp_home)
        assert is_in_tree_workspace(ws, tmp_home / "wsdata1" / "alpha")

    def test_any_path_under_a_repointed_workspaces_dir_is_in_tree(self, std, tmp_home):
        """Not only ``workspaces/<name>``: the whole repointed dir is the workset's."""
        from kanibako.project.workset import is_in_tree_workspace

        ws = self._repointed(std, tmp_home)
        assert is_in_tree_workspace(ws, tmp_home / "wsdata1" / "other" / "deeper")

    def test_a_symlinked_leaf_is_in_tree_by_the_link_that_names_it(self, std, tmp_home):
        """The leaf resolves OUTSIDE the root, so only the parent-resolved arm
        answers — and a link is the workset's own leaf, never the user's dir."""
        from kanibako.project.workset import is_in_tree_workspace

        real = tmp_home / "real"
        real.mkdir()
        ws = create_workset("ws1", tmp_home / "worksets" / "ws1", std)
        (ws.workspaces_dir / "alpha").symlink_to(real)
        assert is_in_tree_workspace(ws, ws.workspaces_dir / "alpha")

    def test_a_true_external_path_is_not_in_tree(self, std, tmp_home):
        from kanibako.project.workset import is_in_tree_workspace

        ws = create_workset("ws1", tmp_home / "worksets" / "ws1", std)
        outside = tmp_home / "elsewhere" / "alpha"
        assert not is_in_tree_workspace(ws, outside)
        # A sibling whose name merely starts with the root's is not under it.
        assert not is_in_tree_workspace(ws, tmp_home / "worksets" / "ws1-other" / "a")


class TestWorksetBoxesAndLogsResolved:
    """``workset.boxes`` / ``workset.logs`` are DECLARED, repointable keys (keyspec
    ``@meta.workset.path/boxes``, ``@meta.workset.path/logs``), and the launch seam
    has always resolved them (``settings_launch``, ``meta.box.path |
    @workset.boxes/@meta.box.name``; ``data/rom/settings/core-defaults.yaml``,
    ``@workset.logs/@{meta.box.name}.jsonl``).

    ⚑⚑ THE STATE THIS CLOSES was worse than plain breakage.  Detection resolved both
    keys while ``Workset.projects_dir`` / ``.logs_dir`` composed the default leaf, so a
    repointed root was FOUND and then WRITTEN TO SOMEWHERE ELSE — detected but
    mislocated.  These pin the store side: every creator, mover and remover of a box
    tree, and the hub's helper-log writer, land on the RESOLVED key.
    """

    # -- the properties ----------------------------------------------------

    def test_default_is_the_spec_formula(self, std, tmp_home):
        root = tmp_home / "worksets" / "plain"
        ws = create_workset("plain", root, std)

        resolved = root.resolve()
        assert ws.projects_dir == resolved / "boxes"
        assert ws.logs_dir == resolved / "logs"

    def test_properties_honor_a_repoint(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "moved").resolve()
        ws = create_workset("moved", root, std)
        dump_doc(root / "workset.yaml", {"workset": {
            "boxes": "@meta.workset.path/store",
            "logs": str(tmp_home / "elsewhere-logs"),
        }})

        assert ws.projects_dir == root / "store"
        assert ws.logs_dir == tmp_home / "elsewhere-logs"

    # -- the STORE: box trees are created and removed at the repoint --------

    def test_add_project_places_the_box_tree_at_the_repoint(self, std, tmp_home):
        """⚑ MUTATION-PROVER, through the real ``add_project`` chain: the box tree
        lands under the resolved ``workset.boxes`` and NOT under the default leaf."""
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "boxstore").resolve()
        ws = create_workset("boxstore", root, std)
        store = tmp_home / "external-boxes"
        store.mkdir()
        dump_doc(root / "workset.yaml", {"workset": {"boxes": str(store)}})

        add_project(ws, "cool-app", tmp_home / "project")

        assert (store / "cool-app").is_dir()
        assert not (root / "boxes" / "cool-app").exists()

    def test_remove_project_removes_the_box_tree_at_the_repoint(self, std, tmp_home):
        """⚑ The remover must match the creator: deleting the composed default while
        the real tree sits at the repoint orphans the box AND removes a dir it never
        used — exactly the failure ``remove_project`` already documents for the vault."""
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "boxstore2").resolve()
        ws = create_workset("boxstore2", root, std)
        store = tmp_home / "external-boxes-2"
        store.mkdir()
        dump_doc(root / "workset.yaml", {"workset": {"boxes": str(store)}})

        add_project(ws, "proj", tmp_home / "project")
        assert (store / "proj").is_dir()

        remove_project(ws, "proj", remove_files=True, std=std)
        assert not (store / "proj").exists()

    def test_delete_workset_clears_an_in_root_repointed_box_store(self, std, tmp_home):
        """⚑ The J-7 pre-pass is owed to exactly the trees ``rmtree(root)`` reaches, so
        a repoint that stays UNDER the root must still get the unshare escalation."""
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "instore").resolve()
        ws = create_workset("instore", root, std)
        dump_doc(root / "workset.yaml", {"workset": {"boxes": "@meta.workset.path/store"}})
        add_project(ws, "proj", tmp_home / "project")
        assert (root / "store" / "proj").is_dir()

        delete_workset("instore", std, remove_files=True)
        assert not root.exists()

    # -- the helper-log WRITER (migration M-14) ----------------------------

    def test_helper_log_path_honors_a_named_logs_repoint(self, std, tmp_home):
        """⚑⚑ M-14: the helpers.jsonl MOUNT is the spec spelling
        ``@workset.logs/@{meta.box.name}.jsonl``, so a composed writer path made the
        hub write where the box does not read.  Both must name one file."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import ProjectGroup, ProjectPaths, helper_log_path

        root = (tmp_home / "worksets" / "logmove").resolve()
        ws = create_workset("logmove", root, std)
        elsewhere = tmp_home / "log-store"
        dump_doc(root / "workset.yaml", {"workset": {"logs": str(elsewhere)}})

        proj = ProjectPaths(
            project_path=ws.workspaces_dir / "b", project_hash="h" * 12,
            metadata_path=ws.projects_dir / "b",
            shell_path=ws.projects_dir / "b" / "home",
            vault_ro_path=ws.vault_ro_dir / "b", vault_rw_path=ws.vault_rw_dir / "b",
            is_new=False, mode=BoxMode.named, _enable_vault=False, name="b",
            group=ProjectGroup(name="logmove", root=root, is_default=False,
                               local_shared_base=root),
        )
        assert helper_log_path(std, proj) == elsewhere / "b.jsonl"

    def test_primary_box_and_log_roots_honor_a_primary_repoint(self, tmp_home,
                                                               config_file):
        """⚑ The PRIMARY workset root is an ordinary workset root: ``std.boxes`` and
        ``std.primary_logs`` are SURROGATES for its ``workset.{boxes,logs}``, so they
        resolve the same keys the named arm does."""
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        pw = std.primary_workset
        pw.mkdir(parents=True, exist_ok=True)
        dump_doc(pw / "workset.yaml", {"workset": {
            "boxes": str(tmp_home / "pw-boxes"),
            "logs": "@meta.workset.path/log-archive",
        }})

        moved = load_std_paths(load_config(config_file))
        assert moved.boxes == tmp_home / "pw-boxes"
        assert moved.primary_logs == pw / "log-archive"

    # -- tripwire: the literal joins must not come back --------------------

    def test_no_boxes_or_logs_literal_join_remains_at_the_sites(self):
        """Pins the collapse the way its ``workspaces`` sibling above is pinned: at
        the composition SITES, so a reintroduced join reds without anyone having to
        re-run the census.  ⚑ The banned spellings include the LEAF CONSTANTS, not
        just the bare strings — ``pw / BOXES_PATH`` was one of the defects, and a
        string-only pin would have passed it.
        ⚑ The file list is the same three the ``workspaces`` pin above names: the
        modules that compose a path out of a workset-root child.  ``project/names.py``
        carries no ``boxes``/``logs`` join today — it speaks only of the ``boxes:``
        MEMBERSHIP section, which is a different thing wearing the same word — and is
        listed so that a real one cannot appear there unremarked."""
        import re

        from tests.support.repo import REPO_ROOT

        src = REPO_ROOT / "src" / "kanibako"
        # file -> the spellings of the two leaves that are IN SCOPE there.  ⚑ Derived
        # from the rule ("the only allowed spelling is the resolver's own argument"),
        # never from an inventory of today's lines.
        banned = {
            "settings/paths.py": ('"boxes"', "BOXES_PATH", '"logs"', "LOGS_PATH"),
            "project/workset.py": ('"boxes"', "BOXES_DIR_NAME", '"logs"', "_LOGS_LEAF"),
            "project/names.py": ('"boxes"', "BOXES_PATH", '"logs"', "LOGS_PATH"),
        }
        for rel, spellings in banned.items():
            text = (src / rel).read_text(encoding="utf-8")
            for spelling in spellings:
                join_re = re.compile(
                    r"/\s*%s|%s\s*/" % (re.escape(spelling), re.escape(spelling)))
                assert not join_re.search(text), (
                    f"literal {spelling} join in {rel}; route it through "
                    f"resolve_workset_boxes / resolve_workset_logs"
                )


class TestWorksetLogsPresentNone:
    """A present ``<None>`` ``workset.logs`` is a VALUE, not an absence ([R177]).

    The companion ("The helper-log bind"): a ``null`` ``workset.logs`` makes the bind's
    source ``<None>`` and the bind is omitted.  So the key resolves to NO dir — never to
    its default — and every log reader, writer and remover sees that one answer.
    """

    def test_the_repoint_read_keeps_three_states(self, tmp_path):
        from kanibako.settings.settings_resolve import UNSET
        from kanibako.settings.workset_dirkeys import early_repoint

        def read(doc):
            return early_repoint(tmp_path, doc, "logs")[0]

        assert read(None) is UNSET
        assert read({"workset": {}}) is UNSET
        assert read({"workset": {"logs": None}}) is None
        assert read({"workset": {"logs": "/x"}}) == "/x"
        # ``""`` is not yet distinguished from unset; it keeps its old answer.
        assert read({"workset": {"logs": ""}}) is UNSET

    def test_a_null_logs_resolves_to_no_dir_in_every_mode(self, tmp_path):
        from kanibako.project.workset import resolve_workset_logs

        doc = {"workset": {"logs": None}}
        assert resolve_workset_logs(tmp_path, doc) is None
        assert resolve_workset_logs(tmp_path, doc, standalone=True) is None

    def test_a_named_workset_has_no_logs_dir_and_still_detects(self, std, tmp_home):
        from kanibako.project.workset import is_workset_skeleton
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "nolog").resolve()
        ws = create_workset("nolog", root, std)
        dump_doc(root / "workset.yaml", {"workset": {"logs": None}})
        assert ws.logs_dir is None
        # The skeleton is the other three dirs; the default ``logs/`` is irrelevant.
        (root / "logs").rmdir()
        assert is_workset_skeleton(root)

    def test_named_box_log_paths_are_none_and_removal_is_a_no_op(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import (
            ProjectGroup, ProjectPaths, creds_watcher_log_path, helper_log_path,
            remove_box_logs,
        )

        root = (tmp_home / "worksets" / "nolog2").resolve()
        ws = create_workset("nolog2", root, std)
        dump_doc(root / "workset.yaml", {"workset": {"logs": None}})
        proj = ProjectPaths(
            project_path=ws.workspaces_dir / "b", project_hash="h" * 12,
            metadata_path=ws.projects_dir / "b",
            shell_path=ws.projects_dir / "b" / "home",
            vault_ro_path=ws.vault_ro_dir / "b", vault_rw_path=ws.vault_rw_dir / "b",
            is_new=False, mode=BoxMode.named, _enable_vault=False, name="b",
            group=ProjectGroup(name="nolog2", root=root, is_default=False,
                               local_shared_base=root),
        )
        assert helper_log_path(std, proj) is None
        assert creds_watcher_log_path(std, proj) is None
        assert remove_box_logs(None, "b") == []

    def test_a_null_primary_logs_is_none_on_std(self, tmp_home, config_file):
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        std.primary_workset.mkdir(parents=True, exist_ok=True)
        dump_doc(std.primary_workset / "workset.yaml", {"workset": {"logs": None}})
        assert load_std_paths(load_config(config_file)).primary_logs is None

    def test_the_helper_table_keeps_the_log_formula_for_the_collapse(self, tmp_path):
        """A ``<None>`` log keeps the FORMULA: the collapse omits it and [R185] warns.

        Omitting it in the table builder was a silent second route (C5).
        """
        from kanibako.settings import core_defaults

        sock = tmp_path / "helper.sock"
        sock.touch()
        table = core_defaults.helper_default_categories(socket_path=sock, log_path=None)
        sources = {dest: entry[0] for arm in table.values() for dest, entry in arm.items()}
        assert set(sources) == set(core_defaults.helper_bind_dests())
        log_dest = next(d for d in sources if d.endswith(".jsonl"))
        assert sources[log_dest].startswith("@workset.logs/")


class TestWorksetBoxesPresentNone:
    """A present ``<None>`` ``workset.boxes`` REFUSES, naming the key and the file (Q96).

    Every box's home and settings live under the box store, so a ``null`` store is not
    "take the default" ([R177]) — that collapse put a connected box under ``<root>/boxes``.
    """

    def test_a_null_boxes_refuses_by_name_in_every_mode(self, tmp_path):
        from kanibako.project.workset import resolve_workset_boxes, resolve_workset_logs
        from kanibako.settings.settings_resolve import SettingsError

        doc = {"workset": {"boxes": None}}
        for call in (
            lambda: resolve_workset_boxes(tmp_path, doc),
            lambda: resolve_workset_boxes(tmp_path, doc, standalone=True),
            # A lone box's default logs dir is ``@workset.boxes``.
            lambda: resolve_workset_logs(tmp_path, doc, standalone=True),
        ):
            with pytest.raises(SettingsError) as exc:
                call()
            assert "workset.boxes" in str(exc.value)
            assert str(tmp_path / "workset.yaml") in str(exc.value)

    def test_a_named_workset_with_a_null_store_creates_no_box_tree(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.settings_resolve import SettingsError

        root = (tmp_home / "worksets" / "nobox").resolve()
        ws = create_workset("nobox", root, std)
        dump_doc(root / "workset.yaml", {"workset": {"boxes": None}})
        source = tmp_home / "src"
        source.mkdir()
        with pytest.raises(SettingsError, match="workset.boxes"):
            add_project(ws, "ex", source, std)
        assert not (root / "boxes" / "ex").exists()
        assert load_workset(root, "nobox").projects == []

    def _member_then_null_store(self, std, tmp_home, ws_name):
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / ws_name).resolve()
        ws = create_workset(ws_name, root, std)
        source = tmp_home / f"{ws_name}-src"
        source.mkdir()
        add_project(ws, "m", source, std)
        assert (root / "boxes" / "m").is_dir()
        dump_doc(root / "workset.yaml", {"workset": {"boxes": None}})
        return root

    def test_purging_a_workset_refuses_while_it_is_still_registered(self, std, tmp_home):
        from kanibako.settings.settings_resolve import SettingsError

        root = self._member_then_null_store(std, tmp_home, "purgeme")
        with pytest.raises(SettingsError, match="workset.boxes"):
            delete_workset("purgeme", std, remove_files=True)
        assert "purgeme" in list_worksets(std)
        assert (root / "boxes" / "m").is_dir()

    def test_disconnecting_with_files_refuses_while_the_member_is_registered(
        self, std, tmp_home,
    ):
        from kanibako.settings.settings_resolve import SettingsError

        root = self._member_then_null_store(std, tmp_home, "keepme")
        ws = load_workset(root, "keepme")
        with pytest.raises(SettingsError, match="workset.boxes"):
            remove_project(ws, "m", remove_files=True)
        assert [p.name for p in load_workset(root, "keepme").projects] == ["m"]
        assert (root / "boxes" / "m").is_dir()


class TestWorksetFiveDirKeysCarryNull:
    """A present ``<None>`` is a VALUE at all five workset dir keys — never "unset".

    spec §2a omits a bind whose source is ``<None>``, and §2c's STANDALONE table declares
    ``workset.{registry,template} | <None>``, so the template layer is SKIPPED rather than
    refused.  MIGRATION *"A `null` setting inside a bind's source leaves the bind out…"*
    and *"One of kanibako's own binds warns when only its entry or only its source is
    `null`"* are the whole of it: the user writes ``workset.vault_ro: null`` and kanibako
    leaves the RO bind out, warning only when one half of the bind is ``<None>``.  Only an
    ABSENT key takes a default.

    ``workset.logs``/``workset.workspaces`` already carry it and ``workset.boxes`` still
    refuses it (MIGRATION "A `workset.boxes` of `null` is refused"); this class covers the
    five that did neither.
    """

    #: ⚑ ONE list, swept once: five copies of this test is five places to forget a key.
    _NULL_CARRYING_KEYS = (
        ("channelroot", "resolve_workset_channelroot", "channels"),
        ("canon", "resolve_workset_canon", "canon"),
        ("template", "resolve_workset_template", "template"),
        ("vault_ro", "resolve_workset_vault_ro", "vault/ro"),
        ("vault_rw", "resolve_workset_vault_rw", "vault/rw"),
    )

    @staticmethod
    def _resolver(name):
        from kanibako.project import workset as ws_mod

        return getattr(ws_mod, name)

    def test_a_present_null_carries_and_never_takes_the_default_leaf(self, tmp_path):
        for key, resolver_name, default in self._NULL_CARRYING_KEYS:
            resolver = self._resolver(resolver_name)
            assert resolver(tmp_path, {"workset": {key: None}}) is None, key
            # 🛑 The regression this pins: the default leaf is a path the user said does
            # not exist, and it is a real directory underneath this root.
            assert not (tmp_path / default).exists(), key

    def test_an_absent_key_still_takes_its_default_leaf(self, tmp_path):
        """GUARD for the other half of the rule: UNSET (key absent) still falls back."""
        for key, resolver_name, default in self._NULL_CARRYING_KEYS:
            resolver = self._resolver(resolver_name)
            assert resolver(tmp_path, None) == tmp_path / default, key
            assert resolver(tmp_path, {"workset": {}}) == tmp_path / default, key
            assert resolver(tmp_path, {"workset": {key: "/srv/elsewhere"}}) == Path(
                "/srv/elsewhere"
            ), key

    def test_one_null_arm_leaves_the_other_on_its_own_default(self, tmp_path):
        """GUARD: the two vault arms resolve INDEPENDENTLY, so neither answers for the other."""
        doc = {"workset": {"vault_ro": None}}
        assert self._resolver("resolve_workset_vault_ro")(tmp_path, doc) is None
        assert self._resolver("resolve_workset_vault_rw")(tmp_path, doc) == (
            tmp_path / "vault" / "rw"
        )


class TestWorksetWorkspacesPresentNone:
    """A present ``<None>`` ``workset.workspaces`` = no workspace dir (Q96, [R177]).

    An in-tree member IS a workspace there, so creating one refuses by name; an external
    member keeps its own dir and still connects, without a discoverability link.
    """

    def _null_workspaces(self, std, tmp_home, ws_name):
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / ws_name).resolve()
        ws = create_workset(ws_name, root, std)
        dump_doc(root / "workset.yaml", {"workset": {"workspaces": None}})
        return ws, root

    def test_an_in_tree_member_refuses_naming_the_key_and_file(self, std, tmp_home):
        from kanibako.errors import WorksetError

        ws, root = self._null_workspaces(std, tmp_home, "nows")
        source = root / "intree"
        source.mkdir()
        with pytest.raises(WorksetError) as exc:
            add_project(ws, "app", source, std)
        assert "workset.workspaces" in str(exc.value)
        assert str(root / "workset.yaml") in str(exc.value)
        assert not (root / "workspaces" / "app").exists()
        assert not (root / "boxes" / "app").exists()
        assert load_workset(root, "nows").projects == []

    def test_an_external_member_connects_without_a_link(self, std, tmp_home):
        ws, root = self._null_workspaces(std, tmp_home, "extws")
        source = (tmp_home / "ext-src").resolve()
        source.mkdir()
        add_project(ws, "ext", source, std)
        [member] = load_workset(root, "extws").projects
        assert (member.name, member.source_path) == ("ext", source)
        assert not (root / "workspaces" / "ext").exists()
        assert not (root / "workspaces" / "ext").is_symlink()

    def test_workspaces_dir_is_none_never_the_default_leaf(self, std, tmp_home):
        ws, root = self._null_workspaces(std, tmp_home, "nodir")
        assert ws.workspaces_dir is None
        assert load_workset(root, "nodir").workspaces_dir is None
        with pytest.raises(WorksetError) as exc:
            ws.require_workspaces_dir("a workspace for 'app'")
        assert "workset.workspaces" in str(exc.value)
        assert str(root / "workset.yaml") in str(exc.value)

    def test_an_external_member_disconnects(self, std, tmp_home):
        ws, root = self._null_workspaces(std, tmp_home, "extrm")
        source = (tmp_home / "ext-rm").resolve()
        source.mkdir()
        add_project(ws, "ext", source, std)
        remove_project(load_workset(root, "extrm"), "ext", remove_files=True)
        assert load_workset(root, "extrm").projects == []
        assert source.is_dir()  # an external source dir is never touched

    def test_remove_files_deletes_an_in_tree_members_recorded_leaf(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "inrm").resolve()
        ws = create_workset("inrm", root, std)
        add_project(ws, "app", root / "workspaces" / "app", std)
        dump_doc(root / "workset.yaml", {"workset": {"workspaces": None}})
        remove_project(load_workset(root, "inrm"), "app", remove_files=True)
        assert not (root / "workspaces" / "app").exists()

    def test_a_link_made_before_the_null_is_unlinked_on_disconnect(self, std, tmp_home):
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "oldlink").resolve()
        ws = create_workset("oldlink", root, std)
        source = (tmp_home / "ext-old").resolve()
        source.mkdir()
        add_project(ws, "ext", source, std)
        assert (root / "workspaces" / "ext").is_symlink()
        dump_doc(root / "workset.yaml", {"workset": {"workspaces": None}})
        remove_project(load_workset(root, "oldlink"), "ext")
        assert not (root / "workspaces" / "ext").is_symlink()
        assert source.is_dir()

    def test_remove_files_never_deletes_the_workset_root(self, std, tmp_home):
        # A hand-edited record naming the root itself is not a leaf to delete.
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "rootrm").resolve()
        ws = create_workset("rootrm", root, std)
        add_project(ws, "app", root / "workspaces" / "app", std)
        dump_doc(root / "workset.yaml", {"workset": {"workspaces": None}})
        loaded = load_workset(root, "rootrm")
        loaded.projects[0].source_path = root
        remove_project(loaded, "app", remove_files=True)
        assert root.is_dir()
        assert (root / "workset.yaml").is_file()

    def test_an_unset_workspaces_still_holds_in_tree_members(self, std, tmp_home):
        root = (tmp_home / "worksets" / "plainws").resolve()
        ws = create_workset("plainws", root, std)
        source = root / "intree"
        source.mkdir()
        add_project(ws, "app", source, std)
        assert (root / "workspaces" / "app").is_dir()


class TestWorksetIdentityIsTheGlobalRegistry:
    """⚑⚑ A workset's identity is its ``worksets:`` entry in the GLOBAL registry, and
    nothing else.  Neither file under the root records a name: ``registry.yaml`` holds
    the flat ``boxes:`` membership and ``workset.yaml`` holds SETTINGS ONLY — sparse,
    optional, and ABSENT on a freshly created workset."""

    def test_no_file_under_the_root_names_the_workset(self, std, tmp_home):
        root = tmp_home / "worksets" / "mset"
        ws = create_workset("mset", root, std)
        add_project(ws, "proj", ws.workspaces_dir / "proj", std)

        from kanibako.settings.config_io import load_doc

        resolved = root.resolve()
        assert not (resolved / "workset.yaml").exists()
        assert not (resolved / "workset.yaml").exists()
        # ⚑ The ONE file under the root is the membership, and it holds ONE section
        # of box rows — no name, no created stamp, no table about the workset itself.
        files = sorted(p for p in resolved.rglob("*") if p.is_file())
        assert files == [resolved / "registry.yaml"], files
        assert set(load_doc(resolved / "registry.yaml")) == {"boxes"}

    def test_the_name_is_in_the_global_registry(self, std, tmp_home):
        from kanibako.project import registry_store

        root = tmp_home / "worksets" / "mset"
        create_workset("mset", root, std)

        section = registry_store.load_section(std.registry, "worksets")
        assert section["mset"] == str(root.resolve())

    def test_membership_write_touches_only_the_boxes_section(self, std, tmp_home):
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        root = tmp_home / "worksets" / "coexist"
        ws = create_workset("coexist", root, std)
        workset_registry.register_workset_box(
            ws.registry_path, "boxa", tmp_home / "elsewhere",
        )
        add_project(ws, "proj", ws.workspaces_dir / "proj", std)

        assert set(load_doc(ws.registry_path)) == {"boxes"}
        assert workset_registry.load_workset_boxes(ws.registry_path) == {
            "boxa": str(tmp_home / "elsewhere"),
            "proj": str(ws.workspaces_dir / "proj"),
        }

    def test_the_registry_names_it_but_the_skeleton_finds_it(
        self, std, tmp_home, config,
    ):
        """⚑⚑ [R139]: NAMING is registry-borne, FINDING is not.  Drop the global
        registration and the same directory is still a workset root on disk — the
        ancestor walk finds it by skeleton and re-imports it under its leaf name."""
        from kanibako.project import registry_store
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "worksets" / "marker"
        create_workset("marker", root, std)
        assert detect_project_mode(root, std, config).mode is BoxMode.named

        registry_store.save_section(std.registry, "worksets", {})
        assert detect_project_mode(root, std, config).mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets") == {
            "marker": str(root.resolve())
        }

    def test_a_settings_file_is_optional_end_to_end(self, std, tmp_home, config):
        """With no workset.yaml at all, the root still detects, loads and lists."""
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "worksets" / "sparse"
        ws = create_workset("sparse", root, std)
        add_project(ws, "proj", ws.workspaces_dir / "proj")
        assert not ws.settings_path.exists()

        assert load_workset(root, "sparse").name == "sparse"
        assert [p.name for p in load_workset(root, "sparse").projects] == ["proj"]
        assert detect_project_mode(root, std, config).mode is BoxMode.named


class TestWorksetSkeletonMarker:
    """``is_workset_skeleton`` — the on-disk marker the ancestor walk looks for.

    ⚑ Presence-only and name-free, exactly like ``_is_standalone_meta_dir``: it
    answers *"is a workset here"*, never *"what is it called"* ([R139]).
    """

    def test_a_created_workset_root_is_a_skeleton(self, std, tmp_home):
        from kanibako.project.workset import is_workset_skeleton

        root = tmp_home / "worksets" / "whole"
        create_workset("whole", root, std)
        assert is_workset_skeleton(root)

    def test_create_and_detect_share_one_definition(self, std, tmp_home):
        """⚑⚑ MUTATION-PROOF against drift: the dirs ``create_workset`` stamps are
        the dirs the predicate tests, because both call ``_workset_skeleton_dirs``.
        Remove ANY of them and detection stops — no leaf name is hard-coded twice."""
        import shutil

        from kanibako.project.workset import _workset_skeleton_dirs, is_workset_skeleton

        root = tmp_home / "worksets" / "shared"
        create_workset("shared", root, std)
        stamped = sorted(p for p in root.resolve().iterdir() if p.is_dir())
        assert stamped == sorted(_workset_skeleton_dirs(root.resolve()))

        for missing in stamped:
            shutil.move(str(missing), str(root.resolve() / "parked"))
            assert not is_workset_skeleton(root), missing
            shutil.move(str(root.resolve() / "parked"), str(missing))
            assert is_workset_skeleton(root)

    def test_a_partial_skeleton_is_not_a_workset(self, tmp_home):
        """A directory with SOME of the leaf names is an ordinary directory."""
        from kanibako.project.workset import is_workset_skeleton

        root = tmp_home / "partial"
        (root / "boxes").mkdir(parents=True)
        (root / "workspaces").mkdir()
        assert not is_workset_skeleton(root)

    def test_an_empty_or_absent_dir_is_not_a_workset(self, tmp_home):
        from kanibako.project.workset import is_workset_skeleton

        empty = tmp_home / "empty"
        empty.mkdir()
        assert not is_workset_skeleton(empty)
        assert not is_workset_skeleton(tmp_home / "does-not-exist")

    def test_a_file_named_like_a_skeleton_dir_does_not_count(self, tmp_home):
        """⚑ The test is ``is_dir``: a FILE called ``logs`` is not the logs dir."""
        from kanibako.project.workset import is_workset_skeleton

        root = tmp_home / "filey"
        for leaf in ("boxes", "workspaces", "vault"):
            (root / leaf).mkdir(parents=True, exist_ok=True)
        (root / "logs").write_text("not a directory\n", encoding="utf-8")
        assert not is_workset_skeleton(root)

    def test_a_repointed_workspaces_dir_still_detects(self, std, tmp_home):
        """⚑ ``workspaces`` is resolved through ``workset.workspaces``, so a
        REPOINTED root is still a skeleton — and the DEFAULT leaf no longer is."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.project.workset import is_workset_skeleton

        root = (tmp_home / "worksets" / "moved").resolve()
        create_workset("moved", root, std)

        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        dump_doc(root / "workset.yaml", {"workset": {"workspaces": str(elsewhere)}})
        # The repoint is honored: the default ``workspaces/`` dir is now irrelevant.
        assert is_workset_skeleton(root)
        (root / "workspaces").rmdir()
        assert is_workset_skeleton(root)
        # ...and the repoint TARGET is what must exist.
        elsewhere.rmdir()
        assert not is_workset_skeleton(root)

    def test_a_repointed_boxes_dir_still_detects(self, std, tmp_home):
        """⚑ ``workset.boxes`` is a DECLARED, repointable key (keyspec:
        ``workset.boxes | @meta.workset.path/boxes``), so the locator is what it
        RESOLVES to.  Testing the literal ``<root>/boxes`` made a repointed root
        invisible to detection — the repoint is precisely what removes that dir."""
        from kanibako.project.workset import is_workset_skeleton
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "boxmoved").resolve()
        create_workset("boxmoved", root, std)

        elsewhere = tmp_home / "elsewhere-boxes"
        elsewhere.mkdir()
        dump_doc(root / "workset.yaml", {"workset": {"boxes": str(elsewhere)}})
        assert is_workset_skeleton(root)
        # The default leaf is now irrelevant...
        (root / "boxes").rmdir()
        assert is_workset_skeleton(root)
        # ...and the repoint TARGET is what must exist.
        elsewhere.rmdir()
        assert not is_workset_skeleton(root)

    def test_a_repointed_logs_dir_still_detects(self, std, tmp_home):
        """⚑ Same for ``workset.logs`` (keyspec: ``@meta.workset.path/logs``).  The
        comment that once stood over ``_LOGS_LEAF`` claimed no ``workset.*`` key
        named it; the keyspec has declared one all along."""
        from kanibako.project.workset import is_workset_skeleton
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "logmoved").resolve()
        create_workset("logmoved", root, std)

        elsewhere = tmp_home / "elsewhere-logs"
        elsewhere.mkdir()
        dump_doc(root / "workset.yaml", {"workset": {"logs": str(elsewhere)}})
        assert is_workset_skeleton(root)
        (root / "logs").rmdir()
        assert is_workset_skeleton(root)
        elsewhere.rmdir()
        assert not is_workset_skeleton(root)

    def test_absent_settings_file_yields_the_default_leaves(self, tmp_home):
        """⚑⚑ LOOK AT workset.yaml, never DEPEND on it.  A workset root's settings
        file is OPTIONAL and absent on a fresh create, so an absent file must yield
        the four default leaves — which is exactly what ``create_workset`` stamps.
        (A regression guard, not a mutation-prover: it held before the resolution
        change too, and it is what must keep holding after it.)"""
        from kanibako.project.workset import _workset_skeleton_dirs

        root = tmp_home / "nofile"
        root.mkdir()
        assert not (root / "workset.yaml").exists()
        assert _workset_skeleton_dirs(root) == (
            root / "boxes", root / "workspaces", root / "vault", root / "logs",
        )

    def test_vault_is_the_one_literal_and_no_repoint_moves_it(self, std, tmp_home):
        """⚑ DELIBERATE non-key: the keyspec declares ``workset.vault_ro`` and
        ``workset.vault_rw`` and NO ``workset.vault``, so ``vault/`` is only their
        shared default parent.  Repointing either leaf must NOT move the skeleton's
        vault dir — there is no key to resolve it through."""
        from kanibako.project.workset import _workset_skeleton_dirs, is_workset_skeleton
        from kanibako.settings.config_io import dump_doc

        root = (tmp_home / "worksets" / "vaulted").resolve()
        create_workset("vaulted", root, std)
        dump_doc(root / "workset.yaml", {"workset": {
            "vault_ro": str(tmp_home / "vro"),
            "vault_rw": str(tmp_home / "vrw"),
        }})
        assert root / "vault" in _workset_skeleton_dirs(root)
        assert is_workset_skeleton(root)
        # It is load-bearing for detection despite naming no key.
        (root / "vault").rmdir()
        assert not is_workset_skeleton(root)


# ---------------------------------------------------------------------------
# The RETIRED identity locations.  v1.6.0/v1.7.x wrote ``workset.meta`` into the root
# workset.yaml; the unreleased v1.8.0 tree wrote first ``meta.workset`` into the same
# file and then a ``workset:``/``projects:`` pair into registry.yaml.
#
# v1.8.0 is a clean break: a workset has NO identity table anywhere under its root, it
# is named by the global registry, and there is no compat read and no auto-migration.
# Each retired shape is DETECTED only so it can be DIAGNOSED — a hard refusal naming
# the real end state, in place of the silent wrong answer it would otherwise give.
# ⚑ The distinction that carries the design: a root with NEITHER shape, and an
# unreadable one, are both ordinary directories and must not refuse.
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

#: The same identity as the UNRELEASED v1.8.0 tree first spelled it, same file.
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

#: The registry.yaml the unreleased v1.8.0 tree wrote NEXT — identity + a second
#: copy of every member path, beside the live ``boxes:`` membership.
_UNRELEASED_REGISTRY_DOC = {
    "workset": {"name": "legacyws", "created": "2026-01-02T03:04:05+00:00"},
    "boxes": {"proj": "/somewhere/proj"},
    "projects": {"proj": {"source_path": "/somewhere/proj"}},
}


def _write_legacy_root(root: Path, doc: dict | None = None) -> Path:
    """Materialize a legacy (workset.yaml-identity) workset root; return its settings file."""
    from kanibako.settings.config_io import dump_doc

    root.mkdir(parents=True, exist_ok=True)
    settings = root / "workset.yaml"
    dump_doc(settings, _LEGACY_IDENTITY_DOC if doc is None else doc)
    return settings


def _write_legacy_registry(root: Path, doc: dict | None = None) -> Path:
    """Materialize a root whose REGISTRY carries the retired sections; return that file."""
    from kanibako.settings.config_io import dump_doc

    root.mkdir(parents=True, exist_ok=True)
    registry = root / "registry.yaml"
    dump_doc(registry, _UNRELEASED_REGISTRY_DOC if doc is None else doc)
    return registry


class TestRetiredWorksetIdentityLocation:
    @pytest.mark.parametrize(
        "doc_name", ["_LEGACY_IDENTITY_DOC", "_UNRELEASED_IDENTITY_DOC"],
    )
    def test_legacy_root_refuses(self, tmp_home, doc_name):
        """⚑ EITHER retired spelling RAISES out of the refusal — never a quiet return."""
        from kanibako.project.workset import refuse_retired_workset_identity

        root = tmp_home / "legacyws"
        _write_legacy_root(root, globals()[doc_name])
        with pytest.raises(LegacyWorksetIdentityError):
            refuse_retired_workset_identity(root)

    def test_cure_names_the_file_the_membership_and_the_global_registry(self, tmp_home):
        """The refusal is actionable: the file, the retired location, the real end state."""
        from kanibako.project.workset import refuse_retired_workset_identity

        root = tmp_home / "legacyws"
        settings = _write_legacy_root(root)
        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            refuse_retired_workset_identity(root)
        message = str(excinfo.value)

        assert str(settings) in message
        assert "workset.meta" in message               # the retired location, named
        assert str(root / "registry.yaml") in message  # where MEMBERSHIP goes, named
        # ⚑ The end state, not a relocation: the name is the GLOBAL registry's.
        assert "global registry" in message
        assert "workset create" in message
        # The target shape itself, shown rather than described — FLAT name: path.
        assert "boxes:\n           <project name>: <its source_path>" in message
        # ⚑ Nothing is told to write `created` anywhere; the field is gone.
        assert "`created` is not recorded anywhere in 1.8.0" in message
        # No auto-migration, and the shipped guide is cited BY FILENAME.
        assert "MIGRATION.md §2.43" in message

    def test_cure_names_the_unreleased_spelling_when_that_is_what_is_there(
        self, tmp_home,
    ):
        """The message names the spelling actually on disk, not a generic pair."""
        from kanibako.project.workset import refuse_retired_workset_identity

        root = tmp_home / "legacyws"
        _write_legacy_root(root, _UNRELEASED_IDENTITY_DOC)
        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            refuse_retired_workset_identity(root)
        assert "'meta.workset' is a RETIRED location" in str(excinfo.value)

    def test_refusal_is_a_workset_error(self, tmp_home):
        """``LegacyWorksetIdentityError`` is a ``WorksetError``, so ``cli.py`` prints it."""
        from kanibako.errors import KanibakoError

        assert issubclass(LegacyWorksetIdentityError, WorksetError)
        assert issubclass(LegacyWorksetIdentityError, KanibakoError)

    def test_non_workset_root_does_not_refuse(self, tmp_home):
        """⚑ A root with NEITHER retired shape is an ordinary directory: silence."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.project.workset import refuse_retired_workset_identity

        # A cascade-only ``workset:`` table — present, but with no ``meta:`` under it.
        cascade_only = tmp_home / "cascade-only"
        cascade_only.mkdir()
        dump_doc(
            cascade_only / "workset.yaml",
            {"workset": {"bindings": {"rw": {"~/data": "/host/data"}}}},
        )
        refuse_retired_workset_identity(cascade_only)

        # A workset file carrying nothing but a box-scope table.
        boxlike = tmp_home / "boxlike"
        boxlike.mkdir()
        dump_doc(boxlike / "workset.yaml", {"box": {"image": "img"}})
        refuse_retired_workset_identity(boxlike)

        # A PLAIN directory — nothing on disk at all — and a missing one.
        plain = tmp_home / "plain-dir"
        plain.mkdir()
        refuse_retired_workset_identity(plain)
        refuse_retired_workset_identity(tmp_home / "nothing-here")

    def test_workset_meta_scalar_is_not_the_legacy_table(self, tmp_home):
        """``workset.meta`` must be a MAPPING to be the retired identity — a scalar is not."""
        from kanibako.settings.config_io import dump_doc
        from kanibako.project.workset import refuse_retired_workset_identity

        scalar = tmp_home / "scalar-meta"
        scalar.mkdir()
        dump_doc(scalar / "workset.yaml", {"workset": {"meta": "not-a-table"}})
        refuse_retired_workset_identity(scalar)

    def test_corrupt_file_does_not_refuse(self, tmp_home):
        """⚑ The load guard stays: an unparseable file is a miss, not a refusal."""
        from kanibako.project.workset import refuse_retired_workset_identity

        corrupt = tmp_home / "corrupt"
        corrupt.mkdir()
        (corrupt / "workset.yaml").write_text("workset:\n  meta:\n   - [broken: :\n")
        refuse_retired_workset_identity(corrupt)

    def test_live_shape_still_works(self, std, tmp_home):
        """A 1.8.0 root — no identity table anywhere — is untouched by the refusal."""
        from kanibako.project.workset import refuse_retired_workset_identity

        root = tmp_home / "worksets" / "goodws"
        create_workset("goodws", root, std)
        refuse_retired_workset_identity(root.resolve())
        assert load_workset(root, "goodws").name == "goodws"

    def test_load_workset_refuses_legacy_root(self, tmp_home):
        """The LOAD path gets the named cure too, not a silently empty workset."""
        root = tmp_home / "legacyws"
        _write_legacy_root(root)
        with pytest.raises(LegacyWorksetIdentityError):
            load_workset(root, "legacyws")

    def test_refusal_survives_the_ancestor_walk(self, std, tmp_home, config):
        """⚑⚑ THE POINT: ``detect_project_mode``'s upward walk propagates it, from a SUBDIR."""
        from kanibako.settings.paths import detect_project_mode

        root = tmp_home / "legacyws"
        _write_legacy_root(root)
        deep = root / "workspaces" / "proj"
        deep.mkdir(parents=True)

        with pytest.raises(LegacyWorksetIdentityError) as excinfo:
            detect_project_mode(deep, std, config)
        assert str(root / "workset.yaml") in str(excinfo.value)

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
        assert str(root / "workset.yaml") in str(excinfo.value)

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
# A MALFORMED workset.yaml — the reader, and the ONE caller that stays tolerant
# ---------------------------------------------------------------------------

#: The three shapes ``load_doc`` refuses: unparseable, a top-level list, a scalar.
_MALFORMED_WORKSET_DOCS = [
    pytest.param("workset:\n  meta:\n   - [broken: :\n", "not valid YAML", id="invalid-yaml"),
    pytest.param("- one\n- two\n", "is a list, not a mapping", id="top-level-list"),
    pytest.param("just-a-scalar\n", "is a single value, not a mapping", id="top-level-scalar"),
]


class TestMalformedWorksetSettingsDoc:
    """A workset root's OWN ``workset.yaml`` is read like every other settings file.

    Reading a malformed one as "no file" is not a safe default: it repoints every
    resolved workset key — including the vault arms a teardown deletes by — to its
    default, in silence.
    """

    @pytest.mark.parametrize("malformed,expected", _MALFORMED_WORKSET_DOCS)
    def test_reader_refuses_and_names_the_file(self, tmp_home, malformed, expected):
        from kanibako.errors import ConfigError
        from kanibako.project.workset import load_workset_settings_doc

        root = tmp_home / "ws"
        root.mkdir()
        (root / "workset.yaml").write_text(malformed)

        with pytest.raises(ConfigError) as excinfo:
            load_workset_settings_doc(root)
        assert expected in str(excinfo.value)
        assert str(root / "workset.yaml") in str(excinfo.value)

    def test_absent_file_is_still_a_miss(self, tmp_home):
        """``None`` still means ABSENT — the settings file is optional by design."""
        from kanibako.project.workset import load_workset_settings_doc

        root = tmp_home / "ws"
        root.mkdir()
        assert load_workset_settings_doc(root) is None

    @pytest.mark.parametrize("malformed,expected", _MALFORMED_WORKSET_DOCS)
    def test_load_workset_refuses_it(self, tmp_home, malformed, expected):
        """The LOAD path refuses, so ``workset info`` names the file instead of lying."""
        from kanibako.errors import ConfigError

        root = tmp_home / "brokenws"
        root.mkdir(parents=True)
        (root / "workset.yaml").write_text(malformed)

        with pytest.raises(ConfigError) as excinfo:
            load_workset(root.resolve(), "brokenws")
        assert expected in str(excinfo.value)

    @pytest.mark.parametrize("malformed", [
        pytest.param("workset:\n  meta:\n   - [broken: :\n", id="invalid-yaml"),
        pytest.param("- one\n- two\n", id="top-level-list"),
        pytest.param("just-a-scalar\n", id="top-level-scalar"),
    ])
    def test_the_ancestor_walk_still_falls_through(
        self, std, tmp_home, config, malformed,
    ):
        """⚑ THE CARVE-OUT, at the walk: a FOREIGN ``workset.yaml`` above the cwd is a
        miss, not a refusal — most directories the walk passes over are not ours."""
        from kanibako.settings.paths import detect_project_mode

        plain = tmp_home / "plain" / "sub"
        plain.mkdir(parents=True)
        (tmp_home / "workset.yaml").write_text(malformed)

        assert detect_project_mode(plain, std, config).mode is BoxMode.primary


class TestRetiredRegistrySections:
    """The OTHER retired shape: an unreleased 1.8.0 build put the identity — and a
    second copy of every member path — into the per-workset ``registry.yaml``."""

    def test_either_retired_section_refuses(self, tmp_home):
        from kanibako.errors import LegacyRegistryIdentityError
        from kanibako.project import workset_registry

        for label, doc in (
            ("both", _UNRELEASED_REGISTRY_DOC),
            ("identity only", {"workset": {"name": "x"}, "boxes": {}}),
            ("projects only", {"boxes": {}, "projects": {"p": {"source_path": "/p"}}}),
        ):
            root = tmp_home / f"reg-{label.replace(' ', '-')}"
            registry = _write_legacy_registry(root, doc)
            with pytest.raises(LegacyRegistryIdentityError):
                workset_registry.load_workset_boxes(registry)

    def test_cure_names_the_sections_the_file_and_the_end_state(self, tmp_home):
        from kanibako.errors import LegacyRegistryIdentityError
        from kanibako.project import workset_registry

        root = tmp_home / "reg-legacy"
        registry = _write_legacy_registry(root)
        with pytest.raises(LegacyRegistryIdentityError) as excinfo:
            workset_registry.load_workset_boxes(registry)
        message = str(excinfo.value)

        assert "'workset:' and 'projects:' sections are RETIRED" in message
        assert str(registry) in message
        assert "global registry" in message
        assert "kanibako workset list" in message
        assert "boxes:\n           <box name>: <the path>" in message
        assert "MIGRATION.md §2.43" in message

    def test_the_message_names_only_what_is_actually_there(self, tmp_home):
        from kanibako.errors import LegacyRegistryIdentityError
        from kanibako.project import workset_registry

        root = tmp_home / "reg-projects-only"
        registry = _write_legacy_registry(
            root, {"boxes": {}, "projects": {"p": {"source_path": "/p"}}},
        )
        with pytest.raises(LegacyRegistryIdentityError) as excinfo:
            workset_registry.load_workset_boxes(registry)
        message = str(excinfo.value)
        assert "'projects:' section is RETIRED" in message
        assert "'workset:'" not in message.split("\n")[0]

    def test_refusal_reaches_the_load_path(self, std, tmp_home):
        from kanibako.errors import LegacyRegistryIdentityError

        root = tmp_home / "worksets" / "reg-legacy"
        _write_legacy_registry(root)
        with pytest.raises(LegacyRegistryIdentityError):
            load_workset(root, "reg-legacy")

    def test_a_live_registry_does_not_refuse(self, std, tmp_home):
        from kanibako.project import workset_registry

        ws = create_workset("live", tmp_home / "worksets" / "live", std)
        add_project(ws, "proj", ws.workspaces_dir / "proj", std)
        assert workset_registry.load_workset_boxes(ws.registry_path) == {
            "proj": str(ws.workspaces_dir / "proj")
        }

    def test_emptied_out_stubs_do_not_refuse(self, tmp_home):
        """Null/empty leftovers record nothing in the wrong place — not a refusal."""
        from kanibako.project import workset_registry

        root = tmp_home / "reg-stubs"
        registry = _write_legacy_registry(
            root, {"workset": None, "boxes": {"b": "/b"}, "projects": {}},
        )
        assert workset_registry.load_workset_boxes(registry) == {"b": "/b"}


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
    def test_internal_membership_write_failure_unwinds(self, std, tmp_home, monkeypatch):
        import kanibako.settings.paths as paths_mod

        root = tmp_home / "worksets" / "my-set"
        ws = create_workset("my-set", root, std)

        # The ``boxes:`` membership write is the LAST step; failing it must roll back
        # the per-project dirs and leave no project in memory or on disk.
        def boom(*a, **k):
            raise _Boom("boxes: registration failed")

        monkeypatch.setattr(paths_mod, "_register_workset_box_membership", boom)
        with pytest.raises(_Boom):
            add_project(ws, "proj", ws.workspaces_dir / "proj")

        resolved = root.resolve()
        assert not (resolved / "boxes" / "proj").exists()
        assert not (resolved / "workspaces" / "proj").exists()
        assert not (resolved / "vault" / "ro" / "proj").exists()
        assert not (resolved / "vault" / "rw" / "proj").exists()
        assert all(p.name != "proj" for p in ws.projects)
        assert all(p.name != "proj" for p in load_workset(root, "my-set").projects)

    def test_external_membership_write_failure_unwinds_symlink(
        self, std, tmp_home, monkeypatch
    ):
        import kanibako.settings.paths as paths_mod

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "keep.txt").write_text("keep me")

        # The symlink is created BEFORE the durable membership write.  If that write
        # fails the link must be rolled back, so the external path is NOT locked out.
        def boom(*a, **k):
            raise _Boom("boxes: registration failed")

        monkeypatch.setattr(paths_mod, "_register_workset_box_membership", boom)
        with pytest.raises(_Boom):
            add_project(ws, "extproj", external, std)

        assert "extproj" not in _workset_boxes(ws)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert all(p.name != "extproj" for p in ws.projects)
        assert external.is_dir()
        assert (external / "keep.txt").read_text() == "keep me"


class TestRemoveProjectFailConsistent:
    def test_membership_removal_is_last_durable_step(self, std, tmp_home, monkeypatch):
        # If the durable membership drop fails, the project must remain registered
        # (re-runnable) rather than half-gone.
        import kanibako.settings.paths as paths_mod
        from kanibako.project.workset import remove_project

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        assert _workset_boxes(ws).get("extproj") == str(external)

        def boom(*a, **k):
            raise _Boom("boxes: unregistration failed")

        monkeypatch.setattr(paths_mod, "_unregister_workset_box_membership", boom)
        with pytest.raises(_Boom):
            remove_project(ws, "extproj", std=std)

        # Still a member on disk, so a re-run completes the removal cleanly, and the
        # external source is untouched throughout.
        assert "extproj" in {p.name for p in load_workset(ws.root, ws.name).projects}
        assert external.is_dir()


class TestStandaloneVaultTeardownSkeleton:
    """The ``vault/`` skeleton goes only when nothing but removable arms and its
    ``.gitignore`` is in it."""

    def _root(self, tmp_path):
        root = tmp_path / "sa"
        for leaf in ("ro", "rw"):
            (root / "vault" / leaf).mkdir(parents=True)
        (root / "vault" / ".gitignore").write_text("rw/\n")
        return root

    def test_the_default_layout_clears_the_skeleton(self, tmp_path):
        from kanibako.project.workset import standalone_vault_teardown

        root = self._root(tmp_path)
        removable, retained = standalone_vault_teardown(root)
        assert root / "vault" in removable
        assert retained == []

    def test_a_nulled_arms_data_keeps_the_skeleton(self, tmp_path):
        from kanibako.project.workset import standalone_vault_teardown

        root = self._root(tmp_path)
        (root / "vault" / "rw" / "data.txt").write_text("mine")
        (root / "workset.yaml").write_text("workset:\n  vault_rw: null\n")
        removable, retained = standalone_vault_teardown(root)
        assert root / "vault" not in removable
        assert removable == [root / "vault" / "ro"]
        assert retained == [root / "vault" / "rw"]

    def test_an_empty_dir_of_a_nulled_arm_does_not_keep_it(self, tmp_path):
        from kanibako.project.workset import standalone_vault_teardown

        root = self._root(tmp_path)
        (root / "workset.yaml").write_text("workset:\n  vault_rw: null\n")
        removable, retained = standalone_vault_teardown(root)
        assert root / "vault" in removable
        assert retained == []


class TestCreateUnderASystemBoxesValue:
    """A system ``workset.boxes`` shapes the skeleton ``create_workset`` stamps in each new root."""

    def _system_boxes(self, value: str) -> None:
        from kanibako.settings.config import system_settings_path
        from kanibako.settings.config_io import dump_doc

        dump_doc(system_settings_path(), {"workset": {"boxes": value}})

    def test_an_outside_dir_is_not_created(self, tmp_home, std):
        outside = tmp_home / "shared-boxes"
        self._system_boxes(str(outside))
        for name in ("one", "two"):
            ws = create_workset(name, tmp_home / "worksets" / name, std)
            assert not (ws.root / "boxes").exists()
        assert not outside.exists()

    def test_an_existing_outside_dir_is_left_alone(self, tmp_home, std):
        outside = tmp_home / "shared-boxes"
        (outside / "a").mkdir(parents=True)
        self._system_boxes(str(outside))
        create_workset("one", tmp_home / "worksets" / "one", std)
        assert [p.name for p in outside.iterdir()] == ["a"]

    def test_a_nested_leaf_inside_the_root_is_created(self, tmp_home, std):
        self._system_boxes("@meta.workset.path/x/boxes")
        ws = create_workset("nested", tmp_home / "worksets" / "nested", std)
        assert (ws.root / "x" / "boxes").is_dir()
