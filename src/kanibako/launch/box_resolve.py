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

from kanibako.errors import ConfigError
from kanibako.project import registry_store, workset_registry
from kanibako.settings.config import WORKSET_META_FILE, BootstrapConfig
from kanibako.settings.config_io import load_doc
from kanibako.settings.paths import (
    BoxMode,
    DetectionResult,
    StandardPaths,
    detect_project_mode,
)
from kanibako.utils import literal_path

# The PRIMARY workset's NAME (not a mode).  Anchored by ``config.primary_workset``,
# not listed in the global ``worksets:`` section — so the enumeration yields it
# explicitly.  Mirrors ``_default_project_group``'s name.
_PRIMARY_WORKSET_NAME = "default"


def stores_standalone_registry_null(project_dir: Path) -> bool:
    """True iff *project_dir*'s OWN ``workset.yaml`` stores ``workset.registry`` as null.

    ⚑ That stored null DEFINES standalone (system-design § Detection & import): this
    file only, never the cascade, so a null in a containing or system file never counts.
    """
    settings = project_dir / WORKSET_META_FILE
    if not settings.is_file():
        return False
    try:
        table = load_doc(settings).get("workset")
    except ConfigError:
        return False
    return isinstance(table, dict) and "registry" in table and table["registry"] is None


def _enumerate_worksets(
    std: StandardPaths,
) -> Iterator[tuple[str, Path, BoxMode]]:
    """Yield ``(workset_name, workset_root, mode)`` for EVERY reachable workset.

    PRIMARY first, then every NAMED workset :func:`list_worksets` returns; a root
    storing the standalone ``workset.registry`` null is skipped.
    """
    from kanibako.project.workset import list_worksets

    yield (_PRIMARY_WORKSET_NAME, std.primary_workset, BoxMode.primary)
    for name, root in list_worksets(std).items():
        if not stores_standalone_registry_null(root):
            yield (name, root, BoxMode.named)


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

    Honors a ``workset.registry`` repoint; matches the literal path (links
    unfollowed), so twins sharing a target stay two boxes.
    ``None`` when no workset owns the dir.
    """
    from kanibako.channels.channels import workset_token
    from kanibako.settings.workset_dirkeys import EarlyScope

    target = literal_path(project_dir)
    for workset_name, root, mode in _enumerate_worksets(std):
        settings: Any = load_doc(root / WORKSET_META_FILE)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, settings,
            early=EarlyScope(std.early_system, workset_token(mode, workset_name)),
        )
        boxes = workset_registry.load_workset_boxes(registry_path)
        for box_name, box_path_str in boxes.items():
            if literal_path(box_path_str) == target:
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
    from kanibako.project.workset import list_worksets, resolve_workspaces_locator
    from kanibako.settings.workset_dirkeys import EarlyScope

    target = Path(literal_path(project_dir))
    best: _OwnedBox | None = None
    best_depth = -1
    for name, root in list_worksets(std).items():
        if stores_standalone_registry_null(root):
            continue
        settings: Any = load_doc(root / WORKSET_META_FILE)
        early = EarlyScope(std.early_system, name)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, settings, early=early,
        )
        # No mapping check needed: ``load_doc`` returns a mapping or refuses the file.
        workspaces = resolve_workspaces_locator(root, settings, early=early)
        workspaces_resolved = workspaces.resolve()
        boxes = workset_registry.load_workset_boxes(registry_path)
        for box_name, box_path_str in boxes.items():
            box_path = Path(literal_path(box_path_str))
            # Skip ONLY members under the CURRENT workspaces dir (literally or
            # resolved) — ordinary location detection owns those.
            if (box_path.is_relative_to(literal_path(workspaces))
                    or box_path.resolve().is_relative_to(workspaces_resolved)):
                continue
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
    # 1. Standalone by the root file's own stored ``workset.registry`` null (OVERRIDES everything).
    if stores_standalone_registry_null(project_dir):
        return DetectionResult(BoxMode.standalone, project_dir.resolve())

    # 2. Workset ownership from the per-workset registries.
    owned = _find_owning_box(project_dir, std, config)
    if owned is not None:
        return DetectionResult(owned.mode, owned.box_path)

    # 3. Treewalk detection (compose — do not duplicate).  ⚑ A PRIMARY result is
    # the no-marker default → NOT a box in the new model → None, which is the
    # caller's create path.  Primary membership lives solely in the registry
    # scanned at case 2.
    result = detect_project_mode(project_dir, std, config)
    if result.mode is BoxMode.primary:
        return None
    return result


def standalone_box_name(box_root: Path, registered_name: str | None) -> str:
    """The name a standalone box at *box_root* goes by — its log files are named for it.

    LIVE name (P6d) ``<stored workset.kuid>_<current leaf>``, so a MOVED standalone
    keeps its identity.  The kuid comes from the box's own workset.yaml (the workset
    tier for a standalone); a pre-kuid box reads back SENTINEL and falls back to its
    ``standalone:`` registry KEY (*registered_name*), else the leaf.
    """
    from kanibako import kuid
    from kanibako.launch import box_identity
    from kanibako.settings.config import read_workset_kuid

    stored_kuid = read_workset_kuid(box_root / WORKSET_META_FILE)
    if stored_kuid != kuid.SENTINEL:
        return box_identity.compose_standalone_name(stored_kuid, box_root)
    if registered_name is not None:
        return registered_name
    return box_root.name


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
        return {
            "mode": result.mode,
            "name": standalone_box_name(box_root, registered_name),
            "workspace": box_root,
            "registered": registered_name is not None,
        }

    # Workset box (primary or named): identity from the per-workset registry.
    owned = _find_owning_box(project_dir, std, config)
    if owned is not None:
        return {
            "mode": owned.mode,
            "name": owned.box_name,  # the ``boxes:`` entry KEY (D1b)
            "workspace": owned.box_path,
            "registered": True,
        }

    # Orphan: a workset-contained but UNREGISTERED dir.  D3-auth makes the
    # registry authoritative, so it reports registered=False with name and
    # workspace derived from the detected box root.
    return {
        "mode": result.mode,
        "name": result.project_root.name,
        "workspace": Path(literal_path(result.project_root)),
        "registered": False,
    }
