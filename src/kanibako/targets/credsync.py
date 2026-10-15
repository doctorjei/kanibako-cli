"""Agent-agnostic credential-sync engine (LIVE — wired into start.py).

This module owns the credential lifecycle of every plugin.  It is driven entirely by a :class:`~kanibako.targets.base.PluginDescriptor`
(its ``init_dirs`` and ``cred_files``) plus the plugin's
:meth:`~kanibako.targets.base.Target.transform_cred` hook for the divergent
filter/merge payload of ``filtered`` specs.

Three primitives map onto the three lifecycle phases:

* :func:`seed_cred_files`      — project init (replaces the credential part of ``init_home``)
* :func:`refresh_cred_files`   — pre-launch host->project sync
* :func:`writeback_cred_files` — post-session project->host sync

The engine owns the data-driven mtime gating / directory creation / chmod; the
per-file transform (claude ``claudeAiOauth`` merge + ``.claude.json`` allowlist)
stays a plugin hook reached via ``transform_cred``.

NOTE: This engine is LIVE.  ``commands/start.py`` calls :func:`seed_cred_files`
(new boxes), :func:`refresh_cred_files` (reattach + pre-launch), and
:func:`writeback_cred_files` (post-session) for every descriptor-bearing target;
the credential part of ``init_home`` is bypassed on the descriptor path.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako import boxfs
from kanibako.launch.creds_watcher import creds_store_lock
from kanibako.log import get_logger
from kanibako.targets.base import Cadence

if TYPE_CHECKING:
    from collections.abc import Callable

    from kanibako.settings.settings_launch import AuthSource
    from kanibako.targets.base import CredFileSpec, PluginDescriptor, Target

logger = get_logger("credsync")

# Writeback caps on what a box can hand the host (no-host-touch design § 4): the
# bytes are read into host memory to be checked, and a box can plant a sparse file
# that costs it no disk.
_BOX_FILE_MAX_BYTES = 1 << 20
_BOX_DIR_MAX_BYTES = 4 << 20
_BOX_DIR_MAX_ENTRIES = 256


def _chmod_600(path: Path) -> None:
    """Set 0600 (owner read/write) permissions on *path*, best-effort."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.debug("credsync: chmod 0600 failed for %s: %s", path, exc)


def _copy_dir(src: Path, dst: Path) -> None:
    """Recursively copy the host directory *src* into the host directory *dst*.

    Merges into an existing *dst*.  Used for ``is_dir`` cred specs (no shipped
    plugin declares one; the ABI keeps them until ``cred_files`` goes).  A directory has no single mtime to gate on, so dir
    specs are NOT mtime-gated: the source tree is the authority and is mirrored
    wholesale on every sync.  Each copied file is chmod 0600 (these dirs hold
    config that may reference secrets).  Both sides are host trees, so links are
    followed as ``copytree`` follows them; a box tree is reached only through
    :func:`_dir_into_box` and :func:`_dir_from_box`.
    """
    shutil.copytree(str(src), str(dst), dirs_exist_ok=True)
    for child in dst.rglob("*"):
        if child.is_file():
            _chmod_600(child)


def _warn_link_race(exc: boxfs.SymlinkRefusedError) -> None:
    """Warn of a link the box put in place after ``boxfs.no_symlink_or_warn`` passed."""
    logger.warning("skipped %s: %s is a symlink, and kanibako does not follow "
                   "symlinks in a box", exc.path, exc.link)


def _box_file_stat(path: Path, root: Path) -> os.stat_result | None:
    """The stat of the regular box file *path*, or None when it is missing or not one."""
    try:
        st = boxfs.stat(path, root)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _file_into_box(src: Path, dst: Path, project_home: Path) -> None:
    """Copy the host file *src* to the box file *dst* as ``copy2`` would, then 0600."""
    boxfs.copy_file(src, dst, src_root=None, dst_root=project_home)
    boxfs.chmod(dst, project_home, stat.S_IRUSR | stat.S_IWUSR)


def _transform_into_box(
    spec: CredFileSpec, target: Target, src: Path | None, dst: Path,
    project_home: Path,
) -> None:
    """Run the plugin's ``transform_cred(..., "in")`` and put its result in the box.

    The plugin never sees a box path: it works on a host-side copy of the box file
    in a private temp dir (a missing box file is missing there too), and its result
    goes into the box through ``boxfs`` at 0600.  A box file that
    :func:`_read_box_file` refuses (not a regular file, too large) is skipped with
    its warning.  Unchanged content is not rewritten.
    """
    exists = True
    try:
        boxfs.stat(dst, project_home)
    except (FileNotFoundError, NotADirectoryError):
        exists = False
    box = _read_box_file(project_home, spec.home_rel, "credential refresh") if exists else None
    if exists and box is None:
        return
    with tempfile.TemporaryDirectory(prefix=".kanibako-cred-in.") as work:
        out = Path(work) / dst.name
        if box is not None:
            out.write_bytes(box[0])
            os.utime(out, ns=(box[1].st_atime_ns, box[1].st_mtime_ns))
        target.transform_cred(spec, src, out, "in")
        if not out.is_file():
            return
        if box is not None and out.read_bytes() == box[0]:
            boxfs.chmod(dst, project_home, stat.S_IRUSR | stat.S_IWUSR)
            return
        _chmod_600(out)
        boxfs.copy_file(out, dst, src_root=None, dst_root=project_home)


def _dir_into_box(src: Path, dst: Path, project_home: Path) -> None:
    """Mirror the host directory *src* into the box directory *dst*.

    The host side is copied as :func:`_copy_dir` copies it (links followed, files
    0600) into a private temp dir, and that copy goes into the box through
    ``boxfs.copy_tree``, which leaves a link already in the box tree untouched,
    with a warning.
    """
    with tempfile.TemporaryDirectory(prefix=".kanibako-cred-in.") as work:
        staged = Path(work) / dst.name
        _copy_dir(src, staged)
        boxfs.copy_tree(staged, dst, src_root=None, dst_root=project_home, merge=True)


def _dir_from_box(src: Path, dst: Path, project_home: Path) -> None:
    """Mirror the box directory *src* into the host directory *dst*.

    ``boxfs.copy_tree`` copies the box side into a private temp dir, leaving out
    every link in it with a warning; :func:`_copy_dir` mirrors that copy into the
    store.
    """
    with tempfile.TemporaryDirectory(prefix=".kanibako-cred-out.") as work:
        staged = Path(work) / dst.name
        boxfs.copy_tree(src, staged, src_root=project_home, dst_root=None,
                        skip_links=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_dir(staged, dst)


def seed_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    source_root: Path | None,
    project_home: Path,
) -> None:
    """Seed credential/config files into a freshly-created project home.

    Replaces the credential portion of each plugin's ``init_home``.  Creates
    the descriptor's ``init_dirs`` and lays down the initial in-copy of every
    ``cred_files`` spec (BOTH cadences get their initial in-copy at seed time;
    cadence only governs later refresh/writeback).

    *source_root* is the SELECTED credential SOURCE root (auth-level design step
    4): the host home for the GLOBAL tier, the box's per-agent WORKSET source dir
    for the WORKSET tier, or ``None`` for the private/BOX tier (no source — today's
    distinct auth). Each spec's ``host_rel`` joins under it (the layout mirrors the
    in-guest ``home_rel``). ``None`` degenerates to "no source": unfiltered specs
    seed nothing, filtered specs get ``src=None`` (the plugin writes its default).

    Filtered specs are routed through ``target.transform_cred(..., "in")`` so the
    plugin can filter/merge or write a default when no source is available.
    Unfiltered specs are a wholesale copy + 0600 when a source file exists.

    Every write under *project_home* goes through :mod:`kanibako.boxfs`: a path
    with a symlink at it or a parent below *project_home* is skipped with a
    warning naming it, and the other paths still run.
    """
    project_home.mkdir(parents=True, exist_ok=True)
    for d in descriptor.init_dirs:
        path = project_home / d
        if boxfs.no_symlink_or_warn(path, project_home):
            try:
                boxfs.mkdir(path, project_home, parents=True, exist_ok=True)
            except boxfs.SymlinkRefusedError as exc:
                _warn_link_race(exc)

    for spec in descriptor.cred_files:
        dst = project_home / spec.home_rel
        src = source_root / spec.host_rel if source_root is not None else None
        if not boxfs.no_symlink_or_warn(dst, project_home):
            continue
        try:
            _seed_one(spec, target, src, dst, project_home)
        except boxfs.SymlinkRefusedError as exc:
            _warn_link_race(exc)


def _seed_one(
    spec: CredFileSpec, target: Target, src: Path | None, dst: Path,
    project_home: Path,
) -> None:
    """Seed the box file or dir *dst* of one *spec* from *src* (None: no source)."""
    if dst.parent != project_home:
        boxfs.mkdir(dst.parent, project_home, parents=True, exist_ok=True)
    if spec.is_dir:
        # Directory spec (``is_dir``): recursive copy when
        # a source dir exists (a private box has no source).  Never filtered.
        if src is not None and src.is_dir():
            _dir_into_box(src, dst, project_home)
        return
    src_ok = src is not None and src.is_file()
    if spec.filtered:
        _transform_into_box(spec, target, src if src_ok else None, dst, project_home)
    elif src_ok:
        assert src is not None  # src_ok implies src present.
        _file_into_box(src, dst, project_home)
    # else: unfiltered + no source -> nothing to seed.


def refresh_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    source_root: Path | None,
    project_home: Path,
) -> None:
    """Pre-launch source->project sync of SYNC-cadence credential files.

    No-op when *source_root* is ``None`` (the
    private/BOX tier — today's distinct auth).  ``SEED_ONCE`` specs are never
    refreshed.  For ``SYNC`` specs the source file (under *source_root* — the host
    home for GLOBAL, the workset source dir for WORKSET) must exist and (when
    ``mtime_gate``) be strictly newer than the project file; filtered specs route
    through ``transform_cred(..., "in")``, unfiltered specs are a wholesale copy +
    0600.  Box paths are written as :func:`seed_cred_files` writes them, so a
    symlinked one is skipped with a warning.
    """
    if source_root is None:
        return

    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = source_root / spec.host_rel
        dst = project_home / spec.home_rel
        # A missing source (file or dir) is skipped silently.
        if not (src.is_dir() if spec.is_dir else src.is_file()):
            continue
        project_home.mkdir(parents=True, exist_ok=True)  # the root; boxfs opens it as given
        if not boxfs.no_symlink_or_warn(dst, project_home):
            continue
        try:
            _refresh_one(spec, target, src, dst, project_home)
        except boxfs.SymlinkRefusedError as exc:
            _warn_link_race(exc)


def _refresh_one(
    spec: CredFileSpec, target: Target, src: Path, dst: Path, project_home: Path,
) -> None:
    """Refresh the box file or dir *dst* of one SYNC *spec* from the store's *src*."""
    if spec.is_dir:
        # Directory spec: mirror the source dir into the box (no mtime gate;
        # see _copy_dir).
        _dir_into_box(src, dst, project_home)
        return
    if spec.mtime_gate:
        box = _box_file_stat(dst, project_home)
        if box is not None and src.stat().st_mtime <= box.st_mtime:
            return
    if spec.filtered:
        _transform_into_box(spec, target, src, dst, project_home)
    else:
        _file_into_box(src, dst, project_home)


def writeback_cred_files(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    source_root: Path | None,
    project_home: Path,
) -> bool:
    """Post-session project->source sync of SYNC-cadence credential files.

    No-op when *source_root* is ``None`` (the
    private/BOX tier).  ``SEED_ONCE`` specs are never written back.  Direction is
    REVERSED: the project file is the source and the file under *source_root* (the
    host home for GLOBAL, the workset source dir for WORKSET) the destination.  The
    project file must exist and (when ``mtime_gate``) be strictly newer than the
    dest; filtered specs route through ``transform_cred(..., "out")``, unfiltered
    specs are a wholesale copy (an existing dest keeps its mode).

    🛑 The box writes everything under *project_home*, so nothing there is trusted:
    a box file reached through a symlink is refused, and so is one the harness's
    :meth:`~kanibako.targets.base.Target.cred_writeback_error` rejects.  A refusal
    warns, naming the file, and leaves the store as it was.  A replaced store file
    keeps its previous content as ``<file>.bak``.  A spec that fails (a failed
    backup, a full disk) warns, naming it, and the remaining specs still run.

    Returns ``False`` when a spec failed, so the caller can leave the box's
    creds-dirty flag set for a retry; a refusal is not a failure.
    """
    if source_root is None:
        return True

    ok = True
    for spec in descriptor.cred_files:
        if spec.cadence is not Cadence.SYNC:
            continue
        src = project_home / spec.home_rel
        dst = source_root / spec.host_rel
        try:
            _writeback_one(spec, target, project_home, src, dst)
        except Exception as exc:
            logger.warning("credential writeback of %s to %s failed: %s",
                           src, dst, exc)
            ok = False
    return ok


def _writeback_one(
    spec: CredFileSpec, target: Target, project_home: Path, src: Path, dst: Path,
) -> None:
    """Write the box's copy of one SYNC *spec* (*src*) back to the store (*dst*)."""
    if spec.is_dir:
        # Directory spec: mirror the box dir back to the source (no mtime
        # gate; see _copy_dir).  Skip silently when the box dir is absent.
        if not boxfs.no_symlink_or_warn(src, project_home):
            return
        try:
            is_dir = stat.S_ISDIR(boxfs.stat(src, project_home).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            return
        if is_dir and not _box_dir_too_big(src):
            _dir_from_box(src, dst, project_home)
        return
    box = _read_box_file(project_home, spec.home_rel)
    if box is None:
        return
    data, box_stat = box
    if (
        spec.mtime_gate
        and dst.is_file()
        and box_stat.st_mtime <= dst.stat().st_mtime
    ):
        return
    reason = target.cred_writeback_error(spec, data)
    if reason is not None:
        logger.warning(
            "credential writeback skipped: %s is not a valid %s credential file "
            "(%s); %s is unchanged", src, target.name, reason, dst,
        )
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    _replace_store_file(spec, target, data, box_stat, dst)


def _box_dir_too_big(src: Path) -> bool:
    """Whether the box dir *src* is over the writeback caps; warns when it is.

    Counts every entry and the size of every regular file, never following a link.
    """
    entries = size = 0
    for d, dirs, files in os.walk(src):
        for name in dirs + files:
            entries += 1
            try:
                st = os.lstat(os.path.join(d, name))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                size += st.st_size
        if entries > _BOX_DIR_MAX_ENTRIES or size > _BOX_DIR_MAX_BYTES:
            logger.warning(
                "credential writeback skipped %s: more than %d entries or %d bytes",
                src, _BOX_DIR_MAX_ENTRIES, _BOX_DIR_MAX_BYTES)
            return True
    return False


def _read_box_file(
    root: Path, rel: str, action: str = "credential writeback",
) -> tuple[bytes, os.stat_result] | None:
    """Read the box file *root*/*rel* without following a link, or ``None``.

    The box controls everything under *root*, so each component is opened with
    ``O_NOFOLLOW`` relative to the last: a symlink anywhere below *root* is
    refused, as is anything but a regular file (``O_NONBLOCK`` keeps a FIFO from
    hanging the open), and so is a file over ``_BOX_FILE_MAX_BYTES``, by its size
    and by what a read returns, since the file can grow after the check.  A missing
    file is ``None`` with no warning; a refusal warns, naming the file and *action*.
    The bytes returned are the bytes checked and written.
    """
    parts = Path(rel).parts
    path = root / rel
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        why = "it or a directory above it is a symlink" if exc.errno in (
            errno.ELOOP, errno.ENOTDIR) else exc.strerror
        logger.warning("%s skipped %s: %s", action, path, why)
        return None
    finally:
        os.close(fd)
    try:
        st = os.fstat(leaf)
        if not stat.S_ISREG(st.st_mode):
            logger.warning("%s skipped %s: not a regular file", action, path)
            return None
        if st.st_size <= _BOX_FILE_MAX_BYTES:
            with os.fdopen(leaf, "rb", closefd=False) as fh:
                data = fh.read(_BOX_FILE_MAX_BYTES + 1)
            if len(data) <= _BOX_FILE_MAX_BYTES:
                return data, st
        logger.warning("%s skipped %s: larger than %d bytes",
                       action, path, _BOX_FILE_MAX_BYTES)
        return None
    except OSError as exc:
        logger.warning("%s skipped %s: %s", action, path, exc)
        return None
    finally:
        os.close(leaf)


def _replace_store_file(
    spec: CredFileSpec, target: Target, data: bytes,
    box_stat: os.stat_result, dst: Path,
) -> None:
    """Write checked box *data* over the store file *dst*, keeping one backup.

    The new content is built in a private temp dir beside the file *dst* names (a
    store file the user made a symlink stays one) and renamed over it, so the store
    is never written in place.  Unfiltered: the new content is *data*, with the box
    file's times as ``copy2`` carried them.  Filtered: the plugin's
    ``transform_cred(..., "out")`` reads a copy of *data*, never the box file, and
    writes over a copy of the store.  An existing store keeps its mode; a new one is
    0600.  Unchanged content leaves the store alone (its inode and times, which the
    refresh's mtime gate reads).  Changed content first saves the previous content
    as ``<dst>.bak`` (0600), replacing any older one; if that fails, the store is
    left as it was.
    """
    real = Path(os.path.realpath(dst))
    old_stat = real.stat() if real.is_file() else None
    old = real.read_bytes() if old_stat is not None else None
    work = Path(tempfile.mkdtemp(prefix=f".{real.name}.", suffix=".writeback",
                                 dir=real.parent))
    try:
        new = work / "out"
        if spec.filtered:
            box_copy = work / "in"
            box_copy.write_bytes(data)
            os.utime(box_copy, ns=(box_stat.st_atime_ns, box_stat.st_mtime_ns))
            if old is not None:
                new.write_bytes(old)
            target.transform_cred(spec, box_copy, new, "out")
            if not new.is_file():
                return
        else:
            new.write_bytes(data)
            os.utime(new, ns=(box_stat.st_atime_ns, box_stat.st_mtime_ns))
        if old is not None:
            if new.read_bytes() == old:
                return
            _write_backup(dst, old)
        os.chmod(new, stat.S_IMODE(old_stat.st_mode) if old_stat else 0o600)
        os.replace(new, real)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _write_backup(dst: Path, old: bytes) -> None:
    """Write *old* to ``<dst>.bak`` (0600) by temp and rename."""
    bak = dst.with_name(dst.name + ".bak")
    fd, tmp = tempfile.mkstemp(prefix=f".{bak.name}.", dir=dst.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(old)
        os.replace(tmp, bak)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- #
# Tier orchestrators — the 3-tier SHARING dispatch (auth-level design step 4)  #
# --------------------------------------------------------------------------- #
#
# The credsync PRIMITIVES above are the ONE uniform op ("sync a cred dir against a
# source root"). These orchestrators apply that op at the SELECTED tier per the
# resolved :class:`~kanibako.settings.settings_launch.AuthSource` (precedence workset >
# global > box):
#
#   * GLOBAL tier  → source_root = the host home (host_rel). One hop.
#   * WORKSET tier → source_root = the box's per-agent workset source dir
#     (meta.box.auth.workset_path; layout mirrors home_rel). PLUS, when global_sync is
#     on, the SAME primitive runs a SECOND hop between the workset dir and global —
#     the workset dir is just another sync client of global (design SYNC). Natural
#     ordering: refresh TOP-DOWN (global → workset → box), writeback BOTTOM-UP
#     (box → workset → global).
#   * BOX tier     → source_root = None → the primitives no-op (private/distinct).
#
# The workset dir is synced against global by treating it as ANOTHER "project
# home" (its layout mirrors home_rel — the SAME layout the box uses), with global
# (host home) as the source root — no new mechanism, the same primitive.


def selected_source_root(
    auth: AuthSource, *, host_home: Path
) -> Path | None:
    """The credential SOURCE root the box syncs its OWN cred files against.

    Per the resolved tier (precedence workset>global): the workset per-agent
    source dir for ``"workset"``, the host home for ``"global"``, ``None`` for the
    private ``"box"`` tier (the primitives no-op). The workset source is
    ``auth.workset_source`` (``meta.box.auth.workset_path`` — already resolved).
    """
    if auth.tier == "workset" and auth.workset_source is not None:
        return Path(auth.workset_source)
    if auth.tier == "global":
        return host_home
    return None


def _sync_workset_dir_from_global(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
) -> None:
    """TOP-DOWN hop: refresh the WORKSET auth dir FROM global (design SYNC).

    Runs only for the WORKSET tier with ``global_sync`` on. Treats the workset
    source dir as another sync client of global (host home) via the SAME
    ``refresh_cred_files`` primitive — the workset dir's layout mirrors ``home_rel``
    (the same layout a box uses), so the primitive applies unchanged.

    ⚑ Holds :func:`~kanibako.launch.creds_watcher.creds_store_lock` over the
    workset dir AND host home — the same pair a global-synced workset writeback
    locks. The workset dir because this WRITES it and must not interleave with a
    writeback into it; host home because this READS it, and a writeback into
    global rewrites those files in place, so an unlocked read can copy a torn file
    into the workset store.
    """
    if (
        auth.tier != "workset"
        or not auth.global_sync
        or auth.workset_source is None
    ):
        return
    workset_dir = Path(auth.workset_source)
    with creds_store_lock(workset_dir, host_home):
        refresh_cred_files(
            descriptor, target,
            source_root=host_home,
            project_home=workset_dir,
        )


def _sync_workset_dir_to_global(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
) -> bool:
    """BOTTOM-UP hop: write the WORKSET auth dir back TO global (design SYNC).

    Runs only for the WORKSET tier with ``global_sync`` on. The mirror of
    :func:`_sync_workset_dir_from_global` via ``writeback_cred_files`` (project =
    the workset dir, dest = global/host home).  ``False`` when a spec failed.
    """
    if (
        auth.tier != "workset"
        or not auth.global_sync
        or auth.workset_source is None
    ):
        return True
    return writeback_cred_files(
        descriptor, target,
        source_root=host_home,
        project_home=Path(auth.workset_source),
    )


def _box_from_source(
    primitive: Callable[..., None],
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
) -> None:
    """The BOX-level hop: run *primitive* (seed or refresh) from the selected source.

    ⚑ Holds :func:`~kanibako.launch.creds_watcher.creds_store_lock` over the
    source root for the read, the same directory a writeback into that store locks:
    a writeback rewrites those files in place, and a torn source file is as
    corrupting as a torn write. Worse here: ``copy2`` carries the source mtime into
    the box, so the mtime gate would skip every later refresh until the source
    changed again. The private tier (source root ``None``) reads no store and takes
    no lock.

    Enter it with NO store lock held, or it self-deadlocks (see
    ``creds_store_lock``); :func:`_sync_workset_dir_from_global` has released its
    lock before either orchestrator reaches this hop.
    """
    source_root = selected_source_root(auth, host_home=host_home)
    locked = () if source_root is None else (source_root,)
    with creds_store_lock(*locked):
        primitive(
            descriptor, target,
            source_root=source_root,
            project_home=project_home,
        )


def _create_workset_source_dirs(
    descriptor: PluginDescriptor, *, auth: AuthSource
) -> None:
    """Create the WORKSET credential SOURCE dirs the plugin owns (design step 5).

    Because the plugin OWNS the in-guest cred LAYOUT (``home_rel`` / ``init_dirs``),
    it also creates the matching WORKSET sync-source directories in that layout:
    the per-agent workset auth dir (``meta.box.auth.workset_path`` = the source root),
    its ``init_dirs`` substructure, and each cred file's parent dir. The GLOBAL
    source is NOT created (it is the user's real host login location). No-op unless
    the WORKSET tier is actually selected.

    ⚑ Gated on ``auth.tier == "workset"`` (NOT merely on ``workset_source is not
    None``): a non-workset box (standalone/global/private) must NEVER mkdir a
    workset source dir. The resolver already scrubs ``workset_source`` to None off
    the workset tier, but this tier gate is the primary guard — belt-and-braces so
    a stray resolved path can never mkdir against the host root.
    """
    if auth.tier != "workset" or auth.workset_source is None:
        return
    root = Path(auth.workset_source)
    root.mkdir(parents=True, exist_ok=True)
    for d in descriptor.init_dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for spec in descriptor.cred_files:
        (root / spec.host_rel).parent.mkdir(parents=True, exist_ok=True)


def _cred_descriptor(
    descriptor: PluginDescriptor, *, suppress_oauth: bool
) -> PluginDescriptor:
    """The descriptor view the cred lifecycle acts on — the PERSONA cred fork.

    THE single fail-safe fork (persona MVP block B): when *suppress_oauth* is set
    (the active agent resolved a non-``<None>`` ``agent.<node>.endpoint``, i.e. a
    persona pointing the harness at a THIRD-PARTY endpoint), the host-login cred sync
    is DROPPED — the descriptor's SYNC-cadence ``cred_files`` (claude's
    ``.claude/.credentials.json`` OAuth token) are filtered out so the user's
    Anthropic token is never delivered to a box that talks to a non-Anthropic
    endpoint. Fail-safe: a persona just has NO token here (block C supplies the
    bearer token); it is never a leak.

    When *suppress_oauth* is False (the ``endpoint is None`` bare case), the
    descriptor is returned UNCHANGED — byte-identical to today's cred lifecycle.
    Only SYNC specs are dropped; ``SEED_ONCE`` specs (static, non-login) survive.
    """
    if not suppress_oauth:
        return descriptor
    from dataclasses import replace
    kept = tuple(s for s in descriptor.cred_files if s.cadence is not Cadence.SYNC)
    return replace(descriptor, cred_files=kept)


def seed_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
    suppress_oauth: bool = False,
) -> None:
    """Seed a fresh box's creds from the SELECTED tier source (design step 4/5).

    Plugin-owned WORKSET source-dir creation runs first (design step 5 — the
    plugin owns the layout, so it creates the workset source dirs mirroring
    ``home_rel``). Then TOP-DOWN: refresh the workset dir from global (when the
    workset tier is global-synced), then seed the box FROM the selected source root
    (workset dir / host home / None). ``init_dirs`` in the box home are always
    created (even for the private tier, which seeds no cred content).

    *suppress_oauth* is the persona cred fork (see :func:`_cred_descriptor`): when
    set, the SYNC host-login creds are dropped so a custom-endpoint box never
    receives the Anthropic OAuth token. Default False = today's behavior unchanged.
    """
    descriptor = _cred_descriptor(descriptor, suppress_oauth=suppress_oauth)
    _create_workset_source_dirs(descriptor, auth=auth)
    _sync_workset_dir_from_global(
        descriptor, target, auth=auth, host_home=host_home
    )
    _box_from_source(
        seed_cred_files, descriptor, target,
        auth=auth, host_home=host_home, project_home=project_home,
    )


def refresh_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
    suppress_oauth: bool = False,
) -> None:
    """Pre-launch refresh from the SELECTED tier source (design step 4).

    TOP-DOWN: refresh the workset dir from global first (when global-synced), then
    refresh the box FROM the selected source root. A private box (tier ``"box"``)
    is a no-op (source_root None). The workset source dirs are ensured present
    (idempotent) so a box that adopted the workset tier after creation still finds
    its source layout.

    *suppress_oauth* is the persona cred fork (see :func:`_cred_descriptor`): when
    set, the SYNC host-login creds are dropped so a custom-endpoint box never
    receives the Anthropic OAuth token. Default False = today's behavior unchanged.
    """
    descriptor = _cred_descriptor(descriptor, suppress_oauth=suppress_oauth)
    _create_workset_source_dirs(descriptor, auth=auth)
    _sync_workset_dir_from_global(
        descriptor, target, auth=auth, host_home=host_home
    )
    _box_from_source(
        refresh_cred_files, descriptor, target,
        auth=auth, host_home=host_home, project_home=project_home,
    )


def writeback_box_credentials(
    descriptor: PluginDescriptor,
    target: Target,
    *,
    auth: AuthSource,
    host_home: Path,
    project_home: Path,
) -> bool:
    """Post-session writeback to the SELECTED tier source (design step 4).

    BOTTOM-UP: write the box back TO the selected source root first, then (for the
    global-synced workset tier) write the workset dir back UP to global. A private
    box is a no-op.  ``False`` when a spec failed in either hop.
    """
    ok = writeback_cred_files(
        descriptor, target,
        source_root=selected_source_root(auth, host_home=host_home),
        project_home=project_home,
    )
    return _sync_workset_dir_to_global(
        descriptor, target, auth=auth, host_home=host_home
    ) and ok
