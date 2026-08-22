"""Per-workset registry (``workset.registry`` → ``<workset_root>/registry.yaml``).

Holds one workset's IDENTITY and MEMBERSHIP in three sections, in this canonical
on-disk order:

* ``workset:`` — the workset's own ``name`` and ``created`` stamp.
* ``boxes:`` — box membership, ``name: workspace_path``.
* ``projects:`` — the workset's project records, ``name: {source_path: …}``.

Every public function takes the RESOLVED registry FILE path; nothing
reconstructs it from a workset root.

⚑⚑ IDENTITY AND MEMBERSHIP ARE REGISTRY-BORNE AT EVERY LEVEL (system-design
§Detect): a workset's name, ``created`` and membership live here exactly as a
box's ``meta.box.name`` does, and the workset root's ``settings.yaml`` carries
SETTINGS ONLY, is sparse, and MAY BE ABSENT.  Nothing here is a derived cache:
the ``boxes:`` entry KEYS yield the ``meta.box.name`` anchors (design D1b) and
membership IS the registration signal (design D3-auth) — the box dirs follow the
registry.  So this file must never be templated, copied between worksets or
regenerated.

See ``llm-docs/kanibako/project/workset_registry.py.md`` for the design: why the
membership is authoritative, the write discipline, and the workspace-uniqueness
invariant.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.errors import ProjectError

# The workset's own identity table: name + created.
_WORKSET_SECTION = "workset"
# The box-membership section of a per-workset registry file: box name → path.
_BOXES_SECTION = "boxes"
# The project records: project name → {source_path: …}.
_PROJECTS_SECTION = "projects"

# Canonical on-disk section order — identity, then membership, then projects.
_SECTIONS = (_WORKSET_SECTION, _BOXES_SECTION, _PROJECTS_SECTION)

# The name-keyed sections, written back sorted by name so a diff stays readable.
_SORTED_SECTIONS = (_BOXES_SECTION, _PROJECTS_SECTION)


def _same_workspace(a: str, b: str) -> bool:
    """True if *a* and *b* denote the SAME workspace path (resolved-path aware)."""
    if a == b:
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):
        return False


def _load_raw(registry_path: Path) -> dict:
    """Return the whole registry document for *registry_path* (absent file → ``{}``)."""
    return load_doc(registry_path) if registry_path.is_file() else {}


def _section(full_doc: Mapping[str, Any], section: str) -> dict:
    """Return *section* of *full_doc* as a dict (absent, null or non-mapping → ``{}``).

    ⚑ The ``or {}`` is NOT a redundant default: yaml ``boxes:\\n`` yields a PRESENT
    key whose value is ``None``, so ``get``'s default never applies.
    """
    table = full_doc.get(section, {}) or {}
    return dict(table) if isinstance(table, Mapping) else {}


def _write_doc(registry_path: Path, full_doc: dict) -> None:
    """Atomically write *full_doc* in canonical section order, name sections sorted."""
    for section in _SORTED_SECTIONS:
        table = full_doc.get(section)
        if isinstance(table, dict):
            full_doc[section] = {name: table[name] for name in sorted(table)}
    ordered = {name: full_doc[name] for name in _SECTIONS if name in full_doc}
    ordered.update({k: v for k, v in full_doc.items() if k not in _SECTIONS})
    dump_doc(registry_path, ordered)


def _load_boxes_raw(registry_path: Path) -> tuple[dict, dict[str, str]]:
    """Return ``(full_doc, boxes)`` for *registry_path* (absent file → empties)."""
    data = _load_raw(registry_path)
    return data, {k: str(v) for k, v in _section(data, _BOXES_SECTION).items()}


def _write_boxes(registry_path: Path, full_doc: dict, boxes: dict[str, str]) -> None:
    """Atomically write *boxes* into *full_doc*'s ``boxes:`` section."""
    full_doc[_BOXES_SECTION] = boxes
    _write_doc(registry_path, full_doc)


def load_workset_boxes(registry_path: Path) -> dict[str, str]:
    """Return the ``boxes:`` membership as ``{box_name: path_str}`` (absent → ``{}``)."""
    _, boxes = _load_boxes_raw(registry_path)
    return boxes


# ---------------------------------------------------------------------------
# Identity + projects — the two sections that MOVED here out of settings.yaml.
# ---------------------------------------------------------------------------

def load_workset_identity(registry_path: Path) -> dict[str, Any] | None:
    """Return the ``workset:`` identity table, or ``None`` when there is none.

    ⚑⚑ THE NAMED-workset-root detection input: ``None`` means "not a workset
    root", so a missing file, a missing section and an empty section all answer
    the same way.
    """
    identity = _section(_load_raw(registry_path), _WORKSET_SECTION)
    return identity or None


def load_workset_projects(registry_path: Path) -> dict[str, dict[str, Any]]:
    """Return the ``projects:`` records as ``{name: {source_path: …}}`` (absent → ``{}``)."""
    out: dict[str, dict[str, Any]] = {}
    for name, record in _section(_load_raw(registry_path), _PROJECTS_SECTION).items():
        if isinstance(record, Mapping):
            out[str(name)] = dict(record)
    return out


def save_workset_record(
    registry_path: Path, *, name: str, created: str,
    projects: Mapping[str, Mapping[str, Any]],
) -> None:
    """Write the ``workset:`` identity and ``projects:`` records in ONE read-modify-write.

    ⚑ The ``boxes:`` membership rides through untouched — it has its own writers.
    """
    full_doc = _load_raw(registry_path)
    full_doc[_WORKSET_SECTION] = {"name": name, "created": created}
    full_doc[_PROJECTS_SECTION] = {k: dict(v) for k, v in projects.items()}
    _write_doc(registry_path, full_doc)


def register_workset_box(registry_path: Path, box_name: str, path: Path) -> None:
    """Register (add or replace) *box_name* → *path* in the ``boxes:`` section."""
    full_doc, boxes = _load_boxes_raw(registry_path)
    path_str = str(path)
    # ⚑ Workspace-path uniqueness (Bug A durable fix): one workspace, EXACTLY one
    # box name.  Relaxing this refusal re-opens duplicate ``list`` rows.  It costs
    # the legitimate flows nothing — a re-register is idempotent and a MOVE (same
    # name, new path) still overwrites below.
    for existing_name, existing_path in boxes.items():
        if existing_name != box_name and _same_workspace(existing_path, path_str):
            raise ProjectError(
                f"Workspace {path_str!r} is already registered in this workset "
                f"as box {existing_name!r}; refusing to register it a second "
                f"time as {box_name!r} (one box per workspace path)."
            )
    boxes[box_name] = path_str
    _write_boxes(registry_path, full_doc, boxes)


def unregister_workset_box(registry_path: Path, box_name: str) -> None:
    """Remove *box_name* from the ``boxes:`` section; no write at all if absent."""
    if not registry_path.is_file():
        return
    full_doc, boxes = _load_boxes_raw(registry_path)
    if boxes.pop(box_name, None) is None:
        return
    _write_boxes(registry_path, full_doc, boxes)


def workset_box_path(registry_path: Path, box_name: str) -> str | None:
    """Return the registered path for *box_name*, or ``None`` if not a member."""
    return load_workset_boxes(registry_path).get(box_name)


def reverse_lookup_workset_box(
    registry_path: Path, workspace: Path | str,
) -> str | None:
    """Return the box name registered for *workspace* in ``boxes:``, or ``None``."""
    workspace_str = str(workspace)
    for name, path in load_workset_boxes(registry_path).items():
        if _same_workspace(path, workspace_str):
            return name
    return None


def resolve_workset_registry_path(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved per-workset registry FILE path (pure; no global state).

    A set ``workset.registry`` repoint wins (relative anchors under *workset_root*);
    anything else falls through to ``<workset_root>/registry.yaml``.
    """
    repoint: Any = None
    if isinstance(workset_settings, Mapping):
        workset_table = workset_settings.get("workset")
        if isinstance(workset_table, Mapping):
            repoint = workset_table.get("registry")
    if repoint:
        expanded = Path(str(repoint)).expanduser()
        if not expanded.is_absolute():
            expanded = workset_root / expanded
        return expanded
    return workset_root / "registry.yaml"
