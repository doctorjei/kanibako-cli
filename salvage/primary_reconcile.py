"""FROZEN, NON-EXECUTING REFERENCE — the retired primary-box importers.

This module is **not part of the live product**, is **not shipped in the wheel**,
and is **not imported anywhere**. It is a frozen snapshot of the primary-box
drop-in reconcile-from-disk importers that were retired from the live path in
P8b (Option A: the registry is the sole authority for box identity — a box does
not self-describe its identity on disk). See ``salvage/README.md``.

It intentionally does NOT import the live ``kanibako.config.read_project_meta``
(deleted from the live tree in P8c). Instead it carries a **frozen local copy**
(``_read_project_meta``) of the minimal read logic those importers needed, so
the snapshot reads coherently on its own. Do not attempt to import this module
against the current tree; treat it as reference source only.

Under the current model a real ``system recover`` built from this pattern would
re-source identity from ``box_resolve.resolve_box_identity`` (dir layout +
``box_data/`` marker + the sparse ``workset.kuid``) rather than the on-disk
``project:``/``resolved:`` meta that ``_read_project_meta`` below reads.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

# NOTE (frozen reference): the live tree still provides ``registry_store`` and
# ``journal``; the identity read helper below is a frozen local copy because the
# live ``read_project_meta`` was deleted in P8c.
from kanibako import registry_store
from kanibako.config_io import load_doc
from kanibako.errors import KanibakoError

# The box settings file name (frozen copy of ``config.BOX_META_FILE``).
BOX_META_FILE = "settings.yaml"


def _read_project_meta(path: Path) -> dict | None:
    """FROZEN local copy of the retired ``config.read_project_meta``.

    Reads the pre-P8b on-disk box identity: the ``project:`` section (name/mode)
    and the ``resolved:`` section (workspace/shell/vault paths). Returns None
    when no ``project.mode`` is present. ``enable_vault`` is sourced only from
    ``box.enable_vault`` (P2 clean break). This logic no longer exists in the
    live tree — a future ``system recover`` re-sources identity from
    ``box_resolve`` instead.
    """
    if not path.exists():
        return None
    data = load_doc(path)

    project_sec = data.get("project", {})
    resolved_sec = data.get("resolved", data.get("paths", {}))

    if not project_sec.get("mode"):
        return None

    return {
        "mode": project_sec["mode"],
        "enable_vault": (data.get("box") or {}).get("enable_vault", True),
        "name": project_sec.get("name", ""),
        "workspace": resolved_sec.get("workspace", ""),
        "shell": resolved_sec.get("shell", ""),
        "vault_ro": resolved_sec.get("vault_ro", ""),
        "vault_rw": resolved_sec.get("vault_rw", ""),
        "metadata": resolved_sec.get("metadata", ""),
        "project_hash": resolved_sec.get("project_hash", ""),
    }


class ImportConflictError(KanibakoError):
    """A drop-in import was refused because its name collides with a different
    already-registered entity (frozen copy of the live class of the same name).
    """


def _alert(mode: str, name: str, path: Path) -> None:
    """Print the import ALERT to stderr (no confirmation; the import proceeds)."""
    print(f"Imported {mode} '{name}' at {path}", file=sys.stderr)


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


@contextmanager
def _journal_register(
    journal: Path | None,
    box_path: Path,
    *,
    op: str,
    name: str,
    mode: str,
    workset: str | None = None,
):
    """Bracket a register-only import with a write-ahead journal entry.

    Write entry -> (register body) -> clear entry. A None *journal* is a no-op
    bracket (plain register). If the register body raises, the entry is left for
    recovery. Frozen copy of the live ``import_reconcile._journal_register``.
    """
    if journal is None:
        yield
        return
    from kanibako import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op=op, name=name, mode=mode, workset=workset,
    )
    yield
    journal_mod.clear_entry(journal, box_path)


def _clear_stale_import(journal: Path | None, box_path: Path) -> None:
    """Clear a stale register-only entry on an already-registered box.

    Frozen copy of the live ``import_reconcile._clear_stale_import``.
    """
    if journal is None:
        return
    from kanibako import journal as journal_mod

    if journal_mod.pending_import(journal, box_path) is not None:
        journal_mod.clear_entry(journal, box_path)


# ---------------------------------------------------------------------------
# PRIMARY (central box store → external workspace) — RETIRED (P8b, Option A)
#
# These read the pre-P8b on-disk box identity (``project:``/``resolved:``) via
# ``_read_project_meta`` above. Under sparse create that meta is no longer
# written, so a future ``system recover`` must re-source identity from
# ``box_resolve`` (layout + marker + sparse ``workset.kuid``) rather than reuse
# these reads verbatim.
# ---------------------------------------------------------------------------

def import_primary_box(
    registry: Path, box_dir: Path, *, journal: Path | None = None,
) -> str | None:
    """Reconcile a single on-disk PRIMARY box at *box_dir* against ``registry.projects``.

    *box_dir* is a per-box directory under ``@config.primary_workset/boxes/``;
    its ``settings.yaml`` (pre-P8b) carried ``project.name`` (the box name) and
    the real external ``workspace`` dir. Reconciles name → workspace against
    ``registry.projects``:

    * The name is already registered to the box's workspace → silent no-op.
    * The name is registered to a DIFFERENT workspace → ImportConflictError.
    * Otherwise register name → workspace, alert, return the name.
    """
    meta = _read_project_meta(box_dir / BOX_META_FILE)
    if not meta:
        return None
    name = (meta.get("name") or "").strip()
    workspace = (meta.get("workspace") or "").strip()
    if not name or not workspace:
        return None
    workspace_str = str(Path(workspace).resolve())

    projects = registry_store.load_section(registry, "projects")
    current = projects.get(name)
    if current is not None:
        if str(Path(current).resolve()) == workspace_str:
            _clear_stale_import(journal, box_dir)
            return name
        raise _conflict("primary box", name, Path(workspace_str), str(current))

    with _journal_register(
        journal, box_dir, op="import", name=name, mode="primary",
    ):
        projects[name] = workspace_str
        registry_store.save_section(registry, "projects", projects)
    _alert("primary box", name, Path(workspace_str))
    return name


def import_primary_box_for_workspace(
    registry: Path, boxes_dir: Path, workspace: Path, *,
    register: bool = True, journal: Path | None = None,
) -> str | None:
    """Import the on-disk PRIMARY box whose recorded workspace is *workspace*.

    Scans *boxes_dir* (``@config.primary_workset/boxes``) for a box dir whose
    ``settings.yaml`` records *workspace* and is NOT yet in ``registry.projects``,
    then imports it (alert + register).

    *register* (J1 interrupted-create recovery): when False, the matching
    on-disk box's NAME is RESOLVED and returned without writing the registry.
    """
    if not boxes_dir.is_dir():
        return None
    target = str(workspace.resolve())
    for entry in sorted(boxes_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_project_meta(entry / BOX_META_FILE)
        if not meta:
            continue
        ws = (meta.get("workspace") or "").strip()
        if not ws or str(Path(ws).resolve()) != target:
            continue
        if not register:
            name = (meta.get("name") or "").strip()
            return name or None
        return import_primary_box(registry, entry, journal=journal)
    return None


def reconcile_primary_boxes(
    registry: Path, boxes_dir: Path, *, journal: Path | None = None,
) -> list[str]:
    """Scan *boxes_dir* for on-disk PRIMARY boxes missing from ``registry.projects``.

    Imports each discovered-but-unregistered box via :func:`import_primary_box`.
    Returns the list of box names imported during this scan.
    """
    if not boxes_dir.is_dir():
        return []
    imported: list[str] = []
    for entry in sorted(boxes_dir.iterdir()):
        if not entry.is_dir():
            continue
        meta = _read_project_meta(entry / BOX_META_FILE)
        if not meta:
            continue
        name = (meta.get("name") or "").strip()
        if not name:
            continue
        projects = registry_store.load_section(registry, "projects")
        if name in projects:
            import_primary_box(registry, entry, journal=journal)
            continue
        if import_primary_box(registry, entry, journal=journal) is not None:
            imported.append(name)
    return imported
