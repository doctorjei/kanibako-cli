"""Filesystem probes, literal paths, hashing, and standalone .gitignore maintenance."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys
from pathlib import Path

from kanibako.errors import ConfigError
from kanibako.settings.bootstrap import IGNORE_FILE, STANDALONE_META_DIR
from kanibako.settings.config_io import (
  append_doc_text_in_existing_parent,
  overwrite_doc_bytes_in_existing_parent,
  read_doc_bytes,
  read_doc_text,
  unlink_doc_file,
)

_GITIGNORE_ENTRIES = [f"{STANDALONE_META_DIR}/"]


def path_hash(project_path: str) -> str:
  """SHA-256 hex digest of the project path string."""
  return hashlib.sha256(project_path.encode()).hexdigest()


def link_write_refusal(path: Path, *internal_roots: Path) -> str | None:
  """Why kanibako must not write through *path*, or ``None`` when it may.

  A non-link is always fine.  A link is fine when it resolves under the user's
  home or under any of *internal_roots* -- the trees the caller owns, since a
  project need not live under home.  Anything else is refused: a dead link would
  be CREATED by ``open(..., "a")`` outside the tree, and a target outside is
  read-only to kanibako.  ``os.path.realpath`` stops at a loop instead of
  raising, so a loop reads as dead.
  """
  if not path.is_symlink():
    return None
  target = os.path.realpath(path)
  if not os.path.exists(target):
    return f"it links to {target}, which does not exist"
  for root in (Path.home(), *internal_roots):
    here = os.path.realpath(root)
    if target == here or target.startswith(here + os.sep):
      return None
  return f"it links outside the home directory and this project, to {target}"


def write_gitignore(project_path: Path) -> None:
  """Append the standalone box-metadata dir (box_data/) to the project .gitignore.

  A root ``.gitignore`` that links outside the home directory, or to nothing, is
  left alone; see :func:`link_write_refusal`.
  """
  gitignore = project_path / IGNORE_FILE
  refusal = link_write_refusal(gitignore, project_path)
  if refusal is not None:
    print(
      f"Note: did not write {gitignore} — {refusal}. Add "
      f"{', '.join(_GITIGNORE_ENTRIES)} to the file you want it in.",
      file=sys.stderr,
    )
    return

  existing = ""
  if gitignore.is_file():
    existing = read_doc_text(gitignore)

  lines_to_add = [entry for entry in _GITIGNORE_ENTRIES if entry not in existing.splitlines()]

  if not lines_to_add:
    return

  lead = "\n" if existing and not existing.endswith("\n") else ""
  append_doc_text_in_existing_parent(
    gitignore, lead + "".join(line + "\n" for line in lines_to_add)
  )


def unrestorable_link_note(path: Path) -> str:
  """The Note for a root file a create wrote through a link and cannot put back.

  The rooted seam opens leaves with ``O_NOFOLLOW``, so a link leaf is never
  captured and the undo has nothing to restore.  Say so rather than skip silently.
  """
  if path.name == IGNORE_FILE:
    return (
      f"left {path} in place — it is a link, so kanibako's "
      f"{', '.join(_GITIGNORE_ENTRIES)} line is yours to remove"
    )
  return f"left {path} in place — it is a link, so this create cannot restore it"


def gitignore_to_strip(project_path: Path) -> Path | None:
  """The root .gitignore holding a line :func:`write_gitignore` writes, else None.

  A symlinked or unreadable file is left alone with a Note.
  """
  gitignore = project_path / IGNORE_FILE
  if not (gitignore.is_file() or gitignore.is_symlink()):
    return None
  try:
    lines = read_doc_bytes(gitignore).decode("utf-8").splitlines()
  except (OSError, UnicodeDecodeError) as exc:
    print(
      f"Note: could not read {gitignore} ({getattr(exc, 'strerror', None) or exc}); "
      "any kanibako line in it is yours to remove.",
      file=sys.stderr,
    )
    return None
  if not any(entry in lines for entry in _GITIGNORE_ENTRIES):
    return None
  if gitignore.is_symlink():
    print(
      f"Note: left {gitignore} in place — it is a link, so kanibako's "
      f"{', '.join(_GITIGNORE_ENTRIES)} line is yours to remove.",
      file=sys.stderr,
    )
    return None
  return gitignore


def _gitignore_kept_text(gitignore: Path) -> str:
  text = read_doc_bytes(gitignore).decode("utf-8")
  return "".join(
    line
    for line in text.splitlines(keepends=True)
    if line.splitlines()[0] not in _GITIGNORE_ENTRIES
  )


def gitignore_holds_only_kanibako(gitignore: Path) -> bool:
  """True when stripping *gitignore* leaves only whitespace, so the file is deleted."""
  try:
    return not _gitignore_kept_text(gitignore).strip()
  except (OSError, UnicodeDecodeError):
    return False


def strip_gitignore(gitignore: Path) -> bool:
  """Drop kanibako's lines from *gitignore*; unlink it if only whitespace remains."""
  try:
    kept = _gitignore_kept_text(gitignore)
    if kept.strip():
      # in place: keeps its mode
      overwrite_doc_bytes_in_existing_parent(gitignore, kept.encode("utf-8"))
    else:
      unlink_doc_file(gitignore)
  except (OSError, UnicodeDecodeError) as exc:
    print(f"Warning: could not remove kanibako's line from {gitignore} ({exc}).", file=sys.stderr)
    return False
  return True


def stat_unless_gone(path: str | os.PathLike[str]) -> os.stat_result | None:
  """*path*'s stat, or ``None`` when it is DEFINITIVELY absent (``ENOENT``/``ENOTDIR``).

  ⚑ NOT ``Path.exists()``/``is_dir()``: they raise a bare ``PermissionError`` on an
  unreadable parent.  Any other failure refuses, naming *path*: a box kanibako cannot
  see is never "gone".
  """
  try:
    return os.stat(path)
  except OSError as exc:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
      return None
    raise unreadable_path_error(path, exc) from exc


def is_dir_unless_gone(path: str | os.PathLike[str]) -> bool:
  """Whether *path* is a directory; refuses (:func:`stat_unless_gone`) when it cannot tell."""
  st = stat_unless_gone(path)
  return st is not None and stat.S_ISDIR(st.st_mode)


def is_file_unless_gone(path: str | os.PathLike[str]) -> bool:
  """Whether *path* is a regular file; refuses (:func:`stat_unless_gone`) when it cannot tell.

  ⚑ NOT ``Path.is_file()``/``exists()``: they raise a bare ``PermissionError`` on an
  unreadable parent and read a link loop as absent.
  """
  st = stat_unless_gone(path)
  return st is not None and stat.S_ISREG(st.st_mode)


def iterdir_unless_gone(path: Path) -> list[Path] | None:
  """*path*'s entries, or ``None`` when it is definitively absent or no directory.

  ⚑ NOT ``Path.iterdir()``: an unlistable directory raises a bare ``PermissionError``.
  Any failure but absence refuses, naming *path*, as :func:`stat_unless_gone` does.
  """
  if not is_dir_unless_gone(path):
    return None
  try:
    return list(path.iterdir())
  except OSError as exc:
    raise unreadable_path_error(path, exc) from exc


def refuse_unreadable_file(path: str | os.PathLike[str]) -> None:
  """Refuse, as :func:`stat_unless_gone` does, a regular file at *path* that cannot be read.

  A gone path or a non-file passes: this answers only "is it there but unreadable",
  which a reader that maps any failure to "absent" or "malformed" cannot tell apart.
  """
  if not is_file_unless_gone(path):
    return
  try:
    with open(path, "rb"):
      pass
  except OSError as exc:
    raise unreadable_path_error(path, exc) from exc


def unreadable_path_error(path: str | os.PathLike[str], exc: OSError) -> ConfigError:
  """The refusal for *path*, which *exc* says kanibako cannot reach: a loop, or unreadable."""
  if exc.errno == errno.ELOOP:
    # ⚑ A loop is no permission problem: "make it readable" would be the wrong cure.
    from kanibako.settings.messages import CANON_SYMLINK_LOOP_WHY

    return ConfigError(
      f"{os.fspath(path)}: {CANON_SYMLINK_LOOP_WHY}. Remove or repair the link, then retry."
    )
  return ConfigError(
    f"{os.fspath(path)} could not be read ({exc.strerror}). Make it and its parent "
    "directories readable, then retry."
  )


def literal_path(value: str | os.PathLike[str]) -> str:
  """*value* absolute and normalized, links unfollowed: box identity's form.

  A relative *value* joins :func:`logical_cwd`, the directory as the user reached it.
  """
  text = os.fspath(value)
  if not os.path.isabs(text):
    text = os.path.join(logical_cwd(), text)
  return os.path.normpath(text)


def is_home_dir(path: str | os.PathLike[str]) -> bool:
  """True when *path* is ``$HOME``, as spelled (:func:`literal_path`) or with every link resolved.

  ⚑ Never raises: ``os.path.realpath`` stops at a symlink loop where ``Path.resolve`` raises
  on 3.11, and a looped link is never ``$HOME``.
  """
  literal, home = literal_path(path), literal_path(Path.home())
  return literal == home or os.path.realpath(literal) == os.path.realpath(home)


def logical_cwd() -> str:
  """``$PWD`` when it names the current directory, else :func:`os.getcwd`."""
  pwd = os.environ.get("PWD", "")
  try:
    if os.path.isabs(pwd) and os.path.samefile(pwd, "."):
      return os.path.normpath(pwd)
  except OSError:
    pass
  return os.getcwd()
