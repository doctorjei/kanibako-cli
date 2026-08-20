# Lifecycle Journal — the write-ahead log of in-flight box-lifecycle operations

`launch/journal.py` is a small, dependency-light store: it reads and writes one YAML document,
`global/journal.yaml`, holding one entry per box-lifecycle operation that is currently mid-flight.
It makes no decisions. Every recovery decision lives in the callers — `commands/start.py` (create),
`project/import_reconcile.py` and `project/workset.py` (import/connect), `settings/paths.py`
(resolve-time recovery), `commands/diagnose.py` (reporting) — and this module is the one place any
of them writes the intent down.

Design authority: `~/canon/workbook/designs/lifecycle-journal-DESIGN.md` (Jei-blessed 2026-06-30b,
`[A225]`). ⚑ The module docstring cited that document as `plans/lifecycle-journal-DESIGN.md`; it
lives in `designs/`, not `plans/`. The pointer in source now names this file instead, and this is
the one place the design path is spelled.

## Registry against journal — two different truths

The REGISTRY (`config.registry`, `global/registry.yaml`) is the **steady-state** truth: what boxes
exist. The JOURNAL (`config.journal`, `global/journal.yaml`, beside it) is the **transient** truth:
what operations are mid-flight. They are separate files on purpose — membership is steady truth, an
in-progress entry is a transient signal, and mixing them would put half-created boxes into the
registry.

At rest the journal is normally EMPTY. An entry is the rare in-flight or crashed op, which is why
`read_journal` treats an absent, empty or malformed file as `{}` rather than as an error: that is
the ordinary state, not a fault.

## Why the entry is written before the seed — the B3 window

A journal entry is written BEFORE the SEED step, and therefore before the registry write. A crash
anywhere in the seed → register window is then forward-recoverable, because the recorded intent
outlives the crash. That window is exactly where B3's create-recovery defect lived.

This mechanism SUPERSEDES the B3 `.seeding` per-box file marker. A per-box marker cannot predate
its own box directory — it sits as a sibling of `home/`, so the metadata dir must already exist to
hold it. A global journal entry can predate any directory, which is the whole reason the journal
won the design.

⚑ **Interrupted-create recovery is why the seed gate reads "`is_new` OR pending create entry."**
`~/canon/notebook/procedures/seed-and-create-model.md` records this explicitly: the create path
gating on a pending journal entry looks like a contradiction of the seed model and is not one.
Removing the journal arm deletes interrupted-create recovery. Do not "fix" it.

## The remaining gap — not yet a TRUE pre-dir write-ahead

The design's stated goal is "write-ahead before any dir exists." The code does not reach it yet, and
the module docstring said so; the note is preserved here rather than dropped, because a reader who
does not find it will assume the window is closed.

Today the resolver materializes the box dir plus `meta` BEFORE the entry is written — the resolver
still creates dirs. A crash DURING that resolver dir-creation, before the entry exists, leaves the
same unrecoverable limbo, only narrower. Closing it fully is deferred tech debt: extract
registration and dir-creation OUT of the resolver, after which the entry can be written before any
dir exists and the design's goal is realized. J1 covers the seed → register window, which is the
defect that was actually costing recoveries.

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
sweep in the tree (`import_reconcile._clear_stale_import`, the resolve-time clear in
`settings/paths.py`) exists to restore that invariant after a crash in the register → clear window.

## The document schema

```yaml
entries:
  /home/jei/projects/foo:        # keyed by box host-side PATH (pre-registration)
    op: create                   # create|import|connect|move|convert|...
    name: foo                    # assigned (pick_primary_box_name); may not be registered yet
    mode: primary                # primary|named|standalone
    workset: __PRIMARY__         # if relevant (else absent)
    workspace: /home/jei/src/foo # if provided (else absent)
    started_at: 2026-06-30T07:12:00Z
    host: blue                   # reserved for liveness detection (later)
```

`entries` is the single top-level mapping in the document (`_ENTRIES` in source).

Field notes that are not visible from the code:

* **`started_at`** is stamped from a real `datetime.now(UTC)`, rendered `%Y-%m-%dT%H:%M:%SZ`.
* **`host`** is `socket.gethostname()`, recorded for a liveness-detection feature that does not
  exist yet. It has no reader today; that is intentional, not dead weight.
* **`started_at` and `host` are assigned AFTER the optional fields** rather than inside the dict
  literal. That ordering is load-bearing on disk: `config_io.dump_doc` pins `sort_keys=False`, so
  insertion order IS the emitted YAML order, and the schema above is what a human sees in the file.
* **`workspace`** is the P8b field; see below. Both it and `workset` are persisted only when
  provided, so an entry never carries an empty slot.

⚑ **FALSE CLAIM FOUND AND DROPPED.** The schema comment in the module docstring enumerated `mode`
as `primary|workset|standalone`. `workset` is not a mode token and never was one at this spelling:
`settings/paths.py` declares `BoxMode` as `primary` / `named` / `standalone`, and both writers agree
with the enum — `commands/start.py` passes `proj.mode.value`, `project/workset.py`'s `connect`
bracket passes the literal `"named"`. Corrected in the table above; not relocated in its wrong form,
because relocating a drifted claim launders it into a document that reads as current.

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
document: load, mutate the `entries` mapping, dump.

## Writing and clearing

**`write_entry`** records an in-flight op keyed by *box_path*. It OVERWRITES any existing entry for
the same path. That is deliberate and harmless: a re-run before recovery simply re-stamps the same
intent, and the op it describes is idempotent either way.

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
  create/seed-path recovery signal, consumed by `commands/start.py::_pending_create_entry` and by
  `commands/box/_parser.py`.
* **`pending_create_for_workspace`** — the PRIMARY deferred-registration create-recovery signal
  (P8b), consumed by `settings/paths.py`. Because the journal is keyed by box host-side PATH, a
  lookup BY WORKSPACE must scan every entry for an `op: create` whose recorded `workspace` resolves
  to the same directory as the argument. Both sides are `resolve()`d so symlinks and trailing
  slashes compare equal. When it hits, the box's NAME is read from the journal, not from on-disk
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

---

## Completeness sweep

Deliberate content drops: **one** — the `primary|workset|standalone` mode enumeration, recorded
above as a false claim with its correction and its evidence, rather than relocated verbatim.
Everything else that left the source landed in a section above.

Kept IN SOURCE, under the keep test (deleting each lets a future edit break something silently at
that exact line):

* the module docstring's HARD INVARIANT and the write-ahead order — the ordering rule is invisible
  in this module, because the committing step it brackets lives in the callers;
* "replay, so every op must stay idempotent; NO `phase`, NO rollback" — a future contributor's
  first instinct on a write-ahead log is to add a progress field;
* the "not yet a true pre-dir write-ahead" note, shortened to the fact plus a pointer here — a
  reader who does not see it will believe the window is already closed;
* at `write_entry`, that the overwrite is intentional, and that `host` has no reader yet;
* at `clear_entry`, that keeping an emptied document is a decision (the absence of a delete is not
  visible as code);
* at `pending_create_for_workspace`, that both sides are `resolve()`d;
* at `_IMPORT_OPS`, that the op TYPE is what keeps the two replay tables apart.
