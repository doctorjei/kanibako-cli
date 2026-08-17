"""Snapshot engine for vault share-rw directories.

Provides point-in-time backups of ``share-rw/`` stored in a ``.versions/``
sibling directory.  Two strategies are supported, both producing directory
snapshots:

* **reflink** -- copy-on-write clone (instant, space-efficient; requires a
  COW filesystem such as Btrfs or XFS with reflink support).
* **hardlink** -- ``rsync --link-dest`` so unchanged files share inodes
  (fast, moderate space; works on any POSIX filesystem).

``detect_snapshot_strategy`` probes the filesystem and picks the best option
automatically.  Automatic snapshots can be triggered before each container
launch.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from kanibako.log import get_logger

logger = get_logger("snapshots")


# Default maximum number of snapshots to retain.
_DEFAULT_MAX_SNAPSHOTS = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _versions_dir(vault_rw_path: Path) -> Path:
    """Return the .versions/ directory for a vault share-rw path."""
    return vault_rw_path.parent / ".versions"


def _force_writable_dirs(root: Path) -> None:
    """Add owner write+execute to every directory at or under *root*.

    Unlinking an entry requires write on its PARENT DIRECTORY -- the entry's own
    mode is irrelevant -- so this only touches directories.  ``os.walk`` is
    top-down and each directory is chmod'ed as it is yielded, which is what lets
    the walk descend into a mode-0555 (or 0444) directory it has just widened.
    Best-effort per entry: a directory we cannot chmod (not ours) is skipped and
    left for the caller's error handling rather than aborting the whole sweep.
    """
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=True):
        try:
            mode = os.stat(dirpath).st_mode
            os.chmod(dirpath, mode | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            continue


def _rmtree_force(path: Path) -> None:
    """``shutil.rmtree`` that also removes trees containing READ-ONLY directories.

    Vault content is arbitrary user data, and a read-only directory in it is
    perfectly legitimate -- copying one in is enough to make a snapshot of it
    undeletable, because ``rmtree`` cannot unlink through a parent that denies
    write.  Measured 2026-08-17: a read-only tree under ``vault/rw`` propagated
    into ``.versions/`` and made ``prune_snapshots`` raise ``PermissionError``
    from inside the launch path, so ``kanibako start`` could not start the box
    at all until the offending directories were moved out BY HAND.

    The plain ``rmtree`` is attempted FIRST so the overwhelmingly common case is
    byte-identical to before; the widening pass runs only after a
    ``PermissionError``, and only over the tree we were already asked to delete.
    """
    try:
        shutil.rmtree(path)
    except PermissionError:
        _force_writable_dirs(path)
        shutil.rmtree(path)


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

    Returns ``"reflink"`` or ``"hardlink"``.
    """
    if _test_reflink(vault_path):
        return "reflink"
    # hardlink is always available on POSIX
    return "hardlink"


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------


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
    vault_rw_path: Path, strategy: str = "hardlink",
) -> Path | None:
    """Create a directory snapshot using the given strategy.

    Returns the path to the snapshot directory, or ``None`` if the directory
    is empty (nothing to snapshot).
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
    return _snapshot_hardlink(vault_rw_path, versions, ts)


def list_snapshots(vault_rw_path: Path) -> list[tuple[str, str, int]]:
    """List snapshots for *vault_rw_path*.

    Returns a list of ``(name, timestamp_iso, size_bytes)`` sorted by time
    (oldest first).  Only directory snapshots (reflink / hardlink) are listed.
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

    return snapshots


def restore_snapshot(vault_rw_path: Path, snapshot_name: str) -> None:
    """Restore *vault_rw_path* from the named directory snapshot.

    The current contents of share-rw are replaced with the snapshot contents.
    Raises ``FileNotFoundError`` if the snapshot does not exist.

    The restore is rollback-safe: the snapshot contents are first built in
    a temporary staging directory, the live contents are moved aside to a
    backup, and only then are the staged contents swapped into place.  If
    anything fails mid-way the live contents are restored from the backup,
    so a partial restore can never destroy pre-existing data.  ``vault_rw_path``
    itself (which may be a mount point) is never removed -- only its contents
    are swapped.
    """
    versions = _versions_dir(vault_rw_path)
    snapshot = versions / snapshot_name

    if not snapshot.is_dir():
        raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

    vault_rw_path.mkdir(parents=True, exist_ok=True)

    # Stage the new contents in a temp sibling of vault_rw_path so the final
    # swap is a same-filesystem rename.
    staging = vault_rw_path.parent / f".{vault_rw_path.name}.restore.tmp"
    backup = vault_rw_path.parent / f".{vault_rw_path.name}.restore.bak"
    if staging.exists():
        _rmtree_force(staging)
    if backup.exists():
        _rmtree_force(backup)
    staging.mkdir(parents=True)

    try:
        # Build the new contents in the staging directory.
        for item in snapshot.iterdir():
            dest = staging / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Move the live contents aside (preserves the mount point itself).
        backup.mkdir(parents=True)
        moved: list[str] = []
        for item in list(vault_rw_path.iterdir()):
            shutil.move(str(item), str(backup / item.name))
            moved.append(item.name)

        # Swap the staged contents into place.
        try:
            for item in list(staging.iterdir()):
                shutil.move(str(item), str(vault_rw_path / item.name))
        except Exception:
            # Roll back: clear whatever made it in, restore the backup.
            # ⚑ _rmtree_force, not rmtree: this is the DATA-PRESERVING arm, and a
            # read-only directory among the staged contents must not be what
            # stops the live vault from being put back.
            for item in list(vault_rw_path.iterdir()):
                if item.is_dir():
                    _rmtree_force(item)
                else:
                    item.unlink()
            for name in moved:
                shutil.move(str(backup / name), str(vault_rw_path / name))
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def prune_snapshots(
    vault_rw_path: Path, max_keep: int = _DEFAULT_MAX_SNAPSHOTS,
) -> int:
    """Remove old directory snapshots, keeping at most *max_keep*.

    Returns the number of snapshots removed.
    """
    versions = _versions_dir(vault_rw_path)
    if not versions.is_dir():
        return 0

    all_snapshots = sorted(
        (f for f in versions.iterdir() if f.is_dir()),
        key=lambda p: p.name,
    )
    if max_keep <= 0:
        to_remove = all_snapshots
    else:
        to_remove = all_snapshots[:-max_keep] if len(all_snapshots) > max_keep else []
    removed = 0
    for old in to_remove:
        # Pruning is HOUSEKEEPING and runs inside the launch path
        # (``auto_snapshot`` <- ``start._run_container``).  Failing to reclaim an
        # OLD snapshot is never a reason to refuse to start a box, so a failure
        # here is reported and skipped rather than propagated -- but it is NOT
        # swallowed: an undeletable snapshot means the retention limit is no
        # longer being honoured, and the user has to be told which one.
        try:
            _rmtree_force(old)
        except OSError as exc:
            logger.warning(
                "Could not prune old vault snapshot %s: %s. "
                "It is being kept; remove it by hand to reclaim the space.",
                old.name, exc,
            )
            continue
        removed += 1
    return removed


def auto_snapshot(
    vault_rw_path: Path,
    *,
    strategy: str = "hardlink",
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
