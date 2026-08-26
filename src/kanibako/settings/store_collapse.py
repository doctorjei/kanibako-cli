"""The COLLAPSE: four scopes -> a bindings map + a seed list + a sync list + an env map.

The ENV SLOTS collapse here too, off the SAME scope-ordered entry list the shapes are
built from rather than off the shapes: ``env`` carries no path and folds into no arm
(``store_shape._NO_ARM``), so it has nothing to contribute to a ``StoreShape`` and
:func:`collapse_env` runs BESIDE the shape set. The arbitration is the same one - a
slot is written once, and the containing scope writes it first. The per-run ``-e``
values are the CASCADE'S CLI LEVEL over that result and are applied INSIDE the same
function, above every scope and below nothing.

⚑ THE MODULE ALSO READS ITS OWN OUTPUT, in a clearly marked section at the foot:
:func:`covering_bind` and :func:`pair_declarations` answer questions ABOUT the
collapsed map - which mount owns a path, and what a DECLARATION actually got. They
are here because those questions are the map's own; a second spelling elsewhere is
how a display comes to disagree with the box. Neither re-folds anything.

Prose: ``llm-docs/kanibako/settings/store_collapse.py.md``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, NamedTuple

from kanibako.settings.kb_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.settings_categories import COPY, ENV, CategoryEntry
from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
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


class CollapsedEnv(NamedTuple):
  """One env slot's winner, ``(value, scope, key)`` - provenance travels WITH the value."""

  value: str
  scope: str
  key: str


#: The collapsed env slots, VAR-keyed. ⚑ ONE entry per VAR by construction: a second
#: scope naming a VAR is refused, never arbitrated, so this map cannot lose a value.
CollapsedEnvs = dict[str, CollapsedEnv]

#: The PROVENANCE SCOPE a per-run ``-e`` writes when it fills a VACANT slot. It is a
#: LABEL, not a settings scope: no cascade level, no containment rank, nothing
#: resolves against it. ⚑ It cannot BE a scope — see :func:`_apply_cli_env`.
CLI_PROVENANCE_SCOPE: Final[str] = "cli"

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
  # ⚑⚑ NEITHER COPY ARM MEETS THE BIND MAP. Seeds apply to the home bind ALONE and
  # complete BEFORE any binding folds; syncs apply LAST, at DELIVERY, resolving
  # through whichever bind covers each dest. Both arms are therefore plain
  # concatenations here - nothing is pruned, nothing is refused for sharing a dest
  # with a mount, and no copy competes with one.
  return CollapsedStore(
    bindings=_collapse_mounts(store_shape_set, home_bind),
    seeded=collapse_seeded(store_shape_set),
    synced=_collapse_synced(store_shape_set),
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
  """Every scope's seed arm concatenated IN SCOPE ORDER - EVERY row, nothing pruned."""
  # ⚑ PUBLIC because it needs no home bind, and its SIGNATURE is the argument: home
  # is pid 0, seeded BEFORE the loop (§2a), so the seed arm is computable where no
  # bind map is. A caller with no home bind (the CREATE-side seed resolve) reads
  # this; ONE implementation, so a seed list cannot come out different depending on
  # which door it was fetched through.
  # ⚑⚑ NOTHING IS ARBITRATED AT A DESTINATION (spec :147-149) - and that now holds
  # ACROSS the two arms as well as within one. A dest carrying both a seed and a
  # sync keeps BOTH rows, because DELIVERY ORDER decides it: `box create` seeds
  # first, then writes every sync row into the bind that covers it UNCONDITIONALLY
  # (Jei, 2026-08-11: *"write synced to it once at creation, irrespective of
  # date"*). The sync therefore owns the dest by having written it LAST, which is
  # also what makes the launch-time mtime gate mean anything - it compares against a
  # dest the SYNC wrote. The earlier prune here answered the same question by
  # deleting the seed, which cost the user a declared copy to protect a gate.
  # Prose: ``llm-docs/kanibako/settings/store_collapse.py.md``.
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].seed:
      dest = normalize_bind_dest(dest_path)
      # ⚑ A mis-declared dest is still an ERROR BY NAME, and a sync sharing that dest
      # does not quietly excuse it.
      _refuse_seed_outside_home(dest, entry)
      copies.append(CollapsedCopy(entry.src, dest, entry.opts))
  return copies


def collapse_env(
  entries: list[CategoryEntry], cli_env: Mapping[str, str] | None = None,
) -> CollapsedEnvs:
  """Arbitrate the env VAR slots: the FIRST scope to name a VAR holds it (PURE).

  ⚑ IT TAKES THE ENTRY LIST, NOT THE SHAPE SET, and that is the whole seam: ``env``
  folds into no ``StoreShape`` arm, so the shapes have already dropped it by the time
  the mount fold runs. It reads the same CREDENTIAL-GATED list the shapes are built
  from, so the mounts and the variables describe one box.

  ⚑ THERE IS NO COMPARATOR HERE, and there is nothing to add one to: the walk order
  IS the arbitration and the refusal below is what enforces it. A slot is written
  ONCE and the CONTAINING scope writes it first, so a second scope's key could never
  take effect - it is refused rather than silently dropped or silently preferred.

  ⚑ A SAME-SCOPE CONTEST CANNOT ARISE. One scope's ``env`` node is a MAP keyed by
  VAR, and the agent tier's two cascade levels are overlaid into ONE effective node
  per name upstream (``settings_launch.snapshot_category_entries``), so each scope
  reaches here with at most one entry per VAR. Do not add a within-scope guard.

  🛑 THE KEYS THEMSELVES ARE ORDINARY WRITE-MANY SETTINGS KEYS. The same key in
  several files cascades and the NEAREST file wins, exactly as every other key does;
  that happens before this function is reached and is untouched by it. What is
  written once is the VARIABLE, and the slot is its NAME.

  *cli_env* is the per-run ``-e VAR=VALUE`` map, already parsed and validated at the
  launch door (``commands.start._parse_cli_env``). It is the CASCADE'S CLI LEVEL
  applied to the env family — Jei, 2026-08-14: *"-e should override the key values,
  not the environment variables themselves"* — and it is applied AFTER the walk, by
  :func:`_apply_cli_env`.
  """
  slots: CollapsedEnvs = {}
  for scope in SCOPE_CONTAINMENT:
    for entry in entries:
      if entry.delivery != ENV or entry.scope != scope:
        continue
      held = slots.get(entry.box_dest)
      if held is not None:
        _refuse_env_twin(entry, held)
      slots[entry.box_dest] = CollapsedEnv(entry.options, entry.scope, entry.key)
  _apply_cli_env(slots, cli_env)
  return slots


def _apply_cli_env(slots: CollapsedEnvs, cli_env: Mapping[str, str] | None) -> None:
  """Overlay the per-run ``-e`` values on the SETTLED slots: override an owner, or fill a vacancy.

  ⚑⚑ IT RUNS AFTER THE CONTAINMENT WALK, AND THAT IS THE WHOLE CONSTRUCTION.
  ``-e`` can never CONTEST a slot — there are exactly two cases and neither is a
  contest: a VAR some key owns has its VALUE replaced for this launch, and a VAR no
  key owns gets a slot of its own. So no refusal above can name a ``-e``, and none
  should ever be taught to.

  🛑 IT CANNOT RIDE THE CLI SETTINGS LEVEL INSTEAD, and this is the measured reason
  a future reader must not "unify" it there: the keyspace has no scope-less ``env``
  spelling and no ``cli`` namespace (``settings_keyspace.key_validity`` refuses both),
  so the level would have to spell a CONCRETE scope — at which point the flag becomes
  a SECOND scope's key naming a variable the user's own key already names, and
  :func:`_refuse_env_twin` above refuses the launch. Overriding by flag would refuse
  exactly the configurations it exists to serve.

  ⚑ AN OVERRIDDEN SLOT KEEPS ITS OWNING PROVENANCE (scope + key). The key still owns
  the variable; what ``-e`` supplies is a value for ONE launch, and the leaf carries
  no marker saying so. That omission is deliberate: nothing reads env provenance for
  display today, and a fourth tuple field would cost a spec + manifest + closure
  change to say something no user can see.

  ⚑ A VACANCY gets :data:`CLI_PROVENANCE_SCOPE` and the key spelling ``-e <VAR>`` —
  HONEST (it names what put the value there) and INERT (no consumer parses either
  field; they are read for display and diagnostics only). ``-e <VAR>`` is not a key
  and is not meant to look like one: there is no such key to write.
  """
  for var, value in (cli_env or {}).items():
    held = slots.get(var)
    slots[var] = (
      CollapsedEnv(value, held.scope, held.key) if held is not None
      else CollapsedEnv(value, CLI_PROVENANCE_SCOPE, f"-e {var}")
    )


def _collapse_synced(store_shape_set: StoreShapeSet) -> CollapsedCopies:
  """Every scope's sync arm concatenated IN SCOPE ORDER - EVERY row, nothing pruned."""
  # ⚑ NO home-only rule here: a sync dest resolves through whichever binding covers
  # it, and home is only the pid-0 foundation among them (that resolution is
  # DELIVERY - the emitted row carries the GUEST dest).
  # ⚑⚑ AND NO BIND MAP EITHER (Jei, 2026-08-12: *"don't check for sync. Let it
  # clobber whatever it wants."*). A sync at a binding's EXACT dest is ordinary: the
  # copy lands on top of the bind and most of the bind remains intact. It is not the
  # collapse's business which mount a copy shares a destination with, so this arm
  # takes no bind map and cannot refuse for a reason the delivery half owns.
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].sync:
      copies.append(
        CollapsedCopy(entry.src, normalize_bind_dest(dest_path), entry.opts)
      )
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


def is_within(dest: str, root: str) -> bool:
  """Is *dest* AT or INSIDE *root*? ⚑ Separator-guarded, never a bare prefix match."""
  # ⚑ PUBLIC for the same reason ``path_depth`` is (cutover 2a-2): DELIVERY asks
  # this question too. ``commands/start._synced_host_dest`` finds the bind covering
  # a sync's dest with it, so "inside" means ONE thing on both sides of the split -
  # a second spelling would let the fold and the copy disagree about which bind a
  # destination sits in.
  return dest == root or dest.startswith(root.rstrip("/") + "/")


def covering_bind(bindings: Mapping[str, CollapsedBind], dest: str) -> str | None:
  """The collapsed dest covering *dest* - the LONGEST prefix, or ``None`` if none does.

  ⚑ ONE spelling, and it now has THREE askers, which is why it moved here from
  ``commands.start`` (where it was ``_synced_cover``, still its caller under this
  name): the sync delivery half asks it twice - whether the covering mount refuses
  a copy, and which mount a copy resolves through - and :func:`pair_declarations`
  asks it to say what a DECLARATION actually got. A second lookup is how a row gets
  refused against one mount and delivered through another, and how a display comes
  to disagree with the box about which mount owns a path.

  LONGEST PREFIX = the INNERMOST bind, which is the one the box sees at that path.
  ⚑ Every candidate is a prefix of ONE string, so length totally orders them and
  there is no tie to break: two covers of equal length are equal.

  ⚑ A dest is DATA: it is compared and sliced as a PATH, never split on ``.``.
  """
  covers = [root for root in bindings if is_within(dest, root)]
  return max(covers, key=len) if covers else None


def _binds_under(combined: CollapsedBindings, dest: str) -> list[str]:
  """The BIND dests AT or INSIDE *dest* - what an arriving bind would subsume."""
  return [
    d for d, bind in combined.items() if bind.src is not None and is_within(d, dest)
  ]


def _masks_over(combined: CollapsedBindings, dest: str) -> list[str]:
  """The MASK dests AT or CONTAINING *dest* - his "same or parent" side, inclusive."""
  return [
    d for d, bind in combined.items() if bind.src is None and is_within(dest, d)
  ]


def _sweep(combined: CollapsedBindings, dest: str) -> None:
  """Delete every entry AT or INSIDE *dest* - the ONE operation both mounts share."""
  for occupied in [d for d in combined if is_within(d, dest)]:
    del combined[occupied]


def _refuse_bind_over_bind(
  combined: CollapsedBindings, dest: str, entry: BindEntry,
) -> None:
  """A bind may NEST inside a bind - never take its point, never land above it.

  ⚑ THE REMEDY SENTENCE IS THE ONE ALREADY PUBLISHED for the identical class of
  refusal a layer up (``raise_binding_vs_binding``, MIGRATION.md §2.2), WORD FOR WORD
  and deliberately: a user meeting one of these has met the other, and two spellings
  of one cure send them to two mechanisms. ⚑ "Suppress" is a present-``None`` at the
  key, resolved to an OMIT at cascade merge - it is NOT masking, and saying "suppress
  one of them" without saying HOW was this message's defect until cutover 2c, when it
  stopped being swallowed and became a user's only diagnostic.
  🐞 BOARDED, out of 2c's scope: this still names no declaration KEY for either
  participant and structurally cannot - ``build_store_shape`` drops
  ``CategoryEntry.key_segments`` when it writes the arm, so naming them is a producer
  shape change.
  """
  subsumed = _binds_under(combined, dest)
  if not subsumed:
    return
  named = ", ".join(f"{d!r} ({combined[d].src!r})" for d in subsumed)
  raise SettingsError(
    f"the binding of {entry.src!r} at {dest!r} collides with the binding(s) already "
    f"collapsed at or inside it: {named}. A binding may nest INSIDE another, never "
    f"AT or OVER one - the mount order follows the path value, not the declaration "
    f"order, so the subsumed binding could never be reached. To change what occupies "
    f"a destination you must SUPPRESS the entry you do not want and then declare the "
    f"one you do. An override is not enough: these are two different KEYS, so both "
    f"survive the cascade. Set the unwanted key to null in the settings file for its "
    f"scope (a file may write its own scope and the scopes it contains). Either "
    f"declaration may be the one you keep."
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
  if not is_within(HOME_DEST, dest):
    return
  raise SettingsError(
    f"the mask at {dest!r} lands at or above the home binding at {HOME_DEST!r}, "
    f"which it would replace and leave the box with no home at all. Nothing may "
    f"subsume home - a mask may sit INSIDE home, never at its point nor over it: "
    f"mask a path inside home, or do not declare the mask."
  )


def _refuse_seed_outside_home(dest: str, entry: BindEntry) -> None:
  """A seed resolves into the HOME bind's source, so its dest must be inside home."""
  if is_within(dest, HOME_DEST):
    return
  raise SettingsError(
    f"the seeded copy of {entry.src!r} targets {dest!r}, which is outside the home "
    f"binding at {HOME_DEST!r}. Seeds apply to the home bind ALONE - they resolve "
    f"into the box home store BEFORE any binding folds, so a destination outside it "
    f"has nowhere to land: give it a destination inside home, deliver it as a "
    f"binding, or declare it 'synced', which is not home-only."
  )


def _refuse_env_twin(arriving: CategoryEntry, held: CollapsedEnv) -> None:
  """Two scopes' keys naming ONE variable: the slot is taken, so refuse - naming BOTH.

  ⚑ THE SOLE RAISE SITE FOR THE ENV SLOT, deliberately: the severity of a contested
  slot is one decision and it is spelled in one place.
  """
  raise SettingsError(
    f"the environment variable {arriving.box_dest!r} is claimed by two keys: "
    f"{held.key!r} at the {held.scope!r} scope already holds it, and "
    f"{arriving.key!r} at the {arriving.scope!r} scope names it again. A variable "
    f"is written ONCE and the containing scope writes it first, so the second "
    f"declaration could never take effect. Give the variable ONE owner: keep the "
    f"key at the scope the value belongs to and remove the other one. An override "
    f"is not enough - these are two different KEYS, so both survive the cascade. To "
    f"change the value WITHOUT moving its owner, write the SAME key "
    f"({held.key!r}) in a nearer settings file: keys cascade, and the nearest "
    f"file wins."
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


# ---------------------------------------------------------------------------
# THE READER - a DECLARATION paired with what the box ACTUALLY RECEIVES.
# ---------------------------------------------------------------------------
#
# 🛑 THE PAIRING IS ASKED IN ONE DIRECTION ONLY, and that is why it needs nothing
# added to :class:`CollapsedBind` / :class:`CollapsedCopy`. The obligation
# (keyspec ``:88``) is DECLARATION -> DELIVERY: "``--effective`` shows BOTH the
# declaration and the derived binding and a user can see WHY a mount exists." That
# question is answered by CONTAINMENT against the finished map - which dest covers
# this declaration's dest, and what sits there. The REVERSE question (given a
# collapsed bind, name the declaration that produced it) is the one the collapse
# cannot answer, and the one ``_refuse_bind_over_bind``'s boarded 🐞 wants; it is
# NOT this one.
#
# 🛑🛑 AND THE TUPLES MAY NOT GROW. ``meta.assembly.bindings`` is spec'd as
# ``dict[guest_dest -> (host_src, opts)]`` and the two copy leaves as
# ``list[(host_src, guest_dest, opts)]`` (keyspec ``:434``/``:440``/``:450``) - the
# arities are NORMATIVE, and the env leaf's ``(value, scope, key)`` shows the spec
# grants provenance in a tuple deliberately where it means to. Widening either
# tuple to carry a declaration key would put the code in contradiction with the
# spec.
#
# ⚑ NOTHING IS RECOMPUTED HERE. The arbitrated answer is READ off the collapsed
# map; a second fold would be exactly the second opinion ``--effective`` exists to
# DETECT.

#: The declaration IS the mount the box receives at its own destination.
DERIVED_MOUNT: Final[str] = "mount"
#: The declaration reaches the box as a COPY row - arbitrated at no destination.
DERIVED_COPY: Final[str] = "copy"
#: A MASK covers the destination: the box receives NO mount for this declaration.
DERIVED_MASKED: Final[str] = "masked"
#: Another declaration's delivery occupies or contains the destination.
DERIVED_SUPERSEDED: Final[str] = "superseded"
#: Two declarations the collapsed map cannot tell apart - SAY SO, never pick one.
DERIVED_AMBIGUOUS: Final[str] = "ambiguous"
#: Nothing in the collapsed map covers the destination (an INCOMPLETE map, §2 gate).
DERIVED_UNCOVERED: Final[str] = "uncovered"


class Declaration(NamedTuple):
  """One declaration offered for pairing: ``(key, dest, src, delivery)``.

  *dest* is the RESOLVED guest destination and *src* the host source, both as the
  declaration's own materialised derivation spells them. *src* is ``None`` for a
  declaration that asks for no source - a mask.
  """

  key: str
  dest: str
  src: str | None
  delivery: str


class Derivation(NamedTuple):
  """One declaration paired with WHAT THE BOX RECEIVES - the ``--effective`` row.

  *at* is the destination the outcome was found AT: the declaration's own when it
  holds its point, an ANCESTOR when something above swallowed it. ⚑ It is the
  field that turns "no mount" into a diagnosis, so a renderer must print it.
  """

  declaration: Declaration
  outcome: str
  at: str | None
  bind: CollapsedBind | None = None
  copy: CollapsedCopy | None = None


def pair_declarations(
  declarations: Sequence[Declaration],
  bindings: Mapping[str, CollapsedBind],
  copies: Sequence[CollapsedCopy] = (),
) -> tuple[Derivation, ...]:
  """Pair every declaration with the delivery the box actually receives for it (PURE).

  *bindings* is the COLLAPSED map (``meta.assembly.bindings``) and *copies* the two
  collapsed copy lists concatenated; both are the arbitrated outputs, read and never
  re-derived.

  ⚑⚑ THE INPUT MAY CONTAIN ARBITRATION LOSERS, and it is meant to: the reserved
  ``binding_derivations`` node materialises a derivation for WINNERS AND LOSERS
  ALIKE (``settings_categories.derive_binding_keys``), which is exactly why reading
  THAT node alone reports a mount for a declaration the box receives nothing for.
  A loser is identified HERE, by what occupies its destination.
  """
  # ⚑ A TUPLE KEY, never a joined string: a dest is DATA and may hold any character,
  # so a separator would be a claim about paths this module has no business making.
  claims: dict[tuple[str, str | None], int] = {}
  for decl in declarations:
    if decl.delivery != COPY:
      claims[decl.dest, decl.src] = claims.get((decl.dest, decl.src), 0) + 1
  return tuple(_pair_one(decl, bindings, copies, claims) for decl in declarations)


def _pair_one(
  decl: Declaration,
  bindings: Mapping[str, CollapsedBind],
  copies: Sequence[CollapsedCopy],
  claims: Mapping[tuple[str, str | None], int],
) -> Derivation:
  """One declaration's outcome - the whole decision, in one place."""
  if decl.delivery == COPY:
    # ⚑ NOTHING IS ARBITRATED AT A COPY'S DESTINATION (spec :147-149), so a copy
    # that reached the list reached it whole. One that did NOT is a declaration the
    # PRODUCER dropped - a §0 row-5 loser - and that is a loss, not a copy.
    row = next(
      (c for c in copies if c.dest == decl.dest and c.src == decl.src), None,
    )
    if row is not None:
      return Derivation(decl, DERIVED_COPY, decl.dest, copy=row)
    return Derivation(decl, DERIVED_SUPERSEDED, decl.dest)
  cover = covering_bind(bindings, decl.dest)
  if cover is None:
    # Reachable only from an INCOMPLETE map - a narrow resolve writes no bindings
    # leaf (``commands.start._install_assembly_collapse``'s whole-box gate). Named
    # rather than guessed: "no mount" and "no map" are different answers.
    return Derivation(decl, DERIVED_UNCOVERED, None)
  bind = bindings[cover]
  if is_mask(bind):
    # ⚑ AT the dest or ABOVE it, one answer: a mask is a tmpfs with no host source,
    # so the box sees nothing at that path either way. *at* carries the difference,
    # and it is the whole diagnosis when the mask is a PARENT - the declaration's
    # own dest is not in the map at all, so a lookup by dest finds nothing and a
    # renderer that reads "absent" as "fine" says nothing.
    return Derivation(decl, DERIVED_MASKED, cover, bind)
  if cover != decl.dest or bind.src != decl.src:
    return Derivation(decl, DERIVED_SUPERSEDED, cover, bind)
  # ⚑ A tie is UNCONSTRUCTIBLE from a launch's own declarations - two live binds at
  # one dest is ``_refuse_bind_over_bind``, and one scope's two abstractions at one
  # dest is §0 row 5, whose loser the producer drops. It IS constructible from the
  # pre-arbitration node when a row-5 pair share a source, and there the honest
  # answer is that the map cannot say which of them the mount came from.
  ambiguous = claims.get((decl.dest, decl.src), 0) > 1
  return Derivation(
    decl, DERIVED_AMBIGUOUS if ambiguous else DERIVED_MOUNT, cover, bind,
  )
