# The `store_shape` Producer (roadmap step 4)

A `store_shape` is the REALIZATION view of ONE scope: `{ro, rw, mask, seed, sync}` — the three MOUNT
arms dest-keyed, the two COPY arms flat ordered lists — with the ABSTRACT categories (`caches`,
`common`) already materialised into `rw` and every bind arm
carrying CONCRETE mount options. A `StoreShapeSet` holds one per scope over
`("system", "agent", "workset", "box")` — **four scopes, deliberately not the six cascade levels.**

This module builds one per scope and **stops there**: it never compares two scopes, because
comparing scopes is the grand-unification collapse's whole job (roadmap step 6).

**Authority:** `designs/store-shape-producer-DESIGN.md` (this step) ·
`designs/grand-unification-collapse-DESIGN.md` §2/§2a (the consumer) ·
`specs/settings-keyspace-1.8.0.md` §0 (the collision table).

## Status: CONSUMED — all three SHAPE-FED leaves, and there is no second route left

`commands/start.py:_install_assembly_collapse` calls `build_store_shape_set` on the launch path and
feeds it to `collapse_store_shapes`, storing the result at
`meta.assembly.{bindings,seeded,synced}` — three leaves, each on its own gate. ⚑ The same seam
writes a FOURTH, `meta.assembly.env`, but **not from a shape**: `env` sits in `_NO_ARM`, so
`collapse_env` reads the ENTRY LIST beside the shape set. The five-key `StoreShape` is untouched by
it, and that is deliberate — the sixth arm is the obvious place and the ruled-out one.

⚑ **All three SHAPE-FED leaves have consumers, and each is the ONLY route to what it delivers**
(`meta.assembly.env` is read too — through `start.py:_launch_env_map`, by the launch and by
`box show --effective` — but it is NOT a shape output, so it is not one of the three).
`start.py:_launch_bind_map` reads `bindings` (2a-3), `_launch_seed_list` reads `seeded` (2b-2) and
`_launch_synced_list` reads `synced` (2b-3); an ABSENT leaf on a whole-box resolve is a NAMED error,
not a fallback. ⚑ **Cutover 6-R3 deleted the second, cross-scope route entirely** — there is no
`reconcile_categories` and no `ReconciledCategories`. §0 is applied by this producer (rows 3, 5 and
row 1 SAME-scope), by the collapse (rows 2, 4 and row 1 cross-scope), and by the launch seam's two
functions for the inputs the collapse cannot see (`secret_path_deliveries`, `narrow_table_winners`).

⚑ The collapse REFUSES some shapes the retired route accepted, and since cutover 2c that refusal IS
the launch's: it propagates out of the resolve and stops the box. 🛑 **This paragraph used to say the
call site catches `SettingsError`, leaves the leaves ABSENT and logs at `debug`.** That swallow was
deleted with 2c, so an absent leaf now means a NARROW resolve and never a refusal — do not "fix" the
collapse to stop raising, and do not restore a catch to keep a launch alive.

## The seam — WITHIN-SCOPE is the producer's, CROSS-SCOPE is the collapse's

Sort spec §0's five collision rows by *how many scopes you must look at to decide the outcome*:

| row | case | scopes needed | owner |
|---|---|---|---|
| 1 | two CONCRETE declarations at one dest — ERROR always | one **or** two | **both** |
| 2 | `masks` at a dest a binding occupies — OVERRIDE | two | collapse |
| 3 | an ABSTRACTION extending onto an occupied dest — ERROR, keep the base | one | **producer** |
| 4 | abstraction vs abstraction, DIFFERENT scopes — scope precedence, silent | two | collapse |
| 5 | abstraction vs abstraction, SAME scope — existing ordering + WARN | one | **producer** |

"These two declarations contradict each other" is a fact about ONE scope's own configuration.
"This scope beats that scope" is a fact about the LADDER. Different concepts, different owners —
and the collapse implements rows 1 (cross-scope), 2 and 4 and implements rows 3 and 5 nowhere.

## What each within-scope row can actually LOOK like

Every bind-shaped category is TERMINAL and DEST-KEYED (R-5/R-6), so within ONE scope a category
holds at most one entry per destination. A same-scope, same-dest collision is therefore always
CROSS-CATEGORY, and there are exactly three shapes:

* **row 1** — `bindings.ro[X]` + `bindings.rw[X]`. ⚑ These fold into DIFFERENT arms, so a producer
  that skipped the check would let both survive silently, one per arm, and the contradiction would
  never surface anywhere.
* **row 3** — `caches[X]` or `common[X]` onto `bindings.{ro,rw}[X]`.
* **row 5** — `caches[X]` + `common[X]` (both fold to `rw`). This is the one same-scope abstract
  pair that actually occurs in shipped configuration.

Row 5 keeps the LAST entry in input order. Within one scope that is exactly what
`settings_categories._most_specific` computes, since its scope-precedence key is constant there.

## The fold

`bindings.ro`→`ro` · `bindings.rw` + `caches` + `common`→`rw` · `masks`→`mask` · `seeded`→`seed` ·
`synced`→`sync`. The abstractions are already `Z,U` rw mounts (spec §0 "EXTEND `bindings.rw`"), so
the fold is materialisation, not reclassification.

⚑ **`seeded` and `synced` ARE COPIES AND STAY COPIES.** The fold changes KEY SHAPE only; a copy
never becomes a mount.

### Where the CATEGORY DEFAULT is applied — measured, and it is NOT here

The design expected the producer to apply `_bind_options` as it folds, on the reasoning that the
collapse folds the MODE (`opts.add(mode)`) but cannot recover the RELABEL POLICY (`Z,U`) once the
category is gone. The obligation is real; the code already discharges it one layer upstream.

`settings_launch._emit_bind` already resolves `options = opts if opts is not None else
_bind_options(category)` for every MOUNT entry, so a `CategoryEntry` arriving here ALREADY carries
concrete opts — `caches`/`common` carry `Z,U`, `bindings.ro` carries `ro`. `CategoryEntry.options`
is typed `str`, not `str | None`, so concreteness is a property of the type, not of this fold.

⇒ **the producer carries `entry.options` VERBATIM and must not re-derive it.** Re-deriving with
`entry.options or bind_options(...)` would be a behaviour change, not a no-op: a deliberate
empty-options bind (`[src, ""]`, reachable through `unpack_bind_entry`) means *no mount options* and
would be silently upgraded to `Z,U`.

COPY entries (`seeded`, `synced`) carry `options == ""` — `_emit_bind` discards a per-entry opts
override for a copy. That is upstream behaviour, carried verbatim here, not a decision of this
module.

## The arms — and why the two COPY arms are NOT shaped like the three MOUNT arms

⚑⚑ **A dest is a KEY on a mount and DATA on a copy, and that split IS the arm types.** A mount
arbitrates at a destination — two mounts at one dest is an error in every scope combination — so
dest-keying a mount arm makes the rule true by construction. A copy arbitrates at nothing: spec
`:147-149` calls `meta.assembly.seeded`/`.synced` *"both flat scope-ordered lists"* and rules that
*"nothing is arbitrated at a destination"*, and the manifest's `meta.assembly.seeded` row spells it
*"the CONCATENATION of every scope's `seeded` arm ... nothing is pruned, and a dest **MAY
repeat**"*. A dict cannot express a list with repeats, so it silently
kept the LAST row.

* `ro` · `rw` are `BindMap` = `dict[dest -> BindEntry(src, opts)]`, matching the collapse's unpack
  `for dest_path, (src_path, opts) in ....items()`.
* `seed` · `sync` are `CopyList` = `list[CopyRow(dest, entry)]`, in DECLARATION order, duplicates
  kept. The collapse iterates them directly and concatenates scope by scope.
  * ⚑ **MEASURED 2026-08-11, and it is the honest scope of the repair:** the live emitter cannot
    yet hand this arm two rows at one dest in one scope — the store LEAF is itself dest-keyed, so
    `~/x` and `/home/agent/x` merge inside `build_launch_snapshot`, and `agent.default` vs
    `agent.<active>` resolve through the cascade before an entry exists. The repair is therefore a
    REPRESENTATION fix, not a live-loss fix. It still matters: nothing else in the chain prunes a
    copy for sharing a destination (the collapse's sync arm is a plain scope-ordered concatenation),
    so the dict arm was the one place where a declared copy could vanish with no warning at any log
    level, and it chose the survivor by raw dest SPELLING rather than by the user's file order.
  * ⚑ **Do not "tidy" the two arms back into a `BindMap` for symmetry with the mounts.** The
    asymmetry is the ruling; symmetry here is the bug.
* `mask` is dest-keyed and its **VALUE IS NEVER READ**: the collapse touches `shape.mask` in exactly
  two places and both iterate KEYS. The `(None, None)` sentinel it writes goes into its OUTPUT map,
  not back into this arm.
  * The value is `True`, mirroring the store leaf (`dict[dest -> bool]`, present = masked, with
    present-`None` unmasks already dropped at build). `None` was rejected as the carrier precisely
    because `None` MEANS UNMASK in the store shape.
  * ⚑ **Do not "tidy" this arm into a set, a list or a `BindMap`.** The mask VALUE reshape is a
    separate, parked roadmap step and is not this module's to settle; when it lands the change is
    confined to this one field.
* ⚑ **`BindEntry.src` is NOT widened to `str | None` to give a mask an entry form.** That would
  relax the storage type for every consumer to serve one arm that never reads the field.

## Every scope always has a shape

The collapse indexes `store_shape_set[scope]` for all four scopes unconditionally, so the producer
emits all four — an empty `StoreShape` where a scope declared nothing. A missing scope would be a
`KeyError` inside his loop; making it unrepresentable is cheaper than guarding it there.

## Categories with NO arm

`_ARM` and `_NO_ARM` together must cover every declared category (`settings_categories._DELIVERY`),
disjointly — a test asserts it, so adding a category to the keyspace without deciding its arm fails
loudly rather than dropping its entries.

* **`env`** is not a path delivery at all (its "dest" is a VAR name); it has no place in a
  disk-store shape.
* **`secret_path`** is PARKED. It is a CONCRETE mount whose dest is `/run/kanibako/secrets/{VAR}` by
  construction; the five-key shape is ratified without it and reshaping it is not this step's.
  ⚑ Consequence worth knowing: because it reaches no arm, the producer does not arbitrate it either,
  so a `bindings.rw[/run/kanibako/secrets/TOK]` + `secret_path.TOK` pair is not refused here. It IS
  refused on the live path, by `settings_categories.secret_path_deliveries` at the launch seam, which
  since 6-R3 is the only layer holding both a secret and everything contending for its dest.

## What the producer must NOT do

1. **Not arbitrate across scopes.** A `StoreShapeSet` must keep all four scopes' entries intact and
   separate, because the collapse re-derives the cross-scope answer itself. ⚑ This is why the
   producer consumes the ENTRY LIST and not a pre-resolved winner set: the retired
   `reconcile_categories` returned ONE winner per dest across all four scopes, which had already
   thrown away the per-scope structure the collapse needs.
2. **Not apply the whole table per scope.** It looks like the free win — the retired
   `_resolve_dest_group` implemented all five rows — and **the failure is silent.** It also
   implemented row 2, and a mask and a binding can perfectly well share ONE scope: applied per scope
   the mask would EAT the
   binding inside the producer, and `shape.rw` would arrive at the collapse missing the entry the
   collapse's own mask loop is written to override. The collapse would be correct and the answer
   still wrong.
3. **Not carry the copy-vs-mount ARBITRATION LADDER forward** ("masks beats everything … every mount
   beats `seeded`"). ⚑ The collapse does not supersede that ladder with scope order — **it does not
   arbitrate copies AT ALL.** Copies apply to the home bind alone, the copy half runs before any binding
   is read, and its output is a concatenation whose only ordering is scope order. Reproducing the ladder
   here would re-create, one layer earlier, the thing the cutover deletes.
4. **Not sort by path depth.** Depth ordering is EMISSION order, not precedence. Arms preserve input
   order; the collapse sorts each scope's binds shallowest-first before processing, which is its own
   ruling and its own intra-scope mechanism. ⚑ For the COPY arms "preserve input order" is now
   load-bearing rather than incidental, which is why the fold walks `_scope_survivors` — the §0 rows
   are decided per DEST, but the survivors come back in DECLARATION order. Grouping the emission by
   dest would pull two copies at one dest together and land a copy at a NESTING dest after both,
   inverting which one overwrites which at apply time.
5. **Not widen past five keys.** `StoreShape` is a frozen five-field structure so "exactly five" is
   true BY CONSTRUCTION rather than by convention. The collapse reads `shape.ro` / `shape.seed` /
   `shape.mask` — attribute access — so the structure satisfies both readings of "a dict with
   exactly `[ro, rw, mask, seed, sync]`".
6. **Not emit home.** Home is pid 0: it enters the collapse as its own `home_bind` parameter and is
   in no scope's `store_shape`.

## Two rules that are same-scope-decidable and are NOT implemented here

Recorded so they are not discovered during step 6.

* **`seeded` onto a binding.** Spec §0 row 3 names `seeded` among the ABSTRACT declarations, but no
  code has ever refused it: `seeded` is a COPY, so it never reaches a MOUNT group where row 3 lives.
  (The retired `reconcile_categories` resolved it through its cross-delivery ladder instead — the
  mount won, silently — and that ladder went with it at 6-R3.) ⚑ The collapse does not rule the case
  at all — a copy never meets a bind
  there, so nothing is refused and nothing is removed. A mount shadowing a copied file is a DELIVERY
  fact, and it is legal ("it's ok for a mount to shadow a seeded file"). Refusing it here would have
  invented an error that neither ships today nor is wanted downstream, so the producer folds `seeded`
  without arbitrating it against a bind. **The spec text and the two implementations disagree; that is
  a spec question, not a writer's.**
* **`synced` vs a binding at one dest.** ⚖️ **RULED 2026-08-12 — NOBODY rules it, because it is not
  a collision** (*"don't check for sync. Let it clobber whatever it wants."*). The producer never
  implemented it, so a same-scope pair survives into `sync` and `rw` — and that is now simply the
  right answer rather than a gap another stage closed. `_resolve_dest_group`'s
  `synced_vs_binding` `CategoryCollisionError` went at cutover 5-1b (the kind no longer exists) and
  `store_collapse._refuse_sync_at_a_bind_dest` went with the ruling. A sync resolves THROUGH the
  bind covering its dest into that bind's host source, at its exact point as much as inside it, so
  it overwrites CONTENT and the mount stands. 🔴 The spec's §0 sentence still says ERROR; that edit
  is owed.

## Warnings are DATA

Row 5 returns `CategoryCollision` on `StoreShapeSet.warnings`, and the ONE emission seam
(`commands.start.emit_collision_warnings`) renders them. The producer stays PURE.

⚑ **It is the ONLY channel.** A second one existed on the retired `ReconciledCategories.warnings`;
5-1c deleted that feed and the field, and 6-R3 deleted the class. One ambiguity, one builder, one
path to the user — re-adding a second means re-adding a type, which is a visible design act.

## Message single-sourcing

The row-1 and row-3 refusals are raised through `settings_categories.raise_binding_vs_binding` and
`raise_extension_onto_occupied`, which were extracted from the retired cross-scope pass verbatim and
made public for this second caller. They have THREE callers now — this producer, plus
`secret_path_deliveries` and `narrow_table_winners` at the launch seam. The message text is
spec-mandated (it must point at SUPPRESS-THEN-ADD and name the extending entry, the occupant and the
dest), so there is ONE copy of it and no second remedy text.

⚑ The row-1 message's "until 1.8.0 the more specific scope won" paragraph reads slightly oddly for a
SAME-scope pair, where there is no more-specific scope. That is deliberate: every caller raises the
identical message, which keeps them byte-identical rather than forking the wording per caller.

## Destinations are DATA

⚑⚑ A box destination is DATA and is **never** `.split(".")`/rejoined. Dests routinely contain dots
(`~/.cache/uv`). `CategoryEntry.key_segments` carries segments; `.key` is derived for display and
matching only. One root cause here produced four bugs, one of which silently deleted a declaration
on every real launch.
