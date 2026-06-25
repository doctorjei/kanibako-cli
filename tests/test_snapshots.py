"""Tests for kanibako.snapshots: vault share-rw snapshot engine."""

from __future__ import annotations

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
    def test_creates_directory_snapshot(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw)

        assert result is not None
        assert result.exists()
        assert result.is_dir()
        assert result.parent.name == ".versions"

    def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        vault_rw.mkdir(parents=True)

        assert create_snapshot(vault_rw) is None

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"

        assert create_snapshot(vault_rw) is None

    def test_snapshot_contains_files(self, tmp_path: Path) -> None:
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        result = create_snapshot(vault_rw)
        assert result is not None
        assert (result / "file1.txt").read_text() == "hello"
        assert (result / "subdir" / "file2.txt").read_text() == "world"

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
# Fallback / degrade path (coverage gap #3)
#
# The snapshot engine degrades gracefully so a full snapshot is still produced
# on filesystems / hosts that lack the faster primitive:
#
#   reflink  -- chosen only when detect_snapshot_strategy's probe succeeds;
#               on a no-reflink fs (e.g. ext4) detection returns "hardlink"
#               so the reflink path is never executed (it has no in-path
#               fallback -- a forced reflink failure raises).
#   hardlink -- ``rsync --link-dest``; if rsync is missing or fails the
#               helper falls back to ``shutil.copytree`` (a full copy).
# ---------------------------------------------------------------------------


def _assert_full_snapshot(snap: Path | None, vault_rw: Path) -> None:
    """Assert *snap* is a complete copy of *vault_rw* (file set + contents)."""
    assert snap is not None
    assert snap.is_dir()

    def rel_files(root: Path) -> dict[str, str]:
        return {
            str(f.relative_to(root)): f.read_text()
            for f in root.rglob("*")
            if f.is_file()
        }

    assert rel_files(snap) == rel_files(vault_rw)


class TestSnapshotFallbackChain:
    def test_hardlink_rsync_failure_falls_back_to_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rsync returning non-zero -> copytree fallback -> full snapshot."""
        import subprocess

        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
            # Simulate an rsync that fails (e.g. EXDEV on --link-dest).
            if cmd and cmd[0] == "rsync":
                raise subprocess.CalledProcessError(1, cmd)
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("kanibako.snapshots.subprocess.run", fake_run)

        result = create_snapshot(vault_rw, strategy="hardlink")
        _assert_full_snapshot(result, vault_rw)

    def test_hardlink_rsync_missing_falls_back_to_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rsync not installed (FileNotFoundError) -> copytree fallback."""
        import subprocess

        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
            if cmd and cmd[0] == "rsync":
                raise FileNotFoundError("rsync not found")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("kanibako.snapshots.subprocess.run", fake_run)

        result = create_snapshot(vault_rw, strategy="hardlink")
        _assert_full_snapshot(result, vault_rw)

    def test_no_reflink_fs_detects_hardlink_and_snapshots(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No-reflink fs: detection returns hardlink, snapshot still complete.

        This mirrors the real launch path (start.py): detect the strategy,
        then create_snapshot with it.  On a filesystem whose reflink probe
        fails, detection must pick ``hardlink`` so the reflink path -- which
        has no in-path fallback -- is never executed.
        """
        import subprocess

        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
            # Make the `cp --reflink=always` probe report no reflink support.
            if cmd and cmd[0] == "cp" and "--reflink=always" in cmd:
                return subprocess.CompletedProcess(cmd, 1, b"", b"unsupported")
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr("kanibako.snapshots.subprocess.run", fake_run)

        strategy = detect_snapshot_strategy(vault_rw)
        assert strategy == "hardlink"

        result = create_snapshot(vault_rw, strategy=strategy)
        _assert_full_snapshot(result, vault_rw)


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
        assert not name.endswith(".tar.xz")
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

        # Manually create two directory snapshots with different timestamps.
        _make_dir_snapshot(versions, "20260101T000000Z", vault_rw)
        _make_dir_snapshot(versions, "20260201T000000Z", vault_rw)

        snaps = list_snapshots(vault_rw)
        assert len(snaps) == 2
        assert snaps[0][0] == "20260101T000000Z"
        assert snaps[1][0] == "20260201T000000Z"

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

    def test_ignores_legacy_tarxz(self, tmp_path: Path) -> None:
        """A leftover legacy .tar.xz archive is no longer recognized."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # An old-format archive file is simply ignored (not listed).
        (versions / "20260101T000000Z.tar.xz").write_bytes(b"old-archive")
        _make_dir_snapshot(versions, "20260201T000000Z", vault_rw)

        snaps = list_snapshots(vault_rw)
        assert len(snaps) == 1
        assert snaps[0][0] == "20260201T000000Z"


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
            restore_snapshot(vault_rw, "20260101T000000Z")

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

    def test_restore_atomic_on_failure_dir_snapshot(self, tmp_path: Path) -> None:
        """A mid-restore failure (dir snapshot) preserves live contents."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        snap = create_snapshot(vault_rw, strategy="hardlink")
        assert snap is not None

        # Mutate live data so we can detect destruction.
        (vault_rw / "file1.txt").write_text("live-precious")
        (vault_rw / "live_only.txt").write_text("must-survive")

        # Make the swap-in fail after the wipe point.
        with patch(
            "kanibako.snapshots.shutil.move",
            side_effect=OSError("disk full"),
        ):
            with pytest.raises(OSError, match="disk full"):
                restore_snapshot(vault_rw, snap.name)

        # Pre-existing live contents survived (not destroyed).
        assert (vault_rw / "file1.txt").read_text() == "live-precious"
        assert (vault_rw / "live_only.txt").read_text() == "must-survive"
        # No staging/backup dirs left behind.
        leftovers = [
            p for p in vault_rw.parent.iterdir() if p.name.startswith(".share-rw.")
        ]
        assert leftovers == []

    def test_restore_partial_swap_rolls_back(self, tmp_path: Path) -> None:
        """A failure partway through the swap rolls back the live contents."""
        vault_rw = tmp_path / "vault" / "share-rw"
        _populate_rw(vault_rw)

        snap = create_snapshot(vault_rw, strategy="hardlink")
        assert snap is not None

        (vault_rw / "file1.txt").write_text("live-precious")
        (vault_rw / "live_only.txt").write_text("must-survive")

        real_move = __import__("shutil").move
        calls = {"n": 0}

        def flaky_move(src: str, dst: str):
            # Fail only when swapping STAGED contents in (src under staging);
            # let the move-aside and the rollback moves succeed.
            if ".restore.tmp" in str(src):
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise OSError("write error mid-swap")
            return real_move(src, dst)

        with patch("kanibako.snapshots.shutil.move", side_effect=flaky_move):
            with pytest.raises(OSError, match="write error mid-swap"):
                restore_snapshot(vault_rw, snap.name)

        # All original live contents are restored intact.
        assert (vault_rw / "file1.txt").read_text() == "live-precious"
        assert (vault_rw / "live_only.txt").read_text() == "must-survive"
        assert (vault_rw / "subdir" / "file2.txt").read_text() == "world"


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
            _make_dir_snapshot(versions, f"2026010{i + 1}T000000Z", vault_rw)

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

    def test_prune_keep_zero_removes_all(self, tmp_path: Path) -> None:
        """max_keep=0 removes every snapshot (not none)."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        _make_dir_snapshot(versions, "20260101T000000Z", vault_rw)
        _make_dir_snapshot(versions, "20260102T000000Z", vault_rw)
        _make_dir_snapshot(versions, "20260103T000000Z", vault_rw)

        removed = prune_snapshots(vault_rw, max_keep=0)

        assert removed == 3
        assert list(versions.iterdir()) == []

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

    def test_prune_ignores_legacy_tarxz(self, tmp_path: Path) -> None:
        """Prune ignores leftover legacy tar.xz archives (only dirs counted)."""
        vault_rw = tmp_path / "vault" / "share-rw"
        versions = tmp_path / "vault" / ".versions"
        versions.mkdir(parents=True)
        _populate_rw(vault_rw)

        # A leftover legacy archive is not a recognized snapshot.
        (versions / "20260101T000000Z.tar.xz").write_bytes(b"old-archive")

        # Directory snapshots.
        _make_dir_snapshot(versions, "20260102T000000Z", vault_rw)
        _make_dir_snapshot(versions, "20260103T000000Z", vault_rw)

        removed = prune_snapshots(vault_rw, max_keep=2)

        # Only the two directory snapshots count; nothing pruned, archive kept.
        assert removed == 0
        remaining = sorted(e.name for e in versions.iterdir())
        assert "20260101T000000Z.tar.xz" in remaining
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
