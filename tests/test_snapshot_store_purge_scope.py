"""A purge takes the box's OWN snapshot store, and NOTHING else.

Review r2 item 5: every purge verb removed the box's vault leaves but left
``.versions/<box>`` where the next box to hold that name would find it.  Measured
end to end: ``box rm --purge`` a box, create a NEW box of the same name at an
unrelated path, and it lists -- and RESTORES -- the dead box's files.

⚑ The other half of the rule is what must SURVIVE.  ``.versions`` is a BASE shared
by every box writing into the same vault share, and ``.unsorted`` is where the
boxes nobody could attribute had been filed.  A purge that took either would turn
one box's cleanup into every neighbour's data loss, so those are pinned too.

⚑ EVERY CASE GOES THROUGH A DOOR THAT EXISTS AT THE BASE (``cli.build_parser``
plus ``args.func``), so a base run fails on an ASSERTION about behaviour rather
than on an import of a name this change introduced.  The controls are named as
controls in their docstrings: they pass at the base and are here to catch the fix
over-reaching, not to prove the defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    load_std_paths, resolve_project, resolve_standalone_project,
)
from kanibako.snapshots import (
    LAYOUT_MARKER_NAME, UNSORTED_DIRNAME, _versions_dir, create_snapshot,
)

LEGACY_TS = "20200101T000000Z"


def _door(*argv: str) -> int:
    """A real verb, through the production parser."""
    from kanibako import cli

    args = cli.build_parser().parse_args(list(argv))
    return int(args.func(args) or 0)


def _store(vault_rw: Path, box: str) -> Path:
    """*box*'s own store under the base *vault_rw* shares."""
    return _versions_dir(vault_rw) / box


@pytest.fixture
def env(tmp_home, config_file):
    config = load_config(config_file)
    return config, load_std_paths(config)


def _primary(env, tmp_home: Path, leaf: str, text: str = "mine"):
    """A materialized PRIMARY box at *leaf*, with one snapshot of its vault."""
    config, std = env
    (tmp_home / leaf).mkdir(parents=True, exist_ok=True)
    proj = resolve_project(std, config, str(tmp_home / leaf), initialize=True)
    (proj.vault_rw_path / f"{leaf}.txt").write_text(text)
    assert create_snapshot(proj.vault_rw_path, box_name=proj.name) is not None
    return proj


class TestPurgeTakesTheStore:
    """Each purge verb must remove the box's own ``.versions/<box>``."""

    def test_box_rm_purge_removes_the_boxes_own_store(self, env, tmp_home):
        proj = _primary(env, tmp_home, "gonebox")
        store = _store(proj.vault_rw_path, proj.name)
        assert store.is_dir(), "precondition: the box has a store"

        _door("box", "rm", str(proj.project_path), "--purge", "--force")

        assert not store.exists(), (
            "the purged box left its snapshots behind for the next box to find"
        )

    def test_the_purge_plan_names_the_store_before_it_asks(self, env, tmp_home,
                                                        capsys):
        """The store is a LISTED step, not a silent extra deletion.

        ``box rm --purge`` itemizes every path it is about to delete; a deletion
        that is not on that list is a surprise handed to whoever typed the verb.
        """
        proj = _primary(env, tmp_home, "listedbox")
        capsys.readouterr()

        _door("box", "rm", str(proj.project_path), "--purge", "--force")

        out = capsys.readouterr().out
        assert "vault snapshots" in out, "the plan does not list the store"
        assert str(_store(proj.vault_rw_path, proj.name)) in out

    def test_box_purge_takes_a_named_members_store(self, env, tmp_home):
        """``box purge`` on a NAMED-workset member (``clean._purge_one``)."""
        from kanibako.project.workset import add_project, create_workset

        config, std = env
        ws = create_workset("purgews", tmp_home / "purgews_root", std)
        source = tmp_home / "named_src"
        source.mkdir()
        add_project(ws, "nbox", source, std)

        vault_rw = ws.vault_rw_dir / "nbox"
        vault_rw.mkdir(parents=True, exist_ok=True)
        (vault_rw / "nfile.txt").write_text("named secret\n")
        assert create_snapshot(vault_rw, box_name="nbox") is not None
        store = _store(vault_rw, "nbox")
        assert store.is_dir()

        _door("box", "purge", "nbox", "--force")

        assert not store.exists(), (
            "a named member's purge left its store under the shared base"
        )

    def test_purge_all_takes_each_boxs_own_store(self, env, tmp_home):
        """``box purge --all`` (``clean._purge_all``) covers every primary box."""
        first = _primary(env, tmp_home, "pa")
        second = _primary(env, tmp_home, "pb")

        _door("box", "purge", "--all", "--force")

        assert not _store(first.vault_rw_path, first.name).exists()
        assert not _store(second.vault_rw_path, second.name).exists()

    def test_a_standalone_purge_takes_its_composed_key_store(self, env, tmp_home):
        """A standalone keys its store on the COMPOSED name, not the directory leaf.

        Its vault arm carries no ``<box>`` leaf, so the store sits one level UP at
        ``<root>/vault/.versions/<kuid>_<dir>`` -- a different shape from the
        other two modes, and one a deleter composing ``vault/rw/.versions/<dir>``
        would miss entirely.
        """
        config, std = env
        root = tmp_home / "sabox"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
        (proj.vault_rw_path / "safile.txt").write_text("standalone secret\n")
        assert create_snapshot(proj.vault_rw_path, box_name=proj.name,
                              store_exclusive=True) is not None
        store = _store(proj.vault_rw_path, proj.name)
        assert store.is_dir(), "precondition: the standalone store exists"

        _door("box", "rm", str(root), "--purge", "--force")

        assert not store.exists(), (
            "the standalone purge left its composed-key store behind"
        )

    def test_the_next_box_with_the_name_inherits_nothing(self, env, tmp_home):
        """The brief's own probe: a NEW box of the same name must see nothing.

        Two paths, one name.  Before the fix the second box listed the first's
        snapshot and could restore it over its own vault.
        """
        config, std = env
        one = tmp_home / "one" / "reborn"
        one.mkdir(parents=True)
        first = resolve_project(std, config, str(one), initialize=True)
        (first.vault_rw_path / "dead_box_secret.txt").write_text("do not inherit\n")
        assert create_snapshot(first.vault_rw_path, box_name=first.name) is not None

        _door("box", "rm", str(first.project_path), "--purge", "--force")

        two = tmp_home / "two" / "reborn"
        two.mkdir(parents=True)
        second = resolve_project(std, config, str(two), initialize=True)
        assert second.name == first.name, "the two boxes must share the store key"
        assert not _store(second.vault_rw_path, second.name).exists(), (
            "a brand-new box inherited a dead box's snapshots"
        )


class TestPurgeNeverTouchesSharedGround:
    """⚑ CONTROLS. These pass at the base too.

    They are not detectors of item 5; they are the fence around its cure.  The
    store is removed from a SHARED base, so an over-wide delete looks identical to
    the fix on the box being purged and only differs in what it takes with it.
    """

    def test_CONTROL_the_shared_base_and_its_marker_survive(self, env, tmp_home):
        proj = _primary(env, tmp_home, "basekeep")
        base = _versions_dir(proj.vault_rw_path)
        assert (base / LAYOUT_MARKER_NAME).exists()

        _door("box", "rm", str(proj.project_path), "--purge", "--force")

        assert base.is_dir(), "the purge removed the base other boxes share"
        assert (base / LAYOUT_MARKER_NAME).exists(), (
            "the purge removed the marker that retires legacy detection"
        )

    def test_CONTROL_a_neighbours_store_survives_my_purge(self, env, tmp_home):
        mine = _primary(env, tmp_home, "minebox", "mine only\n")
        theirs = _primary(env, tmp_home, "theirbox", "theirs only\n")
        their_store = _store(theirs.vault_rw_path, theirs.name)
        before = sorted(p.name for p in their_store.iterdir())

        _door("box", "rm", str(mine.project_path), "--purge", "--force")

        assert their_store.is_dir(), "purging one box took another's store"
        assert sorted(p.name for p in their_store.iterdir()) == before

    def test_CONTROL_the_unsorted_bucket_survives_a_purge(self, env, tmp_home):
        """``.unsorted`` holds data whose owner could not be established.

        Listed, never deleted -- and certainly not deleted as a side effect of
        some other box's purge.
        """
        proj = _primary(env, tmp_home, "unsortedkeep")
        bucket = _versions_dir(proj.vault_rw_path) / UNSORTED_DIRNAME
        legacy = bucket / LEGACY_TS
        legacy.mkdir(parents=True)
        (legacy / "legacy.txt").write_text("someone else's\n")

        _door("box", "rm", str(proj.project_path), "--purge", "--force")

        assert (legacy / "legacy.txt").read_text() == "someone else's\n", (
            "a purge deleted the unattributable bucket"
        )
