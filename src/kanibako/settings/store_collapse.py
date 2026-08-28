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
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Final, NamedTuple

from kanibako.settings.kb_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.settings_categories import COPY, ENV, MOUNT, CategoryEntry
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

#: The DECLARATION KEY that put the mount at each collapsed dest, dest-keyed.
#: 🛑🛑 A SIDE MAP, AND IT MAY NEVER BECOME A TUPLE SLOT. ``meta.assembly.bindings``
#: is spec'd ``dict[guest_dest -> (host_src, opts)]`` and every bind-shaped entry as
#: a 1-or-2 element tuple (keyspec ``:434``/``:440``/``:450``/``:603-605``); those
#: arities are NORMATIVE, so widening :class:`CollapsedBind`, :class:`CollapsedCopy`
#: or ``BindEntry`` to carry a key would put the code in contradiction with the spec.
#: ⚑ The env leaf shows the spec grants provenance a SLOT deliberately where it means
#: to (``:467``, ``(value, scope, key)``) and nowhere else - so here it travels
#: BESIDE the shape instead, off the entry list, the seam :func:`collapse_env`
#: already uses.
DeclaringKeys = dict[str, str]

#: The mount declaration keys the fold reads: ``(scope, kind, dest) -> key``.
_MountKeys = dict[tuple[str, str, str], str]

#: The two mount KINDS a declaration key is filed under. ⚑ ONE scope may hold a bind
#: AND a mask at one dest - the producer keeps them in different arms and the mask
#: loop below overrides the bind - so the kind is part of the identity, not
#: decoration.
_KIND_BIND: Final[str] = "bind"
_KIND_MASK: Final[str] = "mask"


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
  """The collapse's OUTPUT: the three merged structures, plus WHO DECLARED each mount.

  🛑 *declared_by* IS NOT A FOURTH ``meta.assembly`` LEAF AND MUST NOT BECOME ONE.
  The three above are written into the snapshot FIELD BY FIELD
  (``commands.start._install_assembly_collapse``), so nothing carries this one into
  the store, and it is EMPTY unless a caller hands :func:`collapse_store_shapes` the
  entry list. Landing it AS a leaf would be a closed-keyspace addition - a spec and
  manifest edit, and not the code's to make.
  """

  bindings: CollapsedBindings
  seeded: CollapsedCopies
  synced: CollapsedCopies
  declared_by: DeclaringKeys = field(default_factory=dict)


def collapse_store_shapes(
  store_shape_set: StoreShapeSet,
  home_bind: BindEntry,
  entries: Sequence[CategoryEntry] | None = None,
) -> CollapsedStore:
  """Merge the four scopes' shapes into a bind map + a seed list + a sync list (PURE).

  *entries* is the SAME credential-gated ``CategoryEntry`` list the shapes were built
  from, and it buys ONE thing: :attr:`CollapsedStore.declared_by`. It is OPTIONAL
  because a caller that only wants the assembly leaves needs none of it, and omitting
  it collapses byte-identically.

  ⚑ IT IS THE ENTRY LIST, NOT A WIDER SHAPE - the same seam :func:`collapse_env`
  takes, for the same reason its docstring gives: ``store_shape.build_store_shape``
  drops ``CategoryEntry.key_segments`` when it writes the arm, and the arm's tuple is
  spec-normative (see :data:`DeclaringKeys`), so a declaration key can only travel
  beside the shape.
  """
  # ⚑⚑ NEITHER COPY ARM MEETS THE BIND MAP. Seeds apply to the home bind ALONE and
  # complete BEFORE any binding folds; syncs apply LAST, at DELIVERY, resolving
  # through whichever bind covers each dest. Both arms are therefore plain
  # concatenations here - nothing is pruned, nothing is refused for sharing a dest
  # with a mount, and no copy competes with one.
  bindings, declared_by = _collapse_mounts(
    store_shape_set, home_bind,
    _mount_declaration_keys(entries or (), store_shape_set),
  )
  return CollapsedStore(
    bindings=bindings,
    seeded=collapse_seeded(store_shape_set),
    synced=_collapse_synced(store_shape_set),
    declared_by=declared_by,
  )


def _mount_declaration_keys(
  entries: Sequence[CategoryEntry], store_shape_set: StoreShapeSet,
) -> _MountKeys:
  """Each scope's MOUNT declarations, filed by ``(scope, kind, dest)`` (PURE).

  ⚑ AN ENTRY IS MATCHED TO THE ARM ROW IT PRODUCED, never classified by a table of
  its own: a bind entry is filed only when the scope's shape holds an IDENTICAL
  ``BindEntry`` at its dest, and a mask entry only when the mask arm holds that dest.
  That is what keeps the categories out of this module - ``store_shape`` alone
  decides which category reaches which arm, and a second copy of that table here
  would drift the day one moves. It also files the §0 row-5 WINNER rather than the
  loser the producer dropped, because only the winner is in the arm.
  """
  keys: _MountKeys = {}
  for scope in SCOPE_CONTAINMENT:
    shape = store_shape_set[scope]
    bind_rows = {
      normalize_bind_dest(dest_path): bind
      for arm in (shape.ro, shape.rw)
      for dest_path, bind in arm.items()
    }
    mask_dests = {normalize_bind_dest(dest_path) for dest_path in shape.mask}
    for entry in entries:
      if entry.scope != scope or entry.delivery != MOUNT:
        continue
      dest = normalize_bind_dest(entry.box_dest)
      if entry.host_src is None:
        if dest in mask_dests:
          keys[scope, _KIND_MASK, dest] = entry.key
      elif bind_rows.get(dest) == BindEntry(entry.host_src, entry.options):
        keys[scope, _KIND_BIND, dest] = entry.key
  return keys


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
  # ⚑ THAT RULING IS UNTOUCHED BY THE COVERAGE RULE, and the two must not be confused:
  # 2026-08-12 forbids refusing a sync for SHARING a dest with a mount; 2026-08-28
  # refuses one for having NO mount. Opposite conditions, so neither narrows the other
  # - a sync at, inside, or on top of a bind is as ordinary as it ever was.
  # 🛑 THE COVERAGE REFUSAL IS DELIBERATELY NOT CALLED FROM HERE. It needs the FINAL
  # bind map, which this arm has by construction at its call site - but the fold's
  # OTHER caller previews a working set with a partial map, where the same declaration
  # reads uncovered and the launch reads covered. See ``refuse_uncovered_synced``,
  # which states the measurement and names the seam that asks it.
  copies: CollapsedCopies = []
  for scope in SCOPE_CONTAINMENT:
    for dest_path, entry in store_shape_set[scope].sync:
      copies.append(
        CollapsedCopy(entry.src, normalize_bind_dest(dest_path), entry.opts)
      )
  return copies


def _collapse_mounts(
  store_shape_set: StoreShapeSet, home_bind: BindEntry, mount_keys: _MountKeys,
) -> tuple[CollapsedBindings, DeclaringKeys]:
  """Fold every scope's bind + mask arms over the home foundation, in scope order."""
  combined: CollapsedBindings = {
    HOME_DEST: CollapsedBind(home_bind.src, home_bind.opts),
  }
  # ⚑ HOME IS IN NO SCOPE'S SHAPE and is named by no bind-shaped key - it is the pid-0
  # FOUNDATION the seam builds from the RO derived ``meta.box.home`` - so it starts
  # with no declaring key and honestly stays that way.
  declared_by: DeclaringKeys = {}
  for scope in SCOPE_CONTAINMENT:
    _merge_bindings(combined, declared_by, store_shape_set[scope], scope, mount_keys)
  return combined, declared_by


def _merge_bindings(
  combined: CollapsedBindings,
  declared_by: DeclaringKeys,
  shape: StoreShape,
  scope: str,
  mount_keys: _MountKeys,
) -> None:
  """Fold ONE scope's ro/rw arms into *combined*, then let its masks override."""
  # ⚑ THE ARRIVING KEY IS READ ONCE PER ENTRY and handed to the refusals AND to the
  # claim below: the declaration that refuses and the declaration that lands are the
  # same one, so they must not be looked up twice.
  for dest, entry, mode in _scope_binds(shape):
    key = mount_keys.get((scope, _KIND_BIND, dest))
    _refuse_mode_contradiction(dest, entry, mode)
    _refuse_bind_under_mask(combined, declared_by, dest, entry, key)
    _refuse_bind_over_bind(combined, declared_by, dest, entry, key)
    _sweep(combined, declared_by, dest)
    # ⚑ ``entry.opts`` ARRIVES CONCRETE - the category default was applied
    # upstream (``settings_launch._emit_bind``), which ``BindEntry.opts``'s
    # ``str | None`` type cannot say. So this ADDS the arm token to options that
    # already carry ``Z,U`` / ``ro``; it never stands in for the default.
    combined[dest] = CollapsedBind(entry.src, fold_opt(entry.opts, mode))
    _claim(declared_by, dest, key)
  for dest in _scope_masks(shape):
    key = mount_keys.get((scope, _KIND_MASK, dest))
    _refuse_mask_on_mask(combined, declared_by, dest, key)
    _refuse_mask_over_home(dest, key)
    _sweep(combined, declared_by, dest)
    combined[dest] = MASK
    _claim(declared_by, dest, key)


def _declared_clause(key: str | None) -> str:
  """`` declared by '<key>'``, or EMPTY when the fold was handed no entry list.

  ⚑ ONE SPELLING OF THE CLAUSE, for all four refusals and both result phrases alike.
  Two spellings is how a user reads two different sentences for one fact, and how the
  set drifts the day one of them is reworded.

  ⚑ EMPTY IS AN HONEST ANSWER, not a placeholder: a caller that passed no *entries*
  asked a question this fold cannot answer, and every message below then reads exactly
  as it did before provenance existed. Nothing is guessed and no key is invented.
  """
  return f" declared by '{key}'" if key is not None else ""


def _claim(declared_by: DeclaringKeys, dest: str, key: str | None) -> None:
  """File *key* as the declaration now occupying *dest*; a caller with none files none."""
  # ⚑ RECORDED AT THE FOLD, never derived afterwards. "Which scope's mask survived at
  # this dest" is NOT answerable from the finished map: a bind may take a mask's own
  # point and a later scope's mask may retake it, leaving two scopes naming one dest
  # and only one of them the occupant. Deriving it is exactly the second opinion this
  # module exists to prevent.
  if key is not None:
    declared_by[dest] = key


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


def _sweep(
  combined: CollapsedBindings, declared_by: DeclaringKeys, dest: str,
) -> None:
  """Delete every entry AT or INSIDE *dest* - the ONE operation both mounts share."""
  # ⚑ THE SIDE MAP IS SWEPT WITH THE MAP IT DESCRIBES. A subsumed dest keeps no
  # declaration: a key left behind names a mount the box does not have, which is the
  # one thing this provenance exists to stop a display doing.
  for occupied in [d for d in combined if is_within(d, dest)]:
    del combined[occupied]
    declared_by.pop(occupied, None)


def _refuse_bind_over_bind(
  combined: CollapsedBindings,
  declared_by: DeclaringKeys,
  dest: str,
  entry: BindEntry,
  key: str | None,
) -> None:
  """A bind may NEST inside a bind - never take its point, never land above it.

  ⚑ THE REMEDY SENTENCE IS THE ONE ALREADY PUBLISHED for the identical class of
  refusal a layer up (``raise_binding_vs_binding``, MIGRATION.md §2.2), WORD FOR WORD
  and deliberately: a user meeting one of these has met the other, and two spellings
  of one cure send them to two mechanisms. ⚑ "Suppress" is a present-``None`` at the
  key, resolved to an OMIT at cascade merge - it is NOT masking, and saying "suppress
  one of them" without saying HOW was this message's defect until cutover 2c, when it
  stopped being swallowed and became a user's only diagnostic.

  ⚑⚑ IT NAMES BOTH PARTICIPANTS BY KEY - the ARRIVING declaration and each SUBSUMED
  one - which is what keyspec ``:153-165`` obliges: *"the error MUST name the
  extending declaration, the occupant, and the dest. The refusal is symmetric; the
  diagnosis is not."* The remedy above tells the reader to null a KEY, so a message
  that named no key asked for an edit it did not say where to make. 🛑 The occupant's
  key is READ OFF *declared_by*, the fold's own record - NEVER re-derived from the
  dest, which cannot say which scope's declaration is the one sitting there.
  🛑🛑 AND NOT BY WIDENING ``BindEntry``: the entry tuple is spec-normative (keyspec
  ``:603-605``), and the boarded 🐞 that once said this "structurally cannot" was
  wrong about the route, not about the prohibition. See :data:`DeclaringKeys`.
  """
  subsumed = _binds_under(combined, dest)
  if not subsumed:
    return
  named = ", ".join(
    f"{d!r} ({combined[d].src!r}{_declared_clause(declared_by.get(d))})"
    for d in subsumed
  )
  raise SettingsError(
    f"the binding{_declared_clause(key)} of {entry.src!r} at {dest!r} collides with the "
    f"binding(s) already collapsed at or inside it: {named}. A binding may nest "
    f"INSIDE another, never AT or OVER one - the mount order follows the path "
    f"value, not the declaration "
    f"order, so the subsumed binding could never be reached. To change what occupies "
    f"a destination you must SUPPRESS the entry you do not want and then declare the "
    f"one you do. An override is not enough: these are two different KEYS, so both "
    f"survive the cascade. Set the unwanted key to null in the settings file for its "
    f"scope (a file may write its own scope and the scopes it contains). Either "
    f"declaration may be the one you keep."
  )


def _refuse_bind_under_mask(
  combined: CollapsedBindings,
  declared_by: DeclaringKeys,
  dest: str,
  entry: BindEntry,
  key: str | None,
) -> None:
  """A bind may not be a CHILD of a mask; the tmpfs would swallow it.

  ⚑ BOTH PARTICIPANTS BY KEY (keyspec ``:153-165``). The mask's key is the one that
  matters here: it is the OTHER declaration, it may live in a scope the reader is not
  looking at, and — unlike the binding — it carries no host source to recognise it by.
  """
  # ⚑ The lone equality guard in the module, and it states the RULE rather than
  # patching a predicate: a bind may take a mask's own point (the sweep then
  # removes the mask), and may only never sit INSIDE one.
  masks = [d for d in _masks_over(combined, dest) if d != dest]
  if not masks:
    return
  raise SettingsError(
    f"the binding{_declared_clause(key)} of {entry.src!r} at {dest!r} sits inside the "
    f"mask{_declared_clause(declared_by.get(masks[0]))} at {masks[0]!r}, which would "
    f"swallow it. A mask may be a child of a binding, never its parent - bind "
    f"outside the mask, or do not declare the mask."
  )


def _refuse_mask_on_mask(
  combined: CollapsedBindings,
  declared_by: DeclaringKeys,
  dest: str,
  key: str | None,
) -> None:
  """A mask may not take another mask's point nor sit inside one: a void within a void.

  ⚑ BOTH PARTICIPANTS BY KEY (keyspec ``:153-165``), and this is the refusal that
  needed it most: NEITHER mask has a host source, so before the keys the message named
  two bare destinations and nothing a reader could match to a file they had written.
  """
  covering = _masks_over(combined, dest)
  if not covering:
    return
  named = ", ".join(
    f"{d!r}{_declared_clause(declared_by.get(d))}" for d in covering
  )
  raise SettingsError(
    f"the mask{_declared_clause(key)} at {dest!r} lands on the mask(s) already "
    f"collapsed at {named}. A mask may not take another mask's "
    f"point nor sit inside one - a void within a void hides nothing the outer mask "
    f"is not hiding already. Declare one of them, not both."
  )


def _refuse_mask_over_home(dest: str, key: str | None) -> None:
  """Nothing may subsume home, masks included: a mask AT home or above it is refused.

  ⚑ ONE PARTICIPANT HAS A KEY AND THE OTHER GENUINELY HAS NONE, so this message names
  one and says why. Home is pid 0 — the FOUNDATION the launch seam builds from the RO
  derived ``meta.box.home``, seeded beneath every scope's shape and in no scope's arm
  (:func:`_collapse_mounts`). 🛑 There is no bind-shaped key to name and none is
  invented: pointing a reader at a key they cannot write would be worse than the
  silence it replaced.
  """
  if not is_within(HOME_DEST, dest):
    return
  raise SettingsError(
    f"the mask{_declared_clause(key)} at {dest!r} lands at or above the home binding at "
    f"{HOME_DEST!r}, which it would replace and leave the box with no home at all. "
    f"Home is the foundation and no settings key declares it, so there is nothing to "
    f"suppress on that side. Nothing may "
    f"subsume home - a mask may sit INSIDE home, never at its point nor over it: "
    f"mask a path inside home, or do not declare the mask."
  )


def refuse_uncovered_synced(
  bindings: CollapsedBindings, copies: CollapsedCopies,
) -> None:
  """Every ``synced`` dest must be COVERED by a mount - nothing bound is nothing kept.

  ⚖️ RULED 2026-08-28 - *"I don't think we should be checking for XDG; we should be
  checking that the paths resolve. That's it."* The CONDITION is what is refused, never
  a cause: a dest spelled with ``$XDG_DATA_HOME`` and a dest typed out as ``/data/z``
  are the same defect and get the same answer, and a user who MIRRORS the box's layout
  under a bind of their own is refused nothing.

  ⚑⚑ IT IS THE COLLAPSE'S OWN QUESTION, ASKED WITH THE COLLAPSE'S OWN MACHINERY.
  :func:`covering_bind` is the ONE containment lookup - the same one the sync DELIVERY
  half resolves each row through (``commands.start._synced_host_dest``) and the same one
  :func:`pair_declarations` reads. A second spelling here is how a row gets refused
  against one map and delivered through another.

  🛑 A MASK COUNTS AS A COVER, and that is not a carve-out - it is what keeps this rule
  from colliding with one that already exists. A mask IS in the map, so
  :func:`covering_bind` finds it; what a mask then does to a copy is spec §0's two copy
  rows, already enforced by ``commands.start._refuse_synced_under_mask`` (mask as
  PARENT, and a DIRECTORY at a mask's own point). Treating a mask as "no cover" would
  put TWO refusals on one destination with two different messages, and would refuse the
  file-at-a-mask's-point cell the table ACCEPTS.

  ⚑ ``seeded`` IS NOT ASKED HERE, because it is asked already. A seed copies at CREATE,
  before any binding folds, when the only mount in existence is home (pid 0) - so its
  coverage universe is exactly ``{HOME_DEST}`` and "covered" collapses to "inside home",
  which is precisely what :func:`_refuse_seed_outside_home` tests, through the SAME
  :func:`is_within` primitive :func:`covering_bind` is built from. Same invariant, two
  moments. The two can never both fire on one destination: they read different ARMS, so
  a dest carrying a seed AND a sync has each row judged at its own moment against its
  own map, which is the correct answer rather than a collision.

  ⚑ PUBLIC, and its ONE caller is the launch seam
  (``commands.start._install_assembly_collapse``) rather than :func:`_collapse_synced`
  itself. MEASURED 2026-08-28, and this is the reason: every other refusal in this
  module is MONOTONE - adding a scope can only create a conflict, never dissolve one -
  but coverage runs the other way, since a further scope can only ADD binds. The
  collapse has a second caller that builds a DELIBERATELY PARTIAL map
  (``commands.workset_cmd`` previews a working set with no box tier and a stand-in home),
  and there a ``workset.synced`` at ``/opt/cred`` reads UNCOVERED while the launch that
  also has ``box.bindings.rw./opt`` reads COVERED. Refusing inside the fold would make a
  listing refuse a configuration that launches. So the rule lives with its siblings and
  is asked at the one seam that can honestly say its map is a whole box's.

  ⚑ THE SPEC CARRIES THIS RULE (ratified 2026-08-28). §0's numbered list has a SIXTH
  entry for it, ``:196`` names the MASK **and COVERAGE** refusals, and ``:125`` now reads
  "meets mounts or is REFUSED" - a property this function is what MAKES true, and which
  was false as measured (a literal ``/data/z`` reaches ``covering_bind`` and gets
  ``None``). 🛑 Cite those by their TERMS, not the line numbers: they have rotted before.
  """
  for copy in copies:
    if covering_bind(bindings, copy.dest) is not None:
      continue
    raise SettingsError(
      f"the synced copy of {copy.src!r} targets {copy.dest!r}, which NO mount covers. "
      f"A 'synced' copy is applied LAST, after the bind map is final, and it resolves "
      f"THROUGH the mount containing its destination - so with nothing bound at or "
      f"above {copy.dest!r} the copy would be written into the container's own "
      f"ephemeral storage and lost the moment the box stops, silently. Give it a "
      f"destination inside a mount: somewhere under {HOME_DEST!r} (the home binding, "
      f"which always exists), or under a binding you declare yourself - a "
      f"'bindings.rw' entry at or above {copy.dest!r} makes this copy land on the host "
      f"and persist."
    )


def _refuse_seed_outside_home(dest: str, entry: BindEntry) -> None:
  """A seed resolves into the HOME bind's source, so its dest must be inside home.

  ⚑⚑ THIS IS THE COVERAGE RULE, AT SEEDED'S OWN MOMENT - not a different invariant.
  A seed copies at CREATE, before any binding folds, so the only mount that exists is
  home; "covered by a mount" and "inside home" are the same test over a one-element
  map, and :func:`is_within` is the same primitive :func:`covering_bind` is built from.
  🛑 Do NOT "unify" this into :func:`refuse_uncovered_synced` by handing it the whole
  bind map: :func:`collapse_seeded` is called BARE by the create-side seed resolve,
  which has no bind map at all, and widening it would make the seed arm uncomputable
  exactly where it must be computed. See that function's own signature note.
  """
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
# this declaration's dest, and what sits there.
#
# ⚑ THE REVERSE QUESTION - given a collapsed dest, name the declaration that put a
# mount there - is answered by :attr:`CollapsedStore.declared_by`, and ONLY for a
# caller that handed the fold its entry list. It is what turns "the mask at /opt/x"
# into a path the user's own key names, which for a mask ABOVE the declaration is
# the whole diagnosis. 🛑 IT IS STILL NOT A TUPLE SLOT and must not become one -
# see :data:`DeclaringKeys`.
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


def derivation_result(row: Any, declared_by: Mapping[str, str] | None = None) -> str:
  """One :class:`Derivation` as the RESULT PHRASE a display prints for it.

  ⚑ EVERY OUTCOME PRINTS SOMETHING, and a LOSS prints WHY and WHERE. "No mount" on
  its own is the answer a user cannot act on; the destination that swallowed the
  declaration is the whole diagnosis, and for a mask ABOVE the declaration it is not
  a destination the user's own key names.

  *declared_by* is :attr:`CollapsedStore.declared_by` - the fold's own record of
  which declaration put each mount where. It is OPTIONAL because only a display that
  FOLDS IN PROCESS has it: ``box show --effective`` reads a STORED snapshot, whose
  ``meta.assembly.bindings`` leaf carries no key and cannot be taught to without a
  closed-keyspace addition. Given it, the two LOSS phrases name the declaration that
  took the destination; without it each phrase is exactly what it always was.

  ⚑ BOTH MOUNT LOSSES TAKE IT, not just the mask. The mask was the acute case — a
  mask has no host source, so its row named nothing a reader could match to a file
  they had written — but once the refusals name keys, a display that keyed every
  outcome EXCEPT the superseding binding would be the odd one out. The copy branch
  below is the one genuine exception, and it says why in place.

  ⚑ IT LIVES BESIDE THE ``DERIVED_*`` OUTCOMES IT NAMES, not inside either display.
  It was ``config_display._derivation_result`` while ``box show --effective`` was the
  only reader; ``commands.workset_cmd._print_effective_shares`` became the second one
  when that listing started arbitrating, and two copies of these sentences would
  drift the day an outcome's meaning moved. ⚑ ``Any`` rather than ``Derivation`` on
  purpose: the optional halves of that tuple are decided BY the outcome, and each
  branch below reads only the half its own outcome guarantees.
  """
  if row.outcome == DERIVED_COPY:
    return f"{row.copy.src} -> {row.copy.dest}  (copy)"
  keys = declared_by or {}
  if row.outcome == DERIVED_MASKED:
    return (
      f"(no mount — the mask{_declared_clause(keys.get(row.at))} at {row.at} covers "
      f"this destination, and a mask has no host source: the box sees nothing at "
      f"that path)"
    )
  if row.outcome == DERIVED_SUPERSEDED:
    if row.bind is None:
      # ⚑ THIS BRANCH TAKES NO KEY, and *declared_by* could not supply one: the
      # taker here is another COPY row, and the side map records MOUNTS. Naming
      # the mount at this dest would name a delivery that did not take it.
      return (
        "(no copy — no collapsed copy row accounts for this declaration; "
        "another declaration at this destination took it)"
      )
    return (
      f"(no mount — the binding{_declared_clause(keys.get(row.at))} of {row.bind.src} "
      f"at {row.at} occupies this destination)"
    )
  if row.outcome == DERIVED_AMBIGUOUS:
    return (
      f"{row.bind.src} -> {row.at}  [{row.bind.opts}]  (mount — AMBIGUOUS: "
      f"another declaration at this destination names the same source, and "
      f"only one of them is this mount)"
    )
  if row.outcome == DERIVED_MOUNT:
    # ⚑ THE DELIVERY IS STATED, not left to be inferred from the presence of an
    # options column: a mount is LIVE and shadows the dest, a copy runs once and
    # is then the box's own file, and a reader who cannot tell them apart cannot
    # answer the question this display exists for (N2).
    return f"{row.bind.src} -> {row.at}  [{row.bind.opts}]  (mount)"
  # ⚑ UNCOVERED, and it is not "no mount": this resolve carries no collapsed
  # bind map at all (a NARROW resolve writes none), so the question was not
  # answered rather than answered in the negative.
  return (
    "(unknown — this resolve carries no collapsed binding map, so what the "
    "box receives here cannot be read)"
  )
