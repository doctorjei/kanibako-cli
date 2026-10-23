# Lifecycle Journal — the write-ahead log of in-flight box-lifecycle operations

`launch/journal.py` is a small, dependency-light store: it reads and writes one YAML document,
`global/journal.yaml`, holding one entry per box-lifecycle operation that is currently mid-flight.
It makes no decisions. Every recovery decision lives in the callers — `commands/start.py` (create),
`project/import_reconcile.py` and `project/workset.py` (import/connect), `settings/paths.py`
(resolve-time recovery), `commands/diagnose.py` (reporting) — and this module is the one place any
of them writes the intent down.

Design authority: `~/canon/workbook/designs/lifecycle-journal-DESIGN.md` (`[A225]`). This is the
one place the design path is spelled; the source points here.

## Registry against journal — two different truths

The REGISTRY (`config.registry`, `global/registry.yaml`) is the **steady-state** truth: what boxes
exist. The JOURNAL (`config.journal`, `global/journal.yaml`, beside it) is the **transient** truth:
what operations are mid-flight. They are separate files on purpose — membership is steady truth, an
in-progress entry is a transient signal, and mixing them would put half-created boxes into the
registry.

At rest the journal is normally EMPTY. An entry is the rare in-flight or crashed op, which is why
`read_journal` treats an absent or empty file, or a non-mapping `entries`, as `{}` rather than as an
error: that is the ordinary state, not a fault. A file that is not valid YAML, or whose top level is
not a mapping, raises `ConfigError` naming it (`config_io.load_doc`).

## Why the entry is written before the seed — the B3 window

A journal entry is written BEFORE the SEED step, and therefore before the registry write. A crash
anywhere in the seed → register window is then forward-recoverable, because the recorded intent
outlives the crash. That window is exactly where B3's create-recovery defect lived.

This mechanism SUPERSEDES the B3 `.seeding` per-box file marker. A per-box marker cannot predate
its own box directory — it sits as a sibling of `home/`, so the metadata dir must already exist to
hold it. A global journal entry can predate any directory, which is the whole reason the journal
won the design.

⚑ **Interrupted-create recovery is why the seed gate reads "`is_new` OR pending create entry"**
(`is_recovery` in `commands/box/_parser.py`, reached only through `box create --recover`).
`~/canon/notebook/procedures/seed-and-create-model.md` records this explicitly: the create path
gating on a pending journal entry looks like a contradiction of the seed model and is not one.
Removing the journal arm deletes interrupted-create recovery. Do not "fix" it.

## When the create entry is written

`box create` writes its entry from the non-materializing probe, through `_claim_create_entry` under
`rmw_lock(std.journal)`, before the `initialize=True` resolve — that is, before the create's first
box write. The entry is written ONCE; its `state` records the create's inputs (below).

## Recovery model — forward-complete by REPLAY

Blessed model: recovery re-runs the recorded op **from step 1**, and steps that are already done
skip themselves. That is only sound because every lifecycle op is idempotent —
create-if-absent / register-if-absent / remove-if-absent. There is deliberately:

* **NO `phase` / progress field.** Replay covers the whole op, so progress never needs recording.
* **NO rollback.** Undoing a partial op is a user-initiated ABORT concern and is out of scope here.

Replay per op type, from the design:

| op | replay is |
|----|-----------|
| `create` | seed (create-if-absent) → register (if-absent) → clear |
| `import` / `connect` | register (if-absent) → clear — **NO seed step** |
| `move` | copy-to-dest → register new → unregister old → remove old → clear, each if-absent/if-present |
| `convert` / `duplicate` | like create/move, each step if-absent |
| `rm` / `archive` | finish the delete/archive (delete-if-present) → clear |

## Write-ahead order and the HARD INVARIANT

Per op: **write entry → (idempotent op steps) → clear entry.**

The HARD INVARIANT, inherited from B3: the entry is cleared IMMEDIATELY after the op's committing
step — never before it. Hence `registered ==> no pending entry` holds at rest. Every stale-entry
sweep in the tree (`import_reconcile._clear_stale_import`, run at resolve time by
`import_reconcile.reconcile` and `import_named_workset`) exists to restore that invariant after a
crash in the register → clear window.

## The document schema

```yaml
entries:
  /home/jei/projects/foo:        # keyed by box host-side PATH (pre-registration)
    op: create                   # create|import|connect|move|convert|...
    name: foo                    # assigned (pick_primary_box_name); may not be registered yet
    mode: primary                # primary|named|standalone
    workset: __PRIMARY__         # if relevant (else absent)
    workspace: /home/jei/src/foo # if provided (else absent)
    state: {image: ..., agent: ...} # the create's recorded inputs (omitted when empty)
    started_at: 2026-06-30T07:12:00Z
    host: blue                   # reserved for liveness detection (later)
```

`entries` is the single top-level mapping in the document (`_ENTRIES` in source).

Field notes that are not visible from the code:

* **`started_at`** is `utils.utc_stamp("seconds")`.
* **`state`** is a RECORD OF INTENT, not a progress marker: everything the create was told that
  sets box state, so `box create --recover` finishes that attempt rather than guessing it. It never
  changes after the write.
* **`host`** is `socket.gethostname()`, recorded for a liveness-detection feature that does not
  exist yet. It has no reader today; that is intentional, not dead weight.
* **`started_at` and `host` are assigned AFTER the optional fields** rather than inside the dict
  literal. That ordering is load-bearing on disk: `config_io.dump_doc` pins `sort_keys=False`, so
  insertion order IS the emitted YAML order, and the schema above is what a human sees in the file.
* **`workspace`** is the P8b field; see below. Both it and `workset` are persisted only when
  provided, so an entry never carries an empty slot.

## The entry KEY

The key is the box **host-side path**: `str(Path(proj.shell_path).parent)` — the directory
CONTAINING `home/`. It is uniform across all three modes (primary/named `boxes/<name>`; standalone
`<root>/box_data`) and, critically, it is known at write-ahead time, before registration and before
any settings snapshot exists.

`_key` exists to make that normalization one function rather than a `str()` at five call sites.

⚑ The derivation is spelled by hand in `commands/start.py::_box_journal_key`, which carries its own
warning explaining why that hand-spelling of `meta.box.path` is allowed there and nowhere else: the
entry is written before the keystore that would resolve the anchor exists. If the box-root
derivation changes, that site changes with it.

## Atomicity

Reads and writes go through `kanibako.settings.config_io` (`load_doc` / `dump_doc`). `dump_doc`
writes atomically — temp file plus `os.replace`, via `kanibako._atomic.atomic_write_text` — so a
crash mid-journal-write can never leave a torn or corrupt journal document on disk. A write-ahead
log that could be corrupted by the crash it exists to survive would be worthless, so this property
is a requirement, not a convenience.

Both mutators (`write_entry`, `clear_entry`) are therefore read-modify-write over the whole
document, under `rmw_lock(journal_path)`: load, mutate the `entries` mapping, dump.

## Writing and clearing

**`write_entry`** records an in-flight op keyed by *box_path*, with optional *workset*, *workspace*,
and *state*. Without *claim* it OVERWRITES any existing entry for the same path. With *claim*, an
entry already holding the key, or a pending `create` of the same *workspace*, is returned and
nothing is written: two writers never share one key, and one workspace never has two creates.
`None` means written.

Its *workspace* argument is the **P8b** field — the box's workspace dir, recorded so that a create
can be re-discovered BY WORKSPACE during deferred-registration recovery. The journal is keyed by box
PATH, so a workspace lookup has to scan; recording the field is what makes that scan possible at
all. See `pending_create_for_workspace`.

**`clear_entry`** drops the key and keeps the document. This is the JC-J1-3 "lean" decision: the
file persists with `entries: {}` when the last entry clears, rather than being deleted. Clearing is
a no-op when the file is absent or the key is not present, which is what lets a replay call it
unconditionally.

## The four lookups, and why they are four

`pending_entry` is the raw read: the entry for *box_path*, or `None`. The other three are typed
filters over it, and the separation is the point — **the op TYPE selects the replay table**, so a
create entry must never drive a register-only replay and vice versa.

* **`pending_create`** — the entry for *box_path* iff `op == "create"`. A non-`None` result means a
  create was started for this box and never completed (a crash before `clear_entry`). This is the
  create recovery signal, read through `commands/start.py::_pending_create_entry`. In
  `commands/box/_parser.py` it feeds `box create --recover`; a launch refuses on a pending entry
  (`_interrupted_create_error`) and never completes it.
* **`pending_create_for_workspace`** — the PRIMARY deferred-registration create-recovery signal
  (P8b), consumed by `settings/paths.py`. Because the journal is keyed by box host-side PATH, a
  lookup BY WORKSPACE must scan every entry for an `op: create` whose recorded `workspace` is the
  argument. Both sides compare literally (`literal_path`); two paths to one directory are two
  workspaces. When it hits, the box's NAME is read from the journal, not from on-disk
  meta — the box was never registered, so there is nothing else to read it from. `None` is the
  ordinary case: the registry-hit resolve.
* **`pending_import`** — the entry iff its op is register-only (`import` or `connect`, the
  `_IMPORT_OPS` pair). A non-`None` result means a register-only op was started and never
  completed.

## Register-only ops — J2

`import` and `connect` REGISTER an externally-seeded box and NEVER seed it (CONVENTIONS "Seed
model" B7: the box was seeded where it was created, so seeding it again would clobber it). Their
replay is register-if-absent → clear, with no seed step.

That is the entire reason the entry is op-typed: the `op` field is how the journal distinguishes a
create (seed + register) from an import/connect (register-only). A create entry landing on an
imported box would wrongly trigger a re-seed, and the type filter is what makes that unavailable.
