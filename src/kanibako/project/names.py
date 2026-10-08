"""Global name registry (the ``worksets`` section of ``config.registry``).

Central index at ``@config.registry`` (``{data_path}/global/registry.yaml``)
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
from kanibako.errors import AmbiguousNameError, ProjectError
from kanibako.log import get_logger
from kanibako.utils import literal_path

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
    if Path(path).resolve() == Path.home().resolve():
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
    if Path(path).resolve() == Path.home().resolve():
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
    standalone: bool = True,
) -> tuple[str, str]:
    """Look up a bare name and return ``(path, kind)``.

    Resolution order (system-design § Box designation & workset path space): the box
    steps of :func:`_resolve_before_standalone`, then a REGISTERED standalone box
    (skipped unless *standalone*: the caller checks a same-named path first); an
    unregistered one is reachable only by path.  A registered standalone shadowed
    by an earlier BOX is warned about (it stays reachable by path).  Box and
    workset names are per-kind namespaces, so a workset name is matched only when
    no box holds the name, letting a box verb name the workset in its refusal.

    *kind* is ``"project"`` or ``"workset"``.
    Raises ``ProjectError`` if no match is found, or ``AmbiguousNameError`` if
    the name is a member of more than one workset.
    """
    try:
        path, kind = _resolve_before_standalone(
            registry, name, cwd=cwd, primary_workset=primary_workset,
            early_system=early_system,
        )
    except AmbiguousNameError:
        # The standalone step is not a tiebreaker for an earlier step's tie.
        raise
    except ProjectError:
        root = registry_store.standalone_root(registry, name) if standalone else None
        if root is not None:
            return root, "project"
        worksets = _load(registry)["worksets"]
        stored_ws = find_identifier(name, worksets)  # ⚑ case-blind (§0)
        if stored_ws is None:
            raise
        return worksets[stored_ws], "workset"

    shadow = registry_store.standalone_root(registry, name)
    if (
        kind == "project" and shadow is not None
        and Path(shadow).resolve() != Path(path).resolve()
    ):
        logger.warning(
            "bare name '%s' resolved to the box at %s; the registered standalone "
            "box of the same name at %s is shadowed — reach it by path.",
            name, path, shadow,
        )
    return path, kind


def _resolve_before_standalone(
    registry: Path,
    name: str,
    cwd: Path | None = None,
    primary_workset: Path | None = None,
    *,
    early_system: EarlySystem,
) -> tuple[str, str]:
    """The box steps of :func:`resolve_name` before the standalone section.

    Resolution order:

    1. If *cwd* is inside a workset → check that workset's projects first
    2. PRIMARY default-mode boxes: a bare name in the primary per-workset
       ``boxes:`` membership (was the retired global ``[projects]`` section) —
       consulted only when *primary_workset* is supplied
    3. Workset-MEMBER boxes: a bare name registered in some NAMED workset's
       per-workset registry ``boxes:`` membership (so a member box is
       addressable from OUTSIDE its workset)

    *kind* is ``"project"``.  Raises ``ProjectError`` if no
    match is found, or ``AmbiguousNameError`` if the name is a member of more
    than one workset.
    """
    names = _load(registry)

    # 1. Context-aware: if cwd is inside a registered workset, check its
    #    projects first.  "Inside" covers BOTH the workset root AND its resolved
    #    ``workset.workspaces`` dir — an absolute repoint places the workspaces
    #    dir OUTSIDE the root, and a cwd there is workset context too (the S-2
    #    cwd-asymmetry).  The member consult reads the per-workset registry
    #    ``boxes:`` membership FIRST (the authoritative name → workspace store,
    #    honoring paths any composition epoch recorded — bifrost A0), then falls
    #    back to a workspace subdir under the resolved composition.
    if cwd is not None:
        from kanibako.project import workset_registry
        from kanibako.project.workset import (
            load_workset_settings_doc,
            resolve_workspaces_locator,
        )

        cwd_str = str(cwd.resolve())
        for ws_name, ws_root in names["worksets"].items():
            ws_path = Path(ws_root)
            from kanibako.launch.box_resolve import stores_standalone_registry_null
            if stores_standalone_registry_null(ws_path):
                continue
            settings_doc = load_workset_settings_doc(ws_path)
            ws_early = _early(early_system, ws_name)
            ws_workspaces = resolve_workspaces_locator(ws_path, settings_doc, early=ws_early)
            ws_workspaces_str = str(ws_workspaces)
            inside = (
                cwd_str == ws_root
                or cwd_str.startswith(ws_root + "/")
                or cwd_str == ws_workspaces_str
                or cwd_str.startswith(ws_workspaces_str + "/")
            )
            if not inside:
                continue
            registry_path = workset_registry.resolve_workset_registry_path(
                ws_path, settings_doc, early=ws_early,
            )
            registered = workset_registry.workset_box_path(registry_path, name)
            if registered is not None:
                return registered, "project"
            candidate = ws_workspaces / name
            if candidate.is_dir():
                return str(candidate), "project"

    # 2. PRIMARY default-mode boxes (the primary per-workset membership — the
    #    store that succeeded the retired ``[projects]`` section, at the SAME
    #    precedence position).  Only consulted when the caller passes the primary
    #    workset root (a lookup with no *primary_workset* skips this step).
    if primary_workset is not None:
        from kanibako.channels.channels import WS_TOKEN_PRIMARY
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        primary_reg = workset_registry.resolve_workset_registry_path(
            primary_workset, load_doc(primary_workset / WORKSET_META_FILE),
            early=_early(early_system, WS_TOKEN_PRIMARY),
        )
        primary_path = workset_registry.workset_box_path(primary_reg, name)
        if primary_path is not None:
            # ⚑ Per-kind namespaces (spec § Detection & import): a same-named workset is
            # not a collision, and its noun-scoped ``workset`` commands reach it.
            return primary_path, "project"

    # 3. Workset-MEMBER boxes.  A bare name that is a member of a NAMED workset
    #    is otherwise unaddressable from outside that workset (the cwd-inside
    #    case is handled by step 1) — resolve it to the member's registered
    #    WORKSPACE path, the form ``resolve_project`` takes.
    members = _workset_member_paths(names["worksets"], name, early_system=early_system)
    if members:
        # Collapse identical paths, keeping the first workset claiming each so a shared
        # box is named once; distinct paths ⇒ a member of multiple worksets → ambiguous.
        targets: dict[str, tuple[str, str, str]] = {}
        for member in members:
            targets.setdefault(literal_path(member[2]), member)
        if len(targets) == 1:
            return members[0][2], "project"
        # ``workset.workspaces`` is settable, so a registered path need not name its
        # workset: candidates are ``<workset>/<box>`` STORED spellings (§0), the command
        # a user runs.  The existence check is a LABEL — membership is registry-borne.
        candidates: list[str] = []
        for ws_name, box_name, member_path in targets.values():
            missing = "" if Path(member_path).is_dir() else " [workspace missing]"
            candidates.append(f"{ws_name}/{box_name}{missing}")
        raise AmbiguousNameError(
            f"Ambiguous box name '{name}': it is a member of multiple worksets "
            f"({', '.join(candidates)}). Qualify it as '<workset>/{name}' or run "
            f"the command from inside the intended workset."
        )

    raise ProjectError(f"Unknown project or workset: '{name}'")


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
    if not candidate.is_dir():
        raise ProjectError(
            f"Project '{proj_name}' not found in workset '{stored_ws}'"
        )
    return str(candidate), stored_ws
