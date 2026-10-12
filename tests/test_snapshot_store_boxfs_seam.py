"""Snapshot-store moves go through the ``boxfs`` seam: a planted link is refused.

``relocate_snapshot_store``, ``adopt_standalone_store``, and the legacy-layout pass
move stores in a vault tree.  Through ``shutil`` they followed a symlink planted in a
parent (``.versions`` itself), and moved data in or out of whatever it pointed at.
"""
from __future__ import annotations

import errno
from pathlib import Path

import pytest

from kanibako import boxfs, kuid
from kanibako.snapshots import (
    UNSORTED_DIRNAME,
    _versions_dir,
    adopt_standalone_store,
    migrate_legacy_versions,
    relocate_snapshot_store,
)

TS = "20240101T000000Z"


def _rw(root: Path) -> Path:
    rw = root / "vault" / "rw"
    rw.mkdir(parents=True)
    return rw


def _snap(store: Path, name: str = TS, text: str = "mine") -> Path:
    (store / name).mkdir(parents=True)
    (store / name / "f").write_text(text)
    return store / name


def _link_versions(rw: Path, outside: Path) -> None:
    """Plant ``.versions`` as a link to *outside*."""
    outside.mkdir(parents=True, exist_ok=True)
    _versions_dir(rw).symlink_to(outside, target_is_directory=True)


class TestAPlantedLinkIsRefused:

    def test_relocate_refuses_a_linked_old_base(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        outside = tmp_path / "outside"
        _snap(outside / "a")
        _link_versions(old, outside)
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b")
        assert exc.value.errno == errno.ELOOP
        assert (outside / "a" / TS / "f").read_text() == "mine"
        assert not (_versions_dir(new) / "b").exists()

    def test_relocate_merge_refuses_a_linked_old_base(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        outside = tmp_path / "outside"
        _snap(outside / "a")
        _link_versions(old, outside)
        _snap(_versions_dir(new) / "b", "20230101T000000Z", "theirs")
        with pytest.raises(OSError) as exc:
            relocate_snapshot_store(old, new, old_box="a", new_box="b")
        assert exc.value.errno == errno.ELOOP
        assert (outside / "a" / TS / "f").read_text() == "mine"
        assert sorted(p.name for p in (_versions_dir(new) / "b").iterdir()) == [
            "20230101T000000Z"]

    def test_adopt_refuses_a_linked_base(self, tmp_path):
        k = kuid.generate()
        rw = _rw(tmp_path / "s")
        outside = tmp_path / "outside"
        _snap(outside / f"{k}_old")
        _link_versions(rw, outside)
        with pytest.raises(OSError) as exc:
            adopt_standalone_store(rw, box_name=f"{k}_new")
        assert exc.value.errno == errno.ELOOP
        assert (outside / f"{k}_old" / TS / "f").read_text() == "mine"
        assert not (outside / f"{k}_new").exists()

    def test_legacy_pass_refuses_a_linked_base(self, tmp_path):
        rw = _rw(tmp_path / "w")
        outside = tmp_path / "outside"
        _snap(outside)
        _link_versions(rw, outside)
        with pytest.raises(OSError) as exc:
            migrate_legacy_versions(rw, box_name="b")
        assert exc.value.errno == errno.ELOOP
        assert (outside / TS / "f").read_text() == "mine"
        assert not (outside / UNSORTED_DIRNAME / TS).exists()


class TestTheMoveIsOtherwiseUnchanged:

    def test_relocate_across_trees_moves_the_store(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        assert relocate_snapshot_store(old, new, old_box="a", new_box="b") == \
            _versions_dir(new) / "b"
        assert (_versions_dir(new) / "b" / TS / "f").read_text() == "mine"
        assert not (_versions_dir(old) / "a").exists()

    def test_a_cross_device_move_copies_links_verbatim(self, tmp_path, monkeypatch):
        """EXDEV: a no-follow copy, then the source goes; a link inside stays a link."""
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        snap = _snap(_versions_dir(old) / "a")
        (snap / "ln").symlink_to("/etc/passwd")

        def exdev(*a, **kw):
            raise OSError(errno.EXDEV, "cross-device")

        monkeypatch.setattr(boxfs, "rename", exdev)
        relocate_snapshot_store(old, new, old_box="a", new_box="b")
        moved = _versions_dir(new) / "b" / TS
        assert (moved / "f").read_text() == "mine"
        assert (moved / "ln").is_symlink() and str((moved / "ln").readlink()) == "/etc/passwd"
        assert not (_versions_dir(old) / "a").exists()

    def test_merge_keeps_both_and_names_the_owner(self, tmp_path):
        old, new = _rw(tmp_path / "old"), _rw(tmp_path / "new")
        _snap(_versions_dir(old) / "a")
        _snap(_versions_dir(new) / "b", TS, "theirs")
        relocate_snapshot_store(old, new, old_box="a", new_box="b")
        store = _versions_dir(new) / "b"
        assert (store / TS / "f").read_text() == "theirs"
        assert (store / f"{TS}-a" / "f").read_text() == "mine"
        assert not (_versions_dir(old) / "a").exists()

    def test_adopt_moves_the_renamed_store(self, tmp_path):
        k = kuid.generate()
        rw = _rw(tmp_path / "s")
        _snap(_versions_dir(rw) / f"{k}_old")
        assert adopt_standalone_store(rw, box_name=f"{k}_new") == \
            _versions_dir(rw) / f"{k}_new"
        assert (_versions_dir(rw) / f"{k}_new" / TS / "f").read_text() == "mine"
