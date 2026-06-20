"""Drop-in import-on-discovery: reconcile the registry from on-disk truth.

On-disk metadata is **authoritative**; ``system.registry`` is a *derived,
rebuildable index*.  When detection/resolution encounters an on-disk
box/workset/project that is NOT in the registry, kanibako **imports** it:
registers it into the appropriate registry section, prints an ALERT to stderr,
then proceeds — with **no confirmation prompt**.  This is what lets a user copy
or move a box/workset/project tree to a new location (or machine) and have
kanibako re-discover it (it "effectively becomes an import").

Three modes are supported, all backed by one uniform "reconcile registry from
on-disk truth" mechanism (no per-mode special-casing):

* **STANDALONE** — :func:`import_standalone`, called lazily during the
  ancestor-walk when a ``box_data/`` marker is found whose box is not in
  ``registry.standalone``.
* **NAMED** — :func:`import_named_workset`, called lazily during the walk when a
  workset-root marker (``workset.yaml``) is found that is not a registered
  workset root.
* **PRIMARY** — :func:`import_primary_box` (single box) and
  :func:`reconcile_primary_boxes` (scan ``@system.primary_workset/boxes/*``),
  for re-associating a central primary box with its external workspace.

Conflict semantics (all modes): if the import's NAME collides with an entity
already registered to a *different* root/path, the import is **REFUSED** —
nothing is mutated and :class:`ImportConflictError` is raised explaining that a
``rename`` mechanism is coming (future work).  If the on-disk entity is already
registered to its current path, the import is a silent idempotent no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kanibako import registry_store
from kanibako.config import read_project_meta
from kanibako.errors import KanibakoError


class ImportConflictError(KanibakoError):
    """A drop-in import was refused because its name collides with a different
    already-registered entity.

    The import leaves the on-disk entity untouched and mutates nothing in the
    registry.  Resolving the collision needs a ``rename`` mechanism (future
    work, not 1.6.0).
    """


def _alert(mode: str, name: str, path: Path) -> None:
    """Print the import ALERT to stderr (no confirmation; the import proceeds)."""
    print(f"Imported {mode} '{name}' at {path}", file=sys.stderr)


def _persist_box_name(meta_path: Path, name: str) -> None:
    """Write *name* into a box metadata file's ``project.name`` key.

    Mirrors where :func:`kanibako.config.write_project_meta` stores the box name
    (``project.name``), preserving every other section of the file.
    """
    from kanibako.config_io import dump_doc, load_doc

    data = load_doc(meta_path)
    project_sec = data.get("project")
    if not isinstance(project_sec, dict):
        project_sec = {}
        data["project"] = project_sec
    project_sec["name"] = name
    dump_doc(meta_path, data)


def _conflict(
    mode: str, name: str, new_path: Path, existing_path: str,
) -> ImportConflictError:
    """Build the shared REFUSE error explaining the name collision."""
    return ImportConflictError(
        f"Cannot import {mode} '{name}' at {new_path}: the name '{name}' is "
        f"already registered to a different location ({existing_path}). "
        "Refusing the import to avoid clobbering the existing entry. "
        "A 'rename' mechanism to resolve such collisions is planned "
        "(future work); for now, rename or relocate one of them manually."
    )


# ---------------------------------------------------------------------------
# STANDALONE
# ---------------------------------------------------------------------------

def import_standalone(data_path: Path, root: Path) -> str | None:
    """Reconcile an on-disk standalone box at *root* against ``registry.standalone``.

    Behavior (see module docstring):

    * Already registered to *root* (by ``standalone_name_for_root``) → silent
      no-op, returns the registered name.
    * The box's persisted ``name`` (from ``box_data/project.yaml``) is missing
      or empty (a hand-created tree) → generate a fresh ``<random24>_<leaf>``
      name, persist it back into the metadata, register, alert, return it.
    * Otherwise the persisted name is registered to *root* + alerted, UNLESS it
      already maps to a DIFFERENT root → :class:`ImportConflictError` (refuse,
      no mutation).

    *root* is the standalone project root (the dir containing ``box_data/``).
    Returns the registered box name, or ``None`` when *root* has no readable
    standalone metadata (nothing to import).
    """
    root = root.resolve()
    root_str = str(root)

    # Already registered to this exact root → idempotent no-op.
    existing_name = registry_store.standalone_name_for_root(data_path, root)
    if existing_name is not None:
        return existing_name

    meta_path = root / "box_data" / "project.yaml"
    meta = read_project_meta(meta_path)
    if not meta:
        return None  # No standalone metadata on disk → nothing to import.

    persisted_name = (meta.get("name") or "").strip()
    registered = registry_store.load_standalone(data_path)

    if not persisted_name:
        # Hand-created tree with no persisted identity: mint one and persist it.
        from kanibako import box_identity

        name = box_identity.make_standalone_box_name(
            root, registry_store.standalone_box_names(data_path),
        )
        # Persist the generated name back into the box metadata (under
        # ``project.name``, matching write/read_project_meta) so the identity is
        # stable across future resolves.
        _persist_box_name(meta_path, name)
        registry_store.register_standalone(data_path, name, root)
        _alert("standalone box", name, root)
        return name

    # Persisted name present: collision check against a DIFFERENT root.
    other_root = registered.get(persisted_name)
    if other_root is not None and other_root != root_str:
        raise _conflict("standalone box", persisted_name, root, other_root)

    registry_store.register_standalone(data_path, persisted_name, root)
    _alert("standalone box", persisted_name, root)
    return persisted_name


# ---------------------------------------------------------------------------
# NAMED (worksets)
# ---------------------------------------------------------------------------

def import_named_workset(data_path: Path, root: Path) -> str | None:
    """Reconcile an on-disk workset at *root* against the workset registry.

    Reads the workset name from *root*'s ``workset.yaml`` and reconciles it
    against ``registry.workset_roots`` (the name → root index that backs workset
    discovery, written by :mod:`kanibako.workset`):

    * The name is already registered to *root* → silent idempotent no-op.
    * The name is registered to a DIFFERENT root → :class:`ImportConflictError`.
    * Otherwise register name → root (in both the ``workset_roots`` and the
      ``worksets`` name-index sections, matching ``create_workset``), alert,
      return the name.

    Returns the workset name, or ``None`` when *root* has no readable
    ``workset.yaml`` (nothing to import).  Does NOT rewrite the workset-create
    skeleton — it only registers an already-on-disk workset.
    """
    root = root.resolve()
    root_str = str(root)

    toml = root / "workset.yaml"
    if not toml.is_file():
        return None

    from kanibako.config_io import load_doc

    try:
        data = load_doc(toml)
    except Exception:
        return None
    name = (data.get("name") or "").strip() if isinstance(data, dict) else ""
    if not name:
        return None

    roots_section = registry_store.load_section(data_path, "workset_roots")
    current = roots_section.get(name)
    if current is not None:
        if str(Path(current).resolve()) == root_str:
            return name  # Already registered to this root → no-op.
        raise _conflict("workset", name, root, str(current))

    # Register name → root in both sections (mirrors create_workset's two
    # writes: the workset_roots discovery registry + the worksets name index).
    roots_section[name] = root_str
    registry_store.save_section(data_path, "workset_roots", roots_section)
    names_section = registry_store.load_section(data_path, "worksets")
    names_section[name] = root_str
    registry_store.save_section(data_path, "worksets", names_section)
    _alert("workset", name, root)
    return name


# ---------------------------------------------------------------------------
# PRIMARY (central box store → external workspace)
# ---------------------------------------------------------------------------

def import_primary_box(data_path: Path, box_dir: Path) -> str | None:
    """Reconcile a single on-disk PRIMARY box at *box_dir* against ``registry.projects``.

    *box_dir* is a per-box directory under ``@system.primary_workset/boxes/``;
    its ``project.yaml`` carries ``project.name`` (the box name) and the real
    external ``workspace`` dir.  Reconciles name → workspace against
    ``registry.projects``:

    * The name is already registered to the box's workspace → silent no-op.
    * The name is registered to a DIFFERENT workspace → :class:`ImportConflictError`.
    * Otherwise register name → workspace, alert, return the name.

    Returns the box name, or ``None`` when *box_dir* has no readable metadata or
    no workspace recorded (nothing to import).
    """
    meta = read_project_meta(box_dir / "project.yaml")
    if not meta:
        return None
    name = (meta.get("name") or "").strip()
    workspace = (meta.get("workspace") or "").strip()
    if not name or not workspace:
        return None
    workspace_str = str(Path(workspace).resolve())

    projects = registry_store.load_section(data_path, "projects")
    current = projects.get(name)
    if current is not None:
        if str(Path(current).resolve()) == workspace_str:
            return name  # Already registered to this workspace → no-op.
        raise _conflict("primary box", name, Path(workspace_str), str(current))

    projects[name] = workspace_str
    registry_store.save_section(data_path, "projects", projects)
    _alert("primary box", name, Path(workspace_str))
    return name


def import_primary_box_for_workspace(
    data_path: Path, boxes_dir: Path, workspace: Path,
) -> str | None:
    """Import the on-disk PRIMARY box whose recorded workspace is *workspace*.

    Scans *boxes_dir* (``@system.primary_workset/boxes``) for a box dir whose
    ``project.yaml`` records *workspace* as its workspace and is NOT yet in
    ``registry.projects``, then imports it (alert + register).  Used by
    :func:`kanibako.paths.resolve_project` to re-discover a primary box that was
    dropped in / moved when the registry has no name → path entry for it.

    Returns the imported box name, or ``None`` when no matching unregistered
    on-disk box is found.  Idempotent: a box already registered to *workspace*
    is found by the caller's normal reverse-lookup, so this is only reached on a
    genuine miss.
    """
    if not boxes_dir.is_dir():
        return None
    target = str(workspace.resolve())
    for entry in sorted(boxes_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta = read_project_meta(entry / "project.yaml")
        if not meta:
            continue
        ws = (meta.get("workspace") or "").strip()
        if not ws or str(Path(ws).resolve()) != target:
            continue
        return import_primary_box(data_path, entry)
    return None


def reconcile_primary_boxes(data_path: Path, boxes_dir: Path) -> list[str]:
    """Scan *boxes_dir* for on-disk PRIMARY boxes missing from ``registry.projects``.

    Imports each discovered-but-unregistered box via :func:`import_primary_box`
    (alert + register).  This is the registry-wide reconcile a future
    ``box import`` / ``diagnose`` would expose, and the same primitive the
    per-box import uses.  *boxes_dir* is ``@system.primary_workset/boxes``
    (``std.boxes``).

    Returns the list of box names imported during this scan (empty when every
    on-disk box is already registered).  A name-collision in any single box
    raises :class:`ImportConflictError` (the offending box is not imported);
    boxes already scanned before the conflict remain imported (each import is an
    independent atomic registry write).
    """
    if not boxes_dir.is_dir():
        return []
    imported: list[str] = []
    for entry in sorted(boxes_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta = read_project_meta(entry / "project.yaml")
        if not meta:
            continue
        name = (meta.get("name") or "").strip()
        if not name:
            continue
        # Only import the ones missing (idempotent: registered → no-op, and
        # import_primary_box returns the name without alerting).
        projects = registry_store.load_section(data_path, "projects")
        if name in projects:
            # Already present — let import_primary_box decide no-op vs conflict.
            import_primary_box(data_path, entry)
            continue
        if import_primary_box(data_path, entry) is not None:
            imported.append(name)
    return imported
