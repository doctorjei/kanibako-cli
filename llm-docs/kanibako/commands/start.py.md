# `commands/start.py` — overflow prose

⚑ **PARTIAL BY DESIGN.** This file covers the seams of `start.py` whose reasons do not fit in the
source. It does not describe the module as a whole; each section names the seam it covers and
nothing else.

---

## `_launch_bind_map` / `_bind_map_from_mounts` — the emitter consumes the SHAPE

**Authority:** `~/canon/workbook/designs/collapse-implementation-DESIGN.md` ·
`~/canon/workbook/designs/store-shape-producer-DESIGN.md` §7.4, §9.1.

The MAIN launch path emits its category mounts from `meta.assembly.bindings` — the collapse's
dest-keyed `CollapsedBind(src, opts)` map — and nothing else. `_launch_bind_map` is total on both
of its callers by construction: each resolves with the base families on, and a whole-box resolve
either wrote the leaf or REFUSED by name. An absent leaf is a WIRING fault (a consumer handed a
narrow snapshot), so the readers return an option and each consumer refuses `None` with a named
`SettingsError` rather than an `AttributeError` inside the emitter.

### Why the emitter takes a map and not a winner LIST

The three narrow resolves (images, helper hub, spawn budget) run with `include_base_families=False`,
so they carry no home bind and the collapse writes them no node at all. Adapting a collapsed map
back into `CategoryEntry`s is impossible rather than merely ugly — `category`, `scope`, `name` and
`optional` are gone by construction — so the emitter takes the SHAPE (dest → `(src, opts)`, plus its
dest-keyed policies) and each caller supplies that map from wherever it has one.
`_bind_map_from_mounts` is the translation for a caller whose answer is a MOUNT winner list —
`_narrow_bind_map`, over `settings_categories.narrow_table_winners`' answer. It is not a collapse
(no home foundation, no scope fold).

### What the collapsed shape does not carry, and where each fact went

* **`optional`** — a dest-keyed missing-source policy passed to the emitter (next section).
* **`category`** — the mask arm needs none: a mask IS `CollapsedBind(None, None)`, so "no source" is
  the test. `secret_path` carries no arm in the disk-store shape, so it is not in the map
  (`_bind_map_from_mounts` drops it for the same reason — shape fidelity, not a policy filter);
  `_emit_secret_mounts` delivers it from `LaunchDeliveries.secrets`.
* **`scope`** — not needed: ONE emitter walks ONE map. What the agent binds need is a per-dest
  MISSING-SOURCE POLICY, which is dest-keyed. A narrow resolve selects its own rows before handing
  its map over (`_narrow_bind_map`, by its own table's DESTS) — a caller choosing what it emits, not
  the emitter branching on a field.
* **`name`** — under dest-keying `CategoryEntry.name` IS the destination (R-10), so a warning names
  the dest, spelled normalized (`/home/agent/canon`) rather than as declared (`~/canon`).

### Emission owns the depth-sort

A dest-keyed map carries no order — `store_collapse` says so at the type. The emitter sorts on
`settings_categories.path_depth`, so podman receives shallow-first, deepest-wins.

### One behavioral detail, pinned rather than smoothed

The five-arm shape carries ro/rw as the ARM, so the collapse folds the mode back into the option
string: a rw DECLARATION whose own options read `Z,U` arrives as `Z,U,rw`. Podman's default IS rw
and `fold_opt` dedupes, so `ro` stays `ro` and nothing about the box changes — but the option string
podman receives does. Home is the exception by construction: pid 0 is SEEDED before any scope folds
and is in no scope's shape, so no arm ever appends to its options.

---

## `_emit_category_mounts` — the MISSING-SOURCE POLICY travels as a parameter

One emitter, one walk, three per-dest answers to "the host source is not there":

| policy | parameter | who is in it | what happens |
|---|---|---|---|
| must-exist | `must_exist` | the descriptor's `AGENT_CRITICAL` dests ∪ core's packaged-canon dests (`core_defaults.rom_must_exist_dests`) | raise `BindingSourceError` → clean exit-1 |
| skip-if-absent | `skip_if_absent` | the canon chapter dests (`core_defaults.canon_silent_dests`) ∪ the agent's best-effort dests, minus must-exist | dropped at `debug` |
| warn-and-drop | *(default)* | everything else (including the handbook's `SYS_CONTENTS.md`) | ro warns and drops; rw guarantee-creates |

⚑⚑ **The policy is consulted BEFORE the rw guarantee-create, and that ordering is the whole point.**
`mkdir(parents=True, exist_ok=True)` would manufacture the very thing must-exist is asking about, so a
critical dest whose source vanished would get an empty directory bound over the agent's binary
instead of the safe-fail. Reading the policy first makes "mkdir'd into existence" unreachable (P3).
Pinned by `test_a_missing_critical_source_RAISES_before_any_mkdir`.

⚑ The agent's best-effort dests (`BindScope.AGENT`, carried as `LaunchDeliveries.agent_dests`) join
**skip-if-absent** rather than keeping a branch of their own: a missing or suppressed agent share is
fine. ⚑⚑ **Must-exist is SUBTRACTED where the skip set is BUILT**, not merely consulted first: a dest
in both would raise anyway, and the overlap would look correct at the emitter while being a
contradiction in the policy itself.

### Why the DEST is the carrier

`optional` was never a realization fact — it is a DECLARATION fact — and the collapsed map is keyed
by destination: by the time the emitter reads it the KEY is gone and the dest is not. So the policy
is dest-spelled, built with `normalize_bind_dest`, the same function `core_defaults.add_bind` keys
the arm with. A KEY-spelled set matches nothing and silently degrades every critical bind to
best-effort (a missing agent binary reaching podman as a crun crash instead of a clean exit-1), and
every chapter-less workset warns on every launch. Pinned by
`tests/test_seed_hostdest.py`'s `test_a_key_spelled_skip_set_matches_NOTHING`.

⚑ The policy is scope-blind: it fires for whichever scope wins that dest. The collapse drops scope on
purpose, and one dest can only carry one bind.

### It is passed at the MAIN emit only

The narrow emits filter to their own table's dests first (`narrow_table_winners`), so a canon or
agent destination is never in the map they are handed, and a policy there would decide nothing.

---

## `_install_assembly_collapse` / `_snapshot_home` — the collapse wiring

**Authority:** the roadmap's "grand unification function", which *"will merge the information, but
not perform the action"* · `~/canon/workbook/designs/collapse-implementation-DESIGN.md` §0/§1 ·
`~/canon/workbook/designs/grand-unification-collapse-DESIGN.md` §2a (home is pid 0).

`_resolve_launch_snapshot` folds the `CategoryEntry` list (`snapshot_category_entries`) through the
per-scope producer (`build_store_shape_set`) and the collapse (`collapse_store_shapes`), and stores
the results at the declared RO/derived keys `meta.assembly.{bindings,seeded,synced}` — plus
`meta.assembly.env`, which comes from `collapse_env` off the ENTRY LIST rather than the shapes. These
leaves drive the launch: the category mounts and the mask arm (`bindings`), the create-time seed
(`seeded`), the launch-time sync (`synced`), and the box's environment (`env`, read by
`_launch_env_map` and projected by `_build_config_env`, at the launch and at `box show --effective`
alike). The `secret_path` mounts, the agent-delivery dest set and the narrow bind maps read the
`LaunchDeliveries` carrier (below). There is no second, cross-scope reconcile pass.

### The same-scope warning lives HERE, and ONLY here

`build_store_shape_set` is the SOLE builder of a `CategoryCollision` in `src/`, and
`emit_collision_warnings(shapes.warnings)` is its only emission; the memo
(`_COLLISION_WARNED`, keyed `(box_dest, scope)`) reports each once per process. There is no second
feed and no field to re-add one to (P3) — `test_the_retired_routes_are_GONE` pins that structurally,
because the memo would hide a second feed from every log-counting test.

The producer folds each scope alone, so it warns where a cross-scope pick might have stayed silent:

| arrangement | what the producer says | what the launch does |
|---|---|---|
| a `masks` entry at that dest, in ANY scope | warns for the ambiguous scope | **works** — one extra warning line (CHANGELOG, Unreleased/Changed) |
| another scope's abstraction takes the dest | warns for the LOSING scope | refused by `collapse_store_shapes` — two mounts at one dest — so the line only ever precedes that refusal |

The mask row is pinned by
`tests/test_category_collisions.py::TestTheCollapseRouteFeedsTheSameChannel::test_a_MASKED_destinations_ambiguity_is_announced_where_it_once_was_not`.
⚑ The second row is measured, not pinned — no test drives a warning that precedes a refusal.

Anything declared at the home dest is an ordinary entry to the producer: it warns on an ambiguity
there as anywhere else, and the collapse then refuses the survivor against the seeded foundation.

⚑ **Whether the channel should exist AT ALL is a spec question, and still open.** §0's rule makes
two mounts at one destination an error "in every scope combination but one", and warn-and-proceed is
that one exception.

### The credential gate is HOISTED above EVERY consumer

`_resolve_launch_snapshot` calls `settings_categories.gate_credential_delivery` ONCE and hands the
SAME gated list to the collapse and to `launch_deliveries`. That hoisted call is the rule's ONLY
spelling: a gate applied inside one consumer and not the other would deliver every `synced`
credential into a box the user made private (`deliver_creds=False`), reversing D-M4.

⚑ The gate runs AFTER `_install_derived_bindings`, not before. A derived binding materializes a
property of the DECLARATION (R-8) — `binding_derivations` records what was declared, not what this
box is allowed to receive.

⚑ The `seeded` half of the gate is LATENT: `CategoryEntry.is_credential` has no production producer
(only tests set it), so on a real launch the gate drops `synced` rows and nothing else. Both halves
are gated regardless — a gate that covers one of two arms is the shape that produces this class of
bug.

### Home is pid 0, so the SEAM CONSTRUCTS it

Home never enters the entry list. `collapse_store_shapes` seeds its combined bind map with home BEFORE
any scope folds, and takes it as its own parameter; `_install_assembly_collapse` builds that
foundation itself —

```python
home_bind = BindEntry(_snapshot_home(snapshot), HOME_OPTIONS)
```

— from the RO DERIVED key `meta.box.home`, which `settings_launch.workset_anchor_floor` materializes
as `{meta.box.path}/home`. Home does **not** route through `bindings.rw` (spec §2c's `meta.box.home`
row). 🛑 **That key is the ONE spelling of the derivation.** Never re-derive the foundation from
`proj.shell_path`, and never re-inline `{meta.box.path}/home` here.

`HOME_OPTIONS` (`store_collapse`) is a BARE `Z,U` — no `rw` token. The foundation is pre-seeded into
the collapse map and never passes `_merge_bindings`' `fold_opt`, which is exactly what distinguishes
it from every rw bind that folds over it (`Z,U,rw`). Mount options are SEAM MACHINERY, not part of
any key — which is why the literal lives beside the seam and not in the keyspace.

There is no one-home guard, because neither failure is reachable: ZERO homes is unconstructible (the
key is floor-produced on every resolve, and `settings_launch._assert_box_root_resolved` refuses a box
root that did not resolve), and TWO is the collapse's `_refuse_bind_over_bind`, fired against the
foundation seeded beneath every scope's shape. `_snapshot_home` carries the TYPE the snapshot cannot
(a resolved leaf is `object`, and a non-string one would otherwise reach podman as a source).

⚑ **A settings-file entry at `~` is a second bind at pid 0's point and REFUSES.** The cure is
`workset.boxes` — MIGRATION.md § "You can no longer repoint a box's home with a binding".

### Each leaf rides its OWN gate

| leaf | written when |
|------|--------------|
| `seeded` | the seed arm folds |
| `bindings`, `synced` | the seed arm folds AND this is a WHOLE-BOX resolve AND the bind fold does not refuse AND every `synced` dest is COVERED |

The seed list is computable where no bind map is: `collapse_seeded` takes the shape set and nothing
else, and home is seeded before any bind folds (§2a). That matters, because `seed_new_box` — the
`box create` entry — reaches `_apply_init_seeds` on a NARROW resolve (`include_base_families=False`,
no box home), and must still read `meta.assembly.seeded`.

🛑 **THE GATE IS `whole_box`, NEVER "did `meta.box.home` resolve".** The key is materialized by
`workset_anchor_floor`, which the launch builds unconditionally, so it resolves on a NARROW resolve
too — gating on the value would write all three leaves for the narrow tables. `whole_box` is the
resolve's own `include_base_families`, forwarded rather than re-derived.

⚑ **ABSENT and EMPTY are different answers.** The seed leaf is written even when the list is empty.

⚑ **A BIND-FOLD refusal does not erase the seed list.** The refusal says nothing about an arm the
refused bind never touched, and the seed list was already folded when it fired. A SEED-ARM refusal (a
seed outside home) is different: that leaf did not fold, so nothing is written at all.

⚑ A partial write is worse than no write, which is why the two leaves that DESCRIBE an assembly are
installed only after the fold returns — a half-built `meta.assembly.bindings` with no sync list
beside it would describe a box nothing could assemble.

⚑ `collapse_store_shapes` recomputes the seed list in the home branch and the result is discarded.
That is deliberate: it is the same pure concatenation over the same shapes, so it cannot differ, and
one implementation of the seed rule is worth more than one saved traversal.

### The fourth field leaves as a RETURN VALUE, not a leaf

`collapse_store_shapes` produces four things and only three are written. The fourth,
`CollapsedStore.declared_by` (dest → the declaration key that took it), **may not become a
`meta.assembly` leaf** — that is a closed-keyspace addition, a spec and manifest edit. So
`_install_assembly_collapse` **returns** it: `{}` on a narrow resolve, the fold's map on a whole-box
one. `_resolve_launch_snapshot` puts it on `LaunchDeliveries.declared_by`, and `box show --effective`
reads it from there.

⚑ **That display FOLDS IN PROCESS**, per command (`commands/box/_parser.py` builds its own snapshot
through this same resolve), which is why the map it prints from is the SAME fold's as the
`meta.assembly.bindings` it pairs against. 🛑 It cannot be re-derived from the finished map: a sweep
drops a mask's row together with the mount it named, so two scopes' keys can name one destination
with only one of them the occupant.

### The coverage refusal sits between the fold and the leaves

By ruling [Jei:2026-08-28] — *"we should be checking that the paths resolve"* — immediately after
`collapse_store_shapes` returns and BEFORE the leaf writes,
`store_collapse.refuse_uncovered_synced(collapsed.bindings, collapsed.synced)` refuses a `synced`
destination no mount covers, so a refused launch never leaves an assembly a later pass could read
as accepted.

⚑ **THE RULE LIVES IN `store_collapse`; ONLY THE ASKING IS HERE.** Coverage is ANTI-monotone in the
scope set — a further scope can only ADD binds — so unlike every other collapse refusal it reads
differently against a partial map. `commands.workset_cmd` calls the same fold with a deliberately
partial one (no box tier, a stand-in home), where a `workset.synced` at `/opt/cred` measures
UNCOVERED while the launch that also carries `box.bindings.rw./opt` measures COVERED. This is the
first point that can honestly say the map describes a whole box.

### A collapse refusal IS the launch's

The collapse refuses: a bind subsuming a bind or sitting inside a mask, a mask taking another mask's
point or landing at or above home, a seed landing outside home, a sync taking a bind's exact point,
and a bind whose options contradict its arm. A refusal propagates out of `_resolve_launch_snapshot`
and exits through `cli.main`'s `except KanibakoError` arm as `Error: …`, rc 1, no traceback — so no
try/except is needed at the launch site, and `box show --effective` (which catches `KanibakoError`)
is the "check before you hit it" path. MIGRATION.md § "A mount set that cannot be assembled now
stops the launch".

⚑ **A cross-scope pair at ONE identical dest reaches `store_collapse._refuse_bind_over_bind`.** Its
remedy sentence is the §0 suppression text (*"To change what occupies a destination you must
SUPPRESS the entry you do not want …"*), but the message names host SOURCES and the dest and **no
declaration KEY**. Naming the keys is structurally impossible where it stands — `build_store_shape`
drops `CategoryEntry.key_segments` when it writes the arm — so closing it is a PRODUCER SHAPE change,
boarded in that function's own docstring.

---

## `LaunchDeliveries` — what the launch delivers BESIDE the collapse

**Authority:** `~/canon/workbook/designs/store-shape-producer-DESIGN.md` §7.4 (`secret_path` is
PARKED out of the disk-store shape) · §9.1 (what is not a settings key is PASSED).

`_resolve_launch_snapshot` returns `(snapshot, deliveries)`. The carrier holds what the collapse
deliberately does not carry, built off the SAME credential-gated list:

| field | read by |
|---|---|
| `secrets` | `_emit_secret_mounts` |
| `agent_dests` | the emitter's skip-if-absent set |
| `narrow_bindings` | a narrow resolve's emit (`None` unless the caller passed `narrow_bind_dests`) |
| `declared_by` | `box show --effective` |
| `dest_keys`, `convenience_sources` | the `--effective` display |
| `secret_names` | `persist_creation_env` (a creating launch's `-e`) |

§0's table is applied by three seams: the per-scope `store_shape` producer (all that is decidable
inside ONE scope), the assembly collapse (masks and every CROSS-scope pair), and this seam's
`secret_path_deliveries` / `narrow_table_winners` for the inputs the collapse cannot see.
`test_category_collisions.py::test_the_retired_routes_are_GONE` asserts the retired route's absence
structurally.

### The environment is ONE channel: `meta.assembly.env`

There is no `envs` field. Both consumers — `_assemble_launch_env` and `box show --effective` — read
`meta.assembly.env` through `_launch_env_map` and fold it with the same `_build_config_env`, so ONE
value per variable exists on either surface and the winner's `(scope, key)` travels with it. An
un-arbitrated second view of the same declarations is exactly what would let a variable named by two
scopes be settled silently by whichever entry a list happened to end with.

⚑ `_launch_env_map` is ONE function where `bindings`/`seeded`/`synced` each have two (an option
reader plus a total one). Neither env consumer can act on an absent leaf — both describe a box, and
the leaf rides the whole-box gate — so an option form would be a route nothing takes.

**Agent-file variables are ordinary keys.** `agent.<node>.env.<VAR>` cascades to its true rung
(above `system`, below `workset`), meets the expand pass like any value, and realizes through the
same collapse; `_build_config_env` is a straight projection with nothing layered under it. So an
agent-FILE variable and a `box.env.<VAR>` twin are two scopes' keys at one slot and REFUSE the
launch (spec, the `env.<VAR>` block: *"Two scopes' keys naming ONE variable REFUSE the launch, naming
both keys"*), while an agent-FILE variable and the plugin's declared default are the SAME key at two
cascade levels and simply cascade. The persona STORE's env rides the cascade too
(`settings_launch._persona_partial` splices it under `agent.<active>`), and every
`generate_agent_config()` returns an env-less config under the file-purity invariant.

**The four `KANIBAKO_*` stamps are system-scope floor keys** — see `_core_env_default_categories`
below. `_assemble_launch_env` stamps nothing. ⚑ The supervisor argv's `--agent-markers-dir` carries
`container_env.get(...)`, the resolved env value, so the box-side hooks (which read the env) and the
host-side supervisor agree under an override; the compile-time constant is only the shared fallback.

### `-e` is the CLI level of the env family

By [Jei:2026-08-14] (*"-e should override the key values, not the environment variables
themselves"*) and the spec's `env.<VAR>` block (*"`-e` beats a realization uniformly (the CLI level
overrides the owning key — §1A)"*): `_run_container` parses `-e` ONCE at its door, threads the map
through `_resolve_launch_snapshot(cli_env=)` → `_install_assembly_collapse` → `collapse_env`, and
the overlay runs there.

⚑ **THE OVERLAY RUNS AFTER THE CONTAINMENT WALK, AND THAT IS THE CONSTRUCTION** —
`store_collapse._apply_cli_env` carries the reason a reader must not "unify" it onto the CLI
*settings* level instead: `key_validity` refuses `cli.env.FOO` and the scope-less `env.FOO`, so such
a level would have to spell a CONCRETE scope, at which point `-e` becomes a second scope's key naming
the user's own variable and the twin refusal fires on exactly the configurations the flag exists to
serve. Applied after the walk, `-e` cannot CONTEST a slot: it overrides an owner or fills a vacancy.
An overridden slot KEEPS the owning scope + key; a vacancy gets `("cli", "-e <VAR>")`.

⚑ `_parse_cli_env` refuses an item with no `=` and a `=v` naming no variable, at the door before the
launch reads a file. The shape is the keyspace's plain env identifier plus §0's reserved floor via
`leaf_name_reason` — one rule, not a second copy.

⚑ **The two exec doors are a SEPARATE seam** (`runtime.exec(..., env=)` against a live box): there is
no collapse there to apply a level to. They read the map `_run_container` already built, so the doors
and the launch cannot disagree about what the user typed.

### Realizations are launch-derived keys

By the spec's `env.<VAR>` block and system-design § "Delivery at launch" (the refusals ship, no
carve-out). The five realized variables — goose `GOOSE_MODE` (unconditional, every launch) ·
`GOOSE_MODEL` · `GOOSE_PROVIDER` · `OPENAI_HOST`, and claude `ANTHROPIC_BASE_URL` (each conditional
on its driving key resolving truthy); **codex and core realize none** — are **launch-derived
`agent.<node>.env.<VAR>` keys**, and `_assemble_launch_env` is a TOTAL projection of the leaf.

**The seam is a POSITION, and that is the design.** A realization cannot be a floor default: its
inputs (`effective_behavior`, the access tier `-S`/`-A` reach through the CLI level, the persona
provider pin) are OUTPUTS of the resolve. So `_run_container` builds a `_LaunchRealizer` and hands it
to `_resolve_launch_snapshot(realize=)`, which calls it **between `build_launch_snapshot` and
`snapshot_category_entries`** — the only point where its input exists and its output is still in
time — and `_install_realized_env` writes what it derived into that same snapshot. **Nothing
downstream can tell a realized variable from a declared one**: no second producer, no second slot
pass.

⚑ Resolving twice instead (once for the state, once with the variables folded in) costs a second
full resolve AND *inverts* `-M` — the realization would become a floor a stored
`agent.<node>.env.GOOSE_MODEL` sits above, so a flag that outranks every settings file would lose to
a key.

⚑ **The derived trio comes BACK to the caller** (`_LaunchRealizer.result` →
`LaunchRealization(effective_state, access, env)`), so each keeps exactly ONE derivation site. The
un-rendered tier gate runs inside the realizer ahead of `assemble_env`, so the refusal lands before
any best-effort delivery can swallow it. 🛑 `result` RAISES rather than defaulting when the callback
never ran: an empty default would turn a resolve that dropped the kwarg into a box launched at `full`
with no realized variables and nothing said about it.

**Two refusals, two raise sites.** A twin at ANOTHER scope is the ordinary
`store_collapse._refuse_env_twin`. A declaration at the SAME (agent) scope is the one the collapse
cannot see, because the §2d pick merges `agent.<node>` and `agent.default` into one node where one
value would simply overwrite the other; `_refuse_realized_twin` names the declared key AND the key
that DRIVES the variable (`targets.assembly.env_realization_drivers`, the twin walk of
`assemble_env`), because a cross-scope twin has a key to move to and this one does not.

⚑ **`box show --effective` deliberately does NOT gain realizations.** It resolves stored configuration
and is given no realizer, so a launch sets variables a display does not list. Deriving them for a
display would report a tier the launch's own flags may not ship.

### ⚑⚑ The pref-origin enrichment wraps the BLOCK

`_annotate_pref_origin` names the `pref` that installed a colliding key the user never wrote. The
surviving raises are spread across the producer's two refusals and the collapse's refusals (both
inside `_install_assembly_collapse`), and the secret gate and narrow-table pass (both inside
`launch_deliveries`). **The wrap therefore covers the BLOCK, not one call**, and it catches BOTH
`CategoryCollisionError` (the producer and the seam) AND `SettingsError` (the collapse's own
refusals) — narrowing it to either one silently downgrades a class of message.
`_annotate_pref_origin` returns the exception UNCHANGED when no request accounts for a participant.
Pinned END TO END by `test_category_collisions.py::TestThePrefOriginReachesTheLIVEPATH`, which drives
a real resolve; the pure-function cases beside it would all stay green with the wrap removed.

### The narrow resolves emit their OWN table's dests, and nothing else

A narrow resolve carries one injected table but resolves the user's WHOLE cascade, so every user
declaration reaches it. Emitting them would emit a user `box.bindings.*` row a SECOND time on any
helpers-enabled or image-sharing launch, from RAW rows, defeating a later-scope `masks` sweep the
collapse had applied. `core_defaults.helper_bind_dests()` / `image_bind_dests()` /
`spawn_budget_bind_dests()` read the dests from the SAME `core-defaults.yaml` rows that declare the
binds, and `settings_categories.narrow_table_winners` filters to them. **That DELETES the exposure
rather than arbitrating it (P4).**

At a dest that IS the table's, §0 still has to decide, and a narrow resolve has nobody else to ask —
the per-scope producer already raised the SAME-scope pair (`build_store_shape_set` runs ABOVE the
collapse's `whole_box` gate), and the CROSS-scope pair is `collapse_store_shapes`', which returns
early here. So `narrow_table_winners` applies both refusals (through the two public raisers) and the
mask rule (a `masks` OVERRIDES — it is the inverse of a bind, not a second one). A bare dest-filter
would keep both rows and let a dest-keyed map settle them by INSERTION ORDER.

### The cross-category gate for a SECRET dest lives at the seam

`secret_path` carries no arm in the disk-store shape, so the COLLAPSE never sees a secret and cannot
answer "does anything ELSE contend for this destination".
`settings_categories.secret_path_deliveries` composes the per-VAR pick (`secret_path_winners`) with
that answer, over the same entry list, in the same order:

* a `bindings.*` row (or an abstraction deriving one) at `SECRET_MOUNT_DIR/<VAR>` **refuses**, through
  the same raisers, **naming both declarations**;
* a `masks` at the dest takes it and the VAR is simply not delivered — silently, as the flat
  authority ladder puts `masks` on top.

It sits at the SEAM rather than in `_emit_secret_mounts` because the collapsed bind map has lost the
declaring KEY, so a gate reading it could only name ONE participant of a two-participant collision.

⚑ **EXACT DEST ONLY, and that is not a narrowing:** a bind or a mask over the secrets DIRECTORY never
contends with `SECRET_MOUNT_DIR/<VAR>`; the secret mounts inside it, deeper in the depth-sort.

---

## `_bind_map_masks` — the mask arm comes off the SAME map

**Authority:** collapse `DESIGN` §8.1a (a mask is a void).

The map carries masks (a mask IS `CollapsedBind(None, None)`), so the launch's `tmpfs_masks` are the
`is_mask` half of the same value: `launch_binds` is read ONCE at the assembly seam,
`_emit_category_mounts` takes the binds and `_bind_map_masks` takes the masks. Reading once makes the
two arms agree by CONSTRUCTION rather than by both being careful (P3).

The collapse arbitrates masks against binds, so the map decides both halves:

* a **bind nested under a mask** is swept, and the mask survives (MIGRATION.md § "A mask hides the
  binds nested under it");
* a **bind at a mask's own destination**, declared in a LATER scope, sweeps the mask and takes the
  point — the launch emits the bind alone, never a `-v` bind and a `--mount type=tmpfs` at one
  destination;
* a mask **at or above home**, or **on another mask**, is REFUSED by the collapse, which stops the
  launch by name.

⚑ The dests are the map's KEYS, so they are `normalize_bind_dest`-spelled (`/home/agent/x`, never
`~/x`) and the arm is depth-sorted on `path_depth` — the same key the emitter sorts on, so the tmpfs
and the binds reach podman in one order.

⚑ **The arm is read AFTER `_apply_synced_copies`, and that ordering is load-bearing.** That pass may
DELETE a mask from the map — spec §0's `copy (file)` row, a synced file replacing a mask at its own
point (`_synced_masks_replaced` below). Reading the masks before it runs mounts a tmpfs over the file
the same launch just wrote. The bind arm is unaffected either way: `_emit_category_mounts` skips
masks, so only this arm can observe the deletion.

---

## `_launch_seed_list` / `_snapshot_assembly_seeded` — the seed applier consumes the LEAF

**Authority:** `~/canon/workbook/specs/system-design-1.8.0.md` § *The collapse* (*"both flat
scope-ordered lists"*, *"nothing is arbitrated at a destination"*).

`_apply_init_seeds` — the create-time home seed — reads `meta.assembly.seeded`. The seed arm and the
sync arm are two SEPARATE leaves, so there is no category discriminator to test, and a collapsed row
is `CollapsedCopy(src, dest, opts)` — **no `name`, no `category`.** The DEST is the row's identity
(R-10), so it is what a warning prints (normalized, `/home/agent/x`), and `_apply_shell_copy` is
labeled with it. `CollapsedCopy.src` is typed `str`, and `build_store_shape` refuses a source-less
copy row before one exists.

⚑⚑ **`seeded` and `synced` ARE COPIES AND STAY COPIES.** Nothing here turns one into a mount, and
nothing carries a per-category destination space — the dest is DATA on the row.

🐞 **Nothing arbitrates copy-vs-copy at a shared dest, and the cure is at DELIVERY.** A seed can write
a credential dest FIRST with a PRESERVED mtime, after which `_synced_uptodate` would skip the sync
forever. `_sync_box_at_create` therefore writes every sync row UNGATED right after the create-time
seed, so the dest holds sync-written bytes from creation onward. **Read `_sync_box_at_create` below,
and `llm-docs/kanibako/settings/store_collapse.py.md`, "Nothing is arbitrated at a destination".**

### Grouping is order-preserving BY CONSTRUCTION, and that is the load-bearing part

`collapse_seeded` emits in `SCOPE_CONTAINMENT` order and **a dest MAY repeat — the repetition IS the
overlay**. `by_dest.setdefault(dest, []).append(row)` preserves that: each group holds its rows in
scope order, and dict insertion order keeps the dests in first-appearance order. Nothing sorts and
nothing keys a dest to a single row.

🛑 **A dest-keyed (`by_dest[dest] = row`) arm would pass most of the suite and silently drop the
template trio down to its last layer.** That is why the duplicate-dest case is pinned directly.

⚑ **The applier keeps its outside-home guard.** That guard is about DELIVERING a dest, which is its
own concern, independent of `_refuse_seed_outside_home` in the collapse.

⚑ **ABSENT ≠ EMPTY.** `_snapshot_assembly_seeded` returns `None` for absent and `[]` for empty, and
`[]` is a real answer a narrow resolve produces. `None` means "this snapshot was never resolved" (a
refusal raises), which is why the consumer refuses it by name instead of seeding nothing.

---

## `_apply_synced_copies` / `_launch_synced_list` / `_synced_host_dest` — the sync applier consumes the LEAF

**Authority:** `~/canon/workbook/specs/system-design-1.8.0.md` § *The collapse* · spec §0 *"ONE DEST
SPACE, TWO DELIVERIES"*.

### The pass sits BELOW the main resolve and the emit

A narrow resolve carries no base families, therefore no home bind, therefore no `synced` leaf — the
sync leaf is gated on the bind fold by design, because a sync dest is meaningless without a bind map.
So the launch-time pass runs below the main resolve and below the emit, and consumes them:

```
_apply_synced_copies(
    *, snapshot: "KeyStore", bindings, logger,
    skip_if: "Callable[[Path, Path], bool] | None", declared_by: "Mapping[str, str]",
) -> None
```

The launch call passes `snapshot=_snapshot`, `bindings=launch_binds`, `logger=logger`,
`skip_if=_synced_uptodate`, and `declared_by=deliveries.declared_by`.

The second call site is the create-time write in `_sync_box_at_create`, which passes
`bindings=_launch_bind_map(snapshot)` and `skip_if=None` (see below).

⚑ **The inputs are ONE collapse.** `collapse_store_shapes` builds the sync list and the bind map from
one set of shapes. Resolving a sync dest against a bind map from a *different* resolve would resolve
it against a mount set the collapse never validated it over.

⚑ **The sync ARM itself is a plain scope-ordered concatenation and takes NO bind map**
(`_collapse_synced(store_shape_set)`); the bind map is applied to a sync dest at DELIVERY, by
`_synced_host_dest`.

### The position is the contract (P3 — unavailable, not forbidden)

Every parameter is keyword-only and REQUIRED, with no `None` default. A caller above the resolve
cannot name `_snapshot` or `launch_binds` — they are assigned further down the same function — so the
mistake is an `UnboundLocalError`, never a silent no-delivery.
`test_the_consumer_CANNOT_BE_CALLED_before_the_bind_map_exists` pins the signature itself, because
`start_mocks` patches `_resolve_launch_snapshot` outright, so no `_run_container` test drives the
real resolve. ⚑ The create-time caller satisfies the guard by bringing a real, FULL resolve of its
own; it is not relaxed for it.

The pass is ADDITIVE: with no `<scope>.synced` keys configured, and no target default synced
entries, the sync list is empty and it copies nothing. Three further consequences of the position:

* the `return 1` arms above it — an unusable host agent binary, a failed auth check, an agent
  delivery bind whose source disappeared — PRECEDE the pass, so a launch that bails never first
  writes into the box;
* `detect_shadowed_mounts` runs BELOW it, so it sees synced files as pre-existing;
* the plugin descriptor's `cred_files` credsync engine runs BEFORE this settings-driven pass. Where
  both write one host file the `synced` row wins.

### `_synced_host_dest` — the dest resolves THROUGH the bind that covers it

A `synced` dest is GUEST-spelled, and what it means on the host is decided by the mount set: the copy
must land in the SOURCE of whichever bind covers that path, or the box never sees it. So the rule is
**longest-prefix cover over the final bind map**, using `store_collapse.is_within`, so "inside" means
one thing on both sides of the delivery split.

⚑ **It does NOT replace `container._guest_dest_to_host`**, which keeps its stub/shadow-scan callers
and answers a different question: where a guest path's host STUB is, under two hardwired roots. On
shipped config the two AGREE. They part company inside SOME OTHER bind, where the stub arm would send
the copy under the home stub: a host path that bind shadows, invisible in the box.

The lookup is `_synced_cover`, a one-line delegation to `store_collapse.covering_bind` — the question
is the collapsed map's own, answered beside it, and `store_collapse.pair_declarations` asks it too.
`_refuse_synced_under_mask` shares it, so the pass that decides whether a covering mount REFUSES a
row and the pass that resolves the row THROUGH it can never disagree.

| arm | when | why it is where it is |
|---|---|---|
| no cover → **REFUSED UPSTREAM**; this arm is a backstop | dest outside every bind | `store_collapse.refuse_uncovered_synced`, called from `_install_assembly_collapse`, stops the launch before delivery runs. The warn is KEPT because this function also runs against a bind map read back from a snapshot |
| cover is a MASK → warn+skip | `is_mask(bind)` | **must precede any `Path(bind.src)`** — `MASK` is `src=None`, so it raises `TypeError`. By this point the cover can only be the dest's OWN point with a source that is neither file nor directory; the spec's refusals already raised |
| cover is READ-ONLY → warn+skip | `is_read_only(bind.opts)` | see below |
| else | — | `Path(bind.src) / rel` |

⚑⚑ **The two warn arms are not refusals the spec names.** Spec §0 states the `synced` refusals
exhaustively — a mask as its PARENT, a copy of a DIRECTORY at a mask's own point, and a dest NO mount
covers — and the first two raise in `_refuse_synced_under_mask` before this function runs. What is
left here is residue the containment table says nothing about: a read-only cover, and a mask at the
dest's own point over a missing or unreadable source (the module's own class — `_apply_shell_copy`
warns on any missing source). For that residue, a mis-declared dest must not cost the user the
launch.

⚑ **A dest is DATA** — compared and sliced as a path, never `.split(".")`-ed.

🔴 **The MASK arm exists because the collapse ACCEPTS what delivery then JUDGES.** By
system-design § "Collision arbitration" ([Jei:2026-08-12]: *"don't check for sync. Let it clobber
whatever it wants."*) the fold refuses no sync, so every sync row reaches delivery, a mask's exact
point included.

🔴 **The READ-ONLY arm is SPEC-SILENT and deliberately strict.** Writing into a read-only bind's host
source delivers content the box cannot be shown to have received, and the source is usually something
the user did not mean this to reach. Home and workspace are both `rw`, so no shipped configuration
loses anything. Loosening it later breaks no existing box — the reverse would not be true.

### `_refuse_synced_under_mask` — the table's word is REFUSE, so it raises

**Authority:** spec §0's containment table, the two copy rows. A mask that is a strict **PARENT** of a
sync dest refuses the copy; a **DIRECTORY** copied at a mask's own point refuses too, because no mask
may be left half-populated. It raises `SettingsError`, like every sibling refusal in that table
(`_refuse_bind_under_mask`, `_refuse_mask_on_mask`, `_refuse_mask_over_home` in the collapse). A
refusal that only warned would be an acceptance with a log line, and the row it dropped is usually a
**credential**: the box would start, the harness fail to authenticate, and nothing name the cause.

**Position: after `_synced_masks_replaced`, before the first copy.** After, because the cell the table
ACCEPTS — a FILE at a mask's own point — is decided there and deletes its mask. Before, because a
raise mid-loop would leave the box with some rows delivered and some not.

**The one mask cover it does not refuse** is the dest's own point over a source that is neither file
nor directory; that keeps its missing-source warning.

### `_synced_masks_replaced` — the one cell where the two COPY rows differ

At a mask's OWN point a copied **FILE** replaces the mask — one file filling one void is total —
while a copied **DIRECTORY** there is REFUSED. Everywhere else the two rows agree, and a mask that is
a strict PARENT refuses both.

**It can only live at DELIVERY.** FILE vs DIRECTORY is a property of the copy SOURCE, so deciding
it is a host `stat` — and the collapse is PURE (system-design § "Collision arbitration").
`store_collapse.py` carries no copy-vs-mask rule; `_apply_synced_copies` applies both cells over
the collapsed map, before any row resolves.

**The deletion is a MAP EDIT, and the order around it is the contract.** The replaced mask is deleted
from the map the launch holds (`_snapshot_assembly_bindings` copies out, so the snapshot does not
move), and `_run_container` reads its mask arm **after** the sync pass. Three properties, each pinned:

* **the deletion is decided over the DECLARATIONS, not over what was written.** A row the mtime gate
  skips still replaces its mask — otherwise the tmpfs would come back on exactly the launches where
  the sync had nothing to do;
* **downstream skips do not restore it.** Nothing real covering the dest behind the mask, or a
  read-only cover, still leaves the mask deleted: containment is one layer, delivery another;
* **it is applied before any row resolves,** so which mask survives cannot depend on row order.

### `_synced_last_wins` — an overwrite copy's overlay IS last-wins

The sync leaf returns every scope's row at a dest. Applied in list order under the mtime gate
(`_synced_uptodate`), a system row with a NEWER source would make the box row a SKIP — the less
specific scope silently keeping a credential destination. **The mtime gate is an OPTIMIZATION, and an
optimization may not decide which row wins.** So one row per dest, the LAST, which is the most
specific because `SCOPE_CONTAINMENT` and `_SCOPE_APPLY_ORDER` are the same order and the leaf is
emitted in it. Each dest keeps its first appearance's position.

⚑ **There is no seed analog and there must not be:** a `seeded` dest's repeats are LAYERS that all
apply (the §2a template trio).

⚑ `_launch_synced_list` reads the `synced` leaf and nothing else; nothing in the types stops an edit
pointing it at `_snapshot_assembly_seeded`, which would apply every `seeded` row as an OVERWRITE on
every launch. `test_start_assembly` pins it.

---

## `_sync_box_at_create` — the `synced` write happens ONCE at create, UNGATED

**Authority:** by ruling [Jei:2026-08-11] — *"at box creation, since that's the only time seeded is
copied, find the top-most bind in the bindings and write synced to it once at creation,
irrespective of date"*, where "top-most" means the INNERMOST covering bind, the opposite of home.

It is a **CALLER**, not a mechanism. `_synced_host_dest` already resolves a guest dest by
LONGEST-PREFIX cover (innermost), and `_apply_synced_copies` already applies the rows and raises
spec §0's refusals — so a mis-declared dest stops `create` exactly as it stops a launch. A second
covering-bind resolver would be two spellings of one rule.

### Why UNGATED is the whole point

`_synced_uptodate` skips when `dest.st_mtime >= src.st_mtime`. **That comparison means something only
if the destination was last written BY THE SYNC.** The create-time seed writes first through
`shutil.copy2`, which PRESERVES the source mtime, so a seed source newer than the sync source would
pin the SEED's bytes at a `synced` destination, permanently and silently, most often at a credential.
Writing the sync once at create with `skip_if=None` **restores the gate's own invariant**, so nothing
prunes a user's declared seed row to protect it.

⚑ **`skip_if` is therefore a REQUIRED keyword on `_apply_synced_copies`**, with no default. The launch
refresh passes `_synced_uptodate`; the create-time write passes `None`.

⚑⚑ **SEED-THEN-OVERWRITE is the shipped model.** Both rows are delivered at a shared dest, in category
order.

### The second, FULL resolve — and why it does not widen the narrow one

A `synced` dest is meaningless without a bind map, so this needs `include_base_families=True`, while
`_apply_init_seeds` deliberately resolves NARROW: no `desc`, no `install`, no persona tier, no base
families, because *seeding is file delivery*. **Two resolves, two purposes.** Widening the seed
resolve to reach the bind map would be the cheap move and the wrong one. For the same reason it is a
**SIBLING** of `_seed_box_home` and not a fourth step inside it: `_seed_box_home`'s contract is three
ordered *create-if-absent* steps, and a sync is an *overwrite*.

### The two call sites, and the order that matters

Both `_seed_box_home` call sites are creates and both get it, immediately after the seed:

| site | why |
|---|---|
| `seed_new_box` (the `box create` entry) | INSIDE that function, so it lands BEFORE `materialize_canon_skeleton`, which makes the canon region 555 and would fail a later copy with `EACCES` |
| `_run_container`'s `if proj.is_new:` block | a `workset connect` member's FIRST launch, which materializes and seeds the box `workset connect` registered |

⚑ On the `workset connect` first-launch path the sync then runs **again**, later in the same process,
mtime-gated (`_apply_synced_copies` below the emit). That is the design: the create-time write
establishes the invariant, and the launch-time gate then correctly no-ops.

### Two caveats

* **`install` is `target.detect()` — a HOST FILESYSTEM PROBE** (agent name · host binary · install
  root · launcher), nothing to do with the container image. `detect()` returns `None` when the agent
  is not installed on the host, which is a legitimate state.
* **The launch RE-DETECTS after `prepare_host()`** (the auto-updater), so the create-time and
  launch-time bind maps are not guaranteed identical. The later gated pass covers the difference.

⚑ **The SELECTION only, never the full CLI level (§1A)** on the create-side resolve: the create path's
flags are not the launch's, and the ephemeral ones (`-M`/`-N`/`-C`/`-R`) carry no bind. The selection
is REQUIRED because this resolve folds the auth chain (`{workset.auth.path}/{system.agent}`).

---

## `_run_container` — the DOOR TABLE and the OVERRIDE GATE for an already-running box

**Authority:** by ruling [Jei:2026-08-05] — *"could this setting work without restarting the box
and without making a mangled / corrupted / unexpected state?"*

### The four doors

The reattach regime (`if reattach_running:`, further down `_run_container`) has exactly FOUR exits,
and every value that decides WHICH ONE a launch takes is settled by one line near the top of
`_run_container`, into a named value. The override gate immediately below it AND the regime itself
both READ that answer instead of each re-deriving it from its own subset of the deciding inputs.

| door | when | what it does |
|---|---|---|
| `detach` | `--detach` / `--warm-only` | the box is already Up, which is the whole request → report and return 0. ⚑ NOTHING RUNS through this door, so it can carry no per-run flag |
| `box-shell` | `kanibako shell` at a live box that IS RUNNING AN AGENT | resolve a shell off the LIVE container's image and exec it as a second process |
| `entrypoint` | an explicit `--entrypoint` | the same exec, with the program the user named |
| `attach` | everything else | `_bootstrap_attach` (`tmux attach`), whose exec takes NO `env=` and no program of ours |

⚑ **`stored_agent` — the LIVE box's `KANIBAKO_AGENT` stamp — is LOAD-BEARING in the `box-shell`
row**: a live NO-AGENT box's PID-1 tmux session IS the user's own shell, so it must keep reattaching
(exec'ing a fresh shell would strand it). Such a box therefore takes the `attach` door, where a `-e`
would be silently discarded — which is exactly why the gate below must refuse one.

### Why a TABLE and not another predicate term

The gate asks *"will a consumer apply this flag?"* A gate reading a subset of the deciding values
while another value (`detach`) closes the door elsewhere would accept
`start --detach --entrypoint X -e FOO=bar <live box>` and silently discard it, rc 0. A gate that
reads the RESOLVED DOOR cannot disagree with the regime that walks through it, however either grows.

### It reads TYPED values, and it must stay where it is to do so

`entrypoint` is rebound repeatedly further down (the two box-shell resolves, the agent default, the
detached-launch box shell), as are `no_helpers` and `explicit_agent`. The block sits ABOVE every one
of those, so the question it answers is the question the USER asked. `typed_entrypoint` keeps that
frozen answer under its own name so the regime never has to trust that no rebinding slipped in.

### What the override gate refuses, and why each class

For the flags below the answer to the ruling's test is no, so they are REFUSED BY NAME:

* **Class A (container-CREATION inputs)** — the container already EXISTS, so a creation-time input
  cannot reach it. The justification is uniform across the class.
* **Class B (agent argv)** — the reattach execs a bootstrap ATTACH, never a fresh agent, so
  `start -N <running-box>` would attach to the OLD conversation instead of starting a new one.
* **the two SESSION-SHAPE flags** — an EXPLICIT `--persistent` asks for a fresh persistent session,
  which we cannot give without killing the bootstrap session already there, and an explicit
  `--ephemeral` asks for a foreground single-use session, which cannot coexist with it. By ruling,
  killing tmux must not be necessary: if tmux is running, the user goes back to the command line and
  the old session keeps running. This gate IS that check: it precedes every attach, so the running
  session is never signaled, killed, or touched.

The gate sits ahead of BOTH seams (the flag persist and the reattach fast path), so neither can ever
observe one. `--restart` never reaches it — it stopped the box above, so nothing is running.

⚑ **THE SESSION-SHAPE PAIR IS KEYED ON THE FLAG THE USER TYPED, NOT ON `persistent`.** `persistent` is
DERIVED: `run_start` defaults it True whenever the bootstrap program is present, so gating on it would
refuse every ordinary reattach. `explicit_persistent` / `explicit_ephemeral` carry the typed flags,
threaded in from both callers that own them (`run_start` and `run_shell`). They are also the reason
this gate keys on `box_running` rather than `reattach_running`: an explicit `--ephemeral` DERIVES
`persistent` False, so it never becomes a reattach.

⚑ The session-shape refusal is scoped to an AGENT launch: `kanibako shell` and `shell -- cmd` against
a live box exec INTO it, which honors an `--ephemeral` request, so there is nothing to refuse.

**DELIBERATELY NOT GATED:** `--attach` / `--detach` / `--print-container` / `--warm-only` — all
meaningful against a live box in their own right.

### The two PER-RUN flags are gated BY THE DOOR

`--entrypoint` and `-e/--env` are refused only when the door this launch will actually take would not
apply them:

* `entrypoint` / `box-shell` — both land on the one `runtime.exec` that passes `env=`, so both flags
  ride through and neither is refused;
* `detach` — NOTHING RUNS, so a typed `--entrypoint` AND a `-e` are both refused BY NAME. (The
  FRESH-launch path's decision to drop `--entrypoint` under `--detach` is separate — there a container
  is actually created.)
* `attach` — `_bootstrap_attach`'s exec takes no `env=`, so `-e` is refused. A typed `--entrypoint`
  can never reach this door (it would have selected `entrypoint`).

---

## `_persist_or_announce_flags` — settle a SHADOWING flag: persist it, or say OUT LOUD it is ephemeral

**Authority:** [Jei:2026-08-02] — *"(c) BOTH"*.

### The persist arm, and the window it closes

On a creating launch (`proj.is_new` — a launch never creates an unregistered box; the creating launch
is a `workset connect` member's first launch) the launch's whole §1A CLI level — the one
`build_cli_level` output, built at the agent selection — and the typed `--agent` go to the ONE
create-exception helper (`settings.config_interface.persist_creation_level`); this function adds no
persist logic of its own. An `agent reauth` launch (`setup_only`) passes no typed agent: its
`--agent` help says "not saved". Every shadowing flag persists alike (spec: "ALL shadowing flags act
UNIFORMLY"). `-e` cannot persist here: which key owns a variable is known only after the launch
resolve's env collapse, so `_run_container` calls `persist_creation_env` right after that resolve
(with the carrier's `secret_names`), before anything runs in the box but after the seed and
registration, which its refusal says.

It is called from the EARLIEST point on each launch arm at which BOTH preconditions hold — the rig is
PREPPED (built or pulled) and `proj` is MATERIALIZED — because everything between materialization and
the persist is a window in which a failed launch leaves the box EXISTING with the flag unstored, so
the user's retry is non-materializing and their `--image` is silently discarded. The two orderings
are not symmetric:

* **BEFORE the rig prep** would store an image that never pulled — so the persist never goes earlier
  than the prep. A pull that FAILS is therefore the one window that stays open by design; the
  announce arm keeps it from being SILENT.
* **AFTER the rest of the launch** would leave the baseline probe, the agent-config load, the persona
  store read, the launch-decision resolve and the persona pre-flight — each of which can return
  non-zero — inside the window.

⚑ **A REATTACH reaches this seam ZERO times.** The box already exists and no container is being
created, and both flags this seam settles are refused outright by the running-box override gate — a
user who wants `--rig` to take is told to `kanibako --restart`, which relaunches through the normal
arm.

### The announce arm, and why it is ONE notice

On a NON-materializing launch a supplied shadowing flag applies to that launch alone (spec §1A) and
leaves the box file byte-identical. Only the image flags are announced (the agent flags are per-run
by their help text): `--image` and `--share-images`, with the ephemerality and the cure.

⚑ **ONE notice, never two.** The flags are announced TOGETHER because the explanation ("this box
already exists, so nothing was stored") is the SAME sentence for both; a second flag extends the same
sentence and adds a second cure line rather than starting a second notice.

⚑ *box_settings_path* is the BOX-TIER settings file from `box_workset_settings_paths` (M-8) — the
file `box set box.image=…` writes.

---

## `_build_supervisor_pid1` — the import-GATED PID-1 for a DETACHED agent box (E2b)

**Authority:** `~/canon/workbook/designs/split-brain-persistence-DESIGN.md` § *E2 BUILD DESIGN*, its
forward-compat FALLBACK.

The always-on supervisor (`kanibako.box_supervisor`) runs the agent in a detached tmux session and
self-heals it, so a detached AGENT box makes the supervisor PID-1 instead of a bare-shell keep-alive.
An OLD box image may ship a kanibako WITHOUT the supervisor module, so PID-1 is an import-GATED
`sh -c`: it `exec`s the supervisor only when `import kanibako.box_supervisor` succeeds, else `exec`s
the *fallback* bare-shell keep-alive. *supervisor_argv* and *fallback_argv* are FULL argv lists (the
program at index 0); both are `shlex`-quoted into the single `sh -c` script so agent args carrying
spaces / quotes survive the nested shell. It returns `("sh", ["-c", <script>])`.

### ⚑⚑ The gate RETRIES ONCE and the fallback is LOUD

A single probe with its stderr discarded would leave a box at a bash prompt with the agent never
started, the reason lost, and `kanibako start` still returning SUCCESS — and a transient import
failure on a real launch has been measured. So the probe runs twice, its stderr is APPENDED to
`SUPERVISOR_FALLBACK_RELPATH` under the guest home, and taking the fallback writes a warning to BOTH
stderr (which reaches `podman logs`) and that file. The fallback still runs — a degraded box beats no
box — but it is never silent.

### The HOST kanibako goes FIRST on `sys.path`, and the quoting is load-bearing

Both the import PROBE and the supervisor `exec` run with the HOST kanibako — bind-mounted read-only at
`KANIBAKO_PKG_MOUNT_ROOT` by `_kanibako_mounts` — FIRST on `sys.path` via an injected `PYTHONPATH`, so
the supervisor that runs is the host version (== the host CLI), never the image's baked copy, which
may lack the module. `PYTHONPATH` is PREPENDED (`${PYTHONPATH:+:$PYTHONPATH}`), never clobbered; the
supervisor scrubs its OWN mount-root entry back out before spawning the agent/tmux children
(`box_supervisor.scrub_bootstrap_pythonpath`).

⚑ The FALLBACK `exec` carries no PYTHONPATH: it is a bare-shell keep-alive that never imports
kanibako.

⚑ The whole `PYTHONPATH=…` assignment is DOUBLE-QUOTED so the shell still expands `${PYTHONPATH:+…}`
but the RESULT is never word-split or glob-expanded — an image PYTHONPATH holding a space or `*` would
otherwise split the env assignment into extra `env` operands and degrade the probe to the fallback.

---

## `_resolve_box_launch_decisions` — auth SOURCE + persona endpoint + persona model, off ONE snapshot

`build_launch_snapshot` accepts BOTH the auth 3-tier `auth_chain` floor AND the plugin's behavior
floor (`agent_behavior_floor`) in a single call, so the box's sharing decision
(`settings_launch.AuthSource`, `resolve_auth_source`) and its active-node `agent.<node>.endpoint`
(`effective_behavior`) are read off the SAME expanded snapshot — no duplicate build.

* **`auth_src`** — the credential-SHARING SOURCE (tier/source + enables), threaded to every
  credsync/gate consumer.
* **`endpoint`** — the resolved PERSONA endpoint URL, or `None` when unset (`<None>` / empty / no
  descriptors / no target) — the cred-fork signal (non-None ⇒ suppress the OAuth cred).
* **`model`** — the cascade-resolved active-node `agent.<node>.model` (`effective_behavior`): a
  resolved `str` id, or `None` for no model — unset (incl. no descriptors / no target), `null`, or
  blank alike.

The plugin's behavior floor folds in as `agent.<node>.<key>` (core's floor is not passed — its
`endpoint` is `<None>`, which the pick omits like an absent key) and the per-agent FILE state as the
active `agent.<node>` slot; the §2d active-over-default pick yields the endpoint for the NODE. A target
with no declared settings contributes no floor → endpoint `None`.

### Why the model read excludes the harness default

No gate tells an unset model from a `null` one, so the model is the plain `effective_behavior` read,
which omits a present-None. This read DELIBERATELY excludes any harness `model` DEFAULT floor (a
plugin that declared one would name an own-endpoint model, wrong for a third-party provider): an unset
persona model resolves `None` here, and the persona launches with no model rather than a bogus default
in a `[model_providers.<id>]` block. It is consumed only by the persona pre-flight; for bare launches
the `--model` flag resolves its own default via the main launch snapshot.

### Why *persona_values* is threaded in, and why *selection_level* is NOT the full CLI level

*persona_values* is the persona store's LIVE tier (`_persona_values_for`), threaded in because BOTH
returns depend on it: the *endpoint* is the cred fork (a persona endpoint that did not reach this
resolve would silently stop suppressing the host OAuth cred, sending the user's real token to a
third-party endpoint), and the *model* is written into the codex `[model_providers.<id>]` block.
`None` for a bare agent. The store tier sits BELOW the per-agent FILE.

⚑ *selection_level* is the SELECTION only — deliberately NOT the full §1A CLI level (P8). The *model*
this returns lands in the codex `config.toml`, i.e. it is WRITTEN TO DISK; letting `-M` reach it would
make an ephemeral flag mutate a stored value, which spec §1A forbids (*"EPHEMERAL, always"*). `-M`
rides the LAUNCH snapshot only, where it reaches argv and the container env and nothing else.

---

## `_preflight_persona_load` — a persona's LOADABILITY, resolved before any artifact exists

A TRUE pre-flight: called ONLY for a persona (`harness_of(agent_id) != agent_id`), BEFORE any persona
artifact is created. Returns `(endpoint, error, provider)`:

* **endpoint** — the resolved endpoint URL on success (`error` None); `None` when unloadable;
* **error** — an actionable message when the persona cannot be loaded (unresolved endpoint, or
  endpoint-but-no-token); `None` on success. The caller prints it (start) or raises it (create) and
  refuses to launch;
* **provider** — for a CONFIG-FILE harness (codex) the resolved `vscode.vscode_config.CodexModelProvider`
  wired into `~/.codex/config.toml`; `None` for an ENV harness (claude, goose), whose endpoint + token
  ride their existing single-source channels (the `endpoint`→`ANTHROPIC_BASE_URL` `SettingArg` env +
  the `secret_path` mount).

⚑ **NOTHING here mutates *agent_cfg*.** Every persona value is a LIVE resolution input resolved
through the cascade before this seam, so there is nothing to adopt and nothing to write back.

### HARNESS-AWARE — exactly two gates, one picker

The token var and the endpoint DESTINATION are chosen per the resolved target's `_persona_wiring`.
`endpoint_delivery` alone picks between the two gates: ENV (`_preflight_env_persona` — claude, goose)
and CONFIG-FILE (`_preflight_config_file_persona` — codex, which also returns the CodexModelProvider).

### What each input means

**`keyspace_endpoint`** is the endpoint the launch snapshot already resolved from
`agent.<node>.endpoint` — the persona STORE is a cascade tier of that snapshot. `None` therefore means
no endpoint is configured for this persona ANYWHERE, which is a hard error: kanibako will not launch a
persona as the bare host harness on the user's real account. A resolved endpoint with NO usable token
is ALSO a hard error (a bearer endpoint with no token 401s inside the box).

**`keyspace_model`** is the box-level cascade-resolved `agent.<node>.model`, harness default excluded
(see `_resolve_box_launch_decisions`): a model id, or `None`. No model is refused, for any harness:
kanibako passes none, and the harness and its endpoint decide. For codex the provider then carries no
model, so `config.toml` gets no root `model`, there is no `--model`, and codex uses its own default; a
goose persona uses its own config's model or refuses to start.

**`bundle`** is the persona-grata store read for this same launch (`_persona_bundle_for`). It is the
SECOND token source — the agent file rung first, the store below it (`_persona_token_pointer`) — and
it carries the store's own verdict on itself:

* `reject_reason` ⇒ a HARD ERROR naming the reader's SPECIFIC cause, VERBATIM; nothing is persisted,
  so there is no last-known-good to fall back on.
* `no_reader` ⇒ NOT an error, ever. The harness simply has no store reader; a goose persona is
  configured entirely through the keyspace and merely happens to own a store dir.

⚑ That is why the two are separate values rather than one reason string: collapsing them would refuse
every keyspace-configured goose persona that owns a store directory.

**`probe`** runs the PER-LAUNCH verify probe once endpoint and token both resolve
(`_persona_probe_error`). A non-empty `-e` value for the token var (**`cli_env`**, launch only)
satisfies the token gate and is the token the probe sends; an empty one is ignored. `probe` is opt-in and
set ONLY by the launch: the create path keeps its own
WARN-ONLY probe [Jei:2026-08-05]. The bundle's `env` — the persona STORE's own block, not the
collapsed `env` family — rides along so the harness can rewrite a tier alias the way its runtime
will. See `Target.verify_persona` in the `targets/base.py` llm-doc for the measured false `REJECTED`
that requires it, **and for the residual hole it does not close:** a mapping at the agent-file,
workset or box rung (or a keyspace-only persona) is not in the bundle, and the collapse that would
carry it is folded only by the whole-box resolve, which runs after this pre-flight.

🛑 **THE REJECTED MESSAGE NAMES WHAT WAS REFUSED, NEVER WHAT TO FIX.** The probe cannot tell a dead
credential from a live one that lacks model entitlement, and a 401/403 does not say which input was at
fault. It prints the status, the inputs and the provider's own words —
`PersonaProbeOutcome.evidence_block()`, the SAME renderer the create warning uses.

---

## `_merge_default_categories` — folding ONE family's default-categories table into the floor

### ⚑⚑ This exists because `dict.update` is NOT safe here (H5)

Under dest-keying a bindings arm is ONE terminal key holding the whole map (R-5), and about eight of
the families contribute to `box.bindings.ro` — so an `.update()` would replace the map wholesale and
delete every earlier family's entries, silently.

⚑ **ALL SEVEN TERMINAL CATEGORIES, not just the two bindings arms.** `caches` / `seeded` / `common` /
`synced` are terminal too, and several families write each of them: `target.default_seeds()` and
`template_seed_defaults` both write `agent.<a>.seeded`; `target.default_common()` and
`target.default_category_binds()` both fold into `agent.<a>.common`. The membership test is
`settings.settings_keyspace.is_terminal_category_key` — the ONE whole-key predicate, DERIVED from the
keyspace's own declaration so it cannot fall behind.

### Two branches, and the split is deliberate

* a **TERMINAL dest-keyed category key** whose value is a map merges ENTRY BY ENTRY;
* **everything else** is LAST-WINS.

⚑⚑ Do NOT generalize this into a deep merge for every value. Two call sites —
`extra_default_categories` and `inputs.system_floor` — are LATE INJECTIONS that are supposed to
override, and a deep merge would quietly stop them. `<scope>.masks` is a dest-keyed map too
(spec §2a: `dict[box_dest → bool|None]`), so it takes the entry-by-entry branch. ⚑ A scalar leaf
that merely ends in a category token (`system.channels.common`) fails `is_terminal_category_key`
itself, on POSITION.

### The same-key duplicate-dest refusal is STRUCTURAL

A destination already claimed in the SAME key RAISES, naming both families (*origins* carries who
claimed it), for every terminal category. The value is ONE dict keyed by destination, so two families
claiming one dest under one key is not an overlay the shape can express.

That is NOT a statement that seeds may not layer: layering is expressed ACROSS KEYS —
`system.seeded` / `agent.<a>.seeded` / `workset.seeded` all target `~/`, remain three DIFFERENT keys
here, and are regrouped by destination and staged in scope apply order by `_apply_init_seeds`. Two
entries at one dest in DIFFERENT arms or categories are still different keys, still both emitted, and
still reach the collision table in `settings_categories`.

---

## `_resolve_launch_snapshot` — the ONE launch resolve, and what gates each parameter

### What it is

The single launch-time CATEGORY resolve. It aggregates every runtime `default_categories` table (core,
kani, kickoff, the core env stamps, rom and canon, the agent template sources, channels, and the
agent's adapted tables, plus a narrow caller's injected table and the CONDITIONAL helper and image
tables) into ONE floor through `_merge_default_categories`, folds in the resolved `system.*` tier so
`@`-refs resolve from the snapshot (`inputs.system_floor` from `settings_launch.resolve_inputs`, built
by `settings/paths.system_path_floor` — the same floor `commands/workset_cmd` uses), represents the
agent's descriptor delivery binds via `agent_default_partial`, and runs
`assemble_levels → merge → expand` ONCE via `settings.settings_launch.build_launch_snapshot`. The
expanded snapshot is adapted to `CategoryEntry` ONCE, and that ONE list feeds every consumer.

It returns `(snapshot, deliveries)`: the mount set, the seed and sync lists and the environment live IN
the snapshot under `meta.assembly.*`; the `LaunchDeliveries` carrier holds the rest (above).

⚑ **The carrier is a RETURN VALUE and not a snapshot leaf on purpose** — `meta.assembly.*` is a CLOSED
set of DECLARED leaves, and an undeclared one would install silently.

⚑ AGENT_CRITICAL delivery binds flow through the snapshot's `agent.<agent>.bindings.*` subtree
(single-route), emitted by `_emit_category_mounts` under its `must_exist` policy — NOT a parallel
`descriptor_mounts` route or a parallel agent emitter.

### The four plugin-declared tables are ADAPTED TO THE ACTIVE NODE before they fold

A plugin keys `default_common()`, `default_seeds()`, `default_category_binds()` and `default_envs()`
against its OWN name — the HARNESS — while the §2d read pick overlays `agent.default` ∪
`agent.<ACTIVE NODE>`. So each table goes through a node adapter first:
`settings.agent_representation.agent_categories_for_node` for the three CATEGORY hooks (re-key AND
re-root) and `agent_env_for_node` for the scalar env table (re-key only). Both are the identity for a
bare agent.

🛑 **A RAW FOLD IS A SILENT LOSS, NOT AN ERROR.** A harness-keyed entry is simply never read for a
persona: no mount, no copy, no warning, rc 0. Every first-party plugin returns `{}` from the seed and
category-bind hooks, which is why the pin (`tests/test_targets/test_agent_categories.py`) drives a
target that declares something.

⚑ **THE SEED HOOK IS FOLDED AT TWO INDEPENDENT SITES** — here, and again on the CREATE path in
`_apply_init_seeds`. Fixing one leaves the other broken.

⚑ **CONSEQUENCE, ACCEPTED:** a plugin declaring one dest in BOTH `default_common` and
`default_category_binds` lands both in one key after the adapter, and that launch REFUSES. No
carve-out.

### The parameters, and what each one gates

**`narrow_bind_dests`** is a NARROW caller's own injected table's dests
(`core_defaults.helper_bind_dests` / `image_bind_dests` / `spawn_budget_bind_dests`). Given, this
resolve builds `deliveries.narrow_bindings` — that table's rows and NOTHING ELSE, in the emitter's
shape. Omitted, the field stays `None`: a whole-box resolve emits from `meta.assembly.bindings` and
must not have a second map available to reach for.

**The image, helper and spawn-budget tables are CONDITIONAL.** A table is included ONLY when its gate
holds (image-sharing active → *storage_conf_path* given, *graph_root* only when the probe succeeded —
it feeds ONLY the `box.images_store` default; helpers enabled → *socket_path* given, and *log_path* is
`None` when `workset.logs` is a present `<None>`: the log bind's `<None>` source is omitted at the
collapse, and `_start_helper_hub` then starts the hub with no message log; *budget_path* for the
spawn budget every start delivers), so their binds do NOT appear otherwise.

**`guarantee_create`** (default True — a LAUNCH) gates the core table's create-if-missing of the vault
source dirs. A READ-ONLY consumer — `box show --effective` — passes False: it must show what a launch
WOULD mount without making it so.

**`include_base_families`** gates the always-available tables (core, kani, kickoff, core env, rom,
canon, agent template sources, channels). It is True for the MAIN launch snapshot and False for the narrow resolves, which carry
ONLY their own table plus any config-file keys.

⚑⚑ **The base families include `launch.templates.agent_template_defaults` — the AGENT `template`
SOURCE arms, folded beside `core_defaults.canon_default_categories`, their declared sibling.** Both
arms are DECLARED rows with real defaults; emitted only inside `template_seed_defaults`, which reaches
the keyspace through the CREATE-time seed resolve alone, `{agent.default.template}` would be
`__MISSING__` for every box that already exists. The two folds cannot collide: the seed table arrives
through `extra_default_categories` with `include_base_families=False`. ⚑ **SOURCE SCALARS ONLY.** The
`seeded` LAYERS are deliberately not folded here; seed-once is the failsafe against re-seed DATA
LOSS. (The workset arm, `workset.template`, is `settings_launch.workset_anchor_floor`'s.)

**`persona_values`** is the persona store's LIVE tier for this launch (`_persona_values_for`) —
`endpoint` / `model` / `secret_path.<VAR>` / `env.<VAR>`, un-discriminated. It is given by every resolve
that has to AGREE with the launch about what the store says: the MAIN launch resolve, the
`--effective` display, and the two CREATE-side resolves that run the same load-or-error gate
(`persona_create_verdict`, `seed_new_box`). Nothing persists the store's values, so a create-side
resolve without it would not see the persona's endpoint, token MOUNT or env delivery.

The NARROW resolves leave it `None` deliberately: the image / helper tables resolve box_dests disjoint
from anything a persona touches, and the SEED resolve is FILE DELIVERY only, where a token pointer has
no meaning. ⚑ The CREATE-time SYNC resolve (`_sync_box_at_create`) DOES carry the tier: it is a FULL
resolve whose whole product is the bind map, and a persona's `secret_path.<VAR>` is a MOUNT.

**`agent_cfg_path`** is the agent settings file, read WHOLE as the `agent.<active>` cascade level: its
scalars and its category tables ride one level, from one path. ⚑ **It is NOT gated on
`include_base_families` or on a target**: the file is a cascade level, not a launch family, so a
no-target `kanibako shell` reads `agents/shell/agent.yaml` too. The CREATE seed resolve is narrow and
still needs it, because `agent.<a>.template` is the layer-2 seed SOURCE (spec §2a); `_apply_init_seeds`
passes its `agent_config_path`.

**`cli_level`** is the §1A CLI LEVEL (P8), built by `settings.settings_cli_level.build_cli_level` and
validated inside `build_launch_snapshot`. Only the main launch resolve may carry the EPHEMERAL flag
values (`-M` / `-N`-`-C`-`-R`), because its output is this launch's argv / env / mounts and nothing
here is written back. The create-time seed and sync, the persona-endpoint, the narrow and the
`--effective` resolves take a selection-ONLY level. ⚑ Every resolve folds the auth chain — it rides
the `settings_launch.resolve_inputs` inputs — so every one must carry the selection:
`meta.box.auth.workset_path` is `{workset.auth.path}/{system.agent}`. The keyword is REQUIRED (P3).

**`cli_env`** is the parsed per-run `-e` map, forwarded UNTOUCHED to the collapse, which applies it as
the CLI level over the key owning each variable. ⚑ Only the main launch resolve takes it: a `-e` value
belongs to one launch, so a resolve whose product is a stored map or a display
(`_sync_box_at_create`, `box show --effective`) must not see it. It is NOT a settings level and cannot
be folded into *cli_level*: see `store_collapse._apply_cli_env`.

**`realize`** is the launch's REALIZATION callback (a `_LaunchRealizer`); see "Realizations are
launch-derived keys" above. ⚑ Only the main launch resolve takes one.

---

## `_seed_box_home` / `_apply_init_seeds` — the ONE-TIME home seed, at CREATE

⚑⚑ **Read `~/canon/notebook/procedures/seed-and-create-model.md` before touching either.** A box is
seeded ONCE, with registration, at `create` — never at a later `start` — and thereafter the home BIND
owns the content: never re-seed. The create path's journal-entry branch (`box create --recover`)
deliberately differs from that model; it reads like a bug, is not one, and deleting it deletes
interrupted-create recovery.

### `_seed_box_home` — three ordered, create-if-absent steps

It is the SINGLE seed implementation, shared by `box create` (`seed_new_box`) and `_run_container`'s
`if proj.is_new:` block (a `workset connect` member's first launch). It is NEVER run on a relaunch of
an existing box. The three steps are ordered and create-if-absent, so a re-create into a leftover dir
never clobbers user content:

1. the descriptor's one-time credential seed (descriptor-bearing targets only);
2. the configured copy-once-at-init `seeded` category rows — INCLUDING the layered `seeded[~/]` trio
   (system → agent → workset; later overlays earlier, per-file last-wins), ordinary keystore `seeded`
   keys resolved off the committed snapshot in `_apply_init_seeds`;
3. the box's own HANDBOOK CHAPTER — a HOST-side template copy (`_install_box_handbook`), NOT a seed
   and NOT a `seeded` category entry: handbook templates are HOST templates, not GUEST templates. It
   fills `{box.canon}/handbook`, which no guest sees directly — the RO
   `box.bindings.ro[~/canon/handbook/box]` BIND delivers it, and that bind is an ordinary key, so
   single-route is intact.

⚑ **Step 3 is THE ONLY ROUTE THAT FILLS THE BOX HANDBOOK CHAPTER.** Nothing declares a `seeded` entry
at `{box.canon}/handbook`, so a box created without step 3 gets an EMPTY chapter. Do not "simplify" it
away by folding it into step 2.

⚑⚑ **The create-time `synced` write is NOT a fourth step here** — it is the SIBLING
`_sync_box_at_create`, which every caller runs immediately after this returns. A sync is an
OVERWRITE, so folding it in would falsify the create-if-absent contract; and this function's resolve
is deliberately NARROW (no bind map), while the sync needs a FULL one.

The per-launch credsync REFRESH and the channel guarantee-create are SEPARATE per-launch mechanisms
and are NOT part of this one-time seed.

### `_apply_init_seeds` — the applier, and why it RETURNS its snapshot

It copies the configured copy-once-at-init seeds into the new project's shell dir, and RETURNS the
expanded snapshot this resolve built, so the caller's SIBLING step 3 reads resolved keys off the SAME
snapshot instead of building a second one — two builds could disagree about a repointed
`workset.template`.

⚑ **THE TARGET'S TABLE IS NODE-ADAPTED HERE TOO** (`agent_categories_for_node`) — the SECOND fold of
`default_seeds()`, independent of the launch fold. The template layers beside it are already
NODE-keyed by `template_seed_defaults`, so exactly one of the two tables is adapted.

⚑ **IT READS THE AGENT FILE'S SCALARS** as well as its tables: the layer-2 source `agent.<a>.template`
is one. See *`agent_cfg_path`* under `_resolve_launch_snapshot`.

It is ADDITIVE: with no seed config and no target default seeds, it copies nothing. It applies the
COLLAPSED SEED LIST at `meta.assembly.seeded` (via `_launch_seed_list`), translating each guest dest
(`/home/agent/X`) to a host path under `proj.shell_path` and copying its source → that path once.

### The layered `seeded[~/]` trio flows through this same route

The template layers all target `~` (the create-time home); the collapse's seeded arm keeps every
same-dest COPY (copies OVERLAY, they do not shadow — `settings.store_collapse.collapse_seeded`), so
the `~`-group is a list of layer sources in scope apply order that `launch.templates.stage_layers`
stages PER-FILE LAST-WINS, then copies into home CREATE-IF-ABSENT. A layer whose source dir is absent
(an unpopulated `{workset.template}`, say) is skipped.

⚑ **`seeded[~/]` IS THE SPELLING OF THE TRIO** — spec §2a's own seed-table form: the TERMINAL key
`<scope>.seeded` (`system` / `agent.<a>` / `workset`), at the one entry its dest-keyed map holds — the
guest home `~/`, whose source is that scope's `template` scalar plus `box/home`. ⚑ There is NO
`seeded.template` key: the destination IS the entry's identity (R-10), so an entry NAME is not part of
the keyspace.

### The credential gate, and ONE destination namespace

The credential gate (D-M4) is applied ONCE, inside the resolve this pass reads, above every consumer
(`settings_categories.gate_credential_delivery`): a credential-flagged `seeded` entry is suppressed for
a PRIVATE box (*deliver_creds* False).

⚑⚑ **ONE DESTINATION NAMESPACE** (spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*). Every `seeded` dest is
GUEST-spelled, and ALL of them are resolved to the box store by the ONE translator
`container._guest_dest_to_host`, whose `map_home_root=True` maps the bare home to `proj.shell_path`
(= `<box_dir>/home`). See `settings_categories.CategoryEntry` for why a host-spelled dest is not
spelled.

---

## `_core_env_default_categories` — the four `KANIBAKO_*` stamps, as launch-DERIVED `system.env.*` keys

⚑⚑ **THE ONE PLACE THEY ARE SPELLED** (keyspec §2a env.<VAR>; [Jei:2026-08-14]: *"the 'KANIBAKO_'
stuff should be in system.env"*).
`_core_env_default_categories(proj=, target=, agent_id=)` returns `{"system.env.<VAR>": value}`, and
`_resolve_launch_snapshot` merges it into the default-categories floor under `family="core env"`. They
are ordinary system-scope floor keys, so they enter the ONE channel with everything else — a nearer
`system.env.<VAR>` file entry overrides one, a TWIN at another scope REFUSES the launch naming both keys
(`store_collapse.collapse_env`), `-e` reaches them by overriding the stamp's own key VALUE inside that
same collapse, and `box show --effective` shows them. Nothing above the channel may write these
variables; adding a fifth stamp anywhere else is the bug this function exists to prevent.

The values are RESOLVED LITERALS, not `@`-refs (the `meta_identity_floor` pattern), and the floor is
the BASE level, below every settings file — which is what makes the override story "write the same
key in a nearer file". ⚑ **The per-variable GATES live inside this one function**, not at the merge:
splitting it would put "which variables a box gets" in two places under two `family` labels.

Contracts the signature cannot carry:

* **`KANIBAKO_NAME`** is the peer-communication instance identity and is emitted only for a NAMED
  project — an unnamed one leaves the variable unset.
* **`KANIBAKO_DIRECTIVE_SEED`** is UNCONDITIONAL even though its kickoff BIND is descriptor-gated: a
  no-agent box gets the variable and must keep getting it. 🛑 Do NOT "tidy" it onto the bind's gate.
  The path is READ BACK from the ONE declaration of the slot (`core-defaults.yaml` `kickoff.box_dest`,
  the source of core's own bind) — the variable and the bind MUST name the same file. The per-agent
  FINAL slot is each plugin's own `agent.<agent>.env.KANIBAKO_DIRECTIVE_FINAL`.
* **`KANIBAKO_AGENT`** is the resolved agent stamped ON THE CONTAINER and never in durable config (that
  is what keeps `--agent` ephemeral): a later `kanibako start` against the running box, `stop`'s
  writeback and the creds watcher all read it back rather than re-running the selection cascade. It
  carries the NODE identity (full persona), NOT the harness, in the OUTSIDE spelling (`+`, via
  `display_agent_ref`), because an env var is a place a HUMAN looks. 🛑 **Readers CANONICALIZE, THEN
  derive**, through `agent_ref.agent_address_node` (a plain-shell box is stamped `shell`, which the
  claimant grammar refuses; the harness folds to node case, keyspec §0
  "An agent's canonical case is carried by its _name_") — `harness_of` splits on `℘` alone, so
  deriving from the raw stamp returns the whole string and `resolve_target` hunts a plugin that
  does not exist (in `stop.py`, under a blanket catch: writeback stops SILENTLY). Emitted only when
  a TARGET resolved: `kanibako shell` and an explicit-entrypoint launch resolve none and leave the
  variable unset, while a plain-shell BOX (`system.agent: shell`, keyspec §2b) resolves
  `ShellTarget` and is stamped `shell`.
* **`KANIBAKO_AGENT_MARKERS_DIR`** is UNCONDITIONAL: the supervised path and the warm-up panel watch
  both enumerate the dir every agent session's start hook writes its per-PID marker into, and the
  supervisor reads the SAME dir via `--agent-markers-dir`.

---

## `_broken_standalone_error` / `_standalone_store_error` — the state `_unbuilt_box_error` cannot see

A standalone box lives inside the user's own directory: the root is theirs. When its store is gone or
unreachable, the REGISTRY entry survives but the box resolves NAMELESS, and the explicit-create gate
reads it as "no box at all" — which `_unbuilt_box_error` cannot see, because a standalone box's
`metadata_path` IS the user's project root and that is still there.

Without this branch that state falls through to the generic "no box" message, whose suggestion is
built from the user's own spec — so a bare standalone NAME would produce `kanibako create <name>`,
which creates a directory literally named `<name>` in the CWD with a PRIMARY box in it. Two grammars,
one token: `start` resolves a bare token in the NAME grammar, `create` in the PATH grammar.

`_broken_standalone_error` matches *project_dir* as a registered NAME (case-blind), then as a
registered ROOT taken literally, and hands the root to `_standalone_store_error`, which also serves an
UNREGISTERED root (whose own `registry: null` made the probe standalone).

### The marker first, then two questions

⚑ **The marker is asked FIRST** (`box_resolve.standalone_marker_error`): without it the root is no
standalone, so every store question would answer for a box kanibako does not see.

Then BROKEN is two questions. **Question 1: is the DEFAULT `box_data/` leaf gone?** **Question 2: is
the key-resolved store (`workset.boxes`) missing?** Both pass → nothing is broken, `None`. A
`workset.boxes` that is not at its default gets the store-move cure (`_store_move_cure`): the leaf
moves TO the key's place, so the user's data is neither copied onto anything nor left stranded, and
no move is printed onto an existing path or into the old store itself. A null key answers question 2
with its refusal and no move. Otherwise question 1 failing gets the Rebuild. ⚑ The Rebuild stays
pinned to the DEFAULT leaf (decision 8): a key-resolved Rebuild gate reads the repoint, so a
repointed box would get an empty rebuild over a store that still exists.

### Each clause of the Rebuild cure is load-bearing

The cure is by ROOT, with no `--name`: `create --standalone` keeps the root's stored kuid, so the
rebuilt box comes back under its old name (and a stored name may break the rule, which `create`
refuses as `--name`).

* **Registered:** `kanibako box rm <root> && kanibako create --standalone --register <root>`, on ONE
  `&&` line so it cannot be half-followed. The `rm` fires only with the default leaf gone, so
  `_rm_standalone`'s deregistered-park arm is gated OFF (its `metadata_dir.is_dir()` test fails) and
  the `rm` drops the registry entry and touches NOTHING on disk; widening THAT arm would turn the
  suggestion into the destructive parking variant. `--register` re-indexes the box, which a standalone
  `create` does only on request.
* **Unregistered:** `kanibako create --standalone <root>`.

⚑ **The CURE LINE** is the part a future `repair` verb replaces (the MBR-6 row in the canon workbook
plans), exactly as in `_unbuilt_box_error`, whose three-part shape (`Error:` / *why we won't* /
`Rebuild it:`) this deliberately reuses.

---

## `_refuse_null_workspace_bind` — a launch with no workspace to mount (Q106)

```python
def _refuse_null_workspace_bind(std, proj: ProjectPaths) -> None
```
The workspace bind is mounted at every launch, in every mode (system-design § "The workspace bind"),
so a box whose `meta.box.workspace` resolves through a null `workset.workspaces` cannot run — the Q106
answer: *"if a critical bind is <None>, launch should fail"*. `_run_container` calls this right after
`_unbuilt_box_error`, on the same NON-materializing probe, so the refusal leaves nothing on disk;
every launch door reaches it there (`start`, bare `kanibako`, `shell`, `code` via `start_detached`,
and `agent reauth`'s setup-only launch). It picks the workset root (standalone: `metadata_path`;
named: `group.root`) and hands off to `project.workset.refuse_null_box_workspace`, which owns the rule
and the message; the `WorksetError` propagates to the CLI's rc-1 handler. Primary returns at once:
its workspace is the project dir, not a workset key.
⚑ A standalone box under a null carries `project_path = None`, so this is the one refusal for it;
past the probe, `_run_container` narrows the type (`assert`) before the bind-shadow check and
`ContainerRuntime.run`.
