# Templates — the layered box seed, the one copier, and the packaged install

`launch/templates.py` owns every route by which template content reaches a store: the LAYERED box
seed applied once at create, the two HOST-side template copies (workset store, box handbook
chapter), the packaged-content install into the host stores, and the single copier all of them go
through. It also carries the `kanibako setup` reporting classifier and the packaged-content digest.

Three separate things are easy to confuse here and the module keeps them apart deliberately:

* the **box seed** — `seeded`-category COPIES into the GUEST home, resolved through the keystore;
* the **host templates** — copies into a host store (`install_workset_template`,
  `install_box_handbook_template`) that a later bind, not the copy, delivers into a box;
* the **packaged install** — an enumerated set of (packaged subtree → host store) pairs fired at
  first-run init and at `kanibako setup`.

## The layered box seed (spec §2a, "Template seed (LAYERED, ordered)")

The seed model layers three ordered sources into the box store's ONE seeded destination at
creation (base → agent → workset; later overlays earlier) — THREE keys in total:

```
1 system.seeded[~/]        | (@system.template/box/home)
2 agent.<a>.seeded[~/]     | (@agent.<a>.template/box/home)
3 workset.seeded[~/]       | (@workset.template/box/home)
```

The layer SOURCES are NOT derived on disk — they are ORDINARY keystore `seeded` category keys
resolved through the launch snapshot (ruled 2026-07-09 Q1: everything goes through the keystore
plus seeding, no bespoke template route). `template_seed_defaults` declares them as
default-category entries; the seed seam (`commands.start._apply_init_seeds`) resolves them off the
committed snapshot and applies the dest's layers IN ORDER via `stage_layers`.

Sources: `@system.template` (system.* settings tier) · `@agent.<a>.template` =
`@config.agents/<a>/template` — the ACTIVE NODE's own store, harness or persona alike ·
`@workset.template` = `@meta.workset.path/template` (skip-if-absent — the seeded category drops a
layer whose source dir is absent).

### THERE ARE NO HANDBOOK LAYERS HERE, AND THIS IS NOT AN OVERSIGHT

Until 2026-08-07g three more layers seeded `@box.canon/handbook` from each scope's
`box/canon/handbook` subtree. Jei RULED them OUT of the category — *"they do not DIRECTLY interact
with the box itself … They are HOST templates, not GUEST templates"* — and the ratified spec's §2a
no longer declares them, so nothing in the keyspace names a `seeded` entry at `@box.canon/handbook`.
The box's own handbook chapter is filled by `install_box_handbook_template`, a HOST-side copy at
create; read the QUARANTINE section below before touching it. The box HOME layers stay in the
category because the home IS what the guest sees at `~`.

### The dest is the GUEST home `~/`

`_SEED_DEST_HOME` is `"~/"` — the GUEST home (spec §0 "ONE DEST SPACE, TWO DELIVERIES"; §2a's seed
table spells it `seeded[~/]`). A COPY's guest dest is the SPELLING; it is RESOLVED to the box store
when the copy runs — which is what lets a copy that happens BEFORE any guest exists still write
`<box_dir>/home`, the very directory the box home bind then delivers at `~`.

`~/` is the ONLY seed destination — "SEED DESTINATIONS ARE ENUMERATED, NEVER A WHOLE-DIRECTORY
COPY", because a wholesale `template/box/* -> <box_dir>/*` copy could plant
`<box_dir>/box.yaml`, which IS `meta.box.settings`, the LAST cascade level. Enumerating the
GUEST home rather than the box dir makes that stronger, not weaker: `~/` cannot name
`<box_dir>/box.yaml` at all, because the box dir has no guest spelling.

⚑ RESPELLED 2026-08-08c from the host path `@meta.box.path/home`, TOGETHER with the key-shape flip.
That absolute HOST spelling needed a per-entry `dest_space` discriminator to stop the guest
translator re-rooting it under the box home on a host whose user home is `/home/agent`: a box store
under `/home/agent/.local/share/…` starts with the GUEST home prefix and no prefix test can tell
the two apart. The respell removed the ambiguity instead of carrying it. See
`settings_categories.CategoryEntry`. Do not spell a copy dest host-side again; the discriminator
that made it safe is gone.

⚑ `@box.canon` IS NOT `~/canon`. See `settings_categories.CategoryEntry`.

### The per-layer SOURCE subpaths

`_SEED_SRC_HOME = "box/home"` and `_SEED_SRC_HANDBOOK = "box/canon/handbook"` are the subpaths
under each layer's `template` root. The two-level `box/` is the declared WHITELIST BOUNDARY (J-2):
everything under it is box ENDPOINT content and gets the box whitelist; `home` and `canon/handbook`
are the box store's two allowed top-level entries, not decoration. ⚑ Only `home` is a SEED source
now — `canon/handbook` is read by the host-side `install_box_handbook_template` copy (2026-08-07g),
which is why the two constants no longer sit in the same table.

`AGENT_TEMPLATE_STORE_REL = "template"` is the `template` entry of a SCOPE STORE — the subtree the
per-agent and per-workset layer SOURCES (`@agent.<a>.template` / `@workset.template`) point at by
default. It is named because three things must agree on it and one of them fails SILENTLY: the
layer-2/3 source key defaults, the whitelist entry that permits it, and the persona-share symlink
(`commands.start.ensure_persona_share_symlinks`) — spell that one differently and the L7
guarantee-create makes a real directory beside the link, and sharing simply stops.

## `template_seed_defaults` — the DEFAULT-category table

Returns the layered box-seed DEFAULT-category table (spec §2a — THREE layers): three ordered layers
into ONE enumerated destination, as ORDINARY keystore keys, ready to fold into the seed-time
snapshot's `default_categories` (`commands.start._apply_init_seeds`) so they resolve and apply
through the SAME single seeded-category route as every other seed — no bespoke template plumbing
(Q1). Each is a `seeded` COPY into the GUEST home `~/` — resolved to the box store when the copy
runs (spec §0) — sourced from an `@`-ref SETTINGS key so the source stays user-repointable through
the cascade (setting `workset.template` / `agent.<a>.template` reroutes that layer):

* `system.seeded` — ALWAYS (Q4: no carve-out).
* `agent.<a>.seeded` — only when an agent is bound; the source key `agent.<a>.template` defaults to
  `@config.agents/<a>/template` (spec §2a/§2d; `<a>` = the ACTIVE NODE — persona or bare harness).
  ⚑ The KEY segment is the CANONICAL node (`persona℘harness`); the VALUE is a store DIRECTORY, so
  it is the `+` spelling `settings.agent_config.store_dirname` produces. Absent for a NO-AGENT box. ⚑ NODE-ROOTED since 2026-08-27 — see "the persona's template
  is SHARED BY LINK" below; it used to spell `harness_of(<a>)`, which for a bare agent is the same
  string and for a persona silently named the wrong store.
* `workset.seeded` — only for a PRIMARY/NAMED box (a workset tier exists); the source key
  `workset.template` defaults to `@meta.workset.path/template` (Q3, was `<None>`). STANDALONE has
  no workset tier, so the layer is OMITTED. Each layer is SKIPPED when its source dir is absent —
  the seeded category's ordinary missing-source semantics.

⚑ A FOURTH KEY, AND IT IS NOT A LAYER: `agent.default.template` =
`@config.agents/default/template`, the §2d DEFAULT-TIER arm of the layer-2 source, emitted under
the same `if agent_id` gate. It is INERT for delivery — the node arm above is emitted
unconditionally, so the §2d fallback to it never fires (proved by mutation: poisoning this arm
moves no seed, poisoning the node arm reds ten cases) — and it is here because the key is DECLARED,
so some artefact must carry its value or `system defaults` prints a row it cannot source. It is
emitted HERE rather than beside its sibling `agent.default.canon` (`core_defaults`) because this
module owns `AGENT_TEMPLATE_STORE_REL`; `defaults_inventory.source_groups` labels it
`launch/templates.py (layer-2 seed, default arm)`, split from the node arm's label because the node
arm is spelled one `@`-hop from the registry and this one has a plain value oracle. (The node arm's
harness-vs-node divergence — finding 1 — is CLOSED as of 2026-08-27; only the hop is left.)
⚑ It carries NO node-store probe, unlike `canon_default_categories`' `store_canon if
node_store.is_dir() else …` node arm — that conditional is the canon key's own behaviour.

The returned dict mixes the SEED tuple keys with their SOURCE scalar keys (`workset.template` /
`agent.<a>.template`) so both land in the snapshot floor: the scalar resolves the `@`-ref, and a
user override of the scalar (config set / settings file) wins by cascade precedence and reroutes
the seed. `system.template` is already floor-materialized (it is a `system.*` settings-tier path),
as are `@meta.box.path` and `@box.canon` (`settings_launch.workset_anchor_floor`), so none is
re-declared there.

⚑ The SOURCE keys are shared with the box HANDBOOK host-template copy
(`handbook_layer_source_keys`), which reads the SAME three `<scope>.template` scalars this table
declares and is gated by them — that is why the handbook layers leaving the `seeded` category
(2026-08-07g) did not make the box handbook any less repointable.

Each layer's value is a DEST-KEYED map, not a named entry (2026-08-08c): the destination IS the
identity and the value is the 1-element `(src,)` — `opts` is RESERVED on a COPY and no shipped
layer sets it. The per-agent SOURCE key is `@config.agents/<a>/template`, a resolvable/settable
keystore key. STANDALONE (no workset channels) omits BOTH the workset source and its layer, because
its workset tier is `<None>`.

### The persona's template is SHARED BY LINK, not by copy (ruled 2026-08-27)

Layer 2 is rooted at the ACTIVE NODE, so a persona (key `navigator℘claude`) reads
`agents/navigator+claude/template`, not `agents/claude/template`. The harness's CONTENT still
reaches it: `commands.start.ensure_persona_share_symlinks` — the shim that already pointed a
persona's `common/<leaf>` dirs at the harness's — also links the whole `template` store root
`agents/<node>/template` -> `agents/<harness>/template`.

Jei's reasoning, and the alternative he rejected: *"I thought about copying the template. And I
think some users will want to. But of course there is the staleness issue. And chewing on it, the
user can always remove the symlink if they want to create a separate template for the persona-based
agent — so I think symlinking template is the right approach here."* The ESCAPE HATCH is the shim's
own never-clobber rule: a real `agents/<node>/template` directory is left alone for ever, so
replacing the link IS how a persona takes ownership.

⚑⚑ WHY A LINK IS SAFE HERE AND A LINK INSIDE A LAYER IS NOT. `template` is unlike `common`:
`common` is a live BIND, but `template` is a seed source consumed by a COPY. `stage_layers` (and
`copy_tree`) REFUSE any symlink they meet while walking a layer — the §2a exfiltration guard. The
shim's link is an INTERMEDIATE path component of the layer root (`<node>/template` sits ABOVE
`box/home`), so the walk's per-entry `is_symlink()` never lstats it, the OS traverses it, and the
box is seeded with BYTES. A link at a LEAF inside the layer would still be refused, correctly.
Measured, not assumed; pinned by
`test_start.py::TestPersonaShareSymlinks::test_the_seed_copier_reads_THROUGH_the_template_link_by_value`
and end-to-end by `test_templates.py::TestPersonaTemplateLayerThroughTheLink`.

⚑ The shim lays the template link BEFORE its own no-target return, because `template` — unlike
`common` — is not target-declared: `template_seed_defaults` emits the node arm for every agent id,
installed plugin or not.

⚑ Nothing guarantee-creates `agents/<node>/template` behind the shim's back. The L7
guarantee-create acts on MOUNT sources, and a seed source is a COPY; `ensure_agent_stores` (which
DOES mkdir a `template/box/...` skeleton, and would do it THROUGH the link) is only ever called
with discovered plugin/harness names, never a persona node.

### `seed_keys_of` is GONE — do not rebuild it

`seed_keys_of(defs)` USED TO LIVE HERE and was removed 2026-08-08c. It derived the HOST-space key
set the launch resolve needed (`settings_launch.snapshot_category_entries(host_dest_keys=…)`) as
`{key for key in defs if ".seeded." in key}`. TWO things retired it at once, and only the second is
a design decision:

1. It BREAKS MECHANICALLY at the key-shape flip. The key is `system.seeded` now, not
   `system.seeded.template`, so the predicate is False for every layer and the set would come back
   EMPTY — silently, tagging the trio guest and landing the box-home seed where nothing reads it.
2. The CURE is the RESPELL, not a replacement carrier. The dest is `~/` — a guest path — so there
   is no host destination left to discriminate and nothing for a key set to select. Do not rebuild
   one under another name.

## `stage_layers` — the layered-copy MECHANISM

Seeds *dest* once from the ordered *layers* via a TEMP staging dir. The per-file LAST-WINS merge
across the ordered layer dirs is resolved in a temporary staging dir (where overwrite is intended),
and the merged tree is then copied into *dest* with CREATE-IF-ABSENT (an existing *dest* file is
NEVER overwritten). Two phases:

1. **Stage.** Copy each layer's files into a temporary dir in order (LOWEST → HIGHEST). Overwrite
   WITHIN staging is intended, so a later layer's file at the same relative path wins (per-file
   last-wins).
2. **Seed.** Copy the merged staged tree into *dest* with `copy_resource_tree_if_absent` — a
   pre-existing *dest* file survives untouched. This is the load-bearing failsafe against re-seed
   DATA LOSS.

SKIP-IF-ABSENT: a *layers* entry that is not an existing directory is silently skipped (spec §2a
"layer skipped if the source dir is absent" — e.g. an unpopulated `@workset.template`). SEED-ONCE:
the caller invokes this only at box CREATE (registry MEMBERSHIP is the seed signal —
`settings-keyspace-1.8.0.md` §0 "Seed-time vs cascade", `system-design-1.8.0.md` § "Detection &
import"), never on a relaunch. No file is special-cased or merged — every file is a plain ordered
copy (no CLAUDE.md merge, D-B5).

This is the layered-copy MECHANISM only; the layer dirs are resolved through the keystore by the
caller (`commands.start._apply_init_seeds`), NOT derived on disk here — the "sole intermediary is
the keystore" invariant (Q1/Q3/Q4).

The SOURCE SYMLINK refusal (spec §2a enforcement point 3) is checked in the staging loop as well as
in `copy_tree`: staging is where layer content is first read, and the `is_file()` test would FOLLOW
a symlink-to-file, so the exfiltrated bytes would already be in the staging dir by the time the
shared copier saw them (as a plain file).

## THE ONE COPIER — `copy_tree` (P-S4)

Every template stage, box seed and host-store fill routes through `copy_tree`, so the whitelist and
the traversal defences cannot be present on one path and missing on another.

It mirrors the relative tree of *src* into *dest*. Existing destination files are left untouched
(create-if-absent) so user edits to a seeded template survive a later kanibako upgrade. Directories
are created as needed.

When *overwrite* is True (the TRUE-REFRESH path used by
`install_packaged_templates(..., refresh=True)`) an existing destination file IS replaced with the
packaged version. ⚑ That flag is confined to the SYSTEM-OWNED packaged STAGING
(`@system.template/**`) — user-owned stores (`@system.canon/handbook`, `@config.agents/**`,
worksets, boxes) are create-if-absent on EVERY path, always, and their differences are REPORTED
rather than written (J-3 item 1). The default (False) preserves the load-bearing create-if-absent
contract — the alias `copy_resource_tree_if_absent` (reused by the box-SEED apply in
`commands.start`) must NEVER clobber a per-box home file. That alias is the create-if-absent
spelling other modules reuse; it names `copy_tree`, whose skip-if-present behaviour is what the
longer name is asserting.

*scope* (`"box"` / `"agent"` / `"workset"`) turns on the §2a WHITELIST, evaluated on each entry's
path RELATIVE TO *dest_root* — so it is correct both for a whole-store copy (*dest* IS the store
root) and for a copy into a subdirectory of one (the workset stamp's `canon_only` narrowing, which
passes `dest_root=workset_path`). It is OMITTED only
where the dest is not a scope store at all: the box SEED's dest is key-fixed at `home`, the box
handbook host-template copy's is key-fixed at `canon/handbook`
(`install_box_handbook_template`), and the packaged handbook's dest is inside the canon root.

*dest_root* is BOTH the containment boundary and the whitelist's frame of reference (defaults to
*dest*).

⚑⚑ *scope* IS A NAME OR A `WorksetStampScope`, NEVER AN ALLOW-LIST. It used to be a name plus a
companion `allowed` tuple that the workset stamp filled in, and *respells, never widens* then held
only because that one caller behaved. Passing the RESOLVED ROOTS instead moves the respelling
inside `_scope_rules`, where both arms end at `SCOPE_WHITELISTS` — a widened scope is
UNREPRESENTABLE rather than merely undone, because no parameter on the route accepts entries.

### The four enforcement points — all of §2a's, every one BEFORE any write

1. WHITELIST on the leading component(s) of the store-relative path.
2. CONTAINMENT of the resolved destination parent (the `..` escape, and a symlinked intermediate
   DIRECTORY) — checked ahead of the `mkdir`, so a refused copy leaves nothing behind. Creating the
   parent first would litter directories outside the destination subtree on the very path we are
   about to refuse. `resolve()` sees through a symlinked intermediate dir that already exists,
   which is the attack; a not-yet-existing parent resolves to its would-be path and passes.
3. SOURCE SYMLINK refusal — `Path.rglob` does not recurse into symlinked dirs, but
   `entry.is_file()` FOLLOWS a symlink-to-file and `copy2` would then copy the TARGET's bytes, so a
   template containing `x -> ~/.ssh/id_ed25519` exfiltrates it into the box home. Checked BEFORE
   `is_file()`, which would follow it.
4. DEST SYMLINK refusal — the LEAF, by `lstat`, checked before the two write branches, both of
   which follow it. The parent check cannot see this one: `target.exists()` follows a symlink, so a
   DANGLING one reads as absent and `copy2` then writes THROUGH it; and with *overwrite* a live one
   is followed and a file OUTSIDE the subtree is replaced. The escape is the leaf itself.

*check_only* runs every one of those four and writes NOTHING — the PRE-FLIGHT form. It exists
because "refuse loudly" is not the same as "refuse cleanly": a refusal part-way through a walk
leaves the files copied before the offender, and where the caller has already REGISTERED something
(`workset create`) the user is left with a half-built store to clean up by hand. ⚑ It is the SAME
function, deliberately — a separate validator would be a second copy of the rules, free to drift
from the one that actually writes. Pair it with the real call; do not use it as a substitute for
one (nothing here is atomic against a concurrent writer).

The equivalence short-circuit under *overwrite* exists because PREVIEW AND ACTION MUST TELL ONE
TRUTH: `plan_template_refresh` classifies an EQUIVALENT file as "current" and does not report it,
so a refresh that rewrote its bytes anyway would silently revert a user's whitespace/comment edit
in the staging the preview just called unchanged. Same classifier, same verdict, both sides.

## The per-scope whitelists — `SCOPE_WHITELISTS`

Per-SCOPE store-root WHITELISTS (spec §2a, ruled by Jei 2026-07-30). The KEY INSIGHT that makes
them one predicate instead of a list of special cases: each packaged per-scope template subtree
MIRRORS THAT SCOPE'S STORE ROOT, so the whitelist is exactly the set of top-level entries that
subtree may contain.

⚑ DENY-BY-DEFAULT. Anything not listed is an ERROR. The DENIED entries are not hypothetical and
their severities differ:

| scope | denied, and why it matters |
|---|---|
| BOX | `box.yaml` = `meta.box.settings`, the LAST cascade level, so template content would become the box's TOP-PRIORITY settings, carrying any key it liked (CORRECTNESS). Create-if-absent is no defence: on a fresh box there is nothing there to lose the race. |
| AGENT | `agent.yaml` = `meta.agent.<a>.settings` (CORRECTNESS); `caches/`. |
| WORKSET | `workset.yaml`; `registry.yaml` = `workset.registry`, the AUTHORITATIVE box membership + names, so a templated one could ORPHAN or COLLIDE boxes (CORRECTNESS); `auth/` (CREDENTIALS), `vault/`, `workspaces/` (THE USER'S CODE); `boxes/`, `logs/`, `channels/`. |

⚑ STANDALONE is where this bites hardest: `<workset_path>` is a directory the user ALREADY HAD, and
kanibako never deletes it — so a refused copy is not cleanable by removing the destination.

⚑ The sets are NOT one uniform rule. `common/` is allowed at AGENT scope only (Jei: there are use
cases) and behaves differently from its siblings — `canon/` is delivered RO and `template/` is
inert until a box is created, whereas `common/` is bound RW and SHARED by every box running that
agent, so starter content there is live data, not a template. ONLY `canon/handbook` is seedable,
never `canon/` wholesale: no other canon subtree has a justified seed today, and deny-by-default
means a future one needs an explicit decision.

⚑⚑ THE WORKSET ROW IS THE DEFAULT SPELLING, NOT THE ONLY ONE. Both its entries name repointable
keys — `template` is `workset.template`, `canon/handbook` is the chapter under `workset.canon` — so
the workset stamp respells them per root. A repoint MOVES an entry; it never adds one.

### `WorksetStampScope` — the respelling as a TYPE, not a discipline

A frozen `(workset_path, canon_root, template_root)` carrying the paths the respelling is DERIVED
from and NOTHING ELSE, with `name = "workset"` as a ClassVar because choosing the declared row is
not a caller's decision. `allowed()` is `_workset_scope_allowed` over its three fields.

⚑⚑ WHAT IT DOES NOT CARRY IS THE POINT. There is no entries field and no entries parameter anywhere
on the route, so *a repoint MOVES an entry, it never ADDS one* is structural: every entry the
copier can see is `SCOPE_WHITELISTS["workset"]` with its two repointable ANCHORS re-spelled — same
cardinality, `canon/` seedable at `handbook` and nowhere else. Pinned by
`TestTheRespellingCannotWiden` (no `allowed` parameter; fields are the three roots; cardinality and
shape held across every repoint shape a root can have).

`_scope_rules` is the ONE place a scope becomes entries: a plain name reads the declared table
verbatim, a `WorksetStampScope` derives them.

### `_workset_scope_allowed` — the workset row, respelled for one root

Returns the workset whitelist with those two entries spelled as THIS root's resolved leaves: the
store-relative `workset.template`, and the store-relative `workset.canon` plus
`_CANON_CHAPTER_LEAF`. Once the copy's dest follows `workset.canon`, the frame it is judged in has
to follow too, or deny-by-default refuses the very copy the whitelist was written to permit.
Reached only through `WorksetStampScope`.

⚑ The defaults are READ OFF `SCOPE_WHITELISTS`, never re-spelled, so an unrepointed root yields
that tuple EXACTLY —
`test_templates.py::TestWorksetStampFollowsTheKeys::test_the_respelling_degenerates_to_the_declared_table`.

⚑⚑ `relative_to` ALONE IS NOT THE CONTAINMENT TEST and must not be used as one. It is LEXICAL, so
`<root>/../up` IS relative to the root, and the respelling emitted the allow-list ENTRY
`../up/handbook` — a standing permission to write outside the store, produced by the one function
whose contract is that it cannot. `_is_contained` resolves both sides, so an escaping leaf
degenerates to the declared entry instead
(`TestWorksetStampFollowsTheKeys::test_a_dotdot_leaf_does_not_widen_the_allow_list`).

⚑ A leaf resolving OUTSIDE the workset root keeps its DEFAULT spelling. That arm is now a FAILSAFE
rather than the live answer: the stamp refuses an escaping leaf in `_workset_stamp_dirs` before
this function is reached.

### `_check_whitelist` — which relative path it reads

RAISES unless *store_rel*'s leading components are inside *scope*'s allow-list. *scope* is a NAME
or a `WorksetStampScope`; the allow-list is computed here via `_scope_rules`, never handed in.

⚑ *store_rel* is relative to the SCOPE STORE ROOT (`copy_tree`'s *dest_root*), NOT to the copy's
source. The two coincide for a whole-store copy, but they DIVERGE the moment a copy targets a
subdirectory of a store — the workset stamp's `canon_only` arm copies the mould's `canon/` into the
resolved `workset.canon`, where the source-relative path (`handbook/SYS_CONTENTS.md`) says nothing
about which top-level store entry is being written and the store-relative path
(`canon/handbook/SYS_CONTENTS.md` at the default leaf) says exactly that. Checking the wrong one
would either refuse a legal copy or wave through an illegal one.

### `_is_contained` / `_assert_contained`

`_is_contained` is THE ONE CONTAINMENT PREDICATE: *target*'s REAL path is *root*'s real path or
below it. It catches BOTH residual escapes §2a names — a `..` component in a declared dest, and a
symlinked intermediate DIRECTORY in the destination tree that `mkdir` + `copy2` would happily write
THROUGH. `resolve()` on both sides is what makes the second one visible, and what normalises the
first; a plain string comparison sees neither.

`_assert_contained` is its refusal FOR THE COPIER, and `_workset_stamp_dirs` is its refusal for a
repointed stamp LEAF — two audiences, two messages, ONE rule. A second predicate could disagree
with this one about a symlink and the two refusals would then guard different trees.
`_assert_contained` is general and has several callers, so its message names a PATH and not a key;
a caller that knows which settings key produced the path should refuse earlier and say so.

## Packaged content install — the ENUMERATED (packaged subtree → host store) set

The content ships as STATIC files inside the installed packages (mirroring how
`image-baseline.yaml` ships under `kanibako.data`):

```
core   -> kanibako.data resource global/template/, whose FOUR subtrees
          (box, workset, agent_default, handbook) each have their OWN
          destination — the root is never copied wholesale (P-S2)
plugin -> kanibako.plugins.<agent> resource data/base/ (D4), the agent
          STORE payload
```

Destinations, and their OWNERS, because the copy rule follows the owner:

```
@system.template/{box,workset,agent}   SYSTEM-owned STAGING — refresh=True
                                       may rewrite shipped files here
@system.canon/handbook                 USER-owned — create-if-absent ALWAYS
@config.agents/{default,<agent>}       USER-owned — create-if-absent ALWAYS
```

Fired at first-run init (`cli._ensure_initialized`) and at `kanibako setup`. Every copy is
CREATE-IF-ABSENT per file except the staging rows under an explicit refresh: it adds files the user
does not yet have and never clobbers their edits (J-3 item 1).

### The packaged subtree names

`PACKAGED_BOX_TEMPLATE` / `PACKAGED_WORKSET_TEMPLATE` / `PACKAGED_AGENT_DEFAULT` /
`PACKAGED_HANDBOOK` name subtrees of the packaged template root, by their role. ⚑ The install is an
ENUMERATED set of (packaged subtree → host dest) pairs, NEVER a whole-tree copy (P-S2): copying the
root wholesale would leave a SECOND, never-read copy of the handbook at
`@system.template/handbook`, which is the duplicated-shared-data defect design principle 2 forbids
— and §2a states the same rule ("SEED DESTINATIONS ARE ENUMERATED … AND THIS HOLDS AT EVERY LEVEL")
for every level.

`AGENT_MOULD_DIRNAME = "agent"` is the AGENT MOULD's dir name under `@system.template` — the host
copy every agent install stamps from (J-5). ⚑ There is deliberately NO packaged `template/agent`
directory: the mould ships EMPTY (D5), and a wheel cannot ship an empty dir, so the host dir is
GUARANTEE-CREATED by the install action (D7). Shipping structure only is also what keeps it
OVERLAP-FREE with `agent_default`: both are stamped create-if-absent into the same store, so an
overlapping mould file would win over the default agent's own content.

`_BOX_TEMPLATE_SKELETON` is the box-template SKELETON a scope store gets guarantee-created (D7) so
the shape is discoverable: a user who wants a per-workset or per-agent box template can see where
the files go instead of having to know the layout. ⚑ Spelled to the SPEC shape —
`home/canon/{notebook,workbook}`, NOT the samples' `home/{notebook,workbook}` (D6 records that as
an oversight in the sample tree).

⚑⚑ It is relative to the TEMPLATE DIR, not to the store root, and that is exactly what lets its TWO
consumers spell that dir differently: at an AGENT store it is the fixed `AGENT_TEMPLATE_STORE_REL`
leaf (nothing about an agent store is repointable, and `workset.template` has no say over it); at a
workset root it is whatever `workset.template` RESOLVES to. Fold the `template/` prefix back into
the constant and the workset half silently ignores that key again.

### Locating the packaged roots

`_packaged_base_template` locates the packaged TEMPLATE ROOT (`kanibako.data/global/template`), one
of the two packaged content roots (`global/rom` is BOUND, `global/template` is INSTALLED — the same
bound-vs-seeded split §2c draws for the canon books themselves). Its four subtrees are enumerated
above and each has its OWN host destination; the function returns the ROOT, which is what the
staleness DIGEST walks and what `install_packaged_templates` indexes into.

⚑ It is NOT itself an install dest. Before the canon restructure this root WAS copied wholesale
into `@system.base_template` and seeded at the box home `~`, so a root-relative path was also a
home-relative one. That is no longer true — the box-HOME seed source is `template/box/home`, two
levels down — and any code treating a root-relative walk as home-relative is now silently wrong
(see `kanibako.settings.core_defaults.assert_canon_bind_seed_disjoint`, whose caller had to be
re-anchored for exactly this reason).

`packaged_box_home_template` returns the packaged BOX-HOME seed source (`template/box/home`), the
HOME-RELATIVE root: every path under it is spelled exactly as it lands in a box home. It is named
because two consumers need precisely this level and would be wrong one level up — the disjointness
guard (whose seed rels must be comparable with the `canon/...` bind dests) and any future
home-layout check.

`_packaged_shared_bundle` locates the packaged read-only built-in CANON tree (the rom root):
`kanibako.data/global/rom` — the `canon/COLLECTION.md` index plus the whole `canon/bible/` book,
which the launch path bind-mounts LIVE (ro) at `~/canon/COLLECTION.md` and `~/canon/bible` (see
`kanibako.settings.core_defaults.rom_default_categories`). It is NOT copied/seeded to a host
runtime dir, so it has no `install`/`plan_template_refresh` target; it is enumerated only for the
content DIGEST, so a drift in the shipped canon content is visible to the release-time check that
requires the matching `SETUP_FCV`/`SETUP_BCV` bump (R-38 retired the host-side staleness gate that
used to consume the digest). ⚑ Repointed from the retired `global/rom/playbook/kanibako` bundle
root when rom became the canon: the rom ROOT is now the digest root, so a file added anywhere under
`rom/` is watched.

## The plugin payload

`PLUGIN_STORE_PAYLOAD_DIRNAME = "base"` is a plugin's packaged AGENT-STORE PAYLOAD dir, and the ONE
spelling (D4). The dir is stamped into the agent STORE ROOT (`@config.agents/<agent>`), of which
`template/` is only one entry — it also carries `canon/handbook/` and may carry `common/` — so
naming the whole thing "template" would name it after one of its children.

⚑⚑ THE PAYLOAD CARRIES ITS OWN `template/box/home` PREFIX, because it lands at the store ROOT, and
that prefix MUST EQUAL LAYER 2's SOURCE, RESOLVED — what `@agent.<a>.template/box/home` becomes
once `agent.<a>.template` expands to `@config.agents/<a>/template`, i.e. `<store>/template/box/home`.
The prefix is the half that is easy to drop: a payload spelled home-relative lands at
`agents/<name>/<file>`, which NOTHING reads — so the stamp runs, reports nothing, and still leaves
the box with no agent config. Pinned by
`TestTemplateSeedDefaults.test_landing_path_equals_layer_2_source`.

### `_packaged_agent_store`

Locates a plugin's packaged AGENT-STORE payload → `Path`, returning `None` if the plugin is not
installed or ships no `data/base` (`no_agent` / a third-party target without curated content).

⚑ THE SPELLING IS CLOSED: there is no `data/template` fallback and there must not be one again. A
plugin shipping anything else contributes NOTHING rather than landing its payload somewhere
unread — a loud absence beats a silent misplacement.

## `ensure_agent_stores` — the J-6 A-action

Materialises each agent's STORE. An A-action is INSTANTIATION: stamp a new store from the current
host mould at the moment of the action, then the store is the user's. J-6's "two paths, one action"
pair share this one implementation — the deliberate trigger at `kanibako setup` (which reports) and
the lazy backstop in `cli._ensure_initialized` (silent, first run only). Both must run the SAME
full per-file stamp; the bare `mkdir` the lazy path used to do is what this replaces.

Per name, in order:

1. the MOULD — `@system.template/agent` → `agents/<name>`. Uniform for every agent, `default`
   included (J-5), and read AS IT STANDS so a user's mould customisation reaches FUTURE stores only.
2. the SPECIFIC payload — `agents/default` gets the packaged `template/agent_default` DIRECTLY from
   the package (no host staging: with exactly one default agent, a staged copy would be read once
   and never again — the principle-2 dead-copy class); every other name gets its plugin's
   `data/base`.
3. the box-template SKELETON, guarantee-created (D7).

⚑ MOULD FIRST IS SAFE ONLY BECAUSE THE MOULD IS OVERLAP-FREE. Every stamp is create-if-absent, so
on an overlapping path the EARLIER copy wins — the mould would beat the specific content. The mould
therefore ships STRUCTURE ONLY (D5); if it ever gains content, this order must flip to
specific-first.

Create-if-absent per file makes the whole thing IDEMPOTENT and SELF-HEALING: a partially-written
store completes at the next trigger, and a user's edits are never touched. Fill-out happens AT
ACTION TIME — the launch path CONSUMES stores and never creates template content. It returns the
names whose store was touched, for the caller's report.

## The workset host template

`install_workset_template` stamps a NEW workset store from the host workset mould — the J-6
A-action. `@system.template/workset` → `<workset_path>`, called from `workset create`, under the
WORKSET whitelist, whose DEFAULT leaves are `template/` and `canon/handbook/`. It is
create-if-absent, so re-running over an existing workset adds only what is missing.

⚑⚑ BOTH DESTINATION LEAVES ARE RESOLVED KEYS, NEVER LITERALS. `_workset_stamp_dirs` returns the
root's `(workset.canon, workset.template)` from ONE read of its `workset.yaml` — the same shape
`project.workset._workset_skeleton_dirs` uses for the skeleton's three, because reading that file
per key opens a window for two answers about one file. A root with no `workset.yaml` — which is
EVERY root `workset create` makes, since it refuses a root that already exists and writes no
settings file — yields the literal defaults, so an unrepointed stamp lands exactly where it always
did. The REACHABLE repoint is the STANDALONE one: that destination is a directory the user already
had, so it may already carry a `workset.yaml`. Stamping the literal `canon/` there seeds a tier
nothing reads, because the chapter bind asks the key.

⚑ The SOURCE stays a mould-side literal (`_MOULD_CANON_ROOT`) while the DEST follows the key: the
mould is one SYSTEM tree every workset stamps from, so a per-workset repoint moves where content
lands, never where it is read from. `_workset_stamp_copy` — the ONE definition of the (source, dest)
pair, shared by the stamp and its pre-flight so the two cannot narrow differently — therefore takes
the resolved canon root as a parameter. Both callers still pass `dest_root=workset_path`, NOT that
dest: narrowing the copy must not narrow the frame the whitelist judges it in, or every entry looks
top-level and deny-by-default goes blind.

⚑ `_CANON_CHAPTER_LEAF` is `handbook` alone, because the canon ROOT it hangs off is resolved per
workset. The chapter is guarantee-created (D7) under `workset.canon` on BOTH paths — spec `:962`
declares that key UNIFORM IN EVERY MODE, so a lone standalone root has the tier too — which is why
that one line sits outside the `canon_only` branch. Its sibling half does NOT transfer:
`workset.template` is `<None>` in standalone (spec `:936`), and the box-template skeleton rooted
there seeds FUTURE boxes, of which a standalone root will never have one.

⚑ The whitelist matters MOST here, though not because the tree is the user's own source. A
STANDALONE `<workset_path>` is a kanibako-MANAGED wrapper (`workset.yaml` + `box_data/` +
`vault/{ro,rw}/` + `workspace/`); the user's code lives one level down in `workspace/`, which no
stamp reaches. What IS true is that the wrapper is a directory the user ALREADY HAD
(`resolve_standalone_project` requires `root.is_dir()`), so deny-by-default guards a tree nothing
here is entitled to clean up afterwards.

### An escaping leaf — the refusal that names the key

`_assert_stamp_leaf_in_root`, called from inside `_workset_stamp_dirs`, refuses a
`workset.{canon,template}` that resolves OUTSIDE the workset root, naming **the key, the file it was
read from, the offending value, the reason and the remedy** — the bar
`settings.workset_dirkeys` already sets for an unresolvable repoint. Before it, the same case was
refused by `_assert_contained` with a message about some path being *"OUTSIDE the destination
subtree"*: correct, and unactionable, because it named neither `workset.canon` nor the
`workset.yaml` the value came from. A refusal the user cannot act on is the defect.

⚑ IT REFUSES AT THE RESOLVER, not deeper, because that is where the key is still in scope: by the
time the copier holds a destination the only fact left is a directory name. Both callers reach it
through `_workset_stamp_dirs`, so the STAMP and its PRE-FLIGHT refuse identically, and the refusal
lands BEFORE THE FIRST BYTE.

⚑ The check is PER-LEAF and `canon_only` gates the template one: standalone's `workset.template` is
`<None>` (spec `:936`) and that path consults neither the key nor the skeleton, so a value it never
uses must not refuse a root that is otherwise fine.

⚑ A `None` repoint still reaches it — when the DEFAULT leaf is itself a symlink out of the root —
so the message can say *"takes its default `canon` leaf"* rather than print `set to None`.

### The two guarantee-create `mkdir`s — CONTAINMENT-checked, deliberately NOT whitelist-checked

The skeleton loop and the canon-chapter `mkdir` run AFTER `copy_tree` and reach neither of its
guards. Once both leaves became RESOLVED keys that stopped being cosmetic: MEASURED, a
`workset.template` of `../elsewhere` planted all three skeleton dirs (seven directories) outside the
workset root with nothing refusing, and on the standalone path an out-of-root `workset.canon` was
refused only INCIDENTALLY — by the copy that happened to share its destination. Empty the mould's
`canon/` half and `copy_tree` returns on its first line, and the chapter `mkdir` then landed outside
the root unremarked. Each target is `_assert_contained`-checked now.

⚑ THE LEAF CHECK AND THE `mkdir` CHECK ARE NOT REDUNDANT. The leaf check judges the resolved ROOT;
`_assert_contained` judges the actual TARGET, which is what catches a SYMLINKED INTERMEDIATE under a
leaf that is itself perfectly in-root — the skeleton descends four levels below its leaf, and
`mkdir(parents=True)` would build the tree THROUGH the link.

⚑ THE WHITELIST, BY CONTRAST, HAS NOTHING TO SAY HERE, and adding it would only look like
diligence. These targets are composed from the SAME two roots `_workset_scope_allowed` respells its
entries FROM, so the check reduces to comparing each path with its own prefix; and when a root is
out of the store the respelling has no entry for it at all, which is the leaf check's job rather
than the table's. ⚑ It would not even be inert: a leaf repointed to the ROOT ITSELF respells to the
entry `'.'`, which is every relative path's prefix in fact and matches none of them as a STRING, so
the check would refuse a stamp that works today.

`check_workset_template` PRE-FLIGHTS that mould against the workset whitelist and writes nothing.
`workset create` must be ATOMIC in the way that matters to a user: either the workset exists and is
well-formed, or nothing happened. Refusing part-way through `install_workset_template` satisfied
"loud and leak-free" but left a REGISTERED workset with a root, its own `workset.yaml` and a
PARTIAL chapter copy — recoverable only by `workset rm`. So the check runs FIRST, before anything
is registered or created — the same order `workset.create_workset` already uses for its name guards
("BEFORE any on-disk side effect below"), which is why this is a pre-flight rather than an unwind:
it matches how this command already handles the class.

## QUARANTINE — the box handbook host-template copy is A NAMED SPECIAL CASE

⚑⚑⚑ **DO NOT COPY THIS SHAPE.** `handbook_layer_source_keys` and `install_box_handbook_template`
are the BOX HANDBOOK host-template copy, and it is a DELIBERATE, RULED EXCEPTION to the live model.

THE LIVE MODEL for a host template is the SINGLE-SOURCE copy `install_workset_template`: one mould,
one `copy_tree`, one whitelist. A new host template follows THAT.

THIS ONE stages THREE ordered layers. Jei, 2026-08-07g, ruling on the handbook specifically:
*"Yes, handbook copy keeps all three layers. It is a special case."* It is the ONE place the
single-source shape does not reach, and he named that himself. ⚑ It is NOT a pattern, NOT a
precedent, and must not be imitated for any other host template.

WHY IT IS HERE AT ALL — the HOST/GUEST criterion (his ruling, verbatim): *"we should NOT be copying
the handbook templates as if they are 'box' templates - they are system level templates that happen
to be generated on behalf of a box, but crucially, they do not DIRECTLY interact with the box
itself. That's the key. They are HOST templates, not GUEST templates."* The box-HOME templates land
in the very directory the guest sees at `~` ⇒ GUEST, and they stay in the `seeded` category. The
handbook templates fill a HOST location that a separate RO bind (`canon_hb_box`:
`@box.canon/handbook` → `~/canon/handbook/box`) later reads ⇒ HOST, so they leave the category and
are copied here instead.

⚑⚑ SINGLE-ROUTE IS INTACT, NOT BENT. Single-route governs what enters A BOX. A host template never
enters one; what enters is the RO BIND, which remains an ordinary settings key. Bespoke host-side
copy + keyed bind = single route.

⚑⚑ AND THE THREE LAYERS ARE NOT REDUNDANT WITH THE CHAPTER BINDS — measured, so that nobody
"simplifies" them to one source. The five `canon_hb_*` binds read each scope's OWN canon store
(`@system.canon/handbook`, `@agent.<active>.canon/handbook`, `@workset.canon/handbook`). These
three layers read each scope's TEMPLATE subdir (`<scope>.template/box/canon/handbook`) — *what that
scope wants in a NEW BOX's own chapter*. DIFFERENT TREES. Collapsing to one source would silently
DROP the agent's and the workset's contributions to the box chapter.

### `handbook_layer_source_keys`

The ORDERED dotted SOURCE keys whose values root the three handbook layers: `system.template` →
`agent.<a>.template` → `workset.template`, lowest layer first, gated exactly as the box-home layers
are gated (no agent bound → no agent layer; STANDALONE has no workset tier → no workset layer).

⚑ DERIVED FROM `template_seed_defaults`, not restated beside it, and that is the whole point of the
function: the per-agent / per-workset SOURCE scalars are declared THERE, so the gate that decides
whether a layer exists is read from the one table rather than re-implemented here where it could
drift. `system.template` is not in that table because it is already floor-materialized (a
`system.*` settings-tier path), so it is named directly.

⚑ THESE STAY KEYS. They are separate declared SOURCE keys — NOT `seeded` entries — and they carry
the user's repoint route (`config set workset.template` reroutes this copy, pinned by the repoint
tests). The handbook copy leaving the `seeded` category does not make its sources any less keyed:
the caller resolves these off the launch snapshot and passes the resolved roots in. Nothing there
hardcodes a path.

### `install_box_handbook_template`

Fills a NEW box's OWN handbook chapter from the three host template layers.

*dest* is the resolved `@box.canon/handbook` — the box store's CONTRIBUTION root, which lives
OUTSIDE the box home and is the SOURCE of the RO `canon_hb_box` bind. *layer_roots* are the
RESOLVED `<scope>.template` roots in apply order (system → agent → workset), each of which
contributes its `box/canon/handbook` subtree.

⚑ THE ROOTS ARE PARAMETERS, DELIBERATELY. They are settings keys (`handbook_layer_source_keys`) and
are resolved at the seam that already holds the launch snapshot; this function neither re-resolves
them nor keeps module state, so nothing here can disagree with what the snapshot said.

SEED-ONCE / CREATE-IF-ABSENT: the layers are merged per-file last-wins in a temp dir and then
copied in CREATE-IF-ABSENT (`stage_layers`), so a re-create into a leftover box store never
overwrites a chapter the user has edited. That failsafe answers a shipped data-loss bug; it is not
refactorable away. SKIP-IF-ABSENT: a layer whose `box/canon/handbook` dir does not exist is skipped
(an unpopulated `@workset.template` is the normal case).

GUARANTEE-CREATE, and it is a real (intended) behaviour change, so it is stated rather than left to
be discovered: the `mkdir` is UNCONDITIONAL, so `@box.canon/handbook` exists after every create
even when all three layers are empty or absent. The RO `canon_hb_box` bind is declared
`optional: true`, i.e. omitted when its source is missing — so it now ALWAYS mounts, and a user who
has emptied all three handbook template subtrees gets an EMPTY read-only mount where the bind used
to be dropped. `install_workset_template` guarantee-creates its own canon chapter the same way for
the same reason (the chapter is a place the user is expected to fill later, not an artefact of the
template).

⚑ NO DEST WHITELIST HERE, and that is deliberate — do not "restore" one. There is ONE dest policy
on this path: `_host_copy_dest`'s warn-and-skip at the caller. A `scope="box"` `SCOPE_WHITELISTS`
check would be a SECOND SPELLING OF THE SAME CONDITION (CONVENTIONS §0), because it could only ever
fire on the DEST: the dest is key-fixed at `@box.canon/handbook` and `stage_layers` builds its
relative paths by `rglob` UNDER the staged tree, so layer CONTENT cannot reach a non-whitelisted
top-level entry of the box store no matter what a template ships. Two checks on one condition,
disagreeing about severity (raise vs skip), is worse than one.

⚑ This is where it differs from `install_workset_template`, whose whitelist guards something real:
that mould lands at `<workset_path>`, a directory the user already had, where template CONTENT could
plant a `workset.yaml` or a `registry.yaml`. Its whitelist is not an oversight missing here; the two
copies simply have different attack surfaces.

NO PRE-FLIGHT TWIN, decided explicitly (contrast `check_workset_template`). `workset create` needs
one because `install_workset_template` can REFUSE part-way, leaving a REGISTERED workset only
`workset rm` can clean up. This copy has no refusal to pre-flight: the one dest policy is
warn-and-skip, so a misdeclared `box.canon` costs the box its handbook chapter and nothing else,
and the box is left well-formed. A second validator would be a second copy of rules with nothing to
prevent, free to drift.

## `install_packaged_templates`

Installs the packaged content into its host stores — an ENUMERATED set (P-S2). Four destination
pairs, each named because each has a different OWNER and therefore a different copy rule:

| packaged subtree | host destination |
|---|---|
| `template/box` | `@system.template/box` (STAGING) |
| `template/workset` | `@system.template/workset` (STAGING) |
| *(none — ships empty, D5)* | `@system.template/agent` (STAGING) |
| `template/handbook` | `@system.canon/handbook` (USER-OWNED) |
| `template/agent_default` + plugins | `@config.agents/<name>` (USER-OWNED) |

⚑⚑ `refresh=True` (the `kanibako setup` TRUE-REFRESH) reaches the STAGING rows ONLY. The user-owned
rows are create-if-absent on EVERY path — J-3 item 1: *"user-owned canon stores are NEVER
overwritten by any implicit path"*, and §2a's *"a package upgrade must not silently revert their
edits"*. Their differences are REPORTED instead (`plan_template_refresh`'s `kept` list), and an
explicit opt-in update verb is the future mechanism for actually applying them. That split is the
whole point of J-6's action taxonomy: a B-action (template update) changes what FUTURE
instantiations get and never touches an existing store; only a C-action (instance update), which
does not exist yet and will never be implicit, may rewrite one.

The agent-agnostic box guide (the bible's `ROM_GENERAL.md`) is NOT installed here — it is delivered
LIVE from the read-only packaged canon (bound at `~/canon/bible/general` + flattened into each
agent's native instruction slot at launch), so it has no host runtime-install target.

⚑ The two staging copies are SCOPED, and this is where J-2's box whitelist actually BITES. The
mould MIRRORS the store it stamps, so it is subject to that store's whitelist at the moment it is
staged — which is the earliest point a planted `box.yaml` (= `meta.box.settings`, the LAST
cascade level) can be REFUSED rather than carried forward. Unscoped, the deny-list would only be
dead prose: nothing downstream re-checks it, because the two downstream copies (the box-home seed
and the box-handbook host template) read `box/home` and `box/canon/handbook` directly.

⚑ The system handbook copy is UNSCOPED on purpose: the dest is INSIDE the canon root, not a scope
store root, so there is no store whitelist to apply (the equivalent guarantee is that the dest is
key-fixed). The agent MOULD dir is created even though nothing packages it (D5/D7).

## The shipped-file walk and the content digest

`_is_shipped_content` is True iff an entry is a real shipped file, not a build/editor artifact.
Build and editor junk (`__pycache__`/`*.pyc`, `.DS_Store`) never ships in a wheel, so hashing it
would make the content digest non-deterministic across environments/Python versions and report
drift that never shipped. The filter was written when the RO bundle still carried the flattener
`.py` beside the guide (a dev checkout, or the repo's own suite `exec_module`-ing it, dropped a
`__pycache__` right in the digest source). The canon ships NO `.py` at all now — the flattener moved
into the package proper — but the filter stays as the general junk guard for every packaged content
tree it walks.

`walk_shipped_files` returns the SORTED `(posix-relpath, file-path)` shipped-file list under a
root. It is the ONE traversal shared by the two consumers of a packaged content tree — the
staleness DIGEST (`_packaged_manifest_entries`) and the canon rom emitter
(`kanibako.settings.core_defaults.rom_default_categories`, which no longer binds per file but still
walks the rom root for its fail-closed guards). It walks *root* recursively, keeps only real
SHIPPED files, and returns each survivor as `(<root-relative posix path>, <absolute path>)` SORTED
by the relative path so the enumeration is deterministic across machines and Python
filesystem-walk order.

`_packaged_manifest_entries` returns the SORTED `(namespaced-path, file-bytes)` content manifest.
It enumerates every packaged file the setup gate must watch — the base seed tree
(`_packaged_base_template`), each installed agent's store payload (`_packaged_agent_store`), AND
the RO packaged canon (`_packaged_shared_bundle`, which is bind-mounted rather than installed but
still needs drift detection; it carries the box guide at
`canon/bible/general/directives/ROM_GENERAL.md`). Each file contributes ONE
`(namespaced-relative-path, file-bytes)` pair under a source-distinct prefix (`base/` / `shared/` /
`agent/<name>/`), so no file is double-counted; the pairs are SORTED so the manifest is
deterministic across runs and machines regardless of filesystem walk order.

The RO packaged canon (bound live at `~/canon/{COLLECTION.md,bible}`, never installed) is
enumerated ONLY so the setup gate still trips when the shipped canon content drifts — it has no
install target. It is the SOLE source of the box guide in this manifest (the retired
`@system.instructions` flat-copy no longer contributes a second entry). Both this digest and the
canon emitter now walk the SAME rom ROOT, so the namespaced keys read `shared/canon/...`.

`packaged_templates_digest` returns a content-manifest sha256 over the packaged template src trees.
It is a CONTENT hash over `_packaged_manifest_entries`, not a version marker: it moves ONLY when
packaged template content actually changes, and it is immune to the `setup_completed` silent
forward-bump that would mask template drift.

⚑ NO RUNTIME CONSUMER since R-38 (verified 2026-08-02: the host-side `template_staleness_gate` and
both stamp writers were deleted; the only callers left in-tree are this module's own tests). It is
kept for the RELEASE-TIME check — compare the digest against the previous tag and REQUIRE the
matching `SETUP_FCV`/`SETUP_BCV` bump — which is planned CI work (plan step C2) and is NOT wired
yet.

## The J-3 REPORTING classifier — three tiers, keyed by suffix

⚑ REPORTING ONLY. It never decides whether to copy: create-if-absent (or the staging refresh)
already decided that. Its whole job is to keep the setup report HONEST — spec §2a: *"report a skip
ONLY where the packaged file DIFFERS"*, because reporting every skip trains the user to ignore the
output, which costs exactly the signal the report exists to carry.

⚑ ACCEPTED CONSEQUENCE (J-3 item 5): a comment-only upstream change compares EQUIVALENT and goes
unreported today. The `[STOCK]` comment convention is what makes comment-aware handling possible
later, in the explicit opt-in update verb.

`_HTML_COMMENT_RE` matches HTML comments, non-greedy, across lines — stripped by the MD normaliser
because both equivalence tiers ignore comments (J-3 item 5). `_FENCE_RE` matches a fenced code
block (backtick or tilde fence), captured whole so the normaliser can leave its interior ALONE:
whitespace is SEMANTIC inside a fence.

### `_normalise_markdown`

Normalises text for the MD equivalence tier — CONSERVATIVELY. Strips HTML comments, then collapses
insignificant whitespace: CRLF → LF, trailing whitespace per line, runs of blank lines, and the
final newline.

⚑ TWO THINGS ARE DELIBERATELY LEFT ALONE, because in markdown they are not whitespace but SYNTAX:

* the interior of a FENCED CODE BLOCK — indentation and blank lines there are content;
* a TRAILING TWO-SPACE hard line break — stripping it would merge two lines.

Anything this misses only costs a spurious "different" report, never a wrong copy; anything it
over-normalises would HIDE a real change, which is why the bias is conservative.

### `_equivalent`

True when *target* is byte-equal to, or EQUIVALENT to, *src_file*. ONE strategy table keyed by
suffix (J-3 item 2):

* `.yaml` / `.yml` — `safe_load` both sides and deep-compare. A parse failure on EITHER side ⇒
  "different" (never equivalent: an unparseable file is exactly the case a report should surface).
* `.md` — `_normalise_markdown` both sides and compare.
* anything else — bytes only.

### `plan_template_refresh`

Classifies every packaged src file by its host target for the setup preview, returning
`(added, overwritten, kept)` lists of HOST target paths:

* ADDED — the packaged file has no host counterpart yet (create).
* OVERWRITTEN — a STAGING file (`@system.template/**`, system-owned) exists and DIFFERS from the
  packaged version, so a true-refresh replaces it.
* KEPT — a USER-OWNED file (`@system.canon/handbook/**`, `@config.agents/**`) exists and DIFFERS.
  It is NEVER overwritten (J-3 item 1); it is reported so the user knows an upstream version moved
  on and their copy stayed.
* current — byte-equal OR `_equivalent`; in NO list, deliberately unreported (an identical file is
  not a "skip", and neither is one that differs only in comments or insignificant whitespace).

User-only files never appear (they are not in the packaged src loop). This is a pure classification
(no writes) driving the `kanibako setup` preview.
