# `commands/start.py` — overflow prose

⚑ **PARTIAL BY DESIGN.** This file is written OPPORTUNISTICALLY, as `start.py` is touched. It does
not describe the module as a whole; each section names the seam it covers and nothing else.

---

## `_launch_bind_map` / `_bind_map_from_mounts` — the emitter consumes the SHAPE (cutover 2a-2)

**Authority:** `plans/2026-08-09d-CUTOVER-PLAN.md` §2a-2-SHAPE (decided before dispatch) · §2.0e (the
narrow resolves have no collapsed node) · §2.0g (the four fields the collapsed shape drops).

### What the switch is

The MAIN launch path now emits its category mounts from `meta.assembly.bindings` — the collapse's
dest-keyed `CollapsedBind(src, opts)` map. `reconcile_categories` still runs and still computes its
whole answer; nothing is deleted here. What moved is WHERE the main path's rows come from.

### Why the emitter takes a map and not a `ReconciledCategories`

The two narrow resolves (images, helper hub) run with `include_base_families=False`, so they carry no
home bind, so the collapse writes them no node at all — pointing the shared emitter at
`meta.assembly.*` from the inside would empty their mounts silently. Making the emitter accept EITHER
shape is two forms with one meaning (Convention 0), and adapting a collapsed map back into
`CategoryEntry`s is impossible rather than merely ugly: `category`, `scope`, `name` and `optional` are
gone by construction. So the emitter takes the SHAPE — dest → `(src, opts)`, plus its dest-keyed
policies — and each caller supplies that map from wherever it has one. `_bind_map_from_mounts` is the
translation for a caller whose answer is a reconciled winner list; it is not a collapse (no home
foundation, no arbitration, no scope fold).

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
  descriptor's `BindScope.AGENT_CRITICAL` dests. A narrow resolve still drops the agent rows before
  handing its map over (`_narrow_bind_map`), but that is a caller selecting its own rows, not the
  emitter branching on a field.
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

### The FALLBACK, and when it dies

`_launch_bind_map` reads the leaf and falls back to the reconciled rows when it is ABSENT. That is not
a preference between routes: the collapse refuses configurations the live route accepts and leaves all
three leaves unwritten, and until step 2c that refusal must reach nobody. **The fallback and the
`SettingsError` swallow in `_install_assembly_collapse` come out together.**

⚑ **The step-2c precondition, measured and then CLOSED:** `start_mocks` stubbed
`_resolve_launch_snapshot` with a category set carrying no home bind, and — the half that a reading of
the category set alone misses — the stub never called `_install_assembly_collapse`, which lives in the
orchestrator it REPLACES. Either half alone leaves all three leaves absent, so every `_run_container`
unit test took the fallback and deleting the fallback would have emptied the category mount set for
that whole suite at once. The fixture now carries the core home row AND mirrors the orchestrator's
tail (gate → reconcile → collapse, off the same gated list), so those launches read a real
`meta.assembly.bindings`. **Measured delta on the emitted mount set: `+ /home/agent` and nothing
else** — home is lifted out before any scope folds, so its options stay `Z,U`, and the agent delivery
binds fold `fold_opt("ro", "ro") == "ro"`, byte-identical to the fallback.

### One measured behavioural difference, pinned rather than smoothed

The five-arm shape carries ro/rw as the ARM, so the collapse folds the mode back into the option
string: a rw bind the reconciled route emits as `Z,U` arrives as `Z,U,rw`. Podman's default IS rw and
`fold_opt` dedupes, so `ro` stays `ro` and nothing about the box changes — but the option string
podman receives does. Home is the exception by construction: pid 0 is lifted out before any scope
folds, so no arm ever appends to its options.

---

## `_install_assembly_collapse` / `_split_home_bind` — the collapse wiring (roadmap step 6b)

**Authority:** Jei's roadmap step 6, verbatim — *"implement a 'grand unification function' … that
will **merge the information, but not perform the action**"* ·
`designs/collapse-implementation-DESIGN.md` §0/§1 · `designs/grand-unification-collapse-DESIGN.md`
§2a (home is pid 0).

### What it is

`_resolve_launch_snapshot` folds the same `CategoryEntry` list the live route already produced
(`snapshot_category_entries`) through the step-4 producer (`build_store_shape_set`) and the step-6a
collapse (`collapse_store_shapes`), and stores the results at the declared RO/derived keys
`meta.assembly.{bindings,seeded,synced}`.

### What it drives, and what it still does not

🛑 **UPDATED AT CUTOVER 2a-2, AGAIN AT 2b-2, AND AGAIN AT 2b-3 — this section used to read "it drives
nothing", and that is now false for MOUNTS and for BOTH copy arms.** The main launch path emits its
category mounts from `meta.assembly.bindings` (see the section above), the create-time seed applier
reads `meta.assembly.seeded`, and the launch-time sync applier reads `meta.assembly.synced` (both
below). What still runs on `reconciled`: the env set, the row-5 warnings, the agent delivery arm, and
the remaining narrow resolves. Retiring `reconcile_categories`' arbitration half and the warn channel
is step 5, and none of it may be smuggled in early.

That is also why the wiring reuses the existing walk rather than adding a second one: two walks
could disagree about what was declared, and only one of them would be the one that ships.

### The credential gate is HOISTED above BOTH consumers (cutover 2b-0)

`reconcile_categories` applies the D-M4 credential gate INTERNALLY, so before 2b-0 a PRIVATE box
(`deliver_creds=False`) got `reconciled.copies == []` while `_install_assembly_collapse` — handed the
RAW entry list two lines later — folded the credential rows into `meta.assembly.synced` anyway. The
divergence was inert only because nothing consumed that leaf. Pointing a consumer at it would have
delivered every `synced` credential into a box the user made private, reversing D-M4.

`_resolve_launch_snapshot` therefore calls `settings_categories.gate_credential_delivery` ONCE and
hands the SAME gated list to the reconcile and to the collapse. The gate inside
`reconcile_categories` stays: it is idempotent, it is the rule's one spelling (the hoist calls the
same function), and it still guards every OTHER caller. Removing it is step 5.

⚑ The gate runs AFTER `_install_derived_bindings`, not before. A derived binding materialises a
property of the DECLARATION (R-8) — `binding_derivations` records what was declared, not what this
box is allowed to receive.

⚑ The `seeded` half of the gate is LATENT: `CategoryEntry.is_credential` has no production producer
today (only tests set it), so on a real launch the gate drops `synced` rows and nothing else. Both
halves are gated regardless — a gate that covers one of two arms is the shape that produces this
class of bug in the first place.

### Home is pid 0, so it is lifted OUT of the fold

`collapse_store_shapes` seeds `combined_bindings` with home BEFORE any scope folds, and takes it as
its own parameter. A `store_shape` that also carried home would therefore collide with the seed on
the very first scope. `_split_home_bind` removes the home mount from the entry list and hands it
over separately.

The home entry is identified by its DESTINATION — the one MOUNT entry with a source whose
`normalize_bind_dest(box_dest)` is `store_collapse.HOME_DEST`. Not by key, not by category, and
never by splitting a dest on `.`: a destination is data.

**Zero or several such entries ⇒ no BINDINGS and no SYNCED leaf.** There is then no pid 0 to build
on, so there is no assembly to describe. In practice this is what silences the narrow resolves
(`box show --effective`'s families-off siblings, the conditional image and helper resolves) for
those two leaves: they carry image and helper binds only, and no box home.

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
| `bindings`, `synced` | the seed arm folds AND there is exactly one home bind AND the bind fold does not refuse |

⚑ **ABSENT and EMPTY are different answers.** The seed leaf is written even when the list is empty,
so a consumer reading `None` learns the collapse REFUSED — never that this box seeds nothing.

⚑ **A BIND-FOLD refusal does not erase the seed list.** The refusal says nothing could assemble this
box; it says nothing about an arm the refused bind never touched, and the seed list was already
folded successfully when it fired. Erasing it would make a subsuming bind silently cost a box its
seeds the moment the create path reads this leaf — the same class of latent hazard 2b-0 closed on
the credential side. A SEED-ARM refusal (a seed outside home) is different: that leaf did not fold,
so nothing is written at all.

⚑ The `SettingsError` swallow itself is UNCHANGED in kind — still a `debug` log, still never fails a
launch. Only its blast radius narrowed to the leaves each raise actually invalidates. Turning it
into a hard error is step 2c.

⚑ `collapse_store_shapes` recomputes the seed list in the home branch and the result is discarded.
That is deliberate: it is the same pure concatenation over the same shapes, so it cannot differ, and
one implementation of the seed rule is worth more than one saved traversal.

⚑ The home bind row itself (`data/core-defaults.yaml`, `core_defaults.add_bind`'s home arm) is
UNTOUCHED. Re-pointing it at the ratified `meta.box.home` key binds home on every launch and needs a
real-podman e2e; it rides with the cutover.

### A collapse refusal MUST NOT fail a launch

The collapse enforces refusals the shipped route does not: a bind may not subsume a bind, nor sit
inside a mask, and a copied DIRECTORY may not take a mask's exact point. Today's
`reconcile_categories` permits nested binds — it depth-sorts them and errors only on two concrete
declarations at one IDENTICAL dest — so **configurations exist that launch fine and make the
collapse raise.**

Those refusals are intended; enforcing them is simply premature. So `SettingsError` out of the
collapse is caught at this one seam: all three leaves stay ABSENT (the state the manifest already names
for them — *"declared so the closed keyspace admits the name"*), the launch continues on the
unchanged live path, and the cause is logged.

**The log level is `debug`, deliberately.** A `warning` would tell a user their configuration has a
problem when it does not: it is legal on the route that ships, and the computation that rejected it
changes nothing they can observe. The message is for whoever is building the cutover.

⚑ A partial write is worse than no write, which is why all three leaves are installed only AFTER the
collapse returns — a half-built `meta.assembly.bindings` with no `copies` beside it would describe a
box nothing could assemble.

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

`_launch_bind_map` still falls back to the reconciled rows while the leaf is absent (see "The
FALLBACK, and when it dies"), so calling it twice is not the same as calling it once: nothing
guarantees two reads answer from the same arm, and the failure would be silent and per-launch.
Reading once makes the two arms agree by CONSTRUCTION rather than by both being careful (P3).

### What this changes for a box

The collapse arbitrates masks against binds; the old mask arm did not arbitrate at all. Where the
two disagree, the map now decides both halves:

* a **bind nested under a mask** is swept, and the mask survives — already true of the bind arm
  since 2a-2 (MIGRATION §2.27); the mask arm now agrees with it instead of being computed elsewhere;
* a **bind at a mask's own destination**, declared in a LATER scope, sweeps the mask and takes the
  point. The reconcile resolves that same collision the other way (§0 row 2: a mask OVERRIDES a
  binding at its dest), so between 2a-2 and 2a-4 the launch emitted BOTH — a `-v` bind and a
  `--mount type=tmpfs` at one destination — where it now emits the bind alone;
* a mask **at or above home**, or **on another mask**, is REFUSED by the collapse, which leaves the
  leaf absent and drops the whole launch to the fallback: masks and binds both come from the
  reconciled rows there, exactly as before. Nothing about a refusal is mask-specific.

⚑ The dests are the map's KEYS, so they are `normalize_bind_dest`-spelled (`/home/agent/x`, never
`~/x`) and the arm is depth-sorted on `path_depth` — the same key the emitter sorts on, so the tmpfs
and the binds reach podman in one order.

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
`reconcile_categories` arbitrates copy-vs-copy at a shared dest (`_resolve_copy_group` returns the
sync and drops every seeded row); `collapse_seeded` did not, because nothing read it. Reading the leaf
without that rule lets a seed write a credential dest FIRST with a PRESERVED mtime, after which
`_synced_uptodate` skips the sync forever. A prune in `collapse_seeded` closed it for one commit and
was **removed by ruling on 2026-08-11**: `_sync_box_at_create` now writes every sync row UNGATED right
after the create-time seed, so the dest holds sync-written bytes from creation onward and the launch
gate compares against the sync's own prior write. **Read `_sync_box_at_create` below, and
`llm-docs/kanibako/settings/store_collapse.py.md`, "Nothing is arbitrated at a destination".**

### The shape delta, and the three things it moved

A reconciled winner is a `CategoryEntry` carrying `category`, `name`, `box_dest`, `host_src`,
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

### The FALLBACK, and when it dies

Identical in shape and in reasoning to `_launch_bind_map`'s: `_launch_seed_list` reads the leaf, and
falls back to the reconciled `seeded` winners when it is ABSENT. A refusal must reach nobody until
step 2c. **It is also the one arm that can still produce a seed row the collapse would have refused**
— a dest outside the guest home — which is why the applier keeps its outside-home guard even though
`_refuse_seed_outside_home` makes that guard unreachable from the leaf.

⚑ **ABSENT ≠ EMPTY, and here the distinction is the data.** `_snapshot_assembly_seeded` returns
`None` for absent and `[]` for empty. Collapsing the two would make a refusing configuration seed a
brand-new box with NOTHING, silently, at `debug`.

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
_apply_synced_copies(snapshot=_snapshot, reconciled=reconciled, bindings=launch_binds, logger=logger)
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
name `_snapshot`, `reconciled` or `launch_binds` — they are assigned further down the same function —
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
| cover is a MASK → warn+skip | `is_mask(bind)` | **must precede any `Path(bind.src)`** — `MASK` is `src=None`, so it raises `TypeError`, not `AttributeError` |
| cover is READ-ONLY → warn+skip | `is_read_only(bind.opts)` | see below |
| else | — | `Path(bind.src) / rel` |

⚑ **A dest is DATA** — compared and sliced as a path, never `.split(".")`-ed.

🔴 **The MASK arm exists because the collapse ACCEPTS what delivery then SKIPS.**
`_refuse_sync_at_a_bind_dest` returns early when the occupant has no source, and a mask IS the
source-less entry — so a sync at a mask's exact point falls through the refusal by construction. That
acceptance is spec-silent and was an implementer's call, not a ruling; delivery has to cope with it.

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

### 🛑 The category filter SURVIVES, in the fallback arm only

`_launch_synced_list` mirrors `_launch_seed_list`, and the `row.category == "synced"` test inside it
is **not** a leftover from the pre-cutover route. `reconciled.copies` is ONE list holding BOTH copy
categories, so the arm that reads it must still say which half it wants. The seed switch could delete
its filter because it stopped reading that list; this one still reads it on the fallback path.
**Deleting it applies every `seeded` row as an OVERWRITE, on every launch, over content the box owns**
— mutation-proved: the box's own file is clobbered back to the seed's bytes.

The fallback itself is reachable today: `_install_assembly_collapse` writes neither the bindings leaf
nor this one when the fold refuses or there is no single home bind, and swallows the cause at `debug`.
**The arm and the filter inside it come out at step 2c with that swallow**, together with
`_launch_bind_map`'s and `_launch_seed_list`'s.

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
