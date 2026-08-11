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

⚑ **A step-2c precondition, measured here:** `start_mocks` stubs `_resolve_launch_snapshot` with a
category set carrying no home bind, so under that harness the collapse writes nothing and every
`_run_container` unit test takes the fallback. Deleting the fallback before the harness grows a home
bind would empty the category mount set for that whole suite at once.

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

🛑 **UPDATED AT CUTOVER 2a-2, AND AGAIN AT 2b-2 — this section used to read "it drives nothing", and
that is now false for MOUNTS and for the SEEDS.** The main launch path emits its category mounts from
`meta.assembly.bindings` (see the section above), and the create-time seed applier reads
`meta.assembly.seeded` (see below). Everything else still runs on `reconciled`: the `synced` copies,
the env set, the row-5 warnings, the mask arm, the agent delivery arm, and both narrow resolves.
Retiring `reconcile_categories`' arbitration half and the warn channel is step 5, and none of it may
be smuggled in early.

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

🐞 **THIS SWITCH REMOVED AN ARBITER, AND THE SAME COMMIT PUT IT BACK.** `reconcile_categories`
arbitrates copy-vs-copy at a shared dest (`_resolve_copy_group` returns the sync and drops every
seeded row); `collapse_seeded` did not, because nothing read it. Reading the leaf without that rule
lets a seed write a credential dest FIRST with a PRESERVED mtime, after which `_synced_uptodate`
skips the sync forever. The prune now lives in `collapse_seeded` — **read
`llm-docs/kanibako/settings/store_collapse.py.md`, "A sync OWNS its dest"**, for why that is the
only home that works and why it is a reproduction rather than a spec change.

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
