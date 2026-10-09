"""What a collision suffix is allowed to CLAIM about a snapshot's owner.

A legacy flat entry has no recorded owner, so where it lands decides what the
resulting name may assert.  ``.unsorted`` is the bucket for owners that CANNOT
be proven, which means a name filed there must not name a box: the box whose
name got stamped on it was only the one that happened to run the migration.

Two of these are CONTROLS, marked as such: they pass on the reviewed tip and
exist to fence the cure, so fixing the false claim cannot also strip an
attribution that was true.
"""
from __future__ import annotations

import re
from pathlib import Path

from kanibako.snapshots import (
    UNSORTED_DIRNAME,
    _versions_dir,
    migrate_legacy_versions,
    relocate_snapshot_store,
)

TS = "20260101T000000Z"
BOX = "alpha"
#: A snapshot directory name, so a buried one can be recognized as such.
TS_RE = re.compile(r"^\d{8}T\d{6}Z$")


def _dir(path: Path, **files: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).write_text(text)
    return path


def _shared_base(tmp_path: Path) -> tuple[Path, Path]:
    """A base shared by several boxes, with one member's vault_rw and base.

    ``_versions_dir`` is the PARENT of the box's ``vault_rw``, so the shared
    base sits beside the per-box leaf, not inside it.
    """
    member = tmp_path / "primary_workset" / "vault" / "rw" / BOX
    member.mkdir(parents=True, exist_ok=True)
    (member / "f.txt").write_text("live\n")
    return member, _versions_dir(member)


def _names(bucket: Path) -> set[str]:
    return {p.name for p in bucket.iterdir() if p.is_dir()}


def _payloads(bucket: Path) -> list[str]:
    """Every payload file in the bucket, so nothing can be lost or hidden."""
    return sorted(str(p.relative_to(bucket)) for p in bucket.rglob("*") if p.is_file())


class TestUnsortedNamesNoBox:
    """The migrating box must not sign its name to data it cannot prove it owns."""

    def test_a_taken_name_in_unsorted_is_not_resolved_by_naming_the_migrant(
        self, tmp_path: Path
    ) -> None:
        """``.unsorted/<ts>-<box>`` reads as "this box's data", and is a lie.

        An earlier pass already filed ``<ts>`` here; a second flat entry with
        the same timestamp is still loose in the shared base.  Whoever runs the
        migration now is not the author of either one.
        """
        member, versions = _shared_base(tmp_path)
        bucket = versions / UNSORTED_DIRNAME
        _dir(bucket / TS, prior="someone elses\n")
        _dir(versions / TS, loose="unattributable\n")

        migrate_legacy_versions(member, box_name=BOX)

        names = _names(bucket)
        assert len(names) == 2, f"expected two entries, got {sorted(names)}"
        assert TS in names, "the entry already filed there lost its name"
        assert not [n for n in names if BOX in n], (
            f"a box name was stamped onto unattributable data: {sorted(names)}"
        )
        assert "loose" in [p.rsplit("/", 1)[-1] for p in _payloads(bucket)]

    def test_repeated_collisions_in_unsorted_keep_naming_nobody(
        self, tmp_path: Path
    ) -> None:
        """Every free slot must be found without borrowing a box's name."""
        member, versions = _shared_base(tmp_path)
        bucket = versions / UNSORTED_DIRNAME
        _dir(bucket / TS, prior="one\n")
        _dir(bucket / f"{TS}-2", prior="two\n")
        _dir(versions / TS, loose="three\n")

        migrate_legacy_versions(member, box_name=BOX)

        names = _names(bucket)
        assert len(names) == 3, f"expected three entries, got {sorted(names)}"
        assert not [n for n in names if BOX in n], (
            f"a box name was stamped onto unattributable data: {sorted(names)}"
        )
        assert sum(p.endswith("loose") for p in _payloads(bucket)) == 1


class TestNoSnapshotBuriesAnother:
    """A suffix that is already taken must not become the destination's grave."""

    def test_a_snapshot_is_never_moved_inside_another_snapshot(
        self, tmp_path: Path
    ) -> None:
        """``shutil.move`` onto an existing directory files the source INSIDE it.

        That is the same merge per-box stores exist to prevent: two snapshots
        become one directory, and the listing shows one entry holding both.
        Both the plain name AND the single-shot suffix are already taken here.
        """
        member, versions = _shared_base(tmp_path)
        bucket = versions / UNSORTED_DIRNAME
        _dir(bucket / TS, prior="one\n")
        _dir(bucket / f"{TS}-{BOX}", first="was here\n")
        _dir(versions / TS, loose="unattributable\n")

        migrate_legacy_versions(member, box_name=BOX)

        buried = [
            f"{d.name}/{c.name}" for d in bucket.iterdir() if d.is_dir()
            for c in d.iterdir() if c.is_dir() and TS_RE.match(c.name)
        ]
        assert not buried, (
            f"a snapshot was filed inside another snapshot: {buried}"
        )
        assert len(_names(bucket)) == 3, f"entries lost: {sorted(_names(bucket))}"

    def test_a_snapshot_is_never_moved_inside_another_in_a_box_store(
        self, tmp_path: Path
    ) -> None:
        """The same rule inside an attributed store, where the name is true."""
        project = tmp_path / "sabox"
        rw = project / "vault" / "rw"
        rw.mkdir(parents=True)
        (rw / "f.txt").write_text("live\n")
        versions = rw.parent / ".versions"
        own = versions / "dhnj2_sabox"
        _dir(own / TS, prior="one\n")
        _dir(own / f"{TS}-dhnj2_sabox", first="was here\n")
        _dir(versions / TS, loose="legacy\n")

        migrate_legacy_versions(rw, box_name="dhnj2_sabox", store_exclusive=True)

        buried = [
            f"{d.name}/{c.name}" for d in own.iterdir() if d.is_dir()
            for c in d.iterdir() if c.is_dir() and TS_RE.match(c.name)
        ]
        assert not buried, f"a snapshot was filed inside another snapshot: {buried}"
        assert len(_names(own)) == 3, f"entries lost: {sorted(_names(own))}"


class TestControlAttributionThatIsTrueStays:
    """Controls: the cure must not strip attribution it CAN make.

    Both pass on the reviewed tip.  They are here so a fix for the false claim
    cannot quietly become a loss of the true one.
    """

    def test_CONTROL_an_exclusive_collision_is_still_named_after_its_owner(
        self, tmp_path: Path
    ) -> None:
        """A base inside this box's own tree proves the box wrote the entry."""
        project = tmp_path / "sabox"
        rw = project / "vault" / "rw"
        rw.mkdir(parents=True)
        (rw / "f.txt").write_text("live\n")
        own = rw.parent / ".versions" / "dhnj2_sabox"
        _dir(own / TS, first="mine\n")
        _dir(rw.parent / ".versions" / TS, legacy="also mine\n")

        moved = migrate_legacy_versions(
            rw, box_name="dhnj2_sabox", store_exclusive=True
        )

        assert moved["attributed"] == [TS]
        assert (own / TS / "first").read_text() == "mine\n"
        assert (own / f"{TS}-dhnj2_sabox" / "legacy").exists(), (
            "true attribution was removed along with the false claim"
        )

    def test_CONTROL_a_relocate_merge_names_the_displaced_entry_its_real_source(
        self, tmp_path: Path
    ) -> None:
        """A merge knows which store the entry came from, so it may say so.

        Deliberately written with only the arguments the reviewed tip already
        takes, so a pass here is BEHAVIOR and not a guard on a new parameter.
        """
        old_vault = tmp_path / "w2" / "vault" / "rw"
        new_vault = tmp_path / "w" / "vault" / "rw"
        old_vault.mkdir(parents=True)
        # The moving box's own store, in its own OLD base.
        _dir(_versions_dir(old_vault) / "oldie" / TS, carried="from oldie\n")
        # The new box's own store already holds that timestamp.
        landed = _versions_dir(new_vault) / "newie"
        _dir(landed / TS, already="mine\n")

        relocate_snapshot_store(
            old_vault, new_vault, old_box="oldie", new_box="newie",
        )

        assert (landed / TS / "already").read_text() == "mine\n"
        moved_in = [
            d for d in landed.iterdir()
            if d.is_dir() and d.name != TS and (d / "carried").exists()
        ]
        assert len(moved_in) == 1, (
            f"carried entry not merged: {sorted(p.name for p in landed.iterdir())}"
        )
        assert moved_in[0].name == f"{TS}-oldie", (
            f"the merge stopped naming the store it came from: {moved_in[0].name}"
        )
