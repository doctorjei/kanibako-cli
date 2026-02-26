"""Working set data model and persistence.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  The layout is:

    {root}/
        workset.toml              ← workset metadata + project list
        projects/{name}/          ← per-project metadata + home
            home/                 ← agent home (mounted as /home/agent)
            project.toml          ← per-project config
            .kanibako.lock        ← concurrency lock
        workspaces/{name}/        ← per-project workspace (source tree)
        vault/{name}/share-ro/    ← per-project read-only vault
        vault/{name}/share-rw/    ← per-project read-write vault

A global registry at ``$XDG_DATA_HOME/kanibako/worksets.toml`` maps workset
names to root paths so they can be discovered from anywhere.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kanibako.errors import WorksetError
from kanibako.names import register_name, unregister_name
from kanibako.paths import StandardPaths


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WorksetProject:
    """A project registered inside a workset."""

    name: str
    source_path: Path   # original project path (for reference / cloning)


@dataclass
class Workset:
    """In-memory representation of a workset."""

    name: str
    root: Path
    created: str                            # ISO 8601, UTC
    projects: list[WorksetProject] = field(default_factory=list)
    auth: str = field(default="shared")     # "shared" or "distinct"

    # Convenience paths -------------------------------------------------------

    @property
    def projects_dir(self) -> Path:
        return self.root / "boxes"

    @property
    def workspaces_dir(self) -> Path:
        return self.root / "workspaces"

    @property
    def vault_dir(self) -> Path:
        return self.root / "vault"

    @property
    def toml_path(self) -> Path:
        return self.root / "workset.toml"


# ---------------------------------------------------------------------------
# workset.toml (at workset root)
# ---------------------------------------------------------------------------

def _write_workset_toml(ws: Workset) -> None:
    """Serialize *ws* to ``workset.toml`` at the workset root."""
    lines = [
        f'name = "{ws.name}"',
        f'created = "{ws.created}"',
        f'auth = "{ws.auth}"',
        "",
    ]
    for proj in ws.projects:
        lines.append("[[projects]]")
        lines.append(f'name = "{proj.name}"')
        lines.append(f'source_path = "{proj.source_path}"')
        lines.append("")
    ws.toml_path.write_text("\n".join(lines))


def _load_workset_toml(root: Path) -> Workset:
    """Read ``workset.toml`` from *root* and return a ``Workset``."""
    toml_path = root / "workset.toml"
    if not toml_path.is_file():
        raise WorksetError(f"No workset.toml in {root}")
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    name = data.get("name")
    if not name:
        raise WorksetError(f"workset.toml in {root} has no 'name' key")
    created = data.get("created", "")
    auth = data.get("auth", "shared")
    projects = []
    for entry in data.get("projects", []):
        projects.append(
            WorksetProject(
                name=entry["name"],
                source_path=Path(entry["source_path"]),
            )
        )
    return Workset(name=name, root=root, created=created, projects=projects, auth=auth)


# ---------------------------------------------------------------------------
# Global registry: $XDG_DATA_HOME/kanibako/worksets.toml
# ---------------------------------------------------------------------------

def _registry_path(std: StandardPaths) -> Path:
    return std.data_path / "worksets.toml"


def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    path = _registry_path(std)
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {
        name: Path(root)
        for name, root in data.get("worksets", {}).items()
    }


def _write_registry(std: StandardPaths, registry: dict[str, Path]) -> None:
    """Overwrite the global worksets registry."""
    path = _registry_path(std)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[worksets]"]
    for name in sorted(registry):
        lines.append(f'"{name}" = "{registry[name]}"')
    lines.append("")
    path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_workset(name: str, root: Path, std: StandardPaths) -> Workset:
    """Create a new workset directory structure and register it globally.

    Raises ``WorksetError`` if *root* already exists or name is already
    registered.
    """
    if not name:
        raise WorksetError("Workset name must not be empty.")

    registry = _load_registry(std)
    if name in registry:
        raise WorksetError(
            f"Workset '{name}' is already registered at {registry[name]}"
        )

    root = root.resolve()
    if root.exists():
        raise WorksetError(f"Workset root already exists: {root}")

    # Create directory skeleton.
    root.mkdir(parents=True)
    for subdir in ("boxes", "workspaces", "vault"):
        (root / subdir).mkdir()

    ws = Workset(
        name=name,
        root=root,
        created=datetime.now(timezone.utc).isoformat(),
    )
    _write_workset_toml(ws)

    # Register globally.
    registry[name] = root
    _write_registry(std, registry)

    # Register in the name index for name-based lookups.
    register_name(std.data_path, name, str(root), section="worksets")

    return ws


def load_workset(root: Path) -> Workset:
    """Load a workset from its root directory.

    Raises ``WorksetError`` if the directory or ``workset.toml`` is missing.
    """
    root = root.resolve()
    if not root.is_dir():
        raise WorksetError(f"Workset root does not exist: {root}")
    # Migrate old kanibako/ subdir to boxes/ if needed.
    old_subdir = root / "kanibako"
    new_subdir = root / "boxes"
    if old_subdir.is_dir() and not new_subdir.exists():
        old_subdir.rename(new_subdir)
        import sys
        print(f"Migrated workset: {old_subdir} → {new_subdir}", file=sys.stderr)
    return _load_workset_toml(root)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` for all registered worksets."""
    return _load_registry(std)


def delete_workset(name: str, std: StandardPaths, *, remove_files: bool = False) -> Path:
    """Unregister a workset and optionally remove its directory tree.

    Returns the root path of the deleted workset.
    Raises ``WorksetError`` if the name is not registered.
    """
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Workset '{name}' is not registered.")
    root = registry.pop(name)
    _write_registry(std, registry)

    # Unregister from the name index.
    unregister_name(std.data_path, name, section="worksets")

    if remove_files and root.is_dir():
        import shutil
        shutil.rmtree(root)

    return root


def add_project(ws: Workset, name: str, source_path: Path) -> WorksetProject:
    """Add a project to a workset.  Creates per-project subdirectories.

    Raises ``WorksetError`` if a project with *name* already exists.
    """
    for p in ws.projects:
        if p.name == name:
            raise WorksetError(
                f"Project '{name}' already exists in workset '{ws.name}'."
            )

    # Create per-project directories.
    for parent in (ws.projects_dir, ws.workspaces_dir):
        (parent / name).mkdir(parents=True, exist_ok=True)
    vault_proj = ws.vault_dir / name
    (vault_proj / "share-ro").mkdir(parents=True, exist_ok=True)
    (vault_proj / "share-rw").mkdir(parents=True, exist_ok=True)

    proj = WorksetProject(name=name, source_path=source_path.resolve())
    ws.projects.append(proj)
    _write_workset_toml(ws)
    return proj


def remove_project(
    ws: Workset, name: str, *, remove_files: bool = False,
) -> WorksetProject:
    """Remove a project from a workset.

    Raises ``WorksetError`` if no project with *name* exists.
    """
    target = None
    for p in ws.projects:
        if p.name == name:
            target = p
            break
    if target is None:
        raise WorksetError(
            f"Project '{name}' not found in workset '{ws.name}'."
        )

    ws.projects.remove(target)
    _write_workset_toml(ws)

    if remove_files:
        import shutil
        for parent in (ws.projects_dir, ws.workspaces_dir, ws.vault_dir):
            proj_dir = parent / name
            if proj_dir.is_dir():
                shutil.rmtree(proj_dir)

    return target
