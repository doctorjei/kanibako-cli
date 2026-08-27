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
a `box.env.<VAR>` twin are now two scopes' keys at one slot and REFUSE the launch (spec, the
`env.<VAR>` block: *"Two scopes' keys naming ONE variable REFUSE the launch, naming both keys"*),
while an agent-FILE variable and the plugin's declared default are the SAME key at two cascade levels
and simply cascade — the file wins, nothing refuses.

⚑ **The only feeder that argument ever had was the FILE.** The persona STORE's env has always ridden
the cascade (`settings_launch._persona_partial` splices it under `agent.<active>`), and every
`generate_agent_config()` returns an env-less config under the file-purity invariant — which is why
the parameter was retired outright rather than kept for a second caller.

### 🛑 AND THE CORE STAMPS FOLLOWED (MBR-1 P4b)

`_assemble_launch_env` used to end with four assignments onto the finished `container_env` —
`KANIBAKO_NAME` (gated on `proj.name`), `KANIBAKO_DIRECTIVE_SEED`, `KANIBAKO_AGENT` (gated on
`target is not None`) and `KANIBAKO_AGENT_MARKERS_DIR`. They ran after `_build_config_env`, after
`state_env` and after `_parse_cli_env`, which put them above every settings file, above a persona
and above `-e`. **Ruling 2026-08-14** (*"the 'KANIBAKO_' stuff should be in system.env"*) folds them
into the channel: `_core_env_default_categories(proj=, target=, agent_id=)` returns
`{"system.env.<VAR>": value}` and `_resolve_launch_snapshot` merges it into the
default-categories floor beside `family="kickoff"`, under `family="core env"`. The four then enter
`collapse_env` as ordinary system-scope keys, and `_assemble_launch_env` stamps nothing at all —
`target` and `agent_id` died from its signature and its one call site, the same move P3 made with
`agent_cfg`.

Three behaviours arrive with that, and all three are the point rather than side effects: a nearer
`system.env.<VAR>` file entry **overrides** one (same key, ordinary cascade); a twin at any other
scope **REFUSES** the launch naming both keys, where kanibako used to overwrite the user's value a
moment later in silence; and `-e KANIBAKO_NAME=x` **wins**. ⚑ **`-e` needed no code for that at
P4b** — the deletion alone satisfied ruling 42's `-e`-over-the-stamps half, because `_parse_cli_env`
was then the last writer left in the function. P4c-1 has since moved `-e` itself to the CLI cascade
level (below), and the stamps keep winning-order under it for the ordinary reason: `-e` overrides
the value of whichever key owns the variable, and these four are keys. ⚑ Making the
`KANIBAKO_AGENT_MARKERS_DIR` override REAL obliged the supervisor argv
to follow it: `--agent-markers-dir` now carries `container_env.get(...)`, the resolved env value,
so the box-side hooks (which read the env) and the host-side supervisor keep agreeing under an
override — the compile-time constant is only the shared fallback.

⚑ **The per-variable GATES live inside the one function**, not at the merge. Splitting the merge in
two — three stamps in the base-families block, `KANIBAKO_AGENT` inside the existing `if target is
not None:` — would put "which variables a box gets" in two places and give one concept two `family`
labels in the act-once provenance table. One call, one family, one place to read the answer.
🛑 `KANIBAKO_DIRECTIVE_SEED` is UNCONDITIONAL although its kickoff BIND is descriptor-gated: a
no-agent box gets the variable today. Do not tidy it onto the bind's gate.

⚑ The values are **resolved literals, not `@`-refs** (the `meta_identity_floor` pattern). And the
floor is the BASE level, below every settings file — which is exactly what makes the override story
"write the same key in a nearer file" rather than "you cannot".

⚑ `_launch_env_map` is ONE function where `bindings`/`seeded`/`synced` each have two (an option
reader plus a total one). Neither env consumer can act on an absent leaf — both describe a box, and
the leaf rides the whole-box gate — so an option form would be a route nothing takes.

### 🛑 AND THEN `-e` FOLLOWED THEM (MBR-1 P4c-1)

⚖️ **Ruling 42** (*"-e should override the key values, not the environment variables themselves"*)
+ **spec `env.<VAR>`** (*"`-e` beats a realization uniformly (the CLI level overrides the owning
key — §1A)"*; Jei: *"e must win… so it must be part of the collapse"*). `-e VAR=value` was
`container_env.update(_parse_cli_env(cli_env))` — the last line of `_assemble_launch_env`, a dict
paste over the finished environment. It is **the CLI level of the cascade applied to the env
family** now: `_run_container` parses the flag ONCE at its door, threads the map through
`_resolve_launch_snapshot(cli_env=)` → `_install_assembly_collapse` → `collapse_env`, and the
overlay runs there. `_assemble_launch_env`'s `cli_env` parameter and its paste are GONE, which is
what makes that function a projection of `meta.assembly.env` plus one remaining layer — and P4c-2
below took that one too.

⚑ **THE OVERLAY RUNS AFTER THE CONTAINMENT WALK AND THAT IS THE CONSTRUCTION, NOT A DETAIL** —
`store_collapse._apply_cli_env` carries the measured reason a reader must not "unify" it onto the
CLI *settings* level instead: `key_validity` refuses `cli.env.FOO` and the scope-less `env.FOO`, so
such a level would have to spell a CONCRETE scope, at which point `-e` becomes a second scope's key
naming the user's own variable and the twin refusal fires on exactly the configurations the flag
exists to serve. Applied after the walk, `-e` cannot CONTEST a slot at all: it overrides an owner or
fills a vacancy. An overridden slot KEEPS the owning scope + key (the key still owns the variable);
a vacancy gets `("cli", "-e <VAR>")`, honest and inert — no consumer parses either field.

⚑ **The malformed-item silent drop died in the same fold** (boarded with ruling 42). `_parse_cli_env`
skipped an item with no `=` and accepted `=v` as a variable named `""`; both are named errors now,
raised at the door before the launch reads a file. The shape is the keyspace's own plain env
identifier plus §0's reserved floor via `leaf_name_reason` — one rule, not a second copy.

⚑ **The two exec doors are a SEPARATE seam and STAY** (`runtime.exec(..., env=)` against a live
box): there is no collapse there to apply a level to. What they gained is the shared parse — they
read the map `_run_container` already built, so the doors and the launch cannot disagree about what
the user typed.

🛑 **`state_env` (the target's realizations) moved BENEATH the slots in the same edit**, as a stated
intermediate: a per-run `-e` was already a slot value, so a realization pasted on top would have
silently beaten the flag. That layer is GONE — see P4c-2 below, which replaced it rather than
reordering it again.

### 🛑🛑 AND FINALLY THE REALIZATIONS (MBR-1 P4c-2) — there is nothing above the channel left

⚖️ **Spec `env.<VAR>`** — *"`-e` beats a realization uniformly (the CLI level overrides the owning
key — §1A)"* (Jei: *"Hmm. Why does GOOSE_MODEL have to be post-collapse?"* — the question was the
answer: nothing forces it) + **ruling 59** (*"Yes on P4c-2"* — the refusals SHIP, no carve-out).
The five realized variables — goose `GOOSE_MODE` (unconditional, every launch) · `GOOSE_MODEL` ·
`GOOSE_PROVIDER` · `OPENAI_HOST` and claude `ANTHROPIC_BASE_URL` (each conditional on its driving
key resolving truthy); **codex and core realize none** — used to be `assemble_env`'s return value,
applied to the finished `container_env` by the caller. They are **launch-derived
`agent.<node>.env.<VAR>` keys** now, and `_assemble_launch_env` is a TOTAL projection of the leaf.

**The seam is a POSITION, and that is the design.** A realization cannot be a floor default: its
inputs (`effective_behavior`, the `-S`/`-A`-folded access tier, the persona provider pin) are
OUTPUTS of the resolve, so nothing that feeds the resolve can carry them. So `_run_container` builds
a `_LaunchRealizer` and hands it to `_resolve_launch_snapshot(realize=)`, which calls it **between
`build_launch_snapshot` and `snapshot_category_entries`** — the only point where its input exists
and its output is still in time — and `_install_realized_env` writes what it derived into that same
snapshot. `snapshot_category_entries` then adapts those keys like any other, and **nothing
downstream can tell a realized variable from a declared one.** That indistinguishability IS the
fold: no second producer, no second slot pass, no realization-shaped exception anywhere below.

⚑ **The alternative was measured and rejected**: resolving twice (once for the state, once with the
variables folded in) costs a second full resolve AND *inverts* `-M` — the realization would become a
floor a stored `agent.<node>.env.GOOSE_MODEL` sits above, so a flag that outranks every settings
file would lose to a key.

⚑ **The derived trio comes BACK to the caller** (`_LaunchRealizer.result` →
`LaunchRealization(effective_state, cascade_access, launch_access, env)`) so each keeps exactly ONE
derivation site. `_run_container` used to derive all of them itself, after the resolve — which is
precisely why the variables could not be keys. The **un-rendered tier gate moved with them**, into
the realizer ahead of `assemble_env`, because `assemble_env` raises on the LAUNCH tier alone while
the gate must name the CASCADE tier first. 🛑 `result` RAISES rather than defaulting when the
callback never ran: an empty default would turn a resolve that dropped the kwarg into a box launched
at `full` with no realized variables and nothing said about it.

**Two refusals, two raise sites, and the split is the CURE not the mechanism.** A twin at ANOTHER
scope is the ORDINARY `store_collapse._refuse_env_twin` — the existing mechanism catches it for
free, and nothing was built for it. A declaration at the SAME (agent) scope is the one the collapse
cannot see, because the §2d pick merges `agent.<node>` and `agent.default` into one node where one
value would simply overwrite the other; `_refuse_realized_twin` names the declared key AND the key
that DRIVES the variable (`targets.assembly.env_realization_drivers`, the twin walk of
`assemble_env`), because a cross-scope twin has a key to move to and this one does not.

⚑ **`box show --effective` deliberately does NOT gain realizations.** It resolves stored
configuration and is given no realizer, so a launch sets variables a display does not list.
Deriving them for a display would report a tier the launch's own flags may not ship. That drift is
recorded, not pending.

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

⚑ **The three inputs are ONE collapse.** `collapse_store_shapes` builds the sync list and the bind map
from one set of shapes and returns both in one `CollapsedStore`. Resolving a sync dest against a bind
map from a *different* resolve would resolve it against a mount set the collapse never validated it
over. There is exactly one coherent pairing.

⚑ **The sync ARM itself is a plain scope-ordered concatenation and takes NO bind map.**
`_collapse_synced(store_shape_set)` lost that parameter when the sync-at-a-bind refusal was deleted
(ruling 2026-08-12); the bind map is applied to a sync dest at DELIVERY, by `_synced_host_dest`. (This
line read `_collapse_synced(shapes, bindings)` until 2026-08-20 — stale against the shipped signature,
caught by the prose pass.)

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
so no `_run_container` test drives the real resolve at all. ⚑ The create-time caller satisfies the
guard by bringing a real, FULL resolve of its own; it is not relaxed for it. ⚑ The reconciled rows
were a FIFTH parameter until cutover 2c, read ONLY by the sync list's fallback arm; the arm and the
parameter came out together.

The pass is ADDITIVE: with no `<scope>.synced` keys configured, and no target default synced entries,
the sync list is empty and it copies nothing.

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

The lookup itself is `_synced_cover` — ONE spelling, shared with `_refuse_synced_under_mask` below,
so the pass that decides whether a covering mount REFUSES a row and the pass that resolves the row
THROUGH it can never disagree about which mount the destination sits in.

⚑ **Its BODY moved to `store_collapse.covering_bind` on 2026-08-26** and `_synced_cover` is now a
one-line delegation. A THIRD asker appeared — `store_collapse.pair_declarations`, which must say
what a DECLARATION actually got — and the question is the collapsed map's own, so it is answered
beside that map. The name stays here because these two callers read as DELIVERY, not as collapse.

| arm | when | why it is where it is |
|---|---|---|
| no cover → warn+skip | dest outside every bind | there is no host location it could arrive at. Wider than the retired outside-home skip — `/etc/...` with nothing declared over it, for instance |
| cover is a MASK → warn+skip | `is_mask(bind)` | **must precede any `Path(bind.src)`** — `MASK` is `src=None`, so it raises `TypeError`, not `AttributeError`. By this point the cover can only be the dest's OWN point with a source that is neither file nor directory; the two the spec calls refusals already raised |
| cover is READ-ONLY → warn+skip | `is_read_only(bind.opts)` | see below |
| else | — | `Path(bind.src) / rel` |

⚑⚑ **NOT ONE OF THESE THREE IS A REFUSAL THE SPEC NAMES, and that is why they warn.** Spec §0
states the `synced` refusals exhaustively — *"The only refusals a `synced` copy meets are a mask as
its PARENT and a copy of a DIRECTORY at a mask's own point"* — and `_refuse_synced_under_mask`
RAISES on both before this function runs. What is left here is residue the containment table says
nothing about: a dest no bind covers, a read-only cover, and a mask at the dest's own point over a
missing or unreadable source (already the module's own class — `_apply_shell_copy` warns on any
missing source). For residue, a mis-declared dest must not cost the user the launch. They must be
asked in the order the table spells them.

⚑ **A dest is DATA** — compared and sliced as a path, never `.split(".")`-ed.

🔴 **The MASK arm exists because the collapse ACCEPTS what delivery then JUDGES.** Since the
2026-08-12 ruling (*"don't check for sync. Let it clobber whatever it wants."*) the fold refuses a
sync nothing whatever, so every sync row reaches delivery, a mask's exact point included — and a mask is the
source-less entry, so `Path(bind.src)` would raise. Delivery is the only stage that can cope with it.

### `_refuse_synced_under_mask` — the table's word is REFUSE, so it raises

**Authority:** spec §0's containment table, the two copy rows, and the sentence that states their
refusals exhaustively. A mask that is a strict **PARENT** of a sync dest refuses the copy; a
**DIRECTORY** copied at a mask's own point refuses too, because no mask may be left half-populated.

**It raises `SettingsError`, like every sibling refusal in that table** (`_refuse_bind_under_mask`,
`_refuse_mask_on_mask`, `_refuse_mask_over_home` in the collapse). Until 2026-08-23 both were
warn-and-skip here, on the reasoning that *a mis-declared dest must not cost the user the launch* —
which nothing ratified, and which the spec's own word contradicts. A refusal that only warns is an
acceptance with a log line, and the row it silently drops is usually a **credential**: the box
starts, the harness then fails to authenticate, and nothing on the way names the configuration that
caused it.

**Position: after `_synced_masks_replaced`, before the first copy.** After, because the cell the
table ACCEPTS — a FILE at a mask's own point — is decided there and deletes its mask, so it must be
settled before anything asks what the cover is. Before, because a raise mid-loop would leave the box
with some rows delivered and some not.

**The one mask cover it does not refuse** is the dest's own point over a source that is neither file
nor directory. The table has two copy rows; a missing or unreadable source is in neither, so it
keeps the missing-source warning it already had rather than being promoted to a refusal the spec
never named.

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
already carries the three warn-and-skip arms (no cover · the cover is a MASK · the cover is
READ-ONLY). `_apply_synced_copies` already applies the rows, and already raises spec §0's two
refusals through `_refuse_synced_under_mask` — so a mis-declared dest stops `create` exactly as it
stops a launch. This adds the create-time caller and nothing else; a second covering-bind resolver
would be two spellings of one rule, which drift.

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

---

## `_run_container` — the DOOR TABLE and the OVERRIDE GATE for an already-running box

**Authority:** Jei, 2026-08-05 — *"could this setting work without restarting the box and without
making a mangled / corrupted / unexpected state?"* · Jei, 2026-08-05f (the tmux check) · Jei,
2026-08-02b (*"yes, same hint"*).

### The four doors

The reattach regime (`if reattach_running:`, ~400 lines below the table) has exactly FOUR exits, and
every value that decides WHICH ONE a launch takes is settled by one line near the top of
`_run_container`. It is resolved ONCE, there, into a named value — and the override gate immediately
below it AND the regime itself both READ that answer instead of each re-deriving it from its own
subset of the deciding inputs.

| door | when | what it does |
|---|---|---|
| `detach` | `--detach` / `--warm-only` | the box is already Up, which is the whole request → report and return 0. ⚑ NOTHING RUNS through this door, so it can carry no per-run flag |
| `box-shell` | `kanibako shell` at a live box that IS RUNNING AN AGENT | resolve a shell off the LIVE container's image and exec it as a second process |
| `entrypoint` | an explicit `--entrypoint` | the same exec, with the program the user named |
| `attach` | everything else | `_bootstrap_attach` (`tmux attach`), whose exec takes NO `env=` and no program of ours |

⚑ **`stored_agent` — the LIVE box's `KANIBAKO_AGENT` stamp — is LOAD-BEARING in the `box-shell`
row**, not a redundant narrowing of `box_shell_mode`: a live NO-AGENT box's PID-1 tmux session IS the
user's own shell, so it must keep reattaching (exec'ing a fresh shell would strand it). Such a box
therefore takes the `attach` door, where a `-e` would be silently discarded — which is exactly why
the gate below must refuse one.

### Why a TABLE and not another predicate term

The gate below asks *"will a consumer apply this flag?"* It used to answer by reading TWO of the
three values that pick the door (`entrypoint`, and `box_shell_mode` + `stored_agent`) while a THIRD
(`detach`) closed both of them ~400 lines later — so `start --detach --entrypoint X -e FOO=bar <live
box>` was ACCEPTED and then silently discarded, rc 0, the exact failure class the gate exists to end.
A gate that reads the RESOLVED DOOR cannot disagree with the regime that walks through it, however
either one grows.

### It reads TYPED values, and it must stay where it is to do so

`entrypoint` is rebound repeatedly further down (the two box-shell resolves, the agent default, the
detached-launch box shell), as are `no_helpers` and `explicit_agent`. The block sits ABOVE every one
of those, so the question it answers is the question the USER asked. Same shape as `is_agent_mode`
just above it: freeze the typed question once, read it many times. `typed_entrypoint` keeps that
frozen answer under its own name (the `cascade_access` / `launch_access` shape used further down) so
the regime never has to trust that no rebinding slipped in between.

### What the override gate refuses, and why each class

For the flags below the answer to Jei's test is no, so they are REFUSED BY NAME rather than silently
dropped, which is what happened before it:

* **Class A (container-CREATION inputs)** — the container already EXISTS, so a creation-time input
  cannot reach it. That is the whole justification, and it is uniform across the class: no member of
  it gets a bespoke rationale, and none is softened because some later code path happens to ignore it
  anyway.
* **Class B (agent argv)** — the reattach execs a bootstrap ATTACH, never a fresh agent, so
  `start -N <running-box>` attaches to the OLD conversation instead of starting a new one.
* **the two SESSION-SHAPE flags** — an EXPLICIT `--persistent` asks for a fresh persistent session,
  which we cannot give without killing the bootstrap session that is already there, and an explicit
  `--ephemeral` asks for a foreground single-use session, which cannot coexist with it. Jei
  (2026-08-05f): *"it SHOULDN'T be necessary to kill tmux for this; we should find out if tmux is
  running BEFORE we try to reattach. If tmux is there, we go back to the command line (and leave the
  old tmux thread running)."* This gate IS that check: it precedes every attach, so the running
  session is never signalled, killed, or even touched.

The gate sits ahead of BOTH seams (the flag persist and the reattach fast path below), so neither can
ever observe one. `--restart` never reaches it — it stopped the box above, so nothing is running.

⚑ **THE SESSION-SHAPE PAIR IS KEYED ON THE FLAG THE USER TYPED, NOT ON `persistent`.** `persistent`
is DERIVED: `run_start` defaults it True whenever the bootstrap program is present, so gating on it
would refuse every ordinary reattach. `explicit_persistent` / `explicit_ephemeral` carry the typed
flags, threaded in from both callers that own them (`run_start` and `run_shell`) precisely so the two
facts stay distinct. They are also the reason this gate keys on `box_running` rather than
`reattach_running`: an explicit `--ephemeral` DERIVES `persistent` False, so it never becomes a
reattach and `reattach_running` would be False for the very case that must be refused.

⚑ The session-shape refusal is scoped to an AGENT launch: `kanibako shell` and `shell -- cmd`
against a live box exec INTO it (the documented UX), which honours an `--ephemeral` request rather
than dropping it, so there is nothing there to refuse.

**DELIBERATELY NOT GATED:** `--attach` / `--detach` / `--print-container` / `--warm-only` — all
meaningful against a live box in their own right.

### The two PER-RUN flags are gated BY THE DOOR

`--entrypoint` and `-e/--env` are refused on one principle: refuse a per-run flag only when the door
this launch will actually take would not apply it.

* `entrypoint` / `box-shell` — both land on the one `runtime.exec` that passes `env=`, so both flags
  ride through and neither is refused;
* `detach` — NOTHING RUNS: the box is already Up, we say so and return 0. So a typed `--entrypoint`
  AND a `-e` are both refused BY NAME, rather than accepted and dropped. (The FRESH-launch path's
  separate decision to drop `--entrypoint` under `--detach` is unrelated and unchanged — there a
  container is actually created.)
* `attach` — `_bootstrap_attach`'s exec takes no `env=`, so `-e` is refused. A typed `--entrypoint`
  can never reach this door (it would have selected `entrypoint`), so there is nothing to say about
  it there.

---

## `_persist_or_announce_flags` — settle a SHADOWING flag: persist it, or say OUT LOUD it is ephemeral

**Authority:** ruling #12 (Jei, 2026-08-02) — *"(c) BOTH"* · Jei, 2026-08-02b — *"yes, same hint"* ·
2026-08-05f (a reattach reaches this seam zero times).

### The persist arm, and the window it closes

Every flag value still routes through the ONE §1A CREATE-EXCEPTION gate
(`settings.config.persist_creation_flags`), which reads the single `materializing` signal and
decides; this function adds no persist logic of its own. It is called from the EARLIEST point on each
launch arm at which BOTH preconditions hold — the rig is PREPPED (built or pulled) and `proj` is
MATERIALIZED — because everything between materialization and the persist is a window in which a
failed launch leaves the box EXISTING with the flag unstored, so the user's retry is
non-materializing and their `--image` is silently discarded. Ordering both ways round has a cost and
the two are not symmetric:

* **BEFORE the rig prep** would store an image that never pulled (the hazard Jei named) — so we never
  go earlier than the prep. A pull that FAILS is therefore the one window that stays open by design;
  the announce arm is what keeps it from being SILENT.
* **AFTER the rest of the launch** (where this used to be called) left the baseline probe, the
  agent-config load, the persona store read, the launch-decision resolve and the persona pre-flight —
  each of which can return non-zero — inside the window.

⚑ **A REATTACH reaches this seam ZERO times.** There is no window to close: the box already exists
and no container is being created, so nothing could be materializing, and both flags this seam
settles are refused outright by the running-box override gate — a user who wants `--rig` to take is
told to `kanibako --restart`, which relaunches through the normal arm and lands here as usual.

### The announce arm, and why it is ONE notice

On a NON-materializing launch a supplied shadowing flag applies to that launch alone (spec §1A) and,
before this, said nothing about it. It now prints the ephemerality and the cure, which is the general
fix for the residual window above *and* for the far commoner case of a user who simply expected
`start --image` to stick. BOTH shadowing flags this seam settles are announced — `--image` and
`--share-images` have the identical silent gap.

⚑ **ONE notice, never two.** The flags are announced TOGETHER because the explanation ("this box
already exists, so nothing was stored") is the SAME sentence for both: emitting it once per flag
would make a user read two near-identical paragraphs and diff them to find the difference. The
single-flag rendering is therefore byte-identical to what one flag printed before; a second flag
extends the same sentence and adds a second cure line rather than starting a second notice.

⚑ *box_settings_path* is the BOX-TIER settings file from `box_workset_settings_paths` (M-8) — the
file `box set box.image=…` writes.

---

## `_build_supervisor_pid1` — the import-GATED PID-1 for a DETACHED agent box (E2b)

**Authority:** design §221-225 (forward-compat) · the 2026-08-17 measurement below.

The always-on supervisor (`kanibako.box_supervisor`) runs the agent in a detached tmux session and
self-heals it, so a detached AGENT box makes the supervisor PID-1 instead of a bare-shell keep-alive.
An OLD box image may ship a kanibako WITHOUT the supervisor module, so PID-1 is an import-GATED
`sh -c`: it `exec`s the supervisor only when `import kanibako.box_supervisor` succeeds, else `exec`s
the *fallback* bare-shell keep-alive, degrading gracefully. *supervisor_argv* and *fallback_argv* are
FULL argv lists (the program at index 0); both are `shlex`-quoted into the single `sh -c` script so
agent args carrying spaces / quotes survive intact through the nested shell. It returns
`("sh", ["-c", <script>])`.

### ⚑⚑ The gate RETRIES ONCE and the fallback is LOUD (2026-08-17)

The probe used to run ONCE with `2>/dev/null`, so a transient failure produced a box sitting at a
bash prompt with the agent never started, the reason discarded, and `kanibako start` still returning
SUCCESS — no host-side signal of any kind. That is not hypothetical: it was MEASURED on a real launch
that had resolved claude correctly (`podman inspect` showed this exact gated script; PID 1 was its
`||` branch), and the same import in the same container succeeded when probed minutes later. So the
probe now runs twice, its stderr is APPENDED to `SUPERVISOR_FALLBACK_RELPATH` under the guest home
instead of being thrown away, and taking the fallback writes a warning to BOTH stderr (which reaches
`podman logs`) and that file. The fallback still runs — a degraded box beats no box — but it can no
longer be silent.

### The HOST kanibako goes FIRST on `sys.path`, and the quoting is load-bearing

Both the import PROBE and the supervisor `exec` run with the HOST kanibako — bind-mounted read-only
at `KANIBAKO_PKG_MOUNT_ROOT` by `_kanibako_mounts` — FIRST on `sys.path` via an injected
`PYTHONPATH`, so the supervisor that runs is the FRESH host version (== the host CLI), never the
image's baked copy. Published images ship an OLD kanibako WITHOUT the supervisor module, so probing
or exec'ing the baked copy would silently degrade every real launch to the bare-shell fallback.
`PYTHONPATH` is PREPENDED (`${PYTHONPATH:+:$PYTHONPATH}`), never clobbered, so any value the image
sets survives after our entry; the supervisor scrubs its OWN mount-root entry back out before
spawning the agent/tmux children (`box_supervisor.scrub_bootstrap_pythonpath`).

⚑ The FALLBACK `exec` is left UNCHANGED (no PYTHONPATH): it is a bare-shell keep-alive that never
imports kanibako, and runs only when even the host supervisor import fails.

⚑ The whole `PYTHONPATH=…` assignment is DOUBLE-QUOTED so the shell still expands `${PYTHONPATH:+…}`
but the RESULT is never word-split or glob-expanded — an image whose own PYTHONPATH held a space or
`*` would otherwise split the env assignment into extra `env` operands (`env` treats the tail as a
command and fails), silently degrading the probe to the bare-shell fallback. The
`KANIBAKO_PKG_MOUNT_ROOT` literal is itself a fixed, shell-safe path; the argv lists stay
`shlex`-quoted verbatim after each `exec`.

---

## `_resolve_box_launch_decisions` — auth SOURCE + persona endpoint + persona model, off ONE snapshot

The single-source consolidation of the auth resolve and the behavior (endpoint/model) resolve.
`build_launch_snapshot` accepts BOTH the auth 3-tier `auth_chain` floor AND the behavior
`behavior_floor` in a single call, so the box's sharing decision (`settings_launch.AuthSource`,
`resolve_auth_source`) and its active-node `agent.<node>.endpoint` (`effective_behavior`) are read off
the SAME expanded snapshot — no duplicate build. Same pipeline the main launch uses (single-route).

* **`auth_src`** — the credential-SHARING SOURCE (tier/source + enables), threaded to every
  credsync/gate consumer, exactly as `_resolve_box_auth_source`.
* **`endpoint`** — the resolved PERSONA endpoint URL, or `None` when unset (`<None>` / empty / no
  descriptors / no target) — the cred-fork signal (non-None ⇒ suppress the OAuth cred). `None` is
  byte-identical to the behaviour before personas.
* **`model`** — the cascade-resolved active-node `agent.<node>.model` (the box-level override where
  set), THREE-STATE per the 2026-08-17 ruling: `__MISSING__` (never set, incl. no descriptors / no
  target), `None` (PRESENT-null — *"this endpoint needs no model"*), or a resolved `str` id.

The behavior floor folds in as `agent.default.<key>` (OS1) and the per-agent FILE state as the active
`agent.<node>` slot; the §2d active-over-default pick yields the endpoint for the NODE (persona
identity). A target with no declared settings contributes no floor → endpoint `None` (bare).

### Why the model is read by `_persona_model_state`, not by `effective_behavior`

`effective_behavior` deliberately COLLAPSES a present-None scalar into omission — its own docstring:
*"the consumer applies its own default"*, the general reset-to-default convention every OTHER
behavior key wants — and that is exactly the distinction the persona model gate needs KEPT APART.
`_persona_model_state` reads the SAME snapshot and preserves it, mirroring
`agent_select.resolve_selected_agent` / `settings_launch.snapshot_leaf`'s ABSENT-vs-PRESENT-None
idiom for `pref.system.agent: null`.

The codex-persona provider needs a real model id, so this DELIBERATELY excludes the harness `model`
DEFAULT floor (e.g. codex's `gpt-5.5` — an own-endpoint default that is wrong for a third-party
provider): an unset persona model resolves `__MISSING__` here so the preflight surfaces an actionable
empty-model error rather than shipping a bogus default into a NaviGator `[model_providers.<id>]`
block. It is only consumed for a persona load gate, and is unused (harmless) for bare launches, where
the `--model` flag still resolves its own default via the main launch snapshot.

### Why *persona_values* is threaded in, and why *selection_level* is NOT the full CLI level

*persona_values* is the persona store's LIVE tier (`_persona_values_for`), threaded in because BOTH
returns above depend on it: the *endpoint* is the cred fork (a persona endpoint that did not reach
this resolve would silently stop suppressing the host OAuth cred, sending the user's real Anthropic
token to a third-party endpoint), and the *model* is written into the codex `[model_providers.<id>]`
block. `None` for a bare agent. ⚑ The store tier sits BELOW the per-agent FILE, so while the swap
still persists the same values this changes NOTHING — the file wins with an identical value. It is
what keeps both returns correct once the persist is retired and the file no longer carries them.

⚑ *selection_level* is the SELECTION only — deliberately NOT the full §1A CLI level (P8). The *model*
this returns is threaded into `_preflight_persona_load` and lands in the codex `config.toml`
`[model_providers.<id>]` block, i.e. it is WRITTEN TO DISK; letting `-M` reach it would make an
ephemeral flag mutate a stored value, which spec §1A forbids (*"EPHEMERAL, always"*). `-M` therefore
rides the LAUNCH snapshot only (`_resolve_launch_snapshot`), where it reaches argv and the container
env and nothing else.

---

## `_preflight_persona_load` — a persona's LOADABILITY, resolved before any artifact exists

A TRUE pre-flight: called ONLY for a persona (`harness_of(agent_id) != agent_id`), BEFORE any persona
artifact is created. Returns `(endpoint, error, provider)`:

* **endpoint** — the resolved endpoint URL on success (`error` None); `None` when unloadable;
* **error** — an actionable message when the persona cannot be loaded (unresolved endpoint, or
  endpoint-but-no-token); `None` on success. The caller prints it (start) or raises it (create) and
  refuses to launch;
* **provider** — for a CONFIG-FILE harness (codex) the resolved
  `vscode.vscode_config.CodexModelProvider` INC 3 wires into `~/.codex/config.toml`; `None` for an
  ENV harness (claude, goose), whose endpoint + token ride their existing single-source channels (the
  `endpoint`→`ANTHROPIC_BASE_URL` `SettingArg` env + the `secret_path` mount), so no separate carry is
  needed.

⚑ **NOTHING here mutates *agent_cfg*.** Every persona value is a LIVE resolution input resolved
through the cascade before this seam, so there is nothing to adopt and nothing to write back to
`agents/<node>/agent.yaml`.

### HARNESS-AWARE (INC 2) — exactly two gates, one picker

The token var and the endpoint DESTINATION are chosen per the resolved target's `_persona_wiring`,
not hardwired to Anthropic. There are exactly TWO gates below it and `endpoint_delivery` alone picks
between them: ENV (`_preflight_env_persona` — claude, goose) and CONFIG-FILE
(`_preflight_config_file_persona` — codex, which also returns the CodexModelProvider for INC 3).
Everything that used to distinguish claude from another ENV harness was the retired B3 host-dir path.

### What each input means

**`keyspace_endpoint`** is the endpoint the launch snapshot already resolved from
`agent.<node>.endpoint` — the persona STORE is a cascade tier of that snapshot, so a store-configured
endpoint arrives here already resolved. `None` therefore means no endpoint is configured for this
persona ANYWHERE, which is a hard error: kanibako will not launch a persona as the bare host harness
on the user's real account. A resolved endpoint with NO usable token is ALSO a hard error (a bearer
endpoint with no token 401s inside the box).

**`keyspace_model`** is the box-level cascade-resolved `agent.<node>.model` — the single-source
resolution done by the caller off the launch snapshot, harness default excluded (see
`_resolve_box_launch_decisions`) — and THREE-STATE (2026-08-17 ruling, `_model_tristate`):
`__MISSING__` (the default — never configured), `None` (PRESENT-null — "this endpoint needs no
model"), or a resolved id (`str`). Whether an ABSENT model is fatal is the HARNESS's declaration
(`PersonaSpec.model_required`), not a property of the path: codex vetoes (a config-file provider
block with `model = ""` is meaningless and an omitted key falls through to codex's own moving
default), goose vetoes (a third-party OpenAI-compatible endpoint has no meaningful default), claude
does not (its model rides its own channels). A PRESENT-null model SUPPRESSES that veto for an ENV
harness (it can simply omit the model) but CONFLICTS with a CONFIG-FILE harness (it structurally
cannot express "no model" at all) — see the two sub-gates for the refusal.

**`bundle`** is the persona-grata store read for this same launch (`_persona_bundle_for`). It is the
SECOND token source — the agent file rung first, the store below it (`_persona_token_pointer`) — and
it carries the store's own verdict on itself:

* `reject_reason` ⇒ a HARD ERROR naming the reader's SPECIFIC cause, VERBATIM. A persona whose store
  config the harness refused has no usable values, and there is no last-known-good to fall back on
  now that nothing is persisted.
* `no_reader` ⇒ NOT an error, ever. The harness simply has no store reader; a goose persona is
  configured entirely through the keyspace and merely happens to own a store dir, and refusing it
  would break every such launch.

⚑ That is the whole reason the two are separate values rather than one reason string: collapsing them
would refuse every keyspace-configured goose persona that owns a store directory.

**`probe`** runs the PER-LAUNCH verify probe once endpoint and token both resolve
(`_persona_probe_error`). It is opt-in and set ONLY by the launch: the create path keeps its own
WARN-ONLY probe (locked ruling #2), so a create must not inherit this one's hard error on a rejected
token.

---

## `_merge_default_categories` — folding ONE family's default-categories table into the floor

### ⚑⚑ This exists because `dict.update` STOPPED being safe here (H5)

While a bindings arm was NAME-keyed, every family owned distinct `box.bindings.ro.<name>` keys and a
plain union was well-defined. Under dest-keying the arm is ONE terminal key holding the whole map
(R-5), and about eight of the families contribute to `box.bindings.ro` — so an `.update()` would
replace the map wholesale and delete every earlier family's entries, silently and with nothing
downstream able to notice.

⚑ **ALL SEVEN TERMINAL CATEGORIES, not just the two bindings arms.** When `caches` / `seeded` /
`common` / `synced` became terminal too (2026-08-08c) they landed in exactly the state the paragraph
above calls unsafe, and several families write each of them: `target.default_seeds()` and
`template_seed_defaults` both write `agent.<a>.seeded`; `target.default_common()` and
`target.default_category_binds()` both fold into `agent.<a>.common`. The membership test is
`settings.settings_keyspace.is_terminal_category_key` — the ONE whole-key predicate, DERIVED from the
keyspace's own declaration so it cannot fall behind the next flip. ⚑ A private copy lived here until
QC; it is the KEYSPACE's answer, and a second copy is how the two would drift.

### Two branches, and the split is deliberate

* a **TERMINAL dest-keyed category key** whose value is a map merges ENTRY BY ENTRY;
* **everything else** is LAST-WINS, exactly as before.

⚑⚑ Do NOT generalize this into a deep merge for every value. Two call sites —
`extra_default_categories` and `resolved_sys` — are LATE INJECTIONS that are supposed to override,
and a deep merge would quietly stop them from doing so. The `dict` test is what keeps the LIST-valued
`<scope>.masks` on the last-wins branch where it belongs — **masks hold whole.** ⚑ It is NOT what
excludes a scalar leaf that merely ends in a category token: `system.channels.common` fails
`is_terminal_category_key` itself, on POSITION, so no value of any shape at that key can reach the
merge branch.

### The same-key duplicate-dest refusal is STRUCTURAL

A destination already claimed in the SAME key RAISES, naming both families (*origins* carries who
claimed it). ⚑ The refusal therefore holds for every terminal category, not only for act-once
bindings. The value is ONE dict keyed by destination, so it can hold exactly one entry per
destination — two families claiming one dest under one key is not an overlay the shape can express,
and whichever lands second simply erases the first.

That is NOT a statement that seeds may not layer: they may, and do. Layering is expressed ACROSS KEYS
— `system.seeded` / `agent.<a>.seeded` / `workset.seeded` all target `~/`, remain three DIFFERENT
keys here, and are regrouped by destination and staged in scope apply order (per-file last-wins) by
`_apply_init_seeds`. Nothing in that route passes through this function's per-key map, so widening
the raise to the copy categories takes no legitimate overlay away; it only refuses the one
arrangement that has no representation. (Bindings additionally being act-once is a SECOND reason for
the arms, not the reason here: two entries at one dest in DIFFERENT arms or DIFFERENT categories are
still different keys, still both emitted, and still reach the collision table in
`settings_categories` — untouched here.)

---

## `_resolve_launch_snapshot` — the ONE launch resolve, and what gates each parameter

### What it is

The single launch-time CATEGORY resolve (block 7b). It aggregates every runtime
`default_categories` table (core / kani / channel / share / seeds / masks, plus the CONDITIONAL
helper + image tables) into ONE floor, folds in the resolved `system.*` tier so @-refs resolve from
the snapshot (the `resolved_sys` map — ⚑ built by `settings/paths.system_path_floor`, **shared with
`commands/workset_cmd._print_effective_shares`**; it was a hand-written dict here and another one
there, and this one was missing `system.channels.broadcast`, so a binding sourcing that declared key
was dropped from the collapse with no message and rc 0), represents the agent's descriptor delivery
binds via 7a's `agent_default_partial`, and
runs `assemble_levels → merge → expand` ONCE via
`settings.settings_launch.build_launch_snapshot`. The expanded snapshot is then adapted to
`CategoryEntry` ONCE, and that ONE list feeds every consumer below it.

It returns `(snapshot, deliveries)`. The mount set a box is built from lives IN the snapshot, under
`meta.assembly.*`, written by the assembly COLLAPSE — and so do the environment variables it is
launched with (`meta.assembly.env`, read by `_launch_env_map`). The second element is the
`settings.settings_categories.LaunchDeliveries` carrier — the `secret_path` mounts, the
agent-delivered dests and (for a narrow resolve only) that resolve's own bind map: what the collapse
deliberately does not carry, built off the SAME credential-gated list the collapse sees, so the two
describe one box.

⚑ **The carrier is a RETURN VALUE and not a snapshot leaf on purpose** — `meta.assembly.*` is a
CLOSED set of DECLARED leaves, and an undeclared one would install silently; what belongs in that
closed set goes through a keyspace change to get there, as the env leaf did.

⚑ **THERE IS NO SECOND, CROSS-SCOPE `reconcile` PASS ANY MORE (cutover 6-R3).** §0's collision table
is applied by the per-scope producer, the collapse, and the two seam functions that hold inputs the
collapse never sees — see `settings.settings_categories`' module docstring for the split.

⚑ AGENT_CRITICAL delivery binds flow through the snapshot's `agent.<agent>.bindings.*` subtree
(single-route), emitted by `_emit_category_mounts` under its `must_exist` policy at the call site —
NOT a parallel `descriptor_mounts` route, and since cutover 2a-3 not a parallel agent emitter either.

### The parameters, and what each one gates

**`narrow_bind_dests`** is a NARROW caller's own injected table's dests
(`core_defaults.helper_bind_dests` / `image_bind_dests`). Given, this resolve builds
`deliveries.narrow_bindings` — that table's rows and NOTHING ELSE, in the emitter's shape. Omitted,
the field stays `None`: a whole-box resolve emits from `meta.assembly.bindings` and must not have a
second map available to reach for.

**The image + helper tables are CONDITIONAL.** A table is included ONLY when its gate holds
(image-sharing active → *storage_conf_path* given, *graph_root* only when the probe succeeded — it
feeds ONLY the `box.images_store` default, ruled 11a; helpers enabled → *socket_path* / *log_path*
given), so their binds do NOT appear otherwise — exactly as the per-family path emitted them only
inside their conditional block.

**`guarantee_create`** (default True — a LAUNCH) gates the core table's create-if-missing of the
vault source dirs. A READ-ONLY consumer of this resolve — `box show --effective`, which renders the
resolved categories — passes False: it must show what a launch WOULD mount without making it so.
Nothing else about the resolve differs.

**`include_base_families`** gates the always-available tables (core / kani / channel / common /
seeded). It is True for the MAIN launch snapshot and False for the late, conditional image/helper
resolves (whose box_dests are disjoint), so the image/helper resolve carries ONLY their own table
plus any config-file keys — byte-for-byte the old per-family `_build_image_mounts` /
`_build_helper_hub_mounts` resolve, which injected only that one table.

**`persona_values`** is the persona store's LIVE tier for this launch (`_persona_values_for`) —
`endpoint` / `model` / `secret_path.<VAR>` / `env.<VAR>`, un-discriminated. It is given by every
resolve that has to AGREE with the launch about what the store says: the MAIN launch resolve, the
`--effective` display, and the two CREATE-side resolves that run the same load-or-error gate
(`persona_create_verdict`, `seed_new_box`). ⚑ The create side is not optional — it used to see a
persona's endpoint only because the create-side store IMPORT had just persisted it; nothing persists
it now. The tier also carries the token MOUNT and the env delivery, so a resolve that omits it would
show a persona box without the credential the launch actually mounts.

The NARROW resolves leave it `None` deliberately: the image / helper tables
(`include_base_families=False`, no target) resolve box_dests disjoint from anything a persona
touches, and the SEED resolve is FILE DELIVERY only, where a behavior scalar or a token pointer has
no meaning. `None` is byte-identical to a pre-persona build. ⚑ The CREATE-time SYNC resolve
(`_sync_box_at_create`) is NOT among them and DOES carry the tier: it is a FULL resolve whose whole
product is the bind map, and a persona's `secret_path.<VAR>` is a MOUNT — a map built without it
would resolve a sync dest against a mount set the launch does not have.

**`cli_level`** is the §1A CLI LEVEL (P8), built by `settings.settings_cli_level.build_cli_level` and
validated inside `build_launch_snapshot`. This is the ONE resolve that may carry the EPHEMERAL flag
values (`-M` / `-N`-`-C`-`-R`) as well as the resolved selection, because its output is this launch's
argv / env / mounts and nothing here is written back to a settings file. The seed, persona-endpoint
and `--effective` resolves take a selection-ONLY level.

**`cli_env`** is the parsed per-run `-e` map (P4c-1) and is forwarded UNTOUCHED to the collapse, which
applies it as the CLI level over the key owning each variable. ⚑ SAME RULE AS *cli_level* AND FOR THE
SAME REASON: only THIS resolve takes it. A `-e` value is not configuration — it belongs to one launch
— so a resolve whose product is a stored map or a display (`_sync_box_at_create`,
`box show --effective`) must not see it, and the narrow resolves write no env leaf to put it in. ⚑ It
is NOT a settings level and cannot be folded into *cli_level*: see `store_collapse._apply_cli_env`
for the measured reason.

**`realize`** is the launch's REALIZATION callback (P4c-2, a `_LaunchRealizer`) — the derivation of
everything this launch computes FROM the resolved cascade. It runs between the snapshot build and the
entry adaptation because that is the only point where its input exists and its output is still in
time: the variables it derives are written into the snapshot as `agent.<node>.env.<VAR>` keys, so
`snapshot_category_entries` adapts them and the collapse arbitrates them like any declared key.
⚑ SAME RULE AS *cli_level* / *cli_env*: only THIS resolve takes one. A realization is what a launch's
flags produce, so a display or a stored map must not carry it (`box show --effective` deliberately
does not gain them; deriving without the launch flags would show a tier the launch may not ship). It
also carries *desc*'s access tiers back to the caller — see `LaunchRealization` for why re-deriving
them there would be a second site.

---

## `_seed_box_home` / `_apply_init_seeds` — the ONE-TIME home seed, at CREATE

⚑⚑ **Read `~/canon/notebook/procedures/seed-and-create-model.md` before touching either.** A box is
seeded ONCE, atomically with registration, at `create` — never at first `start` — and thereafter the
home BIND owns the content: never re-seed. The create path's journal-entry branch deliberately
differs from that model; it reads like a bug, is not one, and deleting it deletes interrupted-create
recovery.

### `_seed_box_home` — three ordered, create-if-absent steps

It is the SINGLE seed implementation, shared by `box create` (`run_create`) and the `start`
auto-create path. It runs ATOMICALLY-after registration (the caller gates on the just-registered
signal, `proj.is_new`) and is NEVER run on a relaunch of an existing box. The three steps are ordered
and create-if-absent, so a re-create into a leftover dir never clobbers user content:

1. the descriptor's one-time credential seed (descriptor-bearing targets only; a descriptor-less /
   no-agent target has nothing to seed here);
2. the configured copy-once-at-init `seeded` category winners — INCLUDING the layered `seeded[~/]`
   trio (system/base → agent → workset; later overlays earlier, per-file last-wins). The separate
   on-disk template staging route is RETIRED (Q1): the template layers are now ordinary keystore
   `seeded` keys resolved off the committed snapshot in `_apply_init_seeds`, which stages the
   `~`-targeted layers in scope order there;
3. the box's own HANDBOOK CHAPTER — a HOST-side template copy, NOT a seed and NOT a `seeded` category
   entry (Jei, 2026-08-07g: the handbook templates *"do not DIRECTLY interact with the box itself …
   They are HOST templates, not GUEST templates"*). A SIBLING of step 2, deliberately outside it: it
   fills `@box.canon/handbook`, which no guest ever sees — the RO `canon_hb_box` BIND is what
   delivers it at `~/canon/handbook/box`, and that bind is an ordinary key, so single-route is
   intact.

⚑ **Step 3 is THE ONLY ROUTE THAT FILLS THE BOX HANDBOOK CHAPTER.** Step 2 does NOT: the three
handbook seed layers went out with the ruling, so nothing declares a `seeded` entry at
`@box.canon/handbook` any more, and a box created without step 3 gets an EMPTY chapter. This is not a
redundant second copy — do not "simplify" it away by folding it back into step 2.

⚑⚑ **The create-time `synced` write is NOT a fourth step here** — it is the SIBLING
`_sync_box_at_create`, which every caller runs immediately after this returns. Two reasons it must
stay outside: a sync is an OVERWRITE, so folding it in would falsify the create-if-absent contract
stated above; and this function's resolve is deliberately NARROW (no bind map), while the sync needs
a FULL one. Housing both in one function is a standing invitation to "simplify" by widening the
narrow resolve, which is exactly what must not happen.

The per-launch credsync REFRESH and the channel guarantee-create are SEPARATE per-launch mechanisms
and are NOT part of this one-time seed.

### `_apply_init_seeds` — the applier, and why it RETURNS its snapshot

It copies the configured copy-once-at-init seeds into the new project's shell dir, and RETURNS the
expanded launch snapshot this resolve built, so the caller's SIBLING ordered steps (`_seed_box_home`
step 3, the HOST-side handbook template copy) read resolved keys off the SAME snapshot instead of
building a second one — two builds could disagree about a repointed `workset.template`, and this
function already runs the one resolve that has the answer.

It is ADDITIVE: with no seed config and no target default seeds, it copies nothing. It routes the
category config through the ONE launch resolve and applies the COLLAPSED SEED LIST it stored at
`meta.assembly.seeded` (cutover step 2b-2, via `_launch_seed_list`), translating each guest dest
(`/home/agent/X`) to a host path under `proj.shell_path` and copying its source → that path once.

⚑ **A COLLAPSED SEED ROW IS `(src, dest, opts)` AND NOTHING ELSE** — no name, no category. The DEST
is the row's identity (R-10), so it is what a warning names, and there is no category to filter on:
the seed arm and the sync arm are two SEPARATE leaves now, not one list with a discriminator. ⚑⚑ Both
leaves are still COPIES and stay copies; do not let either become a mount.

### The layered `seeded[~/]` trio flows through this same route

The trio (system/base → agent → workset; spec §2a, Q1) flows through THIS route too — no separate
on-disk staging pass. The template layers all target `~` (the create-time home); the collapse's
seeded arm keeps every same-dest COPY (copies OVERLAY, they do not shadow —
`settings.store_collapse.collapse_seeded`), so here the `~`-group is a list of layer sources in scope
apply order that `launch.templates.stage_layers` stages PER-FILE LAST-WINS then copies into home
CREATE-IF-ABSENT, never clobbering user content. A layer whose source dir is absent (an unpopulated
`@workset.template`, say) is skipped.

⚑ **`seeded[~/]` IS THE SPELLING OF THE TRIO** — spec §2a's own seed-table form, and the one this
tree uses everywhere it names them (`cli`, `runtime.container`, `settings.settings_categories`, and
`launch.templates`, which DECLARES them in `template_seed_defaults`). It reads: the TERMINAL key
`<scope>.seeded` (`system` / `agent.<a>` / `workset`), at the one entry its dest-keyed map holds — the
guest home `~/`, whose source is that scope's `template` scalar plus `box/home`. ⚑ There is NO
`seeded.template` key and there never can be: since 2026-08-08c the destination IS the entry's
identity (R-10), so an entry NAME is not part of the keyspace. Do not reintroduce the old label as a
nickname.

### The credential gate, and ONE destination namespace

The credential gate (D-M4) is applied ONCE, inside the resolve this pass reads, above every consumer
of the entry list, the collapse included (`settings_categories.gate_credential_delivery`): a
credential-flagged `seeded` entry is suppressed for a PRIVATE box (*deliver_creds* False).

⚑⚑ **ONE DESTINATION NAMESPACE** (spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*; 2026-08-08c). Every
`seeded` dest is GUEST-spelled — the three §2a seed keys target `~/`, and a user-declared entry
targets whatever guest path it names — and ALL of them are resolved to the box store by the ONE
translator `container._guest_dest_to_host`, whose `map_home_root=True` maps the bare home to
`proj.shell_path` (= `<box_dir>/home`, the very directory the old host spelling named). There is no
branch here and nothing for an entry to carry: see `settings_categories.CategoryEntry` for the
mis-landing bug the retired host arm existed to close, and why the respell closes it at the source.

⚑ **THE BOX HANDBOOK CHAPTER IS NOT SEEDED HERE**, and looking for it in this function is the wrong
place: the three handbook seed layers were retired 2026-08-07g (HOST templates, not GUEST templates),
so no declared `seeded` entry names that dest. `@box.canon/handbook` is filled by the SIBLING step 3,
`_install_box_handbook`, off the snapshot returned above.

---

## `_core_env_default_categories` — the four `KANIBAKO_*` stamps, as launch-DERIVED `system.env.*` keys

⚑⚑ **THE ONE PLACE THEY ARE SPELLED** (MBR-1 P4b, ruling 2026-08-14: *"the 'KANIBAKO_' stuff should
be in system.env"*). They used to be assigned onto the finished container env by
`_assemble_launch_env`, ABOVE every settings file and above `-e`; they are ordinary system-scope
floor keys now, so they enter the ONE channel with everything else — a nearer `system.env.<VAR>` file
entry overrides one, a TWIN at another scope REFUSES the launch naming both keys
(`store_collapse.collapse_env`), `-e` reaches them (since P4c-1 by overriding the stamp's own key
VALUE inside that same collapse), and `box show --effective` shows them. Nothing above the channel
may write these variables; adding a fifth stamp anywhere else is the bug this function exists to
prevent.

The values are RESOLVED LITERALS, not `@`-refs (the `meta_identity_floor` pattern): what a launch
derives, it derives once, here.

Contracts the signature cannot carry:

* **`KANIBAKO_NAME`** is the peer-communication instance identity and is emitted only for a NAMED
  project — an unnamed one leaves the variable unset.
* **`KANIBAKO_DIRECTIVE_SEED`** is UNCONDITIONAL even though its kickoff BIND is descriptor-gated: a
  no-agent box gets the variable today and must keep getting it. 🛑 Do NOT "tidy" it onto the bind's
  gate. The path is READ BACK from the ONE declaration of the slot (`core-defaults.yaml`
  `kickoff.box_dest`, the source of core's own bind) rather than spelled a second time — the variable
  and the bind MUST name the same file. Global and agent-independent: the per-agent FINAL slot is
  each plugin's own `agent.<agent>.env.KANIBAKO_DIRECTIVE_FINAL`.
* **`KANIBAKO_AGENT`** is the resolved agent stamped ON THE CONTAINER and never in durable config
  (that is what keeps `--agent` ephemeral): a later `kanibako start` against the running box, `stop`'s
  writeback and the creds watcher all read it back rather than re-running the selection cascade. It
  carries the NODE identity (full persona), NOT the harness (`target.name`) — but in the OUTSIDE
  spelling (`+`, via `display_agent_ref`), because an env var is a place a HUMAN looks: the shipped
  ROM directive tells an in-box agent to read `$KANIBAKO_AGENT`. 🛑 **Readers CANONICALISE, THEN
  derive** — `harness_of` splits on `℘` alone, so deriving from the raw stamp returns the whole
  string and `resolve_target` hunts a plugin that does not exist (in `stop.py`, under a blanket
  catch: writeback stops SILENTLY). Canonicalising on read is also what keeps a box stamped by an
  older version working, since both separators are accepted. For a bare agent every spelling here is
  one string. Emitted only for a REAL agent
  launch, which is why *target* is the gate: a no-agent / shell launch carries no agent and the
  variable stays unset.
* **`KANIBAKO_AGENT_MARKERS_DIR`** is UNCONDITIONAL: both the E2b/E2c supervised path and the warm-up
  panel watch enumerate the dir that every agent session's start hook writes its per-PID marker into,
  and the supervisor reads the SAME dir via `--agent-markers-dir`. Harmless where no marker hook is
  seeded.

---

## `_broken_standalone_error` — the one state `_unbuilt_box_error` structurally cannot see

A standalone box lives entirely inside the user's own directory: the root is theirs, and `box_data/`
is the only thing kanibako puts there. When `box_data/` is deleted the REGISTRY entry survives, but
`resolve_standalone_project` can no longer read an identity out of the tree, so the box resolves
NAMELESS and the explicit-create gate reads it as "no box at all" — which `_unbuilt_box_error` cannot
see, because a standalone box's `metadata_path` IS the user's project root and that is still there.

Without this branch that state falls through to the generic "no box" message, whose suggestion is
built from the user's own spec — so a bare standalone NAME produces `kanibako create <name>`, and
running THAT creates a directory literally named `<name>` in the CWD with a PRIMARY box in it
(`commands/box/_parser.py` uses the spec as a path). Two grammars, one token: `start` resolves a bare
token in the NAME grammar, `create` resolves it in the PATH grammar.

### Each clause of the cure is load-bearing

⚑ **The `box_data/` clause** guarantees the branch can never fire while a live box tree is on disk,
which is exactly what makes the suggested `box rm` safe to name: with `box_data/` gone,
`_rm_standalone`'s deregistered-park arm is gated OFF (its `metadata_dir.is_dir()` test fails), so
the `rm` drops the registry entry and touches NOTHING on disk. Widening the gate would turn the
suggestion into the destructive parking variant.

⚑ **`--name`** — `validate_standalone_name` / `resolve_standalone_name` accept a verbatim canonical
`<kuid>_<leaf>` and return it AS-IS, so re-creating with the old name preserves the box's kuid, its
channel address and every stored reference to it. Omit it and the box comes back under a NEW kuid.
Both commands are on ONE `&&` line so the pair cannot be half-followed — the `rm` also FREES the name
the `create` then asks for, and without it the `create` refuses.

⚑ **`--register`** is what MAKES `--name` load-bearing, and the cure is void without it. Since
I3/§D4a `create --standalone` registers only on request and drops `--name` entirely when it does not
(`run_create`'s `standalone_name`), so the two-flag pair is indivisible: omit `--register` and the
name never reaches the resolver, the kuid is regenerated, and the box this branch describes comes
back as a different one that is also no longer in the registry that named it.

⚑ **The CURE LINE** is the part a future `repair` verb replaces (`tasks.md` MBR-6), exactly as in
`_unbuilt_box_error`, whose three-part shape (`Error:` / *why we won't* / `Rebuild it:`) this
deliberately reuses so standalone stops being the one mode that answers differently.
