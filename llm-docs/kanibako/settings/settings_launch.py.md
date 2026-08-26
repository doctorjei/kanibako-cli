# Launch-time settings snapshot — the ONE resolve per launch (block 7b)

`settings_launch` is the LIVE read-path of the settings keyspace. `commands/start.py` builds ONE
resolved `KeyStore` snapshot per launch here, through the committed KeyStore pipeline
(`assemble_levels` → `merge` → `expand`), and BOTH the behavior reads AND the CATEGORY delivery read
from that SINGLE snapshot (S12 WRITE-ONCE — resolve ONCE, read many).

It replaced the two inline `LevelView` cascades `start.py` used to hand-build per launch (the
behavior cascade and the per-mount-family category cascade) and the `machine`
(`/etc/kanibako.yaml`) reads (S14). It is the block-7b consumer SWAP: it IMPORTS the pipeline —
single-source, it never re-implements assemble/merge/expand — and ADAPTS the snapshot's category
subtrees into the one `list[CategoryEntry]` the delivery seams consume (§6g). ⚑ The single by-dest
`reconcile` pass those seams replaced was retired at cutover 6-R3; this module never held it and
does not need editing when its successors move.

The module has two halves. The FIRST half builds FLOORS — `{dotted_key: value}` fragments the caller
folds into `build_launch_snapshot`'s one floor, so `expand` resolves every `@`-ref chain ONCE
(single-route, NO second resolver). The SECOND half READS the expanded snapshot: the behavior read
(`effective_behavior`), the launch-grammar read (`meta_agent_grammar`), the auth-source read
(`resolve_auth_source`), and the category adapter (`snapshot_category_entries`).

**Authority:** `specs/settings-keyspace-1.8.0.md` — §0 (the CLOSED keyspace), §1, §2 (the cascade),
§2a (the categories), §2c (worksets + box bindings per mode). ⚑ **The spec is the LIVE authority;
read it first.** SEAMS S7/S8/S9/S12/S14/S17/S20/S26/S27 + OS1.

Historical: `keystore-design.md` §1 (purpose), §2 (storage model), §4 (resolution), §6g (cascade
MERGE and category RECONCILE kept distinct). ⚑ It is ARCHIVED, at
`~/canon/notebook/archives/keystore-2026-06/keystore-design.md`. 🛑 **Its §4 writes the cascade
bracket with a 7th `required` tier that S14 and spec §2 CUT** — so on the cascade the archive is
WRONG and the spec wins. Cite §4 for the resolution mechanics only, never for tier structure.

## What lands in the one snapshot

The launch has SEVERAL runtime-computed `default_categories` tables (channel / core / kani / helper /
image binds, agent common/seeded, masks) and a behavior floor — each of which used to ride a
per-family `LevelView`'s `defaults=` (the cascade FLOOR). They ALL fold into ONE `floor` dict passed
to `assemble_levels(floor=…)`. Step 2a folds it UNDER the base file, so a file at any scope still
overrides by name — precedence-equivalent to the old AGENT-level `defaults=`, verified against
`resolve_value`'s two-pass order. Plus:

* **OS1** — the bare behavior floor (`{d.key: d.default}`) is mapped to the SCOPE-QUALIFIED
  `agent.default.<key>` before folding: the declared behavior defaults are the ALL-AGENTS backstop
  (spec §2d lists them under `agent.default.*` — access / allow_helpers / model / …). There is NO
  bare `agent.<key>` (spec §0); the §2d active-over-default READ layers a per-agent
  `agent.<active>.<key>` over this default.
* **7a** — `agent_representation.agent_default_partial` is an ADDITIONAL agent-level partial (S27):
  the descriptor delivery binds become `agent.<active>.bindings.{ro,rw}.<key>` in the cascade (the
  active agent's DISCRIMINATED slot, `install.name`; §2d / §0 — NO bare `agent` token), so agent
  binary/launcher/share delivery flows through the ONE category keyspace (single-route), NOT a
  parallel `descriptor_mounts` route. A user repoint of a descriptor delivery bind's host source is
  an ordinary settable `agent.<agent>.bindings.{ro,rw}.<key>` set on a scope FILE — the SAME active
  slot 7a delivers into. It merges over 7a BY NAME through the normal cascade; there is no parallel
  override route.

Category default tables are already scope-qualified dotted keys. A live `""`-suppression of a
DEFAULT means "this default is disabled" → the fold just DROPS it (absent ≡ no default), matching
the retired by-name resolver's terminal skip. A box/workset FILE `""`-suppression of an inherited
default is a SEPARATE path, not this one; no shipped default table uses `""`.

The agent-scope default tables arrive ALREADY DISCRIMINATED (`default_common()` →
`agent.<agent>.common`, `default_seeds()` → `agent.<agent>.seeded`, each holding a whole dest-keyed
map since 2026-08-08c — the older `agent.<agent>.common.plugins` spelling was the retired per-name
key). The declaring plugin builds the discriminated key in `settings/agent_defaults.py`, because the
snapshot agent tier is DISCRIMINATED (§2d / §0) and the bare form must not exist anywhere. A
box/workset/agent file STILL overrides them by name through the merge, and the adapter's
active-over-default pick reads them. (`agent.default.*` from the behavior floor is the agent tier's
FALLBACK — a legitimate discriminator, left as-is.)

Two per-entry rules live in the floor fold:

* **The `masks` BRIDGE.** A live `<scope>.masks` value is a LIST[box_dest] (the shipped/file form)
  while the KeyStore model is a keyed `dict[box_dest → bool]` (S5/§6f). The floor converts, so the
  snapshot's masks node is the keyed shape the adapter reads (present = mask). This is a CONVERSION,
  not a filter, and it is a different thing from the `""`-suppression beside it.
* **PER-ENTRY `""`-suppression.** Every bind-shaped category is DEST-KEYED and TERMINAL (R-5;
  2026-08-08c), so the whole map is ONE floor key — which would coarsen the smallest suppressible
  unit from an entry to a whole category. The fold therefore applies the suppression per entry as
  well. No shipped default uses `""`; this keeps the latent path exactly as wide as it was, rather
  than making a behaviour change nobody ruled.

⚑ The floor's list→keyed-dict bridge for `<scope>.masks` is NOT the same permission as a settings
FILE's, and it stays: a floor table is written by kanibako or by an agent plugin, never by a user,
and the bridge runs BEFORE assembly, so what reaches `_assert_declared_categories` is already the
keyed shape. A settings FILE has no such adapter and is refused.

## The DISCRIMINATED agent read (§2d)

The snapshot keeps the agent tier discriminated — `agent.default.*` (the all-agents backstop) and
`agent.<active>.*` (the active slot, where 7a or a per-agent file land). Both the behavior read
(`effective_behavior`) and the category adapter (`snapshot_category_entries`) do the
active-over-default value-pick PER NAME HERE — the consumer's job, since 2a/7a and the merge
deliberately keep both slots' keys discriminated. Emitted `CategoryEntry`s carry the BARE `agent`
scope token, which is load-bearing for scope precedence downstream and is NOT the discriminator.

`_agent_pick_node` is the PURE pick: `agent.default` overlaid by `agent.<active_agent>`, returned as
a FRESH `KeyStore` shaped like a single bare agent scope node — its `bindings.{ro,rw}` / `caches` /
`seeded` / `common` / `synced` / `masks` / `env` subtrees plus behavior leaves, each holding the
per-name winner. The overlay is PER NAME (deep), so an active `agent.<active>.common.cache` and a
default-only `agent.default.common.plugins` BOTH survive. A present-`None` reset was already OMITted
by the merge (§3 / §6e), so it never reaches here. It must NOT itself read `meta.box.agent.*` — that
node is MATERIALIZED FROM this pick, so reading it would be a chicken-and-egg.

A box's `pref.agent.<agent>.<category>` requests (§2h) merged INTO `agent.<active>` as an ordinary
cascade level, so the PURE pick already carries them: the box's tweak is live in category resolution
with NO post-expand overlay (single-route).

### Two scope questions, not one

`_fixed_decl_scope_fn` and `_agent_decl_scope_fn` answer the OTHER scope question — which
DISCRIMINATED scope an entry was DECLARED under, for `CategoryEntry.key`. A `system` / `workset` /
`box` key is spelled with its bare scope token, so declaration scope and precedence scope are the
same string; the agent tier is the only one where they can differ.

An entry's declared KEY must be DISCRIMINATED: a bare `agent.<category>.<name>` is not a key at all
(spec §0), so a message or a `binding_derivations.*` entry spelled that way would name a shape the
keyspace forbids and point a reader at something they cannot write. `_agent_decl_scope_fn` recovers
the tier the same way the pick decides it, and from the same RAW tiers `snapshot_category_entries`
already walks: a leaf declared by the ACTIVE slot came from `agent.<active>`; otherwise it came from
`agent.default`, the only other tier that can have contributed it. It reads the raw tiers directly
and does NOT thread per-leaf provenance through `_overlay_into` — the pick's own rule answers it.

Collapsing the two facts would either lose the precedence token or emit a bare `agent.<category>`
key, which is not a key.

## box_dest deferral (S17 / B6)

The snapshot keeps box-side `$XDG` / `~` in a `Bind.box` RAW (deferred — host ≠ box). The category
ADAPTER (`snapshot_category_entries`) is a `box_dest` consumer: it resolves box-side `~` →
`GUEST_HOME` and `$XDG` against the BOX ctx (matching the retired by-name resolver's
`space="guest"`) BEFORE building each `CategoryEntry`, so every downstream seam keys on the SAME
absolute `box_dest` it did pre-swap (depth-sort + dest-collision unchanged). The S20 escape contract
— a backslash-escaped `$` / `~` / `\` carried literal — is honored by the shared `expand_expr`
scanner.

## The auth 3-tier SHARING chain (spec §2a/§2b/§2c/§2d — 2026-07-01 redesign)

`auth_chain_floor` REPLACES the boolean `group_auth` chain with a global/workset/box SHARING model
that COMPOSES: a box can be global-shared AND/OR workset-shared. The keys are injected into the
launch snapshot floor so `expand` resolves the `@`-ref chain ONCE (single-route — NO second
resolver). A settable scope FILE may still override a settable key by name (the floor sits under
`base`); the `meta.*` capability keys are RO / construct-set, so a scope file cannot fake them.

### The FINAL KEY MODEL (design FINAL — the authority)

```
meta.agent.<agent>.auth.share_support   <plugin-set>   shared creds supported?
system.auth.share_allowed             = true           global share allowed?
workset.auth.share_allowed            = @system.auth.share_allowed
workset.auth.global_sync              = @system.auth.share_allowed
meta.box.agent.auth.share_support     = @meta.agent.<@system.agent>.auth.share_support
                                        (mirror, materialized from the SELECTED node)
box.auth.global_enabled  = %@meta.box.agent.auth.share_support && @system.auth.share_allowed%
box.auth.workset_enabled = %@meta.box.agent.auth.share_support && @workset.auth.share_allowed%
workset.auth.path        = @meta.workset.path/auth   (workset auth dir, per workset; SETTABLE)
meta.box.auth.workset_path = @workset.auth.path/@system.agent
                                        (this box's per-agent source root — RO DERIVED meta
                                         anchor, change 8: NOT settable, so a user can't repoint
                                         it to garbage; the ONLY settable auth-location surface
                                         is workset.auth.path)
```

STORES: GLOBAL = host home (`host_rel`, NOT managed) · WORKSET = `@workset.auth.path/<agent>/`
(layout MIRRORS the in-guest mount = `home_rel`) · BOX = private (no source). Precedence:
workset > global.

The `meta.agent.<agent>.auth.share_support` CAPABILITY is set by the PLUGIN (`*-defaults.yaml`), NOT
by this floor — it rides the meta identity floor. What `auth_chain_floor` materializes is the
`meta.box.agent.auth.share_support` MIRROR (the 29g box.agent mirror pattern made concrete for the
`@`-ref's literal-path resolution), the system/workset allow knobs, the two settable box ENABLE
knobs (per-tier opt-out defaults), the settable `workset.auth.path` store anchor, and the RO DERIVED
per-box source root `meta.box.auth.workset_path`.

### ‼ ENABLE COMPUTATION — why the `&&` is Python, not the engine

The spec writes the two box enables as `%@support && @allow%` expressions, but the launch `expand`
engine resolves ONLY `@`-refs / `$VAR` / `~` — it does NOT evaluate `&&` boolean expressions (the
spec's `%…%` conditionals are computed in Python today, cf. the images bind in `core_defaults`). So
the floor materializes the settable box ENABLE as a plain per-tier bool DEFAULT (`True`, the box's
own volitional knob, which a box may override to `false` to opt out of a tier) plus the resolvable
INPUTS (the capability mirror + the system/workset allow flags). `resolve_auth_source` then ANDs
support && allow && box_enable in PYTHON — exactly as the old `effective_group_auth` did
`available AND on`. The box's `*_enabled` key thus IS its opt-out knob; the composed gate is the
Python AND. Folding the `&&` into the expand engine is a deferred generalization, flagged for the
Editor.

### The node-selector notation is NOT expressible

Spec §2c writes the capability mirror as `@meta.agent.<@system.agent>.auth.share_support`. That is
NODE-SELECTOR NOTATION, and the resolver cannot express it: a reference name is a literal, there is
no second pass, and PHASE R's `@{name}` DELIMITS a name so a literal SUFFIX may follow it — it does
not nest a reference INSIDE a name. So the shipped mechanism is the documented equivalent: the
selected node is interpolated in Python and the resulting `@`-ref is then followed by `expand` on a
literal path. What P7 changed is only the PROVENANCE of `agent_name` — it is now the value the §1A
selection level installs at `system.agent`, i.e. the same string the spelled anchor would resolve.
(Spec follow-up, queued for Jei: either §2c gains a note that this anchor is MATERIALIZED, or a
future grammar phase adds node selection.)

⚑ **A BLANK agent is pinned to the LITERAL `False`, not spelled as a ref.**
`f"@meta.agent.{''}.auth.share_support"` is `@meta.agent..auth.share_support` — a MALFORMED ref with
an empty segment, which resolves to a leftover string and then crashes `resolve_auth_source`'s
strict `as_bool` ("expected bool, got str"). No caller passes blank today (the launch uses the
`"general"` slot for a shell box; stop / creds-watch return early on an absent `KANIBAKO_AGENT`
stamp), but P7 made `""` a MEANINGFUL value — the D-M6 suppression — so the trap is one careless
caller away. `False` is also the semantically right answer: no agent ⇒ no sharing capability, which
is exactly the hard RO floor §2c describes.

### `meta.box.auth.workset_path` — spelled exactly as the spec

`@workset.auth.path/@system.agent`. RECLASSIFIED (change 8) to the RO DERIVED anchor, DIRECTLY under
`meta.box.auth.*` — NOT `meta.box.agent.auth.*`, because that agent sub-namespace is the capability
MIRROR whose every key `@`-refs a `meta.agent.<agent>.*` source, while this is a per-box LOCATION,
not an agent capability. Being `meta.*` it is dropped from every settings FILE in assembly, so a
user CANNOT repoint it to a dangling `@`-ref or garbage.

⚑ P7 spelled it EXACTLY AS THE SPEC (§2c) instead of interpolating the active agent in Python. It is
a CONSTANT now: the per-box variation arrives through `pref.system.agent` (§2h) and the §1A
selection level, both applied BEFORE this level resolves. NO braces are needed — the first ref is
terminated by `/` (not segment-legal) and the second ends the value, so `_REF_NAME_RE`'s greed
cannot swallow anything (PHASE R's `@{name}` exists for a LITERAL SUFFIX after a ref; there is none
here). Both refs are EMBEDDED, so an absent `system.agent` coerces to `""` (§6b) rather than
dropping the key: a NO-AGENT box resolves this to `<auth.path>/`. That is INERT —
`resolve_auth_source` scrubs `workset_source` for every non-workset tier, and the workset tier needs
`share_support`, which no-agent cannot have.

### STANDALONE (the degenerate lone box, spec §2c)

No workset group → `workset_enabled` degenerates false: the two workset allow keys pin to the
LITERAL `False`, so the Python AND for the workset tier is false regardless of the box knob. But
`global_enabled` = support && `system.auth.share_allowed` && box_knob STILL applies — a standalone
box CAN use global/host creds (a deliberate change, IMPL-arc noted).

There is no workset store for a lone box, so `workset.auth.path` is the absent (present-`None`)
anchor and `meta.box.auth.workset_path` is pinned `None` too, as a defensive root-cause fix.
Otherwise `@workset.auth.path/<agent>` would resolve against the absent `workset.auth.path` and
expand to the literal `/<agent>` — an `@`-ref to an absent key renders `""`, not a drop — garbage
the credsync dir-creation would `mkdir` against the host ROOT. The workset enable is false anyway,
so this source is never consulted; pinning `None` makes that explicit at the floor, belt-and-braces
with the resolver's scrub. This is the established meta-anchor-is-`None`-for-standalone pattern.

PRIMARY / NAMED (ALL WORKSETS) instead use the `@`-ref forms: the workset allow defaults to the
system gate, the workset dir syncs UP to global by default, and `workset.auth.path` is
`@meta.workset.path/auth` — a sibling to boxes/vault/logs off the workset root, mirroring the
in-guest mount layout underneath.

### `AuthSource` and `resolve_auth_source`

`AuthSource` replaces the single `effective_group_auth` bool. It carries the per-box tier the
credsync engine syncs against, the two box enables (for diagnostics / display), and the
workset↔global up-sync flag. The two enables COMPOSE, but the SELECTED source obeys precedence
workset > global: when the workset tier is enabled AND its store is present, WORKSET wins; else
global (if enabled); else private.

* *tier* — the SELECTED source tier: `"workset"` / `"global"` / `"box"` (`"box"` = private, no
  source — today's distinct-auth). The credsync gate keys off this: `box` drops synced /
  credential-seeded deliveries.
* *global_enabled* / *workset_enabled* — the resolved box enables. Both may be true; the enables are
  what COMPOSE, the *tier* is the precedence winner.
* *global_sync* — the workset auth dir syncs UP to global (design SYNC): when true and the box syncs
  the WORKSET tier, the workset store is first refreshed from / written back to global (the uniform
  primitive at the second level).
* *workset_source* — the resolved workset per-agent source root (`meta.box.auth.workset_path`), or
  `None` for standalone / when absent. The GLOBAL source is the host home (`host_rel`) and is not
  carried here (implicit).
* `creds_shared` — the single-bool analog of the old `effective_group_auth`, for the gates that only
  care "is this box sharing at all" (auto-auth, the host-source credsync hops, the credential-gate
  drop). `False` ≡ the old distinct-auth.

`resolve_auth_source` reads the chain off the ONE expanded snapshot. Each input is resolved to a
real `bool` terminal by `expand`; `as_bool` does not launder. `meta.box.auth.workset_path` is a
resolved string (or `None` for standalone). An absent `box` node means the floor was not injected →
fail CLOSED (tier `"box"`, no sharing) rather than launder.

It reads the capability MIRROR and the DERIVED source root from under `meta.box`, and only the two
settable ENABLE knobs from `box.auth` — the workset SOURCE path moved to the RO meta node at change
8. Absent / `None` / `""` all coerce the source to `None`.

⚑ The final scrub: the workset source is nulled out UNLESS the workset tier was selected. Otherwise
a standalone / global / private box would carry the resolved `meta.box.auth.workset_path`, which for
standalone is the GARBAGE `/<agent>` described above, and leaving it live makes the credsync
dir-creation `mkdir` against the host ROOT. Only the workset tier ever consults `workset_source`.

## `meta.runtime.*` materialization (block B1 — spec §1A, 2026-06-29h)

The spec's RUNTIME-RESOLVED identity anchors (§1A; §0 `meta.*` is a TOP-LEVEL protected RO group).
The per-mode treewalk values are ALREADY computed at launch (`proj.mode` / `proj.group.root` / the
resolved project dir); `meta_runtime_floor` surfaces them as REAL `@`-referenceable keys via the
SAME floor-injection pattern the auth chain uses. They are `meta.*` keys (NOT `config.*`), so they
ride the FLOOR alongside `system.*` and the auth chain.

```
meta.runtime.ws_root      | primary    = "@config.primary_workset"  (@-ref → #3a foundation)
                          | named      = str(proj.group.root)       (resolved literal)
                          | standalone = str(proj.metadata_path)    (the project ROOT <root>;
                                         B2b fixed this from the B1 <root>/workspace defect)
meta.runtime.project_type | proj.mode.value  ("primary"|"named"|"standalone")
meta.runtime.ws_name      | primary → __PRIMARY__ · named → <detected name>
                          | standalone → __STANDALONE__
```

There is NO `meta.runtime.ws_settings`: spec §1A CUT it — *"no longer needed (unified path)"*. It
was a one-consumer alias for the value string below, holding exactly that value with exactly ONE
consumer, so substituting its definition removes a hop without changing a single resolved value.
Under §0's CLOSED KEYSPACE an undeclared key is not a key, so it does not linger as an alias;
`@meta.workset.settings` is the only spelling.

Then the SINGLE-SOURCE re-root (spec §1A; §2c), UNIFORM across all modes — each is the SAME `@`-ref
into `meta.runtime.*`:

```
meta.workset.path     = "@meta.runtime.ws_root"
meta.workset.settings = "@meta.workset.path/settings.yaml"     (spec §2c)
meta.workset.name     = "@meta.runtime.ws_name"                (spec §2c, 2026-07-04)
meta.box.mode         = "@meta.runtime.project_type"           (RO identity anchor; spec §2b)
```

These resolve transitively in the ONE expand pass. Primary: `meta.workset.path` →
`@meta.runtime.ws_root` → `@config.primary_workset` → foundation. Standalone:
`meta.workset.settings` → `@meta.workset.path/settings.yaml` → `@meta.runtime.ws_root/settings.yaml`
→ `<root>/settings.yaml`, the workset tier.

`meta.workset.settings` is spelled off `@meta.workset.path` — the spec's own spelling — chaining
through the anchor set two lines above it, the same chained-floor-ref pattern the box-root anchors
use. Spelling it off `@meta.runtime.ws_root` instead would resolve to the byte-identical value but
DIVERGE from the spec, and the spec is authority.

`meta.workset.name` anchors into `meta.runtime.ws_name` — the SINGLE SOURCE for the partition token.
Block B2 no longer sets it directly. The token is itself SINGLE-SOURCED on
`channels.channels.workset_name_token` and threaded in by the caller, so the keyspace anchor and the
channel partition cannot drift.

`meta.box.mode` is an RO identity anchor: it replaced the formerly settable `box.mode` config-set
key, which `config_interface` no longer offers.

*ws_root_literal* is the resolved workset-root path STRING for the NAMED and STANDALONE modes
(`str(proj.group.root)` / `str(project dir)` — a runtime treewalk result, no key form; JC-B1-2: an
in-memory floor literal, NOT a file value, so §0's unresolved-FILES rule does not apply). It MUST be
given for `named` / `standalone` and is IGNORED for `primary`, which uses the
`@config.primary_workset` `@`-ref so the value live-propagates from the Layer-1 foundation.

A scope FILE cannot set any of the re-rooted keys: they are construct-set RO per §0, and `meta.*` is
not in the config-set settable known-key list, so the floor is their sole source.

## `meta.*` IDENTITY-ANCHOR materialization (block B2 — spec §2c/§2d, §0)

B1 materialized `meta.runtime.*` plus the single-source re-root. B2 (`meta_identity_floor`)
materializes the REMAINING construct-time IDENTITY anchors as RO floor keys and ROUTES the eligible
core binds through `@meta.*` refs, so a bind's `host_src` RESOLVES via the snapshot instead of being
injected as a proj-attr literal at the assembly seam — the single-route payoff (spec §0).

⚑ **EQUIVALENCE IS THE BAR (JC-B2-4).** Each materialized identity key is the RESOLVED LITERAL the
launch already computes (`str(proj.project_path)`, the channel partition addresses, the plugin agent
name, …) — NOT a re-derivation via the spec's nested `@workset.*` chain. Holding the resolved
literal guarantees the `@meta.*`-routed bind expands to the byte-identical `host_src` the proj-attr
injection produced. This mirrors B1, where `meta.runtime.ws_root` for named / standalone is the
`str(proj.group.root)` / project-dir LITERAL. They are `meta.*` keys (construct-set RO, §0), so NO
scope FILE may override them.

```
meta.box.name           | the box name (proj.name; primary/named = box name,
                          standalone = <kuid>_%leaf% — already composed LIVE and carried on
                          proj.name, JC-B2-2: reuse, do not regen)
meta.box.workspace      | the resolved in-box workspace SOURCE (str(proj.project_path)) —
                          routed to box.bindings.rw.workspace
meta.box.inbox          | this box's own mailbox dir (str(addr.inbox)) —
                          routed to box.bindings.rw.inbox
meta.box.share_global   | this box's system-scope share dir (str(addr.share_global))
meta.box.share_workset  | this box's workset-local share dir (str | None standalone)
meta.agent.<a>.name     | the plugin-set agent name (REQUIRED when an agent exists)
meta.agent.<a>.settings | the agent-tier settings FILE anchor
                          (@meta.agent.<a>.path/settings.yaml — B5, spec §2d)
```

`meta.agent.<a>.{mode,exec}` — the plugin-set LAUNCH GRAMMAR — are B5 keys built by
`meta_agent_grammar_floor` from the harness descriptor and folded into this same identity floor by
the caller. `meta.workset.name` is a `meta_runtime_floor` anchor, NOT a B2 key.

The box name is carried on `proj.name` — standalone's `<kuid>_%leaf%` is composed LIVE in
`resolve_standalone_project` from the stored `workset.kuid` plus the current-dir leaf (P6d); B2 does
NOT re-compose or regenerate it. `share_global` / `share_workset` are materialized identity anchors
for parity and future routing.

*share_workset* is `None` for STANDALONE (no workset-local channels, spec §2c) → materialized as a
whole-value `None` terminal (the key is PRESENT with value `None`). It is now the ONLY standalone
`None` terminal here: a lone box genuinely has no workset-LOCAL channel dir, whereas it DOES have a
box settings tier.

### `meta.box.settings` — uniform in every mode

`meta.box.settings` is the RO box-TIER settings-file anchor, and it is UNIFORM in EVERY mode (spec
§2c ALL PROJECTS: `@meta.box.path/settings.yaml`). Standalone is NOT a `<None>` terminal: its box
tier is `<root>/box_data/settings.yaml` — a real path, merely ABSENT BY DEFAULT (§5), an absent file
being an empty tier, with box-scope values then resolving from the workset tier
`@meta.workset.settings` as R2 downward-defaults. The WRITE target moved with the READ in the same
change (M-8), so a `config set box.*` lands in exactly the file this anchor names.

It still cannot simply BE the `@meta.box.path/settings.yaml` `@`-ref: a bootstrap anchor may not
derive from a key at the scope it bootstraps, and this one resolves BEFORE the cascade exists —
unlike the launch-time home bind, which is why THAT one may root at `@meta.box.path`. It is
materialized as the RESOLVED LITERAL the launch computes, the SAME value the cascade uses as its
box-tier file path (single-sourced through `paths.box_workset_settings_paths` in `start.py`'s
`_launch_snapshot_inputs`), so the anchor and the cascade cannot drift. The parameter stays optional
for narrow/partial resolves that materialize no box tier; the launch always passes a real path.

`meta.box.{workspace(named),container_name,helper_num}` per the spec are non-bind RENDER targets
(`container_name` from name + `helper_num`); B2 materializes the IDENTITY leaves the eligible BINDS
reference plus the agent name. The `container_name` / `helper_num` RENDER and the home/vault binds
stay on attrs / `@workset.*` (JC-B2-3 / JC-B2-4), a tracked follow-up.

### The agent identity keys

When an agent exists, `meta.agent.<a>.name` is the plugin-set agent name (spec §2d, REQUIRED), under
the agent's discriminated slot; a NO-AGENT box omits it. `agent_name` is the cascade discriminator
(`install.name`); `agent_real_name` is the value (the plugin's `meta.agent.<agent>.name` — normally
the same string). Both `None` for a NO-AGENT box.

⚑ The STORE-ROOT anchor `meta.agent.<a>.path` is keyed on the DISCRIMINATOR (*agent_name*), not on
*agent_real_name* — the store dir is `agents/<discriminator>/`, which is what `agent_settings_path`
and the persona shim use. The two are the same string for every shipped plugin; if a plugin ever
returned a different `name`, the path anchor would still (rightly) follow the store, while
`meta.agent.<a>.name` reported the plugin's value.

`meta.agent.<a>.settings` is the agent-tier SETTINGS cascade FILE anchor (spec §2d; B5 — the §3.3
"keep and use" ruling): the spec's own formula `@meta.agent.<a>.path/settings.yaml`, resolved
transitively through the sibling `path` anchor by `expand` — the SAME file `agent_settings_path`
composes (`agents/<a>/settings.yaml`, D-2026-06-22). Keyed on the DISCRIMINATOR like `name`: the
store, and so the settings file, follows the active node.

`meta.agent.<a>.auth.share_support` is the agent's credential-SHARING CAPABILITY (spec §2d; design
step 2): plugin-set, RO — the hard floor a user can't fake. The auth chain's
`meta.box.agent.auth.share_support` mirror views UP to this key, so it must be present in the
snapshot whenever an agent exists. A NO-AGENT box omits it: no agent capability to mirror → the
mirror `@`-ref resolves to `<None>` and the box enables degenerate false.

### `meta_agent_path_floor` — one builder, two seams

`meta_agent_path_floor` is THE single builder for `meta.agent.<a>.path`, used by BOTH the launch
floor (`meta_identity_floor`) and the `config set` SET-TIME validation snapshot. That sharing is
load-bearing, not tidiness: `config set`'s refusal message tells a user to spell an abstract-category
source as `@meta.agent.<agent>.path/<category>/<name>`, and if the set-time snapshot did not carry
the key, the very value the tool just recommended would be rejected as a dangling `@`-reference. A
hint that cannot be accepted is worse than none.

It is spelled `@config.agents/<name>` rather than the spec's `@config.agents/@meta.agent.<a>.name`
chain (§2d): `<name>` IS the value of `meta.agent.<a>.name` at both seams, and the flat form avoids
a floor entry that references its own sibling. Both resolve identically (verified).

⚑ **NODE and HARNESS.** `load_common` keys its entries on the plugin's own `Target.name` (the
HARNESS, e.g. `claude`) while callers pass the ACTIVE NODE (`navigator℘claude` for a persona). On a
persona box those differ, so materializing only the node would leave the harness-keyed refs
DANGLING. Both are materialized; for a bare agent, node == harness and this is a single entry.

⚑ The harness entry is INTENTIONALLY PARTIAL on a persona box: it gets a `path` but no `name` /
`auth.share_support`, which stay the ACTIVE agent's — what every consumer of them means. Nothing
reads `meta.agent.<harness>.name`; the partial node exists solely so a harness-keyed store ref
resolves, so the asymmetry is inert, and making it symmetric would invent a second identity for one
agent.

### `meta_agent_grammar_floor` — the descriptor→keyspace seam

THE single seam for the invocation grammar (spec §2d; §3.3 rulings *"it should exist and be used"* /
*"we should be using this"*; the R-37 pattern — the manifest declares the SHAPE, the descriptor
supplies the MEMBERS).

* `meta.agent.<a>.mode` — the harness's INTERACTIVE launch grammar, `dict[mode_key → argv fragment]`
  (the descriptor's `mode` field, tuples normalized to lists).
* `meta.agent.<a>.exec` — the STANDALONE one-shot fragment (`operations["exec"].fragment`); omitted
  when the descriptor declares no `exec` operation.

⚑ **REPLACEMENT, not a second path.** After B5 the launch composes its argv from these snapshot keys
(`meta_agent_grammar` → `targets.assembly.assemble_argv`), and NOTHING reads `descriptor.mode` /
`descriptor.operations` at argv-assembly time — the descriptor feeds the keyspace HERE and nowhere
else. Two sources for one argv fragment is the drift shape this arc exists to kill.

Keyed on the DISCRIMINATOR (the ACTIVE node) like `name` / `auth.share_support`: on a persona box
the harness slot stays grammar-less (nothing reads it — see the path floor's asymmetry note above),
while the grammar itself is the HARNESS's, since the caller resolves the harness descriptor. A
descriptor-less agent (`None`) materializes nothing — the launch has no grammar to compose and takes
the no-agent path.

`meta_agent_grammar` is the matching LIVE reader: the composition seam (`start.py` →
`targets.assembly.resolve_mode` / `assemble_argv`) takes its argv fragments from HERE — the keyspace
is the single source, and the descriptor only ever feeds it. There is deliberately NO fallback to
the descriptor: a descriptor-bearing launch whose snapshot lacks the grammar is a BUILD BUG (the
materialization and the launch resolve share `_launch_snapshot_inputs`), and falling back would
silently reintroduce the second source. It raises `SettingsError` naming the key instead, and type-
checks each fragment as a list of strings.

## LAYOUT anchors — workset roots + the RO BOX ROOT (spec §2a/§2c)

`workset_anchor_floor` is the other half of the single-route payoff: it materializes the
workset-scope PATH anchors the spec's §2c binds reference (`workset.{boxes,vault_ro,vault_rw,logs}`
plus the workset-local channels) and the RO per-mode BOX ROOT `meta.box.path`, as REAL
`@`-referenceable floor keys. The core home / vault / helper_log binds route through these anchors
so a bind's `host_src` RESOLVES via the snapshot instead of a proj-attr literal injected at the
seam.

JC-B2b-1: these `workset.*` keys do NOT exist as resolvable snapshot keys otherwise —
`resolve_system_paths` derives only the PRIMARY pseudo-keys (`system._boxes` /
`system._primary_vault_*` / `system._primary_logs`) into `StandardPaths`, and there is no `workset.*`
tier in the snapshot. They are MATERIALIZED here.

⚑ **WHERE THE PER-MODE VARIATION LIVES (spec §2c).** It lives HERE and nowhere downstream, so every
rooted key plus the box home spell themselves ONCE against `@meta.box.path` / `@workset.*`. Each
anchor is the spec's own self-resolving `@`-ref FORMULA, not a proj-attr literal, and the formulas
were verified equal to the layout helpers they replace in every mode:

```
workset.boxes      primary  @meta.workset.path/boxes    == std.boxes (paths.py)
                   named    @meta.workset.path/boxes    == ws.projects_dir
                   s'alone  @meta.workset.path/box_data == _standalone_box_paths
workset.vault_*    ALL      @meta.workset.path/vault/{ro,rw}   (ALL PROJECTS)
workset.logs       p/n      @meta.workset.path/logs     == helper_log_path parent
                   s'alone  @meta.box.path              == helper_log_path parent
```

The whole chain is gated by a launch-path byte-identity check on the resolved home / vault /
helper_log mounts.

* `workset.vault_{ro,rw}` are UNIFORM in every mode (§2c ALL PROJECTS). Only the BOX BIND differs per
  mode — the per-box `/@meta.box.name` subdir a lone box does not need.
* `workset.canon` / `box.canon` are the per-scope CANON CONTRIBUTION roots, UNIFORM in every mode
  (§2c ALL PROJECTS / §2b). Their `handbook/` subtrees are the sources of the skip-if-absent
  `canon_hb_{workset,box}` binds, so repointing either key moves that scope's handbook chapter. ⚑
  NEITHER is a seed dest: no seed layer ever targeted `workset.canon`, and the retired handbook
  layers that targeted `@box.canon/handbook` are gone as of 2026-08-07g. The box chapter is filled
  HOST-side at create by `launch.templates.install_box_handbook_template` and delivered by the
  `canon_hb_box` bind.
* Uniformity with no per-mode arm and no `<None>` carve-out is only safe because the chapter binds
  these feed are SKIP-IF-ABSENT (spec §2c says so explicitly): the keys always resolve, and nothing
  is created on disk until someone actually writes a chapter.
* `meta.box.path` — the RO BOX ROOT: `@workset.boxes/@meta.box.name` (primary/named, §2c) ·
  `@workset.boxes` (standalone, §2c). The standalone form is the EMPTY LEAF: a BARE whole-value
  `@`-ref, so the resolver inherits `@workset.boxes` verbatim (`_is_whole_value_ref` decides the
  shape by PARSE) — there is no join, hence no trailing separator and no empty path segment. Being
  `meta.*` it is RO by contract (§0 meta ⟺ not-settable): the CLI refuses a `meta.*` set and
  `assemble_levels` drops a top-level `meta:` table from every settings file. Relocating box data is
  done one level up, via the settable `workset.boxes`.
* `meta.box.home` — the RO DERIVED box-home SOURCE `@meta.box.path/home`, UNIFORM in every mode (§2c
  ALL PROJECTS). Its settable source is the chain under the box root, so it needs no per-mode arm of
  its own — exactly like `meta.box.settings`.

⚑ `workset.logs` is what makes the helper-log bind a SINGLE row for all modes: the bind is the
spec's own `@workset.logs/@{meta.box.name}.jsonl` (§2c ALL PROJECTS), so the per-mode variation is
this anchor and nothing downstream. The braced `@{...}` delimits the ref so the `.jsonl` suffix
survives — the bare form would swallow it into the ref name (PHASE R; `settings_resolve.match_ref`).
There is deliberately NO `meta.box.helper_log` anchor: that construct-time LITERAL existed only
because the spec's spelling did not parse, and it is not a spec-declared key (§2c declares
`meta.box.{path,name,workspace,inbox,share_*,auth.*}` and never it), so under §0's closed keyspace
it was not a key at all. Do not reintroduce it — one bind, one spelling.

⚑ `box.canon` is a BOX-scope key living in a builder named for the workset anchors, and that is
deliberate rather than sloppy: it is spelled against `meta.box.path`, which this builder OWNS (it is
the only place the per-mode box root exists), so splitting it out would mean a second builder whose
sole input is this one's output. The name is the honest cost.

⚑⚑ **`@box.canon` IS NOT `~/canon`.** It is the box's CONTRIBUTION root on the HOST
(`<box_dir>/canon`), whose `handbook/` is ONE CHAPTER bound RO into the assembled
`~/canon/handbook/box`. The box's assembled guest view lives at `<box_dir>/home/canon` and arrives
through the home bind. Same word, adjacent paths, opposite directions of travel.

`BOX_HOME_KEY` is NAMED, unlike its sibling floor keys, because it has readers OUTSIDE this module:
the assembly seam builds the pid-0 foundation bind from it
(`commands/start.py._install_assembly_collapse`) and `box show --effective` renders it. One spelling
for the producer and both consumers. ⚑ Home does NOT route through `bindings.rw` (spec `:1015`) —
this key is what every launch's home mount resolves through. Do not re-inline the formula anywhere
downstream, and do not re-derive it from `proj.shell_path`.

### The declared workset-channel leaves

`_WORKSET_CHANNEL_LEAVES` is the DECLARED `workset.channels.*` family (spec §2c): the workset-LOCAL
type roots (`common` / `chat` / `broadcast` / `share`) plus the ALL-PROJECTS system-rooted addresses
(`mailboxes` / `share_global`, §2c "uniform per-mode addresses"). The floor MANUFACTURES these keys
from a caller-supplied mapping, so without this set it was a free-form passthrough: any leaf the
caller invented became a key, which is precisely what the CLOSED keyspace (spec §0) forbids — an
undeclared key is not a key, and code must REFUSE it rather than quietly accept it. This is the one
place a floor builds a key from a caller-supplied NAME.

⚑ It is the SPEC's declared family, not the subset a caller happens to pass. Pinning it to today's
caller would refuse a declared key the moment a second caller supplied one — the check exists to
stop FABRICATION, not to freeze the current call. That mattered: the live caller passed
`{common, chat, share}` for a long time, so `broadcast`, `mailboxes` and `share_global` were
declared keys that NO floor installed in any mode. `config set` took them, `config get` read them
back, and nothing changed. The caller now passes all six (R-35, "fix the CODE").

⚑ It must EQUAL `settings_keyspace.DECLARED_WORKSET_CHANNEL_LEAVES` (the validity table). The two
declarations answer the SAME question ("is this a `workset.channels` leaf?") from different seams,
and R-35's bug was exactly their disagreement — `mailboxes` accepted here, refused there. A test
pins the agreement so neither set can drift alone.

*workset_channels* maps each declared leaf to its RESOLVED path, materialized as
`workset.channels.*` so the workset-channel binds and the `meta.box.*` addresses (spec §2c) route
through them. ⚑ **TWO MODE GATES, NOT ONE:** the four workset-LOCAL leaves come from
`workset_channel_paths` (PRIMARY/NAMED only), the two ALL-PROJECTS leaves from
`workset_partition_paths` (every mode, standalone included). So this argument is **not `None` for a
standalone box** — the gate is per leaf and lives at the caller,
`start.py::_workset_channel_floor_values`. Treating the whole family as one `None`-for-standalone
group is how three of the six lost their floor.

*channelroot* is the resolved `workset.channelroot` (`None` for STANDALONE, which declares no value
for it). ⚑ A LITERAL, not the spec's `@meta.workset.path/channels` formula, and deliberately: the
key is read on the DETECTION side before any snapshot exists, so the floor must carry the answer
that pass already reached or one key would resolve two ways. It was emitted by no floor at all
until 2026-08-25 — a manifest row with a declared default that the keyspace could not answer, so
`@workset.channelroot` dangled in every launch snapshot.

`_BOX_MODES` is the set of box modes this floor knows how to root. An undeclared variant is NOT a
mode and is REFUSED rather than silently taking the primary/named arm.

### ⚑ A BOX ROOT THAT DOES NOT RESOLVE IS CATASTROPHIC, NOT COSMETIC

The consumer is the ASSEMBLY SEAM, which builds the pid-0 foundation bind straight off
`meta.box.home`, and that key is itself the EMBEDDED ref `@meta.box.path/home` — the embedded rule
(§6b) coerces an absent / present-`None` referent to `""`, so a box root that fails to resolve
yields the `host_src` `/home`, which the L7 guarantee-create then `mkdir`s and mounts OVER the box
home, silently. Naming the key moved the embedded dereference one level up; it did not remove it.
The derived key is a NAME for that formula, not a guard on it.

The floor values themselves are constants and cannot be `None`, but the anchor dereferences the
SETTABLE `workset.boxes`, so a settings file carrying `workset: {boxes: null}` (or an empty-string
terminal) still reaches `_assert_box_root_resolved`. That is why `build_launch_snapshot` asserts the
resolved `meta.box.path` is a usable path — and only when the caller actually supplied the anchor in
its floor fragment, so narrow resolves and partial-floor callers are unaffected.

⚑ **AND THE RESULT CAN LOOK PERFECTLY VALID, which is why BOTH keys are checked.** With
`workset.boxes` present-`None`:

* standalone — `meta.box.path` is the bare `@workset.boxes` (whole-value), so the root inherits
  `None` and the home `host_src` becomes the host's `/home`. A root-level check catches this.
* primary/named — `meta.box.path` is `@workset.boxes/@meta.box.name` (embedded), so the empty
  substitution yields `/mybox`: a syntactically perfect absolute path that no shape check would
  reject, pointing at a top-level directory that is not the box's. Only validating the SOURCE
  catches it.

⚑ **AND A THIRD SHAPE: a root ending in `/` means the LEAF vanished, not the root.** With
`meta.box.name` empty or `None`, the primary/named formula `@workset.boxes/@meta.box.name` yields
`<…>/boxes/` and the home `host_src` becomes `<…>/boxes//home` — the BOXES DIRECTORY's home, which
every box in the workset would then share. That is reachable today: the unregistered-primary
fallback in `paths._resolve_local_dir` returns an empty name, and the launch passes `proj.name`
through unexamined.

⚑ **THE TEST IS EXISTENCE + LEAF, NOT ABSOLUTENESS — deliberately, and please do not "tighten" it to
require a leading `/`.** That was tried: it reddens 131 tests in `tests/test_commands/test_start.py`,
which mock `load_std_paths()` wholesale, so `config.primary_workset` stringifies to `"<MagicMock …>"`
and the resolved root is legitimately not a real path in that context. No production path reaches
this check non-absolute (the foundation is a real `Path`), so the absoluteness arm bought nothing and
cost a harness-wide false positive. What catches the real hazard is the §3 three-state (absent /
present-`None` / `""`-terminal) plus the vanished-leaf shape.

`_BOX_STORE_KEY` (`workset.boxes`) is the SETTABLE key the box root dereferences, and it is
validated ALONGSIDE the root because a broken source does not always produce a broken-LOOKING root.

`snapshot_leaf` is PUBLIC because the assembly seam reads `meta.box.home` through it
(`commands/start.py._install_assembly_collapse`). One reader, so a dotted read off a resolved
snapshot cannot acquire a second spelling with its own idea of what absence looks like.

## `build_launch_snapshot` — the level splice

`build_launch_snapshot` folds the behavior floor (mapped to `agent.default.<key>` — OS1, the
all-agents backstop) and every runtime `default_categories` table into ONE base-level floor,
assembles the 6-level cascade (S8) with 7a's *agent_partial* inserted as an additional agent-level
source (S27), merges (S15), and expands (S17/S19) with *ctx*. There is NO bare `agent.<key>` in the
snapshot (spec §2d / §0) — the agent tier is DISCRIMINATED throughout.

*behavior_floor* is the BARE behavior-default dict (`{d.key: d.default}`). *default_categories* are
the already-scope-qualified category default tables, unioned across every mount family: each KEY is
a whole category ARM and each VALUE the whole DEST-KEYED map under it —
`{"box.bindings.rw": {box_dest: (host_src, opts)}, ...}`, the shape `core_defaults.add_bind` builds
(R-5 / 2026-08-08c: the map is TERMINAL, so there is no trailing entry-name segment and no dest
inside the value).

*persona_values* are the PERSONA STORE's rendered values for the ACTIVE agent (`endpoint` / `model`
/ `secret_path.<VAR>` / `env.<VAR>`), collected ONCE per launch by the caller and threaded in as an
IN-MEMORY level because they are NEVER persisted to any settings file — the store's rendered host
config is a LIVE resolution input, so a launch leaves `agents/<node>/settings.yaml` byte-identical.
Threading (rather than riding a file) is forced by the rebuild count: `_resolve_launch_snapshot`
re-reads the files several times per launch, and a never-written layer has no file to be read from.
The keys arrive UN-DISCRIMINATED (the store knows a persona, not a cascade) and are discriminated
onto *agent_name* here, by `_persona_partial`. `None` (the default) means NO persona tier at all —
the snapshot is byte-identical to a pre-persona build.

*auth_chain* / *meta_runtime* / *meta_identity* / *workset_anchor* are the four floor fragments
described above, each folded into the SAME floor so `expand` resolves its `@`-ref chain ONCE
(single-route). Each is `None` for a NARROW resolve that does not need it — the seed / synced /
image / helper sub-resolves — so those snapshots simply lack those keys. The `meta.*` fragments are
construct-set RO (§0): NO scope FILE may override them, since `meta.*` is not in the config-set
settable known-key list, so the floor is their sole source. A scope FILE MAY legitimately override a
`workset.*` key (it is a settable settings tier), so those sit at the floor (base) and a workset/box
file still wins by name. The auth chain is injected AFTER the category tables so the dotted chain
keys (`box.auth.*` / `workset.auth.*` / `system.auth.*`) land unconditionally.

*prefs* are the `pref.*` REQUESTS (spec §2h) of the workset + box files, in application order.
`None` (the default) means COLLECT THEM HERE from *workset_path* / *box_path* — the fail-safe
default, so a caller cannot omit them by accident. Supplying them is a CACHE, not a second source:
the one collector is `settings_prefs.collect_prefs` either way, and validation runs in `apply_prefs`
regardless of who collected, so supplying the list cannot bypass a filter. `_resolve_launch_snapshot`,
which runs several resolves per launch, collects ONCE and passes the SAME list to all of them. The
narrow seed / synced / image / helper resolves must see a pref on `agent.<a>.seeded.*` too, which is
why no call path may silently skip collection.

⚑ P7's SELECTION pass (`resolve_selected_agent`) is a SEPARATE read, not a share: it runs BEFORE the
agent is known, so there is no launch-side list to hand it. That is two reads of the same two files
per launch, and they cannot disagree — the pair comes from `paths._box_settings_files` (a runtime
treewalk consulting no settings key) and nothing writes between them.

*valid_agents* injects the agent-validity set (defaults to plugin discovery); tests supply their own.
It is passed through UNRESOLVED (`None` = "decide inside"): `apply_prefs` reaches plugin discovery
only when a request actually names `agent.*`, so a pref-free launch pays nothing. The test is `is
None`, not falsy — an empty `AgentNames` is a legitimate caller-supplied value.

### The CLI level (§1A)

*cli_level* is the §1A **top-most input level** — above every settings file AND every pref (*"The
COMMAND LINE is its OWN LEVEL — the highest… a GENERAL rule, not a carve-out"*). It is built by
`settings_cli_level.build_cli_level`, which owns the flag→key table.

`guard_cli_level` is applied HERE, before the splice, so no call site can bypass it (P8). §1A says
the §2h forbidden tiers do NOT cover the CLI, so a flag that could set a LOCATOR-class value needs
its own guard — and a guard a caller can forget to run is not a guard. *valid_agents* is passed
through so a caller that already has the discovered set does not pay for a second discovery; when it
is `None` the guard uses `{agent_name}`, since every agent-scope key in the level is spelled against
the ALREADY-RESOLVED active agent and discovery would be pure cost on a flag-free launch.

The level always carries the RESOLVED agent selection (`agent_select.select_agent`) whichever of its
three sources won — `--agent`, the cascade, or the installed-count rule. Installing that ALWAYS (not
only for `--agent`) is what keeps `@system.agent` equal to the node that actually runs, which the two
re-pointed §2c anchors depend on. P8 added the ephemeral flag values beside it (`-M` →
`agent.<active>.model`, `-N`/`-C`/`-R` → `agent.<active>.continue_mode`).

⚑ **WHO MUST PASS IT, precisely** — "the narrow resolves can skip it" is NOT the rule, and reading it
that way is what cost the credential path once already:

* **REQUIRED** by every resolve that carries the `auth_chain` floor (`_resolve_box_auth_source` /
  `_resolve_box_launch_decisions`, on the launch AND on stop / creds-watch / reauth / the
  `--effective` display), because `meta.box.auth.workset_path` = `@workset.auth.path/@system.agent`
  — omit it and the per-agent credential dir collapses to the workset auth ROOT. Those two functions
  take it as a REQUIRED keyword for that reason.
* **Not needed** by the seed / synced / image / helper narrow resolves: they carry no auth chain and
  no shipped declaration references `@system.agent`.
* `None` for a NO-AGENT box — `system.agent` must stay absent / `None` there, not be pinned to the
  `"general"` template slot.

⚑ **WHICH RESOLVES SEE THE EPHEMERAL FLAGS** (P8, spec §1A *"EPHEMERAL, always … a flag NEVER mutates
a stored value"*): the SELECTION rides every resolve that needs it (the list above); the FLAGS ride
only the resolve that decides THIS launch's runtime. No resolve whose output is WRITTEN TO DISK may
see a flag — so the persona endpoint/model resolve (`_resolve_box_launch_decisions`, whose model
feeds the codex `config.toml` write), the create-time seed, and the `--effective` display all take a
selection-ONLY level.

### The splice itself

`assemble_levels` ALWAYS returns the 6 levels MOST-SPECIFIC-FIRST (S8):

```
[box, workset, agent.<active>, agent.default, system, base]
 idx 0    1        2              3              4       5
```

The FINAL ordered level list splices the optional extra partials at their PRECISE precedence rungs,
computed from these FIXED base indices. Doing all splices in one pass keeps the math robust — no
chained index drift.

* **agent_state** — the per-agent FILE's behavior as an `AgentFileLevel`, wrapped under the node the
  BOUNDARY attached (C-2), at the AGENT-FILE rung: above the empty assemble `agent.<active>` level,
  below workset. That is the OLD `LevelView("agent", agent_cfg.state)` precedence.
* **persona_values** — the persona STORE's live values, wrapped under the active slot, BELOW the
  per-agent FILE (both its flat state rung and its `agent.<active>` tables) and ABOVE
  `agent.default`.
* **agent_partial** — 7a descriptor DEFAULT delivery, the LEAST-specific agent rung (just below
  `agent.default`), so any `agent.<active>` / workset / box repoint wins.
* **pref overlays** — the `pref.*` REQUESTS of the workset and box files (spec §2h), each installed
  IMMEDIATELY BELOW its own level's partial. §2h expands a level's prefs *"at the START of that
  level, BEFORE the level resolves"*, so the level's own keys are applied AFTER and win; and the BOX
  overlay precedes the WORKSET overlay, which IS box-beats-workset by assignment order (§1A).
* **cli_level** — the §1A CLI LEVEL, ABOVE EVERYTHING (index 0).

⚑ **The pref overlay placement is currently UNOBSERVABLE, recorded deliberately.** Nothing LEGAL
contends with either overlay: a box/workset file may not set `system.agent` or `agent.<a>.*` at all
(upward writes, dropped by `_drop_upward_scopes`). So moving an overlay above its level's partial
breaks no test. It is spelled this way because §2h says prefs expand at the START of the level, and
it becomes observable the moment the allowlist grows to a target a lower file may also set. Left
unpinned on purpose: a test asserting an unobservable ordering would be asserting the
implementation, not the behaviour.

⚑ **The persona rung's ordering is semantically FORCED, not a preference.** The agent file stores
ONLY non-default values, so an `agent.<active>.<key>` present in it can only be a DELIBERATE user
edit — and a user edit must outrank a value the store re-renders on every launch. Below the file,
above the defaults: write the file once to pin a value, write nothing to stay persona-driven.

⚑ **The persona placement relative to `base_levels[3]` is likewise UNOBSERVABLE in the merge** — the
same class of note as the pref overlays. This partial spells `agent.<active>.*` and that level
spells `agent.default.*`: DISJOINT names (§2d keeps the two agent slots distinct), so they never
contend by name, and moving the append below breaks no test. Persona-beats-`agent.default` is real
all the same, it is just decided ELSEWHERE — by `effective_behavior`'s §2d active-over-default pick,
which takes the active slot unconditionally. The nearest level this DOES contend with, and must
beat, is `agent_partial`, the 7a descriptor default, which does spell the active slot and is
appended below `agent.default`. It is spelled at this rung anyway because that is the rung the
ordering was ruled at.

⚑ **THE `box.agent.*` CATEGORY FOLD IS GONE (P7).** It existed to give a box's SETTABLE
`box.agent.<category>` tweak box-precedence inside the active agent's slot. Spec §2b retires the
settable mirror wholesale: `box.agent.*` is now the RO read-back `meta.box.agent.*`, and a box tweaks
its agent with `pref.agent.<agent>.<key>` (§2h) — a pref overlay, already spliced. So the fold has no
settable input left to fold, and removing it FLIPS the transitional contest P6 pinned: a box pref now
wins a CATEGORY, as it already won a SCALAR. Pinned by
`tests/test_settings/test_settings_launch.py::TestPrefLevelPrecedence`.

After `merge` + `expand`, `_materialize_box_agent_mirror` runs, and the box-root assertion runs when
the caller supplied the anchor.

### The tail of the seam: measure, then enforce, then choose the message

Three calls close `build_launch_snapshot`, and the ORDER of all three is load-bearing.

1. `observe_keyspace` — the REPORT-ONLY probe. It runs FIRST because a raise ahead of it would blind
   the instrument to precisely the resolves that matter, so any later re-measurement would see only
   the snapshots that already conform.
2. `_refuse_undeclared_snapshot` — §0's RESOLVE clause. A SIBLING of the probe, never a mode of it;
   the two share the ORACLE, so what is armed is exactly what was measured.
3. `_refuse_retired_spelling`, called BY the refusal once it has findings — the message CHOICE.

⚑ **WHY (3) EXISTS.** Every retired spelling is also an undeclared key, so (2) reaches it first —
and the seams that own the tailored retirement messages (`agent_select` for the selection keys,
`commands/start.py` for the behaviour key) both sit DOWNSTREAM of the resolve. Arming (2) therefore
took those messages away from the users they were written for: a `box.yaml` carrying
`box: {agent_name: claude}` got "not a settings key", and the ~40 lines `MIGRATION.md` §2.1 spends
on the cure reached nobody. Measured, and mutation-proved in both directions.

⚑ **IT IS NOT AN EXEMPTION.** The retired key is still refused; only the TEXT differs. A name-keyed
escape from §0 is the carve-out class the closed keyspace exists to reject, and it would hide the
next finding behind itself.

⚑ **LAZY, AND THAT IS WHAT MAKES IT AFFORDABLE.** It re-reads the settings files, so it runs only
after §0 has decided to refuse. Wired ahead of that decision it would put a second read of every
tier on every resolve behind `load_merged_config` — nearly every kanibako command.

⚑⚑ **IT JUDGES WHAT THE CASCADE SEES** — each file goes through `settings_assemble.cascade_view`
before either table is consulted. A settings file may legally CONTAIN a table the cascade never
reads, and reading the raw doc let a retired spelling in one of those speak: MEASURED, a `box.yaml`
holding both `agent: {claude: {auto_approve: true}}` and `box: {zippity: wibble}` refused by naming
`auto_approve` and prescribing a `pref.agent.<agent>.access` write — for a table directional
enforcement had dropped one log line earlier — while `box.zippity`, the entry that actually stopped
the resolve, went unnamed. A cure for a no-op is worse than no cure: it tells a user their
permission tier is about to change when deleting the line changes nothing. Mutation-proved BOTH
ways — reading the raw doc reds the two dropped-table pins, and a `cascade_view` that returned
nothing reds all five retirement pins plus the control, so the fix is a narrowing rather than a
silencing.

⚑ **NEITHER SUBJECT IS GUESSED, AND THE COST IS USER-VISIBLE.** `box_name` and the agent `subject`
keep their `<box>` / `<agent>` placeholders. 🛑 **THE REASON IS ORDERING, NOT ABSENCE.**
`commands/start.py:6394` DOES build `meta_identity` carrying `meta.box.name` and DOES pass it to
`build_launch_snapshot` (`:6075`, `:6161`, `:6819`) — the claim that "no production path arrives
with an identity floor" was simply false. What is true is narrower and sufficient:
`load_merged_config` (`start.py:2417`) runs well before `select_agent` (`:2666`), so the resolve
that REACHES this refusal is always the narrow, identity-free one, and a `meta.box.name` read here
would find nothing on the path that matters. `MIGRATION.md` §2.1 states the placeholder rather than
leaving a user to notice it.

⚑ **THE `base` TIER IS SCANNED**, appended by `_refuse_retired_spelling` itself off
`settings_base_path()` — the same default `assemble_levels` resolves internally, read rather than
threaded through a parameter nobody varies. It was left out on the reasoning that
`build_launch_snapshot` "is not handed its path", which was never a reason: `assemble_levels` takes
`base_path: Path | None = None` and `settings_base_path` is patchable exactly like every other seam
binding here. And the omission was worse than it read — `agent_select` has always scanned base
("a site admin's stale key defaults DOWN into every box on the machine"), and arming the resolve
put this seam FIRST, so a site-wide fault would have got strictly LESS help than before `505e70d`.

⚑ **ONE FILE, ONE MESSAGE.** The scan looks at the whole file, not at the offending path, so a file
carrying BOTH a retired spelling and an ordinary undeclared key reports the retirement and reveals
the other on the next run. That is a deliberate trade against §0's own "name every entry" rule: the
retirement message is worth more than the second line, and the second line is not lost.

## Agent SELECTION — the narrow resolve that precedes the launch snapshot (P7)

`resolve_selected_agent` resolves `system.agent` (`SELECTION_KEY`, spec §2g — the key that names the
agent a box runs) as the settings files plus their prefs give it. It returns the resolved value in
THREE distinguishable states, which the caller MUST keep apart (see `settings/agent_select.py`):

* `str` — a name: the stored `system.agent`, or a `pref.system.agent` request from the box (which
  beats) or the workset file (§2h);
* `None` — PRESENT-`None`: an explicit `pref.system.agent: null` SUPPRESSION ⇒ the NO-AGENT
  plain-shell box (spec §2b, D-M6). A present-`None` on a SCALAR leaf is KEPT by
  `_resolve_present_none`, which is exactly what makes this reachable — `if value is None: continue`
  anywhere on this path silently deletes the capability;
* `__MISSING__` — nothing ever set it ⇒ the caller falls through to the installed-count rule.

**Why this is a SEPARATE resolve.** `build_launch_snapshot` needs the active agent BEFORE it
assembles: it discriminates the agent tier, wraps the per-agent file's state, and builds the
meta-agent floor — and the active agent is now a key INSIDE that cascade. The pass is safe to run
first because the pref-legal file pair comes from the runtime TREEWALK
(`paths._box_settings_files`), which consults no settings key; see the termination note in
`settings_prefs`.

No `agent_path` is passed: the agent-tier FILE is selected BY this key, so reading it here would be
the chicken-and-egg this function exists to break.

**Why LENIENT expand.** `expand` is whole-tree, and in STRICT mode a defect anywhere would abort
selection — e.g. a perfectly legitimate `$AGENT` in some unrelated bind source raises here, because
this pass has no active agent yet (`ctx.agent_name` is `None`) and `_resolve_var` refuses an unset
`$AGENT`. LENIENT mode (`collect_errors=True`, the same arm set-time validation uses) RECORDS each
defective leaf and omits it, so unrelated defects cannot decide which agent runs — while a defect ON
`system.agent` itself is in the error map and is RAISED here, naming the key and the reason (§2h:
*"We don't want to just moving on with bad settings"*). Never a silent fall-through to no-agent.

⚑ The lenient overload is typed `KeyStore | tuple[KeyStore, dict]` because `collect_errors` is a
plain `bool`, so the pair has to be narrowed at the call site — the same two-line shape
`config_interface` uses at its two lenient calls. A `KeyStore` unpacks into two `str`s without
complaint (it IS a `dict[str, …]`), which is exactly what the `assert isinstance(result, tuple)`
stops.

## `meta.box.agent.*` RO mirror materialization (block B5 — spec §2b)

Spec §2b: `meta.box.agent.<key>` is the box-scoped READ-BACK of its active agent's WHOLE resolved
settings subtree — `agent.<@system.agent>.<key>` with the `agent.default` fallback. Values are still
READABLE here; they are no longer SETTABLE. Being `meta.*` it is RO BY CONTRACT (§0: meta ⟺ not
settable), so it cannot be set to a dangling `@`-ref and no settings file can contribute to it. It
is re-materialized whenever the effective agent changes.

⮕ **P7 RETIRED THE SETTABLE `box.agent.*` MIRROR that used to live here.** It was one of the three
devices §2h replaced (*"Every solution is a hack/exception, so I'm biting the bullet"*): it made a
BOX-scope key the input to the agent tier, and it made the L4.1 anchors derive from a settable key at
their own level. A box now tweaks its agent with `pref.agent.<agent>.<key>` (§2h), which targets the
AGENT tier properly and needs no mirror. TWO consequences, both deliberate: there is no gap-FILL any
more — nothing can pre-set a name under `meta.box.agent` — so the materialization is a straight COPY;
and the `box.agent.<category>` → `agent.<active>` pre-merge FOLD is gone with it.

**MECHANISM (JC-B5-1 — COPY, materialized on the current engine, no resolver inversion).** The
resolved active-agent subtree only EXISTS post-merge/expand: the cascade keeps `agent.default` and
`agent.<active>` DISCRIMINATED, and the active-over-default value-pick is a CONSUMER step
(`_agent_pick_node`). So `meta.box.agent.*` is materialized AFTER `expand` as a deep COPY of that
resolved effective-agent node into `snapshot["meta"]["box"]["agent"]`:

* `meta.box.agent.<key>` reads back exactly what `agent.<@system.agent>.<key>` resolved to (the
  effective node IS `agent.default` overlaid by `agent.<active>`, §2d pick) — including any
  `pref.agent.<agent>.<key>` the box requested, since a pref is an INPUT to that resolution rather
  than a patch on top of it;
* NO LEAK — the materialized subtree is a FRESH deep COPY (`_deep_copy_store` leaves immutable
  Bind/scalar/None leaves shared but never aliases a nested KeyStore), written ONLY under
  `meta.box.agent.*`. `snapshot["agent"]` is never mutated, so a later in-place edit of the read-back
  cannot escape into the shared agent subtree.

Re-materialization on an agent change is AUTOMATIC: `agent_name` is the launch-resolved active agent
(`@system.agent` — the stored key, a `pref.system.agent` request, `--agent`, or the installed-count
rule; see `settings/agent_select.py`), threaded into every snapshot build.

⚑ The auth floor separately materializes `meta.box.agent.auth.share_support` (the capability mirror,
a PRE-expand floor key), so this copy must not clobber it: an existing name under `meta.box.agent` is
LEFT INTACT. `_mirror_fill` therefore gap-fills DEEP — it copies each agent-node name the box node
does NOT already set, and recurses into matching KeyStore subtrees so a pre-set leaf does not
suppress mirrored siblings. A box leaf vs an agent subtree (or vice versa) means the box wholesale-
overrode that name: leave the box value, do NOT merge across the type boundary. Reads and writes go
through the UNBOUND `dict` protocol (S3) so a key named `get` or `agent` cannot shadow.

### ⚑ NO-AGENT box — WHAT ACTUALLY HAPPENS, measured

The inherited comment here was WRONG twice over: it claimed a spec requirement that §2b does not
state, and a caller behaviour no caller has.

* The LAUNCH passes `agent_name="general"` for a no-agent/shell box (`start.py`:
  `agent_id = with_harness(...) if target else "general"`), so the blank short-circuit does NOT fire
  and the mirror is materialized from the §2d pick — which for `"general"` is the `agent.default`
  backstop alone (no `agent.general` table exists anywhere). MEASURED on the launch shape:
  `meta.box.agent` holds `auth` (the floor's capability key) PLUS the `agent.default` behavior leaves
  (`access` / `bootstrap` / `model`). That is a defensible read-back — it IS the effective subtree
  when the effective agent is the default backstop — and NOTHING consumes those leaves: the only
  runtime reader under `meta.box.agent` is `auth.share_support`, which the auth FLOOR materializes
  pre-expand, not this copy.
* A caller that passes a BLANK `active_agent` (tests, and any future caller that wants the strict
  reading) gets an EMPTY mirror — the short-circuit. It does NOT fall back to `agent.default`, which
  is the all-agents backstop, not an ACTIVE agent the box runs.

Both shapes are PINNED (`tests/test_settings/test_settings_launch.py`). If the strict reading is ever
wanted at the launch too, the change belongs at the `agent_id` seam in `start.py`, not here — this
function mirrors whatever active agent it is given.

## The two level-wrapping partials

`_agent_state_partial` wraps one agent-file behavior LEVEL under its own slot —
`{agent: {<level.node>: {<key>: <val>}}}`. The per-agent file (`agents/<active>/settings.yaml`,
loaded as `agent_cfg.state`) stores behavior FLAT (`agent.model` — already per-agent), NOT the
discriminated `agent.<active>.*` / `agent.default.*` sub-tables that `assemble_levels`'
`_agent_partial` reads, which treats a flat `[agent]` table as UNSET. So passing the file raw as
`agent_path` DROPS its behavior; this wraps it into the DISCRIMINATED slot (the §2d / §0 form) so it
merges by name.

⚑ **IT NEEDS NO GATE OF ITS OWN, AND THAT IS DELIBERATE (P4).** The undeclared keys it used to ride
through verbatim — the "forward-compat" passthrough spec §0 SPECIFICALLY EXCLUDES — are refused at
the BOUNDARY that builds the level (`agent_file.state_level`), so nothing undeclared can reach this
function to be gated. A second check here would be a rule spelled twice, and the one downstream
would be the one that rots.

⚑⚑ **THE DISCRIMINATOR ARRIVES WITH THE DATA (C-2; [spec:15-21, "self"]).** It used to be a SECOND
parameter taken from the caller's `agent_name` while the state dict travelled undiscriminated all the
way from `agent_file.load`, so the node the table came FROM and the node it merged UNDER were two
independent facts that nothing cross-checked. `agent_file.state_level` now attaches the file's own
node at the boundary and the pair travels as one `AgentFileLevel`; there is no longer a parameter to
pass the wrong node in.

`_persona_partial` wraps the PERSONA STORE's live values under the active slot —
`{agent: {<agent_name>: {...}}}`. The store hands over UN-DISCRIMINATED keys (it knows a persona, not
a cascade): the bare behavior names `endpoint` / `model`, and the two open categories
`secret_path.<VAR>` / `env.<VAR>`. This discriminates them onto *agent_name*, the §2d / §0 form, so
they merge by name at the persona rung. No value is bind-shaped (`env` / `secret_path` are not bind
categories), so every leaf is stored verbatim.

⚑ **DELIBERATE DIVERGENCE from the sibling `dotted_partial` / `_insert_dotted` route, which this must
NOT use.** Those split a key on EVERY dot and explode it into a nested subtree. A `<VAR>` here is
arbitrary user-supplied text out of a JSON file, so a var spelled `FOO.BAR` would silently become the
subtree `env.FOO.BAR` instead of the ONE leaf the user wrote, and would then never be exported. So:
split on the FIRST dot ONLY. A bare key is a leaf directly under the active-agent node; a
`<category>.<VAR>` key puts `<VAR>` in as a LITERAL leaf key under that category node, however it is
spelled.

## The behavior read — `effective_behavior`

This is the LIVE launch behavior reader (block 7b — ruling A, the FULL swap): it replaces
`start.py`'s retired `_build_effective_state` LAUNCH read. The behavior cascade now flows through the
ONE snapshot — each scope file's `agent.default.*` / `agent.<active>.*` tables merge by NAME (block
2b / `assemble_levels` S8), the declared-default floor folds in under `base` as `agent.default.*`
(OS1) — and THIS function does the §2d active-over-default value-pick over that merged result. It
returns the `{key: str}` dict the descriptor assembler consumes.

**Resolution order (the SPEC model, S8 + §2d): cascade FIRST, THEN active-over-default.** The merge
already resolved `agent.<active>.<key>` and `agent.default.<key>` across ALL scopes by name (a
box-file `agent.<active>.model` beats the agent-file one — box is more specific); this pick then
takes the active slot's winner over the default slot's winner. So an agent-file
`agent.<active>.model` BEATS a box-file `agent.default.model` — active wins the pick regardless of
scope. That is the one place this differs from the old per-file-active-over-default-THEN-cascade
reader, a Jei-NOTED spec-CORRECTION covered by a behavior-equivalence test, NOT silent.

*keys*: when given, read exactly those keys; when `None` (the live default), DISCOVER every scalar
behavior leaf present under `agent.<active>` ∪ `agent.default`, so any undeclared agent-scope scalar
keys survive as pass-through, matching the old reader's key union. Category subtrees (`bindings` /
`meta` / `common` / …) and `Bind` leaves are NOT behavior and are skipped.

A key absent from BOTH slots is omitted. A present-`None` scalar (reset-to-default) in the WINNING
slot is omitted — the consumer applies its own default (§3) — and, since present-`None` SETS the
name, it shadows the `agent.default` value below it (the active slot reset it). Values are
stringified, since behavior settings are scalars. Reads go via the UNBOUND `dict` probe (S3).

⚑ **NO `box.agent.*` OVERLAY (P7).** The settable box-scoped agent mirror is RETIRED (spec §2b — it
is now the RO read-back `meta.box.agent.*`), so there is no box-scope behavior source to overlay
here: a box tweaks its agent's behavior with `pref.agent.<agent>.<key>` (§2h), an ordinary cascade
level, therefore ALREADY resolved into the active slot. Reading `meta.box.agent` here instead would
be a cycle — that node is MATERIALIZED FROM this pick.

## The category adapter — snapshot subtrees → the ONE list every delivery seam eats

`snapshot_category_entries` walks the snapshot's category subtrees into the ONE
`list[CategoryEntry]`. Every delivery seam downstream reads THIS list and no other: the per-scope
`store_shape` producer, the assembly collapse, and the launch seam's `LaunchDeliveries`. The shape is
the one the retired by-name resolver produced, unchanged (§6g).

For every `<scope>.<category>` subtree present it emits one entry per leaf. The four scopes are the
SAME `system, agent, workset, box` apply order the old by-name resolver used, so a same-scope tie
breaks identically, and every emitted entry's `scope` is the BARE scope token — the load-bearing
scope identity (§7), NOT the snapshot's agent discriminator. `_SCOPES` aliases the single-source
scope-containment tuple from `kb_store` so this consumer never re-declares the scope set; the old
byte-identical literal was a drift foot-gun. Containment ORDER is not load-bearing here — the emit
loop re-sorts by its own `scope_order` map — so it is safe to reuse verbatim.

`host_src` is read from the expanded `Bind` (already host-resolved at build) and used AS-IS: NOTHING
is prefixed here, ever. A stored source resolves ON ITS OWN (spec §2a); the abstract categories are
rooted at DECLARATION, and an assembly-time root-prepend is the shape §2a calls FORBIDDEN. Do not
reintroduce a per-scope root table here — a structural test scans for it.

`env` carries its VAR name in `box_dest` and its value in `options`; `masks` is value-less (one entry
per masked dest); `secret_path` is the SECRET category (spec §2a, 2026-07-06) — a scalar host PATH
keyed by VAR, delivered as a ro MOUNT to `SECRET_MOUNT_DIR/{VAR}`. It is modeled on the `env` branch
but MOUNT: `host_src` is the scalar path (already host-expanded by the expand pass — a scalar leaf is
expanded host-side for `~` / `$VAR` / `@`-refs) and `box_dest` is the fixed in-box secrets path.
`secret_path_winners` picks the per-VAR winner (box over workset) by identical `box_dest`; `start.py`
emits the ro Mount plus the box-side export shim — kanibako NEVER reads the file VALUE. `options="ro"`
(NO `:U` chown of the host secret), and `name` is the VAR, which the shim exports. A reset (`None`)
env var has no value to export and a reset `secret_path` has no path to mount; both are skipped.

*optional_keys* is matched against the FULL DISCRIMINATED `CategoryEntry.key` and sets
`CategoryEntry.optional` on the matching entries. It defaults EMPTY, so every caller that does not
pass it gets byte-identical output. It is a DECLARATION fact the snapshot cannot carry (a bind entry
has a source slot and no room for a second meaning), supplied by the ONE launch aggregation site:
`canon_optional_bind_keys()` for the skip-if-absent handbook chapters. ⚑ It is not a heuristic on the
VALUE. 🛑 The flag it sets is DECLARATION-ONLY since cutover step 3 — the emitter now takes the same
policy as a DEST SET (`core_defaults.canon_optional_bind_dests`), because a dest is the one thing the
collapsed bind map keeps.

⚑ **THE `host_dest_keys` COMPANION IS GONE (2026-08-08c).** Every destination is GUEST-spelled now —
copies included (spec §0 "ONE DEST SPACE, TWO DELIVERIES") — so there is no second namespace for a
key set to select. Do not reintroduce one; see `CategoryEntry` for the bug the discriminator used to
close and why the respell closed it at the source instead.

⚑ **`agent_delivery_mounts` LIVED HERE and is GONE (cutover 2a-3).** It was the SECOND mount emitter,
walking the same resolved list as the category emitter and filtering to the `scope == "agent"` half
so neither emitted the other's binds. Under one collapsed, dest-keyed bind map there is one emitter
and no half to filter — what survived is a per-dest missing-source POLICY (must-exist ·
skip-if-absent · warn-and-drop), which `commands.start._emit_category_mounts` now applies. The
AGENT_CRITICAL safe-fail is unchanged; only the site moved. 🛑 Do not reintroduce a second emitter:
the L7 guarantee-create / ro-drop rules exist ONCE precisely so two copies cannot drift apart in
silence.

### Where the undeclared-shape refusal runs, and why

⚑ The refusal runs on the RAW TIERS, before the pick, so its message can name the DISCRIMINATED key
the user actually wrote (`agent.default.bindings` vs `agent.<active>.bindings`). Checking the merged
node instead would only be able to say `agent.bindings` — a bare form that is NOT a key (§0), i.e. an
error message instructing the reader toward a shape the keyspace forbids.

`_assert_declared_categories` refuses every UNDECLARED category shape under ONE scope node (spec
§2d), naming the key with the prefix it is really written under. *key_prefix* is the DISCRIMINATED
key prefix — a bare scope token for system/workset/box, and `agent.default` / `agent.<active>` for
the agent tier.

⚑ **COVERAGE IS THE FOUR CATEGORY FAMILIES** — `bindings.{ro,rw}`, the `caches` / `seeded` / `common`
/ `synced` leaf categories, and `masks`. `masks` joined them on 2026-08-10: it is a dest-keyed
TERMINAL key exactly like the others (R-5/R-10), and its silent skip was the last route by which a
user-written category could vanish without a word. A `masks` LIST in a settings file stayed a plain
`list` through the merge, missed the emit's `isinstance(…, KeyStore)` guard, and left the path the
user asked to HIDE plainly readable inside the box — no mount, no warning. Its declared value is the
3-state marker rather than a source (spec §2a — `dict[box_dest → bool|None]`, *"NOT a bare list"*),
which changes only the example the refusal prints.

⚑ `env` and `secret_path` still keep their SILENT SKIP of a non-`KeyStore` node: they are the
scalar-valued pair, they were outside the boundary approved for the bind pass, and widening them is a
decision, not an omission to fix in passing. Tracked for the undeclared-shape sweep.

What the arm check asserts is UNCHANGED by the dest-keyed reshape: a bindings arm's value must be a
MAP node. Before, a map of names; now, a map of destinations. A scalar / `Bind` / list sitting at
`<scope>.bindings.ro` is an undeclared shape either way. An ARM-LESS `bindings.<name>` is refused
too: bindings are declared per arm and the arm is the WHOLE key. `masks` is checked on its own line
rather than folded into `_BIND_LEAF_CATEGORIES`, because that set is what the EMIT walks with
`_emit_bind_map`, and a mask has no source to unpack.

`_require_category_node` refuses a VALUE sitting at a CATEGORY ROOT (spec §2d) and returns the node
itself. Returning the node rather than `None` is what lets a caller keep reading it: the refusal is
the only thing standing between an `object` and a `KeyStore`, so handing the narrowed node back means
no caller has to restate the check to say what this function already guaranteed.

A category token names a NAMESPACE of per-name entries; it is not itself a declared key, so a scalar
/ `Bind` / list there is an UNDECLARED shape. Under the closed-keyspace rule (spec §0) an undeclared
key is an ERROR that names itself — never a silent accept. The check runs against the ASSEMBLED
snapshot, so it catches such a value from any origin that reaches it — a plugin defaults table, a
workset or box YAML, a `config set` — in ONE place. Before P3 these shapes were SILENTLY DROPPED by
`isinstance(x, KeyStore)` guards: the user's binding simply never appeared, with nothing said.

⚑ ONE ROUTE DOES NOT REACH IT: `workset share list --effective` returns "No bindings configured" and
never resolves when a workset file's ONLY `bindings` content is the malformed value, because its
raw-shares reader walks for per-name leaves and finds none. The launch path still refuses.

PRESENT-BUT-EMPTY (`bindings: {}` / `common: {}`) is NOT an error: an empty node is
byte-indistinguishable from an absent one after `assemble`, so erroring would trap a no-op. §2d
itself calls the `agent.default.bindings | {}` row "documentation of intent, not a required default".

The refusal message names what a user must declare: the MAP, keyed by destination — never a `.<name>`
entry, which is no longer a key at any scope. `masks` is dest-keyed like the rest, but its VALUE is
the 3-state marker (present = mask · null = unmask · absent = inherit, spec §2a) and not a source, so
only the example spelling differs (`{box_dest: true}` vs `{box_dest: [src[, options]]}`).

### The emitters

`_emit_scope_node` emits every category entry under ONE (bare) scope NODE. *scope_node* is a single
scope's category subtree (`snapshot.<scope>` for a non-agent scope; the effective agent node for the
agent scope). *scope* is the BARE scope token used for the emitted `CategoryEntry.scope` — the
load-bearing precedence identity. `bindings.{ro,rw}` is the ARMED category, where the map is one
level under the token; `caches` / `seeded` / `common` / `synced` hold the map AT the category token.

**EMISSION ONLY.** The undeclared-shape refusal ran earlier, in `snapshot_category_entries`, against
the RAW tiers. By the time a node reaches the emitter every category token it carries is a
`KeyStore`, and the `isinstance` skips there are unreachable guards rather than the silent drops they
were before P3 — TYPE NARROWS, not filters, so nothing can be dropped in silence.

⚑ The two bind LEAF-TYPE rulings are a different thing from those skips: they are where the
dest-keyed and name-keyed shapes are told apart, and they RAISE (naming the key and the shape
expected) rather than skip.

`_emit_bind_map` emits every entry of ONE terminal DEST-KEYED category map — the single loop behind
all six bind-shaped categories. *map_node* is the `BindMap` node itself, found at the ARM for
`bindings.{ro,rw}` and at the CATEGORY TOKEN for the four leaf categories. The two differ only in
WHERE the caller found the node; the node's contents and everything done with them are identical, so
this is written once (2026-08-08c collapsed two near-identical loops that had already drifted in
their error text).

⚑ **THE DEST-KEYED TYPE SEAM (R-5/R-6).** The map KEY *is* the (unresolved) box destination and the
leaf is a 2-element `BindEntry(src, opts)` that carries no destination at all. The type is ruled in
HERE, at the seam that knows the shape, and the destination handed to `_emit_bind` is the map key —
never a value field. That is what makes "mount at the destination stored in the value"
UNREPRESENTABLE rather than merely guarded against (R-8). Present-`None` binds are omitted at build
(§3/§6e).

⚑ `name` is the DESTINATION for every category now. There is no entry name in the keyspace, so the
collision messages and the `binding_derivations.*` materialisation identify an entry by where it
lands — which is what R-10 means by "the destination IS the identity". ⚑ The DEST is the LAST key
segment and stays whole: it is data, and a dest such as `~/.cache/uv` carries dots of its own (see
`CategoryEntry`).

`_emit_bind` appends one bind-shaped `CategoryEntry` (MOUNT or COPY). ⚑ It takes PRIMITIVES, not a
bind object, and that is the point (P7 ruling). Its one caller has already ruled in the leaf TYPE at
the seam that knows the shape, so by the time anything gets here there is only ONE unpacked triple
and no second place a destination could come from. A leaf type check inside here would put two shapes
in one function (CONVENTIONS §0) and would leave "take the dest from the value" expressible.

*host_src* is used AS-IS. *box_dest_raw* is the UNRESOLVED destination and *box_dest_fn* resolves it
box-side. *opts* is the per-entry options override (`None` ⇒ the category default). *key_segments* is
the DISCRIMINATED declaration key the caller built from `decl_scope_fn`, plus the entry's DEST as the
last segment — carried on the entry for the collision messages and the `binding_derivations.*`
materialisation. *optional_keys* is matched on its DOTTED spelling.

⚑⚑ **EVERY DEST IS GUEST-SPELLED, COPIES INCLUDED** (spec §0 "ONE DEST SPACE, TWO DELIVERIES",
2026-08-08c) — so there is ONE resolution here and no space discriminator. A COPY's guest dest is
resolved to a host path later, when the copy runs: a `seeded` dest by
`container._guest_dest_to_host`, a `synced` dest by `commands.start._synced_host_dest` (through the
bind that covers it — cutover 2b-3). Neither resolution happens here. The retired `host_dest_keys`
parameter and `CategoryEntry.dest_space` field existed only because the seed layers used to spell
their dest as an absolute HOST path.

`_no_lookup` is the `expand_expr` lookup for the box-side `box_dest` pass: the snapshot's `@`-refs
are ALREADY resolved at build (host-side), so a surviving `@`-ref in a `box_dest` is a build/config
error and it raises rather than silently emitting `""`. Only `$XDG` / `~` are deferred.

### ⚑⚑ THREE STATES OF `opts`, NOT TWO

```
None                 -> UNSET: take the category default (``ro`` / ``Z,U``)
""                   -> EXPLICITLY NO OPTIONS, a declared value like any other
any other string     -> that value
```

🛑 `opts or _bind_options(category)` collapses the first two and is WRONG. The live case is the
`helper_sock` entry under `helpers:` in `core-defaults.yaml` (`bindings.rw`, `options: ""`): it is a
unix SOCKET the hub listens on, and a `Z`/`U` relabel/chown breaks the shared socket topology. The
truthiness spelling would hand it `Z,U` — the mount is still emitted, at the same arity, so nothing
fails and the socket quietly stops working. Pinned by `tests/test_settings/test_mount_options.py`.

⚑⚑ **THAT LINE FEEDS BOTH ROUTES — it is UPSTREAM of the collapse, never a peer of it.**
`store_shape.build_store_shape_set` reads `CategoryEntry.options`, i.e. what that line just produced,
so the category default is ALREADY CONCRETE by the time `store_collapse.fold_opt` folds the ARM token
onto it: an options-less `bindings.rw` entry reaches the main path as `Z,U,rw`, and `Z,U` cannot be
lost in the collapse (pinned by
`test_mount_options.py::test_the_category_default_reaches_the_COLLAPSED_route_intact`). The two
routes differ by that arm token ALONE — podman's own rw default — pinned by
`test_start_assembly.py::TestTheEmitterConsumesTheShape`.

🛑 **DO NOT read `fold_opt` as taking the STORED opts.** It takes the resolved value; the stored
`None` never reaches it. Reading the call in isolation manufactures a phantom regression in which an
options-less rw bind collapses to a bare `rw` and silently loses its relabel and chown.

## The small shared declarations

`_BIND_LEAF_CATEGORIES` is the set of bind-shaped category tokens that ARE the terminal key — the
snapshot's `<scope>.<category>` node IS the dest-keyed `BindMap`. `bindings` is the odd one out (its
map sits under an `ro` / `rw` ARM) and is handled by its own two-line branch at each site, never
folded in here: the difference is the DEPTH of the node, which is exactly what a shared set would
hide.

`_BIND_FLOOR_TAILS` / `_is_bind_floor_key` answer "does this floor key address a whole DEST-KEYED
bind map?" — true for `<scope>.bindings.{ro,rw}` and `<scope>.<one of the four>`. One tuple, so the
per-entry `""`-suppression in `build_launch_snapshot`'s floor fold cannot fall out of step with the
reader. The tail test is deliberate: a floor key is always scope-qualified, so the category can never
be the WHOLE key and a bare `common` cannot match.

`AgentGrammar` is the resolved launch-grammar pair read off the snapshot (B5, spec §2d):
`meta.agent.<a>.mode` as `mode_key → interactive argv fragment`, and `meta.agent.<a>.exec` as the
standalone one-shot fragment (`None` when the agent declares no `exec` operation).

`AuthTier` is the auth SHARING tier a box resolves to (design §3, precedence workset > global).

`_overlay_into` deep-overlays *top*'s leaves onto *base*, in place, per name (S3). Matching
`KeyStore` subtrees recurse, so a deep `top` leaf overlays the same deep `base` leaf without
clobbering a sibling `base` leaf; any other `top` leaf replaces `base`'s same key wholesale (the
active slot wins that name). It builds into a fresh tree and never aliases the snapshot.
