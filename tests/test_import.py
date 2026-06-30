"""Tests for drop-in import-on-discovery (kanibako.import_reconcile + wiring).

On-disk metadata is authoritative; the registry is a derived index.  When
detection/resolution finds an on-disk box/workset/project that is NOT in the
registry, it is imported (registered + an ALERT to stderr) with no confirmation;
a NAME collision against a different root/path REFUSES the import (no mutation).
These tests cover all three modes (standalone, named, primary) plus idempotency.
"""

from __future__ import annotations

import pytest

from kanibako import import_reconcile, registry_store
from kanibako.import_reconcile import ImportConflictError
from kanibako.paths import (
    BoxMode,
    detect_project_mode,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.workset import create_workset


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

    def test_handcreated_tree_mints_and_persists_name(
        self, std, config, project_dir, capsys,
    ):
        # A hand-built standalone tree: <root>/settings.yaml (drift I) with no
        # name, plus the box_data/ marker dir.
        from kanibako.config import read_project_meta, write_project_meta

        box_data = project_dir / "box_data"
        box_data.mkdir(parents=True)
        meta_file = project_dir / "settings.yaml"
        write_project_meta(
            meta_file,
            mode="standalone",
            workspace=str((project_dir / "workspace").resolve()),
            shell="", vault_ro="", vault_rw="",
            name="",  # no persisted identity
        )
        capsys.readouterr()

        name = import_reconcile.import_standalone(std.registry, project_dir)
        assert name  # a fresh <random24>_<leaf> name was minted
        # Persisted back into the metadata (project.name).
        assert read_project_meta(meta_file)["name"] == name
        # Registered + alerted.
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

        from kanibako.paths import resolve_standalone_project
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
        # Wipe the registry to simulate a dropped-in workset tree (settings.yaml
        # workset.meta on disk, no registry entry).
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
    def test_dropin_import_via_resolve(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        # Create a primary box, then drop the registry's projects entry so the
        # on-disk box dir (with settings.yaml) survives unregistered.
        proj = resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "projects", {})
        capsys.readouterr()

        # Re-resolving the same workspace re-discovers + imports the box.
        proj2 = resolve_project(
            std, config, project_dir=str(project_dir), initialize=False,
        )
        assert proj2.name == name
        assert registry_store.load_section(std.registry, "projects").get(
            name
        ) == str(project_dir.resolve())
        assert f"Imported primary box '{name}'" in capsys.readouterr().err

    def test_reconcile_scan_imports_unregistered(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        proj = resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "projects", {})
        capsys.readouterr()

        imported = import_reconcile.reconcile_primary_boxes(
            std.registry, std.boxes,
        )
        assert imported == [name]
        assert registry_store.load_section(std.registry, "projects").get(
            name
        ) == str(project_dir.resolve())
        assert f"Imported primary box '{name}'" in capsys.readouterr().err

    def test_reconcile_idempotent_no_op(
        self, std, config, project_dir, credentials_dir, capsys,
    ):
        resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        before = registry_store.load_section(std.registry, "projects")
        capsys.readouterr()

        assert import_reconcile.reconcile_primary_boxes(std.registry, std.boxes) == []
        assert registry_store.load_section(std.registry, "projects") == before
        assert "Imported" not in capsys.readouterr().err

    def test_name_collision_refuses(
        self, std, config, project_dir, credentials_dir,
    ):
        proj = resolve_project(
            std, config, project_dir=str(project_dir), initialize=True,
        )
        name = proj.name
        # The box's name is registered to a DIFFERENT workspace.
        registry_store.save_section(
            std.registry, "projects", {name: "/some/other/workspace"},
        )
        box_dir = std.boxes / name
        with pytest.raises(ImportConflictError, match="rename"):
            import_reconcile.import_primary_box(std.registry, box_dir)
        assert registry_store.load_section(std.registry, "projects") == {
            name: "/some/other/workspace"
        }

    def test_no_boxes_dir_returns_empty(self, std, config, tmp_home):
        missing = tmp_home / "no-boxes"
        assert import_reconcile.reconcile_primary_boxes(std.registry, missing) == []
