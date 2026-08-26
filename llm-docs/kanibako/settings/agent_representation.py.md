# Agent descriptor → KeyStore representation — the binds, the env, the commons

`agent_representation` represents a plugin's per-agent DESCRIPTOR delivery binds as agent-level
category-default entries in the resolved-keyspace
`~kanibako.settings.keystore.KeyStore`, so agent binary/launcher/share delivery flows through the
ONE category keyspace — the single-route invariant — and NOT a parallel descriptor mount route.

Each `~kanibako.targets.base.Binding` in a `~kanibako.targets.base.PluginDescriptor` becomes an
`agent.<name>.bindings.{ro,rw}` DEST-KEYED entry `box_dest -> BindEntry(src, opts)`, mirroring
`~kanibako.targets.assembly.resolve_binding_source`'s origin→host_src resolution. That is the §2d
`agent.<agent>.bindings.{ro,rw}` key form, which is TERMINAL (R-5: the arm is the WHOLE key and the
destinations inside it are NOT key segments), and never a bare `agent` token (§0).

Beside the bind representation live two smaller re-keying helpers for the same §2d read pick —
`agent_env_for_node` for a plugin's `default_envs()` table and `agent_common_for_node` for its
`default_common()` table — plus the two `harness_common_*` functions that write down where a
harness's agent-scope store dir lives.

Historically this module is **block 7a — PURE, ALONGSIDE**, and it is item-0's hard half. The
descriptor's OWN agent name `<name>` (`install.name`; S27) was the original root; see "Rooting"
below for why the active NODE name superseded it.

## PURE + ALONGSIDE — the block-7a boundaries

* **Imported, single-source resolution.** host_src is resolved by IMPORTING
  `~kanibako.targets.assembly.resolve_binding_source` — the origin→path logic is NOT
  re-implemented here.
* **No override application (S26).** `resolve_binding_source` is called with `override=""` so this
  emits the origin DEFAULT only. A user repoint now comes from a HIGHER cascade level (`box` /
  `workset` / `agent.<active>`), merged by block 2b — NOT baked into the agent default here.
* **No existence check (S26).** These functions NEVER touch the filesystem (no `Path.exists()`);
  the `AGENT_CRITICAL` must-exist safe-fail is a CONSUMER concern (the block-7b mount/reconcile
  step), not representation. Feed it a fixture install whose paths do not exist and it still
  represents them.
* **No expansion (§6a / spec §0).** `@`-refs / `$XDG` / `~` are left RAW — expansion is block 3.
  `box_dest` is carried VERBATIM: the descriptor loader
  (`kanibako.settings.agent_defaults._build_binding`) has ALREADY expanded the `$GUEST_HOME` box
  constant at load, so the `Binding` this module receives is post-expansion; re-expanding would be
  wrong. A LITERAL-origin raw `@`/`$XDG`/`~` `box_dest` therefore stays raw for free (§6a).
* **Build ALONGSIDE.** Nothing here was wired into the launch path when it was written; block 7b
  swaps `descriptor_mounts` onto this representation. `assembly.py` / `agent_defaults.py` /
  `start.py` were UNTOUCHED by 7a.

There is no mutation of the *descriptor* or the *install* either — the partial is built from copies
of what they report.

## Representation rules — the heart of the block (brief §3)

For each `Binding` in `descriptor.bindings`, order preserved:

* **host_src** = `resolve_binding_source(binding, install, override="")` — the origin-resolved
  `LAUNCHER` / `INSTALL_DIR` / `BINARY` / `LITERAL` path — stored as `str(host_src)`
  (`BindEntry.src` is `str`, S1) and, ⚑ R-11, NEVER canonicalized: a source is a HOST path and
  resolves on its own.
* **box_dest** = the MAP KEY, `binding.box_dest` normalized by
  `~kanibako.settings.settings_resolve.normalize_bind_dest` (R-11: a dest is a GUEST path, so `~`
  expands to the fixed guest home and `~` / `~/` are one destination). It is no longer part of the
  value.
* **opts** = `"ro"` if `binding.ro` else `None`. `None` (NOT `""`) means "no per-entry
  mount-options override" — `BindEntry.opts` defaults to `None` (S1) and reconcile falls back to
  the category default for an rw bind. This is the bind convention, distinct from
  `descriptor_mounts`'s `""`, which is an argv-mount detail and not the stored shape.
* **key path** = `agent.<node>.bindings.ro` or `agent.<node>.bindings.rw` per `binding.ro`, with
  `box_dest` as the entry key INSIDE that arm.

⚑ **`binding.key` is NOT used in the keyspace any more** (R-10 dropped the entry name from it). It
remains the descriptor's own stable identifier and what `critical` names (`targets/base.py`) — it
simply stopped being a settings key segment.

The agent NAME is IN the key path (the §2d `agent.<agent>.*` form, NOT a bare `agent` token, §0),
so the partial merges BY NAME with 2a's discriminated `agent.<active-name>.*` level and any
higher-scope `agent.<name>.*` override (S8 / block 2b), including a user-set
`agent.<node>.bindings.*` repoint on a scope file.

**Partial shape.** `agent.<name>.bindings` nests `ro` / `rw` sub-tables; a sub-table is present only
if at least one binding lands in it, `bindings` is present only if at least one binding resolved,
and the result is ALWAYS a `KeyStore` rooted at `agent.<name>` — an empty `agent.<name>` node when
no binding resolves — so it merges by NAME with the 2a agent levels (S8). The ro/rw sub-tables are
built locally and only the non-empty ones are attached, so the partial shape stays minimal: an
empty `ro` / `rw` / `bindings` node would be an absent-vs-present-empty distinction the merge need
not carry.

## The None-origin rule (S27, recorded here)

When `resolve_binding_source` returns `None` — an unresolvable origin, e.g. a `LITERAL` binding with
no `literal_src`, or a detection field the install left unset — the entry is **OMITTED**.

This mirrors the AGENT best-effort skip in `~kanibako.targets.assembly.descriptor_mounts` and keeps
the tier-2 typed accessor honest: `bindings` exposes `Mapping[str, BindEntry]` (NOT
`BindEntry | None`) ONLY because build omits absent/None binds (design §5/§6e). Emitting a
`None`-host bind would be a lie a consumer crashes on.

## Rooting: the ACTIVE node, never the harness (Block E fix 2a)

The read side (`_agent_pick_node`) walks `agent.default` ∪ `agent.<active_agent>`, where
`<active_agent>` is the resolved NODE-name — `navigator℘claude` for a persona. The descriptor's
`install.name` is the HARNESS (`"claude"`, hardcoded in claude's `detect()`).

So rooting the binds under `install.name` ORPHANS a persona's AGENT_CRITICAL delivery binds at
`agent.claude.*`: never read → the `claude` binary is never mounted → the container exits
immediately. The partial therefore roots under the ACTIVE node-name (*node_name*).

For a BARE agent, node == harness == `"claude"`, so the binds still land at `agent.claude.*` —
byte-identical. *node_name* falls back to `install.name` only when a caller omits it (legacy / test
convenience). The name is computed before the loop rather than after it only so the act-once refusal
below can name the agent it is talking about.

## Two descriptor bindings at ONE destination — why it raises

Bindings are act-once, so a second entry at the same destination in the same arm cannot be an
overlay. Under dest-keying the second would simply REPLACE the first, with nothing downstream able
to see the loss — so it is named at build time instead, as a `SettingsError` citing `binding.key`,
the destination and the arm. The descriptor is the plugin's, and the plugin author is who has to
fix it.

## `agent_default_bind_keys` used to live here and is GONE (R-9)

⚑ It emitted the same `agent.<node>.bindings.{ro,rw}.<key>` keys as `agent_default_partial`,
detect-free and with a placeholder host_src, as a context-light SET-TIME floor registry. Its only
purpose was to let `config set` repoint a descriptor bind's source without the must-exist gate
refusing it as "nowhere in the cascade". That CLI write route is retired (disk-store rework step 1,
an accepted loss tracked as DS-BL1), so the registry had no consumer left.

⚑ **NOTHING ABOUT LAUNCH CHANGED.** `agent_default_partial` is the LAUNCH representation and is
untouched; a user override authored by hand in `agents/<node>/agent.yaml` still beats it by
cascade merge. **Do not resurrect this function to "restore" a delivery path — it never was one.**

## `agent_env_for_node` — the env twin, and the key swap is the whole job

It re-keys a plugin's `default_envs()` table from the HARNESS to the NODE, and it exists for the
same reason `agent_common_for_node` does: the §2d read pick overlays `agent.default` ∪
`agent.<ACTIVE NODE>`, so a key a plugin declared against its own HARNESS name is invisible to a
PERSONA node (`navigator℘claude`) — which for env means a persona box launching without the
variables its harness requires.

There is NO re-root half here. An env value is a scalar the plugin chose, not a host source pointing
at the harness's own store, so the KEY swap is the whole job. A BARE agent (`node_name == harness`)
gets the identity back.

⚑ **The key is DATA:** only the leading `agent.<harness>.` PREFIX is replaced and the rest is
carried through untouched — never split into segments and rejoined.

## `agent_common_for_node` — re-key AND re-root

A plugin declares its agent-scope commons against its OWN name, the HARNESS
(`load_common(pkg, file, self.name)` → `agent.claude.common.plugins`), but the §2d read pick
overlays `agent.default` ∪ `agent.<ACTIVE NODE>`. For a PERSONA the active node is
`navigator℘claude`, so every harness-keyed common was invisible: a persona box mounted NEITHER
`~/.claude/plugins` NOR `~/.claude/cache`, and `ensure_persona_share_symlinks` maintained links
nothing consumed. This function is the P7 fix for that live bug — found while planning P3, and
deliberately deferred to here, where the agent-key semantics were open.

**A persona INHERITS its harness's commons.** That is the documented intent of the symlink shim,
which points `agents/<node>/common/<leaf>` at `agents/<harness>/common/<leaf>` and explicitly steps
aside when the persona has a real dir of its own. Both halves are re-keyed so that intent holds:

* the KEY `agent.<harness>.common.<leaf>` → `agent.<node>.common.<leaf>`, so the pick actually sees
  it;
* the SOURCE `@meta.agent.<harness>.path/common/<leaf>` → `@meta.agent.<node>.path/common/<leaf>`,
  so the bind resolves through the NODE path — the symlink (shared with the harness) by default, or
  the persona's OWN directory when it has one. Re-keying WITHOUT re-rooting would bind the harness
  dir directly and make the shim's own-dir branch unreachable.

⚑ **The re-root rule is deliberately NARROW:** only a source that is exactly the harness's
declaration root for this category is moved. An absolute / `~` / `$var` / unrelated `@`-ref source
is carried VERBATIM — those are self-resolving by the plugin's own choice (spec §2a) and are not
the plugin saying "my store dir".

A BARE agent (`node_name == harness`) gets the IDENTITY back — byte-identical to the plugin's
table, so nothing about a non-persona launch changes. A key that is not this harness's common is
left untouched.

### ⚑⚑ THE TABLE IS DEST-KEYED (2026-08-08c), AND THE KEY TEST MOVED WITH IT

`common` is a TERMINAL key, so the table holds ONE entry
`agent.<harness>.common -> {box_dest: (host_src[, opts])}` and the re-key is an EXACT match on that
key, not a PREFIX match on `agent.<harness>.common.`.

Getting that backwards is not a cosmetic slip: a trailing-dot prefix test can never match the
terminal key, so the whole function would silently no-op and a persona box would lose
`~/.claude/plugins` and `~/.claude/cache` again — the exact bug this function exists to fix.

The re-root then walks the map's VALUES; the destinations (its keys) are untouched, because a
persona and its harness deliver to the SAME in-box path.

## `harness_common_root` / `harness_common_leaf` — one rule, two consumers

`harness_common_root(node)` is the `@`-ref declaration root of *node*'s agent-scope `common` store,
delegating to `agent_category_root_ref`. `harness_common_leaf(host_src, harness)` is its inverse:
the store-dir LEAF that *host_src* names under *harness*'s `common` root —
`@meta.agent.claude.path/common/plugins` → `"plugins"` for harness `claude`; anything else →
`None`.

⚑ **THE LEAF RULE IS WRITTEN IN ONE PLACE**, and it has two consumers that would otherwise each
invent it: `agent_common_for_node`, which re-roots a persona's inherited source, and
`commands.start.ensure_persona_share_symlinks`, which needs the same dirname to lay the symlink
shim. Before 2026-08-08c both read it off the KEY (`agent.<a>.common.<leaf>`); dest-keying removed
the entry name, so the rooted `host_src` is the only remaining carrier.

⚑ **DELIBERATELY NARROW, and the narrowness IS the contract:** only a source that is EXACTLY the
harness's declaration root for this category yields a leaf. An absolute / `~` / `$var` / unrelated
`@`-ref source is the plugin saying "this specific path", not "my store dir" (spec §2a — such a
source is self-resolving by the plugin's own choice), so it has no store leaf and gets `None`. A
caller must treat `None` as "nothing to re-root / nothing to shim", never as a parse failure.

## Authority

Spec `settings-keyspace-1.8.0.md` §2d (`agent.<agent>.bindings.{ro,rw}.<key>` — the ONLY agent key
form; §0 forbids a bare `agent.<key>`) and §2a (binding REPRESENTATION);
`~/vault/rw/keystore-design.md` §2 (binds are structured) and §6a (raw refs). SEAMS
S1/S2/S3/S7/S8/S9 + S26/S27.
