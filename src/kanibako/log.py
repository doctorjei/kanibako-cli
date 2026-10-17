"""Logging setup for kanibako, and the size rotation of the files it appends to."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

#: The size at which an append-only log rotates.
ROTATE_MAX_BYTES = 1_048_576  # 1 MiB


def setup_logging(verbose: bool = False, *, timestamps: bool = False) -> None:
  """Configure the ``kanibako`` root logger.

  Normal mode: WARNING+ to stderr.
  Verbose mode: DEBUG+ to stderr with ``kanibako: <message>`` format.
  *timestamps* prefixes each record with its time and level, for a stderr that is a
  file read later rather than a terminal read now (the detached creds watcher).
  """
  logger = logging.getLogger("kanibako")
  logger.handlers.clear()

  handler = logging.StreamHandler(sys.stderr)
  fmt = None
  if verbose:
    logger.setLevel(logging.DEBUG)
    fmt = "kanibako: %(message)s"
  else:
    logger.setLevel(logging.WARNING)
  if timestamps:
    fmt = f"%(asctime)s %(levelname)s {fmt or '%(message)s'}"
  if fmt is not None:
    handler.setFormatter(logging.Formatter(fmt))

  handler.setLevel(logging.DEBUG)
  logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
  """Return a logger under ``kanibako``, from a short name or a module ``__name__``."""
  # ``__name__`` inside the package already carries the ``kanibako.`` prefix, so
  # prefixing it again would nest the real module under a phantom ``kanibako.``.
  # The dot is the boundary: ``kanibakox`` is a short name, not the package.
  if name == "kanibako" or name.startswith("kanibako."):
    return logging.getLogger(name)
  return logging.getLogger(f"kanibako.{name}")


def rotate_if_over(path: Path, max_bytes: int, *, root: Path | None = None) -> bool:
  """Move *path* to ``<path>.1`` and leave an empty *path* if it holds *max_bytes* or more.

  Returns whether it rotated.  A missing *path* is not rotated.  A writer holding *path*
  open must reopen it after a rotation: its descriptor follows the file to ``.1``.
  *root* marks *path* as box-writable, under the box tree *root*: every step then goes
  through :mod:`kanibako.boxfs`, so a symlink at *path* or a parent is not rotated.
  """
  if root is not None:
    from kanibako import boxfs

    try:
      if boxfs.stat(path, root).st_size < max_bytes:
        return False
    except OSError:
      return False
    boxfs.rename(path, path.with_suffix(path.suffix + ".1"), root)
    boxfs.touch(path, root)
    return True
  try:
    if path.stat().st_size < max_bytes:
      return False
  except OSError:
    return False
  path.rename(path.with_suffix(path.suffix + ".1"))
  path.touch()
  return True
