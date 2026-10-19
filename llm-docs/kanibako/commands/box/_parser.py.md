# The `box` Verb Tree

`box` is where a box's whole lifecycle outside a launch lives: it is MADE (`create`), enumerated
(`list` / `ps`), inspected (`info`), unregistered and deleted (`rm`), brought back (`register`),
and configured (`set` / `reset` / `get` / `show`). `add_parser` also NESTS the verbs that live in
their own modules — `move` / `convert` / `remap` / `duplicate` from the sibling `_lifecycle` and
`_duplicate`, and `archive` / `purge` / `extract` / `vault` / `helper` / `fork` / `start` /
`shell` / `stop` from `commands/`.

Two facts shape almost everything below. First, **registry MEMBERSHIP is the seed signal**: a box
is seeded ONCE, atomically with `create`, and every later index operation must therefore be
INDEX-ONLY — a re-register that re-seeded would clobber the user's home. Second, **`rm` without
`--purge` retains the metadata** and parks a `deregistered` entry, so a name can outlive its
membership; the guards that make that safe are the bulk of this module's care.

Authority: the keyspace spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0 (closed keyspace, single route), §1A
(CLI level and the create exception), §2h (`pref.*` requests); the box-lifecycle design items I2
(readopt) and I4 (the home-reuse data-loss guard); the J1 lifecycle journal.

## The verb surface

`box` is a released public interface — every flag, alias and help string below ships.

| Verb | Aliases | What it does |
|------|---------|--------------|
| `create` | — | Make a box: probe, gate, materialize, seed, register (standalone: only on `--register`) |
| `list` | `ls` | Every known project + status; the DEFAULT when `box` is given no verb |
| `ps` | — | `list` filtered to active boxes |
| `remap` | — | Records-only relocation (you already moved the folder) |
| `move` | `mv` | Physically relocate the workspace + update records |
| `convert` | — | Change which mode/workset owns a project |
| `duplicate` | — | Copy workspace + metadata under a new path |
| `rm` | `delete` | Unregister; with `--purge` also delete the metadata |
| `register` | — | Readopt a deregistered box, or index a standalone box on disk |
| `info` | `inspect` | Per-box status, paths, container, agent, credentials |
| `set` / `reset` / `get` / `show` | — | The box-scope config verbs |
| `diagnose` · `helper` · `fork` · `archive` · `purge` · `extract` · `vault` · `start` · `shell` · `stop` | — | Delegated to their own modules |

⚑ Flag casing is a ruled convention: UPPERCASE short flags are agent flags, lowercase are
infrastructure. `-i`/`--image` survives on `create` only (`--rig` is its synonym); `--image` is
long-only on `start`/`shell`.

⚑ `--default` / `--standalone` / `--workset <ws>` are one mutually-exclusive group, attached by
`_add_target_group` — OPTIONAL on `move` (no flag = owner unchanged), REQUIRED on `convert`.

⚑ `duplicate --to` is a different surface: it takes a MODE, and `_MODE_CHOICES` is derived from
`BoxMode` (`primary` · `named` · `standalone`), never a hand-kept list. The pre-1.8.0 spellings
`default` and `workset` are not modes; argparse refuses them (rc 2, naming the valid choices) and
nothing translates them.

## `create`: the ordering IS the design

`run_create` is a sequence of gates whose ORDER is load-bearing at almost every step. Reordering
any of them reopens a bug that has already been paid for.

0. **`precheck_create`**, settings-free, before first-run setup: validate `--name` AS TYPED (spec
   §0 NAMING RULES; `validate_box_name`, with `box_name_cure`'s spelling as the cure); refuse an
   unreachable target by name (`stat_unless_gone`); and the `$HOME` guard — a home-directory
   project mounts the entire home tree, so it must be BOTH standalone AND an explicit
   `--allow-home`. Default mode at `$HOME` is never permitted.
1. **Standalone, before any write**: `import_reconcile.check_standalone` refuses another spelling of
   a registered or parked standalone root ([R188]); the directory leaf must have an ASCII spelling
   (`sanitize_cap`); a `--name` other than the name the box gets is refused
   (`refuse_nonleaf_standalone_name`) — standalone identity is machine-generated, and `--register`
   does not make `--name` source the registry entry; then the scalar-section and inherited
   per-owner refusals.
2. **The workset path space** — the named workset whose root holds the CWD. There, a member NAME
   creates a named member (`_plan_workset_member`); any other target inside a named workset's
   space, by path or by the cwd, refuses (`_create_in_workset_space`).
3. **The same-kind name guard**, for an explicit primary `--name`: `check_primary_box_name_free`,
   run HERE so it refuses BEFORE the box dir and seed materialize, then the I4 home guard
   (`_assert_primary_home_free_for_create`, below). Box and workset names are separate namespaces
   (system-design § Detection & import); only a same-kind collision refuses.
4. **The path's conflict arm** (`check_workspace_not_named_box`), and, for a new primary box with no
   `--name`, the box-name rule on its directory's derived name (`refuse_derived_box_name`).
5. **The non-materializing probe** (`initialize=False`, no `mkdir`). `_already` is captured, the
   probe is named (`_name_new_box_probe`), and the pending create entry is read.
6. **The recovery refusal** (`_create_recovery_refusal`). RECOVERY IS NEVER IMPLICIT: a pending
   create entry is refused unless `--recover`, and `--recover` with nothing pending is refused. It
   runs ahead of every door below, so a flag about to be refused never reaches the persona
   pre-flight, the agent persist, or the seed.
7. **The pre-journal fork** (`_orphaned_primary_box_dir`): a crash that left a primary box dir but
   nothing for `--recover` to find.
8. **The nesting refusal** (`_create_nesting_refusal`): a workspace inside another box's, or holding
   one (keyspace § 0 "Per-owner resources").
9. **The persona pre-flight and the persona-grata STORE check**, on the probe, so an unloadable
   persona refuses with NOTHING on disk; the store check runs before the create verdict, so a store
   that cannot yield a usable persona is reported as ITSELF rather than as the verdict's downstream
   "no endpoint configured".
10. **The already-initialized refusal**, HOISTED above the materializing resolve.
11. **The create entry**, CLAIMED before any box write (`_claim_create_entry`), recording the
    create's `state`.
12. **The membership write** (a named member, `add_project`), then **the materializing resolve**,
    with `register=False`, the fresh-create writes, the seed, the canon skeleton, the register, and
    the clear.

The J1 order is: write entry → seed → canon skeleton → register → clear entry. ⚑ The register step
is CONDITIONAL for a standalone box (I3/§D4a): `_register_new_box` runs for every PRIMARY box, whose
membership IS its workset, but for a standalone box only on `--register`. The default leaves an
independent, unindexed box that `kanibako box register <path>` adopts later, index-only and
seed-free. The gate lives at this call site, not inside `_register_new_box`, because the flag is a
create-verb concern. A created box then reports its own state: an unregistered one prints the
`box register` cure, gated on the REGISTRY rather than on the flag so a recovery of an
already-registered box stays quiet.

### Why `_already` is captured where it is

`box_tree_materialized(_probe)` is read BEFORE `_name_new_box_probe`, which MUTATES `_probe.name`. A
NAMED member's answer is whether its box dir exists, read before any write.

### Why the already-initialized refusal is hoisted

The materializing resolve runs with `initialize=True`, and its recovery arms re-create a missing
home (`resolve_project`) and a missing home + workspace (`resolve_standalone_project`) BEFORE
returning — so a refusal after it would bootstrap a deleted home and THEN say "already
initialized", false at the moment it printed. Asking the NON-materializing probe makes the message
TRUE: nothing has been written when it prints.

### Interrupted-create recovery is `--recover`

A box that carries a pending create journal entry is a half-completed create (a crash between the
entry and its clear). **The journal entry, not `is_new`, drives recovery**, and recovery is never
implicit: a plain `create` refuses with the `--recover` cure, and a launch refuses too
(`start._interrupted_create_error`) and never completes a create. `box create --recover` replays
the recorded `state` — a create-shaping flag given again is refused, even when it agrees, because
the entry is the only record of what attempt one was told. `_pending_create_entry` keys on the box
dir, which the probe resolves identically to the materializing call.

### The J1 write-ahead journal

The steps are write-ahead: write the create journal entry (intent), seed the home
(create-if-absent), THEN register (deferred by `register=False`), then clear the entry — clearing
is the IMMEDIATE step after the registry write. A crash anywhere before the entry is cleared
leaves it, so `create --recover` re-seeds, completes registration and clears the entry:
forward-recovery, not rollback. `_register_new_box` is register-if-absent, so recovering a box
that was already registered (a crash in the register → clear window) is a no-op plus the clear. If
register raises a genuine collision the entry is intentionally LEFT (the box is incomplete) and
propagates.

`register=False` is what gives the invariant "registered ⇒ fully seeded": the resolver creates the
box dir + meta and sets `is_new`, and only the registry write is held back to the caller. On a
RE-CREATE of an interrupted box, the `register=False` import HONORS the flag (it resolves the box
name from on-disk meta without registering), so the box resolves with `is_new` False but WITH a
pending create entry — the recovery signal.

The persona load-or-error gate ran as a TRUE PRE-FLIGHT above, before box-dir creation, so by the
time the journal opens the persona is known loadable. It STILL precedes the write-ahead entry: an
abort after the entry would leave a pending entry whose recovery replays the seed.

The canon skeleton (J-7) is materialized AFTER the seed and INSIDE the journal window. After,
because the seed writes `canon/{notebook,workbook}` under the same root this step makes 555 —
protect first and those copies die with `EACCES`. Inside, because an interrupted create must
replay it like every other step.

### What a FRESH create persists (and a recovery does not)

`if proj.is_new:` guards the image, `--private` and `--agent` writes plus the standalone
`.gitignore`. A recovery re-create reuses the half-built box's already-written meta: the on-disk
record is authoritative and must not be overwritten with possibly-different args.

* **Image and `--agent`** — the §1A CREATE EXCEPTION (R-11a) goes through the ONE helper
  `config_interface.persist_creation_level`, the same one the launch-materialization path in
  `start.py` calls, so the persist rule has exactly one home. A refused write raises and the create's
  cleanup undoes the box. Only an EXPLICITLY-GIVEN `-i`/`--image` persists: a
  no-flag create bakes NOTHING into the box tier, and the box resolves the live cascade for its
  image, so later default changes still reach it. The write target is the BOX-TIER file from the
  ONE pair (M-8) — for standalone, `box_data/box.yaml`, the same file `box set box.image=…`
  writes and the launch cascade reads as the box tier. Deriving it independently here is exactly
  the split M-8 exists to prevent.
* **`--private`** — turns the box private BEFORE the home seed runs, so the host OAuth cred is
  never forwarded. Both box-scope auth toggles are persisted OFF through the SANCTIONED settings
  write (`set_config_value`, never a raw YAML dump), the same box-scope path `config set` uses, so
  the keys land in the `box.auth.*` slot the launch snapshot reads. `project_toml` IS the box-tier
  file from `box_workset_settings_paths` — the same file `seed_new_box`'s `resolve_auth_source`
  reads through the snapshot's box tier — so the seed then resolves `tier="box"`
  (`source_root=None`) and `seed_cred_files` no-ops. ⚑ AUTH-CRITICAL that the two agree: if this
  wrote a file the snapshot did not read as the box tier, a supposedly-private box would resolve a
  sharing tier and leak the host OAuth token into the seed. The write is additive: no scrub, two
  boolean overrides. `set_config_value` RETURNS an `"Error: …"` string and never raises, so an
  unchecked call would leak SILENTLY — hence the hard `KanibakoError` before the seed runs.
* **`--agent`** — persists the §2h REQUEST `pref.system.agent` (through the helper above, which
  uses the sanctioned `set_config_value` write), so a plain `start` resolves this agent through the ordinary
  pref path instead of falling through to the system default. ⮕ P7: this used to write the RETIRED
  `box.agent_name` (spec §2b); a pref is how a box influences a key that resolves above it. It
  applies to ANY selector, persona or plain — `create --agent goose` must make a plain start launch
  goose too. The RAW user ref is stored (selection canonicalizes on read, exactly as for a
  hand-set key), and `start --agent <other>` on an existing box remains an ephemeral override on
  top; only a launch that creates the box saves it.
* **`.gitignore`** — standalone only, written at the project ROOT (`metadata_path`), where
  `box_data/` and `vault/` live and need ignoring. `project_path` is the workspace SUBDIR, not the
  root. Its messages (`project already initialized in …`, `Created …`) and `box info`'s `Project`
  row print `<None>` for a standalone box whose root nulls `workset.workspaces` (Q106 review).

⚑ **`--agent` has a SECOND consumer outside the guard, and that is why it is normalized once.**
`seed_new_box` takes an `explicit_agent`, and the seed runs after the journal write — outside
`if proj.is_new:` — so on a recovery re-run a raw `args.agent` steered the seed while the persist
above could not move: the home was seeded for the new agent and the settings still named attempt
one's, with no way back (the home bind owns the content from create onward and nothing re-seeds).
`_agent_arg` is therefore computed ONCE, right after `is_recovery` is known, as
`None if is_recovery else args.agent`, and the persona store check, the create verdict, the persist
and the seed all read that one name. `None` sends each of them through the ordinary cascade, which
reads the very `pref.system.agent` the persist wrote — so the seeded agent and the configured agent
cannot disagree. Spelling `args.agent` at any of those four sites again reopens the defect.

And "given" has ONE spelling: `_agent_arg is not None`. The store check once asked truthiness while
the persist asked `.strip()` truthiness, which are different questions — `--agent "  "` cleared the
first and was dropped by the second. A flag the user typed is given even when its value is blank, so
a given ref is VALIDATED through `parse_agent_address` (the owner of charset, pseudo-agent reservation
and empty-after-strip) and refused by its message — the ADDRESS grammar, because `--agent` selects an
agent rather than naming one: `--agent shell` creates a plain-shell box (keyspec §2b), while
`shell` inside a composite and `default` stay refused; it is never read as "resolve from settings",
which would steer the box to an agent the user did not ask for without saying so. The validated ref
is stripped once at that same site, so no consumer normalizes again. `None` keeps its one meaning —
recovery, or no flag at all.

Explicit-create: `create` MAKES the box but does NOT launch it. A launch (`start` / bare
`kanibako` / `code` / `shell`) no longer auto-creates, so the closing hint names the verb.

## Names, homes, and the I4 data-loss guard

`create --name X` materializes the box at `std.boxes/X` with `mkdir(exist_ok=True)`, so if that
home is ALREADY occupied the create MERGES into the existing box's data instead of failing.
Combined with the retained metadata of a `deregistered` box, a later `rm X --purge` (which
resolves the deregistered entry) would then delete the FRESHLY-CREATED box's home — silent data
loss. `_assert_primary_home_free_for_create` refuses up front and names the recovery.

It detects two states at `std.boxes/<name>`:

* **DEREGISTERED** — an entry parked by `rm` without `--purge`; the exact hazard. Recoverable via
  `register`, deletable via `rm --purge`.
* **ORPHANED metadata** — a dir with NO active membership and NO deregistered entry (a hand-left
  dir, or a create that crashed before its write-ahead journal entry). Neither `register` nor `rm`
  resolves it by name today, so the guidance points at removing the dir or choosing another name.

A genuine half-create being RE-RUN (a pending `create` journal entry keyed by this home) is the
sanctioned recovery re-entry and is NOT refused — `run_create`'s recovery path owns it.
ACTIVE-name collisions never reach here: `check_primary_box_name_free` raises first.

⚑ **The order inside the guard is the guard.** The deregistered refusal MUST precede the
pending-create allow. Otherwise a STALE `create` journal entry — left by a register → clear-window
crash that `rm` does not clear — would FALSE-ALLOW a `create --name X <new path>` to merge into the
deregistered box's retained home, reopening the window. A legitimate half-create has NO
deregistered entry, so this ordering never refuses one.

The guard fires UNCONDITIONALLY of the `register` flag: `run_create` always resolves with
`register=False`, deferring the up-front name-uniqueness check, so this is what closes the reuse
hole on the deferred path.

## `rm`, `register`, and the deregistered section

`rm` resolves its target in a fixed order: the primary membership by name, the global `worksets:`
index by name, then by PATH (primary reverse-lookup, then the worksets index), then STANDALONE
(not in the name index at all — it lives in `registry.standalone`, box name → root), and finally
the global `deregistered` section by name.

That last arm is the reported-bug fix: after a plain `rm` the active membership is gone, so
`rm <name> --purge` and a re-`rm` must resolve the retained metadata there rather than erroring
"not registered".

PRIMARY boxes unregister from the primary per-workset `boxes:` MEMBERSHIP (the sole store since
the global `projects:` section retired); worksets unregister from the global index. Routing the
primary arm to membership is what closed the pre-existing gap where `rm` left the membership
stale. Worksets are NEVER parked in `deregistered` — they keep their own lifecycle.

`register` is the inverse and is **INDEX-ONLY and SEED-FREE**: it writes the active membership
index and drops the deregistered entry, and NEVER touches the box's home content or re-materializes
templates. Membership is itself the seed signal, so a re-index must not re-seed. It unifies two
operations (design I2): READOPT a deregistered box, resolved by NAME first, and REGISTER a
never-registered STANDALONE box that exists on disk (`box_data/` + root `workset.yaml`) with no
index entry, resolved by PATH. Worksets are refused with a redirect; a live box gets a clean
"already registered" at rc 0.

The standalone register-later arm REUSES `import_standalone` rather than writing an index entry
inline: it is already index-only and seed-free (the box was seeded where it was created), already
composes the kuid-first name, and already refuses a name collision to a different root.

Conflict safety comes from the reused registration APIs, not from checks here.
`check_primary_box_name_free` / `register_primary_box_name` enforce `$HOME`, the SAME-kind
active-box name (case-blind), and the one-box-per-workspace-path invariant (`register_workset_box`'s
one-box-per-path guard); worksets are not consulted, since box and workset names are separate
namespaces. A violation refuses cleanly with guidance. The standalone arm checks the name against a different root itself,
and its re-register overwrites a matching name→root. A readopt therefore never clobbers a live
box. Both arms self-heal a stale entry whose metadata is
gone: drop it, report nothing to restore.

## Purge safety

A `deregistered` entry stores a metadata path that a later `--purge` DELETES, and that stored path
is UNTRUSTED — a corrupt or crafted registry must never let purge delete outside a box's own
metadata. `_assert_deletable` refuses (rather than silently no-op-deleting) when the resolved path
is empty, `/`, `$HOME`, or — when `must_be_under` is given — not a STRICT descendant of that root.
`resolve()` collapses `..` and follows symlinks, so a `..`/symlink escape resolves OUTSIDE the root
and is rejected. Strict containment matters because deleting `std.boxes` itself would take every
box with it.

`_purge_deregistered` also carries an I4 belt-and-suspenders check: the name may have been REUSED
by a new ACTIVE box now occupying the SAME metadata path (e.g. a create that re-claimed
`std.boxes/<name>`). Purging by the stale entry would delete the LIVE box's home, so it refuses
AND drops the stale entry — the entry is definitively wrong, since an active box owns that path, so
it must stop shadowing. The active box's home is never deleted.

Every purge runs as a plan of `_PurgeStep`s: a per-kind planner lists the paths, `_confirm_purge`
prints them (and what the run keeps) and asks unless `--force`, and `_run_purge_plan` deletes them
in order. The active `rm --purge` paths, the deregistered `rm --purge` path and the `purge` command
(`commands/clean.py`) share the two planners, so they delete the same paths in the same order:

* `_primary_purge_plan` lists the PRIMARY vault `ro`/`rw` leaves (which are NOT under
  `metadata_dir`), the box's snapshot store, the per-box logs keyed by the registry name, and
  finally the box dir as the ANCHOR. A looped vault leaf or store is kept and named after the run.
* `_standalone_purge_plan` lists the logs, an unheld creds-watcher lock, kanibako's `.gitignore`
  line, the removable canon, the removable vault arms, an in-tree snapshot store, the box store as
  the ANCHOR, and LAST the ROOT `workset.yaml` (the WORKSET tier), then `registry.yaml`'s standalone
  mark (system-design § "Detection & import"), so they go after the anchor: if an earlier step fails, a retry still resolves the box's repoints. It
  returns the steps plus a *retained* list (paths outside the box's root, a kept canon or vault,
  the channel dirs), each reported after the run. The user's workspace files, and `root` itself,
  are never touched.
  ⚑⚑ **The plan resolves everything BEFORE anything is deleted or unregistered** — the vault
  split via `project.workset.standalone_vault_teardown`, the canon split via
  `standalone_canon_teardown`, and the store via `standalone_store_teardown_plan`. An
  unresolvable repoint RAISES there, while the box is still whole.

A GATED step is kept once the anchor could not be removed, and the anchor itself is kept when any
earlier step failed, so re-running the purge can finish.

A purge step is removed by `kanibako.runtime.container.remove_path`: a link loses only the link,
and a folder goes through `remove_box_tree`, which EVERY box-tree deleter shares — `extract`,
`move`, `duplicate` and `purge` all need the same `podman unshare` escalation, and they
need it on every box (the canon skeleton is root-owned by construction).

## The listing

`run_list` merges FOUR sources, because no single index holds them all:

1. `iter_projects` — default-mode boxes.
2. `iter_workset_projects` — workset members.
3. `registry_store.load_standalone` — STANDALONE boxes, which neither `iter_projects` nor
   `iter_workset_projects` returns and which would otherwise be invisible. The two miss them for
   DIFFERENT reasons, and only one of the two is about membership. `iter_workset_projects` walks
   `ws.projects`, which IS the workset's `boxes:` table, and a standalone box holds no `boxes:` row
   anywhere. `iter_projects` never consults a membership table to decide WHAT to list: it
   enumerates the metadata dirs under `std.boxes` and reads the primary `boxes:` table only to fill
   in the workspace column. A standalone box keeps its metadata dir under its OWN root rather than
   as a child of `std.boxes`, so it is invisible there — and would stay invisible even if it did
   carry a `boxes:` row.
4. `registry_store.list_deregistered` — surfaced in a dedicated section, with `deregistered` as
   the status, so a user can SEE what they may `register` or `rm --purge`; before that they were
   invisible. Shown only when entries exist, so a tree with none renders byte-identically.
   `list_deregistered` self-heals genuinely-stale entries and, with none parked, returns `{}` and
   writes nothing. Deregistered boxes are never active, so they are skipped entirely under an
   active-only filter (`--active` / `ps`). Under `quiet` the section prints bare names.

Cross-source dedup collapses rows sharing a `(name, resolved path)` key, so a box double-registered
under the same workspace path prints exactly once. The NAME column floors at 18 (the historical
fixed width), grows to the longest displayed name and caps at 40, so a long name like
`ai-java-course-materials` does not overflow.

`--orphan` short-circuits to `_list_orphans`: default-mode orphans are boxes whose path is missing
or which have no breadcrumb; workset orphans are members whose workspace directory is gone.

## `box info`

`info` routes its subject — the positional path OR the `--box` value — through the unified
path-or-name resolver with NAME precedence, the same way the sibling box commands do, so a bare
registered box NAME selects that box instead of being read as a (nonexistent) relative directory.
The old premature `Path(raw).is_dir()` check rejected every name.

When there is no metadata on disk, "no box directory" collapses TWO states that need different
words. A box with a NAME is REGISTERED: its directory is gone, which is not an unused directory —
it is exactly what a launch refuses (MBR-6), and the cure is MODE-DEPENDENT (`create` for a PRIMARY
box, `workset disconnect` + `connect` for a NAMED one). `info` therefore DEFERS to
`_unbuilt_box_error`, the launch's own refusal, rather than restating it, so the two can never name
different cures. For a genuinely unused default-mode directory it says a launch will not create a
box — ⚑ NOT "start a session with `kanibako start`": since the v1.7.0 explicit-create gate a launch
NEVER materializes a box, so naming `start` described behavior we do not have.

Agent resolution here is INFORMATIONAL, not an agent-requiring launch, so a failure (`system.agent`
unset, `system.agent` null / no default set, adapter missing) DEGRADES to `n/a` rather than
erroring. It still goes through the ONE selection seam, `agent_select.select_agent` (`system.agent`
< workset pref < box pref). ⚑ The Agent row shows WHERE THE SELECTION CAME FROM (P7): "which agent
does this box run, and WHY?" is the question a retired-key or wrong-default box makes urgent, and
the answer is in no single file — it may be the stored `system.agent`, a workset/box
`pref.system.agent`, or `--agent`. `AgentSelection.source` already carries it, and this is the one
place a user can read it. A refused RETIRED key (migration M-4) likewise
surfaces here rather than being swallowed as "unresolved", because `box info` is where a user looks
when a box will not start.

## The config verbs

`run_set` / `run_reset` / `run_get` / `run_show` are thin entry points: they normalize their
per-verb `Namespace` into the shared shape `_run_box_config` expects and thread the same context.
The `config_interface` engine is unchanged by them — the `config.*`-forbid guard (B2), the
scope-direction guard (B4/R2) and the full-cascade set-time validation (Q9) all still apply,
because every set still routes through `set_config_value` with `command_scope=ConfigLevel.box`.

Positionals are `[project] [key[=value]]`, disambiguated by the known-key heuristic: a lone token
containing `=` or answering `is_known_key` is a KEY, otherwise it is a project name. `reset` uses
the same heuristic over `[project] [key]` and then rebuilds the engine shape, with the key (or the
`__ALL__` sentinel) riding on `args.reset`. `--box` also names the subject and is reconciled with
the positional by `resolve_subject_value` (same → warn, differ → error).

⚑ **The M-8 chokepoint.** `get` / `set` / `show` / `reset` all address the box through the ONE
`box_workset_settings_paths` pair, so a box-scope write and the read that follows it CANNOT
disagree. The box's docker `env` FILE tier is GONE (R-39/RQ-1); the env family is the settings key
`box.env.<VAR>`, which lives in that same pair's file. The `reset` arm takes both halves from that
one pair so it clears exactly the file `set`/`get` address.

`set` names the BOX as its target (`std=std, proj=proj`, spec §2a): `set_config_value` builds the
box's own `resolve_inputs`, so its settings files, its real `{meta.box.path}` and its workset anchors
come from the resolve the launch runs, never from the file positions. The caller still threads its
tier files (`cascade_workset_path`, `cascade_box_path`), so the target-less fallback reads the same
files as the target and differs only by the floors. `reset` threads the same pair so its
cleared-message can honestly name the now-effective value and its source tier. The create-time
`--private` and `--agent` writes name the box being created the same way, tier pair included, with no
agent selected yet. The active agent name is resolved best-effort: it selects the `agent.<active>.*` sub-table
the OTHER cascade files may carry, and a resolution failure just leaves it empty, degrading the
message to the cleared-only form. Neither call passes `cascade_agent_path`, so it defaults to
`None`; the per-agent file stores behavior FLAT, so `assemble_levels` reads no category subtree
from it.

### `--effective`

The `--effective` display resolves the PATH-DELIVERY categories and their materialized derivations
(§0) off the SAME single launch pipeline a start takes, so it cannot drift from what actually
mounts. Three properties are deliberate:

* **A collision is REPORTED, never raised.** This display IS the M-7 detection recipe — "resolve
  the snapshot and look for duplicate dests" — so dying on the very fault it exists to surface
  would be backwards.
* **`guarantee_create=False`.** A DISPLAY verb must not write to disk: the core table's vault
  create-if-missing is a LAUNCH guarantee, not a read one. The binds are emitted either way.
* **The persona-store tier is the SAME one the launch resolves against.** The store supplies
  `secret_path.<VAR>`, a PATH-DELIVERY category; without the tier this view would list a persona
  box's mounts MINUS the token the launch actually mounts, and minus its `env` passthrough. It is
  tolerant and `None` for a bare agent, so a store-less box renders exactly as before.

⚑ **The resolve's `LaunchDeliveries` is KEPT, for its `declared_by`.** Because this display folds in
process, per command, the collapse's record of which declaration took each destination is live right
here — and it is on no leaf and never can be (a fourth `meta.assembly.*` leaf is a closed-keyspace
addition). It is passed to `show_config` as `category_declared_by` and reaches
`config_display._print_category_block`, which is what lets a loss print *"the mask declared by
`'box.masks./opt/x'` at /opt/x covers this destination"* rather than a path with no key to go and
edit. Discard the carrier and the block silently degrades to the bare phrase.

The NODE-name keys the `agent.<node>.*` keyspace slot and the `agents/<node>/` dir; `with_harness`
reflects the RESOLVED target (fallback-safe) with the persona preserved, and for a bare,
as-requested agent it equals `target.name`.

`env_resolved` is composed by the SAME helper the launch uses (`_build_config_env`, a straight
projection of the collapse's arbitrated `<scope>.env.<VAR>` slots), so the display cannot claim an
env the box will not get — or miss one it will. It needs the RESOLVE, which is why it lands after
the snapshot rather than beside the agent state. ⚑ The helper took a second argument until MBR-1
P3 — the per-agent file's `self.env`, passed as an under-layer because it was on no cascade level.
It is an ordinary `agent.<node>.env.<VAR>` key now and arrives in the slots, so this call passes
the slots and nothing else.

#### `cli_level` — selection only, and the inert `--agent`

⚑ The display installs the SAME §1A selection level the launch installs (P7). Without it, this
view would resolve `{system.agent}` DIFFERENTLY from the launch it claims to show — an `--agent` box
would render `meta.box.auth.workset_path` with the agent segment dropped. A display that disagrees
with the launch is worse than no display.

⚑ SELECTION ONLY, and for a READ verb that is the WHOLE of the CLI level (P8). `box config` carries
none of the launch's ephemeral VALUE flags (`-M`, `-N`/`-C`/`-R`, `--image`, `--share-images` live
on `start`), so there is nothing ephemeral to install. Rendering a flag from some OTHER invocation
would be the "a flag mutated a stored value" failure §1A forbids, one screen removed: `--effective`
reports what the box IS configured to do, and a per-launch override is by definition not that.

⚑ `--agent` is the EXCEPTION, and it is a PRE-EXISTING DEFECT rather than a P8 decision. The
blanket flag injector puts it on every leaf parser, so `box config --agent goose --effective`
PARSES — but this call site, and the other four read-verb `select_agent` sites, never pass it
through, so it is silently IGNORED. It is advertised and inert. P8 left the behavior exactly as it
found it rather than quietly changing what a read verb reports; honoring it (or refusing it) is a
tracked follow-on.

### The `get` path

Bare `env.*` is RETIRED (R-39) and is refused at the HANDLER, not in the engine, because the get
engine returns VALUES and never error strings — the same handler-side split as the workset
bare-agent-key read guard.

A BARE agent behavior key at box scope has no box-writable value of its own: `get_config_value`
redirects the READ to the box's §2h request `pref.agent.<active>.<key>` (P7). The value is printed
under that canonical form so the read TEACHES the request, mirroring the refusal message `set`
prints. The box's resolved agent NODE is what names it; best-effort, and an unresolvable agent just
means no redirect.

### `_resolve_config_subject` — the `__unregistered__` phantom

`_resolve_local_dir` answers "no PRIMARY box is registered for this workspace" with the SENTINEL
`("", std.boxes / "__unregistered__")` — a name-assignment placeholder for the resolvers that go on
to pick a real name (`resolve_project`'s create block; `restore`, which rewrites it with the box's
real name). The config verbs pick NO name: they simply address the box the sentinel points at, so
`box set box.image=…` from a cwd that is no box used to WRITE
`boxes/__unregistered__/settings.yaml` and report SUCCESS — materializing a placeholder instead of
refusing.

It is refused HERE, at the verb's own seam, so the sentinel keeps working for the resolvers that
legitimately replace it. `mode is primary and not name` IS the sentinel: for a PRIMARY box an empty
name means the membership reverse-lookup missed, which is the only way that path is returned; NAMED
raises `WorksetError` for a non-member, and STANDALONE addresses its own in-tree files rather than
`std.boxes`. It raises `ProjectError`, the shape every caller here already handles.

## Functions

```_add_target_group(parser: argparse.ArgumentParser, *, required: bool = False) -> None```
Attach the uniform, mutually-exclusive ownership-target flags to *parser*.

`--default` / `--standalone` / `--workset <ws>`. Used identically by `move` (optional) and
`convert` (required).

```add_parser(subparsers: argparse._SubParsersAction) -> None```
Build the whole `box` subparser tree. *(No docstring in source; this is the module's parser
constructor.)*

Registers its own verbs, then nests the sibling and `commands/` modules' parsers under `box`, and
finally sets `run_list` as the default when no subcommand is given.

```_assert_primary_home_free_for_create(std, name: str) -> None```
⚑ DATA-LOSS GUARD (I4): refuse a `create --name` that would reuse a box home.

Raises `ProjectError` on a conflict; returns `None` when the home is free — the common case, which
leaves a normal create byte-identical. See "Names, homes, and the I4 data-loss guard" above for
the two detected states and why the internal ordering is load-bearing.

```_check_persona_store_for_create(agent_ref: str) -> str | None```
⚑ READ-ONLY create-side persona-grata store CHECK; an `"Error: …"` or `None`.

The create-side trigger (DESIGN §4): when the explicit agent ref names a persona whose
persona-grata store entry EXISTS, read the store and refuse the create NOW if it cannot yield a
usable persona — BEFORE the persona create verdict, so a broken store is reported as itself rather
than as the verdict's downstream "no endpoint configured".

⚑ NOTHING IS WRITTEN. The store is a LIVE resolution input (`read_persona_bundle` → the
launch's persona cascade level), and the agent settings file holds user-intent values only, so
there is nothing to persist. The store errors are hard refusals; the probe only warns.

Returns an `"Error: …"` message for the caller to print-and-refuse (malformed ref, unusable store
config, a store naming no endpoint), or `None` when there is nothing to do — a bare/plain ref, no
store entry, the harness not installed, or a harness with no persona reader, all of which fall
through to normal create behavior — or when the store checked out (a soft token-pointer warning is
printed here).

`bundle is None` cannot happen (the entry was located a moment ago) but costs nothing to honor.
`no_reader` DOES happen: a goose persona is configured entirely through the keyspace and may own a
store dir purely for its `.secret_path`, so "this harness cannot read a store config" is a
fall-through, never a refusal.

⚑ WARN-ONLY applies to the two ANSWERED non-PASS verdicts. `NOT_APPLICABLE` means nothing was
learned about the token and nothing will be for this input — a harness with no probe, an unreadable
token, an endpoint that requires a model this persona does not name — a valid configuration nobody
can act on, so it goes to the log, exactly as on the launch path
(`kanibako.commands.start._persona_probe_error`). ⚑ A persona that simply names NO MODEL is still
PROBED, with the model field omitted, so a dead token still warns here.

The probe contract is never-raise, and plugins are held to it: a plugin bug IS an anomaly, so it
becomes an inconclusive verdict that warns rather than going quiet.

🛑 **The refusal warning names WHAT WAS REFUSED, never the token.** A 403 on a model the account may
not use is indistinguishable here from a dead credential, and the wording this replaced told users
to fix a valid one. The evidence block is rendered by `PersonaProbeOutcome.evidence_block()` — the
same call the launch error makes, so the two shapes cannot drift.

```run_create(args: argparse.Namespace, outcome: _CreateOutcome) -> int```
Create a new kanibako project (replaces `kanibako init`).

Wrapped by `_unmakes_its_dirs_on_failure`, so callers call `run_create(args)`: the wrapper passes
a fresh `_CreateOutcome`, removes the empty dirs a create that never committed made, and turns an
`OSError` into exit 1 with its cure line.

The full ordering, the J1 journal and the fresh-create-only persists are documented in "`create`:
the ordering IS the design" above.

```run_ps(args: argparse.Namespace) -> int```
List running boxes — `run_list` with active-only filtering.

`ps` shows active boxes by default; `ps --all` / `ps -a` shows all boxes, orphans included, which
makes it equivalent to `list --all` rather than to a plain `list` (a plain `list` hides orphans).

```run_list(args: argparse.Namespace) -> int```
List every known project and its status. *(No docstring in source.)*

See "The listing" above for the four sources, the dedup rule and the column widths. With no
runtime available every project shows as stopped.

```_norm(p: object) -> str```  *(nested in `run_list`)*
Normalize a path (Path or str, possibly None) for row-identity keys.

```_list_orphans(projects: list, ws_data: list, std, quiet: bool) -> int```
List only orphaned projects (the `--orphan` handler).

```_assert_deletable(path, *, must_be_under: Path | None = None) -> Path```
⚑ DESTRUCTIVE-SAFETY gate: validate *path* is safe to `rm -rf`, return it resolved.

Raises `ProjectError` on any violation. The refusal set and the strict-containment rule are in
"Purge safety" above.

```class _PurgeStep```  *(frozen dataclass)*
One path `box rm --purge` deletes: listed before the question, then removed as listed. Fields:
`kind`, `what` (the listing label), `path`, and the flags `anchor` (the box metadata dir — the
retry address), `gated` (kept once the anchor could not go) and `edit` (an in-place edit, such as
stripping kanibako's `.gitignore` line, rather than a delete).

```_primary_purge_plan(std, name: str, metadata_dir: Path, loop_keeps: "list[tuple[str, Path, str | None]] | None" = None) -> list[_PurgeStep]```
What purging a PRIMARY box deletes: box dir, vault ro/rw, per-box logs.

The anchor (the box dir) is appended LAST. A looped vault leaf or store is kept and pushed into
*loop_keeps*, which also gets the box's channel dirs (`box_channel_keeps`); the caller names them
after the run. Steps whose path holds a recorded workspace are dropped and named. ⚑ The CALLER
containment-validates *metadata_dir* first when it comes from an untrusted `deregistered` entry —
see `_assert_deletable`.

```_standalone_purge_plan(root: Path, registered_name: str, *, std: StandardPaths, early: EarlyScope) -> tuple[list[_PurgeStep], list[tuple[str, Path, str | None]]]```
What purging a STANDALONE box deletes: logs, store, root workset.yaml, canon, vaults.

Returns `(steps, retained)`; *retained* is reported by `_report_retained_roles`. Everything
resolves before anything is deleted or unregistered (see "Purge safety" above): an in-root
repoint is removed with the box, an out-of-root arm is retained and named, and an unresolvable one
raises while the box is whole. When the box has no store kanibako created, only the logs and the
creds lock are planned.

```_confirm_purge(steps: list[_PurgeStep], *, force: bool, retained: Sequence[tuple[str, Path, str | None]] = ()) -> bool```
List what *steps* deletes AND what the run keeps, then ask unless *force*.

The KEPT list prints before the question. A symlink step prints its target as kept. With no steps
it says the purge deletes nothing and returns True.

```_run_purge_plan(steps: list[_PurgeStep], std: StandardPaths) -> bool```
Delete *steps* in order; True when every step was removed.

A gated step is kept and reported once the anchor could not go, and the anchor is kept when an
earlier step failed. The `.gitignore` line, the leftover default canon folder (rmdir only, against
the live mold stamp in *std*) and a creds-watcher lock that is held again are handled by kind; every
other step goes through `remove_path`, and a failure prints the `podman unshare rm -rf` cure.

```_read_box_image(settings_file: Path) -> str | None```
The box's `box.image`: `None` if unreadable, refusing if `box` is not a table.

Captured into the deregistered blob for a later readopt (I2). An unreadable file degrades to
`None`; a scalar `box` section raises through `refuse_scalar_sections`.

```_read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None```
`_read_box_image` over a `(box_tier, workset_tier)` pair, box tier wins.

`box.image` is a BOX-scope key, so it is read from the box tier; the workset tier is consulted only
as the R2 downward-default. That fallback is DECLARED DESIGN (keyspec §2c), not a compat path: a
`box.image` set in a standalone box's ROOT `workset.yaml` is captured into the deregistered blob
too.

```_purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace, *, config) -> int```
Handle `rm <name>` when *name* resolves only to a deregistered entry.

Without `--purge` the box is already deregistered — print the recovery guidance and return 0, with
no re-error and no re-deregister. With `--purge`, validate the stored metadata path for
containment, refuse while the box is running (`_refuse_purge_while_live`, probed by kind), build
the per-kind plan (`_standalone_purge_plan` or `_primary_purge_plan`), confirm it, run it, then
drop the deregistered entry. IDEMPOTENT: an entry whose store is already gone is simply dropped,
with no error and no prompt. A purge that leaves the metadata keeps the entry and prints its cure.
The stale-active-owner refusal is described in "Purge safety" above.

For a standalone entry the stored `metadata` is the in-tree ROOT: only planned children are
deleted, but a bare or protected root is still refused. For a primary entry the metadata must be
strictly under `std.boxes`.

```_resolve_standalone_target(std, config, target: str, *, purge: bool = True) -> tuple[str | None, Path | None, bool]```
Resolve a `box rm` *target* to `(box_name, root, registered)`; unregistered by PATH only.

*target* may be a registered standalone box NAME (looked up case-blind in `registry.standalone`,
returning the stored spelling) or a PATH (resolved by ancestor-walk detection, then matched to its
registered root). A same-named PATH ranks first. An unregistered standalone box resolves by PATH
only, with `registered` False. Without *purge*, a row registered at the literal *target* resolves
even when it is another spelling of a registered box's identity: dropping that row is the cure.

⚑ **The blanket `except Exception` here treats any detection failure as "not this box" — except
`LegacyWorksetIdentityError`, `DerivedBoxNameError` and `ConfigError`, which re-raise**, and an
`ImportConflictError` re-raises unless the plain-`rm` literal row above applies. A workset root
still on the retired `workset.meta` spelling (MIGRATION.md § "A workset root no longer carries an
identity table; an un-migrated root refuses"), a directory with no ASCII spelling, or a malformed
root file is a named thing to fix, and swallowing it here would report the path as an unresolvable
box instead of printing the cure.

```_rm_standalone(std, box_name: str, root, args: argparse.Namespace, *, registered: bool, config) -> int```
Remove a standalone box: drop its registry entry if any, its store on `--purge`.

An unregistered box has no entry to drop, so a plain `rm` of one refuses and prints the
`rm <root> --purge` cure. The box is indexed in `registry.standalone`, and its store is the
resolved `workset.boxes` (`standalone_box_store`). With `--purge` the plan is built and confirmed
BEFORE the registry entry is dropped, so a refusal or a declined question leaves the box registered
and whole. Without `--purge`, or when a purge leaves the metadata, a deregistered entry is parked
so a later `rm --purge` or `register` can find the retained metadata BY NAME. The user's workspace
files are never touched. *config* is required: without it the running-box probe cannot see a live
box.

```run_rm(args: argparse.Namespace) -> int```
Unregister a box (primary or standalone), optionally purging metadata.

Resolution order, the membership routing and the deregistered parking rule are in "`rm`,
`register`, and the deregistered section" above.

```_readopt_deregistered(std, name: str, entry: dict) -> int```
⚑ INDEX-ONLY, SEED-FREE readopt: move a box from `deregistered` back to active.

Writes ONLY the active membership index (the primary per-workset `boxes:` /
`registry.standalone`) and drops the deregistered entry; NEVER touches the box's home content or
re-materializes templates, because membership is itself the seed signal. Routes per-kind off the
entry's `kind`, self-heals a stale entry whose metadata is gone, and inherits its conflict guards
from the reused registration APIs.

```run_register(args: argparse.Namespace) -> int```
⚑ Re-register a box: INDEX-ONLY and SEED-FREE, by NAME (readopt) or PATH (standalone).

The two unified operations, the already-active guards and the `import_standalone` reuse are in
"`rm`, `register`, and the deregistered section" above.

```_format_credential_age(creds_path: Path) -> str```
Return a human-readable age string for a credentials file, or 'n/a'.

```_check_container_running(proj, *, fail_closed: bool = False, container_name: str | None = None) -> tuple[bool, str]```
Is *proj*'s container running? `(running, detail)`; *fail_closed* raises on no answer.

*container_name* overrides *proj*'s rendered name; otherwise the container is found by its labels.
Accepts a `ProjectPaths` or a duck-typed equivalent. A container stuck part-way out is reported
with its `rm` cure, and an exited or paused container still holds the name and is reported as
such.

```run_info(args: argparse.Namespace) -> int```
Show per-box status, paths, container state, image, agent and credentials. *(No docstring in
source.)*

See "`box info`" above for the name-precedence resolution, the deferred unbuilt-box refusal and the
informational (degrading) agent resolution.

```run_set(args: argparse.Namespace) -> int```
`box set [project] <key>=<value>` — set a project setting.

```run_reset(args: argparse.Namespace) -> int```
`box reset [project] <key>` / `box reset [project] --all`.

Disambiguates a lone positional as a KEY when `is_known_key` answers True, matching the get/show
heuristic, then rebuilds the engine shape with the key (or `__ALL__`) on `args.reset`.

```run_get(args: argparse.Namespace) -> int```
`box get [project] <key>` — read one project setting.

```run_show(args: argparse.Namespace) -> int```
`box show [project] [--effective]` — show overrides / resolved values.

```_resolve_config_subject(std, config, project_dir: str | None)```
⚑ Resolve the box the config verbs address, refusing the `__unregistered__` phantom.

Full account in "`_resolve_config_subject` — the `__unregistered__` phantom" above. Raises
`ProjectError`.

```_run_box_config(args: argparse.Namespace) -> int```
Shared box-config dispatch into the `config_interface` engine.

Handles get, set, show and reset, using the known-key heuristic to disambiguate project names from
config keys. The M-8 chokepoint, the threaded cascade and the whole `--effective` block are
documented in "The config verbs" above.
