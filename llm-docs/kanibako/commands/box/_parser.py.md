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

Authority: the keyspace spec `settings-keyspace-1.8.0.md` §0 (closed keyspace, single route), §1A
(CLI level and the create exception), §2h (`pref.*` requests); the box-lifecycle design items I2
(readopt) and I4 (the home-reuse data-loss guard); the J1 lifecycle journal.

## The verb surface

`box` is a released public interface — every flag, alias and help string below ships.

| Verb | Aliases | What it does |
|------|---------|--------------|
| `create` | — | Make a box: probe, gate, materialise, seed, register (standalone: only on `--register`) |
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

## `create`: the ordering IS the design

`run_create` is a sequence of gates whose ORDER is load-bearing at almost every step. Reordering
any of them reopens a bug that has already been paid for.

1. **Fold `--name` to lowercase, THEN validate** (R2). The blocklist must see the folded name.
   The folded value is then read into `standalone_name`, which is `""` unless `--register` was
   given: for a standalone box `--name` names the REGISTRY ENTRY, so with no entry to name it is
   dropped before it reaches the resolver and never reaches `resolve_standalone_name` (I3/§D4a).
   ⚑ It is still folded and validated first — the flag's VALUE is checked even on the path that
   ignores it, which is a known incoherence held for a ruling, not an accident to copy.
2. **The `$HOME` guard.** A home-directory project mounts the entire home tree, so it must be BOTH
   standalone AND an explicit `--allow-home`. Default mode at `$HOME` is never permitted.
3. **The cross-kind name guard**, run HERE rather than at registration, so it refuses cleanly
   BEFORE the box dir and seed materialize. An EXPLICIT `--name` for a PRIMARY box that collides
   with a WORKSET name shadows that workset in bare-name resolution (the box wins), so it is
   refused unless `--force`; a SAME-KIND collision is unconditional. Standalone boxes are not in
   the primary/workset name domain and skip it. The deferred registration
   (`_register_new_box`) re-checks with the same flag.
4. **The I4 home guard** (`_assert_primary_home_free_for_create`) — see below.
5. **The persona pre-flight**, on a NON-materialising probe (`initialize=False`, no `mkdir`), so
   an unloadable persona refuses with NOTHING on disk: no box dir, no meta, no journal entry, no
   seed. This applies the same probe → gate → initialize pattern the launch path uses. The probe
   carries the deterministic name it WILL materialise under (`_name_new_box_probe`) so the gate's
   channel-address derivation resolves instead of raising "box has no name".
6. **The persona-grata STORE check**, before the create verdict, so a store that cannot yield a
   usable persona is reported as ITSELF rather than as the verdict's downstream "no endpoint
   configured".
7. **The already-initialized refusal**, HOISTED above the materialising resolve.
8. **The materialising resolve**, with `register=False`.
9. **The J1 write-ahead sequence**: write entry → seed → canon skeleton → register → clear entry.
   ⚑ The register step is CONDITIONAL for a standalone box (I3/§D4a): `_register_new_box` runs
   for every PRIMARY box, whose membership IS its workset, but for a standalone box only on
   `--register`. The default leaves an independent, unindexed box that `kanibako box register
   <path>` adopts later, index-only and seed-free. The gate lives at this call site, not inside
   `_register_new_box`, because the flag is a create-verb concern. A created box then reports
   its own state: an unregistered one prints the `box register` cure, gated on the REGISTRY
   rather than on the flag so a journal-recovery re-run of an already-registered box stays quiet.

### Why `_already` is captured where it is

`box_tree_materialized(_probe)` is read BEFORE `_name_new_box_probe`, which MUTATES `_probe.name`.
The refusal itself is deferred to just past the persona pre-flight so error ORDERING is unchanged;
only the mutation it used to trail now happens after it.

### Why the already-initialized refusal is hoisted

It used to sit AFTER the materialising resolve, reading `proj.is_new`. But that resolve runs with
`initialize=True`, and its recovery arms re-create a missing home (`resolve_project`) and a missing
home + workspace (`resolve_standalone_project`) BEFORE returning — so a box whose home had been
deleted got it silently bootstrapped and was THEN told "already initialized", a message that was
false at the moment it printed. Asking the NON-materialising probe makes the message TRUE: nothing
has been written when it prints. Message and exit code are unchanged.

J1 interrupted-create RECOVERY still takes precedence: a box that already exists but carries a
pending create journal entry is a half-completed create (a crash between seed-start and the
registry write) and is COMPLETED by replay rather than refused. **The journal entry, not
`is_new`, drives completion** — that is the central J1 fix, restoring the hard invariant
"registered ⇒ no pending entry" for PRIMARY and STANDALONE alike. `_pending_create_entry` keys on
the box dir, which the probe resolves identically to the materialising call.

### The J1 write-ahead journal

The four steps are write-ahead: write the create journal entry (intent), seed the home
(create-if-absent), THEN register (deferred by `register=False`), then clear the entry — clearing
is the IMMEDIATE step after the registry write. A crash anywhere before the entry is cleared
leaves it, so the next `create`/launch re-seeds, completes registration and clears the entry:
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
time the journal opens the persona is known loadable. It STILL precedes the write-ahead entry
(Director ruling #3): an abort after the entry would leave a pending entry whose recovery replays
the seed.

The canon skeleton (J-7) is materialised AFTER the seed and INSIDE the journal window. After,
because the seed writes `canon/{notebook,workbook}` under the same root this step makes 555 —
protect first and those copies die with `EACCES`. Inside, because an interrupted create must
replay it like every other step.

### What a FRESH create persists (and a recovery does not)

`if proj.is_new:` guards the image, `--private` and `--agent` writes plus the standalone
`.gitignore`. A recovery re-create reuses the half-built box's already-written meta: the on-disk
record is authoritative and must not be overwritten with possibly-different args.

* **Image** — the §1A CREATE EXCEPTION (R-11a) goes through the ONE shared gate
  `persist_creation_flags`, the same gate the launch-materialization path in `start.py` calls, so
  the persist rule has exactly one home. Only an EXPLICITLY-GIVEN `-i`/`--image` persists: a
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
* **`--agent`** — persists the §2h REQUEST `pref.system.agent` through the same sanctioned write
  (single route, no bespoke YAML poke), so a plain `start` resolves this agent through the ordinary
  pref path instead of falling through to the system default. ⮕ P7: this used to write the RETIRED
  `box.agent_name` (spec §2b); a pref is how a box influences a key that resolves above it. It
  applies to ANY selector, persona or plain — `create --agent goose` must make a plain start launch
  goose too. The RAW user ref is stored (selection canonicalizes on read, exactly as for a
  hand-set key), and `start --agent <other>` remains an ephemeral override on top: CLI args are
  ephemeral over settings, and `start` never persists.
* **`.gitignore`** — standalone only, written at the project ROOT (`metadata_path`), where
  `box_data/` and `vault/` live and need ignoring. `project_path` is the workspace SUBDIR, not the
  root.

Explicit-create: `create` MAKES the box but does NOT launch it. A launch (`start` / bare
`kanibako` / `code` / `shell`) no longer auto-creates, so the closing hint names the verb.

## Names, homes, and the I4 data-loss guard

`create --name X` materialises the box at `std.boxes/X` with `mkdir(exist_ok=True)`, so if that
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
active-box name, the one-box-per-workspace-path invariant (`register_workset_box`'s
one-box-per-path guard) and the CROSS-kind workset-shadow refusal unless `--force`; a violation
refuses cleanly with guidance. The standalone arm checks the name against a different root itself,
and its re-register overwrites a matching name→root. A readopt therefore never clobbers a live
box. Both arms self-heal a stale entry whose metadata is
gone: drop it, report nothing to restore.

## Purge safety

A `deregistered` entry stores a metadata path that a later `--purge` DELETES, and that stored path
is UNTRUSTED — a corrupt or crafted registry must never let purge delete outside a box's own
metadata. `_assert_deletable` refuses (rather than silently no-op-deleting) when the resolved path
is empty, `/`, `$HOME`, or — when `must_be_under` is given — not a STRICT descendant of that root.
`resolve()` collapses `..` and follows symlinks, so a `..`/symlink escape resolves OUTSIDE the root
and is rejected. Strict containment matters because deleting `std.boxes/` itself would take every
box with it.

`_purge_deregistered` also carries an I4 belt-and-suspenders check: the name may have been REUSED
by a new ACTIVE box now occupying the SAME metadata path (e.g. a create that re-claimed
`std.boxes/<name>`). Purging by the stale entry would delete the LIVE box's home, so it refuses
AND drops the stale entry — the entry is definitively wrong, since an active box owns that path, so
it must stop shadowing. The active box's home is never deleted.

The two teardowns are the single deletion routines shared by the active `--purge` path and the
deregistered `--purge` path — same paths, same order, same guards:

* `_teardown_primary_box` removes the box dir, then the PRIMARY vault `ro`/`rw` under
  `@config.primary_workset/vault/{ro,rw}/<name>` (which is NOT under `metadata_dir`), then the
  per-box helper log at `@config.primary_workset/logs/<box>.jsonl`, keyed by the registry name.
* `_teardown_standalone_box` removes the in-tree `box_data/` marker, the ROOT `workset.yaml` and
  `vault/`. The ROOT file is the WORKSET tier AND the other half of the §5 detection marker, so
  dropping it is what stops the box being re-detected; the BOX tier lives inside `box_data/` and
  goes with the dir. The user's workspace files, and `root` itself, are never touched.

`_purge_dir` is a thin alias for `kanibako.runtime.container.remove_box_tree`, which is where the
body lives so EVERY box-tree deleter can reuse it — `extract`, `move`, `duplicate` and `purge` all
need the same `podman unshare` escalation, and since J-7 they need it on every box (the canon
skeleton is root-owned by construction, not only when an agent happened to write as root). The
name is kept because `rm`'s call sites and tests read against it.

## The listing

`run_list` merges FOUR sources, because no single index holds them all:

1. `iter_projects` — default-mode boxes.
2. `iter_workset_projects` — workset members.
3. `registry_store.load_standalone` — STANDALONE boxes, which are not in `names.yaml` /
   `iter_projects` and would otherwise be invisible.
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
NEVER materialises a box, so naming `start` described behaviour we do not have.

Agent resolution here is INFORMATIONAL, not an agent-requiring launch, so a failure (no default
with 2+ agents, 0 agents, adapter missing) DEGRADES to `n/a` rather than erroring. It still goes
through the ONE selection seam, `agent_select.select_agent` (`system.agent` < workset pref < box
pref). ⚑ The Agent row shows WHERE THE SELECTION CAME FROM (P7): "which agent does this box run,
and WHY?" is the question a retired-key or wrong-default box makes urgent, and the answer is in no
single file — it may be the stored `system.agent`, a workset/box `pref.system.agent`, or the
installed-count rule picking the only agent installed. `AgentSelection.source` already carries it,
and this is the one place a user can read it. A refused RETIRED key (migration M-4) likewise
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
one pair so it clears exactly the file `set`/`get` address, and the `set` arm reuses
`workset_path` rather than deriving a second one.

`reset` and `set` both thread the FULL launch cascade — every scope's settings file plus the active
agent name — so a cross-scope `@`-ref in a new value resolves at set time exactly as it would at
launch, and so `reset`'s cleared-message can honestly name the now-effective value and its source
tier. The active agent name is resolved best-effort: it selects the `agent.<active>.*` sub-table
the OTHER cascade files may carry, and a resolution failure just leaves it empty, degrading the
message to the cleared-only form. Neither call passes `cascade_agent_path`, so it defaults to
`None`; the per-agent file stores behavior FLAT, so `assemble_levels` reads no category subtree
from it.

### `--effective`

The `--effective` display resolves the PATH-DELIVERY categories and their materialised derivations
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
view would resolve `@system.agent` DIFFERENTLY from the launch it claims to show — an autopicked or
`--agent` box would render `meta.box.auth.workset_path` with the agent segment dropped. A display
that disagrees with the launch is worse than no display.

⚑ SELECTION ONLY, and for a READ verb that is the WHOLE of the CLI level (P8). `box config` carries
none of the launch's ephemeral VALUE flags (`-M`, `-N`/`-C`/`-R`, `--image`, `--share-images` live
on `start`), so there is nothing ephemeral to install. Rendering a flag from some OTHER invocation
would be the "a flag mutated a stored value" failure §1A forbids, one screen removed: `--effective`
reports what the box IS configured to do, and a per-launch override is by definition not that.

⚑ `--agent` is the EXCEPTION, and it is a PRE-EXISTING DEFECT rather than a P8 decision. The
blanket flag injector puts it on every leaf parser, so `box config --agent goose --effective`
PARSES — but this call site, and the other four read-verb `select_agent` sites, never pass it
through, so it is silently IGNORED. It is advertised and inert. P8 left the behaviour exactly as it
found it rather than quietly changing what a read verb reports; honouring it (or refusing it) is a
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
`boxes/__unregistered__/settings.yaml` and report SUCCESS — materialising a placeholder instead of
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

```_check_persona_store_for_create(agent_ref: str, project_path) -> str | None```
⚑ READ-ONLY create-side persona-grata store CHECK; an `"Error: …"` or `None`.

The create-side trigger (DESIGN §4): when the explicit agent ref names a persona whose
persona-grata store entry EXISTS, read the store and refuse the create NOW if it cannot yield a
usable persona — BEFORE the persona create verdict, so a broken store is reported as itself rather
than as the verdict's downstream "no endpoint configured".

⚑ NOTHING IS WRITTEN. This used to IMPORT the store into `agents/<node>/agent.yaml`; the store
is a LIVE resolution input now (`read_persona_bundle` → the launch's persona cascade level), and
the agent settings file holds user-intent values only, so there is nothing to persist and no
`agent.yaml` for a corrupt-file arm to trip over. The gates themselves are UNCHANGED: same hard
errors, same WARN-ONLY probe.

Returns an `"Error: …"` message for the caller to print-and-refuse (malformed ref, unusable store
config, a store naming no endpoint), or `None` when there is nothing to do — a bare/plain ref, no
store entry, the harness not installed, or a harness with no persona reader, all of which fall
through to normal create behavior — or when the store checked out (a soft token-pointer warning is
printed here).

`bundle is None` cannot happen (the entry was located a moment ago) but costs nothing to honour.
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

```run_create(args: argparse.Namespace) -> int```
Create a new kanibako project (replaces `kanibako init`).

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

```_purge_dir(target: Path) -> bool```
Thin alias for `kanibako.runtime.container.remove_box_tree`, kept for its callers.

Removes *target*, tolerating files a rootless container created. See "Purge safety" above for why
the body lives in `runtime.container` and why the alias survives.

```_assert_deletable(path, *, must_be_under: Path | None = None) -> Path```
⚑ DESTRUCTIVE-SAFETY gate: validate *path* is safe to `rm -rf`, return it resolved.

Raises `ProjectError` on any violation. The refusal set and the strict-containment rule are in
"Purge safety" above.

```_teardown_primary_box(std, name: str, metadata_dir: Path) -> bool```
Delete a PRIMARY box's metadata: box dir + vault ro/rw + helper log.

Returns True if the box dir was removed. ⚑ The CALLER is responsible for containment-validating
*metadata_dir* first when it comes from an untrusted `deregistered` entry — see
`_assert_deletable`.

```_teardown_standalone_box(root: Path) -> bool```
Delete a STANDALONE box's in-tree metadata; the workspace and *root* are never touched.

Removes the in-tree `box_data/` marker, the root `workset.yaml` and `vault/` — the same set the
active standalone purge and the `purge` command delete. Returns True if the `box_data/` marker was
removed.

```_read_box_image(settings_file: Path) -> str | None```
Best-effort read of a box's `box.image` from its box.yaml; failure is `None`.

Captured into the deregistered blob for a later readopt (I2). Purge does not need it, so any read
failure degrades to `None` rather than erroring.

```_read_box_image_tiered(box_tier: Path, workset_tier: Path) -> str | None```
`_read_box_image` over a `(box_tier, workset_tier)` pair, box tier wins.

`box.image` is a BOX-scope key, so it is read from the box tier; the workset tier is consulted only
as the R2 downward-default. That fallback is DECLARED DESIGN (keyspec §2c), not a compat path; it is
also what keeps a pre-P2 standalone box — whose `box.image` was written to its ROOT file when that
file WAS the box tier (M-8) — capturable into the deregistered blob.

```_purge_deregistered(std, name: str, entry: dict, args: argparse.Namespace) -> int```
Handle `rm <name>` when *name* resolves only to a deregistered entry.

Without `--purge` the box is already deregistered — print the recovery guidance and return 0, with
no re-error and no re-deregister. With `--purge`, validate the stored metadata path for
containment, delete it via the shared per-kind teardown, then drop the deregistered entry.
IDEMPOTENT: an entry whose dir is already gone is simply dropped, with no error and no prompt. The
stale-active-owner refusal is described in "Purge safety" above.

For a standalone entry the stored `metadata` is the in-tree ROOT: only fixed children are deleted,
but a bare or protected root is still refused. For a primary entry the metadata must be strictly
under `std.boxes/`.

```_resolve_standalone_target(std, config, target: str) -> tuple[str | None, Path | None]```
Resolve a `box rm` *target* (NAME or PATH) to `(box_name, root)`, else two `None`.

*target* may be a registered standalone box NAME (looked up in `registry.standalone`) or a PATH
(resolved by ancestor-walk detection, then matched to its registered root). Mirrors how `box purge`
finds standalone boxes.

⚑ **The blanket `except Exception` here treats any detection failure as "not this box" — with ONE
carve-out: `LegacyWorksetIdentityError` re-raises.** A workset root still on the retired
`workset.meta` spelling (MIGRATION.md §2.43) is a named thing to fix, and swallowing it here would
report the path as an unresolvable box instead of printing the cure.

```_rm_standalone(std, box_name: str, root, args: argparse.Namespace) -> int```
Remove a standalone box: always drop its registry entry, its `box_data/` on `--purge`.

Standalone state lives in-tree under `<root>/box_data` (plus `<root>/vault`) and the box is indexed
in `registry.standalone`. With `--purge` the in-tree metadata is deleted (on confirmation, with the
`vault/` tree); without it, a deregistered entry is parked so a later `rm --purge` or `register`
can find the retained metadata BY NAME, since the index was just dropped. The user's workspace
files are never touched.

```run_rm(args: argparse.Namespace) -> int```
Unregister a project/workset from the registry, optionally purging metadata.

Resolution order, the membership routing and the deregistered parking rule are in "`rm`,
`register`, and the deregistered section" above.

```_readopt_deregistered(std, name: str, entry: dict, *, force: bool) -> int```
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

```_check_container_running(proj) -> tuple[bool, str]```
Is a kanibako container running for this project? Returns `(is_running, detail)`.

Accepts a `ProjectPaths` or a duck-typed equivalent. A stopped PERSISTENT container still exists
and is reported as such.

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
