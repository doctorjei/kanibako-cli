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


def renders_no_name(box: str) -> bool:
    """True when *box* renders NO name — the keyspec row's first obligation.

    A ``<W>`` ending in ``-`` and a ``<B>`` beginning with ``-`` put the segment boundary
    inside a run of three dashes, which the opposite reading spells identically; an empty
    ``<B>`` leaves the same run at the end.  Both are names the box-name rule no longer
    allows, so both render nothing rather than a name another box could also carry.

    Only a ``str`` is judged: a caller that hands over something which is not a name at
    all has a different fault, and a collision argument does not apply to it.
    """
    return isinstance(box, str) and (not box or box.startswith("-"))


def unrenderable_box_name_refusal(
    box: str, mode: str, path: Path | None,
) -> str:
    """The message for a box that renders no name, naming the rule and the cure.

    ⚑ ONE carrier for both doors that report it (``start`` and ``stop``), so the two
    cannot drift.  The cure names the box by its PROJECT path, never by *box*: a leading
    ``-`` is read as a flag, so the very name being refused could not address it.  A
    standalone box's ROOT is its identity, so it is renamed in place with
    ``convert --standalone``; any other box is moved to a new path.  A box with no
    recorded project path has no addressable path, and the verb is printed without one —
    run from inside the box, where the designation is the current directory.
    """
    from kanibako.launch.box_identity import box_name_reason

    reason = box_name_reason(box) or "box name must not be empty"
    if mode == "standalone":
        cure = (
            f"kanibako box convert {path} --standalone --name <new-name>" if path
            else "kanibako box convert --standalone --name <new-name>"
        )
    else:
        cure = (
            f"kanibako box move {path} <new-path> --name <new-name>" if path
            else "kanibako box move <new-path> --name <new-name>"
        )
    return (
        f"Error: box '{box}' has no container name: {reason}. Give the box a valid "
        f"name, then try again:\n"
        f"  {cure}"
    )


def name_segment(segment: str) -> str:
    """Render one SEGMENT of a container or socket name: every ``-`` written ``--``.

    The escape alone does NOT make a two-segment name decodable — see
    :func:`renders_no_name`, which is what keeps two boxes apart.  An empty
    segment renders empty; the caller decides what a nameless box is.
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

    *workset* and *box* are the ``<W>`` / ``<B>`` segments as spelled by
    :func:`workset_segment` and the caller's box name; each is escaped by
    :func:`name_segment`.  A helper's number is a STRUCTURED argument, never
    recovered from a name — ``meta.box.container`` renders the pair, it is never a
    source to parse back from.  :func:`renders_no_name` decides the ``None``; it is a
    VALUE, not an error, so a door that only reports prints that there is none.
    """
    if renders_no_name(box):
        return None
    name = f"{CONTAINER_NAME_PREFIX}{name_segment(workset)}-{name_segment(box)}"
    if helper_num is not None:
        name += f"-helper-{helper_num}"
    return name


def render_socket_identity(box: str, workset: str) -> str | None:
    """``I`` — the helper-socket name's stem: ``<B>-<W>``, or ``None`` for no name.

    The socket is bound per DIRECTOR box, so its two segments are in the opposite order
    from the container's; both render through :func:`name_segment`.  The ``<B>`` is
    judged by the same :func:`renders_no_name` the container render uses.
    """
    if renders_no_name(box):
        return None
    return f"{name_segment(box)}-{name_segment(workset)}"


def container_name_for_box_name(name: str, workset: str) -> str | None:
    """Container name of a PRIMARY- or NAMED-mode box of *workset*, or ``None``.

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


def container_name_for(proj: ProjectPaths) -> str | None:
    """Deterministic container name for a project, or ``None`` when it renders no name.

    ⚑ A caller holding a registry row rather than a :class:`ProjectPaths` (``box ps``)
    passes the two segments to :func:`render_container_name` directly; it never
    re-spells one by hand.  ⛔ A door that ADDRESSES a container must handle ``None``
    before it reaches the runtime or a path — there is nothing to address.
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
