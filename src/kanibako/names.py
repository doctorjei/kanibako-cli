"""Project name registry (names.toml).

Central index at ``{data_path}/names.toml`` mapping human-readable names to
project paths (for account-centric projects) and workset roots (for worksets).
Decentralized projects are intentionally excluded — they have no central
registration.

The file has two sections::

    [projects]
    myapp = "/home/user/projects/myapp"

    [worksets]
    clientwork = "/home/user/worksets/client"
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from kanibako.errors import ProjectError


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _names_path(data_path: Path) -> Path:
    return data_path / "names.toml"


def _load(data_path: Path) -> dict[str, dict[str, str]]:
    """Load names.toml and return raw sections."""
    path = _names_path(data_path)
    if not path.is_file():
        return {"projects": {}, "worksets": {}}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {
        "projects": {k: str(v) for k, v in data.get("projects", {}).items()},
        "worksets": {k: str(v) for k, v in data.get("worksets", {}).items()},
    }


def _save(data_path: Path, names: dict[str, dict[str, str]]) -> None:
    """Write names.toml from sections dict."""
    path = _names_path(data_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section in ("projects", "worksets"):
        entries = names.get(section, {})
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for name in sorted(entries):
            lines.append(f'{name} = "{entries[name]}"')
    lines.append("")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_names(data_path: Path) -> dict[str, dict[str, str]]:
    """Load names.toml.

    Returns ``{"projects": {name: path, ...}, "worksets": {name: path, ...}}``.
    """
    return _load(data_path)


def register_name(
    data_path: Path,
    name: str,
    path: str,
    section: str = "projects",
) -> None:
    """Register a name → path mapping.

    Raises ``ProjectError`` if *name* is already registered in either section.
    """
    names = _load(data_path)
    # Check for duplicates across both sections.
    for sec in ("projects", "worksets"):
        if name in names[sec]:
            raise ProjectError(
                f"Name '{name}' is already registered"
                f" ({sec}: {names[sec][name]})"
            )
    names[section][name] = path
    _save(data_path, names)


def unregister_name(
    data_path: Path,
    name: str,
    section: str = "projects",
) -> bool:
    """Remove a name from the registry.

    Returns True if the name was found and removed, False otherwise.
    """
    names = _load(data_path)
    if name not in names.get(section, {}):
        return False
    del names[section][name]
    _save(data_path, names)
    return True


def resolve_name(
    data_path: Path,
    name: str,
    cwd: Path | None = None,
) -> tuple[str, str]:
    """Look up a bare name and return ``(path, kind)``.

    Resolution order:

    1. If *cwd* is inside a workset → check that workset's projects first
    2. ``[projects]`` section (AC projects)
    3. ``[worksets]`` section (workset names)

    *kind* is ``"project"`` or ``"workset"``.
    Raises ``ProjectError`` if no match is found.
    """
    names = _load(data_path)

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

    # 2. AC projects.
    if name in names["projects"]:
        return names["projects"][name], "project"

    # 3. Worksets.
    if name in names["worksets"]:
        return names["worksets"][name], "workset"

    raise ProjectError(f"Unknown project or workset: '{name}'")


def resolve_qualified_name(
    data_path: Path,
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
    names = _load(data_path)

    if ws_name not in names["worksets"]:
        raise ProjectError(f"Unknown workset: '{ws_name}'")

    ws_root = Path(names["worksets"][ws_name])
    candidate = ws_root / "workspaces" / proj_name
    if not candidate.is_dir():
        raise ProjectError(
            f"Project '{proj_name}' not found in workset '{ws_name}'"
        )
    return str(candidate), ws_name


def assign_name(
    data_path: Path,
    path: str,
    section: str = "projects",
) -> str:
    """Auto-assign a name from the basename of *path*.

    Handles collisions by appending a number: ``name``, ``name2``, ``name3``, ...
    Registers the name and returns it.
    """
    base = Path(path).name
    if not base:
        base = "project"

    names = _load(data_path)
    all_names = set(names["projects"]) | set(names["worksets"])

    candidate = base
    n = 2
    while candidate in all_names:
        candidate = f"{base}{n}"
        n += 1

    register_name(data_path, candidate, path, section=section)
    return candidate
