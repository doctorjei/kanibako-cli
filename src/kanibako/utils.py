"""Utility functions: cp_if_newer, confirm_prompt, confirm_or_abort, deep_merge, utc_stamp, short_hash,
container naming, the unless-gone filesystem probes, refuse_unreadable_file."""

from __future__ import annotations

import errno
import hashlib
import os
import shlex
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from kanibako.errors import ConfigError, UserCanceled
from kanibako.settings.bootstrap import IGNORE_FILE

if TYPE_CHECKING:
    from collections.abc import Callable

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
    """Print *message*, read a line, raise UserCanceled unless it is 'yes'.

    A question that cannot be answered (no stdin, a closed or unreadable one) is a "no".
    """
    print(message, end="", flush=True)
    try:
        response = input()
    except (EOFError, KeyboardInterrupt, RuntimeError, OSError, ValueError):
        print()
        raise UserCanceled("Aborted.")
    if response.strip() != "yes":
        raise UserCanceled("Aborted.")


def confirm_or_abort(force: bool, message: str, *, announce_abort: bool = True) -> bool:
    """Gate a destructive step on :func:`confirm_prompt`; True ⇒ go on.

    *force* skips the prompt.  A decline prints ``Aborted.`` and answers False; a caller that
    words its own abort line passes ``announce_abort=False``.
    """
    if force:
        return True
    try:
        confirm_prompt(message)
    except UserCanceled:
        if announce_abort:
            print("Aborted.")
        return False
    return True


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


#: The ``compact`` and ``seconds`` stamp formats; a reader parses a stamp with the same name.
#: ``iso`` is ``datetime.isoformat()``, with microseconds and ``+00:00``.
UTC_STAMP_COMPACT = "%Y%m%dT%H%M%SZ"
UTC_STAMP_SECONDS = "%Y-%m-%dT%H:%M:%SZ"


def utc_stamp(kind: Literal["iso", "compact", "seconds"]) -> str:
    """The current UTC time as a *kind* stamp; each writer keeps the kind its reader parses."""
    now = datetime.now(timezone.utc)
    if kind == "iso":
        return now.isoformat()
    return now.strftime(UTC_STAMP_COMPACT if kind == "compact" else UTC_STAMP_SECONDS)


def short_hash(full_hash: str, length: int = 8) -> str:
    """Return the first *length* characters of *full_hash*."""
    return full_hash[:length]


#: The ``<W>`` of a primary and a standalone box — not the partition DIRECTORY names.
WORKSET_SEGMENT_PRIMARY = "primary"
WORKSET_SEGMENT_STANDALONE = "standalone"

#: Every rendered container name starts with this; ``list_running`` filters on it.
CONTAINER_NAME_PREFIX = "kb-"


def renders_no_name(box: str) -> bool:
    """True when *box* renders NO name: it is empty, or the name rule no longer allows it.

    A name the rule no longer allows is a LEGACY name (keyspec ``meta.box.container``);
    with no ``-`` in any name, ``kb-<W>-<B>`` reads back without an escape.
    ⚑ A leading digit is NOT judged here: it leaves the name readable, and the names
    kanibako generates itself — a standalone box's kuid prefix, a nameless box's hash —
    may start with one.  The doors that take a typed or derived name refuse it.
    """
    from kanibako.identifiers import LEADING_DIGIT_FLAW, name_flaw

    if not isinstance(box, str):
        return False
    flaw = name_flaw(box)
    return flaw is not None and flaw != LEADING_DIGIT_FLAW


def workset_renders_no_name(workset: str) -> bool:
    """:func:`renders_no_name` for the ``<W>`` segment: a LEGACY workset name renders none."""
    return renders_no_name(workset)


def rename_box_cure(mode: str, path: Path, name: str | None = None,
                    taken: Callable[[str], bool] | None = None) -> str:
    """The command that gives a *mode* box at *path* a new name, by path.

    A standalone box is named after its directory, so its cure moves the directory.
    With the refused *name*, the cure offers its spelling (spec §0) when one exists,
    suffixed as a derived name is when *taken* (its workset's membership) holds it."""
    if mode == "standalone":
        return f"kanibako box move {shlex.quote(str(path))} <new-path>"
    from kanibako.identifiers import first_free_name
    from kanibako.launch.box_identity import name_spelling

    offer = name_spelling(name) if name else None
    new_name = "<new-name>" if offer is None else (
        offer[0] if taken is None else first_free_name(offer[0], taken))
    return f"kanibako box move {shlex.quote(str(path))} <new-path> --name {new_name}"


def legacy_workset_reason(workset: str) -> str:
    """Why legacy *workset* renders no name: its flaw and the name rule."""
    from kanibako.identifiers import name_flaw
    from kanibako.settings.messages import NAME_CHARSET

    return f"its workset name '{workset}' {name_flaw(workset)}; {NAME_CHARSET}"


def legacy_workset_cure(workset: str, where: Path) -> str:
    """The two commands that move the box at *where* out of legacy *workset*, indented."""
    from kanibako.launch.box_identity import name_spelling

    offer = name_spelling(workset)
    spelled = offer[0] if offer is not None else "<new-name>"
    return (f"  kanibako workset create <new-root> --name {spelled}\n"
            f"  kanibako box convert {shlex.quote(str(where))} --workset {spelled}")


def unrenderable_box_name_refusal(
    box: str, mode: str, path: Path | None, designation: str | None,
    *, workset: str | None = None, taken: Callable[[str], bool] | None = None,
) -> str:
    """The refusal ``start`` and ``stop`` print for a box that renders no name.

    The cure names the box by PATH, never by *box* (a leading ``-`` reads as a flag):
    its project *path*, else the *designation* that reached it.  A box whose own name
    renders but whose *workset* segment is a legacy name is refused for the workset."""
    from kanibako.launch.box_identity import box_name_reason

    where = path if path is not None else Path(literal_path(designation or "."))
    if not renders_no_name(box) and workset is not None and workset_renders_no_name(workset):
        return (
            f"Error: box '{box}' has no container name: {legacy_workset_reason(workset)}. "
            f"Move the box to a workset with a valid name, then try again:\n"
            f"{legacy_workset_cure(workset, where)}"
        )
    reason = box_name_reason(box) or "box name must not be empty"
    return (
        f"Error: box '{box}' has no container name: {reason}. Give the box a valid "
        f"name, then try again:\n"
        f"  {rename_box_cure(mode, where, box, taken)}"
    )


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
    if renders_no_name(box) or workset_renders_no_name(workset):
        return None
    name = f"{CONTAINER_NAME_PREFIX}{workset}-{box}"
    if helper_num is not None:
        name += f"-helper-{helper_num}"
    return name


def render_socket_identity(box: str, workset: str) -> str | None:
    """The helper-socket stem ``<B>-<W>`` (the container's order, reversed), or ``None``."""
    if renders_no_name(box) or workset_renders_no_name(workset):
        return None
    return f"{box}-{workset}"


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


def stat_unless_gone(path: str | os.PathLike[str]) -> os.stat_result | None:
    """*path*'s stat, or ``None`` when it is DEFINITIVELY absent (``ENOENT``/``ENOTDIR``).

    ⚑ NOT ``Path.exists()``/``is_dir()``: they raise a bare ``PermissionError`` on an
    unreadable parent.  Any other failure refuses, naming *path*: a box kanibako cannot
    see is never "gone".
    """
    try:
        return os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise unreadable_path_error(path, exc) from exc


def is_dir_unless_gone(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a directory; refuses (:func:`stat_unless_gone`) when it cannot tell."""
    st = stat_unless_gone(path)
    return st is not None and stat.S_ISDIR(st.st_mode)


def is_file_unless_gone(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a regular file; refuses (:func:`stat_unless_gone`) when it cannot tell.

    ⚑ NOT ``Path.is_file()``/``exists()``: they raise a bare ``PermissionError`` on an
    unreadable parent and read a link loop as absent.
    """
    st = stat_unless_gone(path)
    return st is not None and stat.S_ISREG(st.st_mode)


def iterdir_unless_gone(path: Path) -> list[Path] | None:
    """*path*'s entries, or ``None`` when it is definitively absent or no directory.

    ⚑ NOT ``Path.iterdir()``: an unlistable directory raises a bare ``PermissionError``.
    Any failure but absence refuses, naming *path*, as :func:`stat_unless_gone` does.
    """
    if not is_dir_unless_gone(path):
        return None
    try:
        return list(path.iterdir())
    except OSError as exc:
        raise unreadable_path_error(path, exc) from exc


def refuse_unreadable_file(path: str | os.PathLike[str]) -> None:
    """Refuse, as :func:`stat_unless_gone` does, a regular file at *path* that cannot be read.

    A gone path or a non-file passes: this answers only "is it there but unreadable",
    which a reader that maps any failure to "absent" or "malformed" cannot tell apart.
    """
    if not is_file_unless_gone(path):
        return
    try:
        with open(path, "rb"):
            pass
    except OSError as exc:
        raise unreadable_path_error(path, exc) from exc


def unreadable_path_error(path: str | os.PathLike[str], exc: OSError) -> ConfigError:
    """The refusal for *path*, which *exc* says kanibako cannot reach: a loop, or unreadable."""
    if exc.errno == errno.ELOOP:
        # ⚑ A loop is no permission problem: "make it readable" would be the wrong cure.
        from kanibako.project.workset import CANON_SYMLINK_LOOP_WHY

        return ConfigError(
            f"{os.fspath(path)}: {CANON_SYMLINK_LOOP_WHY}. Remove or repair the link, "
            "then retry."
        )
    return ConfigError(
        f"{os.fspath(path)} could not be read ({exc.strerror}). Make it and its parent "
        "directories readable, then retry."
    )


def literal_path(value: str | os.PathLike[str]) -> str:
    """*value* absolute and normalized, links unfollowed: box identity's form.

    A relative *value* joins :func:`logical_cwd`, the directory as the user reached it.
    """
    text = os.fspath(value)
    if not os.path.isabs(text):
        text = os.path.join(logical_cwd(), text)
    return os.path.normpath(text)


def is_home_dir(path: str | os.PathLike[str]) -> bool:
    """True when *path* is ``$HOME``, as spelled (:func:`literal_path`) or with every link resolved.

    ⚑ Never raises: ``os.path.realpath`` stops at a symlink loop where ``Path.resolve`` raises
    on 3.11, and a looped link is never ``$HOME``.
    """
    literal, home = literal_path(path), literal_path(Path.home())
    return literal == home or os.path.realpath(literal) == os.path.realpath(home)


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


def project_gitignore_to_strip(project_path: Path) -> Path | None:
    """The root .gitignore holding a line :func:`write_project_gitignore` writes, else None.

    A symlinked or unreadable file is left alone with a Note.
    """
    gitignore = project_path / IGNORE_FILE
    if not (gitignore.is_file() or gitignore.is_symlink()):
        return None
    try:
        lines = gitignore.read_bytes().decode("utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Note: could not read {gitignore} ({getattr(exc, 'strerror', None) or exc}); "
              "any kanibako line in it is yours to remove.", file=sys.stderr)
        return None
    if not any(entry in lines for entry in _GITIGNORE_ENTRIES):
        return None
    if gitignore.is_symlink():
        print(f"Note: left {gitignore} in place — it is a link, so kanibako's "
              f"{', '.join(_GITIGNORE_ENTRIES)} line is yours to remove.", file=sys.stderr)
        return None
    return gitignore


def _gitignore_kept_text(gitignore: Path) -> str:
    text = gitignore.read_bytes().decode("utf-8")
    return "".join(line for line in text.splitlines(keepends=True)
                   if line.splitlines()[0] not in _GITIGNORE_ENTRIES)


def gitignore_holds_only_kanibako(gitignore: Path) -> bool:
    """True when stripping *gitignore* leaves only whitespace, so the file is deleted."""
    try:
        return not _gitignore_kept_text(gitignore).strip()
    except (OSError, UnicodeDecodeError):
        return False


def strip_project_gitignore(gitignore: Path) -> bool:
    """Drop kanibako's lines from *gitignore*; unlink it if only whitespace remains."""
    try:
        kept = _gitignore_kept_text(gitignore)
        if kept.strip():
            gitignore.write_bytes(kept.encode("utf-8"))  # in place: keeps its mode
        else:
            gitignore.unlink()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: could not remove kanibako's line from {gitignore} ({exc}).",
              file=sys.stderr)
        return False
    return True
