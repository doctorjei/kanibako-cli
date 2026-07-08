"""New-model box identity derivation (registry + layout).

This module derives a box's identity from the REGISTRIES (the per-workset
``workset_registry`` box membership + the global ``registry_store`` standalone
index) and the on-disk LAYOUT — the replacement for the legacy
``read_project_meta`` (``project:``/``resolved:``) derivation.  Every helper is
PURE: it takes the resolved :class:`~kanibako.paths.StandardPaths`, the
``KanibakoConfig``, and the target directory EXPLICITLY (no hidden global
reads), and never writes.

Design source: ``plans/settings-conformance-registry-DESIGN.md`` — the
**D3-mode** mode-detection precedence, **D1b** name-sourcing (the registry entry
KEY *is* the box name), **D3-auth** membership authority (registry ⇒ dirs), and
**D10** enumerate-and-scan (all worksets are reachable up front; their
per-workset registries collectively form the reverse index).

Introduced as additive phase-P4 infrastructure and made the LIVE identity source
in P5a: the former ``read_project_meta`` consumers now derive identity here, and
the legacy on-disk-meta helpers (``read_project_meta``/``write_project_meta``)
were deleted in P8c once sparse create (P8b) stopped writing the ``project:``/
``resolved:`` sections they read.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from kanibako import registry_store, workset_registry
from kanibako.config import BOX_META_FILE, KanibakoConfig
from kanibako.config_io import load_doc
from kanibako.paths import (
    _STANDALONE_META_DIR,
    BoxMode,
    DetectionResult,
    StandardPaths,
    detect_project_mode,
)

# The PRIMARY (default) workset's name — it is anchored by ``config.primary_workset``
# (NON-EXCEPTIONAL per D0/D1), not listed in the global ``worksets:`` section, so
# the enumeration yields it explicitly.  Mirrors ``_default_project_group``'s name.
_PRIMARY_WORKSET_NAME = "default"


def standalone_settings_present(project_dir: Path) -> bool:
    """True iff *project_dir* carries the standalone box MARKER (presence only).

    The marker is the standalone meta dir PLUS the box settings file, both
    present::

        (project_dir/_STANDALONE_META_DIR).is_dir()
            and (project_dir/BOX_META_FILE).is_file()

    Mirrors :func:`kanibako.paths._is_standalone_meta_dir` BUT deliberately does
    NOT read ``project.mode`` — that field is going away (design D4: the FILE's
    existence is the standalone signal).  This is the highest-precedence
    detection signal (D3-mode #1): a box's own in-place settings file is the
    authoritative self-declaration of standalone identity and OVERRIDES any
    workset determination (a workset must not be able to "steal" it).
    """
    return (project_dir / _STANDALONE_META_DIR).is_dir() and (
        project_dir / BOX_META_FILE
    ).is_file()


def _enumerate_worksets(
    std: StandardPaths,
) -> Iterator[tuple[str, Path, BoxMode]]:
    """Yield ``(workset_name, workset_root, mode)`` for EVERY reachable workset.

    The PRIMARY workset first (anchored by ``config.primary_workset`` ==
    ``std.primary_workset``; NON-EXCEPTIONAL per D0/D1), then every NAMED workset
    from the global ``worksets:`` discovery section (D10 enumerate-and-scan —
    all worksets are reachable up front).
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
    config: KanibakoConfig,  # noqa: ARG001 — signature parity; enumeration is std-sourced
) -> _OwnedBox | None:
    """Scan every workset's per-workset registry for a box AT *project_dir*.

    Enumerates all worksets (:func:`_enumerate_worksets`), resolves each one's
    per-workset registry path (honoring a ``workset.registry`` repoint via its
    settings), loads its ``boxes:`` membership, and returns the entry whose PATH
    == *project_dir* (BOTH sides ``resolve()``d so symlinks / relative /
    trailing-slash forms compare equal).  ``None`` when no workset owns the dir.
    (D10 enumerate-and-scan; the per-workset registries collectively ARE the
    reverse index — no marker in the user's repo.)
    """
    target = project_dir.resolve()
    for workset_name, root, mode in _enumerate_worksets(std):
        settings: Any = load_doc(root / "settings.yaml")
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


def find_owning_workset(
    project_dir: Path,
    std: StandardPaths,
    config: KanibakoConfig,
) -> tuple[str, Path, BoxMode] | None:
    """Return the workset that OWNS the box at *project_dir*, or ``None``.

    Enumerates ALL worksets (global ``worksets:`` + the PRIMARY at
    ``config.primary_workset``) and scans their per-workset registries'
    ``boxes:`` membership for a PATH == *project_dir* match (D10).  Returns the
    owning workset's ``(name, root, mode)`` where ``mode`` is ``primary`` (the
    PRIMARY workset) or ``named``.  ``None`` when no workset lists the dir.
    """
    owned = _find_owning_box(project_dir, std, config)
    if owned is None:
        return None
    return (owned.workset_name, owned.workset_root, owned.mode)


def find_connected_external_box(
    project_dir: Path,
    std: StandardPaths,
) -> _OwnedBox | None:
    """Resolve *project_dir* (or an ancestor) to an EXTERNAL-connected box.

    The per-workset registries collectively ARE the reverse index (D10): a
    connected box is a NAMED workset's ``boxes:`` entry whose registered PATH is
    an EXTERNAL directory (outside that workset's root).  This enumerates every
    NAMED workset (the global ``worksets:`` discovery section), scans its
    per-workset ``boxes:`` membership (honoring a ``workset.registry`` repoint),
    and returns the entry whose registered path is *project_dir* OR a proper
    ANCESTOR of it — DEEPEST wins, the same ancestor semantics the legacy
    ``connected:`` index used so a launch from a SUBDIR of a connected dir still
    resolves.  In-tree boxes (path under the workset root) are skipped — they are
    resolved by ordinary location detection.  The PRIMARY workset is skipped:
    default-mode external boxes were never in ``connected:`` and resolve by their
    own name index.  ``None`` when no connected box owns *project_dir*.

    This REPLACES the global ``connected:`` index + ``workset._find_connected_project``
    (D10 enumerate-and-scan; no marker in the user's repo).
    """
    target = project_dir.resolve()
    best: _OwnedBox | None = None
    best_depth = -1
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        root_resolved = root.resolve()
        settings: Any = load_doc(root / "settings.yaml")
        registry_path = workset_registry.resolve_workset_registry_path(
            root, settings
        )
        boxes = workset_registry.load_workset_boxes(registry_path)
        for box_name, box_path_str in boxes.items():
            box_path = Path(box_path_str).resolve()
            # EXTERNAL only: an in-tree box (path under the workset root) is a
            # normal membership entry, never a connected-external record.
            try:
                box_path.relative_to(root_resolved)
                continue
            except ValueError:
                pass
            # Ancestor match: the registered external path IS *target* or an
            # ancestor of it (deepest registered path wins — the connected:
            # ancestor-walk semantics, so a subdir launch still resolves).
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
    config: KanibakoConfig,
) -> DetectionResult | None:
    """Detect *project_dir*'s box mode by the D3-mode PRECEDENCE (first wins).

    1. **In-place settings file present → STANDALONE.**  A box's own settings
       file is the authoritative self-declaration and OVERRIDES any workset
       determination — a workset must NOT be able to steal a box that declares
       itself standalone (Jei).  Highest-precedence signal.
    2. Else **enumerate worksets and scan** their per-workset registries for a
       ``boxes:`` entry whose PATH == *project_dir* → that workset's mode
       (``primary``/``named``) (D10).
    3. Else the existing :func:`kanibako.paths.detect_project_mode` treewalk.
       A ``named`` (or ``standalone``) result passes through; a ``primary``
       result is detect_project_mode's NO-MARKER default (its case 4) — in the
       new model PRIMARY membership is authoritative via the registry scan
       (case 2 above), so an unregistered dir is NOT an existing box → ``None``
       (the caller's create path).  This ``primary → None`` also collapses
       detect_project_mode's case-2 genuine-primary: new-model primary membership
       lives SOLELY in the per-workset registry (scanned in case 2) now that the
       global ``projects:`` section has been RETIRED (clean split, 2026-07-08).
    4. Else ``None`` (not a box).
    """
    # 1. Standalone by in-place settings-file presence (OVERRIDES everything).
    if standalone_settings_present(project_dir):
        return DetectionResult(BoxMode.standalone, project_dir.resolve())

    # 2. Workset ownership from the per-workset registries.
    owned = _find_owning_box(project_dir, std, config)
    if owned is not None:
        return DetectionResult(owned.mode, owned.box_path.resolve())

    # 3. Treewalk detection (compose — do not duplicate).  A PRIMARY result is
    # the no-marker default (case 4) → not a box in the new model → None.
    result = detect_project_mode(project_dir, std, config)
    if result.mode is BoxMode.primary:
        return None
    return result


def resolve_box_identity(
    project_dir: Path,
    std: StandardPaths,
    config: KanibakoConfig,
) -> dict[str, Any] | None:
    """Return the new-model identity for the box at *project_dir*, or ``None``.

    ``{mode, name, workspace, registered}`` sourced per D1b/D3-auth:

    - ``mode`` — the :class:`~kanibako.paths.BoxMode` from
      :func:`detect_box_mode`.
    - ``name`` — the registry entry KEY (D1b: the ``name: path`` KEY *is* the
      box name).  Workset box → the per-workset ``boxes:`` key; STANDALONE →
      composed LIVE as ``<stored workset.kuid>_<live leaf>`` (P6d: the kuid is the
      STABLE stored prefix, the leaf tracks the current dir); a pre-kuid box (no
      ``workset.kuid``) falls back to the ``standalone:`` registry key or the dir
      leaf.
    - ``workspace`` — the registry entry PATH (workset box) or the layout dir
      (standalone / orphan).
    - ``registered`` — membership present (D3-auth): the per-workset ``boxes:``
      for a workset box, the global ``standalone:`` for a standalone box.

    ``enable_vault`` is intentionally NOT sourced here — it is the settable
    ``box.enable_vault`` key already handled in P2.  ``None`` when *project_dir*
    is not a box.
    """
    result = detect_box_mode(project_dir, std, config)
    if result is None:
        return None

    if result.mode is BoxMode.standalone:
        # Source from the DETECTED box root (``result.project_root``), NOT the
        # passed-in *project_dir* — the two diverge when standalone is detected
        # by the treewalk from a SUBDIR (case 3 finds the marker at an
        # ancestor).  Mirrors the orphan branch below; keeps name/workspace/
        # registration anchored on the box root.
        box_root = result.project_root.resolve()
        registered_name = registry_store.standalone_name_for_root(
            std.registry, box_root
        )
        # LIVE name (P6d): the stored ``workset.kuid`` prefixes the CURRENT-leaf
        # basename, so a MOVED standalone keeps its stable kuid identity while the
        # leaf tracks the new dir. Read the kuid from the box's own settings.yaml
        # (the workset tier for a standalone). A pre-kuid box (SENTINEL — no stored
        # kuid) falls back to the registered ``standalone:`` KEY, else the dir leaf.
        from kanibako import box_identity, kuid
        from kanibako.config import read_workset_kuid

        stored_kuid = read_workset_kuid(box_root / BOX_META_FILE)
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

    # A ``named`` result with NO registry entry: a workset-contained but
    # unregistered dir (D3-auth: the registry is authoritative, dirs follow —
    # so an orphan is registered=False).  Name/workspace are layout-derived
    # from the detected box root.
    return {
        "mode": result.mode,
        "name": result.project_root.name,
        "workspace": result.project_root.resolve(),
        "registered": False,
    }
