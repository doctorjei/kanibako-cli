# `commands/start.py` — overflow prose

⚑ **PARTIAL BY DESIGN.** This file is written OPPORTUNISTICALLY, as `start.py` is touched. It does
not describe the module as a whole; each section names the seam it covers and nothing else.

---

## `_launch_bind_map` / `_bind_map_from_mounts` — the emitter consumes the SHAPE (cutover 2a-2)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §2a-2-SHAPE (decided before dispatch) · §2.0e (the
narrow resolves have no collapsed node) · §2.0g (the four fields the collapsed shape drops).

### What the switch is

The MAIN launch path now emits its category mounts from `meta.assembly.bindings` — the collapse's
dest-keyed `CollapsedBind(src, opts)` map. At 2a-2 the second, cross-scope `reconcile_categories`
pass still ran beside it and computed its whole answer; nothing was deleted at that step. What moved
was WHERE the main path's rows come from. ⚑ **Cutover 6-R3 then deleted that pass outright** — see
"`LaunchDeliveries`" below.

### Why the emitter takes a map and not a winner LIST

The two narrow resolves (images, helper hub) run with `include_base_families=False`, so they carry no
home bind, so the collapse writes them no node at all — pointing the shared emitter at
`meta.assembly.*` from the inside would empty their mounts silently. Making the emitter accept EITHER
shape is two forms with one meaning (Convention 0), and adapting a collapsed map back into
`CategoryEntry`s is impossible rather than merely ugly: `category`, `scope`, `name` and `optional` are
gone by construction. So the emitter takes the SHAPE — dest → `(src, opts)`, plus its dest-keyed
policies — and each caller supplies that map from wherever it has one. `_bind_map_from_mounts` is the
translation for a caller whose answer is a MOUNT winner list — today `_narrow_bind_map`, over
`narrow_table_winners`' answer. It is not a collapse (no home foundation, no scope fold).

### What became of the four dropped fields

* **`optional`** — already a parameter since step 3 (`skip_if_absent`, dest-spelled).
* **`category`** — the mask arm needs no category: a mask IS `CollapsedBind(None, None)`, so "no
  source" is the test, and it is structural rather than a lookup. `secret_path` carries no arm in the
  disk-store shape at all, so it is simply not in the map (`_bind_map_from_mounts` drops it for the
  same reason — shape fidelity, not a policy filter).
* **`scope`** — was doing exactly one job, PARTITIONING: two emitters walked one list, so each had to
  filter to its own half or every agent bind was emitted twice. **2a-3 merged them and the partition
  problem stopped existing** (P4: the representation deletes the code that enforced the rule), so
  `delivered_elsewhere` is gone. What the agent binds actually needed was never the scope — it was a
  per-dest MISSING-SOURCE POLICY, and that was already dest-keyed and already a parameter: the
  descriptor's `BindScope.AGENT_CRITICAL` dests. A narrow resolve selects its own rows before handing
  its map over (`_narrow_bind_map`) — since 6-R2 by its own table's DESTS, which subsumes the agent
  drop it used to spell separately — but that is a caller choosing what it emits, not the emitter
  branching on a field.
* **`name`** — the WARNING text named it. Under dest-keying `CategoryEntry.name` IS the destination
  (R-10), so the dest carries the identity; the only change a user can see is that the message now
  spells the dest normalized (`/home/agent/canon`) rather than as declared (`~/canon`).

### The three missing-source policies, and why their ORDER is load-bearing

One emitter, one walk, three per-dest answers to "the host source is not there":

| policy | parameter | who is in it | what happens |
|---|---|---|---|
| must-exist | `must_exist` | the descriptor's `AGENT_CRITICAL` dests | raise `BindingSourceError` → clean exit-1 |
| skip-if-absent | `skip_if_absent` | the `optional` canon chapters + the agent's best-effort dests | dropped at `debug` |
| warn-and-drop | *(default)* | everything else | ro warns and drops; rw guarantee-creates |

⚑⚑ **The policy is consulted BEFORE the rw guarantee-create, and that ordering is the whole point.**
`mkdir(parents=True, exist_ok=True)` would manufacture the very thing must-exist is asking about, so a
critical dest whose source vanished would get an empty directory bound over the agent's binary instead
of the safe-fail. Reading the policy first makes "mkdir'd into existence" unreachable rather than
merely discouraged (P3). Pinned by `test_a_missing_critical_source_RAISES_before_any_mkdir`.

⚑ The agent's best-effort dests join **skip-if-absent** rather than keeping a branch of their own: a
missing or suppressed agent share is fine (`BindScope.AGENT`), which is skip-if-absent behaviour up to
the log line. The critical dests are subtracted from that set — must-exist wins its own dests outright,
and a dest in both would otherwise resolve two ways depending on which test ran first.

### Emission owns the depth-sort now

A dest-keyed map carries no order — `store_collapse` says so at the type. The reconcile used to hand
the emitter an already depth-sorted list, so the emitter sorts on the same key
(`settings_categories.path_depth`, made public for this second consumer rather than re-spelled) and
podman still receives shallow-first, deepest-wins.

### The FALLBACK — DEAD AT CUTOVER 2c

`_launch_bind_map` read the leaf and fell back to the reconciled rows when it was ABSENT. That was not
a preference between routes: the collapse refuses configurations the live route accepts and left all
three leaves unwritten, so until step 2c a refusal had to reach nobody. **The fallback and the
`SettingsError` swallow in `_install_assembly_collapse` came out together, at 2c**, and the three
sibling arms (`_launch_seed_list`'s, `_launch_synced_list`'s, and the `category == "synced"` filter
that only the synced fallback needed) went with them.

What replaced each is a NAMED `SettingsError` rather than a `None`: the readers still return an
option, and each consumer refuses it. That is a WIRING invariant, not a config diagnostic — a
whole-box resolve refuses at the fold (below) long before a consumer could see an absent leaf, so the
only way to reach these is to hand a consumer a narrow snapshot. Stating it is what keeps the failure
a `KanibakoError` instead of an `AttributeError` on `None.items()` inside the emitter.

⚑ **The step-2c precondition, measured and then CLOSED:** `start_mocks` stubbed
`_resolve_launch_snapshot` with a category set carrying no home bind, and — the half that a reading of
the category set alone misses — the stub never called `_install_assembly_collapse`, which lives in the
orchestrator it REPLACES. Either half alone leaves all three leaves absent, so every `_run_container`
unit test took the fallback and deleting the fallback would have emptied the category mount set for
that whole suite at once. The fixture now carries the core home row AND mirrors the orchestrator's
tail (gate → collapse → carrier, off the same gated list), so those launches read a real
`meta.assembly.bindings`. **Measured delta on the emitted mount set: `+ /home/agent` and nothing
else** — home is lifted out before any scope folds, so its options stay `Z,U`, and the agent delivery
binds fold `fold_opt("ro", "ro") == "ro"`, byte-identical to the fallback.

### One measured behavioural difference, pinned rather than smoothed

The five-arm shape carries ro/rw as the ARM, so the collapse folds the mode back into the option
string: a rw DECLARATION whose own options read `Z,U` arrives as `Z,U,rw`. Podman's default IS rw and
`fold_opt` dedupes, so `ro` stays `ro` and nothing about the box changes — but the option string
podman receives does. Home is the exception by construction: pid 0 is SEEDED before any scope
folds and is in no scope's shape, so no arm ever appends to its options.

---

## `_install_assembly_collapse` / `_snapshot_home` — the collapse wiring (roadmap step 6b)

**Authority:** Jei's roadmap step 6, verbatim — *"implement a 'grand unification function' … that
will **merge the information, but not perform the action**"* ·
`designs/collapse-implementation-DESIGN.md` §0/§1 · `designs/grand-unification-collapse-DESIGN.md`
§2a (home is pid 0).

### What it is

`_resolve_launch_snapshot` folds the same `CategoryEntry` list the live route already produced
(`snapshot_category_entries`) through the step-4 producer (`build_store_shape_set`) and the step-6a
collapse (`collapse_store_shapes`), and stores the results at the declared RO/derived keys
`meta.assembly.{bindings,seeded,synced}` — plus `meta.assembly.env`, which comes from
`collapse_env` off the ENTRY LIST rather than the shapes, and which `_launch_env_map` reads.

### What it drives, and what it still does not

🛑 **UPDATED AT CUTOVER 2a-2, AGAIN AT 2b-2, AND AGAIN AT 2b-3 — this section used to read "it drives
nothing", and that is now false for MOUNTS and for BOTH copy arms.** The main launch path emits its
category mounts from `meta.assembly.bindings` (see the section above), the create-time seed applier
reads `meta.assembly.seeded`, and the launch-time sync applier reads `meta.assembly.synced` (both
below). ⚑ **AND AGAIN AT 6-R2/6-R3: THE SECOND ROUTE IS GONE.** The `secret_path` mounts, the
agent-delivery dest set and both narrow bind maps read the `LaunchDeliveries` carrier (section
below), and 6-R3 DELETED `reconcile_categories`, `ReconciledCategories` and the three group
resolvers under them. ⚑ Its WARN half went first, at 5-1c — next section. ⚑ **AND AGAIN WITH THE ENV
LEAF: it drives the box's ENVIRONMENT too.** `meta.assembly.env` is read by `_launch_env_map` and
projected by `_build_config_env`, for the launch and for `box show --effective` alike; the carrier's
`envs` list is retired, so nothing assembles an environment from raw declarations any more — and
since MBR-1 P3 nothing rides in beside the leaf either (the agent under-layer is gone).

That is also why the wiring reuses the existing walk rather than adding a second one: two walks
could disagree about what was declared, and only one of them would be the one that ships.

### The row-5 warning lives HERE, and ONLY here (cutover 5-0, then 5-1c)

🛑 **UPDATED TWICE. 5-0:** `build_store_shape_set` computed the same same-scope ambiguities all
along and `StoreShapeSet` carried them as data, but nothing ever asked for `.warnings` — the ONLY
emission in `src/` was `emit_collision_warnings(reconciled.warnings)`. 5-0 handed `shapes.warnings`
to that same seam as well, so both feeds were live and reverting was one line.
🛑 **5-1c THEN DELETED THE RECONCILE FEED — and the `ReconciledCategories.warnings` field, and the
row-5 `CategoryCollision` construction in `_resolve_mount_group` that filled it.** The producer is
now the SOLE builder of a `CategoryCollision` anywhere in `src/`, and there is no second feed left
to add without re-adding a field.

**Why the field went with the feed, rather than being left empty.** An always-empty field is a false
claim in the type, and — more to the point — a re-pluggable socket. Two feeds printed one line only
because both arms happened to build an EQUAL `CategoryCollision` and `emit_collision_warnings`
memoises on `(box_dest, scope)`; that was a property of the two CONSTRUCTIONS, never of the channel.
Making the second feed **unavailable** (P3) is what closes it. 🔬 **Mutation-proved:** the full
revert — construction + field + feed, all three — leaves every LOG-based test in
`TestTheCollapseRouteFeedsTheSameChannel` **green**, because the memo hides it. Only the structural
case `test_there_is_NO_SECOND_FEED_left_to_add` goes red. That is exactly why the guarantee is
pinned structurally and not by counting log lines.

**No warning was lost. Measured, not argued.** Sweeping 243,300 two- and three-entry arrangements
(7 categories × 5 scopes × 2 dests) through the PRE-IMAGE reconcile and the live producer and
comparing the warned `(box_dest, scope)` sets: **0 arrangements warned only by the reconcile**;
8,320 warned by both; 1,464 warned only by the producer. The producer's set is a strict SUPERSET —
it folds each scope alone, so it applies none of the reconcile's silences:

| what silenced the reconcile | what the collapse arm says | what the launch does |
|---|---|---|
| a `masks` entry at that dest, in ANY scope (§0 row 2 returned the mask and no warnings) | warns for the ambiguous scope | **works** — one extra line the user did not get before v1.8.0 (CHANGELOG, Unreleased/Changed) |
| another scope's abstraction took the dest (its old row 4) | warns for the LOSING scope, a scope the reconcile never named | refused by `collapse_store_shapes` — two mounts at one dest — so the line only ever precedes that refusal |

The mask row is the one that changes what a working launch prints, and it is the reason this is a
CHANGELOG entry at all; it is pinned by
`tests/test_category_collisions.py::TestTheCollapseRouteFeedsTheSameChannel::test_a_MASKED_destinations_ambiguity_is_announced_where_it_once_was_not`.
⚑ The second row is MEASURED, not pinned — no test drives a warning that precedes a refusal.

⚑ **The old `_split_home_bind` question is MOOT since cutover 6-H.** It used to be worth asking
whether a same-scope ambiguity AT the home dest could reach the reconcile and not the producer, since
the producer saw the entry list minus the one lifted home mount. There is no lift any more: home left
`bindings.rw` entirely and the producer sees the WHOLE gated list. Anything declared at the home dest
is an ordinary entry to the producer — it warns on an ambiguity there exactly as it would anywhere
else — and the collapse then refuses the survivor against the seeded foundation.

⚑ **Whether the channel should exist AT ALL is still a spec question, and still open.** §0's
containment table makes two mounts at one destination "an error in EVERY scope combination",
same-scope included; warn-and-proceed is the retired five-row table's row 5. 5-0 gave that behaviour
a home in the new route and 5-1c gave it exactly one; neither rules on whether it survives.

### The credential gate is HOISTED above EVERY consumer (cutover 2b-0)

The retired `reconcile_categories` applied the D-M4 credential gate INTERNALLY, so before 2b-0 a
PRIVATE box (`deliver_creds=False`) got an empty copy set on that route while
`_install_assembly_collapse` — handed the RAW entry list two lines later — folded the credential rows
into `meta.assembly.synced` anyway. The
divergence was inert only because nothing consumed that leaf. Pointing a consumer at it would have
delivered every `synced` credential into a box the user made private, reversing D-M4.

`_resolve_launch_snapshot` therefore calls `settings_categories.gate_credential_delivery` ONCE and
hands the SAME gated list to the collapse and (since 6-R1) to `launch_deliveries` — the two consumers
that remain after 6-R3.
🛑 **The second copy of the gate, which sat INSIDE the retired pass, is GONE — deleted at cutover 4
(`42f5291`), not deferred.** This paragraph said "it stays … removing it is step 5" and was stale
from that commit onward; the hoisted call above is now the rule's ONLY spelling, on any path.

⚑ The gate runs AFTER `_install_derived_bindings`, not before. A derived binding materialises a
property of the DECLARATION (R-8) — `binding_derivations` records what was declared, not what this
box is allowed to receive.

⚑ The `seeded` half of the gate is LATENT: `CategoryEntry.is_credential` has no production producer
today (only tests set it), so on a real launch the gate drops `synced` rows and nothing else. Both
halves are gated regardless — a gate that covers one of two arms is the shape that produces this
class of bug in the first place.

### Home is pid 0, so the SEAM CONSTRUCTS it (cutover 6-H)

🛑 **REWRITTEN AT 6-H. This section used to describe LIFTING home out of the entry list
(`_split_home_bind`, `_home_bind_entries`, `_refuse_without_one_home`). All three are DELETED, and
the model they implemented is retired: home never enters the entry list at all now.**

`collapse_store_shapes` seeds `combined_bindings` with home BEFORE any scope folds, and takes it as
its own parameter. Until 6-H, home was ALSO a `bindings.rw` row in `data/core-defaults.yaml`, so the
same fact arrived twice and the seam had to pull one copy back out before the producer could fold
the rest. Deleting the row removes the duplication at its source: home does **not** route through
`bindings.rw` (spec `:1015`, amendment 2026-08-08a / A9), and `_install_assembly_collapse` builds the
foundation itself —

```python
home_bind = BindEntry(_snapshot_home(snapshot), _HOME_OPTIONS)
```

— from the RO DERIVED key `meta.box.home`, which `settings_launch.workset_anchor_floor` materialises
as `@meta.box.path/home`. 🛑 **That key is the ONE spelling of the derivation.** Never re-derive the
foundation from `proj.shell_path`, and never re-inline `@meta.box.path/home` here; `settings_launch`
says so in terms at the floor line, and A9 existed precisely to remove the second spelling.

`_HOME_OPTIONS` is a BARE `Z,U` — no `rw` token. The foundation is pre-seeded into the collapse map
and never passes `_merge_bindings`' `fold_opt`, which is exactly what distinguishes it from every
rw bind that folds over it (`Z,U,rw`). Mount options are SEAM MACHINERY, not part of any key — which
is why the literal lives beside the seam and not in the keyspace.

**Neither of the two failures the deleted guard covered survives as a reachable state.** ZERO homes
is unconstructible: the key is floor-produced on every resolve and
`settings_launch._assert_box_root_resolved` refuses a box root that did not resolve. TWO is the
COLLAPSE's `_refuse_bind_over_bind`, fired against the foundation seeded beneath every scope's shape
— which is where §0 rule 2 puts it, and the only layer holding a foundation to compare against.
`_snapshot_home` therefore carries no one-home rule; what it does carry is the TYPE the snapshot
cannot (a resolved leaf is `object`, and a non-string one would otherwise reach podman as a source).

### …but the SEED leaf is not gated on a home bind (cutover 2b-1)

🛑 **UPDATED AT 2b-1 — the paragraph above used to read "no write at all", and that gated three
leaves on a fact belonging to two.** `collapse_seeded` takes the shape set and NOTHING else; only
`_collapse_mounts` ever wanted the home bind. Home is pid 0, seeded BEFORE any bind folds (§2a), so
a seed list is computable where no bind map is.

This is a precondition, not a tidy-up. `seed_new_box` — the `box create` entry — reaches
`_seed_box_home` → `_apply_init_seeds` without ever running a main resolve, and that resolve is
NARROW (`include_base_families=False`, target seeds + template layers injected, no box home). Under
the old gate its `meta.assembly.seeded` was always absent, so consumer 5 could not have been pointed
at the leaf at all: the create path would have read `None` on every box.

So `_install_assembly_collapse` gates each leaf on its OWN facts:

| leaf | written when |
|------|--------------|
| `seeded` | the seed arm folds |
| `bindings`, `synced` | the seed arm folds AND this is a WHOLE-BOX resolve AND the bind fold does not refuse |

🛑 **THE GATE IS `whole_box`, NEVER "did `meta.box.home` resolve" (cutover 6-H).** The key is
materialised by `workset_anchor_floor`, which the launch builds unconditionally, so it resolves on a
NARROW resolve too — gating on the value would write all three leaves for the image and helper
tables. It also closes what the old entry-list gate left open: a USER row at the home dest made
`home_bind is not None` on a narrow resolve and ran the whole fold over a narrow snapshot.

⚑ **ABSENT and EMPTY are different answers.** The seed leaf is written even when the list is empty,
so a consumer reading `None` learns the collapse REFUSED — never that this box seeds nothing.

⚑ **A BIND-FOLD refusal does not erase the seed list.** The refusal says nothing could assemble this
box; it says nothing about an arm the refused bind never touched, and the seed list was already
folded successfully when it fired. Erasing it would make a subsuming bind silently cost a box its
seeds the moment the create path reads this leaf — the same class of latent hazard 2b-0 closed on
the credential side. A SEED-ARM refusal (a seed outside home) is different: that leaf did not fold,
so nothing is written at all.

⚑ The `SettingsError` swallow is GONE (cutover 2c). It was a `debug` log that never failed a launch;
2b-1 narrowed its blast radius to the leaves each raise actually invalidates, and 2c removed it
outright. A fold that refuses now raises out of `_resolve_launch_snapshot`.

⚑ `collapse_store_shapes` recomputes the seed list in the home branch and the result is discarded.
That is deliberate: it is the same pure concatenation over the same shapes, so it cannot differ, and
one implementation of the seed rule is worth more than one saved traversal.

⚑ The home bind row in `data/core-defaults.yaml` is **DELETED** (cutover 6-H) — see the section
above. `core_defaults.add_bind`'s "home arm", which older drafts of this file and the manifest both
named as a second follow-up, was a PHANTOM: the function has been generic since its introducing
commit and the home behaviour lived in the YAML row alone, so deleting the row discharged both.

### A collapse refusal IS the launch's, as of cutover 2c

🛑 **THIS SECTION USED TO READ "A collapse refusal MUST NOT fail a launch". THAT WAS TRUE UNTIL 2c
AND IS THE OPPOSITE OF THE RULE NOW.** Kept, inverted in place, because the reasoning for the old
state is what explains the new one.

The collapse enforces refusals the shipped route does not: a bind may not subsume a bind, nor sit
inside a mask, a mask may not take another mask's point nor land at or above home, a seed may not
land outside home, a sync may not take a bind's exact point, and a bind's options may not contradict
its arm. The retired cross-scope pass permitted nested binds — it depth-sorted them and errored only
on two concrete declarations at one IDENTICAL dest — so **configurations exist that used to launch
fine and make the collapse raise.** That is the tightening, and it is what CHANGELOG + MIGRATION
§2.31 owe.

🐞 **AND 6-R3 CHANGED WHICH MESSAGE A CROSS-SCOPE PAIR GETS. MEASURED, not inferred.** Two binds at
ONE identical dest in two scopes used to hit the reconcile FIRST, so the user got
`raise_binding_vs_binding`'s row-1 text. With that pass deleted the same configuration reaches
`store_collapse._refuse_bind_over_bind` instead. **The configuration still refuses, and the REMEDY
SENTENCE is word-for-word identical** — measured by rendering both: *"To change what occupies a
destination you must SUPPRESS the entry you do not want … Set the unwanted key to null in the
settings file for its scope (a file may write its own scope and the scopes it contains)"*. What the
user LOSES is the surrounding diagnostic: the collapse's message names host SOURCES and the dest but
**no declaration KEY**, carries no *"THIS RULE CHANGED IN kanibako 1.8.0"* paragraph, and prints no
YAML block. ⚑ Naming the keys is **structurally impossible** where it stands —
`build_store_shape` drops `CategoryEntry.key_segments` when it writes the arm — so closing it is a
PRODUCER SHAPE change, boarded in that function's own docstring and out of 6-R3's scope. **Nothing
is owed against v1.7.2:** that release had no §0 table and no collapse, so both texts are 1.8.0-new
and neither has shipped.

Those refusals were always intended; enforcing them was premature while both routes ran. So until 2c
`SettingsError` out of the collapse was caught at this one seam, at `debug`, and every consumer fell
back. **2c removed the catch.** A refusal now propagates out of `_resolve_launch_snapshot` and exits
through `cli.main`'s `except KanibakoError` arm as `Error: …`, rc 1, no traceback — so no new
try/except is needed at the launch site, and `box show --effective` (which already catches
`KanibakoError`) stays safe as the "check before you hit it" path.

⚑ **The one refusal 2c ADDED was this seam's own, and 6-H RETIRED it.** 2c refused a whole-box
resolve with `len(at_home) != 1` by name (`_refuse_without_one_home`), because deleting the reconciled
fallback would otherwise have turned an invalid configuration into an `AttributeError` on
`None.items()` inside the emitter. 6-H removed the reachable states rather than the guarantee: a
whole-box resolve now ALWAYS writes `meta.assembly.bindings` or raises, because the gate is
`whole_box` itself and the foundation is constructed rather than found. Two binds at home is the
collapse's `_refuse_bind_over_bind`; zero is unconstructible. 🛑 The NARROW path keeps its early
return — it describes an injected table, not a box, and asks only for the seed arm — which is why the
gate is the resolve's own `include_base_families`, forwarded as `whole_box` rather than re-derived.

⚑ **A user-visible capability went with the row: a settings-file entry at `~` used to repoint the
box home and WIN through the cascade; it is now a second bind at pid 0's point and REFUSES.** The
cure is `workset.boxes`. CHANGELOG + MIGRATION §2.32, same commit.

⚑ A partial write is worse than no write, which is why the two leaves that DESCRIBE an assembly are
installed only after the fold returns — a half-built `meta.assembly.bindings` with no sync list
beside it would describe a box nothing could assemble. The SEED leaf is not part of that description
and rides its own gate (2b-1).

---

## `_emit_category_mounts` — the MISSING-SOURCE POLICY travels as a parameter (cutover step 3)

**Authority:** `designs/store-shape-producer-DESIGN.md` §9.1 · `plans/2026-08-09d-CUTOVER-PLAN.md`
§3 and §2.0g. **Precondition of 2a-2**, not a later step.

### What moved

The emitter used to read `CategoryEntry.optional` to decide skip-if-absent (spec §2c). It now takes
`skip_if_absent`, a set of normalized box DESTS, and tests `e.box_dest` against it. Nothing else
changed: an rw bind still guarantee-creates, and every other ro bind with a missing source still
warns and drops.

`optional` was never a realization fact — it is a DECLARATION fact, supplied by the `canon:` rows
and consumed only at emission. The collapse has no use for it: it changes no bind, no copy and no
precedence decision, so `CollapsedBind(src, opts)` drops it by construction. A guard lost by folding
MOVES; it never argues for a wider structure.

### Why the DEST is the carrier

The collapsed bind map is keyed by destination — by the time the emitter reads it the KEY is gone
and the dest is not. So the policy has to be dest-spelled to survive 2a-2, and it is built with
`normalize_bind_dest`, the same function `core_defaults.add_bind` keys the arm with.

⚑ **This is the failure `critical_keys` already paid for once.** The retired `agent_delivery_mounts`
tested `e.name in critical_keys` where `e.name` IS the destination (R-10), so a caller passing key
NAMES matched nothing and every critical bind silently degraded to best-effort — a missing agent
binary reaching podman as a crun crash instead of a clean exit-1. The set outlived that function as
`must_exist`, and so did the hazard. Same shape here: a key-spelled
`skip_if_absent` matches nothing and every chapter-less workset warns on every launch. The two
spellings are pinned against each other by
`test_canon_delivery.py::TestLaunchWiring::test_the_skip_set_matches_what_the_declaration_marks_optional`.

### One deliberate consequence: the policy is now scope-blind

`canon_optional_bind_keys()` is spelled `box.<category>.<dest>`, so the old flag only fired for a
BOX-scoped declaration at a chapter dest. The dest set fires for whichever scope wins that dest.
That is the intended end state — the collapse drops scope on purpose, and one dest can only carry
one bind — and it is unreachable in practice, since the box-scoped canon floor outranks any other
scope's declaration at the same dest.

### Shaped for its sibling

`skip_if_absent` is one of three missing-source policies §2.0g enumerates: **must-exist** (the
plugin descriptor's `AGENT_CRITICAL` dest set) · **skip-if-absent** (this one, which the agent's
best-effort dests joined at 2a-3) · **warn-and-drop** (the L7 default, and the reason both
parameters default EMPTY). ✅ **The must-exist set joined this signature at 2a-3, when the two
emitters merged into one** — see "The three missing-source policies" above for why the policy is
read BEFORE the rw guarantee-create.

### It is passed at every call site, including the narrow ones

The image and helper resolves run with `include_base_families=False`, so they carry no canon floor —
but they still read the user's cascade files, so a user-declared bind at a chapter dest reaches them
too. A policy that varied by call site would decide one destination two ways.

---

## `LaunchDeliveries` — the consumers leave the reconcile (6-R2), which is then DELETED (6-R3)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §6 "§6 DESIGN PASS" · producer `DESIGN` §7.4
(`secret_path` is PARKED out of the disk-store shape) · §9.1 (what is not a settings key is PASSED).

### What the switch is

6-R1 built the carrier and returned it beside the reconciled winners, consumed by nobody. 6-R2 moved
every consumer onto it:

| what | was | is |
|---|---|---|
| the container env | `_build_config_env(agent_env, reconciled.envs)` | ⚑ **moved on again — `_launch_env_map(snapshot)`**, the collapsed `meta.assembly.env` leaf, at the launch AND at `box show --effective`; the carrier's `envs` field is RETIRED, and MBR-1 P3 dropped the `agent_env` parameter with it |
| the secret mounts | `_emit_secret_mounts` filtering `reconciled.mounts` | `deliveries.secrets` |
| the agent-delivery dests | `_agent_delivered_dests(reconciled.mounts)` | `deliveries.agent_dests` |
| the narrow bind maps | `_narrow_bind_map(_img_rec.mounts)` | `deliveries.narrow_bindings` |

🛑 **6-R3 THEN DELETED THE ROUTE.** `_resolve_launch_snapshot` returns `(snapshot, deliveries)`;
`reconcile_categories`, `ReconciledCategories`, `_resolve_dest_group`, `_resolve_mount_group`,
`_resolve_copy_group` and the dead `_DISABLE_SENTINEL` are gone, and
`test_category_collisions.py::test_the_retired_routes_are_GONE` asserts their absence structurally.
§0's table is applied by three seams from here on — the per-scope `store_shape` producer (rows 3, 5,
row 1 SAME-scope), the assembly collapse (rows 2, 4, row 1 cross-scope), and this seam's own
`secret_path_deliveries` / `narrow_table_winners` for the inputs the collapse cannot see.

⚑ **6-R2's env flip was byte-identical by inspection, not by hope:** the retired pass did no env
arbitration — its env line WAS `[e for e in entries if e.delivery == ENV]`, the same filter
`launch_deliveries` spelled. The per-VAR winner was, right up to MBR-1, the consumer's `dict.update`.

### 🛑 THE ENV ROW MOVED OFF THIS CARRIER ENTIRELY (MBR-1 P2)

The `envs` field is gone, and it was RETIRED rather than left beside the leaf on purpose: an
un-arbitrated second view of the same declarations is exactly what let a variable named by two
scopes be settled silently by whichever entry the list happened to end with. Both consumers —
`_assemble_launch_env` and `box show --effective` — read `meta.assembly.env` through
`_launch_env_map` and fold it with the same `_build_config_env`, so ONE value per variable exists on
either surface and the winner's `(scope, key)` travels with it. **For a configuration the collapse
accepts this changed no value:** such a configuration has one scope's key per variable by
construction, so the map and the old list carried the same pairs. What changed is that the
arrangement where they would have DIFFERED does not launch at all.

### 🛑 AND THE AGENT UNDER-LAYER WENT WITH IT (MBR-1 P3)

`_build_config_env` took `(agent_env, env_slots)` and layered the first under the second. That first
argument was `AgentConfig.env` — the per-agent FILE's `self.env` table, handed in separately for one
reason: `settings_assemble._agent_partial` re-rooted the file's flat `secret_path` and not its `env`,
so those variables were on no cascade level at all. The consequences were both silent. Being an
under-layer put an agent-scope variable BELOW `system`, inverting the bracket in which the agent tier
outranks system; and being off the snapshot meant it never met the expand pass, so a `~` or `$VAR`
resolved or did not depending purely on WHICH FILE the value was written in.

The re-root closes both, and it deletes the code rather than adding a rule: `agent.<node>.env.<VAR>`
is an ordinary key, it cascades to its true rung (above `system`, below `workset`), it realizes
through the same collapse as every other scope's, and `_build_config_env` is a straight projection of
the slots with nothing layered under it. Two behaviours arrive with that: an agent-FILE variable and
a `box.env.<VAR>` twin are now two scopes' keys at one slot and REFUSE the launch (ruling 2026-08-14),
while an agent-FILE variable and the plugin's declared default are the SAME key at two cascade levels
and simply cascade — the file wins, nothing refuses.

⚑ **The only feeder that argument ever had was the FILE.** The persona STORE's env has always ridden
the cascade (`settings_launch._persona_partial` splices it under `agent.<active>`), and every
`generate_agent_config()` returns an env-less config under the file-purity invariant — which is why
the parameter was retired outright rather than kept for a second caller.

⚑ `_launch_env_map` is ONE function where `bindings`/`seeded`/`synced` each have two (an option
reader plus a total one). Neither env consumer can act on an absent leaf — both describe a box, and
the leaf rides the whole-box gate — so an option form would be a route nothing takes.

### ⚑⚑ The pref-origin enrichment moved with the raises, and that is REAL UX

`_annotate_pref_origin` names the `pref` that installed a colliding key the user never wrote. Its
`try` used to wrap the reconcile call, which raised FIRST on the live path — so its annotated message
is the one users actually saw. Deleting the reconcile spread the surviving raises across three
callees: the producer's rows 1/3 and the collapse's refusals (both inside
`_install_assembly_collapse`), and the secret gate and narrow-table pass (both inside
`launch_deliveries`). **The wrap therefore covers the BLOCK, not one call**, and it catches BOTH
`CategoryCollisionError` (the producer and the seam) AND `SettingsError` (the collapse's own
refusals, which are not the §0 table's two texts) — narrowing it to either one silently downgrades a
class of message. `_annotate_pref_origin` returns the exception UNCHANGED when no request accounts
for a participant, so the common path is byte-identical.

⚑ **Pinned END TO END**, not on the function:
`test_category_collisions.py::TestThePrefOriginReachesTheLIVEPATH` drives a real resolve with a
`pref.agent.claude.common` in a box settings file and asserts the error names the request. **Mutation
proved:** removing the wrap reddens exactly that case; the twelve pure-function cases beside it all
stay green, which is why it had to exist.

### The narrow resolves emit their OWN table's dests, and nothing else

A narrow resolve carries one injected table but resolves the user's WHOLE cascade, so every user
declaration reaches it. Emitting them was the **D1 defect**, and it is not new: at `v1.7.2`
`_emit_category_mounts(_img_rec, label="images")` had no dest filter at all, so a user
`box.bindings.*` row was emitted a SECOND time on any helpers-enabled or image-sharing launch — and
emitted from RAW rows, so a later-scope `masks` sweep the collapse had applied was defeated.

`core_defaults.helper_bind_dests()` / `image_bind_dests()` read the dests from the SAME
`core-defaults.yaml` rows that declare the binds (the `canon_optional_bind_dests` pattern), and
`settings_categories.narrow_table_winners` filters to them. **That DELETES the exposure rather than
arbitrating it (P4):** a user declaration cannot collide inside a narrow resolve unless it names an
internal dest outright.

At a dest that IS the table's, §0 still has to decide, and a narrow resolve has nobody else to ask —
the per-scope producer already raised the SAME-scope pair (`build_store_shape_set` runs ABOVE the
collapse's `whole_box` gate, so it runs on narrow resolves too), and the CROSS-scope pair is
`collapse_store_shapes`', which returns early here. So `narrow_table_winners` applies rows 1/3
(refuse, through the two surviving public raisers) and row 2 (a `masks` OVERRIDES — it is the inverse
of a bind, not a second one). A bare dest-filter would keep both rows and let a dest-keyed map settle
them by INSERTION ORDER, which is the `7b64217` shape and the plan's own storage.conf
counter-example.

### The cross-category gate for a SECRET dest moved to the seam

`secret_path` carries no arm in the disk-store shape, so the COLLAPSE never sees a secret and cannot
answer "does anything ELSE contend for this destination". The only answer was inside the retired
cross-scope pass. `settings_categories.secret_path_deliveries` composes the per-VAR pick with that
answer, over the same entry list, in the same order:

* a `bindings.*` row (or an abstraction deriving one) at `SECRET_MOUNT_DIR/<VAR>` **refuses**, through
  the same raisers, **naming both declarations**;
* a `masks` at the dest takes it and the VAR is simply not delivered — silently, as it has been since
  the flat authority ladder put `masks` on top.

🔬 **Both outcomes MEASURED on the live seam at 6-R2 before the move**, and both are preserved. It
sits at the SEAM rather than in `_emit_secret_mounts` for one concrete reason: the collapsed bind map
has lost the declaring KEY, so a gate reading it could only have named ONE participant of a
two-participant collision — a worse message than the one it replaces.

⚑ **EXACT DEST ONLY, and that is not a narrowing:** a bind or a mask over the secrets DIRECTORY never
contended with `SECRET_MOUNT_DIR/<VAR>`; the secret mounts inside it, deeper in the depth-sort.
Measured both ways.

---

## `_bind_map_masks` — the mask arm comes off the SAME map (cutover 2a-4)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §2.7 · collapse `DESIGN` §8.1a (a mask is a void).

### What moved

The launch's `tmpfs_masks` list was built from the RECONCILED rows
(`[e.box_dest for e in reconciled.mounts if e.category == "masks"]`) while the bind mounts beside it
already came from `meta.assembly.bindings`. Two arms of ONE delivery, off two different sources —
the last half of the 2a divergence. The map already carries masks (a mask IS `CollapsedBind(None,
None)`), so the arm is now the `is_mask` half of the same value: `launch_binds` is read ONCE at the
assembly seam, `_emit_category_mounts` takes the binds and `_bind_map_masks` takes the masks.

### Why one VALUE and not merely one function

`_launch_bind_map` had a second arm when this landed — it fell back to the reconciled rows while the
leaf was absent — so calling it twice was not the same as calling it once: nothing guaranteed two
reads answered from the same arm, and the failure would have been silent and per-launch. 2c deleted
that arm, which removes the *divergence* hazard but not the reason: reading once makes the two arms
agree by CONSTRUCTION rather than by both being careful (P3), and it is one read of one snapshot
rather than two.

### What this changes for a box

The collapse arbitrates masks against binds; the old mask arm did not arbitrate at all. Where the
two disagree, the map now decides both halves:

* a **bind nested under a mask** is swept, and the mask survives — already true of the bind arm
  since 2a-2 (MIGRATION §2.27); the mask arm now agrees with it instead of being computed elsewhere;
* a **bind at a mask's own destination**, declared in a LATER scope, sweeps the mask and takes the
  point. The retired cross-scope pass resolved that same collision the other way (§0 row 2: a mask
  OVERRIDES a binding at its dest), so between 2a-2 and 2a-4 the launch emitted BOTH — a `-v` bind and a
  `--mount type=tmpfs` at one destination — where it now emits the bind alone;
* a mask **at or above home**, or **on another mask**, is REFUSED by the collapse. Until 2c that left
  the leaf absent and dropped the whole launch to the fallback, so masks and binds both came from the
  reconciled rows and the box started; since 2c the refusal STOPS THE LAUNCH by name. Nothing about a
  refusal is mask-specific — this is the same tightening every collapse refusal got.

⚑ The dests are the map's KEYS, so they are `normalize_bind_dest`-spelled (`/home/agent/x`, never
`~/x`) and the arm is depth-sorted on `path_depth` — the same key the emitter sorts on, so the tmpfs
and the binds reach podman in one order.

⚑ **The arm is read AFTER `_apply_synced_copies`, and that ordering is load-bearing.** That pass may
DELETE a mask from the map — spec §0's `copy (file)` row, a synced file replacing a mask at its own
point (`_synced_masks_replaced` below). Reading the masks before it runs mounts a tmpfs over the file
the same launch just wrote. The bind arm is unaffected either way: `_emit_category_mounts` skips
masks, so only this arm can observe the deletion.

---

## `_launch_seed_list` / `_snapshot_assembly_seeded` — the seed applier consumes the LEAF (cutover 2b-2)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §2b (consumer 5) ·
`~/canon/workbook/specs/settings-keyspace-1.8.0.md:147-149` (*"both flat scope-ordered lists"*,
*"nothing is arbitrated at a destination"*) · 2b-1 (the seed leaf's own gate).

### What the switch is

`_apply_init_seeds` — consumer 5, the create-time home seed — used to walk `reconciled.copies` and
filter `seed.category != "seeded"`. It now reads `meta.assembly.seeded`, and **that filter is
deleted**: the seed arm and the sync arm are two SEPARATE leaves, so there is no discriminator left
to test. It was one of only two readers of `reconciled.copies`; the other is consumer 6
(`_apply_synced_copies`), which is a separate step.

⚑⚑ **`seeded` and `synced` ARE COPIES AND STAY COPIES.** Nothing here turns one into a mount, and
nothing carries a per-category destination space — the dest is DATA on the row.

🐞 **THIS SWITCH REMOVED AN ARBITER, AND THE FIX IS AT DELIVERY, NOT IN THE COLLAPSE.**
The retired `_resolve_copy_group` arbitrated copy-vs-copy at a shared dest (it returned the sync and
dropped every seeded row); `collapse_seeded` did not, because nothing read it. Reading the leaf
without that rule lets a seed write a credential dest FIRST with a PRESERVED mtime, after which
`_synced_uptodate` skips the sync forever. A prune in `collapse_seeded` closed it for one commit and
was **removed by ruling on 2026-08-11**: `_sync_box_at_create` now writes every sync row UNGATED right
after the create-time seed, so the dest holds sync-written bytes from creation onward and the launch
gate compares against the sync's own prior write. **Read `_sync_box_at_create` below, and
`llm-docs/kanibako/settings/store_collapse.py.md`, "Nothing is arbitrated at a destination".**

### The shape delta, and the three things it moved

A resolved winner was a `CategoryEntry` carrying `category`, `name`, `box_dest`, `host_src`,
`options`. A collapsed row is `CollapsedCopy(src, dest, opts)` — **no `name`, no `category`.**

| what it was | what it is | why |
|---|---|---|
| `seed.category != "seeded"` filter | *(deleted)* | two leaves, not one list with a discriminator |
| `group[0].name` in the outside-home warning | the `box_dest` itself | the DEST is the row's identity (R-10); the old text printed the dest twice |
| `assert e.host_src is not None` (×2) | *(deleted)* | `CollapsedCopy.src` is typed `str`, and `build_store_shape` refuses a source-less copy row before one exists |
| `name=seed.name` on `_apply_shell_copy` | `name=seed.dest` | same R-10 reason; the label a warning prints is the dest |

The user-visible consequence is confined to two `logger.warning` texts on the seed path, and in both
the value printed is the same destination it always was — normalized (`/home/agent/x`) rather than as
declared (`~/x`), exactly as 2a-2 already did for the mount warnings.

### Grouping is order-preserving BY CONSTRUCTION, and that is the load-bearing part

`collapse_seeded` emits in `SCOPE_CONTAINMENT` order and **a dest MAY repeat — the repetition IS the
overlay** (spec: nothing is arbitrated at a destination). `by_dest.setdefault(dest, []).append(row)`
preserves that: each group holds its rows in scope order, and dict insertion order keeps the dests in
first-appearance order. Nothing sorts and nothing keys a dest to a single row.

🛑 **A dest-keyed (`by_dest[dest] = row`) arm would pass most of the suite and silently drop the
template trio down to its last layer.** That is why the duplicate-dest case is pinned directly, and
mutation-proved against exactly that arm.

### The FALLBACK — DEAD AT CUTOVER 2c, and it was UNREACHABLE by then

`_launch_seed_list` read the leaf and fell back to the reconciled `seeded` winners when it was
ABSENT, identical in shape and reasoning to `_launch_bind_map`'s, because a refusal had to reach
nobody until step 2c.

🛑 **By the time 2c arrived that arm could not be taken at all.** The seed leaf rides its OWN gate
(2b-1) and is written by EVERY resolve, narrow ones included, so the only route to an absent leaf was
a fold that refused — and 2c made a refusal raise. The one test exercising the arm had to monkeypatch
the reader to reach it, which is the tell: a route only a monkeypatch can take is not a route. It
came out with its two siblings.

⚑ **The applier keeps its outside-home guard regardless.** That guard is about DELIVERING a dest,
which is its own concern; it was never a consequence of the arm being able to hand it a row
`_refuse_seed_outside_home` would have rejected.

⚑ **ABSENT ≠ EMPTY, and the distinction is still the data.** `_snapshot_assembly_seeded` returns
`None` for absent and `[]` for empty, and `[]` is a real answer a narrow resolve produces. What
changed is what `None` MEANS: not "the collapse refused" (a refusal raises now) but "this snapshot
was never resolved", which is why the consumer refuses it by name instead of seeding nothing.

### Why the create path could not have been pointed here before 2b-1

`box create` reaches this function through `seed_new_box` → `_seed_box_home`, on a NARROW resolve
(`include_base_families=False`) that has **no home bind at all** and never runs a main resolve. Under
the pre-2b-1 gate the seed leaf was absent on every such resolve, so consumer 5 would have taken the
fallback on every box — a cutover that moved nothing.

---

## `_apply_synced_copies` / `_launch_synced_list` / `_synced_host_dest` — the sync applier consumes the LEAF (cutover 2b-3)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §2b-3-MEASURED (consumer 6) ·
`~/canon/workbook/specs/settings-keyspace-1.8.0.md:147-149` (*"both flat scope-ordered lists"*) ·
spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*.

### 🛑🛑 The switch required MOVING the pass, and that is a MEASUREMENT, not a preference

`_apply_synced_copies` used to run its own NARROW resolve (`include_base_families=False`) ~180 lines
above the main one, next to the create-time seed. **That resolve carries no base families, therefore
no home bind, therefore `_install_assembly_collapse` writes no `synced` leaf on it — ever.** Measured
2026-08-11 by spying on `_snapshot_assembly_synced` across the real function: `[None]`. Pointing the
consumer at the leaf while it still resolved for itself would have read `None` on every launch and
**moved nothing** — precisely the trap 2b-1 had to fix on the seed side, in a form 2b-1's own fix does
not reach (the sync leaf is gated on the bind fold *by design*, because a sync dest is meaningless
without a bind map).

So the pass moved **below the main resolve and below the emit**, and consumes them:

```
_apply_synced_copies(snapshot=_snapshot, bindings=launch_binds, logger=logger, skip_if=...)
```

⚑ **The three inputs are ONE collapse.** `collapse_store_shapes` folds the sync list *against* the
bind map it just built (`_collapse_synced(shapes, bindings)`) and returns both in one
`CollapsedStore`. Resolving a sync dest against a bind map from a *different* resolve would resolve it
against a mount set the collapse never validated it over. There is exactly one coherent pairing.

⚑ **The narrow LAUNCH-time synced resolve is GONE, not disabled.** `emit_collision_warnings`' memo
named five in-process re-resolutions before 2b-3 and four after. It is back at five as of 2026-08-11
— the fifth is now the CREATE-time sync resolve (`_sync_box_at_create`, below), a different resolve on
a different path, and it runs only on a launch that materializes the box.

### The position is the contract (P3 — unavailable, not forbidden)

Every parameter is keyword-only and REQUIRED, with no `None` default. A caller at the old site cannot
name `_snapshot`, `deliveries` or `launch_binds` — they are assigned further down the same function —
so the mistake is an `UnboundLocalError`, never a silent no-delivery. **Mutation-proved:** restoring
the old call site fails 156 tests with `UnboundLocalError: cannot access local variable '_snapshot'`.
`test_the_consumer_CANNOT_BE_CALLED_before_the_bind_map_exists` pins the signature itself, because no
launch-harness test can pin the placement: `start_mocks` patches `_resolve_launch_snapshot` outright,
so no `_run_container` test drives the real resolve at all.

Three further consequences of the position, all deliberate:

* the three `return 1` arms in between — an unusable host agent binary, a failed auth check, an agent
  delivery bind whose source disappeared — now PRECEDE the pass, so a launch that bails no longer
  first writes into the box;
* `detect_shadowed_mounts` still runs BELOW it, so it keeps seeing synced files as pre-existing;
* the plugin descriptor's `cred_files` credsync engine now runs BEFORE this settings-driven pass
  rather than after it. Where both write one host file the `synced` row wins. (The old docstring
  claimed the two "do not overlap" — false as stated, and deleted rather than ported.)

### `_synced_host_dest` — the dest resolves THROUGH the bind that covers it

A `synced` dest is GUEST-spelled, and what it means on the host is decided by the mount set: the copy
must land in the SOURCE of whichever bind covers that path, or the box never sees it. So the rule is
**longest-prefix cover over the final bind map**, using `store_collapse.is_within` — published for
this, exactly as `path_depth` was published at 2a-2, so "inside" means one thing on both sides of the
delivery split.

⚑ **It does NOT replace `container._guest_dest_to_host`**, which keeps three other callers (the
stub/shadow scans) and answers a different question: where a guest path's host STUB is, under two
hardwired roots. On shipped config the two AGREE — `/home/agent` covers everything the stub arm sent
to `shell_path`, `/home/agent/workspace` covers its workspace arm with the same source. They part
company inside SOME OTHER bind, which the stub arm sent under the home stub: a host path that bind
shadows, so the copy was invisible in the box.

| arm | when | why it is where it is |
|---|---|---|
| no cover → warn+skip | dest outside every bind | there is no host location it could arrive at. Wider than the retired outside-home skip |
| cover is a MASK → warn+skip | `is_mask(bind)` | **must precede any `Path(bind.src)`** — `MASK` is `src=None`, so it raises `TypeError`, not `AttributeError`. By this point the cover can only be a mask ABOVE the dest, or the dest's own point with a DIRECTORY source |
| cover is READ-ONLY → warn+skip | `is_read_only(bind.opts)` | see below |
| else | — | `Path(bind.src) / rel` |

⚑ **A dest is DATA** — compared and sliced as a path, never `.split(".")`-ed.

🔴 **The MASK arm exists because the collapse ACCEPTS what delivery then SKIPS.** Since the
2026-08-12 ruling (*"don't check for sync. Let it clobber whatever it wants."*) the fold refuses a
sync nothing whatever, so every sync row reaches here, a mask's exact point included — and a mask is the
source-less entry, so `Path(bind.src)` would raise. Delivery is the only stage that can cope with it,
and this arm is where it does.

### `_synced_masks_replaced` — the one cell where the two COPY rows differ

**Authority:** spec §0's containment table, the `copy (file)` / `copy (dir)` rows.

At a mask's OWN point a copied **FILE** replaces the mask — one file filling one void is total, so
nothing partial survives — while a copied **DIRECTORY** there is REFUSED, because no mask may be left
half-populated. Everywhere else the two rows agree, and a mask that is a strict PARENT refuses both.
That single cell is why the table carries two copy rows rather than one.

**It can only live at DELIVERY.** FILE vs DIRECTORY is a property of the copy SOURCE, so deciding it
is a host `stat` — and the collapse is PURE, asking the filesystem nothing (this is what ruling 27's
stage map means for these rows). `store_collapse.py` therefore carries no copy-vs-mask rule at all;
`_apply_synced_copies` applies both cells over the collapsed map, before any row resolves.

**The deletion is a MAP EDIT, and the order around it is the contract.** A mask lives in
`meta.assembly.bindings` as its `src is None` entry, and `_bind_map_masks` is what turns those into
`runtime.run(tmpfs_masks=…)`. So the replaced mask is deleted from the map the launch holds, and
`_run_container` reads its mask arm **after** the sync pass — reading it before would hand podman a
tmpfs mounted over the file that same launch just wrote. Nothing of the snapshot moves:
`_snapshot_assembly_bindings` copies out, so the map is the launch's own.

Three properties that are easy to get wrong, each pinned by a test:

* **the deletion is decided over the DECLARATIONS, not over what was written.** A row the mtime gate
  skips still replaces its mask — otherwise the tmpfs would come back on exactly the launches where
  the sync had nothing to do, and shadow the file the box already has;
* **downstream skips do not restore it.** Nothing real covering the dest behind the mask, or a
  read-only cover, still leaves the mask deleted: containment is one layer, delivery another;
* **it is applied before any row resolves,** so which mask survives cannot depend on the order two
  sync rows sit in.

🔴 **The READ-ONLY arm is SPEC-SILENT and deliberately strict.** Writing into a read-only bind's host
source delivers content the box cannot be shown to have received, and the source is usually something
the user did not mean this to reach. Home and workspace are both `rw`, so no shipped configuration
loses anything; under the retired translator such a copy landed under the home stub, behind the
read-only mount, invisible. Loosening it later breaks no existing box — the reverse would not be true.

### `_synced_last_wins` — an overwrite copy's overlay IS last-wins

`_resolve_copy_group` returned ONE row per dest and called it *the credential pick*. The leaf returns
**both**. Applied in list order under the mtime gate (`_synced_uptodate`), a system row with a NEWER
source makes the box row a SKIP — so the less specific scope silently keeps a credential destination.

**The mtime gate is an OPTIMIZATION** — it exists to make an unchanged source a no-op — **and an
optimization may not decide which row wins.** So one row per dest, the LAST, which is byte-identical
to `_most_specific` because `SCOPE_CONTAINMENT` and `_SCOPE_APPLY_ORDER` are the same order and the
leaf is emitted in it. Each dest keeps its first appearance's position, so apply order over distinct
dests does not move.

⚑ **There is no seed analogue and there must not be:** a `seeded` dest's repeats are LAYERS that all
apply (the §2a template trio).

### 🛑 The category filter outlived its arm by exactly one step, then went with it

`_launch_synced_list` mirrored `_launch_seed_list`, and the `row.category == "synced"` test inside it
was **not** a leftover from the pre-cutover route: `reconciled.copies` is ONE list holding BOTH copy
categories, so the arm reading it had to say which half it wanted. The seed switch could delete its
filter at 2b-2 because it stopped reading that list; this one still read it on the fallback path, so
the filter was kept alive one step longer — deleting it there would have applied every `seeded` row
as an OVERWRITE, on every launch, over content the box owns.

**Both came out at 2c**, with the swallow that made the fallback necessary, and together with
`_launch_bind_map`'s and `_launch_seed_list`'s arms. The property the filter carried is now
STRUCTURAL: there are two leaves and this consumer reads one of them. That is exactly when a test
earns its keep — nothing in the types stops a future edit pointing the consumer at
`_snapshot_assembly_seeded`, so `test_start_assembly` pins it, mutation-proved against that edit.

---

## `_sync_box_at_create` — the `synced` write happens ONCE at create, UNGATED

**Authority:** Jei, 2026-08-11 — *"at box creation, since that's the only time seeded is copied, find
the top-most bind in the bindings and write synced to it once at creation, irrespective of date"*;
"top-most" corrected in the same exchange to mean *"the opposite of home"* — the INNERMOST covering
bind. On removing the collapse's prune in the same commit: *"Confirmed."*

### What it is, and what it deliberately is not

It is a **CALLER**, not a mechanism. `_synced_host_dest` already resolves a guest dest to its host
landing spot by LONGEST-PREFIX cover — longest prefix *is* innermost *is* "the opposite of home" — and
already carries the three warn-and-skip refusals (no cover · the cover is a MASK · the cover is
READ-ONLY). `_apply_synced_copies` already applies the rows. This adds the create-time caller and
nothing else; a second covering-bind resolver would be two spellings of one rule, which drift.

### Why UNGATED is the whole point

`_synced_uptodate` skips when `dest.st_mtime >= src.st_mtime`. **That comparison means something only
if the destination was last written BY THE SYNC**, and until now nothing made that true — the
create-time seed writes first through `shutil.copy2`, which PRESERVES the source mtime, so a seed
source newer than the sync source pinned the SEED's bytes at a `synced` destination, permanently and
silently, most often at a credential.

Writing the sync once at create with `skip_if=None` **restores the gate's own invariant** rather than
working around it. That is why the collapse's copy-vs-copy prune came out in the same commit: the
prune was deleting a user's declared seed row to protect a gate that can now protect itself.

⚑ **`skip_if` is therefore a REQUIRED keyword on `_apply_synced_copies`**, with no default. The launch
refresh passes `_synced_uptodate`; the create-time write passes `None`. They are two passes with two
answers, and a default would silently hand one of them the other's.

⚑⚑ **This makes SEED-THEN-OVERWRITE the shipped model**, in place of *"a sync owns its dest, seeds
never write there."* Both rows are delivered at a shared dest, in category order. That is decided.

### The second, FULL resolve — and why it does not widen the narrow one

A `synced` dest is meaningless without a bind map, so this needs `include_base_families=True`, while
`_apply_init_seeds` deliberately resolves NARROW: no `desc`, no `install`, no persona tier, no base
families, because *seeding is file delivery* and those inputs feed only `agent.<agent>.bindings.*`
MOUNTs. **Two resolves, two purposes, neither lying about what it reads.** Widening the seed resolve
to reach the bind map would be the cheap move and it is the wrong one.

For the same reason it is a **SIBLING** of `_seed_box_home` and not a fourth step inside it:
`_seed_box_home`'s stated contract is three ordered *create-if-absent* steps, and a sync is an
*overwrite*.

### The two call sites, and the order that matters

Both `_seed_box_home` call sites are creates and both get it, immediately after the seed:

| site | why |
|---|---|
| `seed_new_box` (the `box create` entry) | INSIDE that function, not beside `run_create`'s call to it — so it lands BEFORE `materialize_canon_skeleton`, which makes the canon region 555 and would fail a later copy with `EACCES` |
| `_run_container`'s `if proj.is_new:` block | the `workset connect` flow, whose FIRST launch materialises and seeds the box |

⚑ On the launch auto-create path the sync then runs **again**, later in the same process, mtime-gated
(`_apply_synced_copies` below the emit). That is correct and is the design: the create-time write
establishes the invariant, and the launch-time gate then correctly no-ops.

### Two caveats, stated because neither is blocking and both are easy to misread

* **`install` is `target.detect()` — a HOST FILESYSTEM PROBE** (agent name · host binary · install
  root · launcher), the early-out check, nothing to do with the container image. `create` already
  resolves `target`, so it can call it. **`detect()` returns `None` when the agent is not installed on
  the host**, which is a legitimate state, not an error.
* **The launch RE-DETECTS after `prepare_host()`** (the auto-updater), so the create-time and
  launch-time bind maps are not guaranteed identical. The later gated pass covers the difference.

⚑ **No CLI level (§1A)** on the create-side resolve: the create path's flags are not the launch's, and
the ephemeral ones (`-M`/`-N`/`-C`/`-R`) carry no bind. The image keys ride their own conditional
resolve, which this map does not carry either.
