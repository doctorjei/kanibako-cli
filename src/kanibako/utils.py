"""Utility functions: cp_if_newer, confirm_prompt, deep_merge, short_hash, container
naming."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.errors import UserCanceled
from kanibako.settings.bootstrap import IGNORE_FILE

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
    """Print *message*, read a line, raise UserCanceled unless it is 'yes'."""
    print(message, end="", flush=True)
    try:
        response = input()
    except (EOFError, KeyboardInterrupt):
        print()
        raise UserCanceled("Aborted.")
    if response.strip() != "yes":
        raise UserCanceled("Aborted.")


def deep_merge(base: dict, override: dict) -> dict:
    """*base* with *override*'s entries added, a table into its table; neither is mutated.

    A key both hold as tables is merged recursively; otherwise *override*'s value wins.  The
    result SHARES every nested table neither side overrides with its input: copy before
    mutating it.
    """
    result = dict(base)
    for key, val in override.items():
        mine = result.get(key)
        if isinstance(mine, dict) and isinstance(val, dict):
            result[key] = deep_merge(mine, val)
        else:
            result[key] = val
    return result


def short_hash(full_hash: str, length: int = 8) -> str:
    """Return the first *length* characters of *full_hash*."""
    return full_hash[:length]


#: The ``<W>`` of a primary and a standalone box — not the partition DIRECTORY names.
WORKSET_SEGMENT_PRIMARY = "primary"
WORKSET_SEGMENT_STANDALONE = "standalone"

#: Every rendered container name starts with this; ``list_running`` filters on it.
CONTAINER_NAME_PREFIX = "kb-"


def renders_no_name(box: str) -> bool:
    """True when *box* renders NO name.

    A ``<W>`` ending in ``-`` and a ``<B>`` beginning with ``-`` put the segment boundary
    inside a run of three dashes, which the opposite reading spells identically; an empty
    ``<B>`` leaves the same run at the end.  The box-name rule no longer allows either,
    so both render nothing rather than a name another box could also carry.
    """
    return isinstance(box, str) and (not box or box.startswith("-"))


def unrenderable_box_name_refusal(
    box: str, mode: str, path: Path | None,
) -> str:
    """The refusal ``start`` and ``stop`` print for a box that renders no name.

    The cure addresses the box by its PROJECT *path*, never by *box*: a leading ``-``
    reads as a flag.  Without a path the verb prints bare, to run from inside the box."""
    from kanibako.launch.box_identity import box_name_reason

    reason = box_name_reason(box) or "box name must not be empty"
    if mode == "standalone":
        cure = (
            f"kanibako box convert {shlex.quote(str(path))} --standalone --name <new-name>"
            if path else
            "kanibako box convert --standalone --name <new-name>"
        )
    else:
        cure = (
            f"kanibako box move {shlex.quote(str(path))} <new-path> --name <new-name>"
            if path else
            "kanibako box move <new-path> --name <new-name>"
        )
    return (
        f"Error: box '{box}' has no container name: {reason}. Give the box a valid "
        f"name, then try again:\n"
        f"  {cure}"
    )


def name_segment(segment: str) -> str:
    """Render one SEGMENT of a container or socket name: every ``-`` written ``--``.

    The escape alone does NOT make a two-segment name decodable; :func:`renders_no_name`
    is what keeps two boxes apart.  An empty segment renders empty.
    """
    return segment.replace("-", "--")


def workset_segment(mode: str, group_name: str | None) -> str:
    """Return the ``<W>`` segment for a box in *mode* whose workset is *group_name*.

    *mode* is ``BoxMode``'s ``.value``; *group_name* is read only for ``named``.
    """
    if mode == "primary":
        return WORKSET_SEGMENT_PRIMARY
    if mode == "standalone":
        return WORKSET_SEGMENT_STANDALONE
    if not group_name:
        raise ValueError(
            "NAMED box is missing its workset name; cannot render its workset "
            "segment."
        )
    return group_name


def render_container_name(
    workset: str, box: str, helper_num: int | None = None,
) -> str | None:
    """``kb-<W>-<B>`` (plus ``-helper-<n>``), or ``None`` when the box renders NO name.

    ⚑ THE CONTRACT, owned here: ``None`` is a VALUE, not an error.  A door that only
    REPORTS prints that there is none; a door that ADDRESSES a container must handle
    ``None`` before it reaches the runtime or a path.
    """
    if renders_no_name(box):
        return None
    name = f"{CONTAINER_NAME_PREFIX}{name_segment(workset)}-{name_segment(box)}"
    if helper_num is not None:
        name += f"-helper-{helper_num}"
    return name


def render_socket_identity(box: str, workset: str) -> str | None:
    """The helper-socket stem ``<B>-<W>`` (the container's order, reversed), or ``None``."""
    if renders_no_name(box):
        return None
    return f"{name_segment(box)}-{name_segment(workset)}"


def container_name_for_box_name(name: str, workset: str) -> str | None:
    """Container name of box *name* in the ``<W>`` segment *workset*, or ``None``."""
    return render_container_name(workset, name)


def container_name_segments(proj: ProjectPaths) -> tuple[str, str]:
    """The ``(<W>, <B>)`` pair *proj* renders from; a nameless box's ``<B>`` is its hash."""
    group_name = proj.group.name if proj.group is not None else None
    workset = workset_segment(proj.mode.value, group_name)
    return workset, proj.name or short_hash(proj.project_hash)


def container_name_for(proj: ProjectPaths) -> str | None:
    """Container name for *proj*, or ``None`` (:func:`render_container_name`)."""
    return render_container_name(*container_name_segments(proj))


def legacy_container_names(proj: ProjectPaths) -> tuple[str, ...]:
    """The pre-1.8.0 names of *proj*'s container — the one carrier of the old spelling.

    ``start`` refuses while one runs (``commands.start._refuse_legacy_container``).
    """
    box = proj.name or short_hash(proj.project_hash)
    if proj.mode.value == "standalone":
        escaped = str(proj.metadata_path).lstrip("/").replace("-", "-.").replace("/", "-")
        return (f"kanibako-ronin-{escaped}",)
    return (f"kanibako-{box}",)


def project_hash(project_path: str) -> str:
    """SHA-256 hex digest of the project path string."""
    return hashlib.sha256(project_path.encode()).hexdigest()


def literal_path(value: str | os.PathLike[str]) -> str:
    """*value* absolute and normalized, links unfollowed: box identity's form.

    A relative *value* joins :func:`logical_cwd`, the directory as the user reached it.
    """
    text = os.fspath(value)
    if not os.path.isabs(text):
        text = os.path.join(logical_cwd(), text)
    return os.path.normpath(text)


def logical_cwd() -> str:
    """``$PWD`` when it names the current directory, else :func:`os.getcwd`."""
    pwd = os.environ.get("PWD", "")
    try:
        if os.path.isabs(pwd) and os.path.samefile(pwd, "."):
            return os.path.normpath(pwd)
    except OSError:
        pass
    return os.getcwd()


# ---------------------------------------------------------------------------
# Project .gitignore helper
# ---------------------------------------------------------------------------

_GITIGNORE_ENTRIES = ["box_data/"]


def write_project_gitignore(project_path: Path) -> None:
    """Append the standalone box-metadata dir (box_data/) to the project .gitignore."""
    gitignore = project_path / IGNORE_FILE
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
