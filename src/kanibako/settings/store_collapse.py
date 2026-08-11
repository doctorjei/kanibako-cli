"""The COLLAPSE: four per-scope ``store_shape``s -> a bindings map + a seed + a sync list.

Prose: ``llm-docs/kanibako/settings/store_collapse.py.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, NamedTuple

from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.settings.settings_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.store_shape import StoreShape, StoreShapeSet


class CollapsedBind(NamedTuple):
  """One collapsed binding, ``(src, opts)`` - a ``None`` *src* signifies a MASK."""

  src: str | None
  opts: str | None


class CollapsedCopy(NamedTuple):
  """One collapsed copy, ``(src, dest, opts)`` - the dest is CARRIED, not a key."""

  src: str
  dest: str
  opts: str | None


#: The mask sentinel. The second slot is DEAD, not reserved: a mask is a tmpfs
#: with no host source and carries no mount-option vocabulary.
MASK: Final[CollapsedBind] = CollapsedBind(None, None)

#: The collapsed bindings, dest-keyed. ⚑ ORDER IS NOT MEANING - emission depth-sorts.
CollapsedBindings = dict[str, CollapsedBind]

#: A collapsed copy list, SCOPE-ORDERED. ⚑ A dest MAY repeat - that IS the overlay.
CollapsedCopies = list[CollapsedCopy]

#: Home is pid 0 - the foundation, seeded before the loop, in no scope's shape.
HOME_DEST: Final[str] = normalize_bind_dest("~")


@dataclass(frozen=True)
class CollapsedStore:
  """The collapse's OUTPUT: the three merged structures, and nothing else."""

  bindings: CollapsedBindings
  seeded: CollapsedCopies
  synced: CollapsedCopies


def collapse_store_shapes(
  store_shape_set: StoreShapeSet, home_bind: BindEntry,
) -> CollapsedStore:
  """Merge the four scopes' shapes into a bind map + a seed list + a sync list (PURE)."""
  # ⚑⚑ THE ORDER IS THE RULING: the two copy categories resolve at OPPOSITE ENDS
  # of the fold. Seeds apply to the home bind ALONE and complete BEFORE any binding
  # folds; syncs apply LAST, reading a bind map that is already final. Reading is
  # all the sync pass does - nothing is pruned and no copy competes with a mount.
  seeded = collapse_seeded(store_shape_set)
  bindings = _collapse_mounts(store_shape_set, home_bind)
  return CollapsedStore(
    bindings=bindings,
    seeded=seeded,
    synced=_collapse_synced(store_shape_set, bindings),
  )


def fold_opt(opts: str | None, token: str) -> str:
  """Append *token* to comma-separated *opts*: order-preserving, deduped, never a set."""
  tokens = opt_tokens(opts)
  return ",".join(tokens if token in tokens else [*tokens, token])


def opt_tokens(opts: str | None) -> list[str]:
  """*opts* as its comma-separated TOKEN list, stripped, empties dropped."""
  return [token.strip() for token in (opts or "").split(",") if token.strip()]


def is_mask(bind: CollapsedBind) -> bool:
  """Is *bind* a MASK? :data:`MASK`'s own rule, spelled ONCE for both halves of the split."""
  # ⚑ BOTH sides of the delivery split read it: the emitter skips what it answers
  # True for, the tmpfs arm takes exactly those. Two spellings would drift apart.
  return bind.src is None


def collapse_seeded(store_shape_set: StoreShapeSet) -> CollapsedCopies:
  """Every scope's seed arm concatenated IN SCOPE ORDER - nothing arbitrates, nothing prunes."""
  # ⚑ PUBLIC because it needs no home bind, and its SIGNATURE is the argument: home
  # is pid 0, seeded BEFORE the loop (§2a), so the seed arm is computable where no
  # bind map is. A caller with no home bind (the CREATE-side seed resolve) reads
  # this; ONE implementation, so a seed list cannot come out different depending on
  # which door it was fetched through.
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].seed:
      dest = normalize_bind_dest(dest_path)
      _refuse_seed_outside_home(dest, entry)
      copies.append(CollapsedCopy(entry.src, dest, entry.opts))
  return copies


def _collapse_synced(
  store_shape_set: StoreShapeSet, bindings: CollapsedBindings,
) -> CollapsedCopies:
  """Concatenate every scope's sync arm IN SCOPE ORDER, against the FINAL bind map."""
  # ⚑ NO home-only rule here: a sync dest resolves through whichever binding covers
  # it, and home is only the pid-0 foundation among them (that resolution is
  # DELIVERY and lands at the cutover - the emitted row carries the GUEST dest).
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].sync:
      dest = normalize_bind_dest(dest_path)
      _refuse_sync_at_a_bind_dest(bindings, dest, entry)
      copies.append(CollapsedCopy(entry.src, dest, entry.opts))
  return copies


def _collapse_mounts(
  store_shape_set: StoreShapeSet, home_bind: BindEntry,
) -> CollapsedBindings:
  """Fold every scope's bind + mask arms over the home foundation, in scope order."""
  combined: CollapsedBindings = {
    HOME_DEST: CollapsedBind(home_bind.src, home_bind.opts),
  }
  for scope in SCOPE_CONTAINMENT:
    _merge_bindings(combined, store_shape_set[scope])
  return combined


def _merge_bindings(combined: CollapsedBindings, shape: StoreShape) -> None:
  """Fold ONE scope's ro/rw arms into *combined*, then let its masks override."""
  for dest, entry, mode in _scope_binds(shape):
    _refuse_mode_contradiction(dest, entry, mode)
    _refuse_bind_under_mask(combined, dest, entry)
    _refuse_bind_over_bind(combined, dest, entry)
    _sweep(combined, dest)
    # ⚑ ``entry.opts`` ARRIVES CONCRETE - the category default was applied
    # upstream (``settings_launch._emit_bind``), which ``BindEntry.opts``'s
    # ``str | None`` type cannot say. So this ADDS the arm token to options that
    # already carry ``Z,U`` / ``ro``; it never stands in for the default.
    combined[dest] = CollapsedBind(entry.src, fold_opt(entry.opts, mode))
  for dest in _scope_masks(shape):
    _refuse_mask_on_mask(combined, dest)
    _refuse_mask_over_home(dest)
    _sweep(combined, dest)
    combined[dest] = MASK


def _scope_binds(shape: StoreShape) -> list[tuple[str, BindEntry, str]]:
  """THIS scope's ro+rw binds, PARENT-FIRST - the shortest path lands before what is inside it."""
  binds = [
    (normalize_bind_dest(dest_path), entry, mode)
    for arm, mode in ((shape.ro, "ro"), (shape.rw, "rw"))
    for dest_path, entry in arm.items()
  ]
  # ⚑ ``sorted`` is STABLE, so his ro-before-rw arm order survives at equal length.
  return sorted(binds, key=lambda bind: _segments(bind[0]))


def _scope_masks(shape: StoreShape) -> list[str]:
  """THIS scope's masks, CHILD-FIRST - the INVERSE order, for the inverted prohibition."""
  dests = [normalize_bind_dest(dest_path) for dest_path in shape.mask]
  return sorted(dests, key=_segments, reverse=True)


def _segments(dest: str) -> int:
  """*dest*'s path-component count - the containment sort key (``/`` = 1, ``/a`` = 2)."""
  return len(PurePosixPath(dest).parts)


def _is_within(dest: str, root: str) -> bool:
  """Is *dest* AT or INSIDE *root*? ⚑ Separator-guarded, never a bare prefix match."""
  return dest == root or dest.startswith(root.rstrip("/") + "/")


def _binds_under(combined: CollapsedBindings, dest: str) -> list[str]:
  """The BIND dests AT or INSIDE *dest* - what an arriving bind would subsume."""
  return [
    d for d, bind in combined.items() if bind.src is not None and _is_within(d, dest)
  ]


def _masks_over(combined: CollapsedBindings, dest: str) -> list[str]:
  """The MASK dests AT or CONTAINING *dest* - his "same or parent" side, inclusive."""
  return [
    d for d, bind in combined.items() if bind.src is None and _is_within(dest, d)
  ]


def _sweep(combined: CollapsedBindings, dest: str) -> None:
  """Delete every entry AT or INSIDE *dest* - the ONE operation both mounts share."""
  for occupied in [d for d in combined if _is_within(d, dest)]:
    del combined[occupied]


def _refuse_bind_over_bind(
  combined: CollapsedBindings, dest: str, entry: BindEntry,
) -> None:
  """A bind may NEST inside a bind - never take its point, never land above it."""
  subsumed = _binds_under(combined, dest)
  if not subsumed:
    return
  named = ", ".join(f"{d!r} ({combined[d].src!r})" for d in subsumed)
  raise SettingsError(
    f"the binding of {entry.src!r} at {dest!r} collides with the binding(s) already "
    f"collapsed at or inside it: {named}. A binding may nest INSIDE another, never "
    f"AT or OVER one - the mount order follows the path value, not the declaration "
    f"order, so the subsumed binding could never be reached. Suppress one of them, "
    f"or bind them at distinct destinations."
  )


def _refuse_bind_under_mask(
  combined: CollapsedBindings, dest: str, entry: BindEntry,
) -> None:
  """A bind may not be a CHILD of a mask; the tmpfs would swallow it."""
  # ⚑ The lone equality guard in the module, and it states the RULE rather than
  # patching a predicate: a bind may take a mask's own point (the sweep then
  # removes the mask), and may only never sit INSIDE one.
  masks = [d for d in _masks_over(combined, dest) if d != dest]
  if not masks:
    return
  raise SettingsError(
    f"the binding of {entry.src!r} at {dest!r} sits inside the mask at "
    f"{masks[0]!r}, which would swallow it. A mask may be a child of a binding, "
    f"never its parent - bind outside the mask, or do not declare the mask."
  )


def _refuse_mask_on_mask(combined: CollapsedBindings, dest: str) -> None:
  """A mask may not take another mask's point nor sit inside one: a void within a void."""
  covering = _masks_over(combined, dest)
  if not covering:
    return
  raise SettingsError(
    f"the mask at {dest!r} lands on the mask(s) already collapsed at "
    f"{', '.join(repr(d) for d in covering)}. A mask may not take another mask's "
    f"point nor sit inside one - a void within a void hides nothing the outer mask "
    f"is not hiding already. Declare one of them, not both."
  )


def _refuse_mask_over_home(dest: str) -> None:
  """Nothing may subsume home, masks included: a mask AT home or above it is refused."""
  if not _is_within(HOME_DEST, dest):
    return
  raise SettingsError(
    f"the mask at {dest!r} lands at or above the home binding at {HOME_DEST!r}, "
    f"which it would replace and leave the box with no home at all. Nothing may "
    f"subsume home - a mask may sit INSIDE home, never at its point nor over it: "
    f"mask a path inside home, or do not declare the mask."
  )


def _refuse_seed_outside_home(dest: str, entry: BindEntry) -> None:
  """A seed resolves into the HOME bind's source, so its dest must be inside home."""
  if _is_within(dest, HOME_DEST):
    return
  raise SettingsError(
    f"the seeded copy of {entry.src!r} targets {dest!r}, which is outside the home "
    f"binding at {HOME_DEST!r}. Seeds apply to the home bind ALONE - they resolve "
    f"into the box home store BEFORE any binding folds, so a destination outside it "
    f"has nowhere to land: give it a destination inside home, deliver it as a "
    f"binding, or declare it 'synced', which is not home-only."
  )


def _refuse_sync_at_a_bind_dest(
  bindings: CollapsedBindings, dest: str, entry: BindEntry,
) -> None:
  """A sync may land INSIDE a binding, never AT its point - the dest may BE the file."""
  # ⚑ Exact equality is the dict lookup itself: both sides are normalized dests, so
  # no containment predicate is needed and none is added. Stated STRUCTURALLY
  # because a PURE module cannot tell a file binding from a directory one.
  occupant = bindings.get(dest)
  if occupant is None or occupant.src is None:
    return
  raise SettingsError(
    f"the synced copy of {entry.src!r} targets {dest!r}, which is EXACTLY the "
    f"destination of the collapsed binding of {occupant.src!r}. A sync may land "
    f"strictly INSIDE a binding - it resolves through it into that binding's "
    f"source - but never AT its point: a file binding's destination IS the file, so "
    f"writing there would replace the bound inode. Sync to a path inside "
    f"{dest!r}, or do not bind at that destination."
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
