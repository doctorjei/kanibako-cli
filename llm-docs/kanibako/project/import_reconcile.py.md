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

It is called **lazily, during resolution** — nothing sweeps eagerly. The call sites are in
`settings/paths.py`'s `detect_project_mode`:

* **STANDALONE** — `import_standalone`, called from the step-2 in-place marker check and again from
  the step-5 ancestor walk, whenever a `box_data/` marker is found whose box is not in
  `registry.standalone`.
* **NAMED** — `import_named_workset`, called from the step-5 ancestor walk whenever
  `project/workset.is_workset_skeleton` matches a level: the four dirs `create_workset` stamps,
  present together. After the import the walk re-runs `_check_workset`, which now resolves.

⚑⚑ **THE TWO MODES DIFFER IN WHERE THE NAME COMES FROM, AND ONLY THERE.** A standalone box composes
one from its stored `workset.kuid` plus the live dir leaf. A workset records no name anywhere under
its root — its identity is the global registry's `worksets:` entry — so an imported workset takes
its **leaf directory basename** ([R139]), which is what `workset create` already defaults an unnamed
workset to. 🛑 That a workset's name is not on disk is a fact about NAMING; it never made a workset
root unfindable, and reading it as though it had is what deleted this function once. Detection is
presence-only in both modes, and `_is_standalone_meta_dir` is the standing proof the two questions
separate: it finds a box without reading any name out of it.

⚑ The walk ALSO calls `project/workset.refuse_retired_workset_identity` at each level, **before**
the NAMED test — deliberately. A v1.6/v1.7 root HAS the four-dir skeleton, so a NAMED arm running
first would import it under its leaf name and leave the retired identity table, and the `projects:`
list beside it, unread and unmentioned. The diagnosis has to precede the action or it never happens.

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
* **No marker** → `None`. There was nothing to import.
* **The name collides with an entity already registered to a *different* root or path** → the import
  is **REFUSED**. Nothing is mutated and `ImportConflictError` is raised, explaining that a `rename`
  mechanism to resolve such collisions is planned (future work, not 1.6.0) and that for now the user
  must rename or relocate one of the two manually.

`ImportConflictError` therefore always means "I left both sides exactly as I found them". The shared
message text is built once in `_conflict` so standalone and workset refusals read identically.

### The CROSS-KIND case is NOT one of them — it imports and warns

The three outcomes above are all **same-kind**: a workset name against another workset, a box name
against another box. A **cross-kind** collision — an imported workset's derived name matching an
existing PRIMARY BOX name — does **not** refuse. It imports, and `import_named_workset` logs one
warning saying the bare name now resolves to the box and naming the escape hatch that reaches the
workset (`names.cross_kind_shadow_hatch`, the same wording bare-name resolution uses).

🛑 **Do not "make this consistent" with `create_workset`, which refuses the same collision unless
`--force`.** The two differ on purpose ([R139]): refusing at create is affordable because a human
typed the name and can retype it. On an import nobody typed anything and there is no `--force` to
offer, so a refusal would strand the copied tree — the same silent failure the import exists to end,
wearing a different hat.

⚑ The derived name does clear create's OTHER bars — no empty basename, no reserved sentinel. It
RETURNS `None` where create RAISES, because this one is answering a treewalk stepping past an
ordinary directory rather than a user who can retype: a directory named `default` must not make
every command fail.

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

`root` is the workset root. There is no file to read: the caller has already matched
`workset.is_workset_skeleton`, and the name is **derived** as `root.name`, the leaf directory
basename ([R139]).

Order of operations: derive the name → bar check → already-registered check (plus stale-entry clear)
→ same-kind collision check → cross-kind warning → journalled register → alert. The section it
writes is `registry.worksets`, the name → root index backing both name lookups and workset
discovery. Registration goes through `names.register_name` — the SOLE writer of that section, and
the guard that refuses `$HOME` as a root, which matters here and not at `create_workset`: create is
handed a path, while the ancestor walk arrives at `$HOME` under its own steam.

Returns `None` when the basename cannot be a workset name — empty (only the filesystem root) or a
reserved sentinel.

⚑ `primary_workset` is a REQUIRED keyword argument, not a defaulted one, because it is the sole
input to the cross-kind check: a caller free to omit it would import a shadowed workset without the
one warning that says how to reach it.

⚑ It does **NOT** rewrite the workset-create skeleton. It only registers an already-on-disk
workset — a workset import never seeds a box, which is why its journal bracket is register-only like
the standalone one. A workset has no single `home/`, so the entry is keyed on the workset ROOT.

## Completeness sweep

`prose-relocation-check.py`: every removed prose line is accounted for above. The prose kept in the
source is the trimmed module docstring, the one-line descriptor on each docstring-bearing symbol,
the section banners, and the short `⚑` / HARD INVARIANT markers whose deletion would let a future
edit break something silently at that exact line:

* at `_STANDALONE_BOX_DIR` — the second spelling in `paths_defaults.py` must move with it;
* at `_journal_register`'s clear step — the clear is the immediate post-register step, and a raise
  intentionally leaves the entry;
* at `_clear_stale_import` — only register-only entries are cleared, never a `create` entry;
* at the marker gate and the kuid composition in `import_standalone` — both look like they could be
  simplified to `project.mode` and to the dir leaf respectively, and both must not be;
* at the derived name in `import_named_workset` — it returns where `create_workset` raises, and the
  cross-kind collision warns where `create_workset` refuses; both read like inconsistencies and
  neither is.
