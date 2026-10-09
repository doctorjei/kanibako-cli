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

⚑ SNAPSHOTS ARE PER-BOX: they live in ``.versions/<box>/``, not in a flat
``.versions/``.  The flat store was shared by every box under one vault base, so
one box could list, prune, or RESTORE another box's snapshots.  Legacy entries
that cannot be attributed to a box move to ``.versions/unsorted/``, where they
are listed but never deleted.

⚑ SYMLINKS ARE COPIED VERBATIM, both ways, under the rule in
:mod:`kanibako.tree_copy` (``cp -a`` and ``rsync -a`` already keep them), so a
restore puts back every link with exactly the text it had.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from kanibako.errors import KanibakoError
from kanibako.log import get_logger
from kanibako.tree_copy import copy_tree_keeping_links

logger = get_logger("snapshots")


class UnsafeSnapshotNameError(KanibakoError):
    """A snapshot name that would resolve OUTSIDE the snapshots directory.

    ``restore`` replaces share-rw wholesale, so a name that escapes
    ``.versions/`` replaces the user's data with another directory's contents.
    """



class ForeignSnapshotError(KanibakoError):
    """A snapshot that exists, but belongs to a DIFFERENT box's store.

    Raised by :func:`restore_snapshot` so a timestamp picked out of another box's
    listing cannot silently replace this box's vault.  The message names the
    owning box, because that is the one thing the user cannot see from the name.
    """


# Default maximum number of snapshots to retain.
_DEFAULT_MAX_SNAPSHOTS = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _versions_dir(vault_rw_path: Path) -> Path:
    """Return the SHARED ``.versions/`` base for a vault share-rw path.

    ⚑ This is the base, NOT a box's store. Snapshots live one level down, in
    :func:`_box_store`; a box must never read or write this directory directly.
    """
    return vault_rw_path.parent / ".versions"


#: Legacy snapshots that could not be attributed to any box land here. Listed, never deleted.
UNSORTED_DIRNAME = "unsorted"

#: A legacy snapshot directory name: the bare UTC timestamp, and nothing else.
_LEGACY_TS_RE = re.compile(r"^\d{8}T\d{6}Z$")


def _box_store(vault_rw_path: Path, box_name: str) -> Path:
    """Return *box_name*'s OWN snapshot store: ``.versions/<box>/``.

    Every box under one vault base used to share a single flat ``.versions/`` keyed
    by timestamp alone, so one box could list, prune, or RESTORE another box's
    snapshots.  The per-box store is what closes that; the base itself is only
    ever a container, never a snapshot directory.
    """
    if not box_name or not box_name.strip() or Path(box_name).name != box_name:
        raise UnsafeSnapshotNameError(
            f"Refused box name {box_name!r}: a store key must be a plain name."
        )
    return _versions_dir(vault_rw_path) / box_name


def migrate_legacy_versions(
    vault_rw_path: Path, *, box_name: str, store_exclusive: bool = False,
) -> dict[str, list[str]]:
    """Split pre-per-box ``.versions/<timestamp>`` entries out of the SHARED base.

    The old flat store never recorded WHO wrote a snapshot, so attribution is
    only possible when the store could not have been written by anyone else: a
    standalone box's ``.versions`` sits inside its own project tree, so its
    legacy entries are provably its own (``store_exclusive=True``).  Everywhere
    else -- and every same-second chimera, which by construction holds two boxes'
    files -- the owner is unknown, so the entry goes to ``unsorted/`` where it is
    listed but never pruned.  Idempotent: once split, no top-level timestamp
    directory remains to move.

    Returns ``{"attributed": [...], "unsorted": [...]}`` of moved entry names.
    """
    versions = _versions_dir(vault_rw_path)
    moved: dict[str, list[str]] = {"attributed": [], "unsorted": []}
    if not versions.is_dir():
        return moved
    legacy = [
        entry for entry in versions.iterdir()
        if entry.is_dir() and not entry.is_symlink()
        and _LEGACY_TS_RE.match(entry.name)
    ]
    if not legacy:
        return moved
    target = _box_store(vault_rw_path, box_name) if store_exclusive \
        else versions / UNSORTED_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    bucket = "attributed" if store_exclusive else "unsorted"
    for entry in sorted(legacy, key=lambda p: p.name):
        dest = target / entry.name
        if dest.exists():
            dest = target / f"{entry.name}-{box_name}"
        shutil.move(str(entry), str(dest))
        moved[bucket].append(entry.name)
        logger.info(
            "Migrated legacy vault snapshot %s to %s.", entry.name, dest.relative_to(versions)
        )
    return moved


def _snapshot_child(versions: Path, name: str) -> Path:
    """Return ``versions / name``, refusing anything that is not a DIRECT child.

    The lexical check covers an empty/whitespace name, an absolute path, and any
    name carrying a separator or a ``.``/``..`` component -- ``Path(name).name``
    is the last component, so it equals *name* only for a plain child name.  The
    resolved check follows symlinks, which the lexical one cannot see.
    """
    if not name.strip() or Path(name).name != name:
        raise UnsafeSnapshotNameError(
            f"Refused snapshot name {name!r}: pass a snapshot name listed by "
            f"'kanibako box vault list', not a path, an absolute path, '.' or '..'."
        )
    candidate = versions / name
    if candidate.resolve().parent != versions.resolve():
        raise UnsafeSnapshotNameError(
            f"Refused snapshot {name!r}: it resolves to {candidate.resolve()}, "
            f"outside the snapshots directory {versions.resolve()}."
        )
    return candidate


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
    # Newest non-link snapshot for --link-dest.
    existing = sorted(
        (d for d in versions.iterdir() if d.is_dir() and not d.is_symlink()),
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
        copy_tree_keeping_links(vault_rw_path, dest)
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _unique_snapshot_name(store: Path, ts: str) -> str:
    """A snapshot name in *store* that does not collide with an existing one.

    The timestamp is second-resolution, so two snapshots of the SAME box inside
    one second land on the same name -- and neither strategy handles that safely:
    ``cp --reflink`` copies the vault INTO the existing directory (nesting it a
    level), and ``rsync`` merges the new files into it.  ``restore`` makes this
    reachable in ordinary use, because its pre-restore safety copy is taken in
    the same second as a snapshot the user just made.  A suffixed name keeps both
    copies whole; ``list`` falls back to showing the raw name for the suffix.
    """
    if not (store / ts).exists():
        return ts
    n = 2
    while (store / f"{ts}-{n}").exists():
        n += 1
    return f"{ts}-{n}"


def create_snapshot(
    vault_rw_path: Path, *, box_name: str, strategy: str = "hardlink",
) -> Path | None:
    """Create a directory snapshot of *vault_rw_path* in *box_name*'s own store.

    Returns the path to the snapshot directory, or ``None`` if the directory
    is empty (nothing to snapshot).
    """
    if not vault_rw_path.is_dir():
        return None

    # Don't snapshot an empty directory.
    contents = list(vault_rw_path.iterdir())
    if not contents:
        return None

    store = _box_store(vault_rw_path, box_name)
    store.mkdir(parents=True, exist_ok=True)

    ts = _unique_snapshot_name(
        store, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    if strategy == "reflink":
        return _snapshot_reflink(vault_rw_path, store, ts)
    return _snapshot_hardlink(vault_rw_path, store, ts)


def _snapshot_entries(store: Path) -> list[tuple[str, str, int]]:
    """Describe the directory snapshots directly inside *store* (oldest first)."""
    if not store.is_dir():
        return []
    snapshots: list[tuple[str, str, int]] = []
    for entry in sorted(store.iterdir()):
        name = entry.name
        if entry.is_dir() and not entry.is_symlink():
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


def list_snapshots(vault_rw_path: Path, *, box_name: str) -> list[tuple[str, str, int]]:
    """List *box_name*'s own snapshots -- and only its own.

    Returns a list of ``(name, timestamp_iso, size_bytes)`` sorted by time
    (oldest first).  Only directory snapshots (reflink / hardlink) are listed.
    """
    return _snapshot_entries(_box_store(vault_rw_path, box_name))


def list_unsorted(vault_rw_path: Path) -> list[tuple[str, str, int]]:
    """List legacy snapshots whose owning box could not be proven.

    These are surfaced so the user knows the data exists, but they are never
    counted by ``prune`` and never restored by name -- see
    :func:`migrate_legacy_versions`.
    """
    return _snapshot_entries(_versions_dir(vault_rw_path) / UNSORTED_DIRNAME)


def _find_snapshot_owner(vault_rw_path: Path, snapshot_name: str) -> str | None:
    """Which box's store (or ``unsorted``) holds *snapshot_name*, if any."""
    versions = _versions_dir(vault_rw_path)
    if not versions.is_dir():
        return None
    for entry in sorted(versions.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        if (entry / snapshot_name).is_dir():
            return entry.name
    return None


def restore_snapshot(
    vault_rw_path: Path, snapshot_name: str, *, box_name: str,
    store_exclusive: bool = False,
) -> Path | None:
    """Restore *vault_rw_path* from *snapshot_name* in *box_name*'s own store.

    Two safety properties the flat shared store did not have:

    * **A foreign snapshot is refused.** A timestamp that lives in another box's
      store raises :class:`ForeignSnapshotError` naming that box, instead of
      replacing this box's vault with someone else's data.
    * **The restore is undoable.** The live contents are snapshotted into this
      box's store FIRST, so the copy the swap displaces is never the only copy.

    The restore is also rollback-safe: the new contents are built in a temporary
    staging directory, the live contents are moved aside to a backup, and only
    then are the staged contents swapped into place.  If anything fails mid-way
    the live contents are restored from the backup, so a partial restore can
    never destroy pre-existing data.  ``vault_rw_path`` itself (which may be a
    mount point) is never removed -- only its contents are swapped.

    Returns the pre-restore safety snapshot's path, or ``None`` if the vault was
    empty so there was nothing to preserve.
    """
    migrate_legacy_versions(
        vault_rw_path, box_name=box_name, store_exclusive=store_exclusive
    )
    store = _box_store(vault_rw_path, box_name)
    snapshot = _snapshot_child(store, snapshot_name)

    if not snapshot.is_dir():
        owner = _find_snapshot_owner(vault_rw_path, snapshot_name)
        if owner is not None:
            where = f"{store.parent / UNSORTED_DIRNAME / snapshot_name}"
            extra = (
                f" It is pre-split data of unknown owner, kept at {where} and "
                f"never restored by name."
                if owner == UNSORTED_DIRNAME else ""
            )
            raise ForeignSnapshotError(
                f"Snapshot '{snapshot_name}' belongs to '{owner}', not to box "
                f"'{box_name}'. Snapshots are per-box -- run 'kanibako box vault "
                f"list' to see '{box_name}'s own snapshots.{extra}"
            )
        raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

    vault_rw_path.mkdir(parents=True, exist_ok=True)

    # Preserve the live contents BEFORE displacing them, so a restore that turns
    # out to be the wrong one can itself be undone.  If that copy cannot be made
    # the restore does not run at all: replacing user data and leaving no copy is
    # exactly what this step exists to prevent.
    safety = create_snapshot(
        vault_rw_path, box_name=box_name,
        strategy=detect_snapshot_strategy(vault_rw_path),
    )

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
        copy_tree_keeping_links(snapshot, staging, dirs_exist_ok=True)
        # The copy gave staging the snapshot root's mode; the swap below moves
        # entries OUT of it, which needs write on staging whatever that mode was.
        staging.chmod(0o700)

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
            # ⚑ A restored symlink to a directory is unlinked, never rmtree'd:
            # ``rmtree`` refuses a symlink, which would stop this rollback.
            for item in list(vault_rw_path.iterdir()):
                if item.is_dir() and not item.is_symlink():
                    _rmtree_force(item)
                else:
                    item.unlink()
            for name in moved:
                shutil.move(str(backup / name), str(vault_rw_path / name))
            raise
    finally:
        # The backup is disposable here because the pre-restore snapshot above
        # already holds these contents in the box's store; removing the temp copy
        # no longer destroys the only copy of anything.
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)

    return safety


def snapshots_to_prune(
    vault_rw_path: Path, max_keep: int, *, box_name: str,
) -> list[Path]:
    """What :func:`prune_snapshots` removes for *max_keep*, oldest first.

    Only *box_name*'s own store is considered -- never another box's, and never
    ``unsorted/``.  Any symlink there is skipped: never counted, never deleted.
    """
    store = _box_store(vault_rw_path, box_name)
    if not store.is_dir():
        return []
    all_snapshots: list[Path] = []
    for entry in store.iterdir():
        if entry.is_symlink():
            logger.warning("Skipping in prune: %s is a symlink.", entry)
        elif entry.is_dir():
            all_snapshots.append(entry)
    all_snapshots.sort(key=lambda p: p.name)
    if max_keep <= 0:
        return all_snapshots
    return all_snapshots[:-max_keep] if len(all_snapshots) > max_keep else []


def prune_snapshots(
    vault_rw_path: Path, max_keep: int = _DEFAULT_MAX_SNAPSHOTS, *, box_name: str,
) -> int:
    """Remove old directory snapshots, keeping at most *max_keep*.

    Returns the number of snapshots removed.
    """
    removed = 0
    for old in snapshots_to_prune(vault_rw_path, max_keep, box_name=box_name):
        # Pruning is HOUSEKEEPING and runs inside the launch path
        # (``auto_snapshot`` <- ``start._run_container``).  Failing to reclaim an
        # OLD snapshot is never a reason to refuse to start a box, so a failure
        # here is reported and skipped rather than propagated -- but it is NOT
        # swallowed: an undeletable snapshot means the retention limit is no
        # longer being honored, and the user has to be told which one.
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
    box_name: str,
    strategy: str = "hardlink",
    max_keep: int = _DEFAULT_MAX_SNAPSHOTS,
) -> Path | None:
    """Create a snapshot in *box_name*'s store and prune that store's old ones.

    Convenience wrapper combining ``create_snapshot`` + ``prune_snapshots``.
    Returns the new snapshot path, or ``None`` if share-rw was empty.
    """
    result = create_snapshot(vault_rw_path, box_name=box_name, strategy=strategy)
    if result is not None:
        prune_snapshots(vault_rw_path, max_keep=max_keep, box_name=box_name)
    return result
