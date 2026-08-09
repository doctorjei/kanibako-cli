"""The COLLAPSE: four per-scope ``store_shape``s -> ONE bindings map + ONE copies map.

Prose: ``llm-docs/kanibako/settings/store_collapse.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, NamedTuple

from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.settings.settings_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.store_shape import StoreShape, StoreShapeSet


class CollapsedBind(NamedTuple):
  """One collapsed binding, ``(src, opts)`` - a ``None`` *src* signifies a MASK."""

  src: str | None
  opts: str | None


#: The mask sentinel. The second slot is DEAD, not reserved: a mask is a tmpfs
#: with no host source and carries no mount-option vocabulary.
MASK: Final[CollapsedBind] = CollapsedBind(None, None)

#: The collapsed bindings, dest-keyed. ⚑ ORDER IS NOT MEANING - emission depth-sorts.
CollapsedBindings = dict[str, CollapsedBind]

#: The collapsed copies, dest-keyed; one dest holds a LIST, appended in scope order.
CollapsedCopies = dict[str, list[BindEntry]]

#: Home is pid 0 - the foundation, seeded before the loop, in no scope's shape.
HOME_DEST: Final[str] = normalize_bind_dest("~")


@dataclass(frozen=True)
class CollapsedStore:
  """The collapse's OUTPUT: the two merged structures, and nothing else."""

  bindings: CollapsedBindings
  copies: CollapsedCopies


def collapse_store_shapes(
  store_shape_set: StoreShapeSet, home_bind: BindEntry,
) -> CollapsedStore:
  """Merge the four scopes' shapes into one bindings map + one copies map (PURE)."""
  combined_bindings: CollapsedBindings = {
    HOME_DEST: CollapsedBind(home_bind.src, home_bind.opts),
  }
  final_copies: CollapsedCopies = {}
  for scope in SCOPE_CONTAINMENT:
    shape = store_shape_set[scope]
    _merge_bindings(combined_bindings, shape)
    _prune_shadowed_copies(final_copies, _scope_dests(shape))
    _plant_copies(final_copies, combined_bindings, shape)
  return CollapsedStore(
    bindings=combined_bindings,
    copies={dest: final_copies[dest] for dest in sorted(final_copies)},
  )


def fold_opt(opts: str | None, token: str) -> str:
  """Append *token* to comma-separated *opts*: order-preserving, deduped, never a set."""
  tokens = opt_tokens(opts)
  return ",".join(tokens if token in tokens else [*tokens, token])


def opt_tokens(opts: str | None) -> list[str]:
  """*opts* as its comma-separated TOKEN list, stripped, empties dropped."""
  return [token.strip() for token in (opts or "").split(",") if token.strip()]


def _merge_bindings(combined: CollapsedBindings, shape: StoreShape) -> None:
  """Fold ONE scope's ro/rw arms into *combined*, then let its masks override."""
  for dest, entry, mode in _scope_binds(shape):
    _refuse_double_bind(dest, entry, combined.get(dest))
    _refuse_mode_contradiction(dest, entry, mode)
    _refuse_bind_under_mask(dest, entry, combined)
    _refuse_bind_over_bind(dest, entry, combined)
    _subsume_masks_beneath(combined, dest)
    combined[dest] = CollapsedBind(entry.src, fold_opt(entry.opts, mode))
  for dest_path in shape.mask:
    combined[normalize_bind_dest(dest_path)] = MASK


def _prune_shadowed_copies(copies: CollapsedCopies, dests: list[str]) -> None:
  """Drop every copy AT or INSIDE one of *dests* - a mount shadows it, so skip it."""
  for dest in dests:
    for copy_dest in [d for d in copies if _is_within(d, dest)]:
      del copies[copy_dest]


def _plant_copies(
  copies: CollapsedCopies, combined: CollapsedBindings, shape: StoreShape,
) -> None:
  """Append ONE scope's seed arm to *copies*, unmasking any dest it plants into."""
  for dest_path, entry in shape.seed.items():
    dest = normalize_bind_dest(dest_path)
    occupant = combined.get(dest)
    if occupant is not None and occupant.src is None:
      _refuse_directory_onto_mask(dest, entry)
      del combined[dest]  # It is masked; remove the MASK, we plant copies here.
    copies.setdefault(dest, []).append(entry)


def _scope_binds(shape: StoreShape) -> list[tuple[str, BindEntry, str]]:
  """THIS scope's ro+rw binds, SHALLOWEST-FIRST - a parent lands before its children."""
  binds = [
    (normalize_bind_dest(dest_path), entry, mode)
    for arm, mode in ((shape.ro, "ro"), (shape.rw, "rw"))
    for dest_path, entry in arm.items()
  ]
  # ⚑ ``sorted`` is STABLE, so his ro-before-rw arm order survives at equal depth.
  return sorted(binds, key=lambda bind: _depth(bind[0]))


def _scope_dests(shape: StoreShape) -> list[str]:
  """THIS scope's binding + mask dests. ⚑ CURRENT SCOPE ONLY - never accumulated."""
  return [
    normalize_bind_dest(dest)
    for arm in (shape.ro, shape.rw, shape.mask)
    for dest in arm
  ]


def _depth(dest: str) -> int:
  """*dest*'s component count - the shallow-first sort key (``/`` = 1, ``/a`` = 2)."""
  return len(PurePosixPath(dest).parts)


def _is_within(dest: str, root: str) -> bool:
  """Is *dest* AT or INSIDE *root*? ⚑ Separator-guarded, never a bare prefix match."""
  return dest == root or dest.startswith(root.rstrip("/") + "/")


def _beneath(combined: CollapsedBindings, dest: str, *, masks: bool) -> list[str]:
  """The mask (or bind) dests STRICTLY inside *dest*, in collapsed order."""
  return [
    d for d, bind in combined.items()
    if (bind.src is None) is masks and d != dest and _is_within(d, dest)
  ]


def _refuse_double_bind(
  dest: str, entry: BindEntry, occupant: CollapsedBind | None,
) -> None:
  """Row 1 ACROSS scopes: two real sources contend for one destination."""
  if occupant is None or occupant.src is None:
    return  # Unbound, or a MASK - and a binding may override a mask.
  raise SettingsError(
    f"two bindings target the destination {dest!r}: {occupant.src!r} already binds "
    f"there and {entry.src!r} would bind over it. A binding may override a MASK, "
    f"never another binding - suppress one of them, or bind them at distinct "
    f"destinations."
  )


def _refuse_bind_over_bind(
  dest: str, entry: BindEntry, combined: CollapsedBindings,
) -> None:
  """RULE 1 - a bind may NEST inside a bind, never land ABOVE one already collapsed."""
  subsumed = _beneath(combined, dest, masks=False)
  if not subsumed:
    return
  raise SettingsError(
    f"the binding of {entry.src!r} at {dest!r} would subsume the binding(s) already "
    f"collapsed beneath it at {', '.join(repr(d) for d in subsumed)}. A binding may "
    f"nest INSIDE another, never over it - the mount order follows the path value, "
    f"not the declaration order, so the inner one could never be reached."
  )


def _refuse_bind_under_mask(
  dest: str, entry: BindEntry, combined: CollapsedBindings,
) -> None:
  """RULE 3 - a bind may not be a CHILD of a mask; the tmpfs would swallow it."""
  masks = [
    d for d, bind in combined.items()
    if bind.src is None and d != dest and _is_within(dest, d)
  ]
  if not masks:
    return
  raise SettingsError(
    f"the binding of {entry.src!r} at {dest!r} sits inside the mask at "
    f"{masks[0]!r}, which would swallow it. A mask may be a child of a binding, "
    f"never its parent - bind outside the mask, or do not declare the mask."
  )


def _subsume_masks_beneath(combined: CollapsedBindings, dest: str) -> None:
  """RULE 2 + 6 - a bind SUBSUMES the masks under it, and subsumed means REMOVED."""
  for masked in _beneath(combined, dest, masks=True):
    del combined[masked]


def _refuse_directory_onto_mask(dest: str, entry: BindEntry) -> None:
  """RULE 5 - only a copied FILE may land on a mask's exact point, never a directory."""
  if not Path(entry.src).is_dir():
    return
  raise SettingsError(
    f"the copy of {entry.src!r} would land on the mask at {dest!r}, but its source "
    f"is a DIRECTORY. A copied FILE may take a mask's exact point; a directory may "
    f"not - copy its contents beneath the mask, or do not declare the mask."
  )


def _refuse_mode_contradiction(dest: str, entry: BindEntry, mode: str) -> None:
  """Refuse a per-entry ``ro``/``rw`` override that contradicts its own arm."""
  opposite = "rw" if mode == "ro" else "ro"
  if opposite in opt_tokens(entry.opts):
    raise SettingsError(
      f"the binding at {dest!r} sits in the {mode!r} arm but its options "
      f"{entry.opts!r} carry {opposite!r}. The mode is the ARM, not an option - "
      f"declare it in the arm that means it."
    )
