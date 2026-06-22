"""Tests for kanibako.workset -- working set data model and persistence."""

from __future__ import annotations


import pytest

from kanibako.errors import WorksetError
from kanibako.names import register_name
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
        assert ws.root == std.data_path

    def test_mirrors_names_projects(self, std, tmp_home):
        proj_a = tmp_home / "proj_a"
        proj_b = tmp_home / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        register_name(std.data_path, "alpha", str(proj_a))
        register_name(std.data_path, "beta", str(proj_b))

        ws = default_workset(std)
        by_name = {p.name: p.source_path for p in ws.projects}
        assert by_name == {"alpha": proj_a, "beta": proj_b}

    def test_group_auth_defaults_true(self, std):
        assert default_workset(std).group_auth is True

    def test_group_auth_read_from_config(self, std):
        config_path = std.data_path / "config.yaml"
        config_path.write_text("project:\n  group_auth: false\n")
        assert default_workset(std).group_auth is False

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

        with pytest.raises(WorksetError, match="No workset.yaml"):
            load_workset(root)

    def test_toml_without_name_raises(self, std, tmp_home):
        root = tmp_home / "worksets" / "bad-toml"
        root.mkdir(parents=True)
        (root / "workset.yaml").write_text('created: "2026-01-01"\n')

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


class TestRemoveExternalProject:
    """disconnect symmetric cleanup for external-connected projects.

    Regression guard for the connect-external work: removing an external project
    must drop its connected.yaml entry and the workspaces/{name} symlink, and
    must NEVER delete the user's external source directory.
    """

    def _connect_external(self, std, tmp_home):
        from kanibako.workset import _load_connected

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "file.txt").write_text("keep me")
        add_project(ws, "extproj", external, std)

        # Sanity: markers exist after connect.
        assert (ws.workspaces_dir / "extproj").is_symlink()
        assert str(external) in _load_connected(std)
        return ws, external

    def test_disconnect_clears_markers_keeps_source(self, std, tmp_home):
        from kanibako.workset import _load_connected

        ws, external = self._connect_external(std, tmp_home)

        remove_project(ws, "extproj", std=std)

        # connected.yaml entry gone, symlink gone, external source intact.
        assert str(external) not in _load_connected(std)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert external.is_dir()
        assert (external / "file.txt").read_text() == "keep me"

    def test_disconnect_remove_files_keeps_source(self, std, tmp_home):
        from kanibako.workset import _load_connected

        ws, external = self._connect_external(std, tmp_home)

        # Must not crash on the symlink (rmtree refuses symlinks) and must not
        # delete the external source.
        remove_project(ws, "extproj", remove_files=True, std=std)

        assert str(external) not in _load_connected(std)
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
        assert ws.toml_path == resolved / "workset.yaml"


# ---------------------------------------------------------------------------
# Failure-consistency: multi-step mutations must leave NO half-applied state.
#
# Each test injects a failure at a mid-sequence write/operation and asserts the
# op either fully applied or fully rolled back (no orphan dirs, no dangling
# connected.yaml redirect / symlink, no registry-vs-index mismatch, external
# path never locked out).  Mirrors the lifecycle family's failure-injection
# style (patch a forward step to raise; assert consistent state).
# ---------------------------------------------------------------------------

class _Boom(Exception):
    """Sentinel for injected mid-sequence failures."""


class TestCreateWorksetFailConsistent:
    def test_name_index_write_failure_unwinds(self, std, tmp_home, monkeypatch):
        import kanibako.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"

        # register_name is the LAST step (after dirs + worksets.yaml).  If it
        # blows up, the whole create must roll back: no orphan dirs, nothing in
        # the global registry, nothing in the name index.
        def boom(*a, **k):
            raise _Boom("register_name failed")

        monkeypatch.setattr(ws_mod, "register_name", boom)

        with pytest.raises(_Boom):
            create_workset("my-set", root, std)

        assert not root.exists(), "orphan dirs left behind"
        assert "my-set" not in list_worksets(std), "stale worksets.yaml entry"
        from kanibako.names import read_names
        assert "my-set" not in read_names(std.data_path).get("worksets", {})

    def test_registry_write_failure_unwinds_dirs(self, std, tmp_home, monkeypatch):
        import kanibako.workset as ws_mod

        root = tmp_home / "worksets" / "my-set"

        def boom(*a, **k):
            raise _Boom("worksets.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_registry", boom)

        with pytest.raises(_Boom):
            create_workset("my-set", root, std)

        # Dirs were created before the registry write — must be cleaned up.
        assert not root.exists()
        from kanibako.names import read_names
        assert "my-set" not in read_names(std.data_path).get("worksets", {})


class TestDeleteWorksetSelfHealing:
    def test_stale_name_index_recleaned_on_rerun(self, std, tmp_home):
        # Simulate a prior delete that crashed after the worksets.yaml write but
        # before unregister_name: the workset survives ONLY in names.yaml.  A
        # re-run of delete must recognize + fully clean it (not raise "not
        # registered").
        from kanibako.names import read_names

        root = tmp_home / "worksets" / "my-set"
        create_workset("my-set", root, std)
        # Drop only the worksets.yaml half (the crash-between state).
        from kanibako.workset import _unregister_workset
        _unregister_workset(std, "my-set")
        assert "my-set" not in list_worksets(std)
        assert "my-set" in read_names(std.data_path).get("worksets", {})

        # Re-run delete: must succeed and clean the stale name-index entry.
        ret = delete_workset("my-set", std)
        assert ret == root.resolve()
        assert "my-set" not in read_names(std.data_path).get("worksets", {})

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
        from kanibako.workset import _load_connected

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        (external / "keep.txt").write_text("keep me")

        # The connected.yaml redirect is written BEFORE the final workset.yaml
        # write.  If that final write fails, the redirect + symlink must be
        # rolled back so the external path is NOT locked out.
        def boom(*a, **k):
            raise _Boom("workset.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_toml", boom)
        with pytest.raises(_Boom):
            add_project(ws, "extproj", external, std)

        # No dangling redirect, no orphan symlink, external source untouched.
        assert str(external) not in _load_connected(std)
        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert all(p.name != "extproj" for p in ws.projects)
        assert external.is_dir()
        assert (external / "keep.txt").read_text() == "keep me"

    def test_external_connected_write_failure_unwinds_symlink(
        self, std, tmp_home, monkeypatch
    ):
        import kanibako.workset as ws_mod
        from kanibako.workset import _load_connected

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()

        # Fail the connected.yaml write (after the symlink is created).  The
        # symlink must be rolled back so no orphan link survives.
        def boom(*a, **k):
            raise _Boom("connected.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_connected", boom)
        with pytest.raises(_Boom):
            add_project(ws, "extproj", external, std)

        assert not (ws.workspaces_dir / "extproj").is_symlink()
        assert not (ws.workspaces_dir / "extproj").exists()
        assert str(external) not in _load_connected(std)
        assert all(p.name != "extproj" for p in ws.projects)
        assert external.is_dir()


class TestRemoveProjectFailConsistent:
    def test_registry_removal_is_last_durable_step(self, std, tmp_home, monkeypatch):
        # If the final workset.yaml write fails during removal, the project must
        # remain registered (re-runnable) rather than being dropped from the
        # registry while leaving a dangling connected.yaml redirect.
        import kanibako.workset as ws_mod
        from kanibako.workset import _load_connected, remove_project

        ws = create_workset("ext-set", tmp_home / "worksets" / "ext-set", std)
        external = (tmp_home / "external_repo").resolve()
        external.mkdir()
        add_project(ws, "extproj", external, std)
        assert str(external) in _load_connected(std)

        def boom(*a, **k):
            raise _Boom("workset.yaml write failed")

        monkeypatch.setattr(ws_mod, "_write_workset_toml", boom)
        with pytest.raises(_Boom):
            remove_project(ws, "extproj", std=std)

        # connected.yaml + symlink were cleaned (idempotent re-run safe), but the
        # registry still lists the project — so it is NOT half-gone with a
        # dangling redirect (the external path is never locked out).
        assert "extproj" in {p.name for p in load_workset(ws.root).projects}
        # The redirect was cleared before the failed registry write; a re-run of
        # remove_project will complete the removal cleanly.
        assert str(external) not in _load_connected(std)
        assert external.is_dir()
