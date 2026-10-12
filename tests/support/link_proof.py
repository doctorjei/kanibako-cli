"""The no-follow proof's sentinel: a content hash of a host tree a planted link points at."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def tree_digest(path: Path) -> str:
    """Hash of *path*'s names, modes, and contents (a tree, a file, or absent).

    Walks with ``os.walk`` (links not entered) and lstat, so the hash never follows
    a link; a link's own text is hashed.
    """
    h = hashlib.sha256()
    if path.is_symlink():
        h.update(b"link:" + os.readlink(path).encode())
    elif path.is_file():
        h.update(oct(path.stat().st_mode).encode() + path.read_bytes())
    elif path.is_dir():
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames.sort()
            for name in sorted(dirnames + filenames):
                p = Path(dirpath) / name
                st = p.lstat()
                h.update(f"{p.relative_to(path)}:{oct(st.st_mode)}".encode())
                if p.is_symlink():
                    h.update(os.readlink(p).encode())
                elif p.is_file():
                    h.update(p.read_bytes())
    else:
        h.update(b"absent")
    return h.hexdigest()


def sentinel_tree(root: Path) -> Path:
    """A host tree outside every box: a file and a nested file, under *root*/outside."""
    outside = root / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "sentinel.txt").write_text("host data")
    (outside / "deep" / "nested.txt").write_text("nested host data")
    return outside
