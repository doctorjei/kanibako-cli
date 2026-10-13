# Drop-in import-on-discovery — reconciling the registry from on-disk truth

`import_reconcile` is the module that lets a box, workset or project tree be **copied or moved** —
to a new directory, a new disk, a new machine — and still work. On-disk metadata is
**authoritative**; `system.registry` is a *derived, rebuildable index*. When detection or resolution
walks into an on-disk workset that the registry does not know about, kanibako **imports** it:
registers it into the appropriate registry section, prints an ALERT to stderr, and proceeds. There
is **no confirmation prompt** — moving a tree "effectively becomes an import".

The whole module is one uniform "reconcile the registry from on-disk truth" mechanism, deliberately
with **no per-mode special-casing**. Each mode differs only in which marker it reads and which
registry section it writes.

## The two live modes, and who calls them

* **NAMED** — `import_named_workset` runs lazily in `settings/paths.py`'s `detect_project_mode`
  ancestor walk — nothing sweeps eagerly — whenever `project/workset.is_workset_skeleton` matches a
  level: the four dirs `create_workset` stamps, present together. After the import the walk re-runs
  `_check_workset`, which now resolves.
* **STANDALONE** — a standalone box is unregistered by default, so a standalone root is only
  CHECKED during resolution (`check_standalone`, from `detect_project_mode`, `box_resolve.
  detect_box_mode`, and `commands/box/_parser.py`), which never registers one. `import_standalone`
  runs only from `box register`.

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

`commands/box/_parser.py`'s `register` verb uses `import_standalone` for STANDALONE
**register-later**, with the comment *"⚑ REUSE `import_standalone`: it is already index-only +
seed-free."* That is the reason the function must stay index-only — a seed step added here would
fire on a plain `kanibako box register`.

### `check_standalone` — the resolver's pass over a standalone root

`check_standalone(registry, root, *, journal=None, early)` never registers a box. In order:

1. `refuse_second_link`: another spelling of a registered standalone's identity — a link, a bind
   mount, or a hard-linked `workset.yaml` (`registry_store.standalone_identity_holder`) — raises
   `ImportConflictError` naming the registered root ([R188]).
2. A registered root gets its stale J2 entry cleared, and the check returns.
3. An unregistered root that is another spelling of a PARKED (`deregistered`) standalone's
   directory, or shares its `workset.yaml` file, is refused with the cure
   `kanibako box register <root>`. That refusal lives here only, not in `refuse_second_link`,
   because `box register` is the route that re-registers a parked box at a new spelling.

## No PRIMARY mode

This module has no primary import or reconcile. A primary box does not self-describe its identity
on disk, so there is nothing on disk to re-import from: for primary mode the registry is the sole
identity authority.

A frozen, non-shipping reference copy of a primary reconcile lives in
`salvage/primary_reconcile.py`; it is the starting point for a heuristic `system recover`. 🛑 Do
not import it here.

## Conflict semantics — the same rule in every mode

Three outcomes, and only three:

* **Already registered to this exact path** → silent, idempotent no-op. The function returns the
  registered name and mutates nothing.
* **No marker** → `None`. There was nothing to import.
* **The name collides with an entity already registered to a *different* root or path** → the import
  is **REFUSED**. Nothing is mutated and `ImportConflictError` is raised, explaining that a `rename`
  mechanism to resolve such collisions is planned (future work) and that for now the user must
  rename or relocate one of the two manually.

`ImportConflictError` therefore always means "I left both sides exactly as I found them". The shared
message text is built once in `_conflict` so standalone and workset refusals read identically.

Box and workset names are separate namespaces (system-design § Detection & import); only a
same-kind collision refuses. A workset sharing a primary box's name imports normally.

⚑ The derived name does clear create's bars — no empty name, no name-rule flaw, no reserved name. It
RETURNS `None` where create RAISES, because this one is answering a treewalk stepping past an
ordinary directory rather than a user who can retype: a directory named `default` must not make
every command fail.

## `box_data/` — the box dir, and the journal key it forms

The standalone box's host-side box dir is the resolved `workset.boxes` (by default `<root>/box_data`,
a sibling of the root `workset.yaml`), and holds `home/`.

⚑ Its path is **not composed in this module**: the journal key comes from
`settings.paths.standalone_box_store(root, early=…)`, so it cannot drift from
`commands/start.py::_box_journal_key` (`shell_path.parent`).

The J2 journal entry for a standalone import is keyed by that store — the dir *containing* `home/`
— which is the uniform host-side box-dir key scheme shared with J1
(`str(Path(shell_path).parent)`). A workset has no single `home/` dir, so a workset import keys its
entry on the workset ROOT instead. Both keys are known **pre-registration**, which is what makes a
write-ahead entry possible at all.

## J2 — the lifecycle journal, register-only write-ahead

Import and connect **REGISTER** an externally-seeded box and **NEVER seed**
(`~/canon/notebook/procedures/seed-and-create-model.md`, "The model": *"`create` seeds + registers.
`connect` / import register only — the box was seeded where it was created, so seeding again would
clobber it."*). J2 makes that register seam ATOMIC via the journal: a write-ahead
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
test or a registry-only utility — the wrapper degrades to a plain register.

### `_journal_register` — the bracket

Write-ahead order, by DESIGN: **write entry → register body (the `with` block) → clear entry.** The
entry is cleared as the IMMEDIATE step after the register, which is what makes the invariant hold at
rest.

If the register body raises — a genuine collision — the entry is **intentionally LEFT** (the import
is incomplete) and the exception propagates. Recovery resumes it on the next resolve. This is not an
oversight in the error path; do not "fix" it by clearing on exception.

A `None` journal is a no-op bracket.

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

`root` is the standalone project root: the dir whose `workset.yaml` stores the marker.

**The marker gate** (design D4): the root `workset.yaml` storing `workset.registry: null` is the
standalone signal (`box_resolve.stores_standalone_registry_null(root)`); `box_data/` is not the
marker. No marker → `None`, nothing to import.

**Name composition is kuid-first.** The stored `workset.kuid`, sparse-persisted at create, prefixes the **LIVE dir leaf**:
`<kuid>_<leaf>`. The rule is `launch.box_resolve.standalone_box_name`, the one
`resolve_box_identity` also calls; import passes no registry name, the box being unregistered. It is
what lets a MOVED box keep its stable kuid identity while the leaf tracks the new directory. A
pre-kuid box (the `kuid.SENTINEL` value) falls back to the plain dir leaf.

Order of operations: already-registered check by `standalone_name_for_root` (plus stale-entry clear)
→ marker gate → pending-create check → `refuse_second_link` → compose name → collision check →
journaled register → alert. It returns `None` while a `create` entry is pending for the store:
that box is `create --recover`'s, and an import would overwrite its journal entry. The composed
name is registered to `root` and alerted, UNLESS it already maps (case-blind) to a DIFFERENT root
→ `ImportConflictError` (refuse, no mutation). The register is register-only, with **no seed**: the
box is already seeded on disk.

## `import_named_workset` — the `worksets` section

`root` is the workset root. There is no file to read: the caller has already matched
`workset.is_workset_skeleton`, and the name is **derived** from the leaf directory basename
([R139]) as `identifiers.derived_name(root.name)`: `_` for each `-` and `.` (spec §0).

Order of operations: derive the name → bar checks → `$HOME` guard → already-registered check by
DIRECTORY (`names.lookup_by_path`, under any name; plus stale-entry clear) → same-kind collision
check → journaled register → alert.
The section it writes is `registry.worksets`, the name → root index backing both name lookups and
workset discovery. Registration goes through `names.register_name` — the SOLE writer of that
section.

It returns `None` on FOUR declines: an empty name (only the filesystem root has one); a `name_flaw`
(warns, naming the flaw); a reserved name (warns); and a root that IS `$HOME`. An already-registered
directory returns its registered name.

⚑ **The `$HOME` decline is the one that is not obvious, and it is deliberately one step earlier than
`names.register_name`'s own refusal of a `$HOME` root.** That refusal still stands; it is simply
never reached from here. It has to be pre-empted, because `create_workset` is HANDED a path while the
ancestor walk arrives at `$HOME` under its own steam — `detect_project_mode`'s stop condition fires
only AFTER the level has been tested — so a home directory that happens to carry the four-dir
skeleton would RAISE out of **every** command's mode detection. Declining leaves it what it already
was: a plain primary-mode dir.

⚑⚑ **The guard is an explicit `utils.is_home_dir(root)` test, deliberately NOT a `try` /
`except` around the register.** `register_name` raises `ProjectError` for a `$HOME` root and for an
already-registered name alike, so catching it here to decline would swallow the SAME-KIND collision
refusal too — turning "I left both sides exactly as I found them" into a silent `None`.

⚑ It does **NOT** rewrite the workset-create skeleton. It only registers an already-on-disk
workset — a workset import never seeds a box, which is why its journal bracket is register-only like
the standalone one. A workset has no single `home/`, so the entry is keyed on the workset ROOT.
