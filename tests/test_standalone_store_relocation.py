"""The two doors that still composed ``box_data/``: ``box rm`` and the project plugin scan.

``workset.boxes`` is a repointable workset key whose standalone value is the
``{meta.workset.path}/box_data`` DEFAULT, so ``box_data/`` names a standalone store only
while the key is unset.  These pin that ``box purge``/``box rm`` read the key instead of
composing the leaf, and that a project's file-drop plugins dir hangs off the RESOLVED store.

⚑ ``box rm --purge`` DELETES, so "resolved" is pinned separately from "allowed to delete":
each case asserts what is GONE and what SURVIVED, never the exit code alone.

⚑ EVERY CASE GOES THROUGH A DOOR THAT EXISTS AT THE BASE (``cli.build_parser`` plus
``args.func``, and ``discover_targets``), so a base run fails on an ASSERTION about
behaviour rather than on an import of a name this change introduced.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import (
    BoxMode, _early_scope, resolve_standalone_project)


def _make_standalone(config, std, tmp_home, leaf: str) -> Path:
    """A materialized standalone box at *leaf*, its store in-tree at the default leaf."""
    root = tmp_home / leaf
    root.mkdir()
    (root / "user_code.py").write_text("print('mine')\n")
    (root / "USER_DOCS.md").write_text("my notes\n")
    resolve_standalone_project(std, config, str(root), initialize=True)
    return root


def _workspace_of(config, std, root: Path) -> Path:
    """THE path production hands ``discover_targets``: ``proj.project_path``.

    For a standalone box that is the WORKSPACE, not the root, so a scan that answers
    the store off the argument as given reads a ``workset.yaml`` that never carried
    ``workset.boxes``.  Deriving it here rather than spelling ``root / "workspace"``
    keeps the test pinned to what callers actually pass, not to a layout guess.
    """
    return resolve_standalone_project(
        std, config, str(root), initialize=False).project_path


def _relocate(root: Path, target: Path) -> Path:
    """MOVE the store to ``target/box_data`` and point ``workset.boxes`` there.

    The leftover ``box_data/`` is left in place because that is the shape a
    relocating user actually has.  ⚑ It is NOT what detection finds the box by:
    since SD the root's own ``workset.registry`` null defines it, so this leftover
    directory is inert — which is why these cases can assert against it.
    """
    target.mkdir(parents=True)
    (target / "UNRELATED_USER_FILE.txt").write_text("not kanibako's\n")
    store = target / "box_data"
    shutil.move(str(root / "box_data"), str(store))
    write_nested_key(root / "workset.yaml", ("workset",), "boxes", str(store))
    (root / "box_data").mkdir(exist_ok=True)
    return store


def _rm(root: Path, *extra: str) -> int:
    """The real ``box rm`` door, through the production parser."""
    from kanibako import cli

    args = cli.build_parser().parse_args(["box", "rm", str(root), "--force", *extra])
    return int(args.func(args) or 0)


def _rm_by_name(name: str, *extra: str) -> int:
    """``box rm <name>`` — the address a parked entry is actually reached by."""
    from kanibako import cli

    args = cli.build_parser().parse_args(["box", "rm", name, "--force", *extra])
    return int(args.func(args) or 0)


def _point_at_verbatim(root: Path, value: str, target: Path) -> Path:
    """Point ``workset.boxes`` at *value* VERBATIM, and materialize what it names.

    Unlike :func:`_relocate` this stores the value as written rather than an absolute
    path, which is what lets a case spell a store that leaves the root by ``..`` or
    through a symlinked parent component.
    """
    target.mkdir(parents=True, exist_ok=True)
    (target / "STORE_DATA.txt").write_text("the box store\n")
    write_nested_key(root / "workset.yaml", ("workset",), "boxes", value)
    return target


@pytest.fixture
def plugin_source():
    """The repo's own file-drop plugin source — a hand-built Target would not register."""
    here = Path(__file__).resolve().parent
    text = (here / "test_targets" / "test_discovery.py").read_text(encoding="utf-8")
    return text.split("_PLUGIN_SOURCE = '''\\\n")[1].split("\n'''")[0]


def _drop_plugin(directory: Path, filename: str, name: str, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(source.format(name=name), encoding="utf-8")


class TestBoxRmReadsTheStore:
    def test_a_store_relocated_inside_the_root_is_removed_not_left_behind(
            self, config, std, tmp_home):
        """⚑ THE PIN: ``rm --purge`` takes the RESOLVED store, and takes only it.

        Base composed ``<root>/box_data``, so it removed the LOCATOR marker and left the
        box's real metadata on disk — the teardown reported success over a live store.
        """
        root = _make_standalone(config, std, tmp_home, "rm_inside")
        store = _relocate(root, root / "inside_store")
        (store.parent / "UNRELATED_SIBLING.txt").write_text("not the store\n")

        rc = _rm(root, "--purge")

        assert rc == 0
        assert not store.exists(), "the resolved store must not be left behind"
        assert (store.parent / "UNRELATED_SIBLING.txt").is_file()
        assert (root / "user_code.py").is_file()
        assert (root / "USER_DOCS.md").is_file()

    def test_a_store_outside_the_root_survives_and_is_named(
            self, config, std, tmp_home, capsys):
        """A store the user merely nominated is theirs; the teardown names it, not removes it."""
        root = _make_standalone(config, std, tmp_home, "rm_outside")
        store = _relocate(root, tmp_home / "outside_store")
        (store / "MY_OWN_NOTES.md").write_text("mine\n")

        rc = _rm(root, "--purge")

        assert rc == 0
        assert store.is_dir()
        assert (store / "MY_OWN_NOTES.md").is_file()
        assert (store.parent / "UNRELATED_USER_FILE.txt").is_file()
        assert (root / "user_code.py").is_file()
        err = capsys.readouterr().err
        assert str(store) in err and str(root) in err

    def test_a_store_outside_the_root_leaves_the_root_workable(
            self, config, std, tmp_home):
        """⚑ THE INVERSE OF THE ABOVE: nothing in-root is torn down behind a kept store.

        The ROOT ``workset.yaml`` carries the repoint itself, so unlinking it while the box's
        metadata survives would leave a box that answers to the composed default again.
        """
        root = _make_standalone(config, std, tmp_home, "rm_outside_intact")
        _relocate(root, tmp_home / "outside_store_intact")

        rc = _rm(root, "--purge")

        assert rc == 0
        assert (root / "workset.yaml").is_file()
        assert (root / "box_data").is_dir()

    def test_rm_without_purge_parks_a_deregistered_entry_for_a_repointed_store(
            self, config, std, tmp_home):
        """The existence check behind the park decision reads the key, not the literal.

        Without it a repointed box looks already gone and its recovery blob is never
        written, so the metadata is stranded with no way back to it.
        """
        from kanibako.project import registry_store

        root = _make_standalone(config, std, tmp_home, "rm_park")
        _relocate(root, root / "park_store")

        rc = _rm(root)
        parked = registry_store.load_deregistered(std.registry)

        assert rc == 0
        assert len(parked) == 1
        assert next(iter(parked.values()))["metadata"] == str(root)

    def test_purging_a_deregistered_entry_deletes_the_resolved_store(
            self, config, std, tmp_home):
        """⚑ THE SECOND DOOR: ``rm --purge`` of a parked entry, addressed by NAME.

        Its existence check composed ``box_data/``, so with the store moved and the LOCATOR
        marker still in place it deleted the marker — a directory the box never used — and
        left the real store behind under a success message.
        """
        from kanibako.project import registry_store

        root = _make_standalone(config, std, tmp_home, "rm_dereg")
        store = _relocate(root, root / "dereg_store")
        assert _rm(root) == 0
        name = next(iter(registry_store.load_deregistered(std.registry)))

        rc = _rm_by_name(name, "--purge")

        assert rc == 0
        assert not store.exists(), "the parked root's resolved store must be deleted"
        assert (root / "user_code.py").is_file()


class TestAStoreThatLeavesTheRootIsNeverRemoved:
    """A teardown must not leave the root BY SPELLING.

    ``workset.boxes`` is answered as it was WRITTEN, so a value can name a directory
    outside the root while its text reads as a descendant of it — ``..`` segments, or a
    parent component that is a symlink out of the tree.  Every case here asserts the
    USER'S OWN files, because the verb returns 0 in all of them either way: an exit-code
    assertion would have passed while the data was gone.
    """

    def test_a_dotdot_sibling_store_is_the_users_and_survives(
            self, config, std, tmp_home):
        """``@meta.workset.path/../<name>`` names a SIBLING project, not this box's store."""
        root = _make_standalone(config, std, tmp_home, "esc_sibling")
        sibling = root.parent / f"{root.name}_store"
        _point_at_verbatim(root, f"@meta.workset.path/../{sibling.name}", sibling)
        (sibling / "MY_OTHER_PROJECT.md").write_text("not this box's\n")

        rc = _rm(root, "--purge")

        assert rc == 0
        assert (sibling / "MY_OTHER_PROJECT.md").is_file(), (
            "a SIBLING of the box is another project's directory")
        assert (sibling / "STORE_DATA.txt").is_file()
        assert (root / "user_code.py").is_file()

    def test_a_store_naming_the_roots_parent_spares_the_whole_project(
            self, config, std, tmp_home):
        """⚑ THE WORST SPELLING: ``..`` IS THE ROOT'S PARENT, so the root itself is at stake."""
        root = _make_standalone(config, std, tmp_home, "esc_parent")
        other = root.parent / "OTHER_PROJECT.txt"
        other.write_text("another project's data\n")
        _point_at_verbatim(root, "@meta.workset.path/..", root.parent)

        rc = _rm(root, "--purge")

        assert rc == 0
        assert root.is_dir(), "the box root is the user's directory"
        assert (root / "user_code.py").is_file()
        assert (root / "USER_DOCS.md").is_file()
        assert other.is_file(), "a teardown of one box took another project's file"

    def test_a_store_behind_a_symlinked_parent_component_is_the_users_and_survives(
            self, config, std, tmp_home):
        """A ``via/`` that is a symlink out of the tree puts the store outside the root."""
        root = _make_standalone(config, std, tmp_home, "esc_symlink")
        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "MY_DATA.txt").write_text("mine\n")
        store = elsewhere / "store"
        (root / "via").symlink_to(elsewhere)
        _point_at_verbatim(root, "@meta.workset.path/via/store", store)

        rc = _rm(root, "--purge")

        assert rc == 0
        assert (store / "STORE_DATA.txt").is_file(), (
            "the store resolves outside the root, so it is not this teardown's to remove")
        assert (elsewhere / "MY_DATA.txt").is_file()
        assert (root / "user_code.py").is_file()

    def test_a_store_at_the_root_is_never_removed(self, config, std, tmp_home):
        """``workset.boxes: '@meta.workset.path'`` nominates the root; only STRICTLY
        below counts as kanibako's."""
        root = _make_standalone(config, std, tmp_home, "esc_at_root")
        _point_at_verbatim(root, "@meta.workset.path", root)

        rc = _rm(root, "--purge")

        assert rc == 0
        assert root.is_dir()
        assert (root / "user_code.py").is_file()
        assert (root / "USER_DOCS.md").is_file()

    def test_the_root_itself_reached_through_a_symlink_still_loses_its_own_store(
            self, config, std, tmp_home):
        """⚑ THE INVERSE, so the containment test is not merely the cautious one.

        Resolving must not cost kanibako its own store when the box is ADDRESSED through
        a symlink: the store is strictly below the resolved root, so it goes.
        """
        from kanibako.settings.paths import standalone_store_teardown_plan

        root = _make_standalone(config, std, tmp_home, "esc_symlinked_root")
        store = root / "stores" / "box_data"
        _point_at_verbatim(root, "@meta.workset.path/stores/box_data", store)
        via = tmp_home / "esc_symlinked_root_link"
        via.symlink_to(root)

        removable, retained = standalone_store_teardown_plan(
            via, early=_early_scope(std, BoxMode.standalone))

        assert removable == store.resolve()
        assert retained is None

    def test_an_absolute_store_path_below_a_symlinked_root_is_still_removed(
            self, config, std, tmp_home):
        """The same box, spelled by its REAL path — strictly below the resolved root, so
        kanibako's own store and removable."""
        from kanibako.settings.paths import standalone_store_teardown_plan

        root = _make_standalone(config, std, tmp_home, "esc_real_spelling")
        store = root / "stores" / "box_data"
        _point_at_verbatim(root, str(store), store)
        via = tmp_home / "esc_real_spelling_link"
        via.symlink_to(root)

        removable, retained = standalone_store_teardown_plan(
            via, early=_early_scope(std, BoxMode.standalone))

        assert removable == store.resolve()
        assert retained is None


class TestProjectPluginsFollowTheStore:
    def test_the_scan_follows_a_repoint(self, config, std, tmp_home, plugin_source):
        """A plugin dropped in the RESOLVED store is discovered, as ``discover_targets`` is
        what asks "is this agent installed?"."""
        from kanibako.targets import discover_targets

        root = _make_standalone(config, std, tmp_home, "pl_repoint")
        store = _relocate(root, root / "moved_store")
        _drop_plugin(store / "plugins", "movedplug.py", "movedplug", plugin_source)

        assert "movedplug" in discover_targets(_workspace_of(config, std, root))

    def test_the_locator_markers_plugins_dir_is_no_longer_scanned(
            self, config, std, tmp_home, plugin_source):
        """The leftover ``box_data/`` marker is not the box's store, so it is not its plugins."""
        from kanibako.targets import discover_targets

        root = _make_standalone(config, std, tmp_home, "pl_locator")
        _relocate(root, root / "moved_store")
        _drop_plugin(root / "box_data" / "plugins", "stale.py", "stalebag", plugin_source)

        assert "stalebag" not in discover_targets(_workspace_of(config, std, root))

    def test_a_plugins_dir_is_only_read(self, config, std, tmp_home, plugin_source):
        """Discovery is a scan: every other entry in the plugins dir survives it."""
        from kanibako.targets import discover_targets

        root = _make_standalone(config, std, tmp_home, "pl_readonly")
        store = _relocate(root, root / "moved_store")
        plugins = store / "plugins"
        _drop_plugin(plugins, "okplug.py", "okplug", plugin_source)
        (plugins / "USER_NOTES.md").write_text("mine\n")
        (plugins / "subdir").mkdir()
        (plugins / "subdir" / "holiday.jpg").write_text("mine\n")

        assert "okplug" in discover_targets(_workspace_of(config, std, root))
        assert (plugins / "USER_NOTES.md").is_file()
        assert (plugins / "subdir" / "holiday.jpg").is_file()

    def test_an_unrepointed_box_still_scans_the_default_leaf(
            self, config, std, tmp_home, plugin_source):
        """⚑ THE COMMON CASE IS UNCHANGED: no ``workset.boxes``, so ``<root>/box_data``."""
        from kanibako.targets import discover_targets

        root = _make_standalone(config, std, tmp_home, "pl_default")
        _drop_plugin(root / "box_data" / "plugins", "defplug.py", "defplug", plugin_source)

        assert "defplug" in discover_targets(_workspace_of(config, std, root))