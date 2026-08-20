# Drop-in import-on-discovery — reconciling the registry from on-disk truth

⚠️ **This mirror is COMPLETE for displaced prose** (llm-docs 60% pass, 2026-08-20). A symbol absent
below carried nothing worth displacing — never "does not exist". The source keeps one-line
descriptors and the `⚑`/HARD INVARIANT markers; the reasons live here.

`import_reconcile` is the module that lets a box, workset or project tree be **copied or moved** —
to a new directory, a new disk, a new machine — and still work. On-disk metadata is
**authoritative**; `system.registry` is a *derived, rebuildable index*. When detection or resolution
walks into an on-disk entity that the registry does not know about, kanibako **imports** it:
registers it into the appropriate registry section, prints an ALERT to stderr, and proceeds. There
is **no confirmation prompt** — moving a tree "effectively becomes an import".

The whole module is one uniform "reconcile the registry from on-disk truth" mechanism, deliberately
with **no per-mode special-casing**. Each mode differs only in which marker it reads and which
registry section it writes.

## The two live modes, and who calls them

Both are called **lazily, during resolution** — nothing sweeps eagerly. The call sites are in
`settings/paths.py`'s `detect_project_mode`:

* **STANDALONE** — `import_standalone`, called from the step-2 in-place marker check and again from
  the step-5 ancestor walk, whenever a `box_data/` marker is found whose box is not in
  `registry.standalone`.
* **NAMED** — `import_named_workset`, called from the ancestor walk when a workset-root marker (a
  `settings.yaml` carrying a `workset.meta` identity) is found that is not a registered workset
  root. NAMED is checked first at each level of the walk, because a `workset.meta` marker is the
  more specific identity.

`import_standalone` has one further caller outside detection: `commands/box/_parser.py`'s
`register` verb reuses it for STANDALONE **register-later**, with the comment
*"⚑ REUSE `import_standalone`: it is already index-only + seed-free."* That reuse is the reason the
function must stay index-only — a seed step added here would fire on a plain `kanibako register`.

## The RETIRED third mode — PRIMARY

A third **PRIMARY** mode once lived here: `import_primary_box`, `import_primary_box_for_workspace`
and `reconcile_primary_boxes`. It re-associated a central primary box with its external workspace by
reading that box's on-disk `project:` / `resolved:` meta.

It was **RETIRED FROM THE LIVE PATH (P8b / Option A)**. Under sparse create, a primary box no longer
self-describes its identity on disk, so there is nothing on disk to re-import from — for primary
mode the registry is the sole identity authority.

Its skeleton — enumerate, match-by-workspace, collision-refuse, journal-atomic — is the seed of a
future heuristic `system recover`, so it was not deleted but **sequestered** into
`salvage/primary_reconcile.py` (a non-shipping frozen reference snapshot, P8c). It is no longer part
of the live package. 🛑 Do not re-import it here; if `system recover` is ever built, it starts from
the salvage snapshot, not from a revived live path.

## Conflict semantics — the same rule in every mode

Three outcomes, and only three:

* **Already registered to this exact path** → silent, idempotent no-op. The function returns the
  registered name and mutates nothing.
* **No marker / no readable identity** → `None`. There was nothing to import.
* **The name collides with an entity already registered to a *different* root or path** → the import
  is **REFUSED**. Nothing is mutated and `ImportConflictError` is raised, explaining that a `rename`
  mechanism to resolve such collisions is planned (future work, not 1.6.0) and that for now the user
  must rename or relocate one of the two manually.

`ImportConflictError` therefore always means "I left both sides exactly as I found them". The shared
message text is built once in `_conflict` so standalone and workset refusals read identically.

## `_STANDALONE_BOX_DIR` — and its second spelling

`_STANDALONE_BOX_DIR = "box_data"` is the standalone box's host-side box dir: a sibling of the root
`settings.yaml`, holding `home/`.

⚑ It **mirrors `paths_defaults.STANDALONE_META_DIR`**, which carries the matching note
(*"A hand-kept 2nd spelling lives in `project/import_reconcile._STANDALONE_BOX_DIR`"*). The two are
kept in step by hand; changing one without the other silently splits the marker from the journal
key.

The J2 journal entry for a standalone import is keyed by `<root>/box_data` — the dir *containing*
`home/` — which is the uniform host-side box-dir key scheme shared with J1
(`str(Path(shell_path).parent)`). A workset has no single `home/` dir, so a workset import keys its
entry on the workset ROOT instead. Both keys are known **pre-registration**, which is what makes a
write-ahead entry possible at all.

## J2 — the lifecycle journal, register-only write-ahead

Import and connect **REGISTER** an externally-seeded box and **NEVER seed** (`CONVENTIONS.md`
"Seed model" B7). J2 makes that register seam ATOMIC via the journal: a write-ahead
`op: import` / `op: connect` entry brackets the register, so a crash between write-entry and
clear-entry leaves the entry behind, and the NEXT resolve re-enters this same (idempotent,
register-if-absent) import and replays it — register-if-absent, then clear, **no seed**.

The op TYPE is what keeps "import never seeds" true: the replay table for `import`/`connect` has no
seed step, because the box was seeded where it was originally created. A `create` entry has one; an
import entry must never be confused for one.

Because the import functions **are** the lazy-on-resolve trigger, wrapping them here buys both
write-ahead atomicity *and* recovery in a single place: re-running the resolve completes a half-done
import and clears the entry.

### The optional `journal` argument

The journal path is threaded as an **optional** `journal` argument from the resolver call sites
(`std.journal`). When it is `None` — a std-less direct caller with no journal, such as a low-level
test or a registry-only utility — the wrapper degrades to a plain register. Default behaviour is
then byte-identical to the pre-J2 register-only path.

### `_journal_register` — the bracket

Write-ahead order, by DESIGN: **write entry → register body (the `with` block) → clear entry.** The
entry is cleared as the IMMEDIATE step after the register, which is what makes the invariant hold at
rest.

If the register body raises — a genuine collision — the entry is **intentionally LEFT** (the import
is incomplete) and the exception propagates. Recovery resumes it on the next resolve. This is not an
oversight in the error path; do not "fix" it by clearing on exception.

A `None` journal is a no-op bracket, preserving the pre-J2 behaviour exactly.

### `_clear_stale_import` — closing the register→clear window

**HARD INVARIANT: `registered ⟹ no pending entry`.** The journal is empty at rest.

There is one window that can violate it: a crash AFTER the registry write but BEFORE the entry is
cleared leaves the box REGISTERED with a lingering `import` / `connect` entry. A later re-resolve or
reconcile sweep finds the box already registered, takes the import's idempotent NO-OP branch — and
would skip the clear. Calling `_clear_stale_import` on that branch completes the recovery:
register-if-absent is already satisfied, so recovery *is* "clear the stale entry".

⚑ **Only a register-only (`import` / `connect`) entry is cleared.** A `create` entry is deliberately
left alone for the create-recovery path to find. A `None` journal is a no-op.

## `import_standalone` — kuid-first identity

`root` is the standalone project root: the dir containing `box_data/` and, at the root,
`settings.yaml`.

**The marker gate** (design D4): the box's own settings FILE is the standalone signal — **NOT**
`project.mode`. `box_resolve.standalone_settings_present(root)` is the test. A bare `box_data/` is
not enough. No marker → `None`, nothing to import.

**Name composition is kuid-first** (P8b — the box no longer self-describes its `project.name` on
disk). The stored `workset.kuid`, sparse-persisted at create, prefixes the **LIVE dir leaf**:
`<kuid>_<leaf>`. This mirrors `launch.box_resolve.resolve_box_identity`'s standalone branch, and it
is what lets a MOVED box keep its stable kuid identity while the leaf tracks the new directory. A
pre-kuid box (the `kuid.SENTINEL` value) falls back to the plain dir leaf.

Order of operations: already-registered check by `standalone_name_for_root` (plus stale-entry clear)
→ marker gate → compose name → collision check → journalled register → alert. The composed name is
registered to `root` and alerted, UNLESS it already maps to a DIFFERENT root →
`ImportConflictError` (refuse, no mutation). The register is register-only, with **no seed**: the
box is already seeded on disk.

## `import_named_workset` — the `worksets` section

Reads the workset name from `root`'s `settings.yaml` `workset.meta` table and reconciles it against
`registry.worksets` — the name → root index that backs both name lookups AND workset discovery,
written by `kanibako.project.workset`. Registration matches `create_workset` exactly, so an imported
workset is indistinguishable from a created one.

Returns `None` when `root` has no readable `settings.yaml` `workset.meta` identity, or when that
identity carries an empty name.

⚑ It does **NOT** rewrite the workset-create skeleton. It only registers an already-on-disk
workset — a workset import never seeds a box, which is why its journal bracket is register-only like
the standalone one.

## Completeness sweep

`prose-relocation-check.py`: every removed prose line is accounted for above. The prose kept in the
source is the trimmed module docstring, the one-line descriptor on each of the seven
docstring-bearing symbols, the two section banners, and the short `⚑` / HARD INVARIANT markers whose
deletion would let a future edit break something silently at that exact line:

* at `_STANDALONE_BOX_DIR` — the second spelling in `paths_defaults.py` must move with it;
* at `_journal_register`'s clear step — the clear is the immediate post-register step, and a raise
  intentionally leaves the entry;
* at `_clear_stale_import` — only register-only entries are cleared, never a `create` entry;
* at the marker gate and the kuid composition in `import_standalone` — both look like they could be
  simplified to `project.mode` and to the dir leaf respectively, and both must not be.
