"""THE one place host-side code does file operations on a box-writable path.

The rule: never follow a symlink at the leaf or at any parent below the box tree's
*root* -- a link the box planted could lead the host to a file outside the box.

Every operation takes ``(path, root)``.  *root* is the box tree the path lives under
(box home, shell dir, vault dir); it is the caller's, opened as given, and not itself
checked.  A link raises :class:`SymlinkRefusedError`; :func:`no_symlink_or_warn` is the
check for a site that skips the path with a warning instead.

How: *root* is opened, then each component below it with ``openat`` relative to the
last (``O_NOFOLLOW | O_DIRECTORY``), and the leaf with ``O_NOFOLLOW``.  Every operation
acts through those descriptors, so a box that swaps a component for a link while the
host works cannot redirect it; the swap makes the operation fail instead.  Writes go to
a temp file in the verified parent and are renamed over the leaf through the same
descriptor, so a leaf link is replaced, never written through.

Imports only the stdlib and ``log``, so core and the plugins can both import it
without a cycle.
"""

from __future__ import annotations

import contextlib
import errno
import os
import secrets
import shutil
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

from kanibako.log import get_logger

logger = get_logger(__name__)

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class SymlinkRefusedError(OSError):
    """*path* was not touched because *link* -- *path* or a parent of it -- is a symlink.

    An ``OSError`` with ``errno.ELOOP`` (what ``O_NOFOLLOW`` raises), so an existing
    ``except OSError`` skip path also skips it.
    """

    def __init__(self, path: Path, link: Path) -> None:
        super().__init__(errno.ELOOP, f"refusing to follow the symlink {link}", str(path))
        self.path = path
        self.link = link


# --- the walk ----------------------------------------------------------------------


def _parts(path: Path, root: Path) -> tuple[str, ...]:
    """*path*'s components below *root*; ValueError if it is not under *root*."""
    parts = Path(path).relative_to(root).parts
    if ".." in parts:
        raise ValueError(f"{path} climbs out of {root}")
    return parts


def _is_link_at(name: str, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _open_dir_at(name: str, dir_fd: int, path: Path, shown: Path, create: bool) -> int:
    """Open the directory *name* under *dir_fd* without following a link.

    A link raises SymlinkRefusedError(*path*, *shown*); any other failure re-raises
    with *shown* as its filename.  *create* makes a missing directory first.
    """
    try:
        return os.open(name, _DIR, dir_fd=dir_fd)
    except OSError as exc:
        err = exc
    if create and err.errno == errno.ENOENT:
        with contextlib.suppress(FileExistsError):
            os.mkdir(name, dir_fd=dir_fd)
        return _open_dir_at(name, dir_fd, path, shown, create=False)
    if _is_link_at(name, dir_fd):
        raise SymlinkRefusedError(path, shown) from err
    raise OSError(err.errno, err.strerror, str(shown)) from err


def _open_dirs(path: Path, root: Path, parts: tuple[str, ...], create: bool) -> int:
    """A descriptor on ``root/parts``, each component opened under the last."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for i, part in enumerate(parts):
            nxt = _open_dir_at(part, fd, path, root.joinpath(*parts[: i + 1]), create)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextlib.contextmanager
def _parent(path: Path, root: Path, *, create: bool = False) -> Iterator[tuple[int, str]]:
    """(verified parent descriptor, leaf name) for *path*; *create* makes parents."""
    path, root = Path(path), Path(root)
    parts = _parts(path, root)
    if not parts:
        raise ValueError(f"{path} is the box root itself")
    fd = _open_dirs(path, root, parts[:-1], create)
    try:
        yield fd, parts[-1]
    finally:
        os.close(fd)


@contextlib.contextmanager
def _dir(path: Path, root: Path) -> Iterator[int]:
    """A verified descriptor on the directory *path* itself (*path* may be *root*)."""
    path, root = Path(path), Path(root)
    fd = _open_dirs(path, root, _parts(path, root), create=False)
    try:
        yield fd
    finally:
        os.close(fd)


def _open_leaf(name: str, dir_fd: int, path: Path, flags: int, mode: int = 0o666) -> int:
    """``openat`` the leaf with ``O_NOFOLLOW``; a leaf link raises SymlinkRefusedError."""
    try:
        return os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise SymlinkRefusedError(path, path) from exc
        raise


def _refuse_leaf_link(name: str, dir_fd: int, path: Path) -> None:
    if _is_link_at(name, dir_fd):
        raise SymlinkRefusedError(path, path)


# --- the check ---------------------------------------------------------------------


def first_symlink(path: Path, root: Path) -> Path | None:
    """The first symlink from *root* (exclusive) down to *path* (inclusive), or None.

    "First" is the one nearest *root*: the walk opens each component in turn and
    stops at the first link.  Errors never raise; what each returns:

    - ENOENT or ENOTDIR (a component or the leaf is missing, or a file stands where
      a directory should): None -- nothing there is a link.
    - EACCES (a directory that cannot be opened or searched): that directory, or
      *path* when only the leaf cannot be probed -- fail closed, as if a link.

    ``path == root`` returns None.  A *path* not under *root* raises ValueError:
    that is a caller bug, not a box state.  This is a CHECK; it can go stale before
    the caller acts, so an operation must still go through this module.
    """
    path, root = Path(path), Path(root)
    if not _parts(path, root):
        return None
    try:
        with _parent(path, root) as (fd, leaf):
            try:
                mode = os.stat(leaf, dir_fd=fd, follow_symlinks=False).st_mode
            except (FileNotFoundError, NotADirectoryError):
                return None
            except OSError:
                return path
            return path if stat.S_ISLNK(mode) else None
    except SymlinkRefusedError as exc:
        return exc.link
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        return Path(exc.filename) if exc.filename else path


def no_symlink_or_warn(path: Path, root: Path) -> bool:
    """True if :func:`first_symlink` finds no link; else one WARNING and False.

    For a host site that skips the path and carries on.  The warning names *path*,
    the link, and the link's target when it can be read.
    """
    path = Path(path)
    link = first_symlink(path, root)
    if link is None:
        return True
    try:
        target = os.readlink(link)
    except OSError:
        logger.warning("skipped %s: %s could not be checked for a symlink", path, link)
        return False
    logger.warning(
        "skipped %s: %s is a symlink to %r, and kanibako does not follow symlinks "
        "in a box", path, link, target,
    )
    return False


# --- reads and writes --------------------------------------------------------------


def _regular(fd: int, path: Path) -> os.stat_result:
    """fstat *fd*; close it and raise ``OSError(EINVAL)`` unless a regular file."""
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OSError(errno.EINVAL, "not a regular file", str(path))
    return st


def _open_read(path: Path, root: Path | None) -> int:
    """A read descriptor on the regular file *path*.

    ``O_NONBLOCK`` so a FIFO cannot hang the open.  *root* None marks a path that is
    not box-writable; it is opened as ``open`` would.
    """
    flags = os.O_RDONLY | os.O_NONBLOCK
    if root is None:
        fd = os.open(path, flags | os.O_CLOEXEC)
    else:
        with _parent(path, root) as (pfd, leaf):
            fd = _open_leaf(leaf, pfd, path, flags)
    _regular(fd, path)
    return fd


def _replace_at(
    dir_fd: int, leaf: str, fill: Callable[[BinaryIO], object], mode: int,
    times: tuple[int, int] | None = None,
) -> None:
    """Write a temp file in *dir_fd*, then rename it over *leaf* in the same *dir_fd*."""
    while True:
        tmp = f".{leaf}.{secrets.token_hex(4)}.tmp"
        try:
            fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600, dir_fd=dir_fd,
            )
            break
        except FileExistsError:
            continue
    try:
        with os.fdopen(fd, "wb") as fh:
            fill(fh)
            fh.flush()
            os.fchmod(fd, mode)
            if times is not None:
                os.utime(fd, ns=times)
            os.fsync(fd)
        os.rename(tmp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise


@contextlib.contextmanager
def _write_parent(path: Path, root: Path | None) -> Iterator[tuple[int, str]]:
    """The parent descriptor to write *path* in, parents created; leaf link refused."""
    path = Path(path)
    if root is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            yield fd, path.name
        finally:
            os.close(fd)
        return
    with _parent(path, root, create=True) as (fd, leaf):
        _refuse_leaf_link(leaf, fd, path)
        yield fd, leaf


def read_bytes(path: Path, root: Path) -> bytes:
    """The bytes of the regular file *path*; anything else raises ``OSError``."""
    path = Path(path)
    with os.fdopen(_open_read(path, root), "rb") as fh:
        return fh.read()


def read_text(path: Path, root: Path, encoding: str = "utf-8") -> str:
    """:func:`read_bytes`, decoded."""
    return read_bytes(path, root).decode(encoding)


def write_bytes(path: Path, root: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomically replace *path* with *data* at *mode*; missing parents are created."""
    with _write_parent(path, root) as (fd, leaf):
        _replace_at(fd, leaf, lambda fh: fh.write(data), mode)


def write_text(path: Path, root: Path, data: str, mode: int = 0o600) -> None:
    """:func:`write_bytes` for text, encoded as UTF-8."""
    write_bytes(path, root, data.encode("utf-8"), mode)


def copy_file(
    src: Path, dst: Path, *, src_root: Path | None, dst_root: Path | None,
) -> None:
    """Copy the regular file *src* to *dst*: contents, mode bits, and times.

    ``shutil.copy2`` without the xattrs, and *dst* replaced atomically.  A ``None``
    root marks a side that is not box-writable, which is opened as given.
    """
    src, dst = Path(src), Path(dst)
    with os.fdopen(_open_read(src, src_root), "rb") as fh:
        st = os.fstat(fh.fileno())
        with _write_parent(dst, dst_root) as (fd, leaf):
            _replace_at(
                fd, leaf, lambda out: shutil.copyfileobj(fh, out),
                stat.S_IMODE(st.st_mode), (st.st_atime_ns, st.st_mtime_ns),
            )


def _copy_dir(sfd: int, dfd: int, shown: Path) -> None:
    for name in os.listdir(sfd):
        st = os.stat(name, dir_fd=sfd, follow_symlinks=False)
        times = (st.st_atime_ns, st.st_mtime_ns)
        if stat.S_ISLNK(st.st_mode):
            os.symlink(os.readlink(name, dir_fd=sfd), name, dir_fd=dfd)
        elif stat.S_ISDIR(st.st_mode):
            cs = _open_dir_at(name, sfd, shown / name, shown / name, create=False)
            try:
                os.mkdir(name, 0o700, dir_fd=dfd)
                cd = os.open(name, _DIR, dir_fd=dfd)
                try:
                    _copy_dir(cs, cd, shown / name)
                    os.fchmod(cd, stat.S_IMODE(st.st_mode))
                    os.utime(cd, ns=times)
                finally:
                    os.close(cd)
            finally:
                os.close(cs)
        elif stat.S_ISREG(st.st_mode):
            fs = _open_leaf(name, sfd, shown / name, os.O_RDONLY | os.O_NONBLOCK)
            _regular(fs, shown / name)
            with os.fdopen(fs, "rb") as fin:
                fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=dfd,
                )
                with os.fdopen(fd, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
                    fout.flush()
                    os.fchmod(fd, stat.S_IMODE(st.st_mode))
                    os.utime(fd, ns=times)
        else:
            logger.warning("not copied %s: not a file, directory, or symlink", shown / name)


def copy_tree(
    src: Path, dst: Path, *, src_root: Path | None, dst_root: Path | None,
) -> None:
    """Copy the directory *src* to the new directory *dst* (``FileExistsError`` if it exists).

    Links INSIDE the tree are neither refused nor skipped: each is copied as a link,
    its text verbatim, never followed (the ``tree_copy`` rule).  FIFOs, sockets, and
    devices are skipped with a warning.  Files and directories keep their mode bits
    and times.  ``None`` roots as for :func:`copy_file`.

    No link is ever followed: every entry is listed, opened, and created through
    descriptors with ``O_NOFOLLOW``.  The residual race: an entry the box swaps
    between its ``lstat`` and its open makes the copy raise part-way, leaving a
    partial *dst*.
    """
    src, dst = Path(src), Path(dst)
    with contextlib.ExitStack() as stack:
        if src_root is None:
            sfd = os.open(src, _DIR)
            stack.callback(os.close, sfd)
        else:
            sfd = stack.enter_context(_dir(src, src_root))
        pfd, leaf = stack.enter_context(_write_parent(dst, dst_root))
        st = os.fstat(sfd)
        os.mkdir(leaf, 0o700, dir_fd=pfd)
        dfd = os.open(leaf, _DIR, dir_fd=pfd)
        stack.callback(os.close, dfd)
        _copy_dir(sfd, dfd, src)
        os.fchmod(dfd, stat.S_IMODE(st.st_mode))
        os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))


# --- metadata and removal ----------------------------------------------------------


def touch(path: Path, root: Path, mode: int = 0o666) -> None:
    """Create *path* (at *mode*, less the umask) if missing; set its times to now."""
    path = Path(path)
    with _parent(path, root) as (pfd, leaf):
        fd = _open_leaf(leaf, pfd, path, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK, mode)
    try:
        os.utime(fd)
    finally:
        os.close(fd)


def unlink(path: Path, root: Path, *, missing_ok: bool = False) -> None:
    """Remove the file *path*.  A link at *path* is refused too, not removed."""
    path = Path(path)
    try:
        with _parent(path, root) as (pfd, leaf):
            _refuse_leaf_link(leaf, pfd, path)
            os.unlink(leaf, dir_fd=pfd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def rmtree_contents(path: Path, root: Path) -> None:
    """Remove everything inside the directory *path* (which may be *root*), keeping it.

    Removal goes through descriptors (``shutil.rmtree`` with ``dir_fd``): a link
    inside is removed as a link, never followed.
    """
    with _dir(path, root) as dfd:
        for name in os.listdir(dfd):
            if stat.S_ISDIR(os.stat(name, dir_fd=dfd, follow_symlinks=False).st_mode):
                shutil.rmtree(name, dir_fd=dfd)
            else:
                os.unlink(name, dir_fd=dfd)


def _chmod_fd(fd: int, mode: int, path: Path) -> None:
    """chmod through an ``O_PATH | O_NOFOLLOW`` descriptor (Linux has no ``lchmod``)."""
    if stat.S_ISLNK(os.fstat(fd).st_mode):
        raise SymlinkRefusedError(path, path)
    os.chmod(f"/proc/self/fd/{fd}", mode)


def chmod(path: Path, root: Path, mode: int) -> None:
    """Set the mode bits of *path*."""
    path = Path(path)
    with _parent(path, root) as (pfd, leaf):
        fd = _open_leaf(leaf, pfd, path, os.O_PATH)
    try:
        _chmod_fd(fd, mode, path)
    finally:
        os.close(fd)


def chmod_tree(path: Path, root: Path, *, add: int = 0, remove: int = 0) -> None:
    """``mode | add & ~remove`` on the directory *path* and everything under it.

    Links inside are skipped, never followed.  A directory gets *add* before its
    entries are walked and *remove* after, so ``remove`` may drop its own search
    bit.  The residual race is :func:`copy_tree`'s: a swap raises part-way.
    """
    def walk(dfd: int, shown: Path) -> None:
        mode = stat.S_IMODE(os.fstat(dfd).st_mode)
        os.fchmod(dfd, mode | add)
        for name in os.listdir(dfd):
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                cd = _open_dir_at(name, dfd, shown / name, shown / name, create=False)
                try:
                    walk(cd, shown / name)
                finally:
                    os.close(cd)
            elif not stat.S_ISLNK(st.st_mode):
                fd = _open_leaf(name, dfd, shown / name, os.O_PATH)
                try:
                    _chmod_fd(fd, (stat.S_IMODE(st.st_mode) | add) & ~remove, shown / name)
                finally:
                    os.close(fd)
        os.fchmod(dfd, (mode | add) & ~remove)

    with _dir(path, root) as top:
        walk(top, Path(path))


def rename(src: Path, dst: Path, root: Path) -> None:
    """Rename *src* to *dst*, both under *root*; a link at either end is refused."""
    src, dst = Path(src), Path(dst)
    with _parent(src, root) as (sfd, sleaf), _parent(dst, root) as (dfd, dleaf):
        _refuse_leaf_link(sleaf, sfd, src)
        _refuse_leaf_link(dleaf, dfd, dst)
        os.rename(sleaf, dleaf, src_dir_fd=sfd, dst_dir_fd=dfd)
