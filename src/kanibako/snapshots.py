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

⚑ SNAPSHOTS ARE PER-BOX: ``.versions/<box>/``, so no box reaches another's.
Unattributable legacy entries go to ``.versions/.unsorted/``: listed, never
deleted.  A ``.versions/.layout`` marker retires legacy detection on a base.

⚑ SYMLINKS ARE COPIED VERBATIM, both ways, under the rule in
:mod:`kanibako.tree_copy` (``cp -a`` and ``rsync -a`` already keep them), so a
restore puts back every link with exactly the text it had.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from kanibako import boxfs, kuid
from kanibako.errors import KanibakoError
from kanibako.identifiers import find_identifier
from kanibako.log import get_logger
from kanibako.tree_copy import copy_tree_keeping_links
from kanibako.utils import UTC_STAMP_COMPACT, utc_stamp

logger = get_logger("snapshots")


class UnsafeSnapshotNameError(KanibakoError):
    """A snapshot name that would resolve OUTSIDE the snapshots directory.

    ``restore`` replaces share-rw wholesale, so a name that escapes
    ``.versions/`` replaces the user's data with another directory's contents.
    """



class ForeignSnapshotError(KanibakoError):
    """A snapshot that exists, but belongs to a DIFFERENT box's store.

    The message names the owner, which is the one thing the name does not show.
    """


class RestoreRefusedError(KanibakoError):
    """``restore`` refused before changing anything, or after rolling back to it.

    The message names what stopped it and the cure.
    """


class RestoreUndoFailedError(KanibakoError):
    """A failed ``restore`` could not put the vault back; the message names the kept
    backup that holds the live contents (the pre-restore snapshot holds them too)."""


class RestoreLinkRefusedError(RestoreRefusedError):
    """The box planted a symlink where ``restore`` was putting an entry back.

    The live vault is box-writable and the box may be running, so a link there could
    send the restored entry outside the vault.  The restore is rolled back and refused.
    """


class SnapshotSafetyError(KanibakoError):
    """The pre-restore safety copy could not be made, so the restore did not run.

    NOTHING CHANGED: ``restore`` never displaces live data it cannot preserve.
    """


# Default maximum number of snapshots to retain.
_DEFAULT_MAX_SNAPSHOTS = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _versions_dir(vault_rw_path: Path) -> Path:
    """Return the SHARED ``.versions/`` base for a vault share-rw path.

    ⚑ The base, NOT a box's store; snapshots live one level down in
    :func:`_box_store`.
    """
    return vault_rw_path.parent / ".versions"


#: Legacy snapshots that could not be attributed to any box land here.  Listed,
#: never deleted.
#:
#: ⚑ The LEADING DOT is load-bearing: a box name can never start with ``.``, so
#: no box can be named into the bucket.
UNSORTED_DIRNAME = ".unsorted"

#: ⚑ Once present, legacy detection never runs on that base again.  Detection is
#: a NAME match and a timestamp is a legal box name, so an unmarked base lets
#: any box's pass sweep a timestamp-named box's store away.
LAYOUT_MARKER_NAME = ".layout"

#: Marker contents: which layout the base was converted to.  Written, not read.
LAYOUT_MARKER_VALUE = "per-box-v1\n"

#: A legacy snapshot directory name: the bare UTC timestamp, and nothing else.
_LEGACY_TS_RE = re.compile(r"^\d{8}T\d{6}Z$")

#: Base-level names that are layout, not a box store.
RESERVED_STORE_KEYS = (UNSORTED_DIRNAME, LAYOUT_MARKER_NAME)


def _box_store(vault_rw_path: Path, box_name: str) -> Path:
    """Return *box_name*'s OWN snapshot store: ``.versions/<box>/``.

    The base is only a container, never a snapshot directory; sharing it is what
    let one box reach another box's snapshots.
    """
    if not box_name or not box_name.strip() or Path(box_name).name != box_name:
        raise UnsafeSnapshotNameError(
            f"Refused box name {box_name!r}: a store key must be a plain name."
        )
    if box_name in RESERVED_STORE_KEYS:
        raise UnsafeSnapshotNameError(
            f"Refused box name {box_name!r}: that is a layout entry of the "
            f"snapshot base, not a box store."
        )
    return _versions_dir(vault_rw_path) / box_name


def box_snapshot_store(vault_rw_path: Path, box_name: str) -> Path:
    """*box_name*'s own store under the base *vault_rw_path* shares, never the base."""
    return _box_store(vault_rw_path, box_name)


def _is_migrated(versions: Path) -> bool:
    """True once *versions* carries the per-box layout marker."""
    return (versions / LAYOUT_MARKER_NAME).exists()


def _write_layout_marker(versions: Path) -> None:
    """Declare *versions* a per-box base, so legacy detection never runs on it."""
    versions.mkdir(parents=True, exist_ok=True)
    marker = versions / LAYOUT_MARKER_NAME
    if not marker.exists():
        marker.write_text(LAYOUT_MARKER_VALUE)


def _unique_snapshot_dest(
    target: Path, name: str, owner: str | None
) -> Path:
    """A free path in *target* for snapshot *name*.

    ``shutil.move`` onto an existing directory files the source INSIDE it.  A
    proven *owner* is named in the suffix; None gets a counter, so ``.unsorted``
    never credits a box with an entry it may not have written.
    """
    stem = name if owner is None else f"{name}-{owner}"
    dest = target / name
    if not dest.exists():
        return dest
    if owner is not None and not (target / stem).exists():
        return target / stem
    n = 2
    while (dest := target / f"{stem}-{n}").exists():
        n += 1
    return dest


def _migrate_flat_and_mark(
    vault_rw_path: Path, *, box_name: str, store_exclusive: bool = False,
    create_base: bool = False,
) -> dict[str, list[str]]:
    """Split any legacy flat ``.versions/<timestamp>`` entries out of the base,
    then declare that base per-box.

    ⚑ THE ONLY LEGAL WAY TO MARK A BASE: a marker over flat entries hides them
    from every listing, and a box named like the timestamp prunes them.

    *store_exclusive* (a base in this box's own tree) attributes legacy entries
    to this box; otherwise they go to ``.unsorted`` -- listed, never pruned.

    ⚑ An existing base ends MARKED even when nothing moved, or a later pass
    sweeps a timestamp-named box.  An absent base is created marked only with
    *create_base* (a writer); a reader must never create one.
    """
    versions = _versions_dir(vault_rw_path)
    moved: dict[str, list[str]] = {"attributed": [], "unsorted": []}
    if _is_migrated(versions):
        return moved
    if not versions.is_dir():
        if create_base:
            _write_layout_marker(versions)
        return moved
    legacy = [
        entry for entry in versions.iterdir()
        if entry.is_dir() and not entry.is_symlink()
        and _LEGACY_TS_RE.match(entry.name)
    ]
    if legacy:
        target = _box_store(vault_rw_path, box_name) if store_exclusive \
            else versions / UNSORTED_DIRNAME
        target.mkdir(parents=True, exist_ok=True)
        bucket = "attributed" if store_exclusive else "unsorted"
        for entry in sorted(legacy, key=lambda p: p.name):
            dest = _unique_snapshot_dest(
                target, entry.name, box_name if store_exclusive else None
            )
            _move_tree(entry, dest, vault_rw_path.parent)
            moved[bucket].append(entry.name)
            logger.info(
                "Migrated legacy vault snapshot %s to %s.", entry.name,
                dest.relative_to(versions)
            )
    _write_layout_marker(versions)
    return moved


def migrate_legacy_versions(
    vault_rw_path: Path, *, box_name: str, store_exclusive: bool = False,
    create_base: bool = False, held: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """Split pre-per-box ``.versions/<timestamp>`` entries out of the SHARED base.

    The old store never recorded who wrote a snapshot, so attribution is only
    possible where no other box could have written: a standalone ``.versions``
    is inside its own tree (``store_exclusive=True``).  Everywhere else, and
    every same-second chimera, the owner is unknown and the entry goes to
    ``.unsorted/`` -- listed, never pruned.

    A standalone DIRECTORY RENAME is settled first: it is identity rather than a
    legacy conversion, so it must run even on an already-marked base.  *held* is
    passed through to :func:`adopt_standalone_store`.
    """
    if store_exclusive:
        adopt_standalone_store(vault_rw_path, box_name=box_name, held=held)
    return _migrate_flat_and_mark(
        vault_rw_path, box_name=box_name, store_exclusive=store_exclusive,
        create_base=create_base,
    )


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

    A read-only directory in user data is legitimate, and ``rmtree`` cannot
    unlink through a parent that denies write.  The widening pass runs only
    after a ``PermissionError``, and only over the tree being deleted.
    """
    try:
        shutil.rmtree(path)
    except PermissionError:
        _force_writable_dirs(path)
        shutil.rmtree(path)


def _remove_in_vault(path: Path, root: Path) -> None:
    """Remove *path* (a tree, a file, or a link, never followed) under *root*; a
    missing *path* is fine.  ``force``: never for a snapshot (see :func:`_rmtree_force`)."""
    boxfs.rmtree(path, root, missing_ok=True, force=True)


def _move_tree(src: Path, dst: Path, root: Path, *, dst_root: Path | None = None) -> None:
    """Move *src* (under *root*) to the absent *dst* (under *dst_root*, else *root*)
    through :mod:`kanibako.boxfs`, so no symlink is followed: a rename, or on EXDEV
    a no-follow copy and then the removal of *src*.  A copy that leaves anything out
    removes nothing and raises."""
    dst_root = root if dst_root is None else dst_root
    try:
        boxfs.rename(src, dst, root, dst_root=dst_root)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    skipped = boxfs.copy_tree(src, dst, src_root=root, dst_root=dst_root)
    if skipped:
        raise OSError(errno.EIO, "could not move without following a symlink: "
                      + ", ".join(map(str, skipped)), str(src))
    boxfs.rmtree(src, root)


def _mount_point_refused(path: Path) -> RestoreRefusedError:
    return RestoreRefusedError(
        f"Refused to restore: {path} is a mount point, which a restore can neither "
        f"move nor remove. Nothing was changed; unmount it and restore again."
    )


def _move_entries(
    src_dir: Path, dst_dir: Path, names: list[str], root: Path, done: list[str],
    check: Callable[[str], None] | None = None,
) -> list[Path]:
    """Move each of *names* from *src_dir* to *dst_dir* (both under *root*) through
    :mod:`kanibako.boxfs`; return the source paths that could not be moved.

    A name goes on *done* once it is whole in *dst_dir*.  *check* runs on a name
    before it lands.  A rename refused with EXDEV (*src_dir* or *dst_dir* is a mount
    point) moves the rest the way ``shutil.move`` did, without following a link: a
    no-follow copy of *src_dir* merged into *dst_dir* (:func:`boxfs.copy_tree`), then
    the removal of the rest from *src_dir*.  If the copy leaves anything out, nothing
    is removed or put on *done*, and the paths left out are returned.

    An entry that is itself a mount point (EBUSY, or a device of its own) can never
    be removed, so it raises :class:`RestoreRefusedError` before anything is copied.
    """
    for i, name in enumerate(names):
        if check is not None:
            check(name)
        try:
            boxfs.rename(src_dir / name, dst_dir / name, root)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                raise _mount_point_refused(src_dir / name) from exc
            if exc.errno != errno.EXDEV:
                raise
            rest = names[i:]
            break
        done.append(name)
    else:
        return []
    dev = boxfs.stat(src_dir, root).st_dev
    for name in rest:
        if check is not None:
            check(name)
        try:
            entry_dev = boxfs.stat(src_dir / name, root).st_dev
        except boxfs.SymlinkRefusedError:
            continue  # a link is copied as a link, never a mount point
        if entry_dev != dev:
            raise _mount_point_refused(src_dir / name)
    # copy_tree gives *dst_dir* the source's mode; the vault keeps its own.
    mode = stat.S_IMODE(boxfs.stat(dst_dir, root).st_mode)
    skipped = boxfs.copy_tree(src_dir, dst_dir, src_root=root, dst_root=root, merge=True)
    boxfs.chmod(dst_dir, root, mode)
    if skipped:
        return skipped
    done.extend(rest)
    for name in rest:
        _remove_in_vault(src_dir / name, root)
    return []


def _test_reflink(path: Path) -> bool:
    """Test if *path*'s filesystem supports reflinks.

    The probe runs in a fresh private directory made under *path* (``mkdtemp``: a
    random name, mode 0700), never at a fixed name a box could have planted a link at.
    """
    if not path.is_dir():
        return False
    try:
        probe = Path(tempfile.mkdtemp(prefix=".reflink-test-", dir=path))
    except OSError:
        return False
    try:
        (probe / "src").write_bytes(b"test")
        result = subprocess.run(
            ["cp", "--reflink=always", str(probe / "src"), str(probe / "dst")],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def detect_snapshot_strategy(vault_path: Path) -> str:
    """Detect the best snapshot strategy for the vault share-rw *vault_path*.

    The probe runs where the snapshots land -- the ``.versions`` base, or the vault's
    parent before the base exists -- and never in the box-writable vault itself.

    Returns ``"reflink"`` or ``"hardlink"``.
    """
    versions = _versions_dir(vault_path)
    if _test_reflink(versions if versions.is_dir() else vault_path.parent):
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

    Timestamps are second-resolution, so two snapshots of one box in one second
    share a name, and neither strategy copes: ``cp --reflink`` nests the vault
    inside the existing directory, ``rsync`` merges into it.  ``restore`` makes
    that reachable, taking its safety copy in the same second as a snapshot the
    user just made.  A suffix keeps both copies whole.
    """
    if not (store / ts).exists():
        return ts
    n = 2
    while (store / f"{ts}-{n}").exists():
        n += 1
    return f"{ts}-{n}"


def create_snapshot(
    vault_rw_path: Path, *, box_name: str, strategy: str = "hardlink",
    store_exclusive: bool = False, held: Mapping[str, str] | None = None,
) -> Path | None:
    """Create a directory snapshot of *vault_rw_path* in *box_name*'s own store.

    ⚑ Every write migrates the base first, so a base this code writes into is a
    per-box base before the first snapshot lands in it.  Leaving the marker to a
    later reader would let a timestamp-named box made in between be swept.

    Returns the path to the snapshot directory, or ``None`` if the directory
    is empty (nothing to snapshot).
    """
    if not vault_rw_path.is_dir():
        return None

    # Don't snapshot an empty directory.
    contents = list(vault_rw_path.iterdir())
    if not contents:
        return None

    migrate_legacy_versions(
        vault_rw_path, box_name=box_name, store_exclusive=store_exclusive,
        create_base=True, held=held,
    )

    store = _box_store(vault_rw_path, box_name)
    store.mkdir(parents=True, exist_ok=True)

    ts = _unique_snapshot_name(store, utc_stamp("compact"))

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
                dt = datetime.strptime(name, UTC_STAMP_COMPACT)
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


def adopt_standalone_store(
    vault_rw_path: Path, *, box_name: str, held: Mapping[str, str] | None = None,
) -> Path | None:
    """Give a renamed standalone box the store already sitting in its own tree.

    A standalone box's store is INSIDE its project tree, so a raw ``mv`` carries
    the store but not its KEY: the box re-derives ``<kuid>_<new-leaf>`` from the
    directory while the store is still ``<kuid>_<old-leaf>``, so it lists nothing
    and refuses its own data as foreign.  The kuid survives the rename -- it comes
    from the box's own settings, not the directory -- so a store in this box's
    tree carrying this box's kuid is this box's store.  ⚑ More than one is
    coincidence, not rename, so nothing is guessed.

    ⚑ *held* maps a store name to the root of ANOTHER box that composes it (the caller
    knows the registry; this module does not).  That store is that box's, reached here
    by another spelling of one tree, so it is never moved: a Note names the holder.
    """
    store = _box_store(vault_rw_path, box_name)
    if store.exists():
        return None
    prefix, sep, _leaf = box_name.partition("_")
    if not sep or not kuid.is_valid(prefix):
        return None
    versions = _versions_dir(vault_rw_path)
    if not versions.is_dir():
        return None
    candidates = [
        entry for entry in sorted(versions.iterdir())
        if entry.is_dir() and not entry.is_symlink()
        and entry.name != box_name
        and entry.name.partition("_")[0].lower() == prefix.lower()
    ]
    if len(candidates) != 1:
        return None
    holder = find_identifier(candidates[0].name, held or {})
    if holder is not None:
        logger.warning("Note: snapshot store %s is the store of the box registered at %s; "
                       "it is not adopted as %s.", candidates[0], (held or {})[holder], box_name)
        return None
    store.parent.mkdir(parents=True, exist_ok=True)
    _move_tree(candidates[0], store, vault_rw_path.parent)
    # AFTER the move: run first, the helper would create this store and the
    # adopted one would land inside it.
    _migrate_flat_and_mark(
        vault_rw_path, box_name=box_name, store_exclusive=True
    )
    return store


def relocate_snapshot_store(
    old_vault_rw: Path, new_vault_rw: Path, *, old_box: str, new_box: str,
    new_base_exclusive: bool = False,
) -> Path | None:
    """Carry *old_box*'s snapshot store to *new_box* across a rename or a move.

    Left under the OLD key, the box lists nothing and the next box of that name
    inherits the snapshots.

    ⚑ *new_base_exclusive*: the NEW base is in this box's OWN tree, the ONLY case
    where ``new_base/<old name>`` is ours (a tree copy carried it).  In a shared
    base that path is another box's store.

    ⚑ A BASE change carries the store even when the name is kept.  Never
    overwrites: a taken destination name gets a suffix.
    """
    old_base = _versions_dir(old_vault_rw)
    new_base = _versions_dir(new_vault_rw)
    new_store = _box_store(new_vault_rw, new_box)
    new_root = new_vault_rw.parent

    carried, carried_root = None, new_root
    if new_base_exclusive and new_box != old_box:
        in_new_base = new_base / old_box
        if in_new_base.is_dir() and not in_new_base.is_symlink():
            carried = in_new_base
    if carried is None:
        old_store = old_base / old_box
        if (old_store != new_store and old_store.is_dir()
                and not old_store.is_symlink()):
            carried, carried_root = old_store, old_vault_rw.parent
    if carried is None:
        return None

    _migrate_flat_and_mark(
        new_vault_rw, box_name=new_box, store_exclusive=new_base_exclusive,
        create_base=True,
    )
    if not new_store.exists():
        _move_tree(carried, new_store, carried_root, dst_root=new_root)
        return new_store

    # Destination store already exists: merge, never clobber.
    new_store.mkdir(parents=True, exist_ok=True)
    for name in sorted(boxfs.listdir(carried, carried_root)):
        dest = _unique_snapshot_dest(new_store, name, old_box)
        _move_tree(carried / name, dest, carried_root, dst_root=new_root)
    with contextlib.suppress(OSError):
        boxfs.rmtree(carried, carried_root)
    return new_store


def restore_snapshot(
    vault_rw_path: Path, snapshot_name: str, *, box_name: str,
    store_exclusive: bool = False, held: Mapping[str, str] | None = None,
    box_ref: str | None = None,
) -> Path | None:
    """Restore *vault_rw_path* from *snapshot_name* in *box_name*'s own store.

    * **A foreign snapshot is refused.** A timestamp that lives in another box's
      store raises :class:`ForeignSnapshotError` naming that box, instead of
      replacing this box's vault with someone else's data.  Its ``vault list``
      cure names *box_ref* (``paths.project_cure_ref``), a ``<box>`` placeholder
      without one.
    * **The restore is undoable.** The live contents are snapshotted into this
      box's store FIRST, so the copy the swap displaces is never the only copy.

    The restore is also rollback-safe: the new contents are built in a temporary
    staging directory, the live contents are moved aside to a backup, and only
    then are the staged contents swapped into place, so a partial restore can
    never destroy pre-existing data.  ``vault_rw_path`` itself (which may be a
    mount point) is never removed -- only its contents are swapped, by copy when
    a mount point stops the renames; an entry that is itself a mount point is
    refused.  If even the roll-back fails, :class:`RestoreUndoFailedError` names
    the backup it kept, and a later restore is refused while that backup exists.

    Returns the pre-restore safety snapshot's path, or ``None`` if the vault was
    empty so there was nothing to preserve.
    """
    migrate_legacy_versions(
        vault_rw_path, box_name=box_name, store_exclusive=store_exclusive, held=held,
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
                f"'{box_name}'.{extra} Snapshots are per-box; list '{box_name}'s "
                f"own with:\n  kanibako box vault list {box_ref or '<box>'}"
            )
        raise FileNotFoundError(f"Snapshot not found: {snapshot_name}")

    vault_rw_path.mkdir(parents=True, exist_ok=True)

    # ⚑ The live vault is box-writable, and the box may be running (``vault restore``
    # has no running-box guard), so every move into or out of it, and every removal in
    # it, goes through boxfs rooted at the vault's parent: a link the box plants in it
    # is never followed.  The path is resolved first, because a link AT the vault is the
    # host's own repoint, which a box cannot plant.
    live = vault_rw_path.resolve()
    root = live.parent

    # A backup a failed restore kept holds the only live copy outside the store: never
    # removed by another restore.
    backup = root / f".{live.name}.restore.bak"
    if os.path.lexists(backup):
        raise RestoreRefusedError(
            f"Refused to restore '{snapshot_name}': {backup} holds the vault contents "
            f"an earlier restore could not put back. Move what you need from it into "
            f"{live}, delete it, and restore again."
        )

    # No safety copy, no restore.
    try:
        safety = create_snapshot(
            vault_rw_path, box_name=box_name,
            strategy=detect_snapshot_strategy(vault_rw_path),
            store_exclusive=store_exclusive, held=held,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        # ⚑ OSError too: the hardlink path's copy fallback raises it directly.
        raise SnapshotSafetyError(
            f"Could not snapshot the current vault contents before restoring "
            f"'{snapshot_name}': the copy failed ({exc}). Nothing was changed -- "
            f"your current files are still in place. Restore again once the "
            f"snapshot store is writable and has room."
        ) from exc

    # Stage the new contents in a temp sibling of the vault so the final swap is a
    # same-filesystem rename -- unless the vault is a mount point, when every move
    # falls back to a no-follow copy (:func:`_move_entries`).
    staging = root / f".{live.name}.restore.tmp"
    _remove_in_vault(staging, root)
    staging.mkdir(parents=True)

    def not_moved(paths: list[Path]) -> RestoreLinkRefusedError:
        return RestoreLinkRefusedError(
            f"Refused to restore '{snapshot_name}': {', '.join(map(str, paths))} could "
            f"not be moved without following a symlink (or is not a file, directory, "
            f"or symlink). The vault was put back as it was."
        )

    def no_link(name: str) -> None:
        link = boxfs.first_symlink(live / name, root)
        if link is not None:
            raise RestoreLinkRefusedError(
                f"Refused to restore '{snapshot_name}': {link} is a symlink, "
                f"and kanibako does not follow symlinks in a box. The vault was "
                f"put back as it was; remove the link and restore again."
            )

    moved: list[str] = []
    swapping = False
    kept_backup = False
    try:
        # Build the new contents in the staging directory.
        copy_tree_keeping_links(snapshot, staging, dirs_exist_ok=True)
        # The copy gave staging the snapshot root's mode; the swap below moves
        # entries OUT of it, which needs write on staging whatever that mode was.
        staging.chmod(0o700)

        try:
            # Move the live contents aside (preserves the mount point itself).
            backup.mkdir(parents=True)
            left = _move_entries(live, backup, boxfs.listdir(live, root), root, moved)
            if left:
                raise not_moved(left)

            # Swap the staged contents into place.
            swapping = True
            placed: list[str] = []
            left = _move_entries(
                staging, live, sorted(os.listdir(staging)), root, placed, check=no_link,
            )
            if left:
                raise not_moved(left)
        except Exception as exc:
            # Roll back: clear whatever made it in, restore the backup.
            # ⚑ The DATA-PRESERVING arm: a read-only directory among the staged
            # contents must not be what stops the live vault from being put back, and
            # a link (restored, or planted) is removed as a link, never followed.
            # Before the swap, only what reached the backup is cleared: the rest is
            # still in place.  The backup keeps only what is whole in it.
            try:
                for name in boxfs.listdir(live, root):
                    if swapping or name in moved:
                        _remove_in_vault(live / name, root)
                for name in boxfs.listdir(backup, root):
                    if name not in moved:
                        _remove_in_vault(backup / name, root)
                if _move_entries(backup, live, list(moved), root, []):
                    raise OSError(errno.EIO, "the backup could not be put back whole")
            except Exception as undo:
                # ⚑ Every refusal above says the vault was put back: not true now, so
                # the error the user sees is this one, naming where the contents are.
                kept_backup = True
                held_too = (
                    f" The pre-restore snapshot {safety.name} holds them too."
                    if safety is not None else ""
                )
                raise RestoreUndoFailedError(
                    f"Restore of '{snapshot_name}' failed ({exc}), and the vault could "
                    f"not be put back ({undo}). The contents not in {live} are kept at "
                    f"{backup}.{held_too} "
                    f"Move them back into {live} and delete {backup}; a restore is "
                    f"refused while it exists."
                ) from exc
            raise
    finally:
        # Disposable: the pre-restore snapshot already holds these in the store.
        for leftover in (staging,) if kept_backup else (staging, backup):
            with contextlib.suppress(OSError):
                _remove_in_vault(leftover, root)

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
        # ⚑ Runs in the launch path: a failure is warned and skipped, never
        # raised (it must not block a start) and never silent (retention broke).
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
    store_exclusive: bool = False,
    held: Mapping[str, str] | None = None,
) -> Path | None:
    """Create a snapshot in *box_name*'s store and prune that store's old ones.

    Convenience wrapper combining ``create_snapshot`` + ``prune_snapshots``.
    Returns the new snapshot path, or ``None`` if share-rw was empty.
    """
    result = create_snapshot(
        vault_rw_path, box_name=box_name, strategy=strategy,
        store_exclusive=store_exclusive, held=held,
    )
    if result is not None:
        prune_snapshots(vault_rw_path, max_keep=max_keep, box_name=box_name)
    return result
