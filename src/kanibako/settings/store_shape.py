"""The ``store_shape`` PRODUCER: one scope's category entries -> its five-arm realization view.

Prose: ``llm-docs/kanibako/settings/store_shape.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from kanibako.settings.settings_categories import (
  CategoryCollision,
  CategoryEntry,
  raise_binding_vs_binding,
  raise_extension_onto_occupied,
)
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.settings_store import SCOPE_CONTAINMENT, BindEntry, BindMap

#: A dest-keyed mask arm. ⚑ The VALUE IS NEVER READ - presence is the whole fact.
MaskMap = dict[str, bool]

#: ``StoreShape`` per scope, ``("system", "agent", "workset", "box")`` - the collapse's order.
StoreShapeSetMap = dict[str, "StoreShape"]

# Category -> the ARM it folds into (the ratified five-key fold).
_ARM: Final[dict[str, str]] = {
  "bindings.ro": "ro",
  "bindings.rw": "rw",
  "caches": "rw",
  "common": "rw",
  "masks": "mask",
  "seeded": "seed",
  "synced": "sync",
}

# Declared categories that carry NO arm: ``env`` is not a path delivery at all, and
# ``secret_path`` is PARKED out of the disk-store shape (producer DESIGN §7.4).
_NO_ARM: Final[frozenset[str]] = frozenset({"env", "secret_path"})

# The CONCRETE declarations the producer folds - spec §0's source-of-truth layer
# minus the parked ``secret_path``, which reaches no arm to contend over.
_FOLDED_CONCRETE: Final[tuple[str, ...]] = ("bindings.ro", "bindings.rw")

# The ABSTRACT declarations that fold into a BIND arm. ⚑ ``seeded`` is abstract too
# but is a COPY and folds into ``seed``, so it is not a peer of these two.
_FOLDED_ABSTRACT_MOUNT: Final[tuple[str, ...]] = ("caches", "common")


@dataclass(frozen=True)
class StoreShape:
  """ONE scope's realization view - five dest-keyed arms, opts already CONCRETE."""

  ro: BindMap = field(default_factory=dict)
  rw: BindMap = field(default_factory=dict)
  mask: MaskMap = field(default_factory=dict)
  seed: BindMap = field(default_factory=dict)
  sync: BindMap = field(default_factory=dict)


@dataclass(frozen=True)
class StoreShapeSet:
  """One :class:`StoreShape` per scope, plus the §0 row-5 warnings as DATA."""

  shapes: StoreShapeSetMap
  warnings: tuple[CategoryCollision, ...] = ()

  def __getitem__(self, scope: str) -> StoreShape:
    """``store_shape_set[scope]`` - the collapse's own spelling (§2)."""
    return self.shapes[scope]


def build_store_shape_set(entries: list[CategoryEntry]) -> StoreShapeSet:
  """Fold a ``snapshot_category_entries`` list into one :class:`StoreShape` PER SCOPE (PURE)."""
  by_scope: dict[str, list[CategoryEntry]] = {scope: [] for scope in SCOPE_CONTAINMENT}
  for entry in entries:
    if entry.scope not in by_scope:
      raise SettingsError(
        f"category entry {entry.key!r} carries scope {entry.scope!r}, which is not "
        f"one of the declared scopes {SCOPE_CONTAINMENT}"
      )
    if _arm_of(entry) is not None:
      by_scope[entry.scope].append(entry)
  shapes: StoreShapeSetMap = {}
  warnings: list[CategoryCollision] = []
  for scope in SCOPE_CONTAINMENT:
    shape, scope_warnings = build_store_shape(by_scope[scope])
    shapes[scope] = shape
    warnings.extend(scope_warnings)
  return StoreShapeSet(shapes=shapes, warnings=tuple(warnings))


def build_store_shape(
  entries: list[CategoryEntry],
) -> tuple[StoreShape, list[CategoryCollision]]:
  """Fold ONE scope's entries into its :class:`StoreShape` + its §0 row-5 warnings."""
  by_dest: dict[str, list[CategoryEntry]] = {}
  for entry in entries:
    by_dest.setdefault(entry.box_dest, []).append(entry)
  arms: dict[str, BindMap] = {"ro": {}, "rw": {}, "seed": {}, "sync": {}}
  mask: MaskMap = {}
  warnings: list[CategoryCollision] = []
  for box_dest, group in by_dest.items():
    survivors, dest_warnings = _within_scope_survivors(box_dest, group)
    warnings.extend(dest_warnings)
    for entry in survivors:
      arm = _ARM[entry.category]
      if arm == "mask":
        mask[box_dest] = True
        continue
      if entry.host_src is None:
        raise SettingsError(
          f"category entry {entry.key!r} folds into the {arm!r} arm but has no "
          f"host source (only 'masks' and 'env' are source-less)"
        )
      arms[arm][box_dest] = BindEntry(entry.host_src, entry.options)
  shape = StoreShape(
    ro=arms["ro"], rw=arms["rw"], mask=mask, seed=arms["seed"], sync=arms["sync"],
  )
  return shape, warnings


def _within_scope_survivors(
  box_dest: str, group: list[CategoryEntry],
) -> tuple[list[CategoryEntry], list[CategoryCollision]]:
  """Apply the §0 rows decidable inside ONE scope at ONE dest: 1 (same-scope), 3, 5.

  ⚑ Rows 2 and 4, and row 1's CROSS-scope case, are the COLLAPSE's and are NOT
  applied here: a mask and a binding may share one scope, and eating the binding
  now would strand the collapse's own mask loop with nothing to override.
  """
  concrete = [e for e in group if e.category in _FOLDED_CONCRETE]
  abstract = [e for e in group if e.category in _FOLDED_ABSTRACT_MOUNT]
  if len(concrete) > 1:
    raise_binding_vs_binding(box_dest, concrete)
  if concrete and abstract:
    raise_extension_onto_occupied(
      box_dest, extension=abstract[-1], base=concrete[-1],
    )
  if len(abstract) < 2:
    return group, []
  # ROW 5 - same-scope abstraction ambiguity: the existing ordering stands (LAST
  # wins, as `_most_specific` resolves it once scope is held constant) and the
  # loss is WARNED every launch, never silently swallowed.
  winner, losers = abstract[-1], abstract[:-1]
  warning = CategoryCollision(
    box_dest=box_dest,
    scope=winner.scope,
    winner_key=winner.key,
    loser_keys=tuple(e.key for e in losers),
  )
  survivors = [e for e in group if not any(e is loser for loser in losers)]
  return survivors, [warning]


def _arm_of(entry: CategoryEntry) -> str | None:
  """The arm *entry* folds into, or ``None`` when its category has no arm."""
  if entry.category in _NO_ARM:
    return None
  arm = _ARM.get(entry.category)
  if arm is None:
    raise SettingsError(
      f"category entry {entry.key!r} names category {entry.category!r}, which has "
      f"no store_shape arm (the keyspace is closed: an undeclared category is not "
      f"a category)"
    )
  return arm
