# The `workset` Verb Tree

A working set is a named group of related projects that share a root directory, a settings tier,
and a set of bind mounts. This module is that whole surface: a workset is MADE (`create`),
enumerated (`list`), inspected (`info`), unregistered (`rm`), gains and loses members
(`connect` / `disconnect`), is configured (`set` / `reset` / `get` / `show`), and declares the
directories its boxes mount (`share add` / `rm` / `list`).

Two facts shape almost everything below. First, **the workset tier is ONE file** —
`<root>/workset.yaml`, for every mode — holding SETTINGS ONLY, so every write here must MERGE
rather than overwrite. A workset's identity is its entry in the global registry's `worksets:`
section, and `meta.workset` is refused. Second, **a share is a binding keyed
BY ITS BOX DESTINATION** (R-10): a binding has no entry name, so the destination is the identity,
the `share list` DEST column, and the `share rm` argument, all at once.

Authority: the keyspace spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0 (closed keyspace, single route), §2a
(bind entry shape, self-resolving sources, the canonicalize-the-destination-not-the-source rule),
§2c (`meta.workset.settings`), §2h (`pref.*` requests); the disk-store rulings R-3/R-4/R-6/R-10/R-11
and R-39; the J2 lifecycle journal.

## The verb surface

`workset` is a released public interface — every flag, alias and help string ships.

| Verb | Aliases | What it does |
|------|---------|--------------|
| `create` | — | Make a workset dir, register it, stamp it from the host mold |
| `list` | `ls` | Every registered workset + project count; the DEFAULT when `workset` is given no verb |
| `rm` | `delete` | Unregister; with `--purge` also delete the directory tree |
| `connect` | — | Add an externally-existing project dir to a workset (REGISTERS, never seeds) |
| `disconnect` | — | Drop a member; with `--remove-files` also delete its per-project dirs |
| `info` | `inspect` | Name, root, members |
| `set` / `reset` / `get` / `show` | — | The workset-scope config verbs |
| `share` | — | `add` / `rm` (`remove`) / `list` (`ls`) — the workset's bind mounts |

`share` with no subcommand defaults to `list`, exactly as `workset` with no verb defaults to `list`.

## The workset settings file

`_workset_config_path` is the ONE derivation for every mode (spec §2c: `meta.workset.settings` =
`{meta.workset.path}/workset.yaml`) — always `<root>/workset.yaml`. The cascade tables
(`box`/`agent`/`workset.bindings`) coexist there, which is why `run_create` and `share add` both
LOAD-then-MERGE.

The PRIMARY ("default") workset roots at `{config.primary_workset}`, so its settings file is
`<primary_workset>/workset.yaml`, the file the launch cascade reads (finding **F4** of
`~/canon/notebook/archives/resources/reference/2026-07-02-scoping-cascade-audit.md`).

## `create`: refusals, pre-flight, then stamp

`run_create` refuses, in order, before anything is written:

1. **`--standalone`** (`_STANDALONE_REFUSAL`), before any path work: standalone is a single box's
   mode, not a working set's, with the cure `kanibako box create --standalone [path]`.
2. **A root nested in another box's tree** — inside another box's workspace, root, or store
   (`_lifecycle._dest_nesting_refusal`, judging the root as a nameless named box). Checked only
   when nothing exists at the path; skipped with `--force`. An existing path is `create_workset`'s
   "already exists" refusal.
3. **The mold pre-flight**, `check_workset_template`, BEFORE `create_workset`, and the order is the
   point. A whitelist refusal part-way through the stamp would be loud but NOT atomic: it would
   leave a REGISTERED workset with a root, its own `workset.yaml` and a partial chapter copy,
   recoverable only by `workset rm`. Checking first is also the order `create_workset` uses for
   its own guards, which all raise BEFORE any on-disk side effect.

The name is `--name` as typed, else `identifiers.derived_name` of the directory (`_` for each `-`
and `.`). `--force` also reaches `create_workset` as `force_logs_share`: a `workset.logs` directory
another workset resolves to is accepted.

`install_workset_template` is the **J-6 A-action (INSTANTIATION)**: it stamps the new workset store
from the host workset mold (`{system.template}/workset`) under the WORKSET whitelist. This is what
gives a workset its own handbook chapter dir and its own box template. The pre-flight above already proved the mold passes that whitelist, so this call
cannot refuse half-way.

Credential SHARING is a settable cascade key (`workset.auth.share_allowed`, via the config verbs),
NOT a create-time flag — `--distinct-auth` is retired.

`--image` and `--no-vault` set BOX-scope keys at the workset tier: they MERGE `box.image` /
`box.enable_vault: false` into the file's `box:` table (reaching inside it, so other `box.*` keys
survive), as overridable downward defaults for contained boxes.

## `connect`: the J2 write-ahead journal

`connect` REGISTERS an externally-existing dir into a workset and NEVER seeds. The `_journal_connect`
bracket lives HERE, in the connect command, and NOT in `add_project` — because `add_project` is
also the membership-write seam for the deferred move/convert/duplicate pipelines, which must not
journal a `connect` op.

Write-ahead order: write the entry BEFORE `add_project` (the durable membership write), clear
immediately after it returns. **HARD INVARIANT: registered ⇒ no pending entry at rest.** The key is
the host-side box dir (`ws.projects_dir / project_name`, the dir CONTAINING `home/`) — the uniform
J1/J2 key.

On a crash before the clear, the entry lingers; `resolve_workset_project` clears a stale `connect`
entry on the next resolve of the now-member box (self-heal, symmetric with the import path). If
`add_project` raises, the entry is LEFT (incomplete) and the error propagates after `_Unwind` rolls
back the in-process effects.

## `disconnect`: subject reconciliation and the OSError arm

The positional `<project>` is reconciled with the blanket `--box` flag through
`resolve_subject_value` (same string → warn and continue; different → error). `--box` is injected on
every leaf parser by `flags._walk`, so `workset disconnect` really does have one.

The winner is then resolved path-or-name through the SHARED box resolver `resolve_box_target`
(§Design 8 — the same resolver even though "box" reads oddly for a workset member; consistency
wins). A bare member name that is not an independently-registered box falls back to the raw token,
which `remove_project` matches against the workset's member list by name.

The `OSError` arm is not redundant with the `WorksetError` arm. A box tree can refuse deletion —
a root-owned canon skeleton, or any file a rootless container wrote as root. `remove_project`
reports that by raising a `PermissionError`, which is reported like every other verb does, with
the `podman unshare rm -rf` escalation the user can actually run.

## The config verbs

`run_set` / `run_reset` / `run_get` / `run_show` are thin argument shims onto `_run_workset_config`,
which dispatches into the `config_interface` engine. Credential sharing is an ordinary settable
cascade key (`workset.auth.share_allowed`) routed through the engine like any other — no
special-casing.

**There is no workset-tier docker env FILE (R-39/RQ-1).** The env family is the settings key
`workset.env.<VAR>`, stored in `ws_config` like every other workset key, so there is no second
write target at this scope.

### Which two files the SHOW arm hands the engine

`show_config` takes a config file and a settings file, and `config_dest.noun_settings_file` states
the test that picks between them: the settings argument wins *"when the noun keeps its settings
apart from its config file"*. A workset does — its Layer-1 config file and its settings file are
two different documents — so this arm passes them as the **system noun** does:
`config_path=config_file`, `system_settings_path=ws_config`.

⚑ **Do not spell the pair the other way** (`config_path=ws_config`, no settings argument):
`load_merged_config` would read the settings document as a **BOX-tier** file and drop it with an
upward-scope warning, and the nested-settings flatten would never be fed — so a workset's
`common` / `caches` / `seeded` declarations would be invisible in **both** views, against spec §0
(*"They remain real, declared keys: … `config show` lists them"*).

⚑ **ONE CARRIER.** The rows come from `config_display._nested_settings_overrides`, the same flatten
`system show` already renders its own declarations with. Nothing here re-reads or re-derives the
file, and there is no workset-only renderer.

The `pref:` table is flattened by that same walk, the only `pref` producer at every noun.

`--effective` renders BOTH halves of §0's *"both the declaration and the derived binding"*: the
declarations through the flatten above, and the binding each ABSTRACT one derives through
`_print_effective_derivations` below.

⚑ **The derived half needs no box.** The LAUNCH materializes a derivation in its resolve
(`_resolve_launch_snapshot`), which needs a box identity and an agent a working set does not have,
but §0 leaves *"WHERE the derivation is materialized"* to the implementation and obliges only what
the user SEES. The derived block feeds the workset-only collapse `_print_effective_shares` builds,
with no box. What it CANNOT answer without a box is stated at
`_print_effective_derivations` and is narrower than "the pair": no box's OWN settings file and no
agent file is read (the base, system and working-set files are, and the working set file's `box:`
defaults-down table with them, so a `box.masks` written there does apply), and a `seeded` row names
its guest destination but not the host store the copy eventually lands in.

🛑 **THE `binding_derivations` NODE IS NOT READ HERE AND MAY NOT BE.** It is materialized BEFORE
arbitration, for winners and losers alike (R-8), so every row in it reads as a live mount: a
`common` declaration a `masks` entry swallowed would print `(mount)` with the mask invisible. The
answer comes from PAIRING the declarations against the collapse — `store_collapse.pair_declarations`,
the one decision function, and `store_collapse.derivation_result` for the phrase — exactly as the
share half above does and as `box show --effective` does. The `Declaration` is built from the
CategoryEntry's own `label` (the display name, `workset.common[<dest>]`; the pairing never reads
it) / `box_dest` / `host_src` / `delivery`; `delivery` is READ rather than
re-derived from the key spelling, which is what keeps `seeded`'s `(copy)` distinct from the other
two's `(mount)`.

⚑ **THE GATE IS "IS THERE AN ABSTRACT DECLARATION", and it gates the COLLAPSE ONLY.** Every §0
*arbitration* refusal — bind-over-bind, mask-on-mask, seed-outside-home — is raised by the collapse,
so a working set that declares none of the trio cannot meet one from this verb.

🛑 **THE RESOLVE IS NOT GATED.** The entry list is
built BEFORE the gate, `expand` is strict, and `snapshot_category_entries` refuses an undeclared
shape — while the `show` arm's own rendering is a YAML flatten that expands nothing. So a working
set carrying only `bindings.ro: {/opt/x: ["{$NOPE}/y"]}`, with no abstract declaration anywhere,
prints `Error: Unknown variable: {$NOPE}` and returns 1. The `show_config` call alone returns 0 on
that file, so the rc is this block's.

⚑ **THAT IS THE INTENDED ANSWER, and it is why the code is not "fixed" to suppress it.** A view
whose whole claim is *these are the resolved values* must not report rc 0 over a file it could not
resolve. What the gate buys is narrower and is the part worth having: an ARBITRATION refusal cannot
reach a working set with nothing to arbitrate. A refusal that does arrive is reported through
`_preview_refusal` at rc 1 — the derived binding genuinely does not exist, and rc 0 after an
`Error:` line would say otherwise.

⚑ **THE RENDERING IS THE BOX BLOCK'S** (`config_display._print_category_block`'s abstract half):
declaration line, derivation indented beneath. One form at both nouns. The HEADING is this noun's
own addition, because this view ALSO prints the declaration rows through the flatten, and the block
needs to say which working set it answers for. The flatten keys each row by the NORMALIZED
destination (`normalize_bind_dest`, the spelling `parse_bind_map` files it under), so a declaration
carries ONE key in both blocks; only the source differs — as written above, rooted here.

⚑ **ONE LINE UNDER THE HEADING SAYS WHAT IT DOES NOT COVER.** The resolve reads this working set's
settings file alone, so a `masks` entry in the base or system settings file, an agent file or a box's own file that
would swallow a row at launch is not applied. The line names those four and points at
`kanibako box show <box> --effective`, which answers for a real box.

### Why three guards sit in the GET arm and none in the others

All three refusals — bare agent behavior key, bare `env.<VAR>`, and the closed-keyspace read gate
(`scope_read_key_error`, spec §0) — are checked at the HANDLER in the `get` arm specifically,
because the get engine returns VALUES and never error strings. The `set` and `reset` engines return
an `"Error: …"` string the handler already checks, so the equivalent refusals fire from inside the
engine on those paths.

⚑ **ORDER IS LOAD-BEARING, and §0's is LAST.** The first two recognize a SPECIFIC retired spelling
and hand back a cure written for it; the §0 gate is the generic "that is not a key". Run generically
first and every tailored cure disappears behind a vaguer sentence. The box handler orders its own
pair the same way, and additionally judges the key AFTER the bare-agent redirect — there is no such
arm here, because a workset spans several boxes and has no single agent to mirror.

The three verbs ARE symmetric in OUTCOME: `workset set|get|reset <ws> model` all print "agent
settings can't be set|read|reset at workset scope …", and `workset set|get|reset <ws> env.FOO` all
print the R-39 retirement + the `workset.env.FOO` cure.

A workset spans multiple boxes and agents, so there is no single agent to configure and no
`workset.agent.*` mirror; the refusal points at system scope (all agents) or at the per-box
`pref.agent.<agent>.<key>` request form (spec §2h).

⚑ The box scope does not redirect to a `box.agent.*` mirror either: `box.agent.<key>` is retired
(spec §2b), and the box arm of `bare_agent_key_scope_error` redirects to
`pref.agent.<agent>.<key>` (§2h).

### Cascade threading

Both arms pass `cascade_system_path=std.settings`, and neither passes a box scope — there is no box
at the workset command level.

* **RESET** also passes `cascade_workset_path=ws_config`, so the cleared-message can honestly name
  the now-effective value and its source tier.
* **SET** names the WORKING SET as its target (`std=std, ws=ws`, spec §2a, "Build the full cascade
  snapshot for the COMMAND's target"): `set_config_value` builds its `resolve_inputs(WORKSET)`, so
  the system file, the workset settings file and every workset anchor (`meta.workset.path`,
  `meta.runtime.ws_root`, the auth chain) resolve here as at launch, and every `{meta.box.*}` anchor
  is OMITTED — a value that needs one is refused as a dangling reference, never judged against a
  fabricated box.

## Shares (`workset.bindings.{ro,rw}`)

### Why there is no destination validator

There is no share-name regex (R-10): a binding has no entry NAME, the box DESTINATION is the
identity. A name rule does not convert into a destination validator, deliberately:

* a name character class excludes `/`, so it cannot describe a path at all — a validator would
  be a different rule, i.e. a NEW refusal on a surface that has never validated the destination;
* the destination's real rule IS ruled, and it is R-11 — a dest is normalized to an ABSOLUTE guest
  path (`~` expanded, the SOURCE never) — which lands with the floor producers, not here. A weaker
  second rule now would be two rules for one thing, which is the drift Code Convention 0 opens with.

What guards `share add` is the BIND GRAMMAR in `run_share_add`: exactly one unescaped `:`, both
halves non-empty. Each half must then pass the VALUE grammar `config set` checks
(`settings_configset.scan_tokens`: no unpaired brace, no empty or malformed reference, no illegal
escape), or it is refused naming the half, rather than stored and refused at every launch. A
destination at an INTERNAL bind (spec §2c) is refused before anything is written,
by `settings_launch.internal_bind_refusals` — the resolve's own check and message, called rather than
copied — so `share list --effective` never meets an entry `share add` answered "Added" to. `tests/test_commands/test_workset_share.py` pins the retirement.

### The stored entry shape

**The stored value is the 1-ELEMENT `[host_src]` entry** (R-3/R-6, `kb_store.BindEntry`): the
destination is the KEY and appears exactly once. The reader, the floor producers and this writer
all use the dest-keyed shape, so the merged arm stays HOMOGENEOUS.

The store REFUSES a 3-element entry outright (*"Dest-keyed binding entry must have 1 or 2 elements
[src[, options]] … The DESTINATION is the map key, not part of the entry."*), so the live on-disk
shapes are exactly `[src]` and `[src, options]`.

**Why the destination can be the identity at all:** bindings are strictly ACT-ONCE, so a name could
never distinguish two entries at one destination — it would only ever be decoration. That is why
re-running `share add` with the same destination OVERWRITES its source, and why that overwrite is
the only way to "update" a binding: shares are live bind mounts and NO content sync exists. Because
mounts are fixed at container creation, every mutation prints `_NEXT_LAUNCH_REMINDER` — the change
lands on the NEXT box launch and a running box is unaffected.

### Canonicalize the destination; never the source

The stored DESTINATION is canonicalized (R-11): a leading `~` expands to the fixed guest home, so
`~/x` and `/home/agent/x` are ONE entry rather than two at one destination: adding
`/host/a:~/x` then `/host/b:/home/agent/x` prints "Added" then "Updated" at `/home/agent/x`.

The stored `host_src` is NOT canonicalized — its `~` is the INVOKING USER's home, and a workset file
is read by other users on other machines (spec §2a, "the destination is canonicalized; the source is
not"). `normalize_bind_dest` carries an `@`-ref, a `$var` and a bare leaf through VERBATIM, which is
what makes the retired-name cure below spellable.

Without that canonicalization, `~/x` and `/home/agent/x` would be two dict entries at ONE place and
`share rm` could only ever remove the spelling the user happened to retype.

On the `share rm` side, R-11 is applied to the ARGUMENT rather than to every stored key — the stored
keys are already canonical by construction, and a user who types the `~` spelling of the same place
must still find it. The destination is otherwise matched VERBATIM, so a
literal `:` in a destination is typed plainly to `share rm` even though `share add` needs it escaped
as `\:` (there, the `:` is the separator).

### Relative host sources, and why the default workset refuses them

A BARE-RELATIVE host source is ABSOLUTIZED AGAINST THE WORKSET ROOT at WRITE time (spec §2a: a
stored source must fully resolve on its own). The documented convenience — "a relative `host_src` is
resolved under the working set root" — holds: the stored value is the full path, so it resolves
the same in every context that reads it (launch, `--effective`, another tool), and no reader
applies a root-join. The command says so when it happens: `share add <ws> reldir:/box/rel` stores
`<ws_root>/reldir` and prints "(relative source resolved under the working set root and stored as
…)".

The DEFAULT workset REFUSES a relative source instead, because it has no bindings root:
`{config.primary_workset}` is kanibako's own internal store, not a user project dir, so rooting
there would swap a visible failure for a silent wrong path. Refusing names the mistake at the
moment it is made, with the "an absolute path, '~/…', '{$VAR}' or a '{key}' reference" cure.

### The bind grammar

`share add` parses the `host_src:guest_dest` grammar through the CANONICAL escape-aware splitter
`split_bind` (spec §2a CLI-INPUT edge) — the SAME parser `config set` and the resolver use — so an
escaped colon (`\:` for a literal `:` in a path) behaves identically here. `split_bind` splits at
the FIRST unescaped `:` and returns both halves with escapes resolved (the second half is `None`
when there is no unescaped `:`).

The share grammar is EXACTLY two fields — the `ro`/`rw` mode comes from `--mode`, not from the bind
— so a SECOND unescaped `:` is rejected, detected by RE-SPLITTING the (already-unescaped) guest half
rather than by a raw `:` scan.

Storage stays pure structured (spec §2a — a YAML list, never a colon-joined string); the colon form
is only the user-facing input/display grammar.

### Listing: single route, no second resolver

Single-route (7c): there is no second `resolve_shares` / `read_bindings` resolver path. `share list`
has two views, and neither applies a root-join, so they cannot drift from each other or from a
launch (P3).

* **Raw** (`_workset_raw_shares` → `_share_source_display`): the workset's own configured bindings,
  read through the committed `assemble_levels` — the SAME file reader the launch snapshot uses.
  There is no second `read_bindings` path. The raw value is the structured `Bind`, with `@`-refs /
  `$XDG` / `~` UNRESOLVED (§0). Missing file → `{}`. Each `BindEntry` is rendered back to its
  on-disk list shape (`[src, opts]` or `[src]`) for display.
* **`--effective`** (`_print_effective_shares`): resolves through the LAUNCH's own builder —
  `settings_launch.resolve_inputs(subject=WORKSET)` then `build_launch_snapshot`, then
  `snapshot_category_entries` — with the `GENERAL_SLOT` placeholder agent and no agent file.
  ⚑ **The pipeline itself lives in
  `_workset_preview_entries`, the arbitration in `_workset_preview_collapse` and the refusal arm in
  `_preview_refusal`**, because the derived-binding block of `workset show --effective` describes
  the same working set: two resolves of one file would be two answers about it. Everything the rest
  of this section says about the resolve, the floor, the collapse and the framing holds of those
  three.

  Every stored `host_src` resolves ON ITS OWN (spec §2a), because `share add` absolutizes a relative
  source at WRITE time, so there is no root to apply here and no ROOT-JOIN can make this display
  diverge from what a launch mounts.

⚑⚑ **THE RESOLVE IS THE LAUNCH'S, SO ITS FILES, FLOORS AND REFUSALS ARE TOO.** Every file a box
in the set reads, except the box's own and the agent file, is read, judged and named — the system
and base files, the `pref:` requests and the §2c auth chain included — and `refuse_config_table`,
the retired-spelling refusal and `refuse_read_time_faults` run inside `build_launch_snapshot` as
they do at launch. So a working set the launch refuses (a `config:` table in the workset file, a
bare-relative `pref.agent.<a>.canon` request, an undeclared key in the system file) refuses here
too, and a share sourced at `{workset.auth.path}/notes` lists `<ws_root>/auth/notes`, as a launch
mounts it.
`ResolveSubject.WORKSET` (from `as_kwargs`) gives the §0 message this noun's verbs. The refusals
raise `SettingsError`, so both listings report through `_preview_refusal` at rc 1.
⚑ **A box-scope anchor is OMITTED, never fabricated** (`resolve_inputs`): a working set names no
box, so a value that reads a `meta.box.*` key (which the box-less resolve omits) or any `box.*` key
(which a box's own file may set) is the box's to decide. `_workset_preview_entries` asks
`settings_launch.depends_on_the_box(refs, in_workset=True)` of each entry's refs and replaces such
an entry's source with `DEPENDS_ON_THE_BOX`, so a share sourced at `{meta.box.path}/x` or
`{box.canon}/y` lists its source as `(depends on the box)`. Both `--effective` listings share it,
because both build their entries through `_workset_preview_entries`.
`tests/test_commands/test_workset_share.py` pins it.
⚑ **`run_share_list` TAKES ITS `--effective` BRANCH BEFORE THE EMPTY-SHARES RETURN**, so a working
set with NO shares still reaches the preview, and a `workset: {frob: 1}` refuses there as every box
does. `_print_effective_shares` gives the raw view's one-line empty answer (`_print_no_shares`, one
carrier) — but only AFTER the resolve and the arbitration have run.

The context and floors come from `resolve_inputs`, which keeps the **resolver SPLIT** (spec §1A /
JC-2): Layer-1 `config.*` is the `ctx.config` foundation (`agent_select.host_resolve_ctx`), and
Layer-2 `system.*` (`inputs.system_floor`) rides into the snapshot as a default category table, so a
share value's `@`-ref such as `{system.channelroot}` resolves from the snapshot itself. The xdg map
is the canonical FULL host map anchored on the resolved `std.data_home` — a data-home-only partial
map RAISES on a stored `{$XDG_CACHE_HOME}/…` value.

⚑⚑ **THE `ctx.config` FOUNDATION IS `settings/paths.host_config_map`, AND IT IS SHARED WITH THE
LAUNCH TOO** (`settings/agent_select.launch_resolve_ctx`). It derives the tier from the declared
`CONFIG_PATH_DEFAULTS` table, so every Layer-1 key — `config.journal` included — reaches both call
sites, and a key declared tomorrow does too without an edit. A hand-written map here would drop a
binding sourced at an omitted key: `_lookup_raw` answers `_ABSENT` and the row vanishes, rc 0.

⚑⚑ **THE FLOOR IS `settings/paths.system_path_floor`, AND IT IS SHARED WITH THE LAUNCH** — the
`system_floor` of `settings_launch.resolve_inputs` is the same call, derived from
`SYSTEM_PATH_DEFAULTS` entire. Two hand-written floors would disagree, and an unresolvable `@`-ref
is a legitimately-absent referent rather than a failure — so a binding sourcing a key one floor
lacked (say `{system.channels.chat}`) would mount at launch and be **silently omitted from this
listing**, rc 0. This display folds the map into a resolve floor and prints collapsed BINDINGS,
never the floor.

⚑⚑ **THE FLOOR ALSO CARRIES `meta.workset.path`, AND WITHOUT IT THIS LISTING WOULD PRINT A PATH
THAT EXISTS NOWHERE.** `system_path_floor` is the `system.*` tier and carries no `meta.*` key at
all, while `settings_expand` renders an anchor with no referent as the EMPTY STRING — so a share
written `{meta.workset.path}/refdir` would be listed as mounting `/refdir`, at the filesystem root,
rc 0, no warning. The anchor is spec §2a's workset
`<scope-root>`, so the same gap mis-rooted every `common` / `caches` / `seeded` declaration by
definition — which is what made this a PREREQUISITE of rendering the derived half at all, not a
neighboring fix. 🛑 **THIS IS NOT A ROOT-JOIN** (the section above still holds): nothing is
prepended to a stored value; an anchor the stored value already names is given its referent.

⚑ **`ws.root` IS the anchor, and there is NO per-mode branch to reproduce.**
`paths.workset_settings_path` declares the workset tier as `{meta.workset.path}/workset.yaml`, so the
working set's own root is that anchor by declaration and nothing here decides where a root is. And
the key itself does not vary by mode: §2c's **RUNTIME-DERIVED — ALL PROJECTS (every mode)** block
declares `meta.workset.path | {meta.runtime.ws_root}` once, for all three. The launch reaches that
same value through `{config.primary_workset}` at primary because `meta.runtime.ws_root` is where the
per-mode choice lives (§1A) — one rung below this key, and not a variation at this key. Primary and
named both land on `ws.root` here; standalone cannot reach this noun at all (`workset create
--standalone` is refused, and `resolve_workset_name` reads the registry).

⚑ **This listing does not floor the anchor itself.** It comes from `resolve_inputs`' WORKSET
floors (`_workset_floors`), the sequence a member box's launch also runs, with
`meta.runtime.ws_root` and every `workset.*` layout anchor beside it. `workset set` takes the same
floors at set time (`config_interface._set_time_target`), so there is no separate carrier left.

`tests/test_commands/test_workset_cmd.py::TestWorksetCmdSystemFloor` pins it by DERIVATION: it
drives this display with a binding sourced at every key the launch floor answers and names no key
itself.

The DEST column is the share's IDENTITY (R-10) and is exactly the argument `share rm` takes.

⚑⚑ **AND IT ARBITRATES — the launch's own collapse, not a second walk.** Resolving is only half of
what a launch does. Printing the ENTRY LIST out of `snapshot_category_entries` — every stored
binding, pre-collapse, with no mask arm, no containment and no collision rule — would list a share
under a `workset.masks` entry as a live mount while the box receives **nothing** at that path, and
would list cleanly a workset whose declarations a launch REFUSES outright (a `common` extending onto
a bound dest, which §0 refuses).

The two calls are `store_shape.build_store_shape_set` then `store_collapse.collapse_store_shapes` —
the same pair `commands/start._install_assembly_collapse` makes — and the per-share outcome is read
off `store_collapse.pair_declarations`, the function `box show --effective` already pairs its own
declarations with. **The shares stay the SUBJECT**: a share the collapse swallowed keeps its ROW and
gains a reason rather than vanishing, because dropping a row silently is the failure the floor
above guards against.

* **A delivered share earns the ARROW**: `<src> -> <dest>  [<mode>]`.
* **A share that receives nothing is printed in DECLARATION form** — `<dest>  [<mode>]  (declared:
  <src>)` — with `store_collapse.derivation_result`'s phrase indented beneath it. A reader skimming
  for mounts cannot take a loss for one.

⚑⚑ **A COLLISION IS CAUGHT HERE AND FRAMED — one added line, and the refusal itself is printed FROM
the exception.** The launch's own `Error: …` and exit 1 are the right message and code, but alone
they are context-free: the user asked what a box in this working set would MOUNT, and a bare
collision reads as a listing failure rather than as the answer. `_preview_refusal` catches
`CategoryCollisionError` and prints, to stderr, above the refusal:

```
Cannot say what working set 'myws' would mount: its declarations collide at '/opt/arb', so no box in it can launch. The refusal a launch gives follows.
Error: 'workset.common[/opt/arb]' extends onto '/opt/arb', which
'workset.bindings.ro[/opt/arb]' already binds.
…
```

🛑 **THE COLLISION IS NEVER RESTATED.** Every word of the detail is `f"Error: {e}"` off the caught
exception, byte-identical to what `cli.main`'s `KanibakoError` arm would have printed (`cli.py`), and
`box_dest` in the frame is READ OFF the exception rather than re-derived. A second carrier of that
wording would drift from the launch's, and the launch's is the one a user has to act on. There is
exactly ONE `Error: ` prefix — the test asserts the count, because catching an error that a caller
already formats is how a doubled prefix arrives.

⚑ The frame goes ABOVE the refusal, not below: the refusal runs a dozen lines and ends in a YAML
remedy block, and a reader should be told what they are about to read before wading into it.

⚑ **THE pid-0 FOUNDATION IS `_PREVIEW_HOME_SRC`, and it is SPELLED rather than guessed.** The
collapse folds every scope over a home bind; a workset names no BOX, and the home SOURCE is per-box
(`{meta.box.path}/home`), so there is no true answer to give. Only the foundation's DEST takes part in
arbitration, and the placeholder cannot reach the display anyway: a share AT that dest is refused as
a second bind at the foundation's point, and one INSIDE it holds its own destination. It carries NO
options — home's are seam machinery (spec §2c's `meta.box.home` row), and `config_display` declines
to print them for
the same reason.

⚑ The COLLISION WARNINGS a launch emits (`emit_collision_warnings`) are deliberately NOT raised here.
§0's exempt pair is an ambiguity between two ABSTRACT declarations and a share is never one of the two, so
the surviving share is unaffected — and reaching for them would mean `workset_cmd` importing the
`start` module, which is the import `system_path_floor` was placed in `settings/paths.py` to avoid.

`tests/test_commands/test_workset_cmd.py::TestWorksetShareListArbitrates` pins it, including the
nesting case (a bind INSIDE a bind still mounts — the guard against a fix that reports every
containment as a loss) and TWO mutations, each on a throwaway stand-in rather than a real symbol:

* a stand-in `collapse_store_shapes` returning every declared bind with no mask arm restores the
  defect, so the other cases are pinned on the ARBITRATION and not on the resolve;
* a stand-in `build_store_shape_set` that refuses with a SENTINEL string the source tree does not
  contain pins that the collision detail is carried VERBATIM from the exception — if the display ever
  composed its own version of the refusal, the sentinel would not appear.

### The retired name-keyed refusal

`_workset_raw_shares` REFUSES a retired name-keyed entry (R-10), and it does so on the KEY: the
entry holds no destination to compare against, so the test is whether the key is SPELLABLE as a
destination at all — absolute, or leading `~` / `$` / `@` (spec §2a self-resolving,
`is_self_resolving`).

A bare leaf like `docs` is a NAME, and there is no honest way to DISPLAY one: the DEST column would
print a name, and the `share rm` argument it advertises would be a name too. So it is named and
refused rather than silently mis-rendered (Code Convention 0; disk-store R-8's posture). It raises
`SettingsError`, which `run_share_list` reports rather than letting a traceback out of a listing
command.

The cure the error prints is deliberately spellable: `share rm` deletes whatever key it is given,
because `normalize_bind_dest` passes a bare leaf through unchanged: a hand-written `docs:` key makes
`share list` print the refusal, and the prescribed `workset share rm <ws> docs --mode rw` removes it
verbatim.

### The arity trap

**KNOWN, RULED GAP — a retired 2-element `[src, dest]` under a DESTINATION-shaped key is
UNDETECTABLE** in `_workset_raw_shares` and reads as `BindEntry(src, opts=dest)`. That is the trap
the spec names outright ("the retired 2-element `(host_src, box_dest)` and the current 2-element
`(host_src, options)` have the SAME ARITY"), and it is accepted because R-4 rules NO MIGRATION:
nobody has such a file. Do NOT invent a mount-options grammar here to close it — that would be a new
refusal nobody ruled.

### The dead options bracket

⚑ It points at a live display defect: `_share_source_display`'s options bracket is dead code.

* Element 1 of an entry is the **OPTIONS** (`BindEntry.opts`), not the destination. The destination
  is not in the entry at all — the store refuses a 3-element entry with exactly that message.
* `_workset_raw_shares` emits `[leaf.src, leaf.opts]` or `[leaf.src]`, so `value[2:]` is **always
  empty**. Per-entry options are never shown in the raw listing: a hand-written
  `workset.bindings.ro: {/box/withopts: [/host/src2, "ro,noexec"]}` prints
  `  /box/withopts   ro   /host/src2` — no bracket. The same entry under `--effective` renders as a
  mount, so the entry itself is live and only the raw view drops its options.

The source carries a one-line `⚑` marker at the slice so a future reader does not take it for live
behavior; the fix is a decision, not a prose change.

## Functions

```python
def add_parser(subparsers: argparse._SubParsersAction) -> None
```
Build the whole `workset` verb tree, including the nested `share` subtree.

```python
def _load_std()
```
Load config and standard paths.

```python
def _workset_config_path(ws) -> Path
```
The workset-tier settings file — ONE derivation for every mode (spec §2c). See
"The workset settings file".

```python
def run_create(args: argparse.Namespace) -> int
```
Refuse `--standalone` and a nested root, pre-flight the mold, create + register the workset, stamp
it, then MERGE the create-time `box.*` keys into its file. See "`create`: refusals, pre-flight,
then stamp".

```python
def run_list(args: argparse.Namespace) -> int
```
Table of every registered workset plus the always-present synthesized default; `-q` prints names only.

```python
def run_rm(args: argparse.Namespace) -> int
```
Unregister a workset (never the default); refuses a workset with members unless `--force`.

```python
def run_connect(args: argparse.Namespace) -> int
```
Register an externally-existing project dir as a member, inside the J2 write-ahead bracket. See
"`connect`: the J2 write-ahead journal". An in-tree source under a null `workset.workspaces` is
refused BEFORE the bracket (`refuse_null_workspaces`), so the refusal leaves no pending entry.

```python
def run_disconnect(args: argparse.Namespace) -> int
```
Drop a member, optionally removing its files. See "`disconnect`: subject reconciliation and the
OSError arm".

```python
def run_info(args: argparse.Namespace) -> int
```
Print name, root and members.

```python
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
```
Argument shims: each normalizes `args` into the shape `_run_workset_config` expects and delegates.
`run_reset` is the only one with its own refusal — a reset needs a key or `--all`.

```python
def _run_workset_config(args: argparse.Namespace) -> int
```
Shared get/set/show/reset dispatch into the `config_interface` engine. See "The config verbs".

```python
def _share_source_display(value: object) -> str
```
Render a stored binding entry's HOST SOURCE for the raw listing's SOURCE column. ⚑ See "The dead
options bracket" — the options branch is unreachable.

```python
def _resolve_share_workset(name: str)
```
Resolve *name*: `(ws, std)`, or a printed error and `(None, None)` — the caller returns 1.

```python
def _load_share_doc(ws_config: Path) -> dict
```
Load the workset `workset.yaml` as a nested dict (missing → `{}`).

```python
def run_share_add(args: argparse.Namespace) -> int
```
Add (or overwrite) a workset binding, keyed by its box DESTINATION (R-10). See "The stored entry
shape", "Canonicalize the destination; never the source", "Relative host sources", "The bind
grammar".

```python
def run_share_remove(args: argparse.Namespace) -> int
```
Remove a workset binding by its box DESTINATION. With `--mode` omitted, removes from whichever mode
contains that destination; errors if it exists in both (ambiguous) or in neither (missing).

```python
def run_share_list(args: argparse.Namespace) -> int
```
The raw view by default, the resolved-mount view under `--effective`. See "Listing: single route, no
second resolver". The `--effective` branch runs BEFORE the empty-shares return, so a workset with no
bindings still reaches the resolve; both views print the same one-line empty answer.

```python
def _workset_raw_shares(files: Sequence[SettingsFile]) -> dict[tuple[str, str], object]
```
The workset file's `workset.bindings.{ro,rw}` as a `{(mode, dest): raw}` map (the RAW view);
*files* is its `NARROW` read. See "The retired name-keyed refusal" and "The arity trap".
`assemble_levels` returns `[box, workset, agent.<active>, agent.default, system, base]`, so index 1
is the workset partial — the only file read.

```python
def _workset_preview_entries(ws, std) -> list[CategoryEntry]
def _workset_preview_collapse(entries: list[CategoryEntry]) -> CollapsedStore
def _preview_refusal(ws, exc: CategoryCollisionError | SettingsError) -> int
```
The working set's launch resolve (WORKSET subject), its arbitration, and the one refusal arm —
shared by both `--effective` listings, because they describe ONE working set. *exc* is typed as the two refusals the preview
raises, so an unrelated exception cannot be framed as a listing refusal (P3). See "Listing: single route, no second resolver".

```python
def _print_effective_shares(ws, std) -> int
```
Resolve and print the workset's bindings as launch-time mounts. See "Listing: single route, no
second resolver".

```python
def _print_effective_derivations(ws, std) -> int
```
The DERIVED half of §0 at this noun: every `common` / `caches` / `seeded` declaration with the
binding it produces, paired against the same collapse. Runs only under `--effective`; the COLLAPSE
runs only when there is an abstract declaration to arbitrate, but the RESOLVE always does — so this
verb exits 1 on a file it cannot resolve. See "Which two
files the SHOW arm hands the engine".
