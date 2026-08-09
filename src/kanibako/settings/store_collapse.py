"""The COLLAPSE: four per-scope ``store_shape``s -> ONE bindings map + ONE copy list.

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

#: The collapsed copies, SCOPE-ORDERED. ⚑ A dest MAY repeat - that IS the overlay.
CollapsedCopies = list[CollapsedCopy]

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
  """Merge the four scopes' shapes into one bindings map + one copy list (PURE)."""
  # ⚑ The copy half completes FIRST and reads no binding: copies apply to the home
  # bind alone, so no mount arbitrates them and the two halves never interact.
  copies = _collapse_copies(store_shape_set)
  return CollapsedStore(
    bindings=_collapse_mounts(store_shape_set, home_bind), copies=copies,
  )


def fold_opt(opts: str | None, token: str) -> str:
  """Append *token* to comma-separated *opts*: order-preserving, deduped, never a set."""
  tokens = opt_tokens(opts)
  return ",".join(tokens if token in tokens else [*tokens, token])


def opt_tokens(opts: str | None) -> list[str]:
  """*opts* as its comma-separated TOKEN list, stripped, empties dropped."""
  return [token.strip() for token in (opts or "").split(",") if token.strip()]


def _collapse_copies(store_shape_set: StoreShapeSet) -> CollapsedCopies:
  """Concatenate every scope's seed arm IN SCOPE ORDER - nothing arbitrates, nothing prunes."""
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].seed.items():
      dest = normalize_bind_dest(dest_path)
      _refuse_copy_outside_home(dest, entry)
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
    combined[dest] = CollapsedBind(entry.src, fold_opt(entry.opts, mode))
  for dest in _scope_masks(shape):
    _refuse_mask_on_mask(combined, dest)
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


def _refuse_copy_outside_home(dest: str, entry: BindEntry) -> None:
  """A copy resolves into the HOME bind's source, so its dest must be inside home."""
  if _is_within(dest, HOME_DEST):
    return
  raise SettingsError(
    f"the copy of {entry.src!r} targets {dest!r}, which is outside the home binding "
    f"at {HOME_DEST!r}. Copies apply to the home bind ALONE - they resolve into the "
    f"box home store, so a destination outside it has nowhere to land: give it a "
    f"destination inside home, or deliver it as a binding."
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
