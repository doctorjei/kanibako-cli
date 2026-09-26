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

import os
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path


def copy_tree_keeping_links(
    src: Path,
    dst: Path,
    *,
    ignore: Callable[[str, list[str]], Iterable[str]] | None = None,
    dirs_exist_ok: bool = False,
    replace_existing: bool = False,
) -> None:
    """``shutil.copytree`` *src* to *dst* under THE RULE in the module docstring.

    *ignore* and *dirs_exist_ok* are ``copytree``'s, passed through.  Raises what ``copytree``
    raises: a ``shutil.Error`` listing every entry it could not copy, after copying the rest
    (an existing entry at a link's name in a merge destination is such an entry — reported,
    never overwritten), or ``FileExistsError`` for an existing *dst* without *dirs_exist_ok*.

    A merge never writes THROUGH a link already in *dst*: a real file or directory meeting one
    is reported too, and the link and its target are left untouched.  A file meeting an existing
    real directory is reported too, never copied into it.

    *replace_existing* is the ``--force`` overwrite contract: a link then atomically REPLACES an
    existing non-directory entry at its name.  An existing real directory there is still
    reported in the ``shutil.Error`` and never removed.
    """
    refused: list[tuple[str, str, str]] = []
    walk_ignore = _refusing_links_in_the_way(src, dst, ignore, refused) if dirs_exist_ok else ignore
    failures: list[tuple[str, str, str]] = []
    try:
        shutil.copytree(src, dst, symlinks=True, ignore=walk_ignore, dirs_exist_ok=dirs_exist_ok)
    except shutil.Error as err:
        failures = [entry for entry in err.args[0] if not (replace_existing and _replaced(*entry))]
    if refused or failures:
        raise shutil.Error(refused + failures)


def failed_entries(err: shutil.Error) -> str | None:
    """``"N entries failed:"`` and a ``source: reason`` line for each of the first five.

    None when ``err.args[0]`` is not the ``(source, destination, reason)`` list ``copytree``
    raises, so the caller falls back to the error's own text.
    """
    failures = err.args[0] if err.args else None
    if not isinstance(failures, list):
        return None
    shown = 5
    lines = [f"  {entry_src}: {why}" for entry_src, _entry_dst, why in failures[:shown]]
    if len(failures) > shown:
        lines.append(f"  … and {len(failures) - shown} more")
    count = f"{len(failures)} entr{'y' if len(failures) == 1 else 'ies'} failed:"
    return "\n".join([count, *lines])


def _refusing_links_in_the_way(
    src: Path,
    dst: Path,
    ignore: Callable[[str, list[str]], Iterable[str]] | None,
    refused: list[tuple[str, str, str]],
) -> Callable[[str, list[str]], set[str]]:
    """Wrap *ignore* so a non-link entry whose destination is a link, or a non-directory whose
    destination is a real directory, is skipped and recorded.

    ``copytree`` consults *ignore* once per directory before it touches any entry, so this is
    the one hook that sees a file (``copy2`` would write through the link) and a directory
    (``makedirs`` would descend through it) alike.
    """

    def skip(directory: str, names: list[str]) -> set[str]:
        skipped = set(ignore(directory, names)) if ignore else set()
        target_dir = os.path.join(dst, os.path.relpath(directory, src))
        for name in names:
            source, target = os.path.join(directory, name), os.path.join(target_dir, name)
            if name in skipped or os.path.islink(source):
                continue
            if os.path.islink(target):
                refused.append((source, target, "a link is at the destination; not written through"))
                skipped.add(name)
            elif os.path.isdir(target) and not os.path.isdir(source):
                refused.append((source, target, "a directory is at the destination; not copied into it"))
                skipped.add(name)
        return skipped

    return skip


def _replaced(src_name: str, dst_name: str, _why: str) -> bool:
    """Replace the entry at *dst_name* with the link at *src_name*; False = not replaceable."""
    if not os.path.islink(src_name) or not os.path.lexists(dst_name):
        return False
    if os.path.isdir(dst_name) and not os.path.islink(dst_name):
        return False
    try:
        _replace_link(dst_name, os.readlink(src_name), src_name)
    except OSError:
        return False
    return True


def _replace_link(path: str, text: str, stat_from: str) -> None:
    """Atomically replace the entry at *path* with a link reading *text*; carry *stat_from*'s times."""
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.relink-{os.getpid()}")
    os.symlink(text, tmp)
    try:
        shutil.copystat(stat_from, tmp, follow_symlinks=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.islink(tmp):
            os.unlink(tmp)
        raise
