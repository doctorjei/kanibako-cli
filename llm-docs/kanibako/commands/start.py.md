# `commands/start.py` — overflow prose

⚑ **PARTIAL BY DESIGN.** This file is written OPPORTUNISTICALLY, as `start.py` is touched. It does
not describe the module as a whole; each section names the seam it covers and nothing else.

---

## `_install_assembly_collapse` / `_split_home_bind` — the collapse wiring (roadmap step 6b)

**Authority:** Jei's roadmap step 6, verbatim — *"implement a 'grand unification function' … that
will **merge the information, but not perform the action**"* ·
`designs/collapse-implementation-DESIGN.md` §0/§1 · `designs/grand-unification-collapse-DESIGN.md`
§2a (home is pid 0).

### What it is

`_resolve_launch_snapshot` folds the same `CategoryEntry` list the live route already produced
(`snapshot_category_entries`) through the step-4 producer (`build_store_shape_set`) and the step-6a
collapse (`collapse_store_shapes`), and stores the two results at the declared RO/derived keys
`meta.assembly.bindings` and `meta.assembly.copies`.

### What it is NOT

**It drives nothing.** `snapshot_category_entries → reconcile_categories → emission` runs exactly as
before, and every mount, copy and env the box receives still comes from `reconciled`. Nothing reads
`meta.assembly.*`. The cutover — pointing emission at the collapse, retiring
`reconcile_categories`' arbitration half, the row-5 warn channel and the `synced`↔`binding` refusal
— is a LATER step, and none of it may be smuggled in here.

That is also why the wiring reuses the existing walk rather than adding a second one: two walks
could disagree about what was declared, and only one of them would be the one that ships.

### Home is pid 0, so it is lifted OUT of the fold

`collapse_store_shapes` seeds `combined_bindings` with home BEFORE any scope folds, and takes it as
its own parameter. A `store_shape` that also carried home would therefore collide with the seed on
the very first scope. `_split_home_bind` removes the home mount from the entry list and hands it
over separately.

The home entry is identified by its DESTINATION — the one MOUNT entry with a source whose
`normalize_bind_dest(box_dest)` is `store_collapse.HOME_DEST`. Not by key, not by category, and
never by splitting a dest on `.`: a destination is data.

**Zero or several such entries ⇒ no write at all.** There is then no pid 0 to build on, so there is
no assembly to describe. In practice this is what makes the narrow resolves (`box show
--effective`'s families-off siblings, the conditional image and helper resolves) a no-op: they carry
image and helper binds only, and no box home.

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
collapse is caught at this one seam: both leaves stay ABSENT (the state the manifest already names
for them — *"declared so the closed keyspace admits the name"*), the launch continues on the
unchanged live path, and the cause is logged.

**The log level is `debug`, deliberately.** A `warning` would tell a user their configuration has a
problem when it does not: it is legal on the route that ships, and the computation that rejected it
changes nothing they can observe. The message is for whoever is building the cutover.

⚑ A partial write is worse than no write, which is why both leaves are installed only AFTER the
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

⚑ **This is the failure `critical_keys` already paid for once.** `agent_delivery_mounts` tests
`e.name in critical_keys` where `e.name` IS the destination (R-10), so a caller passing key NAMES
matches nothing and every critical bind silently degrades to best-effort — a missing agent binary
reaching podman as a crun crash instead of a clean exit-1. Same shape here: a key-spelled
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
plugin descriptor's `AGENT_CRITICAL` dest set, today `agent_delivery_mounts(critical_keys=…)`) ·
**skip-if-absent** (this one) · **warn-and-drop** (the L7 default, and the reason the parameter
defaults EMPTY). The must-exist set joins this signature at 2a-3, when the two emitters merge into
one. ⚑ It must then be consulted BEFORE the rw guarantee-create branch, or a critical dest with a
missing source gets `mkdir`'d into existence instead of raising.

### It is passed at every call site, including the narrow ones

The image and helper resolves run with `include_base_families=False`, so they carry no canon floor —
but they still read the user's cascade files, so a user-declared bind at a chapter dest reaches them
too. A policy that varied by call site would decide one destination two ways.
