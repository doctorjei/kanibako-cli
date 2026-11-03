"""File and path helpers: path_hash, and the standalone box's .gitignore line and its purge."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

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


def write_gitignore(project_path: Path) -> None:
  """Append the standalone box-metadata dir (box_data/) to the project .gitignore."""
  gitignore = project_path / IGNORE_FILE
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
