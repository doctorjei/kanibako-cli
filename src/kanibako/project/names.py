"""Global name registry (the ``worksets`` section of ``config.registry``).

Central index at ``{config.registry}`` (``{data_path}/global/registry.yaml``)
mapping human-readable workset names to their root paths.  Default-mode
(PRIMARY) box names are NOT here anymore — the former ``projects`` section was
retired (2026-07-08, clean split): a primary box's identity now lives SOLELY in
the primary workset's per-workset ``boxes:`` membership (spec L514, via
:mod:`kanibako.project.workset_registry`; the primary-membership name API is in
:mod:`kanibako.settings.paths`).  Standalone boxes are likewise excluded — their identity
lives in the registry's ``standalone`` section, owned by
:mod:`kanibako.project.registry_store`.

The registry section this module owns::

    worksets:
      clientwork: /home/user/worksets/client

This module reads/writes ONLY the ``worksets`` section; the
``standalone``/``rigs``/``image_shells`` sections are owned by their respective
callers and preserved across writes by :mod:`kanibako.project.registry_store`.
:func:`resolve_name` additionally consults the PRIMARY per-workset membership
(when a *primary_workset* is supplied) so a bare primary-box name still resolves
at the same precedence the retired ``projects`` section held, and LAST the
``standalone`` section (owned by :mod:`kanibako.project.registry_store`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from kanibako._atomic import rmw_lock
from kanibako.identifiers import find_identifier
from kanibako.project import registry_store
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.errors import AmbiguousNameError, ConfigError, ProjectError
from kanibako.log import get_logger
from kanibako.utils import is_dir_unless_gone, is_home_dir, literal_path

if TYPE_CHECKING:
    from kanibako.settings.workset_dirkeys import EarlyScope, EarlySystem

logger = get_logger("names")


# ---------------------------------------------------------------------------
# I/O helpers — back the worksets section of config.registry.
# ---------------------------------------------------------------------------

def _load(registry: Path) -> dict[str, dict[str, str]]:
    """Load the worksets section of registry.yaml."""
    sections = registry_store.load_registry(registry)
    return {
        "worksets": dict(sections["worksets"]),
    }


def _save(registry: Path, names: dict[str, dict[str, str]]) -> None:
    """Write the worksets section of registry.yaml.

    Reads the full registry first so the ``standalone``/``rigs``/``image_shells``
    sections (owned elsewhere) are preserved.
    """
    sections = registry_store.load_registry(registry)
    sections["worksets"] = dict(names.get("worksets", {}))
    registry_store.save_registry(registry, sections)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_names(registry: Path) -> dict[str, dict[str, str]]:
    """Load the global name registry.

    Returns ``{"worksets": {name: path, ...}}``.  (The ``projects`` section was
    retired — see the module docstring; primary box names live in the primary
    per-workset membership.)

    *registry* is the resolved ``config.registry`` file path (``std.registry``).
    """
    return _load(registry)


def register_name(
    registry: Path,
    name: str,
    path: str,
    section: str = "worksets",
) -> None:
    """Register a workset name → path mapping.

    Raises ``ProjectError`` if *name* is already registered as a workset, or if
    *path* resolves to ``$HOME``.  (Since the ``projects`` section retired, the
    only global name section is ``worksets``; the primary-box name domain and its
    registration live in :mod:`kanibako.settings.paths`.)
    """
    # Guard: never register $HOME as a project path.
    if is_home_dir(path):
        raise ProjectError(
            "Refusing to register $HOME as a project path — this would "
            "mount your entire home directory as the workspace."
        )
    with rmw_lock(registry):
        names = _load(registry)
        # ⚑ Compared case-blind (spec §0, ⚑ NAMING RULES) — ``Foo`` collides with ``foo``.
        held = find_identifier(name, names["worksets"])
        if held is not None:
            raise ProjectError(
                f"Name '{name}' is already registered"
                f" (worksets: {names['worksets'][held]})"
            )
        # 🛑 STORED AS TYPED — fold to compare, NEVER to store.  The key written here is the
        # caller's spelling, unfolded, and it stays that way.
        names[section][name] = path
        _save(registry, names)


def register_name_if_absent(
    registry: Path,
    name: str,
    path: str,
    section: str = "worksets",
) -> None:
    """Idempotent :func:`register_name` for the interrupted-create recovery path.

    A no-op when *name* is already registered in *section* with the SAME
    *path* (the only at-rest collision the deferred-registration marker flow can
    legitimately re-enter — a crash in the tiny register→remove-marker window
    leaves the box registered, so re-running the create/seed recovery must not
    raise on the already-present mapping).  Anything else — *section* holding the
    name under a DIFFERENT path — is a genuine collision and re-raises via
    :func:`register_name`.
    """
    if is_home_dir(path):
        # Surface the $HOME guard with the same message as register_name.
        register_name(registry, name, path, section=section)
        return
    with rmw_lock(registry):
        names = _load(registry)
        # ⚑ Case-blind (§0): the recovery re-entry may type a different case than the
        # interrupted create stored, and that is still the SAME registered name.
        stored = find_identifier(name, names[section])
        existing = None if stored is None else names[section][stored]
        if existing is not None and existing == path:
            return  # identical mapping already present → no-op.
        register_name(registry, name, path, section=section)


def unregister_name(
    registry: Path,
    name: str,
    section: str = "worksets",
    *,
    exact: bool = False,
) -> bool:
    """Remove a name from the registry.

    Returns True if the name was found and removed, False otherwise.  *exact* removes
    only the key spelled as *name*, never a case variant of it.
    """
    with rmw_lock(registry):
        names = _load(registry)
        # ⚑ Found case-blind, DELETED by the stored spelling (§0): folding the query alone
        # would report success while leaving the entry behind.
        entries = names.get(section, {})
        stored = (name if name in entries else None) if exact else find_identifier(name, entries)
        if stored is None:
            return False
        del names[section][stored]
        _save(registry, names)
        return True


def lookup_by_path(
    registry: Path,
    path: str,
) -> tuple[str, str] | None:
    """Find a registered WORKSET name by its path value.

    Returns ``(name, "worksets")`` if found, ``None`` otherwise.  (Primary-box
    reverse-lookup by path lives in :mod:`kanibako.settings.paths` against the primary
    per-workset membership.)
    """
    resolved = str(Path(path).resolve())
    names = _load(registry)
    for name, registered_path in names["worksets"].items():
        if str(Path(registered_path).resolve()) == resolved:
            return name, "worksets"
    return None


def _early(early_system: EarlySystem, workset_name: str) -> EarlyScope:
    """The scope of the workset registered as *workset_name*."""
    from kanibako.settings.workset_dirkeys import EarlyScope

    return EarlyScope(early_system, workset_name)


def _workset_member_paths(
    worksets: dict[str, str], name: str, *, early_system: EarlySystem,
) -> list[tuple[str, str, str]]:
    """Return the ``(workset name, box name, workspace path)`` triples for box *name*.

    Reads each NAMED workset's per-workset registry ``boxes:`` membership — the
    SAME index the box resolver (``box_resolve``) consumes and ``list`` reflects
    (design principle #2: one source of truth; this adds no new registry-reading
    logic, only reuses :mod:`kanibako.project.workset_registry`).  One entry per workset
    whose ``boxes:`` section lists *name*; the caller disambiguates any
    cross-workset collision.  A workset with no such member contributes nothing.
    Both names are the STORED spellings: the ``[worksets]`` key and the
    ``boxes:`` key (§0).

    *worksets* is the ``[worksets]`` section (``{ws_name: ws_root}``) — the
    PRIMARY workset is intentionally excluded (it is not listed there): its
    default-mode members live in the PRIMARY per-workset ``boxes:`` membership,
    which :func:`resolve_name` matches directly (step 2) via *primary_workset*.
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    members: list[tuple[str, str, str]] = []
    for ws_name, ws_root_str in worksets.items():
        ws_root = Path(ws_root_str)
        from kanibako.launch.box_resolve import stores_standalone_registry_null
        if stores_standalone_registry_null(ws_root):
            continue
        registry_path = workset_registry.resolve_workset_registry_path(
            ws_root, load_doc(ws_root / WORKSET_META_FILE), early=_early(early_system, ws_name),
        )
        boxes = workset_registry.load_workset_boxes(registry_path)
        stored = find_identifier(name, boxes)  # ⚑ case-blind membership test (§0)
        if stored is not None:
            members.append((ws_name, stored, boxes[stored]))
    return members


def resolve_name(
    registry: Path,
    name: str,
    cwd: Path | None = None,
    primary_workset: Path | None = None,
    *,
    early_system: EarlySystem,
    path: str | None = None,
    unreadable_path: ConfigError | None = None,
) -> tuple[str, str]:
    """Look up a bare identifier and return ``(path, kind)``; the designation cascade.

    Order (system-design § Box designation & workset path space; plan § 1 S8):

    1. *cwd* in a NAMED workset's path space → that workset's registry.
    2. A PRIMARY box (*primary_workset*'s membership) and *path*, a same-named path
       the caller found on disk.  Exactly one ⇒ that one.  Both ⇒ in a named space,
       the path if that workset records a member there, else the primary box; in the
       primary space, the primary box.  A shadowed path is warned about.
    3. A REGISTERED standalone box; an unregistered one is reachable only by path.
    4. A member of some OTHER named workset ⇒ :class:`AmbiguousNameError` naming the
       qualified ``<workset>/<box>`` spelling (never resolved from outside its space).
    5. A workset name (box and workset names are per-kind namespaces).

    *kind* is ``"project"``, ``"path"`` (*path* won; the caller resolves it), or
    ``"workset"``.  Raises ``ProjectError`` on no match.  *unreadable_path* is the refusal
    for a same-named path kanibako cannot stat: raised at the path's step 2 rank, when
    neither step 1 nor a primary box answers.
    """
    names = _load(registry)
    hit, space_boxes = _space_member(names, name, cwd, early_system=early_system)
    if hit is not None:
        return _warn_standalone_shadow(registry, name, hit), "project"

    primary_hit = _primary_member(name, primary_workset, early_system=early_system)
    if primary_hit is not None and path is not None:
        if space_boxes is not None and _records_member_at(space_boxes, path):
            return path, "path"
        shown = (name, primary_hit, literal_path(path), name)
        if (Path(literal_path(path)).resolve() != Path(primary_hit).resolve()
                and shown not in _WARNED_PATH_SHADOWS):
            # ⚑ Once per process: one command resolves its box in several passes.
            _WARNED_PATH_SHADOWS.add(shown)
            logger.warning(
                "bare name '%s' resolved to the primary box at %s; the path %s is "
                "shadowed — reach it as ./%s or by its full path.", *shown,
            )
        return _warn_standalone_shadow(registry, name, primary_hit), "project"
    if primary_hit is not None:
        return _warn_standalone_shadow(registry, name, primary_hit), "project"
    if path is not None:
        return path, "path"
    if unreadable_path is not None:
        raise unreadable_path

    root = registry_store.standalone_root(registry, name)
    if root is not None:
        return root, "project"

    _refuse_other_space_member(names, name, early_system=early_system)

    stored_ws = find_identifier(name, names["worksets"])  # ⚑ case-blind (§0)
    if stored_ws is not None:
        return names["worksets"][stored_ws], "workset"
    raise ProjectError(f"Unknown project or workset: '{name}'")


#: The path-shadowed-by-a-primary-box warnings this process has printed.
_WARNED_PATH_SHADOWS: set[tuple[str, str, str, str]] = set()


def _warn_standalone_shadow(registry: Path, name: str, path: str) -> str:
    """Warn when the box at *path* shadows a registered standalone of the same name."""
    shadow = registry_store.standalone_root(registry, name)
    if shadow is not None and literal_path(shadow) != literal_path(path):
        logger.warning(
            "bare name '%s' resolved to the box at %s; the registered standalone "
            "box of the same name at %s is shadowed — reach it by path.",
            name, path, shadow,
        )
    return path


def _records_member_at(boxes: dict[str, str], path: str) -> bool:
    """Whether a workset's ``boxes:`` membership records a member AT *path*, literally:
    another spelling of a member's directory is not that member (box identity, [R188])."""
    target = literal_path(path)
    return any(literal_path(recorded) == target for recorded in boxes.values())


def _space_member(
    names: dict[str, dict[str, str]],
    name: str,
    cwd: Path | None,
    *,
    early_system: EarlySystem,
) -> tuple[str | None, dict[str, str] | None]:
    """Step 1: *name* in the named workset whose path space holds *cwd*.

    Returns ``(member workspace or None, that workset's boxes)``; ``(None, None)``
    when *cwd* is in no named workset's space.  "Inside" covers the root AND the
    resolved ``workset.workspaces`` dir, which a repoint can place outside the root.
    The ``boxes:`` record is read first, then a workspace subdir of that name.
    """
    if cwd is None:
        return None, None
    from kanibako.launch.box_resolve import stores_standalone_registry_null
    from kanibako.project import workset_registry
    from kanibako.project.workset import (
        _symlink_loop,
        load_workset_settings_doc,
        resolve_workspaces_locator,
    )

    # Literal cwd against the literal arms (box identity, [R188]), then resolved
    # against resolved (containment): a link spelling of either side still lands.
    cwd_literal = Path(literal_path(cwd))
    cwd_resolved = cwd.resolve()
    space_boxes: dict[str, str] | None = None
    for ws_name, ws_root in names["worksets"].items():
        ws_path = Path(ws_root)
        if not ws_path.is_absolute():
            continue  # a relative root names no place; joining the cwd would hold every cwd
        if _symlink_loop(ws_path):
            continue  # a looped root holds no cwd, and resolving it raises on 3.11
        if stores_standalone_registry_null(ws_path):
            continue
        settings_doc = load_workset_settings_doc(ws_path)
        ws_early = _early(early_system, ws_name)
        ws_workspaces = resolve_workspaces_locator(ws_path, settings_doc, early=ws_early)
        literal_arms = (Path(literal_path(ws_path)), Path(literal_path(ws_workspaces)))
        inside = (
            any(cwd_literal.is_relative_to(arm) for arm in literal_arms)
            # ⚑ A looped arm (a repointed ``workset.workspaces``) holds no cwd; 3.11 raises on it.
            or any(cwd_resolved.is_relative_to(arm.resolve()) for arm in literal_arms
                   if not _symlink_loop(arm))
        )
        if not inside:
            continue
        registry_path = workset_registry.resolve_workset_registry_path(
            ws_path, settings_doc, early=ws_early,
        )
        boxes = workset_registry.load_workset_boxes(registry_path)
        if space_boxes is None:
            space_boxes = boxes
        registered = workset_registry.workset_box_path(registry_path, name)
        if registered is not None:
            return registered, boxes
        candidate = ws_workspaces / name
        if is_dir_unless_gone(candidate):  # ⚑ EACCES refuses, naming the path
            return str(candidate), boxes
    return None, space_boxes


def _primary_member(
    name: str, primary_workset: Path | None, *, early_system: EarlySystem,
) -> str | None:
    """Step 2's box half: *name* in the PRIMARY membership; skipped without *primary_workset*."""
    if primary_workset is None:
        return None
    from kanibako.channels.channels import WS_TOKEN_PRIMARY
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    primary_reg = workset_registry.resolve_workset_registry_path(
        primary_workset, load_doc(primary_workset / WORKSET_META_FILE),
        early=_early(early_system, WS_TOKEN_PRIMARY),
    )
    return workset_registry.workset_box_path(primary_reg, name)


def _refuse_other_space_member(
    names: dict[str, dict[str, str]], name: str, *, early_system: EarlySystem,
) -> None:
    """Step 4: refuse *name* when it is a member of a named workset outside this space.

    Its own space would resolve it (step 1), so outside one the bare name errors and
    names the ``<workset>/<box>`` spelling that reaches it (plan Q4, his text).
    """
    members = _workset_member_paths(names["worksets"], name, early_system=early_system)
    if not members:
        return
    # Collapse identical paths, keeping the first workset claiming each so a shared
    # box is named once.  ``workset.workspaces`` is settable, so a registered path need
    # not name its workset: candidates are ``<workset>/<box>`` STORED spellings (§0),
    # the command a user runs.  The existence check is a LABEL — membership is
    # registry-borne.
    targets: dict[str, tuple[str, str, str]] = {}
    for member in members:
        targets.setdefault(literal_path(member[2]), member)
    candidates: list[str] = []
    for ws_name, box_name, member_path in targets.values():
        missing = "" if Path(member_path).is_dir() else " [workspace missing]"
        candidates.append(f"{ws_name}/{box_name}{missing}")
    if len(candidates) == 1:
        ws_name, box_name, _path = next(iter(targets.values()))
        raise AmbiguousNameError(
            f"Box name '{name}' is not in this workset path space: it is a member of "
            f"workset '{ws_name}'. Name it as '{ws_name}/{box_name}' or run the "
            f"command from inside that workset."
        )
    raise AmbiguousNameError(
        f"Ambiguous box name '{name}': it is a member of multiple worksets "
        f"({', '.join(candidates)}). Qualify it as '<workset>/{name}' or run "
        f"the command from inside the intended workset."
    )


def resolve_qualified_name(
    registry: Path,
    qualified: str,
    *,
    early_system: EarlySystem,
) -> tuple[str, str]:
    """Resolve a qualified name (``workset/project``).

    Returns ``(project_workspace_path, workset_name)``.
    Raises ``ProjectError`` if the workset or project is not found.
    """
    if "/" not in qualified:
        raise ProjectError(
            f"Not a qualified name (expected workset/project): '{qualified}'"
        )
    ws_name, proj_name = qualified.split("/", 1)
    names = _load(registry)

    # ⚑ Case-blind (§0).  A DISTINCT name rather than a rebind of *ws_name*: everything
    # below returns and reports the STORED spelling, and a reader has to be able to see
    # which of the two any given line means.
    stored_ws = find_identifier(ws_name, names["worksets"])
    if stored_ws is None:
        raise ProjectError(f"Unknown workset: '{ws_name}'")

    from kanibako.project import workset_registry
    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workspaces_locator,
    )

    ws_root = Path(names["worksets"][stored_ws])
    from kanibako.launch.box_resolve import stores_standalone_registry_null
    if stores_standalone_registry_null(ws_root):
        raise ProjectError(f"Project '{proj_name}' not found in workset '{stored_ws}'")
    settings_doc = load_workset_settings_doc(ws_root)
    ws_early = _early(early_system, stored_ws)
    # Registered membership FIRST (the authoritative name → workspace store):
    # a member keeps its REGISTERED path wherever a composition epoch put it —
    # a ``workset.workspaces`` repoint must not orphan a pre-repoint member
    # (bifrost A0).
    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, settings_doc, early=ws_early,
    )
    registered = workset_registry.workset_box_path(registry_path, proj_name)
    if registered is not None:
        return registered, stored_ws
    # Fallback: a workspace subdir under the resolved ``workset.workspaces``
    # (repoint honored — §3.3) — e.g. an in-tree connect before its first start
    # (no ``boxes:`` entry yet).
    candidate = resolve_workspaces_locator(ws_root, settings_doc, early=ws_early) / proj_name
    if not is_dir_unless_gone(candidate):  # ⚑ EACCES refuses, naming the path
        raise ProjectError(
            f"Project '{proj_name}' not found in workset '{stored_ws}'"
        )
    return str(candidate), stored_ws
