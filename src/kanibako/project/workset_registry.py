"""Per-workset registry (``workset.registry`` → ``<workset_root>/registry.yaml``).

Holds one workset's MEMBERSHIP, and nothing else, in ONE section:

* ``boxes:`` — box membership, ``name: workspace_path``.

⚑⚑ MEMBERSHIP IS REGISTRY-BORNE (system-design §Detect: *"Members live in
``registry.yaml``'s ``boxes:``, keyed by name"*); IDENTITY IS NOT.  A workset is
NAMED by the ``worksets:`` section of the GLOBAL registry (``@config.registry``),
which maps its name to its root — that mapping is the whole of a workset's
identity, and nothing under the workset root repeats it.  The root
``workset.yaml`` carries SETTINGS ONLY, is sparse, and MAY BE ABSENT.

⚑⚑ THE PATH IS RECORDED EXACTLY ONCE.  The entry is FLAT — ``name: path``,
exactly as the keyspace's ``workset.registry`` row spells it — never a nested
record.  An unreleased 1.8.0 build briefly kept a ``workset:`` identity table
here and the member paths a second time under ``projects:``; both are RETIRED and
now HARD-REFUSE (:func:`_refuse_retired_registry_sections`), because the two
copies drifted — a disconnect dropped one row and orphaned the other.

Every public function takes the RESOLVED registry FILE path; nothing
reconstructs it from a workset root.

Nothing here is a derived cache: the ``boxes:`` entry KEYS yield the
``meta.box.name`` anchors (design D1b) and membership IS the registration signal
(design D3-auth) — the box dirs follow the registry.  So this file must never be
templated, copied between worksets or regenerated.

See ``llm-docs/kanibako/project/workset_registry.py.md`` for the design: why the
membership is authoritative, the write discipline, and the workspace-uniqueness
invariant.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.errors import LegacyRegistryIdentityError, ProjectError

# The box-membership section of a per-workset registry file: box name → path.
# ⚑ THE ONLY SECTION THIS MODULE READS OR WRITES.
_BOXES_SECTION = "boxes"

# RETIRED sections (unreleased 1.8.0 dev tree only): a workset IDENTITY table,
# which belongs in the GLOBAL registry's ``worksets:`` section and nowhere else,
# and a ``projects:`` map that held the same member paths as ``boxes:`` a second
# time.
_RETIRED_SECTIONS = ("workset", "projects")

# Canonical on-disk section order, and the name-keyed sections written back sorted
# by name so a diff stays readable.  ⚑ ONE section is in both — kept as tuples so a
# sibling-owned section can be added without reshaping the writer.
_SECTIONS = (_BOXES_SECTION,)
_SORTED_SECTIONS = (_BOXES_SECTION,)


def _same_workspace(a: str, b: str) -> bool:
    """True if *a* and *b* denote the SAME workspace path (resolved-path aware)."""
    if a == b:
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):
        return False


def _refuse_retired_registry_sections(
    registry_path: Path, full_doc: Mapping[str, Any],
) -> None:
    """RAISE when *full_doc* still carries a retired ``workset:`` or ``projects:`` section.

    ⚑ DETECTED ONLY SO IT CAN BE DIAGNOSED.  v1.8.0 is a clean break: there is no compat
    read and no auto-migration.  Reading past either section is a SILENT wrong answer —
    the identity table is ignored (a workset is named by the global registry, not here)
    and the ``projects:`` map holds a second copy of every member path that nothing
    updates, so the two drift apart with nothing printed to say so.
    """
    if not isinstance(full_doc, Mapping):
        return
    present = [s for s in _RETIRED_SECTIONS if full_doc.get(s)]
    if not present:
        # Absent, null, or emptied-out stubs: nothing is recorded in the wrong place.
        return
    named = " and ".join(f"'{s}:'" for s in present)
    plural = "sections are" if len(present) > 1 else "section is"
    raise LegacyRegistryIdentityError(
        f"{named} {plural} RETIRED from a workset registry and still the shape of "
        f"{registry_path}.\n"
        f"THE RULE: this file records MEMBERSHIP and nothing else — one flat `boxes:` "
        f"entry per member, `name: path`, the path written EXACTLY ONCE. A workset's "
        f"IDENTITY is not on disk under its root at all: it is the entry in the GLOBAL "
        f"registry's `worksets:` section mapping the workset's name to this directory, "
        f"which is what `kanibako workset list` reads. An unreleased 1.8.0 development "
        f"build wrote an identity table into this file, and every member path a second "
        f"time under `projects:`. Refusing rather than running: the two path copies "
        f"drift — a disconnect dropped the `projects:` row and orphaned the `boxes:` "
        f"one, which then refused to let that workspace be registered again under any "
        f"name.\n"
        f"  Fix, BY HAND:\n"
        f"\n"
        f"    1. Each `projects:` entry becomes a flat `boxes:` entry under the SAME "
        f"name, with its `source_path` as the value. Where a name is in BOTH, keep the "
        f"`boxes:` value — that is the path the box actually ran with. Then delete "
        f"`projects:` outright:\n"
        f"\n"
        f"         boxes:\n"
        f"           <box name>: <the path>\n"
        f"\n"
        f"    2. Delete the `workset:` table. NOTHING replaces it — this workset is "
        f"already named by the global registry, and no file under its root records a "
        f"name, a created stamp or anything else about the workset itself.\n"
        f"\n"
        f"  Leave the rest of this file as it is. kanibako 1.8.0 ships no automatic "
        f"migration for this — see MIGRATION.md §2.43."
    )


def _load_raw(registry_path: Path) -> dict:
    """Return the whole registry document for *registry_path* (absent file → ``{}``).

    ⚑ THE ONE READ SEAM, so the retired-section refusal fires on EVERY read.
    """
    if not registry_path.is_file():
        return {}
    data = load_doc(registry_path)
    _refuse_retired_registry_sections(registry_path, data)
    return data


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
