"""Working set data model and persistence.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  The layout is:

    {root}/
        workset.yaml              ← workset metadata + project list
        projects/{name}/          ← per-project metadata + home
            home/                 ← agent home (mounted as /home/agent)
            project.yaml          ← per-project config
            .kanibako.lock        ← concurrency lock
        workspaces/{name}/        ← per-project workspace (source tree)
        vault/{name}/ro/    ← per-project read-only vault
        vault/{name}/rw/    ← per-project read-write vault

A global registry at ``$XDG_DATA_HOME/kanibako/worksets.yaml`` maps workset
names to root paths so they can be discovered from anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import WorksetError
from kanibako.names import read_names, register_name, unregister_name
from kanibako.paths import StandardPaths


# Identity of the synthesized "default" workset (the group of default-mode
# projects).  This workset is virtual — it is never written to disk.
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"


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
    group_auth: bool = field(default=True)  # True = shared creds, False = distinct
    is_default: bool = False                 # True = synthesized default workset

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
        return self.root / "workset.yaml"


# ---------------------------------------------------------------------------
# workset.yaml (at workset root)
# ---------------------------------------------------------------------------

def _write_workset_toml(ws: Workset) -> None:
    """Serialize *ws* to ``workset.yaml`` at the workset root."""
    data: dict = {
        "name": ws.name,
        "created": ws.created,
        "group_auth": ws.group_auth,
        "projects": [
            {"name": proj.name, "source_path": str(proj.source_path)}
            for proj in ws.projects
        ],
    }
    dump_doc(ws.toml_path, data)


def _load_workset_toml(root: Path) -> Workset:
    """Read ``workset.yaml`` from *root* and return a ``Workset``."""
    toml_path = root / "workset.yaml"
    if not toml_path.is_file():
        raise WorksetError(f"No workset.yaml in {root}")
    data = load_doc(toml_path)
    name = data.get("name")
    if not name:
        raise WorksetError(f"workset.yaml in {root} has no 'name' key")
    created = data.get("created", "")
    group_auth = bool(data.get("group_auth", True))
    projects = []
    for entry in data.get("projects", []):
        projects.append(
            WorksetProject(
                name=entry["name"],
                source_path=Path(entry["source_path"]),
            )
        )
    return Workset(name=name, root=root, created=created, projects=projects, group_auth=group_auth)


# ---------------------------------------------------------------------------
# Global registry: $XDG_DATA_HOME/kanibako/worksets.yaml
# ---------------------------------------------------------------------------

def _registry_path(std: StandardPaths) -> Path:
    return std.ws_hints


def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    path = _registry_path(std)
    if not path.is_file():
        return {}
    data = load_doc(path)
    return {
        name: Path(root)
        for name, root in data.get("worksets", {}).items()
    }


def _write_registry(std: StandardPaths, registry: dict[str, Path]) -> None:
    """Overwrite the global worksets registry."""
    path = _registry_path(std)
    data: dict = {
        "worksets": {name: str(registry[name]) for name in sorted(registry)},
    }
    dump_doc(path, data)


# ---------------------------------------------------------------------------
# Connected (external) redirect index: $XDG_DATA_HOME/kanibako/connected.yaml
#
# Maps a resolved EXTERNAL source path to its owning workset+project so that
# launching from the external directory (or a subdir of it) resolves to the
# workset.  Only external connects are recorded here; sources inside a workset
# tree are resolved by ordinary location detection.  Schema:
#
#     connected:
#       /abs/external/repo: {workset: myws, project: foo}
# ---------------------------------------------------------------------------

def _connected_path(std: StandardPaths) -> Path:
    return std.data_path / "connected.yaml"


def _load_connected(std: StandardPaths) -> dict[str, dict]:
    """Return ``{abs_path: {"workset": ..., "project": ...}}`` from the index."""
    path = _connected_path(std)
    if not path.is_file():
        return {}
    data = load_doc(path)
    return dict(data.get("connected", {}))


def _write_connected(std: StandardPaths, mapping: dict[str, dict]) -> None:
    """Overwrite the connected-external redirect index."""
    path = _connected_path(std)
    data: dict = {
        "connected": {key: mapping[key] for key in sorted(mapping)},
    }
    dump_doc(path, data)


def _find_connected_project(
    path: Path, std: StandardPaths,
) -> tuple[Workset, str] | None:
    """Resolve *path* (or an ancestor) to its connected workset+project.

    Walks *path* and its ancestors (same ancestor semantics as
    ``_find_local_ancestor`` in paths.py): the deepest registered external
    source path that is *path* or an ancestor of it wins.  Returns
    ``(load_workset(root), project_name)`` on a hit, else ``None``.
    """
    mapping = _load_connected(std)
    if not mapping:
        return None
    resolved = path.resolve()
    best_key: str | None = None
    best_depth = -1
    for key in mapping:
        registered = Path(key)
        try:
            resolved.relative_to(registered)
        except ValueError:
            continue
        depth = len(registered.parts)
        if depth > best_depth:
            best_key = key
            best_depth = depth
    if best_key is None:
        return None
    entry = mapping[best_key]
    ws_name = entry.get("workset")
    project_name = entry.get("project")
    if not ws_name or not project_name:
        return None
    registry = _load_registry(std)
    root = registry.get(ws_name)
    if root is None:
        return None
    return load_workset(root), project_name


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

    if name in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        raise WorksetError("'default' is reserved for the default workset.")

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

    Raises ``WorksetError`` if the directory or ``workset.yaml`` is missing.
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
    """Return ``{name: root_path}`` for all registered worksets.

    This returns ONLY the on-disk registry; the synthesized default workset is
    never injected here.
    """
    return _load_registry(std)


def default_workset(std: StandardPaths) -> Workset:
    """Synthesize the default workset (the group of default-mode projects).

    The default workset is virtual: its members are the default-mode projects
    in ``names.yaml [projects]`` and its ``group_auth`` lives as a normal key in
    ``{data_path}/config.yaml``.  This object is NEVER persisted to disk (no
    workset.yaml / registry write).
    """
    projects_map = read_names(std.data_path).get("projects", {})
    projects = [
        WorksetProject(name=name, source_path=Path(path))
        for name, path in projects_map.items()
    ]

    group_auth = True
    config_path = std.data_path / "config.yaml"
    if config_path.is_file():
        data = load_doc(config_path)
        # group_auth lives in the [project] section (see config.py loader);
        # tolerate a top-level key too for robustness.
        if "group_auth" in data.get("project", {}):
            group_auth = bool(data["project"]["group_auth"])
        elif "group_auth" in data:
            group_auth = bool(data["group_auth"])

    return Workset(
        name=DEFAULT_WORKSET_ID,
        root=std.data_path,
        created="",
        projects=projects,
        group_auth=group_auth,
        is_default=True,
    )


def resolve_workset_name(name: str, std: StandardPaths) -> Workset:
    """Resolve a workset *name* to a :class:`Workset`.

    The names ``default`` / ``__default__`` resolve to the synthesized default
    workset; any other name is looked up in the on-disk registry.  Raises
    ``WorksetError`` if the name is not registered.
    """
    if name in (DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS):
        return default_workset(std)
    registry = _load_registry(std)
    if name not in registry:
        raise WorksetError(f"Working set '{name}' is not registered.")
    return load_workset(registry[name])


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


def add_project(
    ws: Workset,
    name: str,
    source_path: Path,
    std: StandardPaths | None = None,
) -> WorksetProject:
    """Add a project to a workset.  Creates per-project subdirectories.

    When *source_path* resolves OUTSIDE the workset root and *std* is provided,
    the project is connected to that external directory: the external dir
    becomes the live workspace.  In that case ``workspaces/{name}`` is created
    as a SYMLINK to the external dir (discoverability only — never mounted), a
    ``workspace`` override is written into the project's ``project.yaml``, and a
    redirect entry is recorded in ``connected.yaml`` so launches from the
    external path resolve back to this workset.  Sources inside the workset
    tree keep the normal behavior (a real ``workspaces/{name}`` directory).

    Raises ``WorksetError`` if a project with *name* already exists.
    """
    for p in ws.projects:
        if p.name == name:
            raise WorksetError(
                f"Project '{name}' already exists in workset '{ws.name}'."
            )

    resolved_source = source_path.resolve()

    # Determine whether the source is external (outside the workset root).
    is_external = False
    if std is not None:
        try:
            resolved_source.relative_to(ws.root.resolve())
        except ValueError:
            is_external = True

    # Validate up front (failure-consistency): for an EXTERNAL source, refuse
    # BEFORE creating any directories if connecting it would mis-resolve.  An
    # external dir that is itself inside another registered workset's tree would
    # be shadowed by ordinary in-tree location detection; an already-connected
    # dir (or a dir nested under one) is a 1:1-mapping conflict.  Internal
    # sources and std-less callers (e.g. migrate) are unaffected.
    if std is not None and is_external:
        target_root = ws.root.resolve()
        for other_name, other_root in _load_registry(std).items():
            other_root = Path(other_root).resolve()
            if other_root == target_root:
                continue
            try:
                resolved_source.relative_to(other_root)
            except ValueError:
                continue
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it lives inside workset "
                f"'{other_name}' ({other_root}). It would be shadowed by "
                "in-tree detection and mis-resolve. Move it outside that "
                "workset, or connect it to that workset instead."
            )

        existing = _find_connected_project(resolved_source, std)
        if existing is not None:
            other_ws, other_proj = existing
            raise WorksetError(
                f"Cannot connect '{resolved_source}': it is already connected "
                f"as project '{other_proj}' in workset '{other_ws.name}'. "
                "Disconnect it first."
            )

    # Create per-project directories (boxes always real).
    (ws.projects_dir / name).mkdir(parents=True, exist_ok=True)
    vault_proj = ws.vault_dir / name
    (vault_proj / "ro").mkdir(parents=True, exist_ok=True)
    (vault_proj / "rw").mkdir(parents=True, exist_ok=True)

    if is_external:
        # External: workspaces/{name} is a self-documenting symlink to the
        # external dir (never mounted — the bind-mount uses the workspace
        # override).  Write the override + record the redirect.
        ws.workspaces_dir.mkdir(parents=True, exist_ok=True)
        link = ws.workspaces_dir / name
        if not link.exists() and not link.is_symlink():
            link.symlink_to(resolved_source)

        from kanibako.config import write_project_meta

        project_toml = ws.projects_dir / name / "project.yaml"
        write_project_meta(
            project_toml,
            mode="workset",
            layout="",
            workspace=str(resolved_source),
            shell="",
            vault_ro="",
            vault_rw="",
            name=name,
        )

        mapping = _load_connected(std)
        mapping[str(resolved_source)] = {"workset": ws.name, "project": name}
        _write_connected(std, mapping)
    else:
        # Internal (or no std): a real workspace directory.
        (ws.workspaces_dir / name).mkdir(parents=True, exist_ok=True)

    proj = WorksetProject(name=name, source_path=resolved_source)
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
