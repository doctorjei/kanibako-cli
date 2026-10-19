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
import functools
import os
import secrets
import shutil
import stat as _st
import sys
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
    return _st.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
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
    That directory may be *root* itself: a root that cannot be opened is
    returned, the one case where *root* comes back.

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
      return path if _st.S_ISLNK(mode) else None
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
    "skipped %s: %s is a symlink to %r, and kanibako does not follow symlinks in a box",
    path,
    link,
    target,
  )
  return False


# --- reads and writes --------------------------------------------------------------


def _regular(fd: int, path: Path) -> os.stat_result:
  """fstat *fd*; close it and raise ``OSError(EINVAL)`` unless a regular file."""
  st = os.fstat(fd)
  if not _st.S_ISREG(st.st_mode):
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
  dir_fd: int,
  leaf: str,
  fill: Callable[[BinaryIO], object],
  mode: int,
  times: tuple[int, int] | None = None,
) -> None:
  """Write a temp file in *dir_fd*, then rename it over *leaf* in the same *dir_fd*."""
  while True:
    tmp = f".{leaf}.{secrets.token_hex(4)}.tmp"
    try:
      fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dir_fd,
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


def read_bytes_with_stat(path: Path, root: Path) -> tuple[bytes, os.stat_result]:
  """The bytes of the regular file *path* and its stat, both from the same descriptor.

  Anything but a regular file raises ``OSError``.
  """
  path = Path(path)
  with os.fdopen(_open_read(path, root), "rb") as fh:
    st = os.fstat(fh.fileno())
    return fh.read(), st


def read_bytes(path: Path, root: Path) -> bytes:
  """The bytes of the regular file *path*; anything else raises ``OSError``."""
  return read_bytes_with_stat(path, root)[0]


def read_text(path: Path, root: Path, encoding: str = "utf-8") -> str:
  """:func:`read_bytes`, decoded."""
  return read_bytes(path, root).decode(encoding)


def _kept_mode(dir_fd: int, leaf: str) -> int:
  """The mode of the regular file *leaf* already there, else 0o600."""
  try:
    st = os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False)
  except FileNotFoundError:
    return 0o600
  return _st.S_IMODE(st.st_mode) if _st.S_ISREG(st.st_mode) else 0o600


def write_bytes(
  path: Path,
  root: Path,
  data: bytes,
  mode: int | None = None,
  times: tuple[int, int] | None = None,
) -> None:
  """Atomically replace *path* with *data*; missing parents are created.

  *mode* None keeps an existing file's mode; a new file gets 0o600.  *times* is
  ``(atime_ns, mtime_ns)`` set on the new file before it is renamed into place;
  None leaves them at now.
  """
  with _write_parent(path, root) as (fd, leaf):
    _replace_at(
      fd,
      leaf,
      lambda fh: fh.write(data),
      _kept_mode(fd, leaf) if mode is None else mode,
      times,
    )


def write_text(
  path: Path,
  root: Path,
  data: str,
  mode: int | None = None,
  times: tuple[int, int] | None = None,
) -> None:
  """:func:`write_bytes` for text, encoded as UTF-8."""
  write_bytes(path, root, data.encode("utf-8"), mode, times)


def listdir(path: Path, root: Path) -> list[str]:
  """The entry names of the directory *path* (which may be *root*), read through
  its verified descriptor.  Entries are not classified; a link among them is
  just a name, and acting on it goes through this module again.
  """
  with _dir(path, root) as fd:
    return os.listdir(fd)


def stat(path: Path, root: Path) -> os.stat_result:
  """The stat of *path* itself; a link at it or a parent raises SymlinkRefusedError.

  ``fstat`` on an ``O_PATH | O_NOFOLLOW`` descriptor.  A missing *path* raises
  ``FileNotFoundError``.
  """
  path = Path(path)
  with _parent(path, root) as (pfd, leaf):
    fd = _open_leaf(leaf, pfd, path, os.O_PATH)
  try:
    st = os.fstat(fd)
  finally:
    os.close(fd)
  if _st.S_ISLNK(st.st_mode):
    raise SymlinkRefusedError(path, path)
  return st


def mkdir(
  path: Path,
  root: Path,
  *,
  parents: bool = False,
  exist_ok: bool = False,
  mode: int = 0o755,
) -> None:
  """Make the directory *path* at *mode* (less the umask).

  *parents* makes missing parents (default mode); *exist_ok* accepts an existing
  directory, but never a link or a file at *path*.
  """
  path = Path(path)
  with _parent(path, root, create=parents) as (pfd, leaf):
    try:
      os.mkdir(leaf, mode, dir_fd=pfd)
    except FileExistsError:
      st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
      if _st.S_ISLNK(st.st_mode):
        raise SymlinkRefusedError(path, path) from None
      if not (exist_ok and _st.S_ISDIR(st.st_mode)):
        raise


def copy_file(
  src: Path,
  dst: Path,
  *,
  src_root: Path | None,
  dst_root: Path | None,
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
        fd,
        leaf,
        lambda out: shutil.copyfileobj(fh, out),
        _st.S_IMODE(st.st_mode),
        (st.st_atime_ns, st.st_mtime_ns),
      )


def _not_copied(skipped: list[Path], path: Path, why: str) -> None:
  logger.warning("not copied %s: %s", path, why)
  skipped.append(path)


def _copy_entries(
  sfd: int,
  dfd: int,
  src: Path,
  dst: Path,
  skip_links: bool,
  avoid: tuple[int, int],
  follow_src: bool = False,
) -> list[Path]:
  """Copy the entries of *sfd* into *dfd*; return the source paths not copied.

  An explicit stack, not recursion: depth is bounded by open descriptors, and
  running out raises ``OSError`` (EMFILE).  A directory whose (dev, ino) is
  *avoid* -- the destination itself, when it lies under the source -- is not
  entered.  *follow_src* reads a source link as what it points at; a directory
  link back to one being copied is not entered, with a warning.
  """
  skipped: list[Path] = []
  src_st = os.fstat(sfd)
  open_dirs = {(src_st.st_dev, src_st.st_ino)}
  # Frame: (src fd, dst fd, names left, src path, dst path, src dir stat or None).
  stack: list[tuple[int, int, Iterator[str], Path, Path, os.stat_result | None]] = [
    (sfd, dfd, iter(os.listdir(sfd)), src, dst, None),
  ]
  try:
    while stack:
      cs, cd, names, s, d, dir_st = stack[-1]
      name = next(names, None)
      if name is None:
        stack.pop()
        if dir_st is not None:
          open_dirs.discard((dir_st.st_dev, dir_st.st_ino))
          try:
            os.fchmod(cd, _st.S_IMODE(dir_st.st_mode))
            os.utime(cd, ns=(dir_st.st_atime_ns, dir_st.st_mtime_ns))
          finally:
            os.close(cs)
            os.close(cd)
        continue
      st = os.stat(name, dir_fd=cs, follow_symlinks=False)
      linked = follow_src and _st.S_ISLNK(st.st_mode)
      if linked:
        try:
          st = os.stat(name, dir_fd=cs)
        except OSError as exc:
          _not_copied(skipped, s / name, f"its link cannot be followed ({exc.strerror})")
          continue
      try:
        there: os.stat_result | None = os.stat(name, dir_fd=cd, follow_symlinks=False)
      except FileNotFoundError:
        there = None
      if there is not None and _st.S_ISLNK(there.st_mode):
        _not_copied(skipped, s / name, f"{d / name} is a symlink, left untouched")
      elif _st.S_ISLNK(st.st_mode):
        if skip_links:
          _not_copied(skipped, s / name, "it is a symlink")
        elif there is not None:
          _not_copied(skipped, s / name, f"{d / name} already exists")
        else:
          os.symlink(os.readlink(name, dir_fd=cs), name, dir_fd=cd)
      elif _st.S_ISDIR(st.st_mode):
        if (st.st_dev, st.st_ino) == avoid:
          logger.debug("not copied %s: it is the destination", s / name)
          continue
        if there is not None and not _st.S_ISDIR(there.st_mode):
          _not_copied(skipped, s / name, f"{d / name} is not a directory")
          continue
        if (st.st_dev, st.st_ino) in open_dirs:
          _not_copied(skipped, s / name, "it links back to a directory being copied")
          continue
        if linked:
          child_s = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=cs)
        else:
          child_s = _open_dir_at(name, cs, s / name, s / name, create=False)
        try:
          if there is None:
            os.mkdir(name, 0o700, dir_fd=cd)
          child_d = _open_dir_at(name, cd, d / name, d / name, create=False)
        except BaseException:
          os.close(child_s)
          raise
        open_dirs.add((st.st_dev, st.st_ino))
        stack.append((child_s, child_d, iter(os.listdir(child_s)), s / name, d / name, st))
      elif _st.S_ISREG(st.st_mode):
        if there is not None and not _st.S_ISREG(there.st_mode):
          _not_copied(skipped, s / name, f"{d / name} is not a regular file")
          continue
        if linked:
          fs = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=cs)
        else:
          fs = _open_leaf(name, cs, s / name, os.O_RDONLY | os.O_NONBLOCK)
        _regular(fs, s / name)
        with os.fdopen(fs, "rb") as fin:
          _replace_at(
            cd,
            name,
            functools.partial(shutil.copyfileobj, fin),
            _st.S_IMODE(st.st_mode),
            (st.st_atime_ns, st.st_mtime_ns),
          )
      else:
        _not_copied(skipped, s / name, "not a file, directory, or symlink")
  finally:
    for cs, cd, _, _, _, dir_st in stack:
      if dir_st is not None:
        os.close(cs)
        os.close(cd)
  return skipped


def copy_tree(
  src: Path,
  dst: Path,
  *,
  src_root: Path | None,
  dst_root: Path | None,
  merge: bool = False,
  skip_links: bool = False,
  follow_src_links: bool = False,
) -> list[Path]:
  """Copy the directory *src* to *dst*; return the source paths not copied.

  Without *merge*, *dst* must not exist (``FileExistsError``).  With it, *src* is
  merged into an existing *dst*: a file replaces the one there atomically, through
  the destination's verified descriptors.

  Links: a link INSIDE *src* is copied as a link, its text verbatim, never
  followed (the ``tree_copy`` rule) -- or, with *skip_links*, not copied, with one
  warning each.  A link already in *dst* is REFUSED: left untouched, never
  followed or replaced, with a warning.  A name whose kind differs between the two
  sides (file vs. directory), or a link meeting an existing entry, is likewise
  left with a warning.  FIFOs, sockets, and devices are not copied, with a warning.
  Every entry not copied is in the returned list.  Files and directories keep
  their mode bits and times.  ``None`` roots as for :func:`copy_file`.

  *follow_src_links* reads a HOST source (*src_root* ``None``; else ValueError) as
  ``shutil.copytree`` does: *src* and every link inside it are followed, and what
  they point at is copied.  A dangling link, or a directory link back to a
  directory being copied, is not copied, with a warning.  The *dst* side is
  unchanged: a link there is never followed.

  A *dst* inside *src* is not copied into itself (skipped by dev/ino); *dst* equal
  to *src* raises ``OSError(EINVAL)``.

  Without *follow_src_links*, no link is ever followed: every entry is listed,
  opened, and created through descriptors with ``O_NOFOLLOW``.  The residual race: an entry the box swaps
  between its ``lstat`` and its open makes the copy raise part-way, leaving a
  partial *dst*.
  """
  src, dst = Path(src), Path(dst)
  if follow_src_links and src_root is not None:
    raise ValueError(f"follow_src_links reads a host source; {src} is under a box tree")
  with contextlib.ExitStack() as stack:
    if follow_src_links:
      sfd = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
      stack.callback(os.close, sfd)
    elif src_root is None:
      sfd = os.open(src, _DIR)
      stack.callback(os.close, sfd)
    else:
      sfd = stack.enter_context(_dir(src, src_root))
    pfd, leaf = stack.enter_context(_write_parent(dst, dst_root))
    st = os.fstat(sfd)
    try:
      os.mkdir(leaf, 0o700, dir_fd=pfd)
    except FileExistsError:
      if not merge:
        raise
    dfd = _open_dir_at(leaf, pfd, dst, dst, create=False)
    stack.callback(os.close, dfd)
    dst_st = os.fstat(dfd)
    if (dst_st.st_dev, dst_st.st_ino) == (st.st_dev, st.st_ino):
      raise OSError(errno.EINVAL, "cannot copy a directory onto itself", str(dst))
    skipped = _copy_entries(
      sfd,
      dfd,
      src,
      dst,
      skip_links,
      (dst_st.st_dev, dst_st.st_ino),
      follow_src_links,
    )
    os.fchmod(dfd, _st.S_IMODE(st.st_mode))
    os.utime(dfd, ns=(st.st_atime_ns, st.st_mtime_ns))
    return skipped


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
  """Remove the file *path*; a link at *path* is removed itself (never followed)."""
  path = Path(path)
  try:
    with _parent(path, root) as (pfd, leaf):
      os.unlink(leaf, dir_fd=pfd)
  except FileNotFoundError:
    if not missing_ok:
      raise


def _remove_at(name: str, dir_fd: int, shown: Path, st: os.stat_result | None = None) -> None:
  """Remove the entry *name* under *dir_fd*: a directory with everything in it, else
  the entry itself (a link is removed as a link, never followed).

  *st* is the entry's lstat when the caller already has it.  Directories go
  through ``shutil.rmtree`` with ``dir_fd``; its errors re-raise naming the full
  path under *shown* (a link swapped in at *name* raises SymlinkRefusedError), and
  its recursion depth limit (Python < 3.12) becomes ``OSError(EOVERFLOW)``.
  """
  if st is None:
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
  if not _st.S_ISDIR(st.st_mode):
    os.unlink(name, dir_fd=dir_fd)
    return

  def failed(_func: object, where: str, exc: BaseException) -> None:
    # shutil names *where* relative to *dir_fd*, starting with *name*.
    if where == name and _is_link_at(name, dir_fd):
      raise SymlinkRefusedError(shown, shown) from exc
    err = exc if isinstance(exc, OSError) else OSError(str(exc))
    why = err.strerror or next((a for a in err.args if isinstance(a, str)), "rmtree failed")
    raise OSError(err.errno, why, str(shown.parent / where)) from exc

  try:
    if sys.version_info >= (3, 12):
      shutil.rmtree(name, dir_fd=dir_fd, onexc=failed)
    else:  # ``onerror`` hands over sys.exc_info()
      shutil.rmtree(name, dir_fd=dir_fd, onerror=lambda f, w, i: failed(f, w, i[1]))
  except RecursionError as exc:  # the recursive rmtree of Python < 3.12
    raise OSError(errno.EOVERFLOW, "directory tree too deep", str(shown)) from exc


def _forced(remove: Callable[[], None], path: Path, root: Path, force: bool) -> None:
  """Run *remove*; with *force*, a ``PermissionError`` gives the owner rwx on the
  tree at *path* (:func:`chmod_tree`) and runs it once more."""
  try:
    remove()
  except PermissionError:
    if not force:
      raise
    chmod_tree(path, root, add=_st.S_IRWXU)
    remove()


def rmtree_contents(path: Path, root: Path, *, force: bool = False) -> None:
  """Remove everything inside the directory *path* (which may be *root*), keeping it.

  Removal goes through descriptors (``shutil.rmtree`` with ``dir_fd``): a link
  inside is removed as a link, never followed.  *force*: a ``PermissionError`` (a
  read-only directory in the tree) gives the owner rwx on the whole tree, files
  included, and removes once more.  ⚑ Not for a tree whose files share inodes with
  a tree that is kept (a hardlink snapshot): their modes would change too.
  """
  path = Path(path)

  def remove() -> None:
    with _dir(path, root) as dfd:
      for name in os.listdir(dfd):
        _remove_at(name, dfd, path / name)

  _forced(remove, path, root, force)


def rmtree(
  path: Path,
  root: Path,
  *,
  missing_ok: bool = False,
  force: bool = False,
) -> None:
  """Remove the directory *path* and everything in it, through its verified parent.

  A link at *path* is removed as a link (as :func:`unlink` does), never descended;
  a file at *path* is removed too.  Inside, removal is :func:`rmtree_contents`'s,
  *force* included.  *path* equal to *root* raises ``ValueError``: the root is the
  caller's, so use :func:`rmtree_contents` to empty it.  *missing_ok* accepts only
  a missing *path* or parent; an entry that vanishes inside the tree mid-walk still
  raises.
  """
  path = Path(path)

  def remove() -> None:
    with contextlib.ExitStack() as stack:
      try:
        pfd, leaf = stack.enter_context(_parent(path, root))
        st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
      except FileNotFoundError:
        if missing_ok:
          return
        raise
      _remove_at(leaf, pfd, path, st)

  _forced(remove, path, root, force)


def rmdir(path: Path, root: Path) -> None:
  """Remove the EMPTY directory *path* through its verified parent.

  ``rmdir`` is the emptiness test, so an entry made after the caller's check is
  never removed with it (OSError, ENOTEMPTY).  A link at *path* is not removed or
  followed (``NotADirectoryError``); a link in a parent raises SymlinkRefusedError.
  """
  path = Path(path)
  with _parent(path, root) as (pfd, leaf):
    os.rmdir(leaf, dir_fd=pfd)


def _chmod_fd(fd: int, mode: int, path: Path) -> None:
  """chmod through an ``O_PATH | O_NOFOLLOW`` descriptor (Linux has no ``lchmod``)."""
  if _st.S_ISLNK(os.fstat(fd).st_mode):
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
  with _dir(path, root) as top:
    # Frame: (dir fd, names left, dir path, original mode); an explicit stack.
    top_mode = _st.S_IMODE(os.fstat(top).st_mode)
    os.fchmod(top, top_mode | add)
    stack: list[tuple[int, Iterator[str], Path, int]] = [
      (top, iter(os.listdir(top)), Path(path), top_mode),
    ]
    try:
      while stack:
        dfd, names, shown, mode = stack[-1]
        name = next(names, None)
        if name is None:
          stack.pop()
          try:
            os.fchmod(dfd, (mode | add) & ~remove)
          finally:
            if dfd != top:
              os.close(dfd)
          continue
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        if _st.S_ISDIR(st.st_mode):
          cd = _open_dir_at(name, dfd, shown / name, shown / name, create=False)
          try:
            os.fchmod(cd, _st.S_IMODE(st.st_mode) | add)
            names_in = iter(os.listdir(cd))
          except BaseException:
            os.close(cd)
            raise
          stack.append((cd, names_in, shown / name, _st.S_IMODE(st.st_mode)))
        elif not _st.S_ISLNK(st.st_mode):
          fd = _open_leaf(name, dfd, shown / name, os.O_PATH)
          try:
            _chmod_fd(fd, (_st.S_IMODE(st.st_mode) | add) & ~remove, shown / name)
          finally:
            os.close(fd)
    finally:
      for dfd, *_ in stack:
        if dfd != top:
          os.close(dfd)


def rename(
  src: Path,
  dst: Path,
  root: Path,
  *,
  dst_root: Path | None = None,
  across_devices: bool = False,
) -> None:
  """Rename *src* to *dst*, both under *root* (*dst* under *dst_root* when given); a
  link in a parent is refused.

  ``renameat`` never follows a leaf: a link at *src* is moved itself, and a link
  at *dst* is replaced itself.

  *across_devices*: a rename refused with EXDEV moves as ``shutil.move`` does, but
  follows no link.  A leaf link is recreated at *dst* as a link, its text verbatim.
  A regular file is copied with its mode bits and times, replacing *dst* atomically.
  A directory is copied to an absent *dst* (:func:`copy_tree`).  Then *src* is
  removed.  A tree copy that fails or leaves anything out removes the partial
  *dst* and keeps *src*; one that leaves anything out raises ``OSError(EIO)``.
  Any other kind of entry re-raises the EXDEV.  Not atomic: an interrupt can leave
  the entry in both places, never in neither.
  """
  src, dst = Path(src), Path(dst)
  dst_root = root if dst_root is None else dst_root
  try:
    with _parent(src, root) as (sfd, sleaf), _parent(dst, dst_root) as (dfd, dleaf):
      os.rename(sleaf, dleaf, src_dir_fd=sfd, dst_dir_fd=dfd)
  except OSError as exc:
    if not across_devices or exc.errno != errno.EXDEV:
      raise
    _move_across(src, dst, root, dst_root, exc)


def _move_across(src: Path, dst: Path, root: Path, dst_root: Path, refused: OSError) -> None:
  """:func:`rename`'s EXDEV fallback: copy *src* to *dst* without following a link,
  then remove *src*."""
  with _parent(src, root) as (sfd, sleaf):
    st = os.stat(sleaf, dir_fd=sfd, follow_symlinks=False)
    text = os.readlink(sleaf, dir_fd=sfd) if _st.S_ISLNK(st.st_mode) else None
  if text is not None:
    with _parent(dst, dst_root) as (dfd, dleaf):
      while True:
        tmp = f".{dleaf}.{secrets.token_hex(4)}.tmp"
        try:
          os.symlink(text, tmp, dir_fd=dfd)
          break
        except FileExistsError:
          continue
      try:
        os.rename(tmp, dleaf, src_dir_fd=dfd, dst_dir_fd=dfd)
      except BaseException:
        with contextlib.suppress(OSError):
          os.unlink(tmp, dir_fd=dfd)
        raise
  elif _st.S_ISREG(st.st_mode):
    with os.fdopen(_open_read(src, root), "rb") as fh:
      fst = os.fstat(fh.fileno())
      with _parent(dst, dst_root) as (dfd, dleaf):
        _replace_at(
          dfd,
          dleaf,
          lambda out: shutil.copyfileobj(fh, out),
          _st.S_IMODE(fst.st_mode),
          (fst.st_atime_ns, fst.st_mtime_ns),
        )
  elif _st.S_ISDIR(st.st_mode):
    absent = not _lexists(dst, dst_root)
    try:
      skipped = copy_tree(src, dst, src_root=root, dst_root=dst_root)
    except BaseException:
      if absent:  # else *dst* is not ours to remove
        _drop_partial(dst, dst_root)
      raise
    if skipped:
      _drop_partial(dst, dst_root)
      raise OSError(
        errno.EIO,
        "could not move; not copied (a symlink that is not followed, or an entry "
        "that is not a file, directory, or symlink): " + ", ".join(map(str, skipped)),
        str(src),
      )
    rmtree(src, root)
    return
  else:
    raise refused
  unlink(src, root)


def _lexists(path: Path, root: Path) -> bool:
  """Whether anything, a link included, is at *path*; a missing parent is False."""
  try:
    with _parent(path, root) as (pfd, leaf):
      os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
  except FileNotFoundError:
    return False
  return True


def _drop_partial(dst: Path, dst_root: Path) -> None:
  """Remove the partial copy :func:`_move_across` made at *dst*; a failure is logged."""
  try:
    rmtree(dst, dst_root, missing_ok=True, force=True)
  except OSError as exc:
    logger.warning("could not remove the partial copy %s: %s", dst, exc)
