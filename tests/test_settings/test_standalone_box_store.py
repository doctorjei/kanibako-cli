"""A STANDALONE box's store follows ``workset.boxes`` ([R177] key; spec §2c STANDALONE).

``workset.boxes`` is a repointable workset key whose standalone value is the
``{meta.workset.path}/box_data`` DEFAULT — so ``box_data/`` names the store only while
the key is unset.  These pin that home, the box tier and the ``purge`` teardown read the
key rather than composing the leaf.

⚑ THE OUT-OF-ROOT STORE IS THE DATA-SAFETY HALF: it is the directory a verb would
``rm -rf`` on a user's behalf, so "retained and named" is pinned separately from
"removed" — a resolver that returns the right path is not by itself a teardown that is
allowed to delete it.

⚑ EVERY CASE HERE GOES THROUGH A DOOR THAT EXISTS AT THE BASE (``resolve_standalone_project``,
``box_metadata_dir``, ``clean.run``), so a base run fails on an ASSERTION about
behaviour rather than on an import of a name this change introduced.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

from kanibako.commands import clean as clean_cmd
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    BoxMode,
    _early_scope,
    box_metadata_dir,
    box_workset_settings_paths,
    resolve_standalone_project,
)


def _make_standalone(config, std, tmp_home, leaf: str) -> tuple[Path, str]:
    """A materialized standalone box at *leaf*, its store in-tree at the default leaf."""
    root = tmp_home / leaf
    root.mkdir()
    (root / "user_code.py").write_text("print('mine')\n")
    proj = resolve_standalone_project(std, config, str(root), initialize=True)
    return root, proj.name


def _relocate(root: Path, target: Path, *, keep_locator: bool = True) -> Path:
    """Move the store to *target* and point ``workset.boxes`` at it."""
    target.mkdir(parents=True)
    (target / "UNRELATED_USER_FILE.txt").write_text("not kanibako's\n")
    shutil.move(str(root / "box_data"), str(target / "box_data"))
    write_nested_key(root / "workset.yaml", ("workset",), "boxes", str(target / "box_data"))
    if keep_locator:
        # The spec keeps ``box_data/`` as the detection LOCATOR, so a relocating user
        # leaves it — and detection is how a teardown is reached at all.
        (root / "box_data").mkdir(exist_ok=True)
    return target / "box_data"


def _purge(root: Path) -> int:
    """The real ``purge`` door, with the flags it would carry non-interactively."""
    return clean_cmd.run(SimpleNamespace(path=str(root), all_projects=False, force=True))


class TestTheStoreFollowsTheKey:
    def test_the_default_leaf_is_unchanged_for_an_unrepointed_box(self, config, std,
                                                                 tmp_home):
        """The common case is unchanged: no ``workset.boxes``, so ``<root>/box_data``."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_default")

        assert box_metadata_dir(
            BoxMode.standalone, root, early=_early_scope(std, BoxMode.standalone)
        ) == root / "box_data"

    def test_home_follows_a_repoint(self, config, std, tmp_home):
        """Home is spelled once against the store, so it moves with the key."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_home")
        store = _relocate(root, tmp_home / "home_store")

        proj = resolve_standalone_project(std, config, str(root), initialize=False)

        assert proj.shell_path == store / "home"

    def test_the_box_tier_follows_a_repoint(self, config, std, tmp_home):
        """The BOX tier is the other half of the store, and follows it too."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_tier")
        store = _relocate(root, tmp_home / "tier_store")

        proj = resolve_standalone_project(std, config, str(root), initialize=False)
        box_tier, _workset_tier = box_workset_settings_paths(proj)

        assert box_tier == store / "box.yaml"

    def test_box_metadata_dir_follows_a_repoint(self, config, std, tmp_home):
        """``box_metadata_dir`` is the helper the lifecycle verbs call; it must not re-literal."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_meta")
        store = _relocate(root, tmp_home / "meta_store")

        assert box_metadata_dir(
            BoxMode.standalone, root, early=_early_scope(std, BoxMode.standalone)
        ) == store


class TestPurgeWithARelocatedStore:
    """``purge`` is a real teardown, so the split has to hold end to end through it."""

    def test_a_store_outside_the_root_survives_with_the_users_data(self, config, std,
                                                                  tmp_home, capsys):
        """⚑ THE DATA-SAFETY PIN: purge deletes no directory the user merely nominated."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_purge_outside")
        store = _relocate(root, tmp_home / "user_store")
        (store / "MY_OWN_NOTES.md").write_text("mine\n")

        rc = _purge(root)

        assert rc == 0
        assert root.is_dir()
        assert (root / "user_code.py").is_file()
        assert store.is_dir(), "a relocated store is the user's; purge must not remove it"
        assert (store / "MY_OWN_NOTES.md").is_file()
        assert (store.parent / "UNRELATED_USER_FILE.txt").is_file()

    def test_a_retained_store_is_named_on_stderr(self, config, std, tmp_home, capsys):
        """A keep that cannot name the path as the user's is a silent leak, not a keep."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_purge_named")
        store = _relocate(root, tmp_home / "named_store")

        _purge(root)
        err = capsys.readouterr().err

        assert str(store) in err
        assert str(root) in err

    def test_purge_keeps_the_root_file_when_the_store_is_kept(self, config, std, tmp_home):
        """⚑ SAME LINE ``box rm --purge`` DRAWS: a kept store leaves the box whole.

        The ROOT ``workset.yaml`` carries the repoint itself, so unlinking it while the
        box's metadata survives leaves a box that answers to the composed default again.
        """
        root, _name = _make_standalone(config, std, tmp_home, "sa_purge_keeps_root")
        store = _relocate(root, tmp_home / "kept_store")

        rc = _purge(root)

        assert rc == 0
        assert store.is_dir()
        assert (root / "workset.yaml").is_file()
        assert (root / "user_code.py").is_file()

    def test_purge_removes_the_root_file_when_the_store_is_removed(
            self, config, std, tmp_home):
        """The inverse: with the store gone the root is unworkable, so its file goes too."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_purge_drops_root")
        _relocate(root, root / "inside_store")

        rc = _purge(root)

        assert rc == 0
        assert not (root / "workset.yaml").exists()

    def test_a_store_relocated_inside_the_root_is_removed(self, config, std, tmp_home):
        """The inverse: a store strictly below the root IS kanibako's, so it goes — and
        only it goes: an unrelated sibling in the same parent survives."""
        root, _name = _make_standalone(config, std, tmp_home, "sa_purge_inside")
        store = _relocate(root, root / "inside_store")
        (store.parent / "UNRELATED_SIBLING.txt").write_text("not the store\n")

        rc = _purge(root)

        assert rc == 0
        assert not store.exists()
        assert (store.parent / "UNRELATED_SIBLING.txt").is_file()
        assert (root / "user_code.py").is_file()


class TestCreateDoesNotFollowTheStoreParent:
    def test_the_vault_skeleton_is_stamped_at_the_root_not_the_store(
            self, config, std, tmp_home):
        """A create with a repointed store stamps ``vault/``'s .gitignore at the ROOT.

        The store's parent is not the workset root once ``workset.boxes`` is repointed, so
        deriving it from the store answers with a directory outside the box.
        """
        root = tmp_home / "sa_new"
        root.mkdir()
        store = tmp_home / "new_store"
        store.mkdir()
        write_nested_key(root / "workset.yaml", ("workset",), "boxes", str(store))

        proj = resolve_standalone_project(std, config, str(root), initialize=True)

        assert proj.shell_path == store / "home"
        assert not (store / ".gitignore").exists()
        assert not (store / "vault").exists()


def test_every_relocated_store_is_kept_out_of_the_way_of_the_workspace(config, std,
                                                                     tmp_home):
    """Two boxes with relocated stores: neither purge takes the other's directory."""
    root_a, _ = _make_standalone(config, std, tmp_home, "sa_two_a")
    root_b, _ = _make_standalone(config, std, tmp_home, "sa_two_b")
    store_a = _relocate(root_a, tmp_home / "shared_area", keep_locator=True)
    store_b = tmp_home / "shared_area" / "box_data"

    assert _purge(root_a) == 0

    assert store_a.is_dir()
    assert root_b.is_dir()
    assert (root_b / "user_code.py").is_file()
    assert store_b.is_dir()
