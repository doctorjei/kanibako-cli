"""Atomic file writes for persistent state.

A crash mid-write with a plain ``path.write_text(...)`` leaves a torn/truncated
file that the next run fails to parse.  Writing to a temp file in the *same
directory* and then ``os.replace``-ing it over the target is atomic on POSIX
(``rename(2)`` within one filesystem), so a reader sees either the old file or
the new one — never a half-written one.

These helpers are dependency-free (only the stdlib) so any module can route its
registry/state writes through them without risking an import cycle.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, data: str) -> None:
    """Write *data* to *path* atomically.

    The bytes land in a temp file in ``path.parent`` (so ``os.replace`` stays on
    one filesystem), are flushed and ``fsync``-ed, then renamed over *path*.  On
    any failure the temp file is removed and the original *path* is left intact.
    Parent directories are created if needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    encoded = data.encode("utf-8")
    # Create the temp file in the SAME directory as the target so os.replace is
    # a same-filesystem rename (atomic).  delete=False so we control the rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Leave the original file untouched; clean up the temp on any failure.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


