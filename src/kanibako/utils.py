"""Utility functions: cp_if_newer, confirm_prompt, deep_merge, short_hash, container
naming."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako.errors import UserCanceled

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


#: The ``<W>`` segment for a box in the PRIMARY workset, and the STANDALONE one — the
#: bare words, NOT the ``__PRIMARY__``/``__STANDALONE__`` partition DIRECTORY names
#: (``channels.channels.WS_TOKEN_*``), which address a path and never a container.
WORKSET_SEGMENT_PRIMARY = "primary"
WORKSET_SEGMENT_STANDALONE = "standalone"

#: The ``kb-`` every rendered container name starts with — the ``kanibako ps`` listing's
#: own filter.  A second spelling of this prefix is a second carrier, so a box whose name
#: no longer carries it would silently leave the listing.
CONTAINER_NAME_PREFIX = "kb-"


def name_segment(segment: str) -> str:
    """Render one SEGMENT of a container or socket name: every ``-`` written ``--``.

    The escape is what makes the render INJECTIVE across segments, so two different
    ``(workset, box)`` pairs can never spell one name.  An empty segment renders empty —
    the caller, not this function, decides what a nameless box is.
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
) -> str:
    """``kb-<W>-<B>`` (plus ``-helper-<n>``) — the ONE container-name renderer.

    *workset* and *box* are the ``<W>`` / ``<B>`` segments as spelled by
    :func:`workset_segment` and the caller's box name; each is escaped by
    :func:`name_segment`.  A helper's number is a STRUCTURED argument, never
    recovered from a name — ``meta.box.container`` renders the pair, it is never a
    source to parse back from.
    """
    name = f"{CONTAINER_NAME_PREFIX}{name_segment(workset)}-{name_segment(box)}"
    if helper_num is not None:
        name += f"-helper-{helper_num}"
    return name


def render_socket_identity(box: str, workset: str) -> str:
    """``I`` — the helper-socket name's stem: ``<B>-<W>``, each segment escaped.

    The socket is bound per DIRECTOR box, so its two segments are in the opposite order
    from the container's; both render through :func:`name_segment`.
    """
    return f"{name_segment(box)}-{name_segment(workset)}"


def container_name_for_box_name(name: str, workset: str) -> str:
    """Container name of a PRIMARY- or NAMED-mode box of *workset*, keyed by *name*.

    *workset* is the ``<W>`` segment, so two boxes of one name in two worksets do not
    collide.  :func:`container_name_for` also passes a short project hash here for a
    nameless (legacy) primary box, so *name* is not always a box name.
    """
    return render_container_name(workset, name)


def container_name_segments(proj: ProjectPaths) -> tuple[str, str]:
    """The ``(<W>, <B>)`` pair *proj* renders from — its identity, before rendering.

    A caller that must name something DERIVED from a box (a helper's container) takes
    the pair here, not a rendered name it would have to take apart again.
    """
    group_name = proj.group.name if proj.group is not None else None
    workset = workset_segment(proj.mode.value, group_name)
    return workset, proj.name or short_hash(proj.project_hash)


def container_name_for(proj: ProjectPaths) -> str:
    """Deterministic container name for a project — its ``<W>`` and ``<B>`` rendered.

    ⚑ A caller holding a registry row rather than a :class:`ProjectPaths` (``box ps``)
    passes the two segments to :func:`render_container_name` directly; it never
    re-spells one by hand.
    """
    return render_container_name(*container_name_segments(proj))


def legacy_container_names(proj: ProjectPaths) -> tuple[str, ...]:
    """The names *proj*'s container carried BEFORE the ``kb-<W>-<B>`` render.

    A container started by an earlier release keeps its old name, which the current
    verbs no longer address — so ``start`` refuses while one runs
    (``commands.start._refuse_legacy_container``).  ⚑ ONE carrier of the old spelling:
    it is here, beside the render it replaced, and nothing else may re-spell it.

    Primary and named boxes were ``kanibako-<box name>``; a standalone box was
    ``kanibako-ronin-<escaped root>`` (leading ``/`` dropped, ``-`` written ``-.``,
    ``/`` written ``-``).  A nameless primary box falls back to its project hash, as
    the current render does.
    """
    box = proj.name or short_hash(proj.project_hash)
    if proj.mode.value == "standalone":
        escaped = str(proj.metadata_path).lstrip("/").replace("-", "-.").replace("/", "-")
        return (f"kanibako-ronin-{escaped}",)
    return (f"kanibako-{box}",)


def project_hash(project_path: str) -> str:
    """SHA-256 hex digest of the project path string."""
    return hashlib.sha256(project_path.encode()).hexdigest()


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
