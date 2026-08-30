# The Config Verb Engine — get / set / show / reset

Every noun command (`box` / `workset` / `agent` / `system`) runs its `config` verbs through this
one module, so a key behaves the same way whichever noun the user typed it at. It owns the
argument grammar, the dispatch order over the key FAMILIES, the write-time guards (scope
direction, retired routes, value shape), the set-time resolution probe, and the two display
modes. It decides nothing about WHAT a key is — the closed keyspace, the routing tables and the
refusal wordings live in `config_keys`, and the file-and-slot rule lives in `config_dest`.

Authority: spec `settings-keyspace-1.8.0.md` §0 (closed keyspace, directional enforcement, read
verbs), §2a (categories + value types), §2b (`meta.box.agent.*`), §2h (`pref.*` requests).

## The argument grammar

| Form | Action |
|------|--------|
| `key=value` | set |
| `key` | get |
| *(no args)* | show overrides at this level |
| `--effective` | show resolved values |
| `--null key` | SET an explicit present-`None` — the suppression request |
| `reset key` | remove the override; the verb that UNDOES `--null` |
| `reset --all` | remove all overrides (confirmed unless `--force`) |

⚑ `reset` is a sibling VERB (`box reset <key>`), not a `--reset` flag — no parser defines one.
`args.reset` is only the namespace attribute the command modules set before calling in here.

### Why `--null` is a FLAG and not a magic value token

`config set` stores scalars VERBATIM — nothing in it YAML- or literal-parses a value (only keys
declared `bool` in `KEY_TYPES` coerce) — so there is no existing rule under which the string
`"null"` would become `None`, and inventing one for this route alone would be a dialect:
`box.env.X=null` and `box.image=null` are legitimate strings. The flag says what is meant,
applies to EVERY key whose leaf accepts the §3 present-`None` terminal, and cannot collide with
data. It is the CLI spelling of §2h's suppression request
(`pref.agent.<agent>.<category>.<name>: null`), which is the ONLY channel a box has to drop
something its agent declares.

## The get model — stored-at-noun

Spec §2a "Read verbs" clause (folded 2026-07-02 — Jei clause 5, impl `3e0eb9e`): a plain
`get <key>` returns the value STORED AT THIS NOUN'S settings file (including a downward key it
stored), else `None` (rendered "(not set)"). It NEVER fabricates a built-in default and NEVER
returns another tier's value — that is the `--effective` cascade view (the `show` path), which is
unchanged. So a settings read reads the NOUN'S file (`settings_dest` = `system_settings_path` at
SYSTEM, else `project_toml`) — get reads exactly where `set` wrote (F5/F6 plus the F2/F3-class
downward-key sibling: all "get reads where set wrote").

## The retired routes, and why their READS survive

Three retirements shape almost every branch in this module:

* **R-39 + the RQ-1 re-ruling — the docker `.env` files.** The bare `env.<VAR>` spelling and the
  `.env` files it wrote are retired outright: no verb writes them, no launch reads them. The live
  family is `<scope>.env.<VAR>`. The vestigial `env_path` / `env_global` / `env_project`
  parameters survive only for call-site stability (see below).
* **R-9 — the two bind CLI write routes**, `{system,workset,box}.bindings.{ro,rw}.<name>` and
  `agent.<node>.bindings.{ro,rw}.<name>`.
* **DS-BL1 = (a) (Jei 2026-08-07g, *"accept the loss uniformly"*) — the direct category set/reset
  route** for `caches` / `seeded` / `common` / `synced` at every scope.

⚑ In all three cases the KEY is not retired — only the CLI WRITE route is. So the write verbs
refuse BY NAME in their preamble (spec §0: refuse loudly, never degrade to "unknown config key")
while `config get` still READS the key. The read survives on purpose: the keys are still
declared, still hand-authored in the settings YAML, still delivered at launch — and hand-editing
that YAML is precisely the cure the refusal prescribes. A get that answered "(not set)" for a
tuple the launch is actually using would make the cure unverifiable, and would be the F6 lie in a
new place. **Refuse the write, keep the read honest.**

⚑ Do NOT "restore" any of the deleted write branches to fix a refused repoint: the loss is the
RULING, not an oversight, and re-adding a write route would need a visible spec edit.

### The vestigial parameters

`set_config_value(env_path=…)`, `reset_config_value(env_path=…)`, `reset_all(env_path=…)`,
`get_config_value(env_global=…, env_project=…)` and `show_config(env_global=…, env_project=…)`
are ACCEPTED and feed nothing. They named the docker `.env` files. They are kept so call sites
(the three handlers and `test_config_dest_parity`'s bench) keep working unchanged; DELETING them
is a signature change across that bench and belongs to the KeyKind verb rewrite, not to a prose
pass. Nothing else may be threaded through them in the meantime.

`reset_all` in particular used to WIPE the level's `.env` file and count its lines as overrides;
clearing it now would neither change what the box gets nor be honestly countable as "overrides
reset". `<scope>.env.<VAR>` lives in the settings file and IS swept, as part of the nested scope
tables.

## Functions

```parse_config_arg(arg: str | None, *, set_null: bool = False) -> tuple[ConfigAction, str, str | None]```
Parse a single positional config argument into `(action, key, value)`.

* `"key=value"` → `(set, key, value)`
* `"key"` → `(get, key, "")`
* `None` → `(show, "", "")`

*set_null* is the `--null` flag: `config set --null <key>` is a SET whose value is Python `None`
— an explicit present-`None`, distinct from the terminal empty string `key=` and from the sibling
`reset` VERB (which REMOVES the override rather than writing one, and is therefore how a user
UNDOES a `--null`). See "Why `--null` is a FLAG" above.


```_pref_value_error(canonical, value, *, config_path, system_path, agent_path, workset_path, box_path, agent_name) -> str | None```
Validate a pref's VALUE against the shape + resolution of its TARGET key.

⚑ **THE VALUE IS VALIDATED AT THE TARGET PATH, NEVER AT THE `pref.*` PATH.** A pref's key
position says nothing about what value is legal there — the TARGET does. Two consequences, both
of which bit before this existed:

* **A structured target rejects a scalar.** `pref.agent.claude.common just-a-string` is
  accepted-then-fatal without this guard: it killed the LAUNCH with "category
  agent.claude.common is str, expected a Bind" — naming a key the user never wrote. ⚑ The
  per-name spelling that example used (`pref.agent.claude.common.x`) is refused by a filter
  EARLIER now — the four categories went TERMINAL on 2026-08-08c, so the target is not a key at
  all — and the DIRECT category set route it appealed to is gone with DS-BL1 = (a). Neither
  retirement weakens the rule: the bare terminal target still takes a structured value, and this
  is the only door left that checks it.
* **The E3 resolution probe must run at the TARGET.** Probing at `pref.<target>` is a NO-OP by
  construction: `expand` carries the `pref` subtree through unexpanded (spec §2h), so no `@`-ref
  in it is ever resolved and no defect can be recorded. `pref.agent.claude.template @typo` was
  therefore accepted and then silently DROPPED the target at launch. Applying the candidate at
  the target path is what makes the probe mean anything.

Returns an `Error: …` string when refused, else `None`. A `None` *value* (the `--null`
suppression request) is always shape-legal: present-`None` is the §3 terminal every category and
scalar leaf accepts, and it is §2h's ONLY suppression channel.

### The `pref.system.agent` non-check

⚑ DELIBERATE, DO NOT "FIX": the VALUE of `pref.system.agent` is NOT checked against the installed
agents. §2h validates the TARGET KEY, and its own agent rule is about the DISCRIMINATOR in
`agent.*.**` — *"the agent test is 'is it a VALID agent', NOT 'is it the ACTIVE agent' — so
pre-configuring an agent you may switch to is allowed"* (§2h). An unknown NAME here surfaces at
agent RESOLUTION (P7), with the error that subsystem already owns, rather than being pre-judged
by the config writer.

### The `access` guard, reached through the pref spelling

`is_access_key` answers False for `pref.agent.<node>.access` BY DESIGN — it matches the TARGET
key shapes, and a pref is not its target — so the generic set-time guard in `validate_config_set`
does not see it. It is checked HERE instead, at the TARGET, which is this function's whole rule:
a pref's value is legal iff it is legal at the key it requests.

Not cosmetic. `pref.agent.<agent>.access=<tier>` is the exact command the RQ-2 retired-key
refusal PRESCRIBES to box/workset users, i.e. the spelling they are most likely to type; without
this the write is accepted and the launch resolver is the only fence, so a typo is STORED and
then fails every future launch of that box instead of failing the write that caused it.

### The bind-shaped-target test: every term is load-bearing

⚑ SEVERAL terms, because "is this target bind-shaped?" is not the same question as "is this
target CLI-settable?" — and since DS-BL1 = (a) NOTHING bind-shaped is CLI-settable, so every
per-name term is a retired spelling. Their VALUE is still a structured entry, so a scalar written
at a bind-shaped target is still wrong and must still be refused HERE. Dropping any term would
open exactly the hole this guard exists to close, on the very keys that lost their direct route
(spec §2h: a pref's value is legal iff it is legal at its target).

* ⚑ `BIND_KEY_RE` **never matches any more** (2026-08-08c emptied the non-terminal complement and
  it compiles its fail-closed form). The term is KEPT, not deleted: it is the ONE place that asks
  "does a per-entry bind key exist at this scope", and it must keep asking through the regex
  rather than through a hardcoded `False`, so re-admitting one is an edit to the tuple and not to
  this guard. The per-entry AGENT-scope spelling it used to catch is now refused a step EARLIER,
  by `_pref_target_error` — the target is not a key at all.
* ⚑ `pref` is NOT a retired route — a box may still REQUEST a bind change — so the retired
  spellings must keep being recognised here even though the verbs refuse them.
* ⚑ Of the two node/scope retirement terms only the AGENT one is currently REACHABLE: the §2h
  allowlist refuses `pref.<file-scope>.…` several steps earlier, so `SCOPE_BIND_KEY_RE` here is
  belt-and-braces. Kept anyway — this is a value-shape rule about a target, and a rule that reads
  "which targets are bind-shaped" must not silently depend on which targets a different rule
  happens to admit today.
* ⚑ **The terminal-tail term** (`is_terminal_category_key`), added with the P4′ terminalization
  (R-5/R-10), now carries the whole weight: the BARE `<scope>.bindings.{ro,rw}` arm and, since
  2026-08-08c, the bare `<scope>.{caches,seeded,common,synced}`. Those are the ONLY bind-shaped
  targets a pref can name — none of the per-name terms match them (they all require a trailing
  `.<name>`), so without this a scalar at `pref.agent.claude.common` would be WRITTEN and the
  launch would refuse it later. `masks` has had this guard all along (`MASK_KEY_RE` matches the
  bare key); every dest-keyed category now has the same shape and gets it from ONE predicate
  rather than a second regex that could drift from the keyspace's own answer.
* ⚑ **It is the WHOLE-KEY predicate (QC)**: a pref TARGET is a canonical scope-rooted key, so the
  category must sit where the SCOPE ends. The older suffix test also claimed a scalar leaf ending
  in a category token, which would have refused a scalar value at a SCALAR key with a message
  telling the user it is a dest-keyed map.


```_yaml_skeleton(target: str) -> list[str]```
The nested-YAML skeleton for *target*, for a refusal message.

A user refused a CLI spelling needs the spelling that DOES work; printing the dotted key they
just typed would only repeat what failed.

⚑ **THE LEAF LINE DEPENDS ON THE CATEGORY**, and getting it wrong hands the user a shape that
will be refused again. EVERY bind-shaped category is a TERMINAL DEST-KEYED key now (`masks`,
`bindings.{ro,rw}` — R-5/R-10; `caches` / `seeded` / `common` / `synced` — 2026-08-08c), so the
leaf is always a MAP: the key ends AT the category and the destinations live inside its value.
The NAME-KEYED pair form `[<host_src>, <box_dest>]` this used to print for the four is GONE —
printing it would hand the user a shape the reader now refuses by name.

⚑ The test is the WHOLE-KEY predicate (QC): *target* is a canonical scope-rooted key, and a
scalar leaf that merely ends in a category token must not be handed a dest-keyed skeleton.


```_host_xdg_map(data_home: Path | None = None) -> dict[str, str]```
Module-PRIVATE deferred-import delegate to `kanibako.settings.paths.host_xdg_map`.

Exists so the ONE canonical XDG-map builder is reachable as a `config_interface` attribute
(patchable, single-source) WITHOUT a module-load import of `paths` (which would cycle:
`config_interface` ↔ `paths`). Underscored so it is NOT a second PUBLIC import surface for the
builder (Editor NIT): the one public builder stays `paths.host_xdg_map`; this is only the
deferred-import hook `_set_time_ctx` calls. There is no second hand-rolled XDG map (spec §1 XDG
clause + L2 §3).


```_set_time_ctx(config: dict[str, str] | None = None) -> Any```
The `settings_resolve.ResolveCtx` for the set-time E3 probe.

Populates the FULL XDG var set (so `$XDG_*` host-source tokens resolve) plus home; `$AGENT` /
`$WORKSET` are left unset here (a set-time check has no live launch agent/workset, and a category
`host_src` carrying `$AGENT`/`$WORKSET` is unusual — an unset one falls into the resolver's "not
set in this context" branch, which the lenient expand records as a defect, exactly as build would
for a host-side `$AGENT` with no agent). Box-side `$XDG`/`~` in a `box_dest` are NOT validated
here — they are DEFERRED (S17) and the probe only resolves the `host_src` half.

*config* is the Layer-1 `config.*` foundation (resolved bootstrap paths) so an `@config.*`
`host_src` ref routes to the foundation (JC-2), NOT the snapshot.

The `$XDG_*` map is built by the ONE canonical builder `paths.host_xdg_map` (spec §1 XDG clause +
L2 §3 single-source-of-truth: a hand-rolled per-context map is a bug), reached through the
module-private `_host_xdg_map` deferred-import hook so it stays a single source.


```_path_tier_split() -> tuple[dict[str, str], dict[str, object]]```
The path tier as `(config_foundation, floor)`, RAISING on failure.

The Layer-1 `config.*` foundation goes to the resolve context (so an `@config.*` `host_src`
routes there — JC-2) and the Layer-2 `system.*` paths become the cascade FLOOR (so an `@system.*`
`host_src` resolves from the snapshot).

⚑ **THE FAILURE ARM IS THE CALLER'S, DELIBERATELY.** Its two callers disagree about what a
resolution failure means and they are BOTH right: a `config set` must still work with an empty
floor, because refusing to write when the path tier is unreadable would make the tool useless for
repairing exactly that; while a post-reset "effective value" computed on an empty floor would
NAME A VALUE THAT IS NOT THE ONE the cascade resolves, and the honest answer there is to say
nothing. So this function raises and each caller catches what it means. Collapsing the two arms
into one would be a behavior change on one of them, and which one is correct is a spec question,
not a refactor's call.


```_category_set_lookups(config_path, *, canonical, system_path=None, agent_path=None, workset_path=None, box_path=None, agent_name="") -> tuple[Callable, Callable]```
The set-time lookups over ONE merged cascade snapshot: `(resolves, raw_bind)`.

Built for a `config set` at *config_path* (the COMMAND-scope file): the E3 RESOLUTION probe (Q9,
spec §2a) AND the raw-cascade `Bind` lookup (F10 — the must-exist-in-the-CASCADE check), both
over the SAME single merged snapshot (E3 single-snapshot; no second assembly). The FULL merged
cascade is built ONCE via the committed pipeline (`assemble_levels` → `merge` — single-source,
NOT re-implemented), then:

* `resolves(key, value)` applies the candidate RAW *value* (the new `host_src`) at *key* into a
  FRESH copy of the merged snapshot, lenient-`expand`s it (collect-not-raise), and returns the
  edited key's defect reason (BLOCK) or `None` (ALLOW) — the E3 test "does the edited value
  resolve cleanly post-edit?". Setting the leaf to the raw `host_src` STRING is sufficient for
  the E3 upstream-chain check: `_expand_str` resolves it host-side exactly as `_expand_bind`
  resolves the host half. The clone is S19 (never mutate the shared merged snapshot).
  ⚑ A `ReservedKeyError` from the candidate write (`…common.get`) is a set-time DEFECT, not a
  crash: it is a `KeyError`, so it escaped this closure and broke `set_config_value`'s "returns
  an error string, NEVER raises" contract (the H1 rule). It is reported as the defect it is.
* `raw_bind(key)` returns the key's effective RAW pre-expansion `kb_store.Bind` from the
  merged snapshot — the tuple the resolver would pick (merge precedence) — or `None` when no
  scope in the set-time cascade sets a bind there (absent / suppressed / not bind-shaped). It
  walks the pre-expansion store with UNBOUND `dict` ops (S3). NOTE: the set-time cascade covers
  every scope's settings FILE plus the resolved `system.*` floor; the runtime-gathered default
  binds (core/kani/channel/target tables, launch-only floor) are NOT in it.

### FULL CASCADE at set-time (Jei ruling 2026-06-29 — (b))

The visible keyspace is the SAME resolved cascade the launch would see (spec §2a "layer the
target's settings in precedence order"): every scope's settings file (*system_path* /
*agent_path* / *workset_path* / *box_path*) is layered in its TRUE precedence slot — EXACTLY as
`settings_launch.build_launch_snapshot` / `start._effective_behavior_for_display` assemble for
`config --effective` — plus the resolved `system.*` config tier folded as the `base` FLOOR (so
`@config.data` etc. resolve). So a cross-scope `@`-ref in the edited value (e.g. a `box set`
value referencing `@workset.vault_ro/x`) resolves at set-time exactly as it would at launch — no
longer a false-block.

The COMMAND-scope file (*config_path*) is placed into its OWN precedence slot by the edited key's
SCOPE token (`box.*` → box slot, `workset.*` → workset slot, `system.*` → system slot), NOT
always the box slot — so a sibling repoint still sees the file's own keys, and a higher-scope ref
sees the higher-scope file. The explicit `*_path` kwargs default to the command-scope file (so a
caller that passes ONLY *config_path* still gets the file in its true slot); a caller that plumbs
the full cascade (the three set handlers) passes every scope's file.

⚑ The AGENT arm is the one that is easy to get wrong. A per-node descriptor bind
(`agent.<node>.bindings.*`, item-0) sets the AGENT-scope file (`agents/<node>/agent.yaml`);
it goes in the agent slot so its own already-set tuple (read by `_agent_partial` at the
`agent.<agent_name>` sub-table) is the cascade winner — NOT the box slot, where
`_drop_upward_scopes` would DROP its agent-scope keys.

Resolution NEVER touches the stored file — it writes RAW (§0); the snapshot is in-memory and for
the CHECK only.

### The agent store-root anchor, and the second anchor that is gone

The `meta_agent_path_floor` fold is the agent STORE-ROOT anchor (spec §2d), from the SAME builder
the launch floor uses, so an `@meta.agent.<a>.path/…` value in the edited key resolves at set
time exactly as it would at launch. With no agent in play the key stays absent, so such a source
is correctly a DANGLING ref rather than a silently-empty one.

⚑ **THE SECOND ANCHOR IS GONE (DS-BL1 = (a)).** It read the agent out of the EDITED KEY
(`_agent_scope_node`) because an agent-scope CATEGORY set arrived here with no *agent_name*
threaded, and the bare-relative refusal's rooted-form hint had to resolve. No bind-shaped
category reaches a set any more — all six are refused by name in the verb preamble — so every key
that reaches this function is a SCALAR, for which that predicate answered `""` anyway.

### The set-time floor-registry fold that is gone

⚑ **THERE IS NO SET-TIME FLOOR-REGISTRY FOLD HERE ANY MORE, and its absence is deliberate.** A
`default_categories` registry (the CORE box mounts, and before them the per-node descriptor
binds) used to be folded into *floor* so a source-only repoint of a LAUNCH-ONLY bind would pass
the F10 must-exist-in-the-cascade gate. R-9 retired BOTH bind CLI write routes, which left the
fold unable to change any outcome: no `bindings.*` key of any scope reaches this function, and
the categories that still do (`caches`/`seeded`/`common`/`synced`) were never in that registry.
The whole thread — the parameter on five functions, both producers, and the three handler call
sites — was removed together rather than left inert.

⚑ Do NOT restore it to "fix" a refused bind repoint: that surface is a KNOWN, ACCEPTED LOSS of
R-9 (boarded as DS-BL1), and the cure the refusal prescribes is hand-editing the settings file.
The LAUNCH-time floor fold in `settings_launch.build_launch_snapshot` is a DIFFERENT, LIVE
mechanism and is untouched by this.


```_clone_keystore(store: Any) -> Any```
Deep-clone a `KeyStore` — nested nodes rebuilt, immutable leaves shared (S19).

Leaves are shared because they are immutable `Bind`s / scalars. Used so the candidate-edit +
lenient expand never mutate the shared base merged snapshot. Unbound `dict` ops (S3).


```_set_leaf(store: Any, parts: list, value: object) -> None```
Set *value* at the *parts* path in *store*, creating nested `KeyStore` nodes as needed.

Unbound `dict` ops (S3). Used to apply the candidate edit into the cloned snapshot before the E3
lenient-expand check.


### `_set_category_value` — GONE, and what went with it

⚑ `_set_category_value` IS GONE (DS-BL1 = (a), Jei 2026-08-07g — *"accept the loss uniformly"*).
It was the glue for the source-only RAW category repoint (S24/S25): validate the raw value, then
swap element 0 of the existing tuple in the command-scope file. Every bind-shaped category is now
YAML-only, so the set and reset branches it served are gone and the write verbs refuse all six BY
NAME in their preamble (spec §0 — refuse loudly, never degrade to "unknown key").

⚑ Its callee `settings_configset.repoint_host_src` was thereby left with no live caller and is
now DELETED TOO (QA′, 2026-08-08), along with R-8's three-element stale-shape refusal and
`validate_config_set`'s `is_category` arm. Do not reach for either name: see the banner on
`settings_configset`'s module docstring for what went and what a rebuild would owe.


## The verbs

```get_config_value(key, *, global_config_path, project_toml=None, env_global=None, env_project=None, system_settings_path=None, agents_root=None, command_scope=None, active_agent=None) -> str | None```
Read one config value STORED AT THIS NOUN, or `None` when it is not set there.

*active_agent* is the box's resolved agent NODE, needed ONLY to redirect a BARE agent behavior
key at box scope to its `pref.agent.<active>.<key>` request (P7 — see `box_agent_redirect_key`).
Absent/unknown ⇒ no redirect.

*system_settings_path*, when supplied (the SYSTEM scope), is the file used for SETTINGS reads
(`system.agent` + agent settings) — i.e. `@config.settings` = `global/settings.yaml`. When `None`
(box/workset scope) the existing `project_toml`/`global_config_path` paths are used, so those
scopes keep their own `box.yaml`/`workset.yaml` behavior. CONFIG (`system.*` layout) reads always use
`global_config_path`. For the semantics see "The get model — stored-at-noun" above; for
*env_global* / *env_project* see "The vestigial parameters".

⚑ `get` is the only verb carrying BOTH the noun's settings file and its config file as separate
parameters (`global_config_path` + `project_toml`) where the write verbs carry one `config_path`,
so the mapping onto the shared destination rule happens here, once. Collapsing the two parameters
is a signature change and belongs to the verb rewrite, not to a move.

### The dispatch, branch by branch

The order below is the order in the source, and several steps of it are load-bearing.

* **The box bare-agent redirect.** A BARE agent behavior key at BOX command scope has no readable
  value of its own: a box cannot write `agent.default.<key>` (it is dropped at launch — see
  `box_agent_redirect_key` + `set_config_value`). The read is REDIRECTED to the box's
  active-agent mirror `box.agent.<key>` so `get` reads exactly where a corrected
  `set box.agent.<key>` wrote, and the caller NAMES the value `box.agent.<key>` (teaching the
  canonical form). WORKSET has no mirror, so a workset bare-agent-key get is REFUSED at the
  command handler (`bare_agent_key_scope_error`, verb "read"), not here — this forgiving read
  only applies to box. Every other form / scope is unchanged.
* **`pref.<target-key>`** — return the REQUEST stored at this noun (spec §2h "config get
  pref.system.agent returns the REQUEST"; clause 5's plain get = stored-at-noun). The RESOLVED
  result is the `--effective` view.
* **Bare `env.*`** — RETIRED (R-39, spec §2a: the env family is scoped). This engine returns
  values, never error strings, so the refusal-with-cure lives at the three command handlers
  (`bare_env_retired_error`, verb "read") — the same handler-side split as the workset
  bare-agent-key read. `None` here keeps a direct library read honest: the bare spelling is not a
  key, and the docker `.env` FILE the old branch merged is not read by anything any more.
* **`<scope>.env.<VAR>`** — the LIVE env family: the stored value from the NOUN's settings file
  (stored-at-noun, exactly where set wrote). The SIBLING of the scope-secret read, threaded the
  same way — `noun_file` is the system settings file at SYSTEM, else the command's own
  `project_toml`.
* **`agent.<node>.bindings.{ro,rw}.<name>`** — the per-node DESCRIPTOR bind (item-0): the RAW
  tuple STORED in the node's OWN `agents/<node>/agent.yaml`. ⚑ Checked BEFORE the persona
  branch: a bind literally NAMED after a state leaf (`agent.<node>.bindings.ro.model`) would
  otherwise be mis-captured by the persona form (`model` is a state leaf). A plain get is
  stored-at-noun — the RESOLVED bind (descriptor floor + this override) is the `show --effective`
  cascade view. A missing `agents_root` (box/workset scope) / malformed node → `None`. ⚑ THE READ
  SURVIVED THE WRITE (R-9) — see "The retired routes" above.
* **`agent.<node>.secret_path.<VAR>`** — the per-node SECRET category (spec §2a): the stored PATH
  (never the secret VALUE) at the DISCRIMINATED slot in the node's OWN settings file — the
  get/set/reset symmetry twin. ⚑ Checked BEFORE the persona branch. Missing `agents_root` /
  malformed node → `None`.
* **`<scope>.secret_path.<VAR>`** — the stored PATH from the NOUN's settings file. ⚑ `noun_file`
  is the SAME per-noun selection set/reset use (`settings_dest`). It read `project_toml`
  unconditionally before, which the SYSTEM handler never threads — so a
  `system set system.secret_path.X` (written to the system settings file) read back "(not set)"
  forever while `reset` cleared it. Box/workset are unaffected: there `noun_file` IS
  `project_toml`.
* **`agent.<node>.<key>`** — the PER-PERSONA agent key (block B1): the value STORED at the flat
  slot in the agent's OWN settings file (symmetric with set/reset; the cascade view is
  `show --effective` / `agent show`). Missing `agents_root` / malformed node → `None`.
* **Bare agent settings** (`model`, `continue_mode`, `access`, `allow_helpers`) — the
  agent-agnostic `config` CLI reads/writes the reserved any-agent `agent.default` tier; per-agent
  overrides live under `agent.<name>` and are resolved by the launch-time effective-state
  cascade. For the SYSTEM scope these are SETTINGS in the system settings file, not the
  `kanibako_config.yaml` CONFIG file.
* **`box.agent.<key>`** — RETIRED (P7, spec §2b): there is no settable box-scoped agent mirror
  any more, so there is no stored value to read. Returning `None` (rather than reading a
  hand-written legacy leaf) is deliberate: reading it would report a value that no longer has ANY
  effect on the launch, which is worse than "(not set)". The set/reset verbs refuse with the
  cure; the effective value is readable at `meta.box.agent.<key>` via `--effective`.
* **Category keys, ALL READ-ONLY** — the DECLARED terminal keys (`<scope>.masks`,
  `<scope>.bindings.{ro,rw}`, `<scope>.{caches,seeded,common,synced}`, each holding a whole
  dest-keyed MAP since 2026-08-08c) plus the RETIRED per-name spellings, still claimed so their
  read lands somewhere explicable. ⚑ Checked BEFORE the `system.*` file-only branch because a
  SYSTEM-scope category key (`system.caches`) only LOOKS like a `system.*` config key —
  categories are gettable at every scope. See below for the missing write twin.
* **`config.*` / `system.*` path keys** — the raw set-value from the bootstrap config file's
  `[config]`/`[system]` tables (file-only tier; not a merged-config field). ⚑ `load_config`, not
  `load_merged_config`: `config_paths` is CONFIG-FILE-ONLY (project/workset files never
  contribute it), and the merged loader now runs the B6 box-scalar KEYSPACE resolve — pure cost
  here, and a malformed box settings file must not break a bootstrap-tier read (the doctrine
  boundary the B6 consumer map fences off).
* **Regular config keys** — routed via the SAME known-key table set/reset use (no
  get-validated/set-unguarded asymmetry). ⚑ The OLD path returned
  `getattr(load_merged_config(...), flat)` — the merged dataclass, which fabricates the built-in
  DEFAULT when the noun stored nothing (the F6 lie: `box get box.image` printing the default
  image) and folds in the GLOBAL config file (returning another tier's value). Under the get
  model a plain get reads ONLY the file `set` wrote to, at the routed `(sections, leaf)` slot —
  and through the SAME rule site set/reset write through, so "get reads where set wrote" is
  structural rather than a claim two copies had to keep agreeing on. An unknown key (no family
  claims it) reads `None`, exactly as the routing-table miss did before.

### The category branch has no write twin

⚑⚑ **THERE IS NO WRITE TWIN LEFT TO BE SYMMETRIC WITH, and that is the whole shape of this branch
now.** DS-BL1 = (a) retired the CLI write route for every bind-shaped category at every scope
(R-9 took the `bindings` arms first), so the set/reset branches this used to mirror are GONE and
the verbs refuse those keys BY NAME in their preamble. The READ SURVIVES ON PURPOSE — see "The
retired routes" above.

⚑ The old get/set-symmetry note here (a SYSTEM-scope set once wrote the `kanibako_config.yaml`
CONFIG file this branch never read; an AGENT-scope category set was a SILENT NO-OP WRITE into a
file in no cascade level) is RETIRED WITH THE WRITES, not fixed — there is no longer a write to
disagree with.

⚑ The agent-scope read still routes through `_read_dest` (the `self:` table of
`agents/<node>/agent.yaml` is what the agent tier actually reads, and re-pointing this read at
it is a STORAGE-SHAPE change, deliberately NOT part of the route retirement). The per-node BIND
form is routed EARLIER (`_is_agent_node_bind_key`, the node file). Going through the same rule
site the write side uses is what makes `_read_dest`'s one documented divergence from
`_write_dest` (this family, at agent scope) a fact about running code rather than a claim in a
docstring nothing exercised.


```set_config_value(key, value, *, config_path, env_path=None, is_system=False, system_settings_path=None, cascade_system_path=None, cascade_agent_path=None, cascade_workset_path=None, cascade_box_path=None, cascade_agent_name="", command_scope=None, agents_root=None) -> str```
Write a config value to the appropriate store; returns a message or an error, NEVER raises.

*config_path* is the `box.yaml`/`workset.yaml` (for box/workset) or `kanibako_config.yaml` (for system).
*system_settings_path*, when supplied (the SYSTEM scope), is the file SETTINGS (`system.agent` +
agent settings) are written to — `@config.settings` = `global/settings.yaml` — keeping them out
of the `kanibako_config.yaml` CONFIG file. When `None` (box/workset) writes go to `config_path`
as before. Returns a human-readable confirmation message.

The `cascade_*` kwargs supply the FULL launch cascade (every scope's settings file + the active
agent name) for the set-time E3 resolution probe (Jei (b), 2026-06-29): the three set handlers
(`box/_parser.py` / `workset_cmd.py` / `system_cmd.py`) already hold this context and thread it
here so a cross-scope `@`-ref resolves at set-time exactly as it would at launch. They are
additive; absent, the command-scope file is still placed in its true slot. (The older wording
said they are "only consulted on the category path" — that has not been true since DS-BL1 = (a)
retired the category set route; `_probes_at_set_time` decides which keys reach the probe now.)

*command_scope* is the scope the `config set` was issued at (block B4). It drives the §0
directional-write guard (`_scope_direction_error`): a write is permitted for a key of the command
scope's OWN namespace or of any scope it CONTAINS (`system ⊃ agent ⊃ workset ⊃ box` — a downward
write lands in the command scope's file as an overridable default); an UPWARD write (and any
`meta.*` write) is REFUSED. When `None` the guard is skipped.

⚑ There is NO `default_categories` set-time FLOOR registry parameter any more — see the absence
note under `_category_set_lookups`.

### The preamble — the guards that run before any dispatch branch

The order is deliberate at every step.

1. **`config.*` foundation keys are NEVER CLI-settable (block B2).** They locate the files
   everything else lands in, so they cannot live in those files — they live in the bootstrap
   config file, hand-edited by a human/admin. Refused EXPLICITLY here, BEFORE the scope guard, so
   every command scope gets the same ruled message (not the cross-scope guard message, and not
   the DELETED generic `system_key_refusal`, which mentioned `setup` — spec §2a forbids naming it
   in THIS message). The READ/show path still consults `is_config_file_only_key`'s `config.`
   branch — only set/reset short-circuit here.
2. **The `pref.*` WRITE-SITE guard (spec §2h)** — BEFORE the three TARGET filters and before the
   scope guard. A pref is legal only in a workset or box settings file, and that restriction is
   what BOUNDS the resolution recursion, so it is a hard rule rather than a convenience. Checked
   ahead of the target filters deliberately: a user at the system scope must be told the FILE is
   wrong regardless of the target's quality, or they fix the target and only then discover the
   write site was never legal.
3. **The scope-direction guard (block B4, spec §0 + §2a)** — enforced at the TOP, after canonical
   key resolution and BEFORE any dispatch branch (env / category / system / regular), so EVERY
   write path is gated uniformly.
4. **The bare agent behavior key at BOX or WORKSET scope** targets the any-agent `agent.default`
   tier — an UPWARD write (agent contains both box and workset) that
   `settings_assemble._drop_upward_scopes` DROPS at launch (a silent no-op the old CLI reported
   as "Set"). Refused HERE, BEFORE the write: box teaches the `box.agent.<key>` mirror; workset
   refuses (no mirror — a workset spans many agents). Uniform over the whole `_is_agent_setting`
   family (NOT a per-key list). Legitimate forms untouched: `box.agent.<key>` is
   `_is_box_agent_key` (a SAME-scope box write); `agent.<name>.<key>` is `_is_persona_agent_key`;
   a bare key at SYSTEM scope is a DOWNWARD write.
5. **Bare `env.*` — RETIRED (R-39, spec §2a).** The env family is scoped (`<scope>.env.<VAR>`);
   the bare spelling wrote the docker `.env` FILE — an undiscriminated variant that silently
   meant something different from the discriminated key (Code Convention 0). Refused with the
   cure BEFORE any write machinery (`--null` included). The cure is REACHABLE: the scoped arm it
   names is routed a few branches below.
6. **`<scope>.env.<VAR>` with a RESERVED VAR name (spec §0** — a public `dict` method name or a
   dunder). Refused at write time as §0 requires; the shape test deliberately still MATCHES the
   key so the message can name the rule instead of degrading to "unknown config key".
7. **The two RETIRED bind CLI write routes (R-9, disk-store rework step 1)** —
   `{system,workset,box}.bindings.{ro,rw}.<name>` and `agent.<node>.bindings.{ro,rw}.<name>`.
   Refused with the cure BEFORE any write machinery, `--null` and the E3 probe included, for the
   same reason the bare `env.<VAR>` spelling is: a retired spelling must be REFUSED BY NAME,
   never degraded to "unknown config key" (spec §0) and never quietly accepted. The keys
   themselves are NOT retired — only these routes — so each message points at the settings file
   that actually holds the tuple, and `config get` still reads both.
8. **A BARE RELATIVE value for a PATH key ([R147])** — the LAST guard, and the only VALUE rule in
   a preamble otherwise made of NAME rules. That is why it is last: a retired or wrong-scope
   spelling gets its own specific message rather than this generic value complaint. It runs
   BEFORE the E3 probe, which splices the value into a candidate store — an illegal value is
   refused, not resolved. `_bare_relative_path_error` shares `is_unambiguous_path_value` and
   `ambiguous_path_value_error` with the two READ-time seams (`paths._refuse_bare_relative`,
   `workset_dirkeys.resolve_workset_dir_key`), so the rule has one wording at both ends. ⚑ The
   `config.*` tier never reaches it — guard 1 short-circuits — and that is correct: those six are
   `set: file` with no CLI write route, so read time is their only enforcement. ⚑ The `pref.*`
   spelling does not reach it either; it is checked AT THE TARGET in `_pref_value_error`, beside
   the `access` and `transform_settings` guards, for the same reason those live there.

### The `@meta.{workset,box}.path` anchors, and why the refusal needed them

`_meta_scope_anchor_floor` is folded into the set-time snapshot beside `meta_agent_path_floor`,
one scope out, and for the identical reason: a value spelled against a declared root DANGLES at
set time unless the root is floored. It went in with [R147]'s set-time half because the refusal
OFFERS `@meta.workset.path/<value>` as the cure — `MIGRATION.md` § *A bare relative path in a
settings key is refused* names it as the first row of the replacement table — and the E3 probe
was answering "dangling @-reference" to it. A rule that bans a form and then refuses its own
replacement has not removed the guess, it has removed the key.

⚑ **It is not a second derivation of either root.** Both tiers' settings files are DECLARED as
`<that root>/<filename>` (`paths.workset_settings_path`, `paths._box_settings_files`), so a
threaded tier file NAMES its root by its parent. A tier the command did not thread yields
nothing — `system set box.canon=x` names no box — and the refusal message then falls back to the
ref SPELLING, dropping its `, spelled '…'` clause so the line does not say the same thing twice.

### `--null` route coverage

⚑ **THE CATEGORY EXCEPTION IS GONE, AND IT IS THE REFUSAL ABOVE THAT ATE IT.** The RULE is
uniform (`--null <key>` writes an explicit present-`None` at that key) and the ONE mechanism that
could not express it was the source-only category REPOINT: it rewrote the host half of an
EXISTING tuple and had no null form, so `--null <scope>.<category>.<name>` was refused here with
a cure. DS-BL1 = (a) retired that whole route, so every bind-shaped category — with or without
`--null` — is now refused BY NAME in the preamble, several steps earlier and with a better
message. A guard here would be a second spelling of that refusal, reachable only if the preamble
missed a spelling; the preamble is the place to fix that, not here.

⚑ Direct category SUPPRESSION is still its own unbuilt feature (write `null` at the key in the
settings file, or request it with `--null pref.<key>`, spec §2h) — unchanged by this, and still
not half-implemented here.

The docker `env.<VAR>` arm that also refused `--null` ("the env file is a plain string store with
no null value") is GONE with the spelling itself. The LIVE `<scope>.env.<VAR>` is a nested YAML
scalar and carries `None` natively, so it needs no exception. Everything else lands through a
nested YAML write, which carries `None` natively — so `pref.*`, `box.agent.*` and the routed
scalars all work.

### The `access` write-time guard

Write-time validation for the auth-critical `access` permission key (Editor finding B; R-41
respelled the key and the guard followed it). It routes VERBATIM below (bare →
`_is_agent_setting`; per-node → `_is_persona_agent_key`), so a typo (`config set access=fll`)
would otherwise be STORED and then re-read at launch. An off-enum value is rejected NOW, with the
SAME message and the SAME truth table the launch resolver uses
(`settings_keyspace.ACCESS_TIERS`). ONLY `access` is guarded (Jei: only the auth-critical key),
not `allow_helpers` / `model`.

### The set-time resolution probe

SET-TIME RESOLUTION PROBE for a value the EXPANDER will see (E3, spec §2a / Q9). See
`_probes_at_set_time` for exactly which keys qualify and why the test is "does this value reach
`expand`" rather than "is it a scalar".

The probe was wired ONLY at the category path, so a set accepted a value whose `@`-ref or `$VAR`
does not resolve — e.g. `config set workset.boxes "@meta.nope.key/boxes"`. For an expanded value
that is not inert: an embedded dangling ref is substituted with the EMPTY STRING at launch (§6b)
and the key silently resolves to something else.

The probe blocks ONLY on the edited value's own transitive upstream chain, so an UNRELATED
pre-existing defect still allows the set and `config set` stays usable to REPAIR a broken config.
`reset` is untouched: removing an override cannot introduce a dangling ref in the removed value.

### The write dispatch

* **`pref.<target-key>`** — the §2h REQUEST. Validated with the SAME three filters the launch
  applies (so a stored request cannot fail every future launch), then written to the COMMAND
  scope's settings file at the NESTED `pref.<target…>` slot — the shape `assemble_levels` mirrors
  and `collect_prefs` reads. ⚑ NESTED, never a dotted literal: a bind-shaped value spelled the
  dotted way would never be bind-parsed, so the two spellings would behave differently (see
  `settings_prefs`).
* **`agent.<node>.secret_path.<VAR>`** — the per-node SECRET category (spec §2a). A SCALAR path
  write to the node's OWN settings file at the DISCRIMINATED `agent.<node>.secret_path`
  sub-table (the shape `_agent_partial` reads into the cascade + `agent_file.load` reads back).
  ⚑ Checked BEFORE the persona branch (`env_file` was there in rc; `secret_path` is discriminated
  node storage, a clean break). The §0 directional guard already ran: `agent.*` is settable only
  DOWNWARD from system, so box/workset was refused above; SYSTEM threads `agents_root`.
* **`<scope>.secret_path.<VAR>`** — the SECRET category at a NON-agent scope: a SCALAR path write
  to the command scope's SETTINGS file at the nested slot (the shape `_file_partial` reads into
  the cascade). The §0 directional guard already permitted it (own/contained scope).
  `settings_dest` = the command scope's settings file (`config_path` at box/workset; the system
  settings file at SYSTEM — never the Layer-1 config file).
* **`<scope>.env.<VAR>`** — the ENV category at a NON-agent scope: a SCALAR write to the command
  scope's SETTINGS file at the nested slot (the shape `_file_partial` reads into the cascade and
  `settings_launch._emit_scope_node` delivers as a `category="env"` entry). Spec §2a declares the
  key (L383) and puts it under "Scalars → full CLI set" (L496); the AGENT form
  `agent.<node>.env.<VAR>` is DISCRIMINATED and routed by the persona branch. The value is
  written VERBATIM — the set-time E3 probe already ran on it (`_probes_at_set_time`: this arm IS
  host-expanded at launch, so a dangling `@`-ref must be caught now).
* **`agent.<node>.<key>`** — the PER-PERSONA agent key (block B1): a write to the agent's OWN
  `agents/<node>/agent.yaml` (NOT the command scope's settings file), at the FLAT slot
  `agent_file.load` reads back (state leaf under `agent:`; `env.<VAR>` under `env:`). The
  SECRET pointer `secret_path.<VAR>` is handled EARLIER (discriminated node storage), not here.
  The node was `℘`-canonicalized by `resolve_key`. Sparse by construction: `write_nested_key` is
  read-modify-write, so only the key the user set is materialised — a default-only persona file
  stays empty of everything else. The value is written VERBATIM (like every other agent-setting
  write) — the persona-critical trio (`endpoint`, `secret_path.ANTHROPIC_AUTH_TOKEN`, `model`)
  are strings. `agents_root` is supplied only by the system scope (the global `config.agents`
  store); absent it, the write is refused (the directional guard already refuses this key from
  box/workset — an UPWARD agent-scope write).
* **Bare agent settings** — the agent-agnostic CLI writes the any-agent `agent.default` tier
  (per-agent overrides live under `agent.<name>`). SYSTEM scope routes to the system settings
  file (`settings_dest`).
* **`box.agent.<key>` — RETIRED (P7, spec §2b).** There is NO settable box-scoped mirror of the
  agent's settings any more: §2b replaced it with the RO read-back `meta.box.agent.<key>`, and a
  box tweaks its agent through the §2h request `pref.agent.<active>.<key>`, which targets the
  agent tier properly instead of smuggling a box-scope key into it. So this branch REFUSES and
  names the cure; nothing is written. ⚑ It is checked BEFORE the path-category branch so the
  refusal claims the WHOLE retired spelling — every `box.agent.*` tail, not just its scalar half.
  ⚑ An older note here claimed `box.agent.bindings.ro.X` "matches the category regex too". It
  does not, and did not: `BIND_KEY_RE` reads the segment after the scope as the CATEGORY, and
  `agent` is not one. The ordering is still right — it is just belt-and-braces, not a live
  collision.
* **STRUCTURAL `system.*` path-tier keys (the `SYSTEM_PATH_DEFAULTS` family) — FILE-ONLY.** They
  live in `kanibako_config.yaml`'s `[system]` table (the file `resolve_system_paths` reads),
  editable there or via `kanibako setup` (`write_system_value` bypasses this guard). The refusal
  names THAT file. ⚑ This is a precise family check (F2): a `system.*` SETTINGS key (auth chain /
  `system.agent` / categories / env) was routed above or falls through to the routing table below
  — it is never refused here.
* **Regular config keys** — routed via the single known-key table (the H1 fix: an unknown key
  returns an error string and NEVER raises). ⚑ The canonical dotted spelling and ONLY it — the flat
  underscore form used to be normalised in here and is now refused by name (see the deleted
  `config_keys._route_key`); `_coerce_value` is the H2 fix (real `bool` etc.) and only returns
  a `str` for a typed key when coercion failed. The confirmation echoes the CANONICAL key, so a
  successful `set` cannot advertise a form `get` refuses. A scope-prefixed SETTINGS key (`{agent,workset,
  box}.*` — including a DOWNWARD write at a containing command scope, spec §0) lands in the
  COMMAND scope's SETTINGS file with the key's scope token KEPT (the nested form
  `assemble_levels` mirrors — never remapped to the key-scope's own file). `settings_dest` ==
  `config_path` at box/workset; at SYSTEM it is the system settings file (`@config.settings`) —
  settings keys never land in the Layer-1 `kanibako_config.yaml` (spec §1). Non-scope keys
  (`allow_helpers`) and `system.*` regular keys keep their historical `config_path` slot.

### The category SET branch that is gone

⚑ **THERE IS NO CATEGORY SET BRANCH ANY MORE (DS-BL1 = (a), Jei 2026-08-07g — *"accept the loss
uniformly"*), and its absence is DELIBERATE.** It ran the source-only RAW repoint (S24/S25, spec
§2a / design §6d) for `caches` / `seeded` / `common` / `synced` at every scope: validate the raw
value, then swap ONLY `host_src` in the existing tuple at the command-scope file. Every
bind-shaped category is now YAML-only, so all six are REFUSED BY NAME in the preamble
(`scope_bind_retired_error` at the file scopes, `agent_node_bind_retired_error` at the agent
scope) and none reaches the dispatch. `config get` still READS them — refuse the write, keep the
read honest.

⚑ Do NOT "restore" this branch for the four: the loss is the ruling, not an oversight, and
re-adding a write route would need a visible spec edit.

⚑ It also carried the ONE known-broken destination arm in the tree (an agent-scope category set
landed in the command's own config file, which is in no cascade level — a SILENT NO-OP WRITE).
Retiring this branch made that arm unreachable and QA′ then DELETED it, so
`config_dest._write_dest` and `_read_dest` now answer identically for every key. The `_CATEGORY`
file rule itself SURVIVES in `config_dest` as the key's declared FAMILY — it is still answered
for agent-scope terminal keys on the READ side, which is a separate, still-open defect documented
on `config_dest._read_dest`.

⚑ There is also NO `agent.<node>.bindings.{ro,rw}.<name>` branch. It was a SOURCE-ONLY repoint of
the descriptor delivery bind, routed to the category path against a detect-free descriptor floor;
R-9 retired the route and it is refused BY NAME in the preamble. Its absence is deliberate, not
an oversight to "restore" — and the ordering note that used to live there (checked before
`_is_persona_agent_key` so a bind NAMED `model` is not captured as the persona state leaf) now
belongs to the preamble refusal, which runs before every branch and so cannot be out-ordered.


```reset_config_value(key, *, config_path, env_path=None, system_settings_path=None, command_scope=None, cascade_system_path=None, cascade_agent_path=None, cascade_workset_path=None, cascade_box_path=None, cascade_agent_name="", agents_root=None) -> str```
Remove an override for a single key; returns a confirmation or an error, NEVER raises.

*system_settings_path*, when supplied (SYSTEM scope), is where SETTINGS (`system.agent` + agent
settings) are removed from (`@config.settings` = `global/settings.yaml`); when `None`
(box/workset) they are removed from `config_path` as before.

*command_scope* is the scope the `config reset` was issued at (block B2, RESET-GUARD). It drives
the §0 directional-write guard (`_scope_direction_error`) symmetrically with `set_config_value`:
a reset is permitted for a key of the command scope's OWN namespace or of any scope it CONTAINS
(containment order, spec §0); an UPWARD reset (and any `meta.*` reset) is REFUSED. When `None`
the guard is skipped.

The `cascade_*` kwargs supply the FULL launch cascade (every scope's settings file + the active
agent name) — the SAME context `set_config_value` receives — so the honest cleared-message can
append the now-effective value + its source tier AFTER the removal (residuals item 1, F7 "where
cheap"). They are additive and consulted ONLY for that message; a caller that omits them still
gets the correct cleared-only form.

⚑ There is NO `default_categories` FLOOR registry parameter any more. It was consulted so the
honest cleared-message could name a reverted-to FLOOR bind value; R-9 retired both bind reset
routes, so no reachable branch could find an entry in it, and the whole thread — including
`config_keys._floor_bind_display` — was removed.

### The reset preamble mirrors the set preamble, refusal for refusal

**A reset is a WRITE.** That single fact explains every guard here, and each one is symmetric
with its `set_config_value` twin.

* **`config.*`** — NEVER CLI-resettable (block B2), same rationale as set (they locate the files
  everything else lands in; hand-edited in the bootstrap config file). Refused FIRST, BEFORE the
  scope guard, with the ruled message (verb "changed" — a reset is a change, not a "set"),
  pointing at the SAME config file.
* **`pref.*` write site** — symmetric with set.
* **Scope direction (block B2 RESET-GUARD)** — after the `config.*` forbid and BEFORE any
  dispatch branch, so every reset path is gated uniformly.
* **The bare agent behavior key at BOX or WORKSET scope.** Without this, a bare `reset <key>`
  fell to the `_is_agent_setting` branch and removed `agent.default.<key>` from the command file
  — which the box/workset never wrote (it is DROPPED at launch), so it reported "No override"
  while the real value (at `box.agent.<key>` for a box) stayed STUCK. Refused BEFORE the removal
  path: box teaches the `reset box.agent.<key>` mirror; workset refuses (no mirror). Uniform over
  the whole `_is_agent_setting` family; SYSTEM-scope bare resets and the `box.agent.<key>` /
  per-agent forms are UNAFFECTED.
* **Bare `env.*` (R-39)** — "No override" would be a lie: the `.env` file is not an override
  store any more, and nothing reads it.
* **A RESERVED VAR in `<scope>.env.<VAR>`** — so a name that can never be written is never
  reported as merely unset.
* **The two RETIRED bind routes (R-9)** — "No override for …" would be a lie in BOTH directions:
  it implies the spelling could have been written from the CLI, and a hand-authored tuple at that
  key may well exist in the settings file, untouched.

### The reset dispatch

Each branch clears exactly where its `set` twin wrote. `pref.<target-key>`;
`agent.<node>.secret_path.<VAR>` (⚑ BEFORE the persona branch; a missing `agents_root` /
malformed node → refused, only resettable at the system scope); `<scope>.secret_path.<VAR>`;
`<scope>.env.<VAR>`; `agent.<node>.<key>` (`remove_nested_key` prunes now-empty `agent:`/`env:`
tables, keeping the file sparse); the bare agent settings (`agent.default`, SYSTEM routing to the
system settings file); `box.agent.<key>` — RETIRED (P7, spec §2b), refused with the cure rather
than silently clearing a key that no longer does anything, and with the SAME named agent the set
path uses so the two verbs prescribe the identical spelling; STRUCTURAL `system.*` path-tier keys
— FILE-ONLY, refused for symmetry (edit the config file directly or re-run `kanibako setup`);
and the regular keys, routed through the same known-key table as set/get (no
get-validated/set-unguarded asymmetry). The regular arm is symmetric with `set_config_value` BY
CONSTRUCTION: the same rule site picks the file, so a scope-prefixed SETTINGS key is removed from
exactly the file the set wrote it to.

⚑ There is NO `agent.<node>.bindings.{ro,rw}.<name>` branch here any more — it removed the
source-only repoint from the node's own settings file, and R-9 retired that route. It is refused
BY NAME in the preamble, symmetrically with set. Deliberate absence, not an oversight: a reset
that reported "No override" for a hand-authored bind sitting in the node file would be the same
double lie the preamble refusal exists to avoid.

⚑ **THERE IS NO CATEGORY RESET BRANCH ANY MORE (DS-BL1 = (a))**, and it is gone for the same
reason as its SET twin — symmetrically, which is the point. A reset is a WRITE: it removed the
command-scope override tuple so the cascade's own tuple resurfaced. With the write route retired,
every bind-shaped category is REFUSED BY NAME in the preamble, and that refusal is the honest
answer in both directions — "No override for …" would imply the spelling could have been written
from the CLI, while a hand-authored tuple at that key may well sit in the settings file,
untouched. (Exactly the double lie the `bindings` preamble refusal already existed to avoid.)

### The post-reset "effective" gate

The now-effective value + source tier is computed from the POST-RESET cascade (residuals item 1)
— the file is already written, so the assembled snapshot reflects the removal. It threads the
SAME cascade files/agent the three handlers hold; `None` (no inputs / unresolved) keeps the
cleared-only form.

⚑ **GATE (Editor F1): ONLY a scope-prefixed SETTINGS key (`{system,agent,workset,box}.*`)
actually READS through the assemble/merge cascade** — so only for those is the assembled snapshot
the key's real read path. A SCOPELESS key (`vault.*`, `allow_helpers`,
`model`/`continue_mode`/`access`) is read from a single settings file / the flat `KanibakoConfig`
(NOT the cascade), so a cascade-derived "effective" would name a value from a tier NOTHING reads
— a wrong claim. Those keep the cleared-only form. This is the SAME token test that picks `dest`
(the write path and the read path agree).

⚑ The token test stays at the call site rather than moving to the rule site: it asks whether the
key READS through the cascade, not where it is STORED. Those are different questions that happen
to share a test.


```_reset_dest(canonical, command_scope, config_path, system_settings_path) -> DestRoute```
`reset`'s destination — the SAME route `set` wrote through.

A thin adapter over `config_dest._write_dest` so each reset branch reads as one line. It asserts
the route exists because every branch that calls it has already established its family, and a
family with no slot would mean the dispatch and the rule site disagree — which is a bug to
surface, not to route around.


```_honest_reset_message(key, command_scope, effective=None) -> str```
The HONEST `reset` confirmation (F7, Jei-ruled 2026-07-02d).

The behavior is right — clearing a scope override lets the value fall back through the cascade —
but the OLD message lied: it printed "reverts to default: `<built-in>`" even when the fallback
lands on a HIGHER-TIER stored default (a workset/system value), not the built-in. The ruling: say
we CLEARED the value set on THIS noun (named from the COMMAND scope, not hardcoded "box"), and —
"where cheap" — show the now-effective value + its source tier.

*effective*, when supplied (residuals item 1 — the caller threads the same resolved cascade
`set_config_value` receives, so it IS cheap now), is the `(value, tier)` the POST-RESET cascade
resolves for this key, computed by the SAME assemble/merge/expand path the launch uses (no
bespoke re-derivation, no built-in guess). When `None` — no cascade inputs supplied, OR the key
does not resolve cleanly post-reset — the cleared-only form is kept (evidence honesty: omit
rather than guess a wrong value, the exact lie being fixed).


```_effective_after_reset(routed, sections, leaf, *, agent_name, system_path, agent_path, workset_path, box_path) -> tuple[str, str] | None```
The now-effective `(value, source_tier)` for *routed* AFTER a reset, else `None`.

Reuses the SAME committed pipeline the launch + set-time probe use (`assemble_levels` → `merge` →
lenient `expand`, single-source — NOT a re-implementation), so the tier is the one the cascade
ACTUALLY resolves. The reset already wrote the file, so the assembled snapshot is the POST-RESET
state (the Editor's condition: build AFTER removal, not stale).

Returns `None` — so the caller keeps the cleared-only form — when: no cascade files are supplied
(a caller that does not thread them), the key is absent from the post-reset snapshot, it is not a
plain scalar (a `Bind`/`KeyStore`/list has no single "effective value" to print here), or it does
not expand cleanly (an unresolved `@`-ref / cycle — no built-in guess). A stored/resolved EMPTY
string also renders to `None` (Editor NIT-a), so the message is never "effective is now
`<blank>`"; `render_stored_scalar` already maps `""` → `None`.

⚑ The path-tier inputs are identical to the set-time probe's, but the FAILURE ARM DIFFERS: a
failure must not break a reset, and an "effective" computed without the floor would name a value
the cascade does not resolve — so this one returns `None` where the probe falls back to an empty
floor. See `_path_tier_split`.

⚑ **The tier NAMES parallel `assemble_levels`' order (MOST-SPECIFIC-FIRST):** `[box, workset,
agent.<active>, agent.default, system, base]`. The SOURCE tier is the first level that SETS the
key (the merge's precedence winner), read with the UNBOUND `dict` ops (S3, collision-safe), NEVER
the bound `.get`.


```write_system_value(config_path: Path, leaf: str, value: object) -> None```
Programmatically write a `system: <leaf>` key into the file it is given.

This is the PROGRAM editing a settings document on the user's behalf, at a point where no CLI verb
is running: `kanibako setup` recording `system.setup_completed`, and `setup_compat_gate`'s
best-effort forward bump of the same marker.

⚑ **THE PARAMETER NAME SAYS `config_path`, AND BOTH LIVE CALLERS NOW PASS THE SYSTEM SETTINGS
FILE.** The function is path-agnostic — its whole body is `write_nested_key(path, ("system",), leaf,
value)` — and the marker's storage moved to `@config.settings` on 2026-08-26, so nothing programmatic
writes a `system:` table into `kanibako_config.yaml` any more. That file cannot carry settings at all
(Jei), so a caller passing it here would be writing something no reader reads.

⚑ **IT DOES NOT "BYPASS A GUARD" (2026-08-23), and the older wording claimed a guard that is gone.**
`config set system.setup_completed=…` writes the same table through `_KEY_ROUTES`. The two writers
agree on the address on purpose — that agreement is what makes the CLI verb non-inert.

*leaf* is the bare key name under the `system:` table (NOT prefixed with `system.`). Writes preserve
all other content (read-modify-write via `write_nested_key`).


```_count_leaves(node: object) -> int```
Count the scalar/leaf entries under a nested-dict *node* (a scope table).

A `dict` recurses; anything else (scalar / list / `Bind`) is ONE leaf. Used so `reset_all`
reports the real number of overrides it removed when it clears a whole nested scope table
(residuals item 3).


```_clear_writable_scope_tables(path: Path, command_scope: ConfigLevel | None) -> int```
Drop the top-level SCOPE tables *command_scope* may write from *path*; count the leaves.

`reset --all` mirrors a per-key reset over the WHOLE file: a nested scope table (`box:` in a
workset file, `system: auth:` / `workset: auth:` / `box: bindings:` …) is cleared IFF a single
reset of a key in it at this command scope would PASS the §0 scope-direction guard — i.e. the
table's top-level token is in `_SCOPE_WRITE_ALLOWED[command_scope]` (the command scope's OWN
namespace + those it CONTAINS). ⚑ An UPWARD table (e.g. a hostile `system:` hand-edited into a
box file) is LEFT INTACT — a single reset of such a key is refused, so `--all` must not clear it
either.

NEVER touched here: `agent` (agent-keyed; cleared by the caller's dedicated pass, which holds the
scopeless `model`/`continue_mode` settings), `meta` (RO identity, §0), and non-scope keys
(top-level scalars like `allow_helpers` — the flat `load_project_overrides` pass owns those).
When *command_scope* is `None` (no scope context) NOTHING is cleared here — the guard cannot be
evaluated.


```reset_all(*, config_path, env_path=None, force=False, system_settings_path=None, command_scope=None) -> str```
Remove all overrides at this config level. Confirms unless *force*.

*system_settings_path*, when supplied (SYSTEM scope), is where the SETTINGS (the `agent` table +
nested SCOPE tables) are cleared from (`@config.settings` = `global/settings.yaml`), while CONFIG
overrides are cleared from `config_path`. When `None` (box/workset) everything is cleared from
`config_path` as before.

*command_scope* drives the §0 scope-direction guard for the nested SCOPE tables (residuals item
3): `--all` clears a nested table iff a single reset of a key in it at this scope would pass
`_scope_direction_error` — the command scope's OWN namespace + those it CONTAINS; an UPWARD table
is left intact. When `None` the flat/agent clears still run (backward compatible) but no nested
SCOPE table is touched.

It runs three clears in order: the project-level config overrides (always from `config_path`),
the agent settings table (agent-keyed `{<agent>: {key: val}}`; every agent's subsection, the
reserved `default` tier included), and the nested SCOPE tables.

⚑ **COUNT ONLY WHAT WAS ACTUALLY REMOVED (Editor F2).** `load_project_overrides` can report a
phantom `config_paths` field for any file carrying a `[system]`/`[config]` table (`KanibakoConfig`
folds those), and `unset_project_config_key` returns `False` when the flat key names no real
top-level entry — so an unconditional `count += 1` over-reported: a file with only a `[system]`
table said "Reset 1" while removing nothing, and SYSTEM-scope `--all` could never say "No
overrides".

⚑ The nested SCOPE table pass exists because the flat `load_project_overrides` pass only reaches
the `KanibakoConfig` dataclass fields, leaving nested scope tables (`<scope>.auth` /
`box.bindings` / a downward `box:` table in a workset file …) intact. It reads the same file the
settings live in (`settings_dest` — `config_path` at box/workset, the system settings file at
SYSTEM) and is gated by the §0 containment guard.


```show_config(*, global_config_path, config_path=None, env_global=None, env_project=None, effective=False, file=None, workset_path=None, agent_state=None, env_resolved=None, system_settings_path=None, category_snapshot=None, category_error=None) -> int```
Display config values — overrides only, or the full resolved view. Returns an exit code.

* *effective=False*: show only overrides at this level.
* *effective=True*: show all resolved values including inherited defaults.

*category_snapshot* (BOX scope, `--effective` only) is the resolved launch `KeyStore`. When
supplied, the PATH-DELIVERY categories are rendered too: each binding, and each ABSTRACT
declaration paired with the `binding_derivations.*` binding it produces (spec §0 — "`--effective`
shows BOTH the declaration and the derived binding and a user can see WHY a mount exists").
*category_error* carries a collision message when the snapshot could not be resolved, so
`config show --effective` REPORTS an M-7 collision rather than dying on it — it is the
migration's own detection recipe.

⚑ **ONE SCOPE.** The workset / system / agent `config show --effective` verbs still render no
category key at all: that display predates the keystore and reads `load_merged_config`. Extending
it across all five scopes is a read-surface job with its own owner, not a side effect of this one.

*system_settings_path*, when supplied (SYSTEM scope), is the file the agent SETTINGS +
`system.agent` are DISPLAYED from (`@config.settings` = `global/settings.yaml`); the `system.*`
CONFIG display always uses `global_config_path`. When `None` (box/workset) settings display reads
`config_path` as before.

### What each view prints

The `--effective` view prints, in order: the merged `KanibakoConfig` fields (each marked
`(override)` when the level overrides it); the agent settings — a fully-resolved *agent_state*
when supplied (the box view, marking only the keys actually set at the box level), else the
project-level overrides; at SYSTEM scope the nested settings-tier entries in the system settings
file (`system.auth.share_allowed`, downward scope defaults) — the values a system-scope `set`
stores and the launch cascade reads (F2: the effective view must show what set wrote); the `pref`
REQUESTS and the RESULT each produced (spec §2h read verbs); the path-delivery CATEGORIES and
their materialised derivations (§0); and the env vars.

The plain view prints the project overrides, the agent settings, the SYSTEM nested settings-tier
overrides (they ARE overrides at this level), and the `pref` REQUESTS stored at this noun (spec
§2h "config show lists prefs" — also overrides at this level).

⚑ **Then, LAST and not counted as an override, the entries the noun's settings file carries that
the keyspace does not DECLARE** (`_undeclared_stored_entries`, whose docstring holds the reasoning).
It is a display of FILE CONTENT, not a §0 read of a key: nothing is resolved, no default is
fabricated. It exists because `box get` / `workset get` now REFUSE such a name (§0) and the only
cure is a hand edit — a cure nobody can follow for a line they cannot see. The SYSTEM nested block
SUBTRACTS this set before printing: that flatten has no key semantics, so without the subtraction
one line would appear twice, once called an override and once called junk. The agent-settings
block does not subtract, because it renders a leaf FLAT (`bogus`, not `agent.default.bogus`) and
matching the two would need this display to re-derive a key's spelling.

### The env rows, and the two `.env` absences

⚑ *env_global* / *env_project* are VESTIGIAL and are NOT displayed. This display used to harvest
`env.<K>` rows from the docker `.env` files whenever *env_resolved* was absent, which after the
RQ-1 retirement would render as EFFECTIVE config values that never reach the box — the false
surface this project refuses. Env rows now come from *env_resolved* alone (the BOX view, composed
by `commands.start._build_config_env` from exactly what the launch applies), so a row shown here
is a value the box gets. A scope with no box view shows no env block rather than a fabricated one.

⚑ **Rendered `env <VAR>`, NOT `env.<VAR>`.** Every other row is a KEY, and `env.<VAR>` is now a
REFUSED spelling (R-39) — a reader who copied it into `config set` would be told it is retired.
These rows are not a key at all: they are the MERGE the box receives, whose parts live at
`<scope>.env.<VAR>` across several scopes, so no single key names a row. The space says so.

⚑ The plain view has NO docker `.env` block either. It used to list the level's `.env` file as
`env.<K>` overrides; those files are RETIRED (R-39/RQ-1) — not written by any verb, not read at
launch — so the rows would name a refused spelling AND assert an override that has no effect. A
stored `<scope>.env.<VAR>` is a nested SETTINGS entry and shows through
`_nested_settings_overrides`, under its real key.
