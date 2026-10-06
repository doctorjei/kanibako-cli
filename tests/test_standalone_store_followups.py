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
    ``workset.yaml``.  The leftover ``box_data/`` stays: the spec makes it an ordinary
    directory the user may keep or remove — detection reads the root's ``workset.yaml``
    registry null, not this leaf — so leaving it is the shape a relocating user has.
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
            root.resolve(), elsewhere.resolve(), mode=BoxMode.standalone,
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

        ⚑ RE-SHAPED TO A SHAPE A REAL COMMAND PRODUCES (ruled 153).  This pin used to
        build a PRIMARY project whose OWN ``workset.yaml`` declared ``boxes``/
        ``workspaces``.  No command on main can make that shape: ``resolve_project(…,
        initialize=True)`` writes NOTHING under a primary project's own root (measured —
        the tree is empty), and the real write verb, ``workset set <ws>
        workset.boxes=…``, writes the WORKSET's settings file under the data dir, not the
        user's project root.  Hand-dumped into the project root it still does not change the
        mode, because a primary workset is found by its registry entry FIRST
        (system-design § "Detection & import").  Under the ruled rule that ``root/store``
        would be the USER's own directory, and copying it would be CORRECT — so the old
        shape asserted the opposite of the rule about a tree no command can create.

        The reachable shape carrying the same hazard is a STANDALONE box whose store is
        repointed in-tree: ``box create`` (``resolve_standalone_project(…,
        initialize=True)``) then a ``workset.boxes`` repoint — exactly what
        :func:`_relocate_store` builds for the sibling pins.  The assertion is unchanged.
        """
        from kanibako.commands.box._lifecycle import (
            TargetSpec, execute_lifecycle, resolve_lifecycle_target,
        )
        from kanibako.project.workset import create_workset

        ws = create_workset("cw", tmp_home / "cw_root", std)
        root = _standalone(config, std, tmp_home, "conv_store")
        store = _relocate_store(root, "store")
        (root / "file.txt").write_text("mine")
        _workspace_at(root)          # the store sits INSIDE the tree being copied

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
        assert not (nested / "store" / "MY_STORE_DATA.txt").exists()
        assert not (nested / "store" / "home").exists()
        # ⚑ ... and NOT duplicated: exactly one copy of the store exists, wherever the
        # convert left it.  A convert MOVES a workspace, so the store's own content is
        # either at the source (metadata carried across) or gone with it — what must
        # never happen is a SECOND copy inside the destination workspace.
        assert not (nested / "store" / "box.yaml").exists(), (
            "no second copy of the box's own box tier inside the workspace")
        assert not (nested / "store" / "home" / "notes.md").exists()
        if store.is_dir():
            assert (store / "MY_STORE_DATA.txt").read_text() == "the box's own metadata\n"
            assert (store / "home" / "notes.md").read_text() == "HOME\n"
            assert (store / "box.yaml").is_file()
        # The user's own file did sweep down — the store is not the user's file.
        assert (nested / "file.txt").read_text() == "mine"


class TestAMoveKeepsAUserOwnedBoxData:
    """⚑ D1, DATA SAFETY. A top-level ``box_data/`` is the USER'S directory unless the
    ``workset.boxes`` key resolves there.

    STEP-2 of ``box move`` applies the store exclusion to EVERY relocating source, so it
    resolves the store FOR THE SOURCE'S MODE rather than composing the standalone default
    leaf.  A primary or named box resolves its store OUTSIDE its workspace, so the
    exclusion lands on nothing and the user's own ``box_data/`` travels.

    ⚑ Asserted on what SURVIVES at the destination, never on the exit code — every one
    of these returns 0 whether or not the file is still there.
    """

    def test_a_primary_box_move_keeps_its_own_top_level_box_data(
            self, config, std, tmp_home):
        from kanibako.settings.paths import resolve_project

        src = tmp_home / "proj"
        src.mkdir()
        (src / "app.py").write_text("code\n")
        (src / "box_data").mkdir()
        (src / "box_data" / "mine.txt").write_text("the user's own file\n")
        resolve_project(std, config, project_dir=str(src), initialize=True)

        dest = tmp_home / "proj2"
        rc = _cli("box", "move", str(src), str(dest), "--force")

        assert rc == 0
        assert (dest / "box_data" / "mine.txt").is_file(), (
            "a primary box's own box_data/ must travel with the move, not be excluded "
            "from the copy and deleted with the source")
        assert (dest / "box_data" / "mine.txt").read_text() == "the user's own file\n"
        assert (dest / "app.py").read_text() == "code\n"

    def test_an_in_tree_named_box_move_keeps_its_own_top_level_box_data(
            self, config, std, tmp_home):
        """An IN-TREE NAMED box: its workspace leaf's ``box_data/`` is the user's too.

        Registered with ``add_project`` and moved OUT of the workset — an in-tree
        ``<new>`` has to be the member's own ``{workset.workspaces}/<name>``, which
        is not a move at all.
        """
        from kanibako.project.workset import add_project, create_workset

        ws = create_workset("wskeep", tmp_home / "wskeep_root", std)
        member = ws.workspaces_dir / "alpha"
        member.mkdir(parents=True)
        (member / "app.py").write_text("code\n")
        (member / "box_data").mkdir()
        (member / "box_data" / "mine.txt").write_text("the user's own file\n")
        add_project(ws, "alpha", member, std)

        dest = tmp_home / "moved_out"
        rc = _cli("box", "move", str(member), str(dest), "--force")

        assert rc == 0
        assert (dest / "box_data" / "mine.txt").read_text() == "the user's own file\n"
        assert (dest / "app.py").read_text() == "code\n"

    def test_a_default_layout_standalone_move_keeps_its_user_box_data(
            self, config, std, tmp_home):
        """⚑ R1 — THE SAME SEAM ONE LAYOUT DEEPER, AND IT IS STILL A DELETION.

        A DEFAULT-LAYOUT standalone keeps its workspace at ``<root>/workspace``, so the
        workspace is NOT the root.  ``box_metadata_dir`` answers a standalone store from
        the ROOT's ``workset.yaml``; handed the workspace dir instead it reads
        ``<root>/workspace/workset.yaml`` (absent), falls back to
        ``<root>/workspace/box_data`` — the USER'S directory — and excludes it.  The copy
        then drops the file and STEP 5's retire deletes it with the source: rc 0, gone.

        Rooting the resolver on ``state.metadata_path`` answers ``<root>/box_data``, which
        sits OUTSIDE the copied tree, so the exclusion lands on nothing and the file
        travels.  ``metadata_path`` IS the standalone root here; ``workspace_path`` is one
        level below it.

        ⚑ Pinned on SURVIVAL + content, per the 153rd ruling.  It currently lands at
        ``<dest>/box_data/``, merged into the store — placement is pre-existing (base does
        the same) and boarded as its own DATA SAFETY row, so this asserts the file arrives
        anywhere intact rather than naming a path no shipped version produced.
        """
        root = _standalone(config, std, tmp_home, "mv_default_layout")
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "box_data").mkdir()
        (workspace / "box_data" / "mine.txt").write_text("the user's own file\n")
        (workspace / "app.py").write_text("code\n")

        dest = tmp_home / "mv_default_dest"
        rc = _cli("box", "move", str(root), str(dest), "--force")

        assert rc == 0
        landed = sorted(dest.rglob("mine.txt"))
        assert landed, (
            "a default-layout standalone move must not drop the user's own "
            "workspace/box_data/ out of the copy and then delete it with the source")
        assert landed[0].read_text() == "the user's own file\n"
        assert list(dest.rglob("app.py")), "ordinary workspace content still travels"

    def test_a_standalone_box_move_takes_its_store_rather_than_stranding_it(

            self, config, std, tmp_home):
        """⚑ THE INVERSE, and the one a MOVE actually risks: the exclusion must not strand
        the store at a source the move then deletes.

        With ``workset.workspaces`` repointed AT the root, the store sits INSIDE the
        tree being copied — precisely where an over-broad exclusion would drop it and
        the source cleanup would take it with.  It arrives instead.
        """
        root = _standalone(config, std, tmp_home, "mv_travel")
        store = root / "box_data"
        (store / "PRECIOUS.txt").write_text("must arrive\n")
        _workspace_at(root)

        dest = tmp_home / "mv_external"
        rc = _cli("box", "move", str(root), str(dest), "--force")

        assert rc == 0
        assert (dest / "box_data" / "PRECIOUS.txt").read_text() == "must arrive\n"

    def test_a_standalone_move_still_excludes_the_superseded_default_leaf(
            self, config, std, tmp_home):
        """⚑ ARM (b) OF THE RULED EXCLUSION, at the very arm D1 changed.

        A standalone box whose store is REPOINTED away from the default leaf leaves
        ``<root>/box_data`` holding the box's OWN superseded metadata — a stale ``home/``
        and box tier.  Mode-aware resolution must still exclude that leaf from the copy:
        it is the box's stale metadata, not the user's, and dropping the term would drag
        it into the destination.  The RESOLVED store is untouched at the source.
        """
        root = _standalone(config, std, tmp_home, "mv_superseded")
        _relocate_store(root, "store")
        (root / "box_data" / "home").mkdir(parents=True, exist_ok=True)
        (root / "box_data" / "home" / "STALE.md").write_text("stale home\n")
        (root / "box_data" / "box.yaml").write_text("box: {}\n")
        _workspace_at(root)          # the superseded leaf sits INSIDE the copied tree

        dest = tmp_home / "mv_superseded_dest"
        rc = _cli("box", "move", str(root), str(dest), "--force")

        assert rc == 0
        assert not (dest / "box_data").exists(), (
            "the superseded default leaf must not travel with the move")
        assert not (dest / "box_data" / "home" / "STALE.md").exists()
        # ⚑ The RESOLVED store is the one that moves — to the destination under the name
        # the key gives it, carrying its own content.
        assert (dest / "store" / "MY_STORE_DATA.txt").read_text() == "the box's own metadata\n"


class TestStandalonePurgeKeepsThePerOwnerRefusal:
    """⚑ D2. ``25c770f1`` dropped the per-owner refusal from ``_rm_standalone``.

    With it gone, ``box rm <sa> --purge --force`` purged a standalone store while the
    SYSTEM tier handed a per-owner key a value that reaches no owner identity — the
    state keyspec §0 forbids a deleting verb to act on.  Base refused with rc 1 and
    changed nothing; the series deleted.

    ⚑ Pinned through THIS door on purpose: ``test_per_owner_read_door.py`` did not
    catch the removal, so a read-tier test is not evidence that the verb is guarded.
    """

    def _share_the_system_mailboxes(self, tmp_home):
        """Hand-write a SYSTEM-tier per-owner value the set door would refuse to create."""
        from kanibako.settings.config import system_settings_path
        from kanibako.settings.config_io import dump_doc, load_doc

        sysf = system_settings_path()
        sysf.parent.mkdir(parents=True, exist_ok=True)
        doc = load_doc(sysf) if sysf.exists() else {}
        doc.setdefault("workset", {}).setdefault("channels", {})
        doc["workset"]["channels"]["mailboxes"] = str(tmp_home / "shared_mailboxes")
        dump_doc(sysf, doc)
        return sysf

    def test_the_purge_refuses_and_changes_nothing(self, config, std, tmp_home):
        from kanibako.cli import main

        root = _standalone(config, std, tmp_home, "po_purge")
        store = root / "box_data"
        (store / "PRECIOUS.txt").write_text("do not delete me\n")
        self._share_the_system_mailboxes(tmp_home)

        with pytest.raises(SystemExit) as excinfo:
            main(["box", "rm", str(root), "--purge", "--force"])

        assert excinfo.value.code != 0, (
            "a system-tier per-owner value that reaches no owner identity must stop "
            "the purge; the series returned 0 and deleted")
        assert (store / "PRECIOUS.txt").read_text() == "do not delete me\n", (
            "the refusal runs BEFORE the unregister and the delete, so nothing is gone")

    def test_the_purge_still_runs_with_no_inherited_value(self, config, std, tmp_home):
        """⚑ THE INVERSE: the guard is not "never purge". An ordinary purge still works."""
        root = _standalone(config, std, tmp_home, "po_normal")
        store = root / "box_data"
        (store / "PRECIOUS.txt").write_text("delete me\n")

        rc = _cli("box", "rm", str(root), "--purge", "--force")

        assert rc == 0
        assert not store.exists(), "an ordinary standalone purge still removes the store"


class TestATeardownPlanFollowsASystemTierRepoint:
    """⚑ D4, ruling (c): a SYSTEM-tier ``workset.boxes`` repoint reaches the teardown.

    The behavior works — the reviewer probed ``system set 'workset.boxes=...'`` and
    saw the store move.  What was missing is a pin that the PLAN the purge acts on is
    computed against the repointed store, so the verb deletes what the key names and
    leaves what it does not.  Red without threading the system tier through.
    """

    def test_the_plan_targets_the_repointed_store(self, config, std, tmp_home,
                                                config_file):
        from kanibako.settings.config import load_config, system_settings_path
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import (
            load_std_paths, standalone_store_teardown_plan,
        )

        sysf = system_settings_path()
        sysf.parent.mkdir(parents=True, exist_ok=True)
        doc = load_doc(sysf) if sysf.exists() else {}
        doc.setdefault("workset", {})
        doc["workset"]["boxes"] = "@meta.workset.path/sysstore"
        dump_doc(sysf, doc)
        # The EARLY SYSTEM TIER is read when ``std`` is built, so the fixture predates
        # the repoint written above.  Rebuild both or the plan reads a tier without it.
        config = load_config(config_file)
        std = load_std_paths(config)

        root = _standalone(config, std, tmp_home, "tp_repoint")
        sysstore = root / "sysstore"
        sysstore.mkdir(exist_ok=True)
        (sysstore / "PRECIOUS.txt").write_text("the repointed store\n")

        removable, retained = standalone_store_teardown_plan(
            root, early=_early_scope(std, BoxMode.standalone))

        rendered = f"removable={removable!r} retained={retained!r}"
        assert removable is not None and "sysstore" in str(removable), (
            f"the teardown plan must name the repointed store, got: {rendered}")
        assert (sysstore / "PRECIOUS.txt").read_text() == "the repointed store\n"
