"""A named member's purge takes its OWN vault leaves; a disconnect takes its STORE.

Two rows carried forward from the snapshotshare r2 review as deliberate
non-widenings, both MEASURED on the base before being fixed:

* ``box purge`` on a NAMED-workset member printed ``done`` while
  ``<ws>/vault/ro/<box>`` and ``<ws>/vault/rw/<box>`` both stayed on disk -- the
  per-box vault removal in ``clean._purge_one`` was gated on ``BoxMode.primary``,
  so no named member ever reached it.
* ``workset disconnect --remove-files`` removed the box tree and the vault leaves
  but left ``.versions/<box>`` behind, so the snapshots outlived every record of
  the box that made them.

⚑ THE SURVIVORS ARE PINNED AS HARD AS THE DELETIONS.  ``vault/{ro,rw}`` under a
workset is a BASE shared by every member, and ``.versions`` likewise; a purge that
reached a neighbor's leaf or the base would turn one box's cleanup into every
other box's data loss.  Those cases are controls: they pass at the base and are
here to catch the fix over-reaching, not to prove the defect.

⚑ WHY THE STORE FIX IS AT THE DISCONNECT CALLER AND NOT IN ``remove_member_store``.
That function has two callers, and the relocation one (``_to_workset`` ->
``_retire_old_store``) runs at STEP 4a while the carry
(``_relocate_snapshot_store``) is STEP 4c -- retire BEFORE carry.  Deleting the
store inside the shared function would destroy the only copy before the carry
could move it.  ``test_disconnect_without_remove_files_leaves_the_store`` fences
the other half: a plain disconnect must not touch it either.

⚑ EVERY CASE GOES THROUGH A DOOR THAT EXISTS AT THE BASE (``cli.build_parser``
plus ``args.func``), so a base run fails on an ASSERTION about behavior rather
than on an import of a name this change introduced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import load_std_paths
from kanibako.snapshots import LAYOUT_MARKER_NAME, _versions_dir, create_snapshot


def _door(*argv: str) -> int:
    """A real verb, through the production parser."""
    from kanibako import cli

    args = cli.build_parser().parse_args(list(argv))
    return int(args.func(args) or 0)


def _store(vault_rw_leaf: Path, box: str) -> Path:
    """*box*'s own store under the base its vault leaf shares."""
    return _versions_dir(vault_rw_leaf) / box


@pytest.fixture
def env(tmp_home, config_file):
    config = load_config(config_file)
    return config, load_std_paths(config)


def _named_pair(env, tmp_home: Path, wsname: str = "plws"):
    """A workset holding two NAMED members, each with vault data + one snapshot.

    Returns ``(ws, {box: (ro_leaf, rw_leaf, store)})``.  Both members are built the
    same way so a neighbor is a real peer, not a stand-in.
    """
    from kanibako.project.workset import add_project, create_workset

    config, std = env
    ws = create_workset(wsname, tmp_home / f"{wsname}_root", std)
    made = {}
    for box in ("alpha", "neighbour"):
        source = tmp_home / f"{box}_src"
        source.mkdir()
        add_project(ws, box, source, std)
        ro = ws.vault_ro_dir / box
        rw = ws.vault_rw_dir / box
        ro.mkdir(parents=True, exist_ok=True)
        rw.mkdir(parents=True, exist_ok=True)
        (ro / "r.txt").write_text(f"RO-{box}\n")
        (rw / "w.txt").write_text(f"RW-{box}\n")
        assert create_snapshot(rw, box_name=box) is not None
        made[box] = (ro, rw, _store(rw, box))
    return ws, made


class TestNamedPurgeTakesOwnVaultLeaves:
    """Row 1: ``box purge`` on a NAMED member must remove its own vault leaves."""

    def test_named_purge_removes_both_vault_leaves(self, env, tmp_home):
        """RED at the base: both leaves survived a purge that reported ``done``."""
        _ws, made = _named_pair(env, tmp_home)
        ro, rw, _store_path = made["alpha"]

        assert _door("box", "purge", "alpha", "--force") == 0
        assert not ro.exists(), (
            f"named-member purge left the box's own vault ro leaf: {ro}"
        )
        assert not rw.exists(), (
            f"named-member purge left the box's own vault rw leaf: {rw}"
        )

    def test_named_purge_leaves_a_neighbours_vault_intact(self, env, tmp_home):
        """CONTROL: purging one member must not reach another's leaves."""
        _ws, made = _named_pair(env, tmp_home)
        n_ro, n_rw, _ = made["neighbour"]

        _door("box", "purge", "alpha", "--force")

        assert (n_ro / "r.txt").read_text() == "RO-neighbour\n"
        assert (n_rw / "w.txt").read_text() == "RW-neighbour\n"

    def test_named_purge_leaves_the_shared_versions_base(self, env, tmp_home):
        """CONTROL: the ``.versions`` base and its marker are not the box's to take."""
        _ws, made = _named_pair(env, tmp_home)
        versions = _versions_dir(made["alpha"][1])

        _door("box", "purge", "alpha", "--force")

        assert versions.is_dir(), "the shared .versions base was removed"
        assert (versions / LAYOUT_MARKER_NAME).exists(), (
            "the layout marker that retires legacy detection was removed"
        )

    def test_named_purge_of_a_linked_vault_leaf_takes_only_the_link(
        self, env, tmp_home
    ):
        """A vault leaf that is a LINK loses the link; its target is never followed."""
        import shutil

        _ws, made = _named_pair(env, tmp_home)
        _ro, rw, _ = made["alpha"]
        outside = tmp_home / "outside_target"
        outside.mkdir()
        (outside / "keep.txt").write_text("OUTSIDE\n")
        shutil.rmtree(rw)
        rw.symlink_to(outside)

        _door("box", "purge", "alpha", "--force")

        assert not rw.exists(), "the linked vault leaf survived"
        assert (outside / "keep.txt").read_text() == "OUTSIDE\n", (
            "the purge wrote through the link into the user's target"
        )


class TestDisconnectTakesTheMembersStore:
    """Row 2: ``disconnect --remove-files`` must take ``.versions/<box>``."""

    def test_disconnect_remove_files_takes_the_store(self, env, tmp_home):
        """RED at the base: the store outlived every record of its box."""
        ws, made = _named_pair(env, tmp_home)
        store = made["alpha"][2]
        assert store.is_dir()

        assert _door("workset", "disconnect", ws.name, "alpha",
                    "--remove-files", "--force") == 0
        assert not store.exists(), (
            f"disconnect --remove-files left the member's snapshot store: {store}"
        )

    def test_disconnect_remove_files_leaves_a_neighbours_store(self, env, tmp_home):
        """CONTROL: the neighbor's store and its listing survive."""
        ws, made = _named_pair(env, tmp_home)
        n_store = made["neighbour"][2]

        _door("workset", "disconnect", ws.name, "alpha", "--remove-files", "--force")

        assert n_store.is_dir(), "the disconnect took a neighbour's store"

    def test_disconnect_without_remove_files_leaves_the_store(self, env, tmp_home):
        """CONTROL: a plain disconnect removes no files, store included."""
        ws, made = _named_pair(env, tmp_home)
        store = made["alpha"][2]
        ro = made["alpha"][0]

        assert _door("workset", "disconnect", ws.name, "alpha", "--force") == 0
        assert store.is_dir(), "a plain disconnect removed the snapshot store"
        assert ro.is_dir(), "a plain disconnect removed the vault leaf"
