"""Copy a directory tree keeping every symlink exactly as it is.

``shutil.copytree``'s default FOLLOWS symlinks: a link to an outside directory lands in the copy
as a full copy of that directory, and a dangling link fails the copy.  A user who made a link
meant a pointer, so this module keeps the pointer (Q70: *"Keep symlinks. If the user had a
symlink, there's a reason."*).

THE RULE: every link is copied VERBATIM — the same text, absolute or relative, inside the tree or
leaving it, dangling or not — and a symlinked directory is never traversed.  Verbatim is right
because a link is judged in the BOX's view (Q74: *"it's the box view except for when a symlink is
directly mounted."*): relative text names a box path, and every tree copied through here ends up
at the box path it came from (a relocated vault, home or workspace; a snapshot or a stash once put
back), so the same text names the same thing there.

⚑ NOT YET HANDLED — the directly mounted link.  Podman resolves it on the host and the box never
sees it, so its relative text would need repointing to the same host target.  Which links count
is not yet decided; until it is, such a link is copied verbatim like any other.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from pathlib import Path


def copy_tree_keeping_links(
    src: Path,
    dst: Path,
    *,
    ignore: Callable[[str, list[str]], Iterable[str]] | None = None,
    dirs_exist_ok: bool = False,
) -> None:
    """``shutil.copytree`` *src* to *dst* under THE RULE in the module docstring.

    *ignore* and *dirs_exist_ok* are ``copytree``'s, passed through.  Raises what ``copytree``
    raises: a ``shutil.Error`` listing every entry it could not copy, after copying the rest
    (an existing entry at a link's name in a merge destination is such an entry — reported,
    never overwritten), or ``FileExistsError`` for an existing *dst* without *dirs_exist_ok*.
    """
    shutil.copytree(src, dst, symlinks=True, ignore=ignore, dirs_exist_ok=dirs_exist_ok)
