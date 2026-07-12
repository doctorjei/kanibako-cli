"""Per-workset registry (``workset.registry`` → ``<workset_root>/registry.yaml``).

Each workset owns a per-workset registry file whose location is the resolved
``workset.registry`` key (default ``@meta.workset.path/registry.yaml`` ==
``<workset_root>/registry.yaml``; a set ``workset.registry`` repoint is honored
— see :func:`resolve_workset_registry_path`).  It holds that workset's
**box membership** in a single top-level section::

    boxes:
      mybox: /abs/path/to/mybox
      other: /abs/path/to/other

The entry KEY is the box name (the ``<leaf>`` for a workset box) — reading those
keys is what YIELDS the ``meta.box.name`` anchors at resolution (design D1b); the
value is the box's path.  Registry MEMBERSHIP is the seed/registration signal
(design D3-auth): a box present here belongs to the workset; the registry is
authoritative and the box dirs follow.

This is ADDITIVE infrastructure (settings-conformance phase P3): nothing consumes
it yet — the launch/create cutover that moves box membership onto per-workset
registries is P4/P5.  It changes no existing flow.

Every public function takes the resolved per-workset registry FILE path — the
single source of the registry location; nothing reconstructs it from a workset
root (the resolver does that once, up front).  Writes are atomic (via
``config_io.dump_doc`` — temp file + ``os.replace``) and preserve every sibling
section untouched (the raw document is read back and only ``boxes:`` is swapped),
so a future per-workset ``connected:``/marker section can coexist.  An absent
file yields an empty membership; a write never scaffolds sections it was not
asked to write.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import ProjectError

# The single membership section of a per-workset registry file: box name → path.
_BOXES_SECTION = "boxes"


def _same_workspace(a: str, b: str) -> bool:
    """True if *a* and *b* denote the SAME workspace path.

    Exact-string equality first (the common case — callers store already-resolved
    paths); then a resolved-path fallback so a normalization/symlink difference
    between a stored value and a re-registering caller still counts as the same
    workspace (the drift that let Bug A mint duplicate ``boxes:`` entries).
    ``Path.resolve`` is non-strict here, so a not-yet-existing path never raises.
    """
    if a == b:
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):
        return False


def _load_boxes_raw(registry_path: Path) -> tuple[dict, dict[str, str]]:
    """Return ``(full_doc, boxes)`` for *registry_path* (absent file → empties).

    ``full_doc`` is the raw document (so a write can preserve sibling sections);
    ``boxes`` is the normalized ``{name: path_str}`` membership.
    """
    data = load_doc(registry_path) if registry_path.is_file() else {}
    # ``... or {}`` coerces a PRESENT-but-NULL ``boxes:`` section (yaml
    # ``boxes:\n`` → the key is present with value ``None``, so the ``{}``
    # default never applies) to empty — mirroring ``registry_store``'s guard.
    boxes = {k: str(v) for k, v in dict(data.get(_BOXES_SECTION, {}) or {}).items()}
    return data, boxes


def _write_boxes(registry_path: Path, full_doc: dict, boxes: dict[str, str]) -> None:
    """Atomically write *boxes* into *full_doc*'s ``boxes:`` section, sorted.

    Sibling sections in *full_doc* are preserved; keys are sorted for stable
    diffs (matching the ``registry_store`` name-section writer).
    """
    full_doc[_BOXES_SECTION] = {name: boxes[name] for name in sorted(boxes)}
    dump_doc(registry_path, full_doc)


def load_workset_boxes(registry_path: Path) -> dict[str, str]:
    """Return the ``boxes:`` membership as ``{box_name: path_str}``.

    Absent file (or absent section) → ``{}``.  *registry_path* is the resolved
    per-workset registry FILE path.
    """
    _, boxes = _load_boxes_raw(registry_path)
    return boxes


def register_workset_box(registry_path: Path, box_name: str, path: Path) -> None:
    """Register (add or replace) *box_name* → *path* in the ``boxes:`` section.

    Idempotent for a matching pair; overwrites the stored path if the same name
    re-registers a different path (a moved box).  Atomic; preserves every sibling
    section of the registry file.

    Workspace-path uniqueness invariant (Bug A durable fix): within a workset's
    ``boxes:`` section a workspace path maps to EXACTLY ONE box name.  If *path*
    is already registered under a DIFFERENT name, this refuses (``ProjectError``)
    rather than mint a second entry pointing at the same workspace — the
    root-cause that surfaced as duplicate ``list`` rows.  This never obstructs the
    legitimate flows: a re-register of the SAME ``(box_name, path)`` is idempotent,
    and a MOVE (same *box_name*, new *path*) overwrites — only a *different* name
    claiming an ALREADY-registered workspace is refused.
    """
    full_doc, boxes = _load_boxes_raw(registry_path)
    path_str = str(path)
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
    """Remove *box_name* from the ``boxes:`` section (no-op if absent).

    A no-op (no write) when the file is absent or the name is not present, so an
    unregister never scaffolds an empty section.  Otherwise atomic; preserves
    every sibling section.
    """
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
    """Return the box name registered for *workspace* in ``boxes:``, or ``None``.

    The reverse of the name→path membership map, resolved-path aware (via
    :func:`_same_workspace`) so a symlink/normalization alias of the stored path
    still matches — the SAME drift :func:`register_workset_box`'s uniqueness guard
    resolves.  Used at the registration layer (``resolve_project`` Guard 2) to
    reuse an already-registered box name instead of minting a duplicate: the
    per-workset ``boxes:`` membership is the registry ``list``/``box_resolve``
    actually read, so this catches drift the GLOBAL name registry has lost (e.g. a
    purge that dropped the global name but left this membership).
    """
    workspace_str = str(workspace)
    for name, path in load_workset_boxes(registry_path).items():
        if _same_workspace(path, workspace_str):
            return name
    return None


def resolve_workset_registry_path(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
) -> Path:
    """Return the resolved per-workset registry FILE path for a workset.

    A pure function of its inputs (no global state — the caller passes the
    workset root and its settings).  Logic:

    - if ``workset.registry`` is SET in *workset_settings* (the routed nested
      slot ``workset: {registry: <path>}`` — the same location
      ``config set workset.registry=<path>`` writes), honor it: ``~`` expands and
      an absolute path is used as-is; a relative repoint anchors under
      *workset_root* (deterministic, like the sibling path keys);
    - else DEFAULT ``workset_root / "registry.yaml"``
      (== ``@meta.workset.path/registry.yaml``).

    *workset_settings* is the workset settings document (mapping); ``None`` or a
    non-mapping ``workset`` table falls through to the default.
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
