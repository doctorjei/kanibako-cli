"""Tests for kanibako.snapshots: vault share-rw snapshot engine."""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.snapshots import (
    _test_reflink,
    auto_snapshot,
    create_snapshot,
    detect_snapshot_strategy,
    list_snapshots,
    prune_snapshots,
    restore_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _populate_rw(vault_rw: Path) -> None:
    """Put some files into share-rw for snapshot tests."""
    vault_rw.mkdir(parents=True, exist_ok=True)
    (vault_rw / "file1.txt").write_text("hello")
    sub = vault_rw / "subdir"
    sub.mkdir()
    (sub / "file2.txt").write_text("world")


def _make_dir_snapshot(versions: Path, name: str, vault_rw: Path) -> Path:
    """Create a directory snapshot manually for testing."""
    snap_dir = versions / name
    snap_dir.mkdir(parents=True, exist_ok=True)
    for item in vault_rw.iterdir():
        dest = snap_dir / item.name
        if item.is_dir():
            import shutil
            shutil.copytree(item, dest)
        else:
            import shutil
            shutil.copy2(item, dest)
    return snap_dir


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------


class TestCreateSnapshot:
    def test_creates_archive(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw)

        assert result is not None
        assert result.exists()
        assert result.name.endswith(".tar.xz")
        assert result.parent.name == ".versions"

    def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        assert create_snapshot(vault_rw) is None

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"

        assert create_snapshot(vault_rw) is None

    def test_archive_contains_files(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw)
        with tarfile.open(result, "r:xz") as tar:
            names = tar.getnames()
            assert "file1.txt" in names
            assert "subdir/file2.txt" in names

    def test_create_snapshot_tarxz_explicit(self, tmp_path: Path) -> None:
        """Explicit strategy='tarxz' produces a tar.xz archive."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw, strategy="tarxz")

        assert result is not None
        assert result.name.endswith(".tar.xz")
        assert result.is_file()
        with tarfile.open(result, "r:xz") as tar:
            names = tar.getnames()
            assert "file1.txt" in names

    def test_create_snapshot_hardlink(self, tmp_path: Path) -> None:
        """strategy='hardlink' produces a directory snapshot."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw, strategy="hardlink")

        assert result is not None
        assert result.is_dir()
        assert (result / "file1.txt").read_text() == "hello"
        assert (result / "subdir" / "file2.txt").read_text() == "world"
        assert result.parent.name == ".versions"

    def test_create_snapshot_hardlink_link_dest(self, tmp_path: Path) -> None:
        """Second hardlink snapshot can use --link-dest from the first."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        _populate_rw(vault_rw)

        # Create the first snapshot manually with a known timestamp.
        first = create_snapshot(vault_rw, strategy="hardlink")
        assert first is not None

        # Rename to a distinct timestamp so the second doesn't collide.
        first_renamed = versions / "20260101T000000Z"
        first.rename(first_renamed)
        first = first_renamed

        # Modify a file so the second snapshot differs.
        (vault_rw / "file1.txt").write_text("changed")
        second = create_snapshot(vault_rw, strategy="hardlink")
        assert second is not None
        assert second != first
        assert (second / "file1.txt").read_text() == "changed"
        # Original snapshot is untouched.
        assert (first / "file1.txt").read_text() == "hello"


# ---------------------------------------------------------------------------
# detect_snapshot_strategy
# ---------------------------------------------------------------------------


class TestDetectSnapshotStrategy:
    def test_reflink_returns_false_on_tmpfs(self, tmp_path: Path) -> None:
        """_test_reflink returns False on typical test filesystems (tmpfs)."""
        # Most CI / tmpfs filesystems do not support reflinks.
        # This test ensures the probe does not crash.
        result = _test_reflink(tmp_path)
        # We can't guarantee the result, but it should be a bool.
        assert isinstance(result, bool)

    def test_reflink_returns_false_for_missing_dir(self) -> None:
        assert _test_reflink(Path("/nonexistent/path")) is False

    def test_detect_defaults_to_hardlink(self, tmp_path: Path) -> None:
        """On filesystems without reflink support, returns 'hardlink'."""
        # Patch _test_reflink to always return False.
        with patch("kanibako.snapshots._test_reflink", return_value=False):
            assert detect_snapshot_strategy(tmp_path) == "hardlink"

    def test_detect_returns_reflink_when_supported(self, tmp_path: Path) -> None:
        """When reflink is supported, returns 'reflink'."""
        with patch("kanibako.snapshots._test_reflink", return_value=True):
            assert detect_snapshot_strategy(tmp_path) == "reflink"


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------


class TestListSnapshots:
    def test_lists_snapshots(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        create_snapshot(vault_rw)
        snaps = list_snapshots(vault_rw)

        assert len(snaps) == 1
        name, ts, size = snaps[0]
        assert name.endswith(".tar.xz")
        assert "UTC" in ts
        assert size > 0

    def test_empty_when_no_versions(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        assert list_snapshots(vault_rw) == []

    def test_sorted_by_time(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # Manually create two snapshots with different timestamps.
        import tarfile
        for name in ("20260101T000000Z.tar.xz", "20260201T000000Z.tar.xz"):
            with tarfile.open(versions / name, "w:xz") as tar:
                tar.add(str(vault_rw / "file1.txt"), arcname="file1.txt")

        snaps = list_snapshots(vault_rw)
        assert len(snaps) == 2
        assert snaps[0][0] == "20260101T000000Z.tar.xz"
        assert snaps[1][0] == "20260201T000000Z.tar.xz"

    def test_lists_directory_snapshots(self, tmp_path: Path) -> None:
        """Directory snapshots are listed with computed size."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw, strategy="hardlink")
        assert result is not None

        snaps = list_snapshots(vault_rw)
        assert len(snaps) == 1
        name, ts, size = snaps[0]
        assert not name.endswith(".tar.xz")
        assert "UTC" in ts
        assert size > 0

    def test_list_snapshots_mixed(self, tmp_path: Path) -> None:
        """Both directory and tar.xz snapshots are listed together."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # Create a tar.xz snapshot manually with an earlier timestamp.
        with tarfile.open(versions / "20260101T000000Z.tar.xz", "w:xz") as tar:
            tar.add(str(vault_rw / "file1.txt"), arcname="file1.txt")

        # Create a directory snapshot with a later timestamp.
        _make_dir_snapshot(versions, "20260201T000000Z", vault_rw)

        snaps = list_snapshots(vault_rw)
        assert len(snaps) == 2
        # tar.xz first (older).
        assert snaps[0][0] == "20260101T000000Z.tar.xz"
        # directory second (newer).
        assert snaps[1][0] == "20260201T000000Z"


# ---------------------------------------------------------------------------
# restore_snapshot
# ---------------------------------------------------------------------------


class TestRestoreSnapshot:
    def test_restores_contents(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        snap = create_snapshot(vault_rw)

        # Modify share-rw.
        (vault_rw / "file1.txt").write_text("modified")
        (vault_rw / "new_file.txt").write_text("should disappear")

        restore_snapshot(vault_rw, snap.name)

        assert (vault_rw / "file1.txt").read_text() == "hello"
        assert (vault_rw / "subdir" / "file2.txt").read_text() == "world"
        assert not (vault_rw / "new_file.txt").exists()

    def test_raises_on_missing_snapshot(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match="Snapshot not found"):
            restore_snapshot(vault_rw, "nonexistent.tar.xz")

    def test_restore_from_directory_snapshot(self, tmp_path: Path) -> None:
        """Restore from a directory snapshot (hardlink/reflink)."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        snap = create_snapshot(vault_rw, strategy="hardlink")
        assert snap is not None

        # Modify share-rw.
        (vault_rw / "file1.txt").write_text("modified")
        (vault_rw / "new_file.txt").write_text("should disappear")

        restore_snapshot(vault_rw, snap.name)

        assert (vault_rw / "file1.txt").read_text() == "hello"
        assert (vault_rw / "subdir" / "file2.txt").read_text() == "world"
        assert not (vault_rw / "new_file.txt").exists()

    def test_raises_on_nonexistent_directory_snapshot(self, tmp_path: Path) -> None:
        """Raises FileNotFoundError for a missing directory snapshot."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        vault_rw.mkdir(parents=True)

        with pytest.raises(FileNotFoundError, match="Snapshot not found"):
            restore_snapshot(vault_rw, "20260101T000000Z")


# ---------------------------------------------------------------------------
# prune_snapshots
# ---------------------------------------------------------------------------


class TestPruneSnapshots:
    def test_prunes_old_snapshots(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # Create 7 snapshots manually.
        for i in range(7):
            name = f"2026010{i + 1}T000000Z.tar.xz"
            with tarfile.open(versions / name, "w:xz") as tar:
                tar.add(str(vault_rw / "file1.txt"), arcname="file1.txt")

        removed = prune_snapshots(vault_rw, max_keep=3)

        assert removed == 4
        remaining = list(versions.iterdir())
        assert len(remaining) == 3

    def test_no_prune_when_under_limit(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)
        create_snapshot(vault_rw)

        removed = prune_snapshots(vault_rw, max_keep=5)
        assert removed == 0

    def test_no_prune_when_no_versions(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        removed = prune_snapshots(vault_rw, max_keep=5)
        assert removed == 0

    def test_prune_directory_snapshots(self, tmp_path: Path) -> None:
        """Prune handles directory snapshots correctly."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # Create 5 directory snapshots manually.
        for i in range(5):
            _make_dir_snapshot(versions, f"2026010{i + 1}T000000Z", vault_rw)

        removed = prune_snapshots(vault_rw, max_keep=2)

        assert removed == 3
        remaining = sorted(d.name for d in versions.iterdir())
        assert len(remaining) == 2
        # Kept the two newest.
        assert remaining == ["20260104T000000Z", "20260105T000000Z"]

    def test_prune_mixed_snapshots(self, tmp_path: Path) -> None:
        """Prune handles a mix of tar.xz and directory snapshots."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # tar.xz (oldest).
        with tarfile.open(versions / "20260101T000000Z.tar.xz", "w:xz") as tar:
            tar.add(str(vault_rw / "file1.txt"), arcname="file1.txt")

        # Directory snapshots (newer).
        _make_dir_snapshot(versions, "20260102T000000Z", vault_rw)
        _make_dir_snapshot(versions, "20260103T000000Z", vault_rw)

        removed = prune_snapshots(vault_rw, max_keep=2)

        assert removed == 1
        remaining = sorted(e.name for e in versions.iterdir())
        assert "20260101T000000Z.tar.xz" not in remaining
        assert "20260102T000000Z" in remaining
        assert "20260103T000000Z" in remaining


# ---------------------------------------------------------------------------
# auto_snapshot
# ---------------------------------------------------------------------------


class TestAutoSnapshot:
    def test_creates_and_prunes(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = auto_snapshot(vault_rw, max_keep=2)
        assert result is not None
        assert result.exists()

    def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        assert auto_snapshot(vault_rw) is None

    def test_auto_snapshot_with_strategy(self, tmp_path: Path) -> None:
        """auto_snapshot accepts and passes through the strategy parameter."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = auto_snapshot(vault_rw, strategy="hardlink", max_keep=3)
        assert result is not None
        assert result.is_dir()
        assert (result / "file1.txt").read_text() == "hello"

    def test_auto_snapshot_with_tarxz_strategy(self, tmp_path: Path) -> None:
        """auto_snapshot with strategy='tarxz' creates tar.xz archive."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = auto_snapshot(vault_rw, strategy="tarxz", max_keep=3)
        assert result is not None
        assert result.is_file()
        assert result.name.endswith(".tar.xz")
