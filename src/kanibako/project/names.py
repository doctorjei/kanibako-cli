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
at the same precedence the retired ``projects`` section held.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.project import registry_store
from kanibako.errors import ProjectError
from kanibako.log import get_logger

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
    names = _load(registry)
    if name in names["worksets"]:
        raise ProjectError(
            f"Name '{name}' is already registered"
            f" (worksets: {names['worksets'][name]})"
        )
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
    names = _load(registry)
    existing = names[section].get(name)
    if existing is not None and existing == path:
        return  # identical mapping already present → no-op.
    register_name(registry, name, path, section=section)


def unregister_name(
    registry: Path,
    name: str,
    section: str = "worksets",
) -> bool:
    """Remove a name from the registry.

    Returns True if the name was found and removed, False otherwise.
    """
    names = _load(registry)
    if name not in names.get(section, {}):
        return False
    del names[section][name]
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


def _workset_member_paths(worksets: dict[str, str], name: str) -> list[str]:
    """Return the workspace paths registered under box *name* across worksets.

    Reads each NAMED workset's per-workset registry ``boxes:`` membership — the
    SAME index the box resolver (``box_resolve``) consumes and ``list`` reflects
    (design principle #2: one source of truth; this adds no new registry-reading
    logic, only reuses :mod:`kanibako.project.workset_registry`).  One entry per workset
    whose ``boxes:`` section lists *name*; the caller disambiguates any
    cross-workset collision.  A workset with no such member contributes nothing.

    *worksets* is the ``[worksets]`` section (``{ws_name: ws_root}``) — the
    PRIMARY workset is intentionally excluded (it is not listed there): its
    default-mode members live in the PRIMARY per-workset ``boxes:`` membership,
    which :func:`resolve_name` matches directly (step 2) via *primary_workset*.
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    paths: list[str] = []
    for ws_root_str in worksets.values():
        ws_root = Path(ws_root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            ws_root, load_doc(ws_root / "settings.yaml"),
        )
        box_path = workset_registry.load_workset_boxes(registry_path).get(name)
        if box_path is not None:
            paths.append(box_path)
    return paths


def resolve_name(
    registry: Path,
    name: str,
    cwd: Path | None = None,
    primary_workset: Path | None = None,
) -> tuple[str, str]:
    """Look up a bare name and return ``(path, kind)``.

    Resolution order:

    1. If *cwd* is inside a workset → check that workset's projects first
    2. PRIMARY default-mode boxes: a bare name in the primary per-workset
       ``boxes:`` membership (was the retired global ``[projects]`` section) —
       consulted only when *primary_workset* is supplied
    3. ``[worksets]`` section (workset names)
    4. Workset-MEMBER boxes: a bare name registered in some NAMED workset's
       per-workset registry ``boxes:`` membership (so a member box is
       addressable from OUTSIDE its workset)

    *kind* is ``"project"`` or ``"workset"``.
    Raises ``ProjectError`` if no match is found, or if the name is a member of
    more than one workset (ambiguous when resolved from outside any workset).
    """
    names = _load(registry)

    # 1. Context-aware: if cwd is inside a registered workset, check its
    #    projects first.
    if cwd is not None:
        cwd_str = str(cwd.resolve())
        for ws_name, ws_root in names["worksets"].items():
            if cwd_str == ws_root or cwd_str.startswith(ws_root + "/"):
                # cwd is inside this workset — check if name matches a
                # workspace subdir.
                ws_path = Path(ws_root)
                candidate = ws_path / "workspaces" / name
                if candidate.is_dir():
                    return str(candidate), "project"

    # 2. PRIMARY default-mode boxes (the primary per-workset membership — the
    #    store that succeeded the retired ``[projects]`` section, at the SAME
    #    precedence position).  Only consulted when the caller passes the primary
    #    workset root (a lookup with no *primary_workset* skips this step).
    if primary_workset is not None:
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc

        primary_reg = workset_registry.resolve_workset_registry_path(
            primary_workset, load_doc(primary_workset / "settings.yaml"),
        )
        primary_path = workset_registry.workset_box_path(primary_reg, name)
        if primary_path is not None:
            # Cross-kind shadow (per-kind name policy, Jei 2026-07-08): a bare
            # name that is BOTH a primary box and a workset resolves
            # deterministically to the box (this step precedes the worksets
            # step).  Warn once so the shadowed workset is not silently missed —
            # it stays reachable via its noun-scoped ``workset`` commands.
            if name in names["worksets"]:
                logger.warning(
                    "bare name '%s' resolved to the primary box; a workset of "
                    "the same name is shadowed (reach it via "
                    "'kanibako workset <cmd> %s').",
                    name, name,
                )
            return primary_path, "project"

    # 3. Worksets.
    if name in names["worksets"]:
        return names["worksets"][name], "workset"

    # 4. Workset-MEMBER boxes.  A bare name that is a member of a NAMED workset
    #    is otherwise unaddressable from outside that workset (the cwd-inside
    #    case is handled by step 1) — resolve it to the member's WORKSPACE path
    #    (what ``resolve_project`` expects: an existing box workspace dir).
    member_paths = _workset_member_paths(names["worksets"], name)
    if member_paths:
        # Collapse identical targets (a symlinked workspace can normalize to the
        # same path); genuinely distinct paths mean the name is a member of
        # multiple worksets → ambiguous from outside any workset.
        distinct = list(dict.fromkeys(str(Path(p).resolve()) for p in member_paths))
        if len(distinct) == 1:
            return member_paths[0], "project"
        raise ProjectError(
            f"Ambiguous box name '{name}': it is a member of multiple worksets "
            f"({', '.join(distinct)}). Qualify it as '<workset>/{name}' or run "
            f"the command from inside the intended workset."
        )

    raise ProjectError(f"Unknown project or workset: '{name}'")


def resolve_qualified_name(
    registry: Path,
    qualified: str,
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

    if ws_name not in names["worksets"]:
        raise ProjectError(f"Unknown workset: '{ws_name}'")

    ws_root = Path(names["worksets"][ws_name])
    candidate = ws_root / "workspaces" / proj_name
    if not candidate.is_dir():
        raise ProjectError(
            f"Project '{proj_name}' not found in workset '{ws_name}'"
        )
    return str(candidate), ws_name
