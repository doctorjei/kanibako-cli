"""Snapshot engine for vault share-rw directories.

Provides point-in-time backups of ``share-rw/`` stored in a ``.versions/``
sibling directory.  Three strategies are supported:

* **reflink** -- copy-on-write clone (instant, space-efficient; requires a
  COW filesystem such as Btrfs or XFS with reflink support).
* **hardlink** -- ``rsync --link-dest`` so unchanged files share inodes
  (fast, moderate space; works on any POSIX filesystem).
* **tarxz** -- compressed tar archive (slow but universally portable; legacy
  default).

``detect_snapshot_strategy`` probes the filesystem and picks the best option
automatically.  Automatic snapshots can be triggered before each container
launch.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path


# Default maximum number of snapshots to retain.
_DEFAULT_MAX_SNAPSHOTS = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _versions_dir(vault_rw_path: Path) -> Path:
    """Return the .versions/ directory for a vault share-rw path."""
    return vault_rw_path.parent / ".versions"


def _test_reflink(path: Path) -> bool:
    """Test if *path*'s filesystem supports reflinks."""
    if not path.is_dir():
        return False
    test_src = path / ".reflink-test-src"
    test_dst = path / ".reflink-test-dst"
    try:
        test_src.write_bytes(b"test")
        result = subprocess.run(
            ["cp", "--reflink=always", str(test_src), str(test_dst)],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False
    finally:
        test_src.unlink(missing_ok=True)
        test_dst.unlink(missing_ok=True)


def detect_snapshot_strategy(vault_path: Path) -> str:
    """Detect the best snapshot strategy for the given path.

    Returns ``"reflink"``, ``"hardlink"``, or ``"tarxz"``.
    """
    if _test_reflink(vault_path):
        return "reflink"
    # hardlink is always available on POSIX
    return "hardlink"


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


def _snapshot_tarxz(vault_rw_path: Path, versions: Path, ts: str) -> Path:
    """Create a tar.xz snapshot (original behaviour)."""
    archive = versions / f"{ts}.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        for item in sorted(vault_rw_path.iterdir()):
            tar.add(str(item), arcname=item.name)
    return archive


def _snapshot_reflink(vault_rw_path: Path, versions: Path, ts: str) -> Path:
    """Create a snapshot using reflink (COW) copy."""
    dest = versions / ts
    subprocess.run(
        ["cp", "--reflink=always", "-a", str(vault_rw_path), str(dest)],
        check=True,
        capture_output=True,
    )
    return dest


def _snapshot_hardlink(vault_rw_path: Path, versions: Path, ts: str) -> Path:
    """Create a snapshot using hardlinks (fast for unchanged files)."""
    dest = versions / ts
    # Find the most recent directory snapshot for --link-dest.
    existing = sorted(
        (d for d in versions.iterdir() if d.is_dir()),
        key=lambda p: p.name,
    )
    link_dest = existing[-1] if existing else None

    cmd = ["rsync", "-a"]
    if link_dest:
        cmd.extend(["--link-dest", str(link_dest)])
    cmd.extend([str(vault_rw_path) + "/", str(dest) + "/"])

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # rsync not available or failed -- fall back to regular copy.
        shutil.copytree(vault_rw_path, dest)
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_snapshot(
    vault_rw_path: Path, strategy: str = "tarxz",
) -> Path | None:
    """Create a snapshot using the given strategy.

    Returns the path to the snapshot (archive or directory), or ``None`` if
    the directory is empty (nothing to snapshot).
    """
    if not vault_rw_path.is_dir():
        return None

    # Don't snapshot an empty directory.
    contents = list(vault_rw_path.iterdir())
    if not contents:
        return None

    versions = _versions_dir(vault_rw_path)
    versions.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if strategy == "reflink":
        return _snapshot_reflink(vault_rw_path, versions, ts)
    elif strategy == "hardlink":
        return _snapshot_hardlink(vault_rw_path, versions, ts)
    else:
        return _snapshot_tarxz(vault_rw_path, versions, ts)


def list_snapshots(vault_rw_path: Path) -> list[tuple[str, str, int]]:
    """List snapshots for *vault_rw_path*.

    Returns a list of ``(name, timestamp_iso, size_bytes)`` sorted by time
    (oldest first).  Both directory snapshots (reflink / hardlink) and
    legacy tar.xz archives are included.
    """
    versions = _versions_dir(vault_rw_path)
    if not versions.is_dir():
        return []

    snapshots: list[tuple[str, str, int]] = []
    for entry in sorted(versions.iterdir()):
        name = entry.name
        if entry.is_dir():
            # Directory snapshot (reflink or hardlink).
            try:
                dt = datetime.strptime(name, "%Y%m%dT%H%M%SZ")
                ts_iso = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                ts_iso = name
            # Approximate size.
            try:
                size = sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                )
            except Exception:
                size = 0
            snapshots.append((name, ts_iso, size))
        elif entry.name.endswith(".tar.xz"):
            # Legacy tar.xz snapshot.
            stem = name.removesuffix(".tar.xz")
            try:
                dt = datetime.strptime(stem, "%Y%m%dT%H%M%SZ")
                ts_iso = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                ts_iso = stem
            size = entry.stat().st_size
            snapshots.append((name, ts_iso, size))

    return snapshots


def restore_snapshot(vault_rw_path: Path, snapshot_name: str) -> None:
    """Restore *vault_rw_path* from the named snapshot.

    Handles both directory snapshots and legacy tar.xz archives.  The
    current contents of share-rw are replaced with the snapshot contents.
    Raises ``FileNotFoundError`` if the snapshot does not exist.
    """
    versions = _versions_dir(vault_rw_path)
    snapshot = versions / snapshot_name

    if snapshot.is_dir():
        # Directory snapshot (reflink or hardlink).
        if vault_rw_path.is_dir():
            for item in vault_rw_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        vault_rw_path.mkdir(parents=True, exist_ok=True)
        # Copy contents.
        for item in snapshot.iterdir():
            dest = vault_rw_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
    elif snapshot.is_file() and snapshot_name.endswith(".tar.xz"):
        # Legacy tar.xz.
        if vault_rw_path.is_dir():
            for item in vault_rw_path.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
        with tarfile.open(snapshot, "r:xz") as tar:
            tar.extractall(path=str(vault_rw_path), filter="data")
    else:
        raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")


def prune_snapshots(
    vault_rw_path: Path, max_keep: int = _DEFAULT_MAX_SNAPSHOTS,
) -> int:
    """Remove old snapshots, keeping at most *max_keep*.

    Handles both directory snapshots and legacy tar.xz archives.
    Returns the number of snapshots removed.
    """
    versions = _versions_dir(vault_rw_path)
    if not versions.is_dir():
        return 0

    # Collect all snapshots (dirs and tar.xz files).
    all_snapshots = sorted(
        (
            f
            for f in versions.iterdir()
            if f.is_dir() or f.name.endswith(".tar.xz")
        ),
        key=lambda p: p.name.removesuffix(".tar.xz"),
    )
    to_remove = all_snapshots[:-max_keep] if len(all_snapshots) > max_keep else []
    for old in to_remove:
        if old.is_dir():
            shutil.rmtree(old)
        else:
            old.unlink()
    return len(to_remove)


def auto_snapshot(
    vault_rw_path: Path,
    *,
    strategy: str = "tarxz",
    max_keep: int = _DEFAULT_MAX_SNAPSHOTS,
) -> Path | None:
    """Create a snapshot and prune old ones.

    Convenience wrapper combining ``create_snapshot`` + ``prune_snapshots``.
    Returns the new snapshot path, or ``None`` if share-rw was empty.
    """
    result = create_snapshot(vault_rw_path, strategy=strategy)
    if result is not None:
        prune_snapshots(vault_rw_path, max_keep=max_keep)
    return result
