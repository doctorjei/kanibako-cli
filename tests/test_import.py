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

from pathlib import Path

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
# NAMED (worksets).
#
# ⚑⚑ DETECTION AND NAMING ARE TWO QUESTIONS ([R139]).  A workset root records no name
# anywhere on disk — its identity is the global registry's ``worksets:`` entry — but it
# is still FOUND on disk, by its four-dir skeleton, and the name it is imported under is
# its LEAF DIRECTORY basename: the same default ``workset create`` has always applied to
# a workset created without ``--name``.
# ---------------------------------------------------------------------------

def _stamp_skeleton_onto(target: Path, tmp_home: Path, std) -> Path:
    """Give an ALREADY-EXISTING *target* dir the four-dir workset skeleton.

    ``create_workset`` refuses a root that exists, so build one beside *target*
    and move its children over.  ⚑ The dirs come from the creator rather than a
    list written here: a hand-copied list would drift from the shape detection
    actually tests for, and the test would then prove nothing.  The caller is
    left to clear the seed's registration.
    """
    import shutil

    from kanibako.project.workset import is_workset_skeleton

    seed = tmp_home / "skeleton_seed"
    create_workset("skeleton-seed", seed, std)
    for subdir in seed.resolve().iterdir():
        shutil.move(str(subdir), str(target / subdir.name))
    seed.resolve().rmdir()
    assert is_workset_skeleton(target), target
    return target


class TestNamedWorksetImport:
    def test_an_unregistered_workset_root_is_imported_and_alerts(
        self, std, config, tmp_home, capsys,
    ):
        """Drop the global registration and the SAME directory is still a workset."""
        ws_root = tmp_home / "worksets" / "wsdetect"
        create_workset("wsdetect", ws_root, std)
        assert detect_project_mode(ws_root, std, config).mode is BoxMode.named

        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        result = detect_project_mode(ws_root, std, config)
        assert result.mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets") == {
            "wsdetect": str(ws_root.resolve())
        }
        err = capsys.readouterr().err
        assert "Imported workset 'wsdetect'" in err
        assert str(ws_root.resolve()) in err

    def test_the_name_comes_from_the_leaf_directory(
        self, std, config, tmp_home, capsys,
    ):
        """⚑ The tree is MOVED and renamed: the import names it after where it now
        lives, not after what it used to be called."""
        import shutil

        orig = tmp_home / "worksets" / "oldname"
        create_workset("oldname", orig, std)
        moved = tmp_home / "elsewhere" / "newname"
        moved.parent.mkdir(parents=True)
        shutil.move(str(orig.resolve()), str(moved))
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        assert detect_project_mode(moved, std, config).mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets") == {
            "newname": str(moved.resolve())
        }
        assert "Imported workset 'newname'" in capsys.readouterr().err

    def test_a_subdir_of_an_unregistered_root_walks_up_to_it(
        self, std, config, tmp_home, capsys,
    ):
        """The ancestor walk is what reaches the root — resolution starts deeper."""
        ws_root = tmp_home / "worksets" / "walkup"
        ws = create_workset("walkup", ws_root, std)
        inner = ws.workspaces_dir / "proj"
        inner.mkdir()
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        assert detect_project_mode(inner, std, config).mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets") == {
            "walkup": str(ws_root.resolve())
        }

    def test_a_registered_workset_root_needs_no_import(
        self, std, config, tmp_home, capsys,
    ):
        ws_root = tmp_home / "worksets" / "idem"
        create_workset("idem", ws_root, std)
        assert registry_store.load_section(std.registry, "worksets") == {
            "idem": str(ws_root.resolve())
        }
        capsys.readouterr()
        assert detect_project_mode(ws_root, std, config).mode is BoxMode.named
        # Step 3 answers it; nothing is re-registered and nothing is announced.
        assert "Imported workset" not in capsys.readouterr().err

    def test_import_of_an_already_registered_root_is_a_silent_no_op(
        self, std, tmp_home, capsys,
    ):
        """The direct call is idempotent too — same root, no alert, no rewrite."""
        ws_root = tmp_home / "worksets" / "noop"
        create_workset("noop", ws_root, std)
        before = registry_store.load_section(std.registry, "worksets")
        capsys.readouterr()

        assert import_reconcile.import_named_workset(
            std.registry, ws_root, primary_workset=std.primary_workset,
        ) == "noop"
        assert registry_store.load_section(std.registry, "worksets") == before
        assert capsys.readouterr().err == ""

    def test_same_kind_collision_refuses_and_leaves_the_tree(
        self, std, tmp_home, capsys,
    ):
        """⚑ SAME-KIND (the name is another WORKSET's): REFUSE, leave it on disk."""
        held = tmp_home / "worksets" / "dup"
        create_workset("dup", held, std)
        # A SECOND tree whose leaf basename derives the same name.
        other = tmp_home / "copies" / "dup"
        create_workset("scratch", other, std)
        registry_store.save_section(
            std.registry, "worksets", {"dup": str(held.resolve())},
        )
        capsys.readouterr()

        with pytest.raises(ImportConflictError, match="already registered"):
            import_reconcile.import_named_workset(
                std.registry, other, primary_workset=std.primary_workset,
            )
        # Nothing mutated, and the refused tree is untouched on disk.
        assert registry_store.load_section(std.registry, "worksets") == {
            "dup": str(held.resolve())
        }
        assert other.is_dir() and (other / "boxes").is_dir()

    def test_cross_kind_collision_imports_and_warns(
        self, std, config, tmp_home, capsys, caplog,
    ):
        """⚑ CROSS-KIND (the name is a primary BOX's): IMPORT ANYWAY and WARN.

        Nobody typed this name and there is no ``--force`` to offer, so refusing
        would strand the tree the import exists to recover ([R139]).  The warning
        names the same escape hatch bare-name resolution names.
        """
        from kanibako.settings.paths import register_primary_box_name

        box_dir = tmp_home / "boxproj"
        box_dir.mkdir()
        register_primary_box_name(
            std.primary_workset, std.registry, "clash", str(box_dir),
        )
        ws_root = tmp_home / "worksets" / "clash"
        create_workset("clash", ws_root, std, force=True)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        with caplog.at_level("WARNING"):
            result = detect_project_mode(ws_root, std, config)

        assert result.mode is BoxMode.named
        assert registry_store.load_section(std.registry, "worksets") == {
            "clash": str(ws_root.resolve())
        }
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, warnings
        assert "clash" in warnings[0] and "primary box" in warnings[0]
        assert "kanibako workset <cmd> clash" in warnings[0]

    def test_no_cross_kind_warning_without_a_colliding_box(
        self, std, config, tmp_home, capsys, caplog,
    ):
        """The warning fires only on a LIVE collision (parity with resolve_name)."""
        ws_root = tmp_home / "worksets" / "solo"
        create_workset("solo", ws_root, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        with caplog.at_level("WARNING"):
            detect_project_mode(ws_root, std, config)
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    def test_a_reserved_leaf_name_is_not_imported(self, std, tmp_home, capsys):
        """⚑ The DERIVED name clears the same bars a typed one does: a directory
        named for a reserved sentinel is left alone, not registered under it."""
        root = tmp_home / "holder" / "default"
        create_workset("holder-ws", root, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        assert import_reconcile.import_named_workset(
            std.registry, root, primary_workset=std.primary_workset,
        ) is None
        assert registry_store.load_section(std.registry, "worksets") == {}
        assert capsys.readouterr().err == ""

    def test_a_home_directory_root_is_declined_not_refused(
        self, std, tmp_home, capsys,
    ):
        """⚑ A $HOME that happens to carry the skeleton is DECLINED, like a reserved
        name — ``register_name`` would REFUSE it, and a refusal here escapes into
        every command, because the walk tests $HOME before it stops there."""
        home = _stamp_skeleton_onto(Path.home().resolve(), tmp_home, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        assert import_reconcile.import_named_workset(
            std.registry, home, primary_workset=std.primary_workset,
        ) is None
        assert registry_store.load_section(std.registry, "worksets") == {}
        assert capsys.readouterr().err == ""

    def test_detection_still_answers_for_a_home_directory_with_the_skeleton(
        self, std, config, tmp_home, capsys,
    ):
        """The reason the decline matters: mode detection stays usable.  Step 5
        declines the import and the walk falls through to ordinary primary mode
        instead of raising out of the resolver every command calls."""
        home = _stamp_skeleton_onto(Path.home().resolve(), tmp_home, std)
        registry_store.save_section(std.registry, "worksets", {})
        capsys.readouterr()

        result = detect_project_mode(home, std, config)
        assert result.mode is BoxMode.primary
        assert result.project_root == home
        assert registry_store.load_section(std.registry, "worksets") == {}
        assert "Imported workset" not in capsys.readouterr().err


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
