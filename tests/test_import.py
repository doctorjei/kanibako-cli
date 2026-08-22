"""Tests for drop-in import-on-discovery (kanibako.project.import_reconcile + wiring).

On-disk metadata is authoritative; the registry is a derived index.  When
detection/resolution finds an on-disk box/workset/project that is NOT in the
registry, it is imported (registered + an ALERT to stderr) with no confirmation;
a NAME collision against a different root/path REFUSES the import (no mutation).
These tests cover the two live modes (standalone, named) plus idempotency, and
assert that a dropped-in PRIMARY box is NOT rediscovered (P8b/Option A — the
primary reconcile skeleton was sequestered to ``salvage/primary_reconcile.py``).
"""

from __future__ import annotations

import pytest

from kanibako.project import import_reconcile, registry_store
from kanibako.project.import_reconcile import ImportConflictError
from kanibako.settings.paths import (
    BoxMode,
    detect_project_mode,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.project.workset import create_workset


# ---------------------------------------------------------------------------
# STANDALONE
# ---------------------------------------------------------------------------

class TestStandaloneImport:
    def test_dropin_import_registers_and_alerts(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        # Create a standalone box, then wipe the registry's standalone section
        # to simulate a dropped-in tree (on-disk meta present, not registered).
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()  # drain init output

        # Detection walks to the box_data/ marker and imports it.
        result = detect_project_mode(project_dir, std, config)
        assert result.mode is BoxMode.standalone

        standalone = registry_store.load_standalone(std.registry)
        assert standalone.get(name) == str(project_dir.resolve())
        err = capsys.readouterr().err
        assert f"Imported standalone box '{name}'" in err
        assert str(project_dir.resolve()) in err

    def test_import_is_idempotent_no_op(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        # Already-registered standalone box → no re-register, no alert.
        resolve_standalone_project(std, config, str(project_dir), initialize=True)
        before = registry_store.load_standalone(std.registry)
        capsys.readouterr()

        detect_project_mode(project_dir, std, config)
        detect_project_mode(project_dir, std, config)

        assert registry_store.load_standalone(std.registry) == before
        assert "Imported" not in capsys.readouterr().err

    def test_name_collision_refuses(
        self, std, config, project_dir, credentials_dir,
    ):
        # The box's persisted name is already registered to a DIFFERENT root.
        proj = resolve_standalone_project(
            std, config, str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(
            std.registry, "standalone", {name: "/some/other/root"},
        )
        with pytest.raises(ImportConflictError, match="rename"):
            import_reconcile.import_standalone(std.registry, project_dir)
        # Refusal must NOT mutate the registry.
        assert registry_store.load_standalone(std.registry) == {
            name: "/some/other/root"
        }

    def test_prekuid_tree_falls_back_to_dir_leaf(
        self, std, config, project_dir, capsys,
    ):
        # A hand-built / pre-kuid standalone tree: the box_data/ marker + a
        # settings.yaml carrying NO workset.kuid (P8b: import composes kuid-first
        # and, absent a stored kuid — the SENTINEL — falls back to the dir leaf,
        # mirroring box_resolve.resolve_box_identity; it does NOT persist a name).
        from kanibako.settings.config_io import dump_doc, load_doc

        box_data = project_dir / "box_data"
        box_data.mkdir(parents=True)
        meta_file = project_dir / "settings.yaml"
        # A sparse settings.yaml with a box: table but no workset.kuid.
        dump_doc(meta_file, {"box": {"enable_vault": True}})
        capsys.readouterr()

        name = import_reconcile.import_standalone(std.registry, project_dir)
        # Falls back to the current dir leaf (no kuid stored).
        assert name == project_dir.resolve().name
        # Import does NOT write project.name back to disk (sparse model).
        assert "project" not in load_doc(meta_file)
        # Registered + alerted.
        assert registry_store.load_standalone(std.registry).get(name) == str(
            project_dir.resolve()
        )
        assert f"Imported standalone box '{name}'" in capsys.readouterr().err

    def test_kuid_first_compose_from_stored_kuid(
        self, std, config, project_dir, capsys,
    ):
        # A standalone tree carrying a stored workset.kuid: import composes the
        # LIVE name as <kuid>_<dir leaf> (P8b kuid-first), NOT project.name.
        from kanibako import kuid
        from kanibako.launch import box_identity
        from kanibako.settings.config_io import dump_doc

        box_data = project_dir / "box_data"
        box_data.mkdir(parents=True)
        meta_file = project_dir / "settings.yaml"
        box_kuid = kuid.generate()
        dump_doc(meta_file, {"workset": {"kuid": box_kuid}})
        capsys.readouterr()

        name = import_reconcile.import_standalone(std.registry, project_dir)
        expected = box_identity.compose_standalone_name(
            box_kuid, project_dir.resolve(),
        )
        assert name == expected
        assert registry_store.load_standalone(std.registry).get(name) == str(
            project_dir.resolve()
        )
        assert f"Imported standalone box '{name}'" in capsys.readouterr().err

    def test_no_metadata_returns_none(self, std, config, project_dir):
        # box_data/ absent → nothing to import.
        assert import_reconcile.import_standalone(std.registry, project_dir) is None

    def test_moved_standalone_rebases_resolved_paths(
        self, std, config, tmp_home, credentials_dir, capsys,
    ):
        import shutil

        from kanibako.settings.paths import resolve_standalone_project
        # Create a standalone box at the original location.
        orig = tmp_home / "orig"
        orig.mkdir()
        resolve_standalone_project(std, config, str(orig), initialize=True)
        # Move the whole tree to a new path; clear the registry (simulate a move).
        moved = tmp_home / "moved"
        shutil.move(str(orig), str(moved))
        registry_store.save_section(std.registry, "standalone", {})
        capsys.readouterr()
        # Import + resolve from the NEW location.
        import_reconcile.import_standalone(std.registry, moved)
        resolved = resolve_standalone_project(
            std, config, str(moved), initialize=False,
        )
        # resolved.* point at the NEW root, not the deleted original.
        assert resolved.shell_path.name == "home"
        assert resolved.shell_path.parent == (moved / "box_data").resolve()
        assert resolved.shell_path.parent.parent == moved.resolve()
        assert resolved.vault_ro_path == (moved / "vault" / "ro").resolve()
        assert resolved.vault_rw_path == (moved / "vault" / "rw").resolve()
        assert str(orig) not in str(resolved.shell_path)
        # Drift I: metadata_path is the ROOT (settings.yaml lives there);
        # the workspace is the <root>/workspace subdir.
        assert resolved.metadata_path == moved.resolve()
        assert resolved.project_path == (moved / "workspace").resolve()


# ---------------------------------------------------------------------------
# NAMED (worksets)
# ---------------------------------------------------------------------------

class TestNamedWorksetImport:
    def test_dropin_import_registers_and_alerts(
        self, std, config, tmp_home, capsys,
    ):
        ws_root = tmp_home / "worksets" / "imported"
        create_workset("imported", ws_root, std)
        # Wipe the GLOBAL registry to simulate a dropped-in workset tree (the
        # per-workset registry.yaml identity is on disk, no global entry).
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        name = import_reconcile.import_named_workset(std.registry, ws_root)
        assert name == "imported"
        assert registry_store.load_section(std.registry, "worksets") == {
            "imported": str(ws_root.resolve())
        }
        assert "Imported workset 'imported'" in capsys.readouterr().err

    def test_detection_imports_unregistered_workset(
        self, std, config, tmp_home, capsys,
    ):
        ws_root = tmp_home / "worksets" / "wsdetect"
        create_workset("wsdetect", ws_root, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        # Detection walks up from inside the workset root and imports it.
        result = detect_project_mode(ws_root, std, config)
        assert result.mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets").get(
            "wsdetect"
        ) == str(ws_root.resolve())
        assert "Imported workset 'wsdetect'" in capsys.readouterr().err

    def test_import_is_idempotent_no_op(self, std, config, tmp_home, capsys):
        ws_root = tmp_home / "worksets" / "idem"
        create_workset("idem", ws_root, std)
        before = registry_store.load_section(std.registry, "worksets")
        capsys.readouterr()

        assert (
            import_reconcile.import_named_workset(std.registry, ws_root) == "idem"
        )
        assert registry_store.load_section(std.registry, "worksets") == before
        assert "Imported" not in capsys.readouterr().err

    def test_name_collision_refuses(self, std, config, tmp_home):
        ws_root = tmp_home / "worksets" / "clash"
        create_workset("clash", ws_root, std)
        # Same name already registered to a DIFFERENT root.
        registry_store.save_section(
            std.registry, "worksets", {"clash": "/other/root"},
        )
        with pytest.raises(ImportConflictError, match="rename"):
            import_reconcile.import_named_workset(std.registry, ws_root)
        assert registry_store.load_section(std.registry, "worksets") == {
            "clash": "/other/root"
        }

    def test_no_workset_yaml_returns_none(self, std, config, tmp_home):
        plain = tmp_home / "plaindir"
        plain.mkdir()
        assert import_reconcile.import_named_workset(std.registry, plain) is None


# ---------------------------------------------------------------------------
# PRIMARY (central box store → external workspace)
# ---------------------------------------------------------------------------

class TestPrimaryBoxImport:
    def test_dropin_not_rediscovered_option_a(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        """P8b/Option A: an unregistered on-disk PRIMARY box is NOT auto-
        rediscovered on a normal (register=True) resolve — the registry is the
        sole identity authority and a sparse-created box does not self-describe on
        disk, so there is nothing to re-import.  (``system recover`` is the future
        remedy.)"""
        from kanibako.settings.paths import (
            load_primary_boxes,
            unregister_primary_box_name,
        )

        proj = resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        assert proj.name  # created + registered
        # Drop the PRIMARY-membership entry (the sole store since the global
        # ``projects:`` section retired) — the on-disk box dir survives,
        # unregistered.
        unregister_primary_box_name(std.primary_workset, proj.name)
        assert load_primary_boxes(std.primary_workset) == {}
        capsys.readouterr()

        # Re-resolving the same workspace does NOT silently re-register the box.
        proj2 = resolve_project(
            std, config, project_dir=str(project_dir), initialize=False,
        )
        assert proj2.name == ""  # not recovered from disk
        assert load_primary_boxes(std.primary_workset) == {}
        assert "Imported primary box" not in capsys.readouterr().err

    # NOTE (P8c): the direct unit tests of ``import_primary_box`` /
    # ``reconcile_primary_boxes`` were removed — those functions were sequestered
    # out of the live package into ``salvage/primary_reconcile.py`` (a
    # non-shipping frozen reference).  The live-path assertion above (a dropped-in
    # box is NOT rediscovered) is the behavior that matters here.
