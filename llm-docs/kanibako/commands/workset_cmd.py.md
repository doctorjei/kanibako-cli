# The `workset` Verb Tree

A working set is a named group of related projects that share a root directory, a settings tier,
and a set of bind mounts. This module is that whole surface: a workset is MADE (`create`),
enumerated (`list`), inspected (`info`), unregistered (`rm`), gains and loses members
(`connect` / `disconnect`), is configured (`set` / `reset` / `get` / `show`), and declares the
directories its boxes mount (`share add` / `rm` / `list`).

Two facts shape almost everything below. First, **the workset tier is ONE file** —
`<root>/settings.yaml`, for every mode — so identity (`workset.meta`) and cascade settings coexist
in it and every write here must MERGE rather than overwrite. Second, **a share is a binding keyed
BY ITS BOX DESTINATION** (R-10): a binding has no entry name, so the destination is the identity,
the `share list` DEST column, and the `share rm` argument, all at once.

Authority: the keyspace spec `settings-keyspace-1.8.0.md` §0 (closed keyspace, single route), §2a
(bind entry shape, self-resolving sources, the canonicalize-the-destination-not-the-source rule),
§2c (`meta.workset.settings`), §2h (`pref.*` requests); the disk-store rulings R-3/R-4/R-6/R-10/R-11
and R-39; the J2 lifecycle journal.

## The verb surface

`workset` is a released public interface — every flag, alias and help string ships.

| Verb | Aliases | What it does |
|------|---------|--------------|
| `create` | — | Make a workset dir, register it, stamp it from the host mould |
| `list` | `ls` | Every registered workset + project count; the DEFAULT when `workset` is given no verb |
| `rm` | `delete` | Unregister; with `--purge` also delete the directory tree |
| `connect` | — | Add an externally-existing project dir to a workset (REGISTERS, never seeds) |
| `disconnect` | — | Drop a member; with `--remove-files` also delete its per-project dirs |
| `info` | `inspect` | Name, root, creation date, members |
| `set` / `reset` / `get` / `show` | — | The workset-scope config verbs |
| `share` | — | `add` / `rm` (`remove`) / `list` (`ls`) — the workset's bind mounts |

`share` with no subcommand defaults to `list`, exactly as `workset` with no verb defaults to `list`.

## The workset settings file

`_workset_config_path` is the ONE derivation for every mode (spec §2c: `meta.workset.settings` =
`@meta.workset.path/settings.yaml`) — always `<root>/settings.yaml`. A NAMED workset's file also
carries the workset identity (`workset.meta`); the cascade tables (`box`/`agent`/`workset.bindings`)
coexist there without colliding, which is why `run_create` and `share add` both LOAD-then-MERGE.

The PRIMARY ("default") workset roots at `@config.primary_workset`. Its old
`@config.data/config.yaml` write target was a DEAD WRITE — the launch cascade never read it — which
is finding **F4** of `reference/2026-07-02-scoping-cascade-audit.md`.

## `create`: pre-flight, then stamp

`check_workset_template` runs BEFORE `create_workset`, and the order is the point. A whitelist
refusal part-way through the stamp would be loud but NOT atomic: it would leave a REGISTERED
workset with a root, its own `settings.yaml` and a partial chapter copy, recoverable only by
`workset rm`. Checking first is also the order `create_workset` already uses for its own name
guards (reserved-name, same-kind uniqueness, and the cross-kind primary-box guard all raise
BEFORE any on-disk side effect).

`install_workset_template` is the **J-6 A-action (INSTANTIATION)**: it stamps the new workset store
from the host workset mould (`@system.template/workset`) under the WORKSET whitelist. This is what
gives a workset its own handbook chapter dir and its own box template; before this step existed,
neither did. The pre-flight above already proved the mould passes that whitelist, so this call
cannot refuse half-way.

Credential SHARING is a settable cascade key (`workset.auth.share_allowed`, via the config verbs),
NOT a create-time flag — `--distinct-auth` is retired.

The `image` / `standalone` / `no_vault` writes MERGE into the existing file rather than overwriting
it, so the `workset.meta` identity `create_workset` just wrote survives.

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
which `remove_project` matches against the workset's member list by name. *(Verified end-to-end:
`workset disconnect <ws> <member>` from an unrelated cwd removes the member.)*

The `OSError` arm is not redundant with the `WorksetError` arm. A box tree can refuse deletion —
a root-owned canon skeleton, or any file a rootless container wrote as root. `remove_project`
reports that by raising, and only `WorksetError` was caught, so the `PermissionError` escaped as a
raw traceback. It is now reported like every other verb does, with the `podman unshare rm -rf`
escalation the user can actually run.

## The config verbs

`run_set` / `run_reset` / `run_get` / `run_show` are thin argument shims onto `_run_workset_config`,
which dispatches into the `config_interface` engine. Credential sharing is an ordinary settable
cascade key (`workset.auth.share_allowed`) routed through the engine like any other — no
special-casing (the old `group_auth` `workset.meta` identity key is retired).

**The workset-tier docker env FILE is GONE (R-39/RQ-1).** The env family is the settings key
`workset.env.<VAR>`, stored in `ws_config` like every other workset key, so there is no second
write target at this scope.

### Why two guards sit in the GET arm and none in the others

Both refusals — bare agent behavior key, and bare `env.<VAR>` — are checked at the HANDLER in the
`get` arm specifically, because the get engine returns VALUES and never error strings. The `set`
and `reset` engines return an `"Error: …"` string the handler already checks, so the same two
refusals fire from inside the engine on those paths.

The three verbs ARE symmetric in OUTCOME. *(Verified on the production path: `workset set|get|reset
<ws> model` all print "agent settings can't be set|read|reset at workset scope …", and
`workset set|get|reset <ws> env.FOO` all print the R-39 retirement + the `workset.env.FOO` cure.)*

A workset spans multiple boxes and agents, so there is no single agent to configure and no
`workset.agent.*` mirror; the refusal points at system scope (all agents) or at the per-box
`pref.agent.<agent>.<key>` request form (spec §2h).

> ⚑ **A FALSE CLAIM WAS DROPPED HERE, NOT RELOCATED.** The old comment ended *"The box scope
> instead redirects the read to its `box.agent.*` mirror."* `box.agent.<key>` is **RETIRED (P7,
> spec §2b)** — `settings/config_interface.py:485`, `:713`, `:881` each refuse it by name — and the
> box arm of `bare_agent_key_scope_error` redirects to `pref.agent.<agent>.<key>` (§2h), not to a
> `box.agent.*` mirror. The only surviving `box.agent` spelling in the spec is the RO read-back
> mirror `meta.box.agent.<key>`, which is a different key and not what that arm names. The comment
> described a superseded design (the 2026-07-08b convergence) that P7 later reversed.

### Cascade threading

Both the RESET and the SET arms pass `cascade_system_path=std.settings` and
`cascade_workset_path=ws_config`, and neither passes a box scope — there is no box at the workset
command level.

* **RESET** needs it so the cleared-message can honestly name the now-effective value and its
  source tier.
* **SET** needs it for a CATEGORY set's set-time E3 probe (Jei (b), 2026-06-29): an `@system.*` or
  lower-scope ref in the new value must resolve here exactly as it will at launch.

The workset is the COMMAND SCOPE, so `ws_config` lands in the cascade's workset slot and is passed
twice — once as the write target (`config_path`) and once as that cascade rung
(`cascade_workset_path`).

## Shares (`workset.bindings.{ro,rw}`)

### Why there is no destination validator

`_SHARE_NAME_RE` (`^[A-Za-z0-9._-]+$`) was RETIRED 2026-08-06c under R-10 — a binding has no entry
NAME, the box DESTINATION is the identity. It was NOT converted into a destination validator,
deliberately:

* its character class excludes `/`, so it cannot describe a path at all — "converting" it would
  mean writing a different rule, i.e. inventing a NEW refusal on a surface that has never validated
  the destination;
* the destination's real rule IS ruled, and it is R-11 — a dest is normalized to an ABSOLUTE guest
  path (`~` expanded, the SOURCE never) — which lands with the floor producers, not here. A weaker
  second rule now would be two rules for one thing, which is the drift Code Convention 0 opens with.

What guards `share add` is the BIND GRAMMAR in `run_share_add`: exactly one unescaped `:`, both
halves non-empty. `tests/test_commands/test_workset_share.py` pins the retirement.

### The stored entry shape

**The stored value is the 1-ELEMENT `[host_src]` entry** (R-3/R-6, `kb_store.BindEntry`): the
destination is the KEY and appears exactly once. P4′ deliberately left it as the 2-element
`[host_src, box_dest]` pair — the destination written twice — because the reader could not flip
alone: the floor producers still emitted name-keyed entries into the SAME merged arm, and a merged
arm must be HOMOGENEOUS. P6 flipped the reader, the floor and this writer together, which is what
made the deferred "drop element 2" safe.

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
`~/x` and `/home/agent/x` are ONE entry rather than two at one destination. *(Verified: adding
`/host/a:~/x` then `/host/b:/home/agent/x` prints "Added" then "Updated" at `/home/agent/x`.)*

The stored `host_src` is NOT canonicalized — its `~` is the INVOKING USER's home, and a workset file
is read by other users on other machines (spec §2a, "the destination is canonicalized; the source is
not"). `normalize_bind_dest` carries an `@`-ref, a `$var` and a bare leaf through VERBATIM, which is
what makes the retired-name cure below spellable.

Without that canonicalization, `~/x` and `/home/agent/x` would be two dict entries at ONE place and
`share rm` could only ever remove the spelling the user happened to retype — which is the collision
that triggered R-11 in the first place.

On the `share rm` side, R-11 is applied to the ARGUMENT rather than to every stored key — the stored
keys are already canonical by construction, and a user who types the `~` spelling of the same place
must still find it. The destination is otherwise matched VERBATIM, so a
literal `:` in a destination is typed plainly to `share rm` even though `share add` needs it escaped
as `\:` (there, the `:` is the separator).

### Relative host sources, and why the default workset refuses them

A BARE-RELATIVE host source is ABSOLUTISED AGAINST THE WORKSET ROOT at WRITE time (spec §2a: a
stored source must fully resolve on its own). The documented convenience — "a relative `host_src` is
resolved under the working set root" — is preserved EXACTLY: the same input yields the same mount,
because this join is the one the launch used to apply (the retired assembly-time prepend). What
changes is the ARTIFACT: the stored value is now the full path, so it resolves the same in every
context that reads it (launch, `--effective`, another tool) instead of only in the one that knew
the root. The command says so when it happens. *(Verified: `share add <ws> reldir:/box/rel` stores
`<ws_root>/reldir` and prints "(relative source resolved under the working set root and stored as
…)".)*

The DEFAULT workset REFUSES a relative source instead, because it has no bindings root. The old
root-join lived in TWO places — `_launch_snapshot_inputs` in `start.py` and `_print_effective_shares`
here — and NEITHER applied to the default workset, so a relative source there never joined and went
to the mount spec as a relative string, resolved against whatever the process CWD happened to be.
There is no defensible root to pick at write time either: `@config.primary_workset` is kanibako's
own internal store, not a user project dir, so rooting there would swap a visible failure for a
silent wrong path. Refusing names the mistake at the moment it is made. *(Verified: refused, with
the "an absolute path, '~/…', '$VAR' or an '@'-reference" cure.)*

> ⚑ **A STALE PARENTHETICAL WAS DROPPED HERE.** The old `run_share_add` docstring said the two root
> tables *"deliberately excluded it (`_launch_snapshot_inputs` and `_print_effective_shares` set the
> workset arms only `if not is_default`)"* — present tense. Neither root table exists any more:
> `_print_effective_shares` has no root-join at all (see below), and `_launch_snapshot_inputs` at
> HEAD has no `is_default` workset-bindings arm. The correct, PAST-tense statement of the same fact
> is the one carried above, which is how `_print_effective_shares`' own docstring already phrased it.

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
* **`--effective`** (`_print_effective_shares`): resolves through the committed KeyStore snapshot
  pipeline (`assemble_levels → merge → expand → snapshot_category_entries`) scoped to the workset
  file — the SAME resolver the launch uses. This replaced a retired `resolve_shares` /
  `read_bindings` / `LevelView` path; `resolve_shares` and `read_bindings` are gone from the tree,
  while `LevelView` itself survives and is still live in `settings/paths.py` and
  `settings/settings_resolve.py` — it is only this function's use of it that was retired.

  Every stored `host_src` resolves ON ITS OWN (spec §2a), because `share add` absolutises a relative
  source at WRITE time, so there is no root to apply here. This display therefore cannot diverge
  from what a launch mounts, which it previously could.

`_print_effective_shares` builds its context from the **resolver SPLIT** (spec §1A / JC-2): Layer-1
`config.*` becomes the `ctx.config` foundation, Layer-2 `system.*` becomes the snapshot floor (flat
dotted keys, which `assemble_levels` explodes) so a share value's `@`-ref such as
`@system.channelroot` resolves from the snapshot itself, replicating the old `_lookup` map. The xdg
map must be the canonical FULL host map anchored on the resolved `std.data_home` — a data-home-only
partial map RAISES on a stored `$XDG_CACHE_HOME/…` value.

The DEST column is the share's IDENTITY (R-10) and is exactly the argument `share rm` takes.

### The retired name-keyed refusal

`_workset_raw_shares` REFUSES a retired name-keyed entry (R-10), and it does so on the KEY. P4′
could compare the key against the value's own stored destination; P6 dropped that element (R-6), so
the test is instead whether the key is SPELLABLE as a destination at all — absolute, or leading
`~` / `$` / `@` (spec §2a self-resolving).

A bare leaf like `docs` is a NAME, and there is no honest way to DISPLAY one: the DEST column would
print a name, and the `share rm` argument it advertises would be a name too. So it is named and
refused rather than silently mis-rendered (Code Convention 0; disk-store R-8's posture). It raises
`SettingsError`, which `run_share_list` reports rather than letting a traceback out of a listing
command.

The cure the error prints is deliberately spellable: `share rm` deletes whatever key it is given,
because `normalize_bind_dest` passes a bare leaf through unchanged. *(Verified end-to-end: a
hand-written `docs:` key makes `share list` print the refusal, and the prescribed
`workset share rm <ws> docs --mode rw` removes it verbatim.)*

### The arity trap

**KNOWN, RULED GAP — a retired 2-element `[src, dest]` under a DESTINATION-shaped key is
UNDETECTABLE** in `_workset_raw_shares` and reads as `BindEntry(src, opts=dest)`. That is the trap
the spec names outright ("the retired 2-element `(host_src, box_dest)` and the current 2-element
`(host_src, options)` have the SAME ARITY"), and it is accepted because R-4 rules NO MIGRATION:
nobody has such a file. Do NOT invent a mount-options grammar here to close it — that would be a new
refusal nobody ruled.

### The dead options bracket

> ⚑ **A FALSE CLAIM WAS DROPPED HERE, NOT RELOCATED — and it points at a live display defect.**

`_share_source_display`'s old docstring said it renders "the host source, plus any per-entry mount
options in brackets", and its inline comment said "Element 1 is the stored destination (already the
DEST column); anything beyond it is the per-entry options override." **Both are false under the live
entry shape.**

* Element 1 is the **OPTIONS** (`BindEntry.opts`), not the destination. The destination is not in the
  entry at all — the store refuses a 3-element entry with exactly that message.
* `_workset_raw_shares` emits `[leaf.src, leaf.opts]` or `[leaf.src]`, so `value[2:]` is **always
  empty** and the bracket is dead code. Per-entry options are never shown in the raw listing.

Both claims are residue of the retired 2-element `[src, dest]` era, in which options WOULD have been
element 2 and later. R-6 moved options to element 1 and the slice was never moved with them.

*(Proven on the production path, not by reasoning: a hand-written
`workset.bindings.ro: {/box/withopts: [/host/src2, "ro,noexec"]}` prints
`  /box/withopts   ro   /host/src2` — no bracket. The same entry under `--effective` renders as a
mount, so the entry itself is live and only the raw view drops its options.)*

The source now carries a one-line `⚑` marker at the slice so a future reader does not take it for
live behaviour. **The code was not changed** — the fix is a decision, not a prose pass.

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
Pre-flight the mould, create + register the workset, stamp it, then MERGE the create-time cascade
settings into its file. See "`create`: pre-flight, then stamp".

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
"`connect`: the J2 write-ahead journal".

```python
def run_disconnect(args: argparse.Namespace) -> int
```
Drop a member, optionally removing its files. See "`disconnect`: subject reconciliation and the
OSError arm".

```python
def run_info(args: argparse.Namespace) -> int
```
Print name, root, creation date and members.

```python
def run_set(args: argparse.Namespace) -> int
def run_reset(args: argparse.Namespace) -> int
def run_get(args: argparse.Namespace) -> int
def run_show(args: argparse.Namespace) -> int
```
Argument shims: each normalises `args` into the shape `_run_workset_config` expects and delegates.
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
Load the workset `settings.yaml` as a nested dict (missing → `{}`).

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
second resolver". Note the empty-config early return runs BEFORE the `--effective` branch, so a
workset with no bindings prints the same one-line message either way.

```python
def _workset_raw_shares(ws_config: Path) -> dict[tuple[str, str], object]
```
The workset file's `workset.bindings.{ro,rw}` as a `{(mode, dest): raw}` map. See "The retired
name-keyed refusal" and "The arity trap". `assemble_levels` returns
`[box, workset, agent.<active>, agent.default, system, base]`, so index 1 is the workset partial —
the only file passed.

```python
def _print_effective_shares(ws, std, ws_config: Path) -> int
```
Resolve and print the workset's bindings as launch-time mounts. See "Listing: single route, no
second resolver".
