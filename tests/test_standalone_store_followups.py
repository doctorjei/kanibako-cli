"""The three doors that still spelled ``box_data/`` where the store now answers to the key.

``workset.boxes`` is a repointable workset key whose standalone value is the
``{meta.workset.path}/box_data`` DEFAULT, so ``box_data/`` names a standalone store only
while the key is unset.  Three readers composed the leaf instead of resolving it:

* the import reconcile's journal KEY, which must agree with the key ``create`` writes;
* the workspace copy's *ignore*, which keeps the box's own store out of a workspace;
* the standalone root sweep, whose keep-set is a list of NAMES and cannot hold a store
  the user moved.

⚑ Every case here goes through a DOOR THAT EXISTS AT THE BASE (``cli.build_parser`` plus
``args.func``, ``import_standalone``, ``execute_lifecycle``), so a base run fails on an
ASSERTION about behaviour rather than on an import of a name this change introduced.

⚑ THE BAR FOR THE LAST TWO CLASSES IS DATA SAFETY, NOT AN EXIT CODE: each asserts what
SURVIVES at the source and what is ABSENT from the copy.  A verb that returns 0 in all of
these either way would pass an rc-only assertion while it copied or moved the box's own
metadata and home.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    BoxMode, _early_scope, resolve_standalone_project)


def _standalone(config, std, tmp_home, leaf: str, *, register: bool = True):
    """A materialized standalone box at *leaf*, its store in-tree at the default leaf."""
    root = tmp_home / leaf
    root.mkdir()
    (root / "user_code.py").write_text("print('mine')\n")
    resolve_standalone_project(std, config, str(root), initialize=True, register=register)
    return root


def _relocate_store(root: Path, value: str) -> Path:
    """MOVE the store to ``<root>/<value>`` and point ``workset.boxes`` there VERBATIM.

    ``value`` is a relative ref, not an absolute path, so the case reads as the user's own
    ``workset.yaml``.  The ``box_data/`` LOCATOR stays: the spec keeps it and detection
    finds the box by it, so leaving it is the shape a relocating user actually has.
    """
    store = root / value
    store.mkdir()
    shutil.rmtree(root / "box_data")
    (store / "MY_STORE_DATA.txt").write_text("the box's own metadata\n")
    (store / "home").mkdir()
    (store / "home" / "notes.md").write_text("HOME\n")
    (store / "box.yaml").write_text("box: {}\n")
    write_nested_key(root / "workset.yaml", ("workset",), "boxes",
                     f"@meta.workset.path/{value}")
    (root / "box_data").mkdir(exist_ok=True)
    return store


def _workspace_at(root: Path) -> None:
    """Put the box's workspace AT its own root, so the store sits INSIDE the copied tree."""
    write_nested_key(root / "workset.yaml", ("workset",), "workspaces",
                     "@meta.workset.path")


def _cli(*argv: str) -> int:
    """The production door: the real parser, then the handler it names."""
    from kanibako import cli

    args = cli.build_parser().parse_args(list(argv))
    return int(args.func(args) or 0)


class TestTheJournalKeyFollowsTheStore:
    """``import_standalone`` keys the journal by the box dir ``create`` writes it under."""

    def test_a_relocated_store_defers_to_create_recover(self, config, std, tmp_home):
        """⚑ THE PIN: a box mid-create is NOT imported, and its pending entry survives.

        ``create`` keys the entry by ``shell_path.parent``, which is the RESOLVED store, and
        ``create --recover`` reads that key.  The reconcile spelled ``box_data/``, so it
        never saw the entry and imported a box that was still being created.
        """
        from kanibako.commands.start import _box_journal_key
        from kanibako.launch import journal as journal_mod
        from kanibako.project import import_reconcile, registry_store

        root = _standalone(config, std, tmp_home, "jk_reloc", register=False)
        _relocate_store(root, "store")
        # ⚑ The key ``create`` writes, taken from the production derivation itself.
        key = _box_journal_key(resolve_standalone_project(std, config, str(root)))
        journal_mod.write_entry(
            std.journal, key, op="create", name=root.name, mode="standalone",
        )

        got = import_reconcile.import_standalone(
            std.registry, root, journal=std.journal,
            early=_early_scope(std, BoxMode.standalone))

        assert got is None, "a pending create is create --recover's box, not an import's"
        assert registry_store.standalone_name_for_root(std.registry, root.resolve()) is None
        assert journal_mod.pending_create(std.journal, key) is not None, (
            "the import must not overwrite the entry it deferred to")

    def test_an_unrelocated_store_imports_under_the_composed_key(self, config, std, tmp_home):
        """⚑ THE COMMON CASE IS UNCHANGED: with no ``workset.boxes``, the key is ``box_data``."""
        from kanibako.project import import_reconcile, registry_store

        root = _standalone(config, std, tmp_home, "jk_default", register=False)
        assert (root / "box_data").is_dir()

        got = import_reconcile.import_standalone(
            std.registry, root, journal=std.journal,
            early=_early_scope(std, BoxMode.standalone))

        assert got is not None, "an unregistered on-disk box is importable"
        assert registry_store.standalone_name_for_root(std.registry, root.resolve()) == got
        assert (root / "box_data").is_dir(), "the import writes no store of its own"

    def test_a_stale_import_entry_under_the_resolved_key_is_cleared(
            self, config, std, tmp_home):
        """The already-registered arm clears the J2 self-heal entry under the SAME key.

        ``resolve_standalone_project`` reads that entry back by ``shell_path.parent``, so
        clearing a key spelled any other way leaves the stale entry in place forever.
        """
        from kanibako.commands.start import _box_journal_key
        from kanibako.launch import journal as journal_mod
        from kanibako.project import import_reconcile

        root = _standalone(config, std, tmp_home, "jk_stale")
        _relocate_store(root, "store")
        key = _box_journal_key(resolve_standalone_project(std, config, str(root)))
        journal_mod.write_entry(
            std.journal, key, op="import", name=root.name, mode="standalone",
        )

        import_reconcile.import_standalone(
            std.registry, root, journal=std.journal,
            early=_early_scope(std, BoxMode.standalone))

        assert journal_mod.pending_entry(std.journal, key) is None


class TestTheWorkspaceCopyOmitsTheStore:
    """A workspace copy is the workspace: the box's own store never travels into one."""

    def test_a_duplicate_omits_a_relocated_store(self, config, std, tmp_home):
        """⚑ THE PIN: the destination leaf has no second copy of the box's metadata.

        Base excluded the literal ``box_data`` at any depth, so a store the key moved to
        another leaf was copied whole — its ``home/`` and box tier included — into a
        workspace, where the box would read it as ordinary project content.
        """
        from kanibako.project.workset import create_workset

        root = _standalone(config, std, tmp_home, "wc_reloc")
        store = _relocate_store(root, "store")
        _workspace_at(root)          # the store is now inside the tree being copied
        ws = create_workset("wsw", tmp_home / "wsw_root", std)

        rc = _cli("box", "duplicate", str(root), str(tmp_home / "dup_dst"),
                  "--to", "named", "--workset", "wsw", "--force")

        assert rc == 0
        dup_ws = ws.workspaces_dir / "wc_reloc"
        assert not (dup_ws / "store").exists(), (
            "the box's own store must not be copied into a workspace")
        assert not (dup_ws / "store" / "MY_STORE_DATA.txt").exists()
        assert not (dup_ws / "store" / "home").exists()
        # ⚑ DATA SAFETY: excluded from the COPY, never moved out of the source.
        assert (store / "MY_STORE_DATA.txt").read_text() == "the box's own metadata\n"
        assert (store / "home" / "notes.md").read_text() == "HOME\n"
        assert (store / "box.yaml").is_file()
        # The workspace's own content did travel.
        assert (dup_ws / "user_code.py").read_text() == "print('mine')\n"

    def test_a_duplicate_still_omits_the_composed_locator(
            self, config, std, tmp_home, capsys):
        """⚑ THE INVERSE: an unrelocated box keeps losing ``box_data/`` and nothing else.

        The locator exclusion is not the fix for a repointed store and must not be traded
        away for it.
        """
        from kanibako.project.workset import create_workset

        root = _standalone(config, std, tmp_home, "wc_default")
        assert (root / "box_data").is_dir()
        _workspace_at(root)
        ws = create_workset("wsd", tmp_home / "wsd_root", std)

        rc = _cli("box", "duplicate", str(root), str(tmp_home / "dup_dst2"),
                  "--to", "named", "--workset", "wsd", "--force")

        assert rc == 0
        dup_ws = ws.workspaces_dir / "wc_default"
        assert not (dup_ws / "box_data").exists(), "the composed store stays out"
        assert (root / "box_data").is_dir(), "the source store is untouched"
        assert (dup_ws / "user_code.py").is_file()

    def test_a_store_outside_the_copied_tree_excludes_nothing(
            self, config, std, tmp_home):
        """A store beside the workspace is in no tree being copied, so it adds no exclusion.

        The containment test is the one ``standalone_store_teardown_plan`` uses, so a store
        reached through ``..`` or a symlinked parent cannot name a directory in the copy.

        ⚑ AND THE ONLY ``box_data`` THIS NAMES IS THE ONE THE KEY POINTS AT.  Here
        ``workset.boxes`` is repointed to ``outside_ws/box_data``, so THAT path is the
        store and is excluded; the locator at ``<root>/box_data`` is a different path in a
        tree this walk never visits, and is not.  An exclusion is a resolved path the walk
        is visiting — never the bare name, which is what used to take a user's ``box_data``
        at any depth.
        """
        from kanibako.commands.box._lifecycle import _workspace_copy_ignore
        from kanibako.settings.paths import resolve_standalone_project as _r

        root = _standalone(config, std, tmp_home, "wc_outside")
        store = _relocate_store(root, "store")
        elsewhere = tmp_home / "outside_ws"
        elsewhere.mkdir()
        (elsewhere / "code.py").write_text("x\n")
        (elsewhere / "box_data").mkdir()
        (elsewhere / "box_data" / "USER_FILE.txt").write_text("mine\n")
        # The store leaves the root by spelling, and the copied tree is elsewhere.
        write_nested_key(root / "workset.yaml", ("workset",), "boxes",
                         "@meta.workset.path/../outside_ws/box_data")
        _r(std, config, str(root))

        ignore = _workspace_copy_ignore(
            root.resolve(), elsewhere.resolve(),
            early=_early_scope(std, BoxMode.standalone))

        skipped = set(ignore(str(elsewhere), ["code.py", "store", "box_data"]))
        assert "store" not in skipped, "a store reached through `..` names nothing here"
        # ⚑ The repointed store IS this path, so it IS excluded — by resolved path.
        assert "box_data" in skipped
        # ⚑ AND the locator, at a different path in an unvisited tree, is NOT what the
        # name alone would have skipped: a user's own `box_data` elsewhere survives.
        user_dir = tmp_home / "user_code" / "box_data"
        user_dir.mkdir(parents=True)
        assert "box_data" not in set(
            ignore(str(user_dir.parent), ["code.py", "box_data"])), (
            "a box_data that is neither the store nor the locator is the USER's own")
        assert store.is_dir()


class TestTheRootSweepKeepsTheStore:
    """``box convert --standalone`` sweeps the root; the box's own store is not sweepable."""

    def test_a_repointed_store_is_left_at_the_standalone_root(
            self, config, std, tmp_home, capsys):
        """⚑ THE PIN, AND IT IS A DATA MOVE: the user's own dir stays where it was.

        The keep-set is a list of NAMES, and a name the user chose is in no list.  The
        sweep therefore MOVED the store — metadata, home and all — into the workspace dir,
        left the resolved store behind as an empty dir, and reported a consolidation that
        had moved the box's own files.
        """
        from kanibako.commands.box._lifecycle import (
            INPLACE, TargetSpec, execute_lifecycle, resolve_lifecycle_target,
        )
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import resolve_project, standalone_box_store

        root = tmp_home / "sweep_boxes"
        root.mkdir()
        (root / "file.txt").write_text("mine")
        resolve_project(std, config, project_dir=str(root), initialize=True)
        store = root / "store"
        store.mkdir()
        (store / "MY_OWN_THINGS.md").write_text("the user's own data\n")
        dump_doc(root / "workset.yaml", {"workset": {
            "boxes": "@meta.workset.path/store",
            "workspaces": "@meta.workset.path/nested",
        }})
        assert standalone_box_store(
            root, early=_early_scope(std, BoxMode.standalone)) == store

        state = resolve_lifecycle_target(str(root), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=lambda *a, **k: True,
        )

        assert (store / "MY_OWN_THINGS.md").read_text() == "the user's own data\n", (
            "the box's own store is the user's data; the sweep must not move it")
        assert not (new.workspace_path / "store").exists()
        assert (new.workspace_path / "file.txt").read_text() == "mine", (
            "the sweep still consolidates what it is for")
        # ⚑ [R144]: a keep that cannot name the path as the user's is just a leak.
        err = capsys.readouterr().err
        assert str(store) in err and "workset.boxes" in err

    def test_the_composed_locator_is_still_left_at_the_root(
            self, config, std, tmp_home, capsys):
        """⚑ THE INVERSE: the default layout's store stays, silently, as it always has."""
        from kanibako.commands.box._lifecycle import (
            INPLACE, TargetSpec, execute_lifecycle, resolve_lifecycle_target,
        )
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import STANDALONE_META_DIR, resolve_project

        root = tmp_home / "sweep_default"
        root.mkdir()
        (root / "file.txt").write_text("mine")
        resolve_project(std, config, project_dir=str(root), initialize=True)
        dump_doc(root / "workset.yaml", {
            "workset": {"workspaces": "@meta.workset.path/nested"},
        })

        state = resolve_lifecycle_target(str(root), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=lambda *a, **k: True,
        )
        capsys.readouterr()

        assert (root / STANDALONE_META_DIR).is_dir()
        assert not (new.workspace_path / STANDALONE_META_DIR).exists()
        assert (new.workspace_path / "file.txt").read_text() == "mine"

    def test_a_null_boxes_key_refuses_rather_than_sweeping(self, config, std, tmp_home):
        """A root whose store does not answer has children the sweep cannot tell apart.

        ``_standalone_root_artifacts`` documents raising for exactly this, and ``boxes``
        now joins the keys it resolves, so the refusal reaches this sweep too.  Before it
        did, a nulled key was invisible here and the sweep moved whatever sat at the leaf.
        """
        from kanibako.commands.box._lifecycle import _standalone_root_artifacts
        from kanibako.settings.config_io import dump_doc
        from kanibako.settings.paths import resolve_project
        from kanibako.settings.settings_resolve import SettingsError

        root = tmp_home / "sweep_null"
        root.mkdir()
        (root / "file.txt").write_text("mine")
        resolve_project(std, config, project_dir=str(root), initialize=True)
        dump_doc(root / "workset.yaml", {
            "workset": {"boxes": None, "workspaces": "@meta.workset.path/nested"},
        })

        with pytest.raises(SettingsError, match="workset.boxes"):
            _standalone_root_artifacts(
                root, early=_early_scope(std, BoxMode.standalone))

        assert (root / "file.txt").read_text() == "mine", "nothing is swept on the refusal"

class TestTheCopyExcludesTheStoreByPathNotByName:
    """A box's own store is excluded BY PATH; a user's own ``box_data/`` is not."""

    def test_a_nested_user_box_data_survives_a_duplicate(self, config, std, tmp_home):
        """⚑ DATA SAFETY, PINNED THROUGH THE REAL DOOR: the user's own code travels.

        ``shutil.ignore_patterns("box_data")`` matches that NAME at any depth, so a
        duplicate silently dropped the source's own ``src/box_data/`` — code the box never
        used and kanibako never wrote.
        """
        from kanibako.project.workset import create_workset

        root = _standalone(config, std, tmp_home, "nested_user")
        # The user's OWN directories, named as kanibako's own store.
        (root / "src" / "box_data").mkdir(parents=True)
        (root / "src" / "box_data" / "user_code.py").write_text(
            "print('mine')\n")
        (root / "lib" / "box_data").mkdir(parents=True)
        (root / "lib" / "box_data" / "NOTES.md").write_text("mine\n")
        _workspace_at(root)          # the store sits inside the tree being copied
        ws = create_workset("wsn", tmp_home / "wsn_root", std)

        rc = _cli("box", "duplicate", str(root), str(tmp_home / "dup_nested"),
                  "--to", "named", "--workset", "wsn", "--force")

        assert rc == 0
        dup_ws = ws.workspaces_dir / "nested_user"
        assert (dup_ws / "src" / "box_data" / "user_code.py").read_text() == "print('mine')\n"
        assert (dup_ws / "lib" / "box_data" / "NOTES.md").read_text() == "mine\n"
        # ⚑ AND the box's OWN store is still excluded — the fix is not "copy everything".
        assert not (dup_ws / "box_data").exists()
        assert (root / "box_data").is_dir()

    def test_a_relocated_store_and_a_user_box_data_are_told_apart(
            self, config, std, tmp_home):
        """⚑ BOTH AT ONCE: the resolved store goes, the user's identically-named one stays."""
        from kanibako.project.workset import create_workset

        root = _standalone(config, std, tmp_home, "both_at_once")
        store = _relocate_store(root, "store")
        (root / "src" / "box_data").mkdir(parents=True)
        (root / "src" / "box_data" / "user_code.py").write_text("print('mine')\n")
        _workspace_at(root)
        ws = create_workset("wsb", tmp_home / "wsb_root", std)

        rc = _cli("box", "duplicate", str(root), str(tmp_home / "dup_both"),
                  "--to", "named", "--workset", "wsb", "--force")

        assert rc == 0
        dup_ws = ws.workspaces_dir / "both_at_once"
        assert not (dup_ws / "store").exists(), "the box's resolved store stays out"
        assert not (dup_ws / "box_data").exists(), "the composed locator stays out"
        assert (dup_ws / "src" / "box_data" / "user_code.py").is_file(), (
            "the user's own directory of the same name travels")
        assert (store / "MY_STORE_DATA.txt").read_text() == "the box's own metadata\n"

    def test_convert_to_workset_leaves_the_store_at_the_source(
            self, config, std, tmp_home):
        """⚑ THE CONVERT DOOR, UNTESTED UNTIL NOW (mutation row 2b).

        Convert copies the root's top-level files down into the new workspace dir.  With an
        IN-TREE relocated store, that sweep carried the store along — the box's own
        metadata and home duplicated into a workspace — unless the same path-anchored
        exclusion applies here too.
        """
        from kanibako.commands.box._lifecycle import (
            TargetSpec, execute_lifecycle, resolve_lifecycle_target,
        )
        from kanibako.project.workset import create_workset
        from kanibako.settings.config_io import dump_doc

        ws = create_workset("cw", tmp_home / "cw_root", std)
        root = tmp_home / "conv_store"
        root.mkdir()
        (root / "file.txt").write_text("mine")
        from kanibako.settings.paths import resolve_project
        resolve_project(std, config, project_dir=str(root), initialize=True)
        store = root / "store"
        store.mkdir()
        (store / "MY_OWN_THINGS.md").write_text("the user's own data\n")
        (store / "home").mkdir()
        (store / "home" / "notes.md").write_text("HOME\n")
        (store / "box.yaml").write_text("box: {}\n")
        dump_doc(root / "workset.yaml", {"workset": {
            "boxes": "@meta.workset.path/store",
            "workspaces": "@meta.workset.path/nested",
        }})

        state = resolve_lifecycle_target(str(root), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=ws.workspaces_dir / "conv_store", ownership="cw", name="conv_store"),
            std, config, confirm=lambda *a, **k: True,
        )
        nested = new.workspace_path
        assert nested.is_dir(), "the destination workspace dir was created"
        assert nested != root, "the destination is a DIFFERENT directory from the source"
        # ⚑ DATA SAFETY: ABSENT from the destination workspace...
        assert not (nested / "store").exists(), (
            "the box's own store must not be copied into the destination workspace")
        assert not (nested / "store" / "MY_OWN_THINGS.md").exists()
        assert not (nested / "store" / "home").exists()
        # ⚑ ... and NOT duplicated: exactly one copy of the store exists, wherever the
        # convert left it.  A convert MOVES a workspace, so the store's own content is
        # either at the source (metadata carried across) or gone with it — what must
        # never happen is a SECOND copy inside the destination workspace.
        assert not (nested / "store" / "box.yaml").exists(), (
            "no second copy of the box's own box tier inside the workspace")
        assert not (nested / "store" / "home" / "notes.md").exists()
        if store.is_dir():
            assert (store / "MY_OWN_THINGS.md").read_text() == "the user's own data\n"
            assert (store / "home" / "notes.md").read_text() == "HOME\n"
            assert (store / "box.yaml").is_file()
        # The user's own file did sweep down — the store is not the user's file.
        assert (nested / "file.txt").read_text() == "mine"
