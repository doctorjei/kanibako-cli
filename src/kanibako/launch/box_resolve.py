"""New-model box identity derivation (registry + layout).

Every helper is PURE: it takes the resolved
:class:`~kanibako.settings.paths.StandardPaths`, the ``BootstrapConfig``, and the
target directory EXPLICITLY (no hidden global reads), and never writes.

Design letters (D0/D1, D1b, D3-mode, D3-auth, D4, D10, P6d), the history this
replaced, and the full case enumeration: ``llm-docs/kanibako/launch/box_resolve.py.md``.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from kanibako.channels.channels import WS_TOKEN_STANDALONE
from kanibako.errors import (
  ConfigError,
  DerivedBoxNameError,
  LegacyRegistryIdentityError,
  LegacyWorksetIdentityError,
  ReservedWorksetNameError,
  WorksetError,
)
from kanibako.project import registry_store, workset_registry
from kanibako.settings.config import WORKSET_META_FILE, BootstrapConfig, holds_standalone_mark
from kanibako.settings.config_io import load_doc
from kanibako.settings.messages import CURE_MOVED_LEAF_NOT_ASCII
from kanibako.settings.settings_assemble import refuse_retired_registry_key
from kanibako.settings.paths import (
  BoxMode,
  DetectionResult,
  ProjectPaths,
  StandardPaths,
  detect_project_mode,
)
from kanibako.utils import is_file_unless_gone, literal_path, refuse_unreadable_file

# The PRIMARY workset's NAME (not a mode).  Anchored by ``config.primary_workset``,
# not listed in the global ``worksets:`` section — so the enumeration yields it
# explicitly.  Mirrors ``_default_project_group``'s name.
_PRIMARY_WORKSET_NAME = "default"


def is_standalone_root(project_dir: Path, *, strict: bool = False) -> bool:
  """True iff *project_dir*'s OWN registry file holds the standalone mark.

  ⚑ The mark DEFINES standalone (system-design § Detection & import):
  :func:`~kanibako.settings.config.holds_standalone_mark`.  A retired
  ``workset.registry`` that this root's ``workset.yaml`` still stores refuses first.  A
  malformed ``workset.yaml`` is left to its own reader unless *strict*, which refuses it.
  """
  settings = project_dir / WORKSET_META_FILE
  # ⚑ EACCES or a loop refuses, naming the file, strict or not.
  if is_file_unless_gone(settings):
    try:
      refuse_retired_registry_key(load_doc(settings), level="workset", path=settings)
    except ConfigError:
      if strict:
        raise
      refuse_unreadable_file(settings)
  return holds_standalone_mark(project_dir)


def standalone_marker_error(root: Path, name: str) -> str | None:
  """Why standalone box *name*, registered at *root*, is no box: its marker is gone.

  ``None`` when *root* holds the marker or is no dir.  The one message for D4 state 3:
  a launch, ``duplicate``, ``move``, ``convert``, and ``remap`` refuse with it, while
  ``rm`` (with or without ``--purge``) and ``info`` still act on the box.  The second
  cure, ``box rm`` without ``--purge``, unregisters it and keeps its files.  No
  ``Error:`` prefix.
  """
  if not root.is_dir() or is_standalone_root(root):
    return None
  return (
    standalone_marker_refusal(root, f"box '{name}' is registered as a standalone box")
    + f" Or unregister it, keeping its files:\n  kanibako box rm {shlex.quote(name)}"
  )


def standalone_marker_refusal(root: Path, lead: str) -> str:
  """The refusal's head for a standalone box at *root* whose marker is gone.

  *lead* names the box ("box 'x' is registered as a standalone box"); the text runs
  through ``Restore it: <cure>.`` and the caller appends its next step.  ONE spelling
  for the launch gate (:func:`standalone_marker_error`) and for ``box register``
  readopting a deregistered box.  No ``Error:`` prefix.
  """
  fault, cure = standalone_marker_repair(root)
  return (
    f"{lead} at {root}, but {workset_registry.workset_registry_path(root)} {fault}, so kanibako does "
    f"not see a box there.\n  Restore it: {cure}."
  )


def standalone_marker_repair(root: Path) -> tuple[str, str]:
  """``(fault, cure)`` for *root*'s registry file, which lacks the standalone marker.

  *fault* follows the file's path ("is missing"); *cure* is the edit that restores it.
  """
  if workset_registry.workset_registry_path(root).exists():
    fault, cure = "does not hold the standalone marker", "make its only content"
  else:
    fault, cure = "is missing", "create that file holding only"
  return fault, f"{cure} '{WS_TOKEN_STANDALONE}'"


def registered_standalone_marker_error(root: Path, std: StandardPaths) -> str | None:
  """:func:`standalone_marker_error` for *root* when the standalone registry holds it, else ``None``."""
  name = registry_store.standalone_name_for_root(std.registry, root)
  return None if name is None else standalone_marker_error(root, name)


def _enumerate_worksets(
  std: StandardPaths,
) -> Iterator[tuple[str, Path, BoxMode]]:
  """Yield ``(workset_name, workset_root, mode)`` for EVERY reachable workset.

  PRIMARY first, then every NAMED workset :func:`list_worksets` returns; a root
  holding the standalone mark is skipped.  ⚑ A LOOPED root has no
  marker to read and is yielded as named, never refused: it is not the box asked about.
  """
  from kanibako.project.workset import _symlink_loop, list_worksets

  yield (_PRIMARY_WORKSET_NAME, std.primary_workset, BoxMode.primary)
  for name, root in list_worksets(std).items():
    if _symlink_loop(root) or not is_standalone_root(root):
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
  *,
  workset: str | None = None,
) -> _OwnedBox | None:
  """Scan every workset's per-workset registry (only *workset*'s, when given) for a box AT *project_dir*.

  Matches the literal path (links unfollowed), so twins sharing a target stay two boxes.
  ``None`` when no workset owns the dir.
  """

  from kanibako.project.workset import _symlink_loop

  target = literal_path(project_dir)
  for workset_name, root, mode in _enumerate_worksets(std):
    # ⚑ A looped root holds no registry to read: no box is recorded there.
    if (workset is not None and workset_name != workset) or _symlink_loop(root):
      continue
    boxes = workset_registry.load_workset_boxes(workset_registry.workset_registry_path(root))
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


def recorded_workspaces_under(
  std: StandardPaths, tree: Path, *, standalone: bool = True, primary: bool = True
) -> list[tuple[str, Path]]:
  """Every box whose RECORDED workspace is *tree* or lies under it, as ``(owner, path)``.

  Covers the primary registry unless *primary* is false, every named workset's, and,
  unless *standalone* is false, the standalone section, whose rows record a root.  *owner* is the box name,
  ``<workset>/<name>`` for a named member.  Paths compare both literal and resolved: a
  safety test, so a workspace reached through a link still counts.
  """
  from kanibako.project.workset import _resolved_unless_loop, _symlink_loop

  literal = Path(literal_path(tree))
  resolved = _resolved_unless_loop(literal)

  def inside(recorded: str) -> bool:
    path = Path(literal_path(recorded))
    return path.is_relative_to(literal) or _resolved_unless_loop(path).is_relative_to(resolved)

  found: list[tuple[str, Path]] = []
  for workset_name, root, mode in _enumerate_worksets(std):
    if _symlink_loop(root):
      continue  # ⚑ No registry to read through a loop: it records no box.
    if mode is BoxMode.primary and not primary:
      continue
    registry_path = workset_registry.workset_registry_path(root)
    for box_name, recorded in workset_registry.load_workset_boxes(registry_path).items():
      if inside(recorded):
        owner = box_name if mode is BoxMode.primary else f"{workset_name}/{box_name}"
        found.append((owner, Path(recorded)))
  if standalone:
    found.extend(
      (name, Path(root_str))
      for name, root_str in registry_store.load_standalone(std.registry).items()
      if inside(root_str)
    )
  return found


def find_connected_external_box(
  project_dir: Path,
  std: StandardPaths,
  *,
  skip: Callable[[_OwnedBox], bool] | None = None,
) -> _OwnedBox | None:
  """Resolve *project_dir* (or an ancestor) to a registered box OUTSIDE the
  current composition (external connect OR a pre-repoint stranded member).

  Scans every NAMED workset; DEEPEST registered ancestor wins, so a launch from
  a SUBDIR of a connected dir still resolves.  The PRIMARY workset is skipped
  (its external boxes resolve by their own name index).  ``None`` when no such
  box owns *project_dir*.  A box *skip* accepts is passed over, so the next one up wins.

  ⚑ The in-scan skip is "under the CURRENT resolved ``workset.workspaces`` dir",
  NOT "under the workset root".  Widening it strands members registered under an
  OLD composition — bifrost A0, 2026-08-02.  Reasoning: the llm-doc.
  """
  from kanibako.project.workset import _symlink_loop, list_worksets, resolve_workspaces_locator
  from kanibako.settings.workset_dirkeys import EarlyScope

  target = Path(literal_path(project_dir))
  best: _OwnedBox | None = None
  best_depth = -1
  for name, root in list_worksets(std).items():
    # ⚑ A looped root holds no registry to read: no box is recorded there.
    if _symlink_loop(root) or is_standalone_root(root):
      continue
    settings: Any = load_doc(root / WORKSET_META_FILE)
    early = EarlyScope(std.early_system, name)
    registry_path = workset_registry.workset_registry_path(root)
    # No mapping check needed: ``load_doc`` returns a mapping or refuses the file.
    workspaces = resolve_workspaces_locator(root, settings, early=early)
    workspaces_resolved = None if _symlink_loop(workspaces) else workspaces.resolve()
    boxes = workset_registry.load_workset_boxes(registry_path)
    for box_name, box_path_str in boxes.items():
      box_path = Path(literal_path(box_path_str))
      # Skip members under the CURRENT workspaces dir; location detection owns those.
      if box_path.is_relative_to(literal_path(workspaces)) or (
        workspaces_resolved is not None
        and not _symlink_loop(box_path)
        and box_path.resolve().is_relative_to(workspaces_resolved)
      ):
        continue
      # Ancestor match: the registered path IS *target* or an ancestor.
      try:
        target.relative_to(box_path)
      except ValueError:
        continue
      depth = len(box_path.parts)
      if depth > best_depth:
        found = _OwnedBox(
          workset_name=name,
          workset_root=root,
          mode=BoxMode.named,
          box_name=box_name,
          box_path=box_path,
        )
        if skip is not None and skip(found):
          continue
        best = found
        best_depth = depth
  return best


def _path_space_workset(project_dir: Path, std: StandardPaths) -> str:
  """The workset whose path space holds *project_dir*: the named one containing it, else primary.

  The same lookup ``create`` uses (``_find_workset_for_path``); a workset the lookup
  cannot read is an error to show, not a miss.
  """
  from kanibako.settings.paths import _find_workset_for_path

  try:
    ws, _member = _find_workset_for_path(project_dir, std)
  except (ReservedWorksetNameError, LegacyWorksetIdentityError, LegacyRegistryIdentityError):
    raise
  except WorksetError:
    return _PRIMARY_WORKSET_NAME
  return ws.name


def detect_box_mode(
  project_dir: Path,
  std: StandardPaths,
  config: BootstrapConfig,
) -> DetectionResult | None:
  """Detect *project_dir*'s box mode by the D3-mode order (first match wins).

  The ONE carrier of that order (system-design § Detection & import): the path space's
  registry, the standalone registry, an in-place settings file, then the treewalk.
  Every path compares literally (links unfollowed).  ``None`` = not a box (the
  caller's create path).
  """
  from kanibako.project import import_reconcile
  from kanibako.settings.paths import _early_scope

  literal = Path(literal_path(project_dir))

  # 1. The path space's registry has a box AT this path.  A box CONNECTED into a named
  # workset from outside its tree is recorded in that workset's registry, so it is that
  # workset's box: it beats an in-place standalone marker, as in ``detect_project_mode``.
  owned = _find_owning_box(
    literal, std, config, workset=_path_space_workset(literal, std)
  ) or find_connected_external_box(literal, std)
  if owned is not None and Path(literal_path(owned.box_path)) == literal:
    return DetectionResult(owned.mode, owned.box_path)

  # 2. The standalone registry has a box AT this path, marker or not: a root that lost
  # its marker is that box, broken (D4 state 3), never "not a box" with a create cure.
  # It resolves nameless, so the launch gate refuses it naming the file (D5).
  # 3. Else an in-place settings file.
  if registry_store.standalone_name_for_root(
    std.registry, literal
  ) is not None or is_standalone_root(literal):
    import_reconcile.check_standalone(
      std.registry, literal, journal=std.journal, early=_early_scope(std, BoxMode.standalone)
    )
    return DetectionResult(BoxMode.standalone, literal)

  # 4. The treewalk (compose, do not duplicate).  A PRIMARY result AT the path is its
  # no-marker default, so not a box; at an ANCESTOR it is that box (a subdir launch).
  result = detect_project_mode(literal, std, config)
  if result.mode is BoxMode.primary and Path(literal_path(result.project_root)) == literal:
    return None
  return result


def detect_box_or_default(
  project_dir: Path,
  std: StandardPaths,
  config: BootstrapConfig,
) -> DetectionResult:
  """:func:`detect_box_mode`, with "not a box" as primary AT *project_dir* (the create default)."""
  result = detect_box_mode(project_dir, std, config)
  if result is None:
    return DetectionResult(BoxMode.primary, Path(literal_path(project_dir)))
  return result


def standalone_box_name(box_root: Path, registered_name: str | None) -> str:
  """The name a standalone box at *box_root* goes by — its log files are named for it.

  LIVE name (P6d) ``<stored workset.kuid>_<current leaf>``, so a MOVED standalone
  keeps its identity.  The kuid comes from the box's own workset.yaml (the workset
  tier for a standalone); a pre-kuid box reads back SENTINEL and falls back to its
  ``standalone:`` registry KEY (*registered_name*), else the leaf.  Raises
  :class:`~kanibako.errors.DerivedBoxNameError`, cure included, when the current
  directory name has no ASCII spelling.
  """
  from kanibako import kuid
  from kanibako.launch import box_identity
  from kanibako.settings.config import read_workset_kuid

  stored_kuid = read_workset_kuid(box_root / WORKSET_META_FILE)
  if stored_kuid != kuid.SENTINEL:
    try:
      return box_identity.compose_standalone_name(stored_kuid, box_root)
    except DerivedBoxNameError as e:
      # ⚑ No fallback name: every door refuses with the one cure.
      raise DerivedBoxNameError(e.with_cure(CURE_MOVED_LEAF_NOT_ASCII), e.name) from None
  if registered_name is not None:
    return registered_name
  return box_root.name


def held_standalone_stores(registry: Path, own_root: Path) -> dict[str, str]:
  """``{store name: root}`` for every OTHER registered standalone whose root holds a kuid.

  The name is the one :func:`standalone_box_name` composes, so it is the snapshot store
  that box owns; ``snapshots.adopt_standalone_store`` never moves one.  A root with no
  valid kuid, an unreadable ``workset.yaml``, or a leaf with no ASCII spelling, holds
  nothing.
  """
  from kanibako import kuid
  from kanibako.launch import box_identity
  from kanibako.settings.config import read_workset_kuid

  held: dict[str, str] = {}
  for name, root_str in registry_store.load_standalone(registry).items():
    if root_str == str(own_root):
      continue
    root = Path(root_str)
    try:
      stored = read_workset_kuid(root / WORKSET_META_FILE)
      if kuid.is_valid(stored):
        held[box_identity.compose_standalone_name(stored, root)] = root_str
    except (ConfigError, DerivedBoxNameError, OSError):
      continue  # another box's broken root holds nothing; it is not this box's error
  return held


def unregistered_box_refusal(proj: ProjectPaths, belongs: str, name_cure: str) -> str | None:
  """The refusal head for the nameless primary (the ``__unregistered__`` phantom), else ``None``.

  *belongs* finishes "…, and" (``"a vault belongs to a box"``); *name_cure* is the command
  that names the box.  The caller appends its own last cure line.
  """
  if proj.mode is not BoxMode.primary or proj.name:
    return None
  return (
    f"no box at {proj.project_path} — kanibako has no box registered "
    f"for this directory, and {belongs}.\n"
    f"  Name the box:   {name_cure}\n"
  )


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
    box_root = result.project_root
    registered_name = registry_store.standalone_name_for_root(std.registry, box_root)
    return {
      "mode": result.mode,
      "name": standalone_box_name(box_root, registered_name),
      "workspace": box_root,
      "registered": registered_name is not None,
    }

  # Workset box (primary or named): identity from the per-workset registry, at the
  # DETECTED root (an ancestor box for a subdir), the path space's registry first.
  root = result.project_root
  owned = _find_owning_box(
    root, std, config, workset=_path_space_workset(root, std)
  ) or _find_owning_box(root, std, config)
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
