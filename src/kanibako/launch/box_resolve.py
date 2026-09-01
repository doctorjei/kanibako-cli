"""New-model box identity derivation (registry + layout).

Every helper is PURE: it takes the resolved
:class:`~kanibako.settings.paths.StandardPaths`, the ``BootstrapConfig``, and the
target directory EXPLICITLY (no hidden global reads), and never writes.

Design letters (D0/D1, D1b, D3-mode, D3-auth, D4, D10, P6d), the history this
replaced, and the full case enumeration: ``llm-docs/kanibako/launch/box_resolve.py.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from kanibako.project import registry_store, workset_registry
from kanibako.settings.config import WORKSET_META_FILE, BootstrapConfig
from kanibako.settings.config_io import load_doc
from kanibako.settings.paths import (
    STANDALONE_META_DIR,
    BoxMode,
    DetectionResult,
    StandardPaths,
    detect_project_mode,
)

# The PRIMARY workset's NAME (not a mode).  Anchored by ``config.primary_workset``,
# not listed in the global ``worksets:`` section — so the enumeration yields it
# explicitly.  Mirrors ``_default_project_group``'s name.
_PRIMARY_WORKSET_NAME = "default"


def standalone_settings_present(project_dir: Path) -> bool:
    """True iff *project_dir* carries the standalone box MARKER (presence only).

    ⚑ Mirrors :func:`kanibako.settings.paths._is_standalone_meta_dir` but must NOT
    read ``project.mode`` — under D4 the FILE's existence is the signal and that
    field is going away.  Highest-precedence detection signal; see the llm-doc.
    """
    return (project_dir / STANDALONE_META_DIR).is_dir() and (
        project_dir / WORKSET_META_FILE
    ).is_file()


def _enumerate_worksets(
    std: StandardPaths,
) -> Iterator[tuple[str, Path, BoxMode]]:
    """Yield ``(workset_name, workset_root, mode)`` for EVERY reachable workset.

    PRIMARY first (``std.primary_workset``), then every NAMED workset from the
    global ``worksets:`` discovery section.
    """
    yield (_PRIMARY_WORKSET_NAME, std.primary_workset, BoxMode.primary)
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        yield (name, Path(root_str), BoxMode.named)


class _OwnedBox(NamedTuple):
    """A box found (path-matched) in some workset's per-workset registry."""

    workset_name: str
    workset_root: Path
    mode: BoxMode
    box_name: str
    box_path: Path


def _find_owning_box(
    project_dir: Path,
    std: StandardPaths,
    config: BootstrapConfig,  # noqa: ARG001 — signature parity; enumeration is std-sourced
) -> _OwnedBox | None:
    """Scan every workset's per-workset registry for a box AT *project_dir*.

    Honors a ``workset.registry`` repoint; matches on PATH with BOTH sides
    ``resolve()``d, so symlink / relative / trailing-slash forms compare equal.
    ``None`` when no workset owns the dir.
    """
    target = project_dir.resolve()
    for workset_name, root, mode in _enumerate_worksets(std):
        settings: Any = load_doc(root / WORKSET_META_FILE)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, settings
        )
        boxes = workset_registry.load_workset_boxes(registry_path)
        for box_name, box_path_str in boxes.items():
            if Path(box_path_str).resolve() == target:
                return _OwnedBox(
                    workset_name=workset_name,
                    workset_root=root,
                    mode=mode,
                    box_name=box_name,
                    box_path=Path(box_path_str),
                )
    return None


def find_connected_external_box(
    project_dir: Path,
    std: StandardPaths,
) -> _OwnedBox | None:
    """Resolve *project_dir* (or an ancestor) to a registered box OUTSIDE the
    current composition (external connect OR a pre-repoint stranded member).

    Scans every NAMED workset; DEEPEST registered ancestor wins, so a launch from
    a SUBDIR of a connected dir still resolves.  The PRIMARY workset is skipped
    (its external boxes resolve by their own name index).  ``None`` when no such
    box owns *project_dir*.

    ⚑ The in-scan skip is "under the CURRENT resolved ``workset.workspaces`` dir",
    NOT "under the workset root".  Widening it strands members registered under an
    OLD composition — bifrost A0, 2026-08-02.  Reasoning: the llm-doc.
    """
    from kanibako.project.workset import resolve_workset_workspaces

    target = project_dir.resolve()
    best: _OwnedBox | None = None
    best_depth = -1
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        settings: Any = load_doc(root / WORKSET_META_FILE)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, settings
        )
        # No mapping check needed: ``resolve_workset_workspaces`` guards
        # non-mapping docs itself.
        workspaces_resolved = resolve_workset_workspaces(root, settings).resolve()
        boxes = workset_registry.load_workset_boxes(registry_path)
        for box_name, box_path_str in boxes.items():
            box_path = Path(box_path_str).resolve()
            # Skip ONLY members under the CURRENT resolved workspaces dir —
            # ordinary location detection owns those.
            try:
                box_path.relative_to(workspaces_resolved)
                continue
            except ValueError:
                pass
            # Ancestor match: the registered path IS *target* or an ancestor.
            try:
                target.relative_to(box_path)
            except ValueError:
                continue
            depth = len(box_path.parts)
            if depth > best_depth:
                best = _OwnedBox(
                    workset_name=name,
                    workset_root=root,
                    mode=BoxMode.named,
                    box_name=box_name,
                    box_path=box_path,
                )
                best_depth = depth
    return best


def detect_box_mode(
    project_dir: Path,
    std: StandardPaths,
    config: BootstrapConfig,
) -> DetectionResult | None:
    """Detect *project_dir*'s box mode by the D3-mode PRECEDENCE (first wins).

    Standalone marker, else workset-registry ownership, else the treewalk, else
    ``None`` (not a box).  The four cases in full: the llm-doc.
    """
    # 1. Standalone by in-place settings-file presence (OVERRIDES everything).
    if standalone_settings_present(project_dir):
        return DetectionResult(BoxMode.standalone, project_dir.resolve())

    # 2. Workset ownership from the per-workset registries.
    owned = _find_owning_box(project_dir, std, config)
    if owned is not None:
        return DetectionResult(owned.mode, owned.box_path.resolve())

    # 3. Treewalk detection (compose — do not duplicate).  ⚑ A PRIMARY result is
    # the no-marker default → NOT a box in the new model → None, which is the
    # caller's create path.  Primary membership lives solely in the registry
    # scanned at case 2.
    result = detect_project_mode(project_dir, std, config)
    if result.mode is BoxMode.primary:
        return None
    return result


def resolve_box_identity(
    project_dir: Path,
    std: StandardPaths,
    config: BootstrapConfig,
) -> dict[str, Any] | None:
    """Return ``{mode, name, workspace, registered}`` for the box at *project_dir*.

    Sourced per D1b (the registry entry KEY *is* the name) and D3-auth; field
    table in the llm-doc.  ``enable_vault`` is intentionally NOT sourced here —
    it is the settable ``box.enable_vault`` key.  ``None`` when not a box.
    """
    result = detect_box_mode(project_dir, std, config)
    if result is None:
        return None

    if result.mode is BoxMode.standalone:
        # ⚑ Source from the DETECTED box root, NOT the passed-in *project_dir* —
        # the two diverge when the treewalk finds the marker at an ANCESTOR of a
        # subdir launch.  The orphan branch below mirrors this.
        box_root = result.project_root.resolve()
        registered_name = registry_store.standalone_name_for_root(
            std.registry, box_root
        )
        # LIVE name (P6d) ``<stored workset.kuid>_<current leaf>``, so a MOVED
        # standalone keeps its identity.  The kuid comes from the box's own
        # workset.yaml (the workset tier for a standalone); a pre-kuid box reads
        # back SENTINEL and falls back to the ``standalone:`` KEY, else the leaf.
        from kanibako import kuid
        from kanibako.launch import box_identity
        from kanibako.settings.config import read_workset_kuid

        stored_kuid = read_workset_kuid(box_root / WORKSET_META_FILE)
        if stored_kuid != kuid.SENTINEL:
            name = box_identity.compose_standalone_name(stored_kuid, box_root)
        elif registered_name is not None:
            name = registered_name
        else:
            name = box_root.name
        return {
            "mode": result.mode,
            "name": name,
            "workspace": box_root,
            "registered": registered_name is not None,
        }

    # Workset box (primary or named): identity from the per-workset registry.
    owned = _find_owning_box(project_dir, std, config)
    if owned is not None:
        return {
            "mode": owned.mode,
            "name": owned.box_name,  # the ``boxes:`` entry KEY (D1b)
            "workspace": owned.box_path.resolve(),
            "registered": True,
        }

    # Orphan: a workset-contained but UNREGISTERED dir.  D3-auth makes the
    # registry authoritative, so it reports registered=False with name and
    # workspace derived from the detected box root.
    return {
        "mode": result.mode,
        "name": result.project_root.name,
        "workspace": result.project_root.resolve(),
        "registered": False,
    }
