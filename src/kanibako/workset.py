"""Working set data model and persistence.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  The layout is:

    {root}/
        workset.yaml              ← workset metadata + project list
        projects/{name}/          ← per-project metadata + home
            home/                 ← agent home (mounted as /home/agent)
            settings.yaml          ← per-project config
            .kanibako.lock        ← concurrency lock
        workspaces/{name}/        ← per-project workspace (source tree)
        vault/ro/{name}/    ← per-project read-only vault
        vault/rw/{name}/    ← per-project read-write vault

A global registry at ``$XDG_DATA_HOME/kanibako/worksets.yaml`` maps workset
names to root paths so they can be discovered from anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from kanibako import registry_store
from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import WorksetError
from kanibako.names import read_names, register_name, unregister_name
from kanibako.paths import StandardPaths


# ---------------------------------------------------------------------------
# Failure-consistency: a tiny LIFO unwind stack for multi-step mutations.
#
# Several public mutators below touch more than one file (a workset.yaml plus
# the global worksets.yaml/names.yaml registries, or a symlink + settings.yaml +
# connected.yaml redirect).  Individual writes are torn-file-safe (atomic temp +
# os.replace via config_io.dump_doc), but a crash *between* steps could strand a
# half-applied cross-file state: registry says X while disk says Y, an orphan
# symlink, or an external path locked out by a dangling connected.yaml redirect.
#
# The unwind stack mirrors the lifecycle family's pattern (_lifecycle._Unwind):
# each forward step pushes a compensating action; on any exception we run them
# in reverse (best-effort, swallowing secondary failures) and re-raise, leaving
# the op either fully applied or fully rolled back.  These sequences are short
# (2-5 steps), so this stays deliberately small rather than a generic framework.
# ---------------------------------------------------------------------------

class _Unwind:
    """LIFO stack of compensating actions for fail-consistent mutations."""

    def __init__(self) -> None:
        self._actions: list[Callable[[], None]] = []

    def push(self, action: Callable[[], None]) -> None:
        self._actions.append(action)

    def run(self) -> None:
        while self._actions:
            action = self._actions.pop()
            try:
                action()
            except Exception:  # noqa: BLE001 - best-effort restore
                pass


# Identity of the synthesized "default" workset (the group of default-mode
# projects).  This workset is virtual — it is never written to disk.
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"

# Sentinel workset names reserved by the three-mode model (TARGET §2c): they
# address the PRIMARY and STANDALONE channel partitions and must never be a
# user-chosen NAMED-workset name.  The legacy ``default``/``__default__`` pair
# (now meaning "primary") is also reserved.  A workset name is a user-typed
# shared-channel address, so collisions here are refused, never auto-resolved.
RESERVED_WORKSET_NAMES = frozenset(
    {DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS, "__PRIMARY__", "__STANDALONE__"}
)


def is_reserved_workset_name(name: str) -> bool:
    """Return True if *name* is a reserved sentinel (cannot be a NAMED workset)."""
    return name in RESERVED_WORKSET_NAMES


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
# Global worksets registry: the ``worksets`` section of ``system.registry``.
# ---------------------------------------------------------------------------

def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry."""
    section = registry_store.load_section(std.data_path, "workset_roots")
    return {name: Path(root) for name, root in section.items()}


def _write_registry(std: StandardPaths, registry: dict[str, Path]) -> None:
    """Overwrite the global worksets registry."""
    entries = {name: str(registry[name]) for name in sorted(registry)}
    registry_store.save_section(std.data_path, "workset_roots", entries)


# ---------------------------------------------------------------------------
# Connected (external) redirect index: the ``connected`` section of
# ``system.registry``.
#
# Maps a resolved EXTERNAL source path to its owning workset+project so that
# launching from the external directory (or a subdir of it) resolves to the
# workset.  Only external connects are recorded here; sources inside a workset
# tree are resolved by ordinary location detection.  Schema:
#
#     connected:
#       /abs/external/repo: {workset: myws, project: foo}
# ---------------------------------------------------------------------------

def _load_connected(std: StandardPaths) -> dict[str, dict]:
    """Return ``{abs_path: {"workset": ..., "project": ...}}`` from the index."""
    return dict(registry_store.load_section(std.data_path, "connected"))


def _write_connected(std: StandardPaths, mapping: dict[str, dict]) -> None:
    """Overwrite the connected-external redirect index."""
    entries = {key: mapping[key] for key in sorted(mapping)}
    registry_store.save_section(std.data_path, "connected", entries)


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

    if is_reserved_workset_name(name):
        raise WorksetError(
            f"Workset name '{name}' is reserved and cannot be used. The names "
            f"{', '.join(sorted(RESERVED_WORKSET_NAMES))} are reserved sentinels "
            "for the primary and standalone partitions. Choose another name."
        )

    # A NAMED workset's name is a user-typed shared-channel address, so it must
    # be unique across all registered worksets (D-B3). Refuse on collision —
    # never auto-suffix — and point the user at the existing root.
    registry = _load_registry(std)
    if name in registry:
        raise WorksetError(
            f"Workset name '{name}' is already in use (registered at "
            f"{registry[name]}). Workset names must be unique; choose a "
            "different name."
        )

    root = root.resolve()
    if root.exists():
        raise WorksetError(f"Workset root already exists: {root}")

    # Multi-step: create disk skeleton + workset.yaml, then the global
    # worksets.yaml registry, then the names.yaml index.  A crash between any two
    # would leave orphan dirs (not in the registry) or a worksets.yaml entry with
    # no names.yaml index (bare-name resolution fails while `workset list`
    # works).  Track forward effects and unwind in reverse on any failure so the
    # create is all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # Create directory skeleton.
        root.mkdir(parents=True)
        unwind.push(lambda: shutil.rmtree(root, ignore_errors=True))
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
        unwind.push(lambda: _unregister_workset(std, name))

        # Register in the name index for name-based lookups.
        register_name(std.data_path, name, str(root), section="worksets")
    except Exception:
        unwind.run()
        raise

    return ws


def _unregister_workset(std: StandardPaths, name: str) -> None:
    """Drop *name* from the global worksets.yaml registry (compensating action)."""
    registry = _load_registry(std)
    if name in registry:
        del registry[name]
        _write_registry(std, registry)


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
    name_index = read_names(std.data_path).get("worksets", {})
    in_registry = name in registry
    # Self-healing: also recognize a workset that survives ONLY in the name
    # index (e.g. a prior delete that crashed after the worksets.yaml write but
    # before unregister_name).  Re-running delete then cleans BOTH halves rather
    # than raising "not registered" and stranding the stale name-index entry.
    if not in_registry and name not in name_index:
        raise WorksetError(f"Workset '{name}' is not registered.")

    root = registry.pop(name) if in_registry else Path(name_index[name])

    # Drop both registry halves (both writes are idempotent: a missing entry is
    # a no-op, so a partially-cleaned prior state heals on re-run).  The two
    # removes leave a transient list-vs-index mismatch on a crash between them,
    # but that state is recognized + fully cleaned by the next delete.
    _write_registry(std, registry)
    unregister_name(std.data_path, name, section="worksets")

    # Irreversible step LAST: only after both registry halves are clean.
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
    ``workspace`` override is written into the project's ``settings.yaml``, and a
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

    # Multi-step mutation (external case touches a symlink + settings.yaml +
    # connected.yaml redirect + the durable workset.yaml registry write).  A
    # crash between steps could strand a symlink/redirect with no workset.yaml
    # entry (external path locked out by a dangling connected.yaml redirect) or
    # vice-versa.  Track forward effects on an unwind stack; on any failure undo
    # in reverse + re-raise so the connect is all-or-nothing.  Success behavior
    # is identical to before (same final state).
    import shutil

    unwind = _Unwind()
    try:
        # Create per-project directories (boxes always real).  exist_ok=True keeps
        # this idempotent; only rmtree on unwind dirs we (may have) created.
        proj_box = ws.projects_dir / name
        existed_box = proj_box.exists()
        proj_box.mkdir(parents=True, exist_ok=True)
        if not existed_box:
            unwind.push(lambda: shutil.rmtree(proj_box, ignore_errors=True))

        # Vault ro/rw split nests ABOVE the box name (vault/{ro,rw}/<name>) to
        # match PRIMARY and STANDALONE.  Unwind removes the per-box ro/rw leaves
        # only — never the shared vault/ro or vault/rw parents.
        vault_ro_proj = ws.vault_dir / "ro" / name
        vault_rw_proj = ws.vault_dir / "rw" / name
        existed_vault_ro = vault_ro_proj.exists()
        existed_vault_rw = vault_rw_proj.exists()
        vault_ro_proj.mkdir(parents=True, exist_ok=True)
        vault_rw_proj.mkdir(parents=True, exist_ok=True)
        if not existed_vault_ro:
            unwind.push(lambda: shutil.rmtree(vault_ro_proj, ignore_errors=True))
        if not existed_vault_rw:
            unwind.push(lambda: shutil.rmtree(vault_rw_proj, ignore_errors=True))

        if is_external:
            # External: workspaces/{name} is a self-documenting symlink to the
            # external dir (never mounted — the bind-mount uses the workspace
            # override).  Write the override + record the redirect.
            # is_external can only be True when std is not None (set above),
            # but mypy can't track that cross-variable invariant.
            assert std is not None
            ws.workspaces_dir.mkdir(parents=True, exist_ok=True)
            link = ws.workspaces_dir / name
            if not link.exists() and not link.is_symlink():
                link.symlink_to(resolved_source)
                unwind.push(
                    lambda: link.unlink() if link.is_symlink() else None
                )

            from kanibako.config import BOX_META_FILE, write_project_meta

            project_toml = ws.projects_dir / name / BOX_META_FILE
            write_project_meta(
                project_toml,
                mode="named",
                workspace=str(resolved_source),
                shell="",
                vault_ro="",
                vault_rw="",
                name=name,
            )

            mapping = _load_connected(std)
            mapping[str(resolved_source)] = {"workset": ws.name, "project": name}
            _write_connected(std, mapping)
            unwind.push(
                lambda: _drop_connected(std, str(resolved_source))
            )
        else:
            # Internal (or no std): a real workspace directory.
            ws_dir = ws.workspaces_dir / name
            existed_ws = ws_dir.exists()
            ws_dir.mkdir(parents=True, exist_ok=True)
            if not existed_ws:
                unwind.push(lambda: shutil.rmtree(ws_dir, ignore_errors=True))

        # Durable registry write LAST.  If it fails the unwind removes the
        # symlink/redirect/dirs above, leaving no orphaned connected.yaml entry.
        proj = WorksetProject(name=name, source_path=resolved_source)
        ws.projects.append(proj)
        unwind.push(lambda: _detach_project(ws, name))
        _write_workset_toml(ws)
    except Exception:
        unwind.run()
        raise

    return proj


def _drop_connected(std: StandardPaths, key: str) -> None:
    """Drop a connected.yaml redirect entry by source path (compensating action)."""
    mapping = _load_connected(std)
    if key in mapping:
        del mapping[key]
        _write_connected(std, mapping)


def _detach_project(ws: Workset, name: str) -> None:
    """Drop *name* from the in-memory project list (compensating action)."""
    ws.projects[:] = [p for p in ws.projects if p.name != name]


def remove_project(
    ws: Workset, name: str, *, remove_files: bool = False,
    std: StandardPaths | None = None,
) -> WorksetProject:
    """Remove a project from a workset.

    When *std* is provided, the external-connect markers written by
    :func:`add_project` are undone regardless of *remove_files*: any
    ``connected.yaml`` entry pointing at this workset+project is dropped and the
    ``workspaces/{name}`` symlink (external projects) is unlinked.  Unlinking a
    symlink removes ONLY the link — never the user's external source directory.
    With *remove_files* the workset-side per-project directories are removed too
    (symlinks unlinked, real dirs rmtree'd); the external source is left intact.

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

    # Failure-consistency ordering: clean the connected.yaml redirect + the
    # discoverability symlink BEFORE the durable workset.yaml registry write, so
    # the registry removal (which makes the project "gone") is the LAST durable
    # step.  A crash mid-cleanup then leaves the project still in workset.yaml —
    # a re-runnable state — instead of removing it from the registry while the
    # connected.yaml redirect still points at it (which would lock out the
    # external path).  The cleanups are idempotent: a re-run finds nothing to do.
    import shutil

    # Always undo the external-connect markers when we have std, so the
    # connected.yaml redirect never outlives the project (regression from the
    # connect-external work).
    if std is not None:
        mapping = _load_connected(std)
        stale = [
            key
            for key, entry in mapping.items()
            if entry.get("workset") == ws.name and entry.get("project") == name
        ]
        if stale:
            for key in stale:
                del mapping[key]
            _write_connected(std, mapping)

    # The workspaces/{name} marker is a SYMLINK for external projects; unlink it
    # (removes only the link, never the external target).  Do this regardless of
    # remove_files so the discoverability symlink never dangles.
    link = ws.workspaces_dir / name
    if link.is_symlink():
        link.unlink()

    # Durable registry removal LAST (after redirect + symlink are clean).
    ws.projects.remove(target)
    _write_workset_toml(ws)

    if remove_files:
        # Vault now nests ro/rw ABOVE the box name (vault/{ro,rw}/<name>), so
        # remove those per-box leaves — never the shared vault/ro or vault/rw
        # parents (they hold every box's vault).
        targets = (
            ws.projects_dir / name,
            ws.workspaces_dir / name,
            ws.vault_dir / "ro" / name,
            ws.vault_dir / "rw" / name,
        )
        for proj_dir in targets:
            if proj_dir.is_symlink():
                # Defensive: only the link is removed, never its target.
                proj_dir.unlink()
            elif proj_dir.is_dir():
                shutil.rmtree(proj_dir)

    return target
