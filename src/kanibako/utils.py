"""Utility functions: cp_if_newer, confirm_prompt, short_hash, path encoding, container naming."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.errors import UserCancelled

if TYPE_CHECKING:
    from kanibako.settings.paths import ProjectPaths


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


def container_name_for_box_name(name: str) -> str:
    """Container name of a PRIMARY- or NAMED-mode box, keyed by its box *name*.

    :func:`container_name_for` also passes a short project hash here for a nameless
    (legacy) primary box, so *name* is not always a box name.
    ⚑ NEVER a standalone box — its container is keyed by its ROOT, not its name
    (:func:`container_name_for_standalone_root`).
    """
    return f"kanibako-{name}"


def container_name_for_standalone_root(root: Path) -> str:
    """Container name of a STANDALONE box, keyed by its *root* — never by its box name.

    *root* is the box ROOT (``metadata_path``), NOT its ``workspace/`` subdir.
    """
    return f"kanibako-ronin-{escape_path(str(root))}"


def container_name_for(proj: ProjectPaths) -> str:
    """Deterministic container name for a project — picks the spelling for its mode.

    - Primary or named, with a name: ``kanibako-{name}``
    - Primary, nameless (legacy): ``kanibako-{short_hash}``
    - Standalone: ``kanibako-ronin-{escape_path(root)}``

    ⚑ A caller holding a registry row rather than a :class:`ProjectPaths` (``box ps``)
    calls the spelling for its mode directly; it never re-spells one by hand.
    """
    if proj.mode.value == "standalone":
        return container_name_for_standalone_root(proj.metadata_path)
    return container_name_for_box_name(proj.name or short_hash(proj.project_hash))


def project_hash(project_path: str) -> str:
    """SHA-256 hex digest of the project path string."""
    return hashlib.sha256(project_path.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Standalone path encoding (for container names)
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


# ---------------------------------------------------------------------------
# Project .gitignore helper
# ---------------------------------------------------------------------------

_GITIGNORE_ENTRIES = ["box_data/"]


def write_project_gitignore(project_path: Path) -> None:
    """Append the standalone box-metadata dir (box_data/) to the project .gitignore."""
    gitignore = project_path / ".gitignore"
    existing = ""
    if gitignore.is_file():
        existing = gitignore.read_text()

    lines_to_add = [
        entry for entry in _GITIGNORE_ENTRIES
        if entry not in existing.splitlines()
    ]

    if not lines_to_add:
        return

    with open(gitignore, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        for line in lines_to_add:
            f.write(line + "\n")
