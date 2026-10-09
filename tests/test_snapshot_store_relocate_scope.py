"""Scope tests for the snapshot-store relocation seam.

Each test pins a boundary that ``relocate_snapshot_store`` crossed in review r2:
whose store is safe to adopt, when a store must be carried, and what a base must
look like before it is declared per-box.  All three are written against the
BEHAVIOR, not the signature, so they fail on the reviewed tip rather than on
an ImportError.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.snapshots import (
    LAYOUT_MARKER_NAME,
    UNSORTED_DIRNAME,
    _versions_dir,
    list_snapshots,
    migrate_legacy_versions,
    relocate_snapshot_store,
)

LEGACY_TS = "20200101T000000Z"


def _vault(root: Path, share: str) -> Path:
    """A vault share-rw path for the *share* under *root*."""
    path = root / share / "vault" / "rw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _store(vault_rw: Path, box: str) -> Path:
    return _versions_dir(vault_rw) / box


def _make_snapshot(vault_rw: Path, box: str, ts: str, marker: str) -> Path:
    store = _store(vault_rw, box) / ts
    store.mkdir(parents=True, exist_ok=True)
    (store / marker).write_text(f"content of {marker}\n")
    return store


class TestSharedBaseOldNameIsNotOurs:
    """Item 1: a shared base's ``<old name>`` belongs to whoever already has it."""

    def test_a_convert_into_a_shared_base_leaves_the_other_box_alone(self, tmp_path):
        """No ``new_base_exclusive`` passed -- a shared base is the DEFAULT case.

        ⚑ Deliberately written without the new keyword so it fails at the
        reviewed tip on BEHAVIOR, not on a missing argument: the old code
        consulted ``new_base/<old name>`` unconditionally and moved the
        stranger's store out from under them.
        """
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")

        # The moving box's own store, in its own old base.
        _make_snapshot(old_vault, "projA", "20240101T000000Z", "mine.txt")

        # A DIFFERENT box already named projA lives in the destination workset.
        theirs = _make_snapshot(new_vault, "projA", "20230101T000000Z", "theirs.txt")
        theirs_hash = (theirs / "theirs.txt").read_text()

        relocate_snapshot_store(
            old_vault, new_vault, old_box="projA", new_box="projC",
        )

        # The other box still owns its store, byte for byte, under its own name.
        assert theirs.exists(), "another box's store was moved out from under it"
        assert (theirs / "theirs.txt").read_text() == theirs_hash
        assert _store(new_vault, "projA").exists()

        # The mover's data came from the OLD base, not from the stranger.
        landed = _store(new_vault, "projC")
        assert (landed / "20240101T000000Z" / "mine.txt").exists()
        assert not (landed / "20230101T000000Z").exists(), (
            "the mover picked up the other box's snapshot"
        )

    def test_an_own_tree_may_still_carry_the_old_key(self, tmp_path):
        """The exclusive case is unchanged: a copied standalone tree is ours."""
        old_vault = _vault(tmp_path, "sabox_old")
        new_vault = _vault(tmp_path, "sabox_new")
        _make_snapshot(old_vault, "k1_oldleaf", "20240101T000000Z", "mine.txt")
        # A copy of the tree already carried the store into the new base.
        carried = _make_snapshot(
            new_vault, "k1_oldleaf", "20240102T000000Z", "copied.txt"
        )

        relocate_snapshot_store(
            old_vault, new_vault,
            old_box="k1_oldleaf", new_box="k1_newleaf", new_base_exclusive=True,
        )

        landed = _store(new_vault, "k1_newleaf")
        assert (landed / "20240102T000000Z" / "copied.txt").exists()
        assert not carried.exists()


class TestBaseChangeCarriesTheStore:
    """Item 2: the BASE changing moves the store even when the name does not."""

    def test_a_convert_that_keeps_the_name_still_moves_the_store(self, tmp_path):
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")
        snap = _make_snapshot(old_vault, "projB", "20240101T000000Z", "data.txt")

        relocate_snapshot_store(
            old_vault, new_vault, old_box="projB", new_box="projB",
        )

        landed = _store(new_vault, "projB") / "20240101T000000Z" / "data.txt"
        assert landed.exists(), (
            "a base change that kept the name left the store stranded in the "
            "old base -- MIGRATION promises it moves"
        )
        assert landed.read_text() == "content of data.txt\n"
        assert not snap.exists()

    def test_a_kept_name_does_not_clobber_a_store_already_there(self, tmp_path):
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")
        _make_snapshot(old_vault, "projB", "20240101T000000Z", "mine.txt")
        existing = _make_snapshot(
            new_vault, "projB", "20240101T000000Z", "already.txt"
        )

        relocate_snapshot_store(
            old_vault, new_vault, old_box="projB", new_box="projB",
        )

        # Same timestamp, two owners: nothing is overwritten.
        assert (existing / "already.txt").read_text() == "content of already.txt\n"
        landed_root = _store(new_vault, "projB")
        names = {p.name for p in landed_root.iterdir() if p.is_dir()}
        assert len(names) == 2, f"expected two entries, got {names}"


class TestSafetyCopyFailureIsNotATraceback:
    """Item 4: the hardlink fallback raises OSError, and that path must be caught.

    The reflink path shells out and raises ``CalledProcessError``; the HARDLINK
    path -- what ``detect_snapshot_strategy`` picks on ext4, NFS and tmpfs, i.e.
    most hosts -- falls back to ``copy_tree_keeping_links``, which raises
    ``PermissionError``/``OSError`` straight out of the copy.  A restore into an
    unwritable store must report a failed safety copy and change nothing, not
    die with a traceback.
    """

    def test_an_unwritable_store_reports_a_failed_safety_copy(self, tmp_path,
                                                          monkeypatch):
        from kanibako import snapshots
        from kanibako.snapshots import (
            SnapshotSafetyError,
            create_snapshot,
            restore_snapshot,
        )

        vault = _vault(tmp_path, "w")
        (vault / "live.txt").write_text("do not lose me\n")
        snap = create_snapshot(vault, box_name="boxy")
        assert snap is not None

        # What the hardlink fallback does when the store is not writable.
        def unwritable(*_a, **_k):
            raise PermissionError(13, "Permission denied: '.versions'")

        monkeypatch.setattr(snapshots, "create_snapshot", unwritable)

        with pytest.raises(SnapshotSafetyError) as caught:
            restore_snapshot(vault, snap.name, box_name="boxy")

        assert "Nothing was changed" in str(caught.value)
        # The live vault is untouched -- the restore never started.
        assert (vault / "live.txt").read_text() == "do not lose me\n"

    def test_an_oserror_from_the_copy_is_not_left_to_escape(self, tmp_path,
                                                        monkeypatch):
        """A bare OSError is not a CalledProcessError; it must not escape raw."""
        from kanibako import snapshots
        from kanibako.snapshots import SnapshotSafetyError, restore_snapshot

        vault = _vault(tmp_path, "w2")
        (vault / "live.txt").write_text("keep\n")
        snap = snapshots.create_snapshot(vault, box_name="boxy")
        assert snap is not None

        monkeypatch.setattr(
            snapshots, "create_snapshot",
            lambda *_a, **_k: (_ for _ in ()).throw(
                OSError(30, "Read-only file system")
            ),
        )

        with pytest.raises(SnapshotSafetyError):
            restore_snapshot(vault, snap.name, box_name="boxy")
        assert (vault / "live.txt").read_text() == "keep\n"

class TestMarkingMigratesFirst:
    """Item 3: a base is never declared per-box while it still holds flat entries."""

    def test_a_relocate_migrates_legacy_before_marking_the_base(self, tmp_path):
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")
        _make_snapshot(old_vault, "mover", "20240101T000000Z", "mine.txt")

        # The destination base still holds a legacy FLAT entry and no marker.
        new_base = _versions_dir(new_vault)
        new_base.mkdir(parents=True, exist_ok=True)
        legacy = new_base / LEGACY_TS
        legacy.mkdir()
        (legacy / "old.txt").write_text("legacy payload\n")

        relocate_snapshot_store(
            old_vault, new_vault, old_box="mover", new_box="mover2",
        )

        assert (new_base / LAYOUT_MARKER_NAME).exists()
        assert not legacy.exists(), (
            "the base was marked per-box while its legacy flat entry was still "
            "there: hidden from every listing, and the next box named like the "
            "timestamp prunes it for good"
        )
        assert (new_base / UNSORTED_DIRNAME / LEGACY_TS / "old.txt").exists()

    def test_a_relocate_into_an_absent_base_marks_the_base_it_makes(self, tmp_path):
        """An unmarked new base lets the next reader sweep a timestamp-named store."""
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")
        _make_snapshot(old_vault, "mover", "20240101T000000Z", "mine.txt")
        new_base = _versions_dir(new_vault)
        assert not new_base.exists()

        relocate_snapshot_store(
            old_vault, new_vault, old_box="mover", new_box=LEGACY_TS,
        )
        migrate_legacy_versions(new_vault, box_name="other")

        assert (new_base / LAYOUT_MARKER_NAME).exists()
        assert [n for n, _, _ in list_snapshots(new_vault, box_name=LEGACY_TS)] \
            == ["20240101T000000Z"]
        assert not (new_base / UNSORTED_DIRNAME).exists()

    def test_a_legacy_entry_survives_the_mark_as_unsorted(self, tmp_path):
        """Migrated means filed away, not deleted -- it must still be readable."""
        old_vault = _vault(tmp_path, "w2")
        new_vault = _vault(tmp_path, "w")
        _make_snapshot(old_vault, "mover", "20240101T000000Z", "mine.txt")
        new_base = _versions_dir(new_vault)
        new_base.mkdir(parents=True, exist_ok=True)
        (new_base / LEGACY_TS).mkdir()
        (new_base / LEGACY_TS / "old.txt").write_text("legacy payload\n")

        relocate_snapshot_store(
            old_vault, new_vault, old_box="mover", new_box="mover2",
        )

        kept = new_base / UNSORTED_DIRNAME / LEGACY_TS / "old.txt"
        assert kept.read_text() == "legacy payload\n"
