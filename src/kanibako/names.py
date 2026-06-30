"""Project name registry (the ``projects``/``worksets`` sections of
``system.registry``).

Central index at ``@config.registry`` (``{data_path}/global/registry.yaml``)
mapping human-readable names to project paths (for default-mode projects) and
workset roots (for worksets).  Standalone projects are intentionally excluded
here — their identity lives in the registry's ``standalone`` section (later
sub-step), not in these two sections.

The registry holds these two sections (among others — see
:mod:`kanibako.registry_store`)::

    projects:
      myapp: /home/user/projects/myapp

    worksets:
      clientwork: /home/user/worksets/client

This module reads/writes ONLY the ``projects``/``worksets`` sections; the
``connected``/``standalone`` sections are owned by their respective callers and
preserved across writes by :mod:`kanibako.registry_store`.
"""

from __future__ import annotations

from pathlib import Path

from kanibako import registry_store
from kanibako.errors import ProjectError


# ---------------------------------------------------------------------------
# I/O helpers — back the projects/worksets sections of system.registry.
# ---------------------------------------------------------------------------

def _load(registry: Path) -> dict[str, dict[str, str]]:
    """Load the projects/worksets sections of registry.yaml."""
    sections = registry_store.load_registry(registry)
    return {
        "projects": dict(sections["projects"]),
        "worksets": dict(sections["worksets"]),
    }


def _save(registry: Path, names: dict[str, dict[str, str]]) -> None:
    """Write the projects/worksets sections of registry.yaml.

    Reads the full registry first so the ``connected``/``standalone`` sections
    (owned elsewhere) are preserved.
    """
    sections = registry_store.load_registry(registry)
    sections["projects"] = dict(names.get("projects", {}))
    sections["worksets"] = dict(names.get("worksets", {}))
    registry_store.save_registry(registry, sections)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_names(registry: Path) -> dict[str, dict[str, str]]:
    """Load names.yaml.

    Returns ``{"projects": {name: path, ...}, "worksets": {name: path, ...}}``.

    *registry* is the resolved ``config.registry`` file path (``std.registry``).
    """
    return _load(registry)


def register_name(
    registry: Path,
    name: str,
    path: str,
    section: str = "projects",
) -> None:
    """Register a name → path mapping.

    Raises ``ProjectError`` if *name* is already registered in either section,
    or if *path* resolves to ``$HOME``.
    """
    # Guard: never register $HOME as a project path.
    if Path(path).resolve() == Path.home().resolve():
        raise ProjectError(
            "Refusing to register $HOME as a project path — this would "
            "mount your entire home directory as the workspace."
        )
    names = _load(registry)
    # Check for duplicates across both sections.
    for sec in ("projects", "worksets"):
        if name in names[sec]:
            raise ProjectError(
                f"Name '{name}' is already registered"
                f" ({sec}: {names[sec][name]})"
            )
    names[section][name] = path
    _save(registry, names)


def register_name_if_absent(
    registry: Path,
    name: str,
    path: str,
    section: str = "projects",
) -> None:
    """Idempotent :func:`register_name` for the interrupted-create recovery path.

    A no-op when *name* is already registered in *section* with the SAME
    *path* (the only at-rest collision the deferred-registration marker flow can
    legitimately re-enter — a crash in the tiny register→remove-marker window
    leaves the box registered, so re-running the create/seed recovery must not
    raise on the already-present mapping).  Anything else — the name registered
    in the OTHER section, or in *section* under a DIFFERENT path — is a genuine
    collision and re-raises via :func:`register_name`.
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


def update_name_path(
    registry: Path,
    name: str,
    new_path: str,
    section: str = "projects",
) -> bool:
    """Update the path for an existing registered name.

    Returns True if the name was found and updated, False otherwise.
    Raises ``ProjectError`` if *new_path* resolves to ``$HOME``.
    """
    if Path(new_path).resolve() == Path.home().resolve():
        raise ProjectError(
            "Refusing to register $HOME as a project path — this would "
            "mount your entire home directory as the workspace."
        )
    names = _load(registry)
    if name not in names.get(section, {}):
        return False
    names[section][name] = new_path
    _save(registry, names)
    return True


def unregister_name(
    registry: Path,
    name: str,
    section: str = "projects",
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
    """Find a registered name by its path value.

    Returns ``(name, section)`` if found, ``None`` otherwise.
    """
    resolved = str(Path(path).resolve())
    names = _load(registry)
    for section in ("projects", "worksets"):
        for name, registered_path in names[section].items():
            if str(Path(registered_path).resolve()) == resolved:
                return name, section
    return None


def resolve_name(
    registry: Path,
    name: str,
    cwd: Path | None = None,
) -> tuple[str, str]:
    """Look up a bare name and return ``(path, kind)``.

    Resolution order:

    1. If *cwd* is inside a workset → check that workset's projects first
    2. ``[projects]`` section (default-mode projects)
    3. ``[worksets]`` section (workset names)

    *kind* is ``"project"`` or ``"workset"``.
    Raises ``ProjectError`` if no match is found.
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

    # 2. Default-mode projects.
    if name in names["projects"]:
        return names["projects"][name], "project"

    # 3. Worksets.
    if name in names["worksets"]:
        return names["worksets"][name], "workset"

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


def pick_name(
    registry: Path,
    path: str,
    section: str = "projects",
    boxes_dir: Path | None = None,
) -> str:
    """Pick a collision-free name from the basename of *path* WITHOUT writing.

    The candidate-selection core of :func:`assign_name`, split out so the
    deferred-registration (interrupted-create) path can obtain the name a box
    will be registered under WITHOUT writing the registry yet (marker → seed →
    register → remove-marker, B3).

    Collisions append a number: ``name``, ``name2``, ``name3``, ...  A candidate
    is rejected when it is already a registered name (either section) OR — when
    *boxes_dir* is supplied — when its box directory ``boxes_dir/<candidate>``
    already EXISTS on disk.  The directory check guards the deferred-registration
    window: a half-built box (dir present, name not yet registered after a crash)
    keeps its name reserved so a SECOND create cannot grab it and seed over the
    interrupted box's home.  Recovery of that interrupted box resolves it by its
    directory, not through ``pick_name``.

    Performs NO registration and NO filesystem mutation — caller registers (or
    defers).
    """
    base = Path(path).name
    if not base:
        base = "project"

    names = _load(registry)
    all_names = set(names["projects"]) | set(names["worksets"])

    def taken(cand: str) -> bool:
        if cand in all_names:
            return True
        if boxes_dir is not None and (boxes_dir / cand).exists():
            return True
        return False

    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}{n}"
        n += 1

    return candidate


def assign_name(
    registry: Path,
    path: str,
    section: str = "projects",
    boxes_dir: Path | None = None,
) -> str:
    """Auto-assign a name from the basename of *path*.

    Handles collisions by appending a number: ``name``, ``name2``, ``name3``, ...
    Registers the name and returns it.  Equivalent to :func:`pick_name` followed
    by :func:`register_name` (behavior-identical for existing callers; *boxes_dir*
    is forwarded to the directory-aware collision check when supplied).
    """
    candidate = pick_name(registry, path, section=section, boxes_dir=boxes_dir)
    register_name(registry, candidate, path, section=section)
    return candidate
