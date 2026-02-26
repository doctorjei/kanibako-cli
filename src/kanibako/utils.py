"""Utility functions: cp_if_newer, confirm_prompt, short_hash, path encoding, container naming."""

from __future__ import annotations

import hashlib
import os
import shutil
from typing import TYPE_CHECKING

from kanibako.errors import UserCancelled

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths


def cp_if_newer(src: str | os.PathLike, dst: str | os.PathLike) -> bool:
    """Copy *src* to *dst* only if *src* is strictly newer (by mtime).

    Creates parent directories for *dst* if needed.
    Returns True if the copy was performed.
    """
    src_s = str(src)
    dst_s = str(dst)
    if not os.path.isfile(src_s):
        return False
    do_copy = (
        not os.path.isfile(dst_s)
        or os.stat(src_s).st_mtime > os.stat(dst_s).st_mtime
    )
    if do_copy:
        os.makedirs(os.path.dirname(dst_s) or ".", exist_ok=True)
        shutil.copy2(src_s, dst_s)
    return do_copy


def confirm_prompt(message: str) -> None:
    """Print *message*, read a line, raise UserCancelled unless it is 'yes'."""
    print(message, end="", flush=True)
    try:
        response = input()
    except (EOFError, KeyboardInterrupt):
        print()
        raise UserCancelled("Aborted.")
    if response.strip() != "yes":
        raise UserCancelled("Aborted.")


def short_hash(full_hash: str, length: int = 8) -> str:
    """Return the first *length* characters of *full_hash*."""
    return full_hash[:length]


def container_name_for(proj: ProjectPaths) -> str:
    """Deterministic container name for a project.

    - AC with name: ``kanibako-{name}``
    - AC without name (legacy): ``kanibako-{short_hash}``
    - Workset: ``kanibako-{short_hash}`` (name-based pending workset naming)
    - Decentralized: ``kanibako-ronin-{escape_path(project_path)}``
    """
    if proj.mode.value == "decentralized":
        return f"kanibako-ronin-{escape_path(str(proj.project_path))}"
    if proj.name:
        return f"kanibako-{proj.name}"
    return f"kanibako-{short_hash(proj.project_hash)}"


def project_hash(project_path: str) -> str:
    """SHA-256 hex digest of the project path string."""
    return hashlib.sha256(project_path.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Decentralized path encoding (for container names)
# ---------------------------------------------------------------------------

_DASH_ESCAPE = "-."


def escape_path(path: str) -> str:
    """Encode a filesystem path for use in container names.

    - Drop leading ``/``
    - Escape literal ``-`` → ``-.`` (dash-dot)
    - Replace ``/`` → ``-``

    Example: ``/home/user/my-project/app`` → ``home-user-my.-project-app``
    """
    path = path.lstrip("/")
    path = path.replace("-", _DASH_ESCAPE)
    path = path.replace("/", "-")
    return path


def unescape_path(encoded: str) -> str:
    """Decode a container-name-encoded path back to a filesystem path.

    Reverses ``escape_path``: ``-.`` → ``-``, lone ``-`` → ``/``,
    prepends ``/``.
    """
    # Use a sentinel to avoid double-replacement.
    sentinel = "\x00"
    result = encoded.replace(_DASH_ESCAPE, sentinel)
    result = result.replace("-", "/")
    result = result.replace(sentinel, "-")
    return "/" + result
