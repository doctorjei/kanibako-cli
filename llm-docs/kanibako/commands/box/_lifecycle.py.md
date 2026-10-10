# Box Lifecycle — the transactional `remap` / `move` / `convert` engine

⚠️ **This mirror is COMPLETE for displaced prose** (relocation pass, 2026-08-11). A symbol absent
below carried nothing worth displacing — never "does not exist". The source keeps one-line
descriptors and `⚑` markers; the reasons live here.

🛑 **This module is DESTRUCTIVE.** It `copytree`s a user's project directory and, once the whole op
has succeeded, `rmtree`s the old one, and
it removes registry entries that are the only record of where a box lives. Almost every note below
exists because a step's ORDER, or the exact path it was handed, is what keeps a deletion from
taking the wrong tree. Read the step order before changing anything.

⚑ **Not to be confused with the top-level `kanibako/box_supervisor.py` + `kanibako/box_lifecycle.py`
PID-1 pair.** Same word, unrelated job: those are the in-box keep-alive, pinned flat and
stdlib-only. This module is the host-side project-lifecycle engine.

## What it owns, and what it does not

`box remap` · `box move` (alias `mv`) · `box convert`, plus `copy_into_workset`, the std-aware copy
path `box duplicate` calls. Three CLI verbs over ONE engine, because the three are the same
transaction with different axes moved.

It does **not** own `create`, seeding, or the lifecycle journal. A box is seeded ONCE at `create`
and never re-seeded, and the create path's gate (registration OR a pending journal entry) lives in
`commands/box/_parser.py`; the seed itself is `commands/start.py::_seed_box_home`. Nothing in this
file seeds, and nothing here should learn to.

## The two axes

A project's identity splits into two independent axes, and `TargetSpec` names one field for each.
Keeping them separate is what lets one engine serve three verbs:

* **location** — where the workspace files physically live. `TargetSpec.location` is
  `INPLACE` (keep the files where they are), `BARE_INTO_WS` (move into
  `{ws}/workspaces/<name>`; valid ONLY with a workset ownership target), or a concrete `Path`.
* **ownership** — which mode/workset owns the project. `TargetSpec.ownership` is `UNCHANGED`,
  `"default"`, `"standalone"`, or a workset name — **a plain string, NOT prefixed with
  `workset:`** (that prefixed form is the `ProjectState.owner` token, a different vocabulary).

`TargetSpec.name` optionally renames the project at the destination, defaulting to the existing
name.

### `records_only` — the `remap` axis-of-none

`records_only` is `remap`'s whole semantics: **the workspace has ALREADY moved on disk**; record
the new `location` WITHOUT copying or deleting any files. Only meaningful with a concrete `Path`
location.

⚑ It is why three of `_validate`'s guards carry an explicit `records_only` exemption, and each
exemption is a real invariant rather than a convenience:

* the **destination-occupied** check — the files are *supposed* to be at `dest` already, so
  requiring `dest` to be empty would refuse every correct `remap` (and a no-op same-path remap is
  fine);
* the **CWD-inside-old** guard — `remap` removes nothing, so it cannot strand the user's shell;
* **STEP 5** — there is no old workspace to clean up. ⚑ A `remap` of an in-tree workset member
  whose old leaf is still a directory prints `Note: left <old>; remap deletes nothing` on success:
  the release keeps that leaf, so a user who copied rather than moved learns the old tree is theirs.

⚑ One guard deliberately does **not** take the exemption. A records-only relocation still ends up
RECORDING its `dest` as the box's workspace, so the in-tree-landing refusal below applies to
`remap` unchanged; only its message changes ("move it to `<workspaces>/<name>` and remap").

## `ProjectState` — the uniform descriptor

* `owner` is the canonical ownership token: `"primary"`, `"standalone"`, or `"workset:<name>"`.
* `ws` is the loaded `Workset` when the owner is a workset, else `None`.
* `is_external` is True when the live workspace is not one of the owning workset's OWN workspaces
  (`project.workset.is_in_tree_workspace` — the root OR the resolved `workset.workspaces` dir, each
  tested with the leaf followed and with only its parent resolved) — i.e. a connected-external
  project, the user's own directory. **Every destructive branch keys on this flag.**
* ⚑ **Two vault fields, and they are not interchangeable.** `enable_vault` is the RESOLVED
  `box.enable_vault` — the whole cascade, `base < system < workset < box`, via
  `config.resolve_box_enable_vault` (it was a two-file read until 2026-08-29, which is why a
  system-scope value used to be ignored).
  `box_authored_vault` is what the BOX ITSELF authored: one file, `config.read_box_enable_vault`,
  which since the same date takes no fallback parameter at all. **Only the authored
  value is ever persisted at a destination box tier**; writing the resolved one pins a value the box
  was merely inheriting, so a later edit to the workset that published it can no longer reach the
  box, and the default follows the box into a workset that never declared it.

## The canonical 5-step order

From the redesign DESIGN. `execute_lifecycle` runs it; `_run_steps` is the body.

1. **Validate everything up front** (`_validate`) — refuse early ⇒ zero partial state.
2. **Move files**, if relocating.
3. **Update location records / markers.**
4. **Apply the ownership / mode change** — re-root metadata/shell/vault, registry, names, write the
   destination `box.yaml` (sparse — see the drifted-claim note below for what it does NOT carry).
5. **Retire the old workspace, on success only** — never the user's external source dir.

Steps 2–5 push compensating actions onto an unwind stack; on ANY failure the stack runs in
reverse to restore a consistent state, then re-raises. `confirm`, if given, is called AFTER
validation; returning False aborts cleanly (no changes) by raising `ProjectError`.

⚑ The catch around `_run_steps` is on `BaseException`, not `Exception`. A `KeyboardInterrupt`
arriving after the ws→ws release would otherwise skip every compensating action, leaving the
relocation's stash in `$TMPDIR` — a copy of the box home, so possibly holding credentials — with
nothing printed at all. The interrupt is still re-raised, so the CLI's own handling is unchanged.

### ⚑ Why step 1 holds everything

Refusing early is what makes "zero partial state" a property of the ENGINE rather than of each
step's individual care. A refusal added inside `_run_steps` instead of `_validate` would fire after
files had already moved — which is precisely the failure the unwind stack exists to make survivable
and the ordering exists to make impossible. **New refusals belong in `_validate`.**

### ⚑⚑ The three refusals that decide whether a landing is recordable

A relocation's `dest` is a real directory the user named, but the box can only ever RECORD one
workspace per name. These three are what makes that a checked fact rather than an emergent one, and
each one fires before the first `copytree`:

* **An in-tree landing must be the leaf the target records.** With a named target, a `dest` that
  `is_in_tree_workspace(target_ws, dest)` is in scope for that workset's member, and the only path
  such a member lives at is `workspaces/<name>`. Any other in-tree path is copied to by STEP 2 and
  copied again to `workspaces/<name>` by STEP 2b, so the box would record `workspaces/<name>` while
  the user asked for the other path — a stray second copy of their data, and a source leaf retired
  against a landing it never had. The refusal names both paths, and its advice is in the refused
  verb's own syntax: `TargetSpec.verb` (`"move"` / `"convert"`, set by `run_move` / `run_convert`)
  and `records_only` (remap) pick it, because `box move` has no `--move`. The field is advice only;
  no check reads it. An EXTERNAL `dest` is not in-tree and is always allowed, which is what keeps a
  same-workset same-name move to a path of the user's own working.
* **An occupied target leaf is the user's, not this op's.** `add_project` adopts whatever
  `workspaces/<name>` / `boxes/<name>` / the two vault leaves already hold, so a landing onto one
  is a silent merge. Exempt: `records_only` (its files are *meant* to be there) and a leaf that IS
  the source's own — same workset, same name, whose store is stashed and released in leg 1 and
  whose workspace leaf is the same-name move above. The rollback's created-leaves rule is the second
  line of defense for the leaves this one lets through.
* **An external source cannot be relocated at all.** Its "workspace" is the user's own directory;
  a copy would leave the real files where they are and record a path that holds nothing, and no
  ownership change makes that better. The message points at an in-place `convert` or a `remap`. It
  is deliberately checked in `_validate` rather than in `run_move`, which is what puts
  `_abort_if_locked` ahead of it: a LOCKED external box reports the lock and exits 2, because the
  lock is the first fact the user can act on.

⚑ None of the three takes `force`. `force` answers a name collision and the CWD-inside-old stranding,
not "this landing is not recordable".

### STEP 2, arm by arm

The four arms are mutually exclusive and each answers a different question about what "relocating"
means for that source. ⚑ For a workset→workset re-root the *workspace* is not what relocates here
when the source is EXTERNAL; for an INTERNAL ws→ws the move IS required, and `_validate` has
already enforced that (`STUBBORN_INPLACE_MSG`). A standard move `copytree`s the workspace to `dest`.

* **`records_only`** — files presumed already at `dest`; copy and remove nothing.
* **internal relocate** — `copytree` the workspace to `dest`, push an `rmtree(dest)` unwind. The old
  tree is deleted only by STEP 5's success-only retire. ⚑ **When the target is STANDALONE and the
  source is not workspace-at-root, the copy is aimed at the workspace dir that root resolves
  (`_resolve_standalone_workspaces(dest, None, …)`), not at the root itself** — onto the root, a
  user's own `<workspace>/box_data/` lands ON the store path and the store copy merges over it by
  name, which `--purge` then deletes. `new_workspace` stays `dest` either way; `_to_standalone`
  resolves the workspace dir itself, so the two agree. Workspace-at-root is exempt: there the store sits
  beside the workspace's own content, and the ignore keeps exactly the resolved store out of the copy.
* **external relocate** — the "workspace" is the USER'S OWN directory. It is NEVER moved, only
  re-recorded; `dest` becomes the new recorded location when it is the destination of an
  internalizing move. Re-pointing an external project to some other external location is out of
  Phase-1 scope beyond the ws→ws repoint, which ownership handles.
* **in-place convert of a STANDALONE source** — the one arm that is about the source's SHAPE
  rather than about relocating. ⚑⚑ **Its root is `state.metadata_path` (drift I), READ, never
  `state.workspace_path.parent`.** The workspace is the RESOLVED `workset.workspaces`, so the
  parent is the root only in the default layout; the positional spelling aimed a step that MOVES
  USER DIRECTORIES at `<root>/nested` under a one-level repoint, and at a directory kanibako was
  never given under an absolute one. Four cases, and the last two are why it is a `if/elif` and
  not one line:
  * **target is standalone** — an in-place RENAME. `_to_standalone` reads its `root` argument as
    the root, so it gets the root; the earlier spelling handed it the workspace and built a
    second box inside the first (see `_to_standalone`).
  * **workspace resolves AT the root** (`workspaces: @meta.workset.path`) — already the shape
    every other mode wants; nothing to lift.
  * **workspace resolves BELOW the root** — the reverse of drift H: lift the files up to the root
    and root the converted box there, so the project stays at the directory the user is standing
    in rather than a subdir.
  * **workspace resolves OUTSIDE the root** — [R144]: a directory the USER named. Nothing moves
    and the keep is REPORTED naming the key. Every non-standalone mode's workspace is just its
    project dir, and that dir may be anywhere, so keeping it is not a special case — it is the
    ordinary shape. Lifting instead emptied and deleted the user's directory and scattered its
    contents into a sibling tree.

### STEPS 3+4 are interleaved, deliberately

The destination metadata roots DEPEND on the target owner, so markers cannot be written before
ownership is decided. `_apply_ownership_and_markers` therefore does both at once: compute the new
metadata/shell/vault dirs for the target owner, copy them, write the destination `box.yaml`,
update registry/names, then clean up the old side. It dispatches to exactly one of `_to_workset` /
`_to_standalone` / `_to_default`.

⚑ **A drifted claim, corrected rather than relocated (2026-08-11):** the pre-relocation prose said
this path writes a "rewritten `settings.yaml` (mode + workspace override + hash + markers)" — in
five places, including the module docstring and `run_remap`. **No hash is computed or written
anywhere in this module**, and under sparse identity (P8b) the only thing written to the
destination `box.yaml` is the non-default `box.enable_vault`; mode and workspace override are
not persisted either. The word `hash` survived only in comments, never in code. Those claims were
DROPPED, not carried here.

### ⚑ STEP 4b runs AFTER identity is final (A9 / IN-8)

Channel relocation reads the box's address off `new_state`, not `state`, because a **standalone
convert REGENERATES the box name**. Run before identity is finalized, it would move the partition to
an address that is about to change. This is the one ordering constraint in the file that is not
about file safety.

### ⚑⚑ STEP 5 retires the old workspace ON SUCCESS ONLY

**Rule: no lifecycle step deletes a workspace before the whole op succeeded AND a copy of it landed
elsewhere.** The releases (`release_project` in `_to_workset`'s leg 1 and `_remove_old_metadata`)
drop records and the store, never the workspace leaf, so every failure path finds the source
workspace whole. See **Rollbacks delete only what the op created** for the store. STEP 5 registers `_retire_old_workspace(old, dest)` with
`unwind.on_success` for a real, INTERNAL move (`not records_only and relocating and dest and not state.is_external`). The
retire itself skips an absent `old`, and an `old` that is or holds the copy's landing — a move into
its own subtree, which `_validate` refuses first. Every relocation that reaches it records its
landing, so an `old` the box still records is not a case it has to recognize.

It used to be an in-op `rmtree(old_ws, ignore_errors=True)`, and the named release deleted the leaf
even earlier, before the copy that read it — so a rename in place, or any failure after the
release, lost the workspace. An in-place rename (`convert --name`) never passes STEP 2, so `_to_workset` registers the
same retire after its own copy.

⚑ An EXTERNAL landing in the source's own workset (same name) finds `workspaces/<name>` held by the
old leaf, so `add_project` writes no discoverability link. STEP 5 therefore registers
`ensure_discoverability_link` AFTER the retire (success actions run in registration order); on an
occupied leaf it is a no-op.

## The unwind stack (`_Unwind`)

A LIFO stack of compensating actions for failure-consistency, defined in `project/workset.py` and
imported here. Each pushed action is a zero-arg callable that reverses a forward step. On `run()` actions execute in REVERSE order, and individual
failures are swallowed — best-effort restore, so one bad unwind does not mask the rest.
A `KeyboardInterrupt` from an action (a second Ctrl-C) is held until every action has run, then
re-raised; `note_interrupted` does the same, so a second interrupt neither stops the unwind part-way
nor drops a `Note`.

`on_success` is the second list: actions that run only when the WHOLE operation succeeds. It exists
for work that must wait for completion: discarding the ws→ws stash (`_dispose_stash`), removing a
workset source's old store (`_retire_old_store`), retiring the old workspace
(`_retire_old_workspace`), writing a deferred discoverability link, and the `remap` Note.

⚑ Both lists swallow what an action raises, so an action that can leave something behind prints its
own `Note` naming it. None of them prints through the caller.

### ⚑⚑ Rollbacks delete only what the op created

**Rule: no failure path deletes a leaf that existed before the op, and a store is deleted only once
nothing can still need it.**

* **Target leaves.** Before `add_project` registers a workset target, `_existing_member_leaves`
  records which of its four leaves (`_member_leaves`: `workspaces/<name>`, `boxes/<name>`, the two
  vault leaves) are already on disk. `add_project` adopts an existing leaf, so the unwind
  (`_unwind_target_member`) drops the record and removes only the others: a link is unlinked, the
  box tree goes through `remove_box_tree`, and a leaf it cannot remove is named in a `Note`. The same
  rule covers `copy_into_workset`'s rollback (`box duplicate --to named`).
* **Leg 1 (ws→ws).** `_restore_source` is pushed BEFORE `release_project` +
  `remove_member_store`: once the release starts, the stash holds the only copy of the store, and a
  store removal that fails part-way (an undeletable file in a vault leaf) must restore from it.
  Before, the leg-1 `except` dropped the stash, and the box, its home, and its record were gone.
  A box tree `remove_box_tree` could not delete does not raise, so leg 1 then calls
  `_report_store_leftovers`: the leftover may hold credentials, and a later `add_project` under the
  same name would adopt it.
  A failure while the stash is still being taken disposes of the partial stash; nothing is released
  yet.
* **`_restore_source`** runs its four steps (re-register, box-tree copy-back, the two vault
  copy-backs) independently: each failure prints `Note: could not restore <what> at <where>: <err>`
  and the rest still run, so a raising `add_project` never skips the copy-back. It disposes of the
  stash only when every step succeeded; otherwise it prints `Note: kept <stash>; ...`. A
  `KeyboardInterrupt` in a step counts as that step's failure (`Note: may not have restored ...:
  interrupted`) and is re-raised after the Note, as `_Unwind.run` does.
* **The stash** is disposed of through `remove_box_tree` (`_dispose_stash`), because it holds a copy
  of the box home and its 0o555 canon dirs defeat a plain `rmtree`. A `False` prints
  `Note: could not remove <stash>; it may hold credentials`.
* **A workset source converted to primary or standalone** (`_remove_old_metadata`'s workset arm)
  releases its record in-op and registers `_retire_old_store` on success. A store removal that fails
  there lands after the new box is complete: rc stays 0 and `Note: could not remove the old store of
  '<name>'[: <err>]; left <paths>` names every leaf still on disk.

⚑ **Reverse order is load-bearing, not incidental.** `_to_default`'s FIX1 restore relies on it: that
unwind runs BEFORE any later one, so a failed re-register leaves the source's `name -> old path`
mapping intact rather than orphaned.

## Mode shapes the steps must respect

### Drift H + drift I — the standalone layout

⚑ `_to_standalone` takes the standalone ROOT — the project dir, not the live workspace — and says
so in its parameter name (`root`), because the caller's variable is `new_workspace` and reading one
as the other is the defect this pair keeps producing. (The returned `ProjectState.workspace_path` is
the resolved workspace dir; `metadata_path` is the root, and it is where every other step must READ
the root from.) A standalone box's tree roots at `<root>` with:

* `workset.yaml` **AT THE ROOT** (drift I — and `ProjectState.metadata_path` for a standalone IS
  that root),
* a `box_data/` marker dir (home + helper log) — the real box metadata dir,
* `vault/{ro,rw}/`,
* and the **live workspace as a `workspace/` SUBDIR** (drift H) — ⚑ that leaf is the DEFAULT of
  `workset.workspaces`, not a fixed name; a standalone root is a degenerate workset root, so every
  directory in this list except `box_data/` and the root meta is a repointable key.

Every other mode roots its live workspace at the project dir itself. That single difference is the
source of the consolidate/unconsolidate pair, of the root-artifact split described under
`_STANDALONE_FIXED_ARTIFACTS`, and of the `box_metadata_dir` rule below.

### ⚑⚑ `box_metadata_dir`, never `metadata_path` (M-8)

Three sites copy the source's box metadata, and all three must go through
`box_metadata_dir(state.mode, state.metadata_path)`. For a standalone source the two differ (root
vs `box_data/`), and using the root instead:

* drags `workspace/` and `vault/` into the destination's box dir,
* lands the source's **WORKSET-tier** settings file at the destination's **BOX** tier, and
* on a standalone→standalone move, strands `<dst>/box_data/box_data/`.

### ⚑ Sparse identity (P8b / Option A)

Neither `_to_default` nor `_to_workset` writes a `project:` or `resolved:` identity block. A moved
box's identity lives in the PRIMARY `boxes:` membership (default mode) or the global name index plus
the target workset's `boxes:` registry (workset mode). The ONLY thing persisted is the non-default
`box.enable_vault`, written sparsely and carried from the source so a disabled-vault box stays
disabled across the move — and it is `state.box_authored_vault`, **never** `state.enable_vault`
(see `ProjectState` above). A box that leaves the workset loses the workset's default, because the
value was the workset's.

The same ruling is what makes `_default_state_from_meta` work: the PRIMARY-membership reverse-lookup
hit IS the existence signal, because identity no longer self-describes on disk — so there is no
`project.mode` presence gate to consult, and `enable_vault` is a plain box-scope `box.enable_vault`
resolve, decoupled from identity.

And it is why `_to_workset`'s EXTERNAL arm reads the recorded workspace back out of the per-workset
`boxes:` registry that `add_project` just wrote: under sparse create the box's own `box.yaml`
stops self-describing, so the D10 connection record is the authoritative external-workspace source.

## Naming — the PRIMARY membership register

Four separate rulings converge on `_to_default`, and they are easy to mistake for one rule.

**F-7 — an explicit `--name` landing in default mode is HONORED** (it used to be silently dropped)
and is held to the SAME per-kind name policy as `create`:

* a **WORKSET-name** collision refuses UNLESS `--force` — the box would shadow the workset in
  bare-name resolution;
* a **SAME-KIND** collision (another primary box) refuses UNCONDITIONALLY.

`_validate` checks this up front, via `check_primary_box_name_free`, so a name refusal costs no file
copy. NAMED-workset targets are out of scope (the same-kind workset-membership check covers them);
standalone targets are outside the cross-kind domain entirely.

**F-3-fix2 — an in-place rename of a primary box is REFUSED, not ignored.** For a primary-source
SAME-PATH edge, a `--name` that DIFFERS from the current name would be an in-place rename, which is
not supported: the box keeps its `boxes/<name>` metadata dir and its registration at this path.
Rather than silently drop the name, `_default_rename_name` raises an actionable `ProjectError`
telling the user to move the box to rename it.

**L2 — same-name in-place convert.** When the converted box reuses its own name, the destination
metadata/vault **IS** the source. `preserve_name` therefore tells `_remove_old_metadata` to neither
re-unregister the name nor delete the reused `boxes/<name>` dir — cleaning up here would delete the
box that was just written.

**FIX1 — self-reuse is not a collision.** A relocating move/convert that KEEPS the same name reuses
the SOURCE box's OWN registration (its name → its own current path). Both the `_validate` guard and
the `_to_default` register path must exempt it, or `register_primary_box_name` reads the box's own
still-live entry as "already registered". Genuine same-kind and cross-kind collisions (where `mint`
names a DIFFERENT box, or a workset) still refuse.

### The `_to_default` register dance, in order

⚑ **The mint is decided BEFORE the reuse-unregister**, because the same-path reuse case is DETECTED
BY the source's still-live registration. Move the `_default_rename_name` call below the unregister
and the detection silently stops working.

A PRIMARY source's own name is still registered when `_to_default` starts, so register/assign would
read the source's OWN entry as a same-kind collision — auto-suffixing `foo` → `foo2`, or refusing
outright. Two reuse shapes need the source entry freed FIRST:

* **SAME-PATH in-place convert** — the source is registered AT `new_workspace`; unregister so
  `assign_primary_box_name` reuses the name verbatim rather than suffixing it.
* **RELOCATING same-name move** — the source's OWN name (`== mint`) is still registered at its OLD
  path, while `new_workspace` is the fresh destination. Unregister so the re-register at the new
  path is not a self-collision, and push a FAILURE-WINDOW unwind that RESTORES the old registration
  if the re-register (or any later step) fails.

In both, `preserved_name` is set so `_remove_old_metadata` leaves the reused name and metadata
alone.

`_primary_source_own_name` is the reverse lookup those guards exempt on: it answers what name the
SOURCE primary box is currently registered under, or `None` when the source is not a registered
primary box.

## Channel partition relocation (D-M10, §6)

A box's mailbox and system-scope share are partitioned by `@meta.workset.name` and keyed by box
name, so a move/convert that changes the workset and/or the box name **changes its channel
address**. `_relocate_channel_partition` moves the OWN `mailboxes/<ws>/<box>` and `share/<ws>/<box>`
dirs from the OLD partition to the NEW one.

⚑⚑ **EACH SIDE'S PARTITION IS READ THROUGH ITS OWN WORKSET'S KEYS (2026-08-26).** Both addresses
were built from `(std, ws_token)` alone, which can only produce the partition's DEFAULT — while
`channels.box_channel_addresses` has routed through `workset.channels.{mailboxes,share_global}`
since R-35. So a workset that repointed `mailboxes` had its boxes MOUNTED at the repointed address
and this step moved the default directory: an empty one, leaving every message the box had received
stranded at an address no longer registered to it. `own_partition_dirs` now REQUIRES a `ws_root`,
and `_state_ws_root` is the `ProjectState` twin of `channels.workset_root` that supplies it —
primary → `std.primary_workset`, named → `state.ws.root`, standalone → `state.metadata_path` (which
IS the standalone root). It has the same three arms in the same order as `_state_ws_token`, because
a relocation needs both answers for both sides: the token says WHICH partition, the root says which
`workset.yaml` may repoint it.

⚑ Reading a key put a **refusing** resolver on this path for the first time — the channel key read
raises, naming the key, on a repoint it cannot resolve. That is caught here, warned and skipped:
this step runs AFTER the files have moved, and a settings error in a best-effort cleanup must not
abort an otherwise-complete lifecycle operation.

⚑ The idempotent no-op is still compared on the TOKEN and the box name, not on the resolved paths:
two tokens that happen to repoint to one directory are still two partitions, and the box's own
subdir is what moves.

**BEST-EFFORT, by ruling.** Any failure — missing source, destination already present, permission,
an unresolvable channel key — is WARNED and swallowed; the lifecycle continues. It is deliberately
NOT on the unwind stack: a partial move is not catastrophic and re-running reconciles. No forwarding
marker is left for stale cross-box references to the old address.

⚑ **Workset-LOCAL channels (`common` / `chat`) are NOT relocated** — they are scope-owned, not
box-owned. The box simply stops mounting the old workset's local channels and starts mounting the
new one's.

## The canon-skeleton hazard (J-7)

Three sites copy a box home, and all three re-assert the canon skeleton afterwards, because
`copytree` reproduces the skeleton's **555 MODES but never its OWNERSHIP**. The copy lands
host-user-owned — which in-box is the agent, who can chmod it back —
so `materialize_canon_skeleton` is called on the destination shell (idempotent).

The same fact makes removal asymmetric: a plain `shutil.rmtree` over a tree containing a root-owned
canon skeleton fails with `EACCES` and leaves the old box behind. Every box-tree deletion therefore
goes through `remove_box_tree`, which escalates — including the one on the UNWIND path
(`_unwind_box_tree`), since `_copy_metadata` creates a skeleton at its own destination and an
`ignore_errors=True` rmtree would swallow the `PermissionError` and silently leave the failed
destination in place.

## Layout facts recorded here so nobody re-derives them

* **PRIMARY vault** is a per-box `<name>` LEAF under the primary workset's RESOLVED
  `@workset.{vault_ro,vault_rw}` (default `@config.primary_workset/vault/{ro,rw}/<name>`, Phase 5),
  so it is NOT under `metadata_path`. `_remove_old_metadata` removes the per-box `ro`/`rw` dirs
  explicitly and never their shared arm, which holds EVERY box's vault.
  ⚑⚑ **The containment guard's subject is the ARM (`std.primary_vault_{ro,rw}`), not the workset
  root.** It was the root only because the arms were composed off it and could not be anywhere
  else; once both became repointable keys a root-subject guard began SKIPPING an out-of-root
  repoint, silently leaving the box's real vault behind. Naming the arm is also strictly NARROWER
  than the root was — it admits exactly the leaves this loop may delete.
  ⚑ **Containment is STRICT** (`arm not in vault_dir.parents`, the `_assert_deletable` spelling):
  `relative_to` ACCEPTS an equal path, so a leafless `vault_dir` would take every box's vault. A
  path that fails the guard is REPORTED on stderr, never silently skipped.
* **Every tree copy here keeps symlinks VERBATIM** (Q70/Q74): the vault carry, the workspace, the
  box data and home, and both workset-to-workset stash legs go through `copy_tree_keeping_links`
  in `src/kanibako/tree_copy.py`, never a bare `copytree`, which materialized a linked outside
  tree and failed the move on a dangling link. Relative text is never rewritten, even when it
  leaves the tree: the tree lands at the same box path, and the box view is what a link means.
* **Phase 5 / A7:** layouts are gone and the vault is never "hidden" inside the workspace, so the
  human-vault / project-vault discovery symlinks were deleted. There is nothing left to clean up.
* **B2b (Option A, Jei-ruled):** the per-box `meta["shell"]` / `meta["vault_*"]` custom-path
  OVERRIDE is dropped in `_default_state_from_meta` too, to stay CONSISTENT with the launch path
  (`resolve_project`), which derives home/vault SOLELY from the default location. Reading the stored
  override here would make stop/cleanup/move target a DIFFERENT home than the launch binds
  (JC-B2b-4).
* **Standalone registration** is `registry.standalone` (Phase 5d) — the GLOBAL registry's own
  section, NOT the per-workset `registry.yaml` `boxes:` membership that carries primary and named
  boxes. A standalone source's entry must be dropped on a move or a standalone→standalone move
  strands the old name → root mapping.

## Functions

```def owner_token(mode: BoxMode, ws_name: str | None = None) -> str```
Build a canonical owner token from a mode (+ workset name).

```def _default_rename_name(state: ProjectState, std: StandardPaths, landing_ws: Path, requested_name: str) -> str | None```
The explicit primary-box name a DEFAULT-mode edge would MINT, or `None`.

Returns the user's `requested_name` ONLY when it will actually name a NEW primary registration — a
true `--name` rename edge landing in default mode. It returns `None` for:

* an auto-derived name (no `--name`), which keeps the basename-derived auto-suffix path; and
* the primary-source SAME-PATH REUSE case where `--name` equals the current name — the box keeps
  its existing name, so `--name` is moot.

The returned name is exactly the one the cross-kind (box-vs-workset) name policy must gate; see
**F-7** and **F-3-fix2** above for the policy and for the refusal this function raises.

```def _primary_source_own_name(state: ProjectState, std: StandardPaths) -> str | None```
The name the SOURCE primary box is CURRENTLY registered under, else `None`.

Reverse-looks-up `state.workspace_path` in the PRIMARY membership. A same-name move/convert reuses
THIS entry (source's own name → its own current path), which the cross-kind/same-kind name guards
must exempt: it is not a foreign collision. See **FIX1**.

```def _ownership_to_mode(ownership: str) -> tuple[BoxMode, str | None]```
Map a TargetSpec ownership value to `(mode, workset_name | None)`.

A string that is neither `"default"` nor `"standalone"` is treated as a workset name.

```def resolve_lifecycle_target(old: str | None, std: StandardPaths, config: BootstrapConfig | None = None) -> ProjectState```
Resolve an existing project (by path or name) to a `ProjectState`.

*old* may be a path or a registered project/workset-relative name; `None` means the current working
directory. Builds on the existing detectors and resolvers, and honors `meta["workspace"]` overrides
(external-connected projects) so the descriptor reflects the **live** workspace location. Raises
`ProjectError` / `WorksetError` when no project is found.

The designation goes through `kanibako.settings.paths.resolve_designation`, the one front door the
path resolvers share, with `unknown_name_is_path=True`: an identifier that names no box still
path-ifies. **This is essential for `remap` / `convert`**, where the folder has already moved — a
primary box is found by its registered path (`_default_state_from_meta`), not by a name, and an
identifier is also a relative path. A bare WORKSET name is rejected there (lifecycle ops act on a
single project box), and a qualified `workset/project` name resolves to its workspace.

```def _default_state_from_meta(workspace: Path, std: StandardPaths) -> ProjectState | None```
Build a default-mode `ProjectState` from registered metadata.

Used by `remap` when the recorded workspace directory NO LONGER EXISTS on disk: the box is still
registered in the PRIMARY `boxes:` membership (path → name) and its metadata lives in
`boxes/<name>`. `resolve_project` requires the directory to exist, so this is the fallback. Returns
`None` when no such registration is found, so the caller can raise the normal error.

See **Sparse identity** for why the membership hit is the existence signal, and **B2b** for why
home/vault are the default location only.

⚑ It derives its settings pair from `_default_project_group(std)` — the PRIMARY workset, the same
group `resolve_project` builds — and hands both to the same `config.resolve_box_enable_vault`, so
`box.enable_vault` resolves here exactly as it does on the live path. Passing `None` as the group
made `remap` answer differently depending only on whether the workspace directory still existed:
present ⇒ the workset's `false` applied, gone ⇒ it did not and the vault was created. ⚑ Calling the
two-file READER here (which this did until 2026-08-29) was the same defect one level up: it made
`remap` disagree with `resolve_project` whenever a base- or system-tier value existed.

```def _resolve_workset_state(raw_path: Path, std: StandardPaths, config: BootstrapConfig) -> ProjectState```
Resolve a workset project (internal or external-connected) to a state.

Falls back to `box_resolve.find_connected_external_box` when the path is not inside any workset
tree. EXTERNAL is then decided by the same test the rest of the module uses: the live workspace is
not one of the workset's own (`is_in_tree_workspace`).

```def _state_from_paths(owner: str, proj: ProjectPaths, *, ws: Workset | None, is_external: bool = False) -> ProjectState```
Adapter from `ProjectPaths` to `ProjectState`.

⚑ `ProjectPaths.enable_vault` is the RESOLVED value only, so the adapter re-reads the BOX TIER
(`box_workset_settings_paths(proj)[0]`) for `box_authored_vault`.
⚑ A standalone box whose root nulls `workset.workspaces` has `project_path = None` (Q106 review):
the adapter refuses it with `refuse_null_workspaces(..., standalone=True)`, so `box move`, `remap`
and `convert` name the key instead of moving or copying a workspace that does not exist.

```def copy_into_workset(ws: Workset, proj_name: str, metadata_path: Path, shell_path: Path, source_path: Path, source_mode: BoxMode, *, copy_workspace: bool, std: StandardPaths) -> None```
Re-root a project into *ws* — the std-aware copy path for `duplicate`.

⚑ **The duplicate is ALWAYS an INTERNAL workset project.** It gets a real `workspaces/<name>`
directory, never an external symlink/redirect back to the source. Duplicate makes a *copy*, not a
*connection*; an external connection (1:1 in the per-workset registry) is what `connect` is for, and
a bare duplicate of an already-connected source is refused up front in `run_duplicate`.

*std* is threaded to `kanibako.project.workset.add_project` so its up-front guards run, and to keep
a single std-aware registration path. Because the registration target is the IN-TREE workspace dir,
`add_project` always creates a real directory and writes no external markers — which also avoids the
source-into-symlink `copytree` collision that registering the external *source* path would cause.

*copy_workspace* controls whether the source tree is copied into the new internal workspace
(`True`) or left as an empty skeleton dir — a *bare* duplicate (`False`). *metadata_path* /
*shell_path* are the SOURCE project's dirs to copy from.

⚑ **Failure-consistency:** a crash AFTER `add_project` (which registers the project in the
`meta.workset` identity and creates per-project dirs) but DURING the copies would strand a
registered-but-incomplete project. The whole copy block therefore rolls the registration and partial
dirs back on any failure, then re-raises. The rollback is `_unwind_target_member` with the leaves
that existed before `add_project`, so a `--bare` or `--force` duplicate that adopted an existing leaf
never deletes it (see **Rollbacks delete only what the op created**). `run_duplicate` refuses an
occupied `workspaces/<name>` (unless `--bare`) or `boxes/<name>` without `--force`, as its primary
path does.

```def _resolve_target_workset(name: str, std: StandardPaths) -> Workset```
Load a named workset from the registry, or raise `WorksetError`.

```def _validate(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig, *, force: bool, cwd: Path) -> dict```
Validate the requested operation up front; return resolved plan facts.

Refuses early (raising `ProjectError` / `WorksetError`) so steps 2–5 only ever run on a sound
request ⇒ zero partial state. Returns a dict carrying the resolved target mode / target workset /
destination so `execute_lifecycle` need not re-derive them.

The guards, in order, and what each protects:

* **bare-into-ws** requires a workset target (the sentinel names a path inside one).
* **no-op guard** — nothing to do when the target equals the current location, owner, AND name.
* **ws→ws with an INTERNAL workspace requires relocation** — refused with
  `STUBBORN_INPLACE_MSG`, which is a module-level constant because it is user-facing text several
  paths could reach.
* **destination not already occupied** — `records_only` exempt, see above. Beside it (same
  exemption): **a destination inside the source workspace** is refused ("Destination is inside the
  project being moved") — STEP 2 would copy into a child of the tree the retire deletes (shape X1).
  `_retire_old_workspace` also skips that shape, as a second guard.
* **membership guard** — refuse landing the project inside a workset it is not (becoming) a member
  of. ⚑ The test is `is_in_tree_workspace`, NOT `landing.resolve()` against the root alone, so a
  landing under a repointed `workset.workspaces` dir and a **symlinked** in-tree leaf are refused
  like a plain one. `relocating` is exactly `dest is not None`; the code tests `dest` directly so
  mypy narrows away the `None` for the `.resolve()`.
* **null `workset.workspaces`** (Q96) — a named target whose landing is in-tree (`BARE_INTO_WS`,
  or a landing under the target root) refuses via `refuse_null_workspaces`, before STEP 2 moves a
  tree or a ws→ws source releases; an in-place convert to standalone refuses the same way when the
  root's workset.yaml nulls the key (`_consolidate_workspace_subdir` would fill it).
* **CWD-inside-old guard** — a move is copytree+retire, NOT a rename, so a shell sitting inside the
  source would be stranded on a removed directory. Refused unless `--force`; `records_only` exempt.
* **name not taken in the target workset.**
* **cross-kind name policy** on a DEFAULT-mode `--name` rename edge — F-7, above.

`requested_name` in the returned plan is the user's EXPLICIT `--name` (empty when not given).
⚑ Unlike `new_name`, which defaults to the source name, it distinguishes "no `--name`" from
"`--name <source-name>`" — the standalone target treats only a REAL `--name` as a user assertion
(R1/R3).

```def execute_lifecycle(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig | None = None, *, force: bool = False, confirm: Callable[[], bool] | None = None) -> ProjectState```
Apply *spec* to *state* transactionally and return the new state.

See **The canonical 5-step order**.

```def _run_steps(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: BootstrapConfig, plan: dict, unwind: _Unwind) -> ProjectState```
The step body of `execute_lifecycle`.

Steps 2, 3+4, 4b and 5 in order; see the step-order section for each arm and each ordering
constraint.

```def _retire_old_workspace(old: Path, landed: Path, recorded: Path) -> None```
Delete the relocated-from workspace *old*, whose copy landed at *landed* — an `on_success` action
only; see **STEP 5**.

It skips an absent *old*, and an *old* that is or holds *landed* or *recorded* — the workspace the
box now records. The *recorded* guard covers a same-workset, same-name move that lands back on its
own leaf, and a `workspaces` dir repointed inside the moving box's own tree. A symlink is unlinked, never followed, and `Note: left <target>; it is yours` names what
stays. A directory goes through a plain `shutil.rmtree` — it is user content, not a box tree, so
`remove_box_tree`'s escalation does not apply. A failure prints `Note: could not remove the old
workspace <old>: <err>` and stops: the op already succeeded, so the exit code stays 0, and falling
back to another deleter would widen what a relocation may delete. It prints its own Notes because
`_Unwind.finish` swallows exceptions.

```def _apply_ownership_and_markers(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, target_mode: BoxMode, target_ws: Workset | None, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None, requested_name: str = "", force: bool = False) -> ProjectState```
Re-root metadata/shell/vault into the target owner + rewrite markers.

Handles EVERY transition the same way: copy the source metadata into the target owner's metadata
root, write the destination `box.yaml`, update registry/names, remove the old owner's
metadata. Returns the resulting `ProjectState`. Dispatch is purely on `target_mode`. (See the
drift note under **STEPS 3+4** for what that `box.yaml` write actually contains.)

```def _unwind_box_tree(path: Path) -> None```
Best-effort box-tree removal shaped for `_Unwind.push`.

`remove_box_tree` returns a bool; `_Unwind` wants `Callable[[], None]`. A NAMED wrapper rather than
an inline lambda that discards the result, because "swallow this value" is exactly the kind of thing
worth saying out loud.

```def _copy_metadata(src_metadata: Path, src_shell: Path, dst_metadata: Path, *, shell_into_metadata: bool, home_leaf: str = "home", unwind: _Unwind) -> Path```
Copy metadata (minus lock+home) and shell into *dst_metadata*.

Returns the destination shell path (`dst_metadata / home_leaf`) and pushes a removal of
*dst_metadata* onto *unwind*. See **The canon-skeleton hazard** for why that removal must escalate
and why the copied shell is re-materialized.

```def _deliver_carried_box_settings(state: ProjectState, dst_box_tier: Path) -> None```
Write the source box's carried box-scope settings to *dst_box_tier* (M-8).

A convert/move makes a NEW box that INHERITS the source's box settings. The source's box tier and
the destination's are BOTH resolved through the single derivation, so the settings land where the
destination will actually read them. It is the BOX TIER ONLY: a `box.*` key at the source's WORKSET
tier is that workset's downward default, not the box's, and is left where it was authored
(`kanibako.settings.config.carried_box_settings`).

A no-op when the source carries nothing, so a box with no settings file still produces no
destination file — sparse, matching `create`.

⚑ `group=None` is deliberate: only the STANDALONE arm consults it, and standalone's workset tier is
derived from the ROOT, not from a `ProjectGroup` (which `ProjectState` does not carry). For
primary/named the workset tier is therefore `None` — no legacy underlay, so those modes stay
byte-identical to pre-P2.

```def _remove_old_metadata(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, preserve_name: str | None = None, preserve_root: Path | None = None) -> None```
Remove the source project's metadata/shell (+ PRIMARY vault).

⚑ **TWO reuse signals, because each mode's IDENTITY on disk is a different thing:**
`preserve_name` for a primary source (whose metadata dir is named after the box) and
`preserve_root` for a standalone one (whose metadata IS a root, and whose NAME is exactly what an
in-place rename changes). A destination that reused the source must not then have the source torn
out from under it.

🛑 The three arms differ in what they are allowed to touch, and the difference is the whole point:

* **Standalone source** — removes the in-tree kanibako artifacts (the `box_data/` marker dir, the
  root `workset.yaml`, and the vault) and **NOT the project root itself**. For a standalone the root
  IS `metadata_path`: deleting it would wipe the user's whole project directory AND the
  already-converted destination. The success tail `rmdir`s the root last (`old_root`), so it goes
  only when nothing is left in it; it first deletes a started box's stale `CREDS_WATCHER_LOCK_FILE`.
  ⚑ `preserve_root` naming THIS root (an in-place rename) drops the OLD name's `registry.standalone`
  entry and returns: `box_data/`, the root meta and the vault are the box just re-established, and
  the only stale thing is the name.
  ⚑⚑ The vault comes from `project.workset.standalone_vault_teardown(root)`, **called before
  anything at all is dropped — the registry entry included**. Two reasons and both bite: the root
  `workset.yaml` this arm unlinks is the only carrier of a `workset.vault_*` repoint, so a later
  read answers the composed default; and an UNRESOLVABLE repoint raises there, which must happen
  while the source is still whole and the unwind still has something to restore. An arm the user
  pointed OUTSIDE the root is reported and left — see that function for why.
* **Primary source** — unregisters the name, removes the `boxes/` metadata dir, and removes the
  per-box leaf under the primary workset's resolved vault arms (Phase 5 moved it out of the
  workspace), through `remove_path` like every other mode. A leaf linking outside its arm was not
  carried, so `_unreceived_vault_leaves` (given the arms and the box name) retains it with a Note.
  A carried link loses only the link, and the tail's Note names the target it left. `preserve_name` (L2) suppresses both when the
  converted box reuses its own name in place.
* **Workset source** — `release_project` (the registration, and an external member's
  discoverability link) in-op; the store (box tree, vault leaves) goes on success through
  `_retire_old_store`. **The workspace leaf is NEVER deleted here** — in-tree or external; a
  relocation retires an in-tree leaf in STEP 5. The primary and standalone arms do not use
  *unwind*.

```def _to_default(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str = "", force: bool = False) -> ProjectState```
Convert/relocate the project so its owner becomes the default workset.

See **Naming — the PRIMARY membership register** for the mint/unregister/register ordering, and
**M-8** for why the copy source is `box_metadata_dir` rather than `metadata_path`.

⚑ When the name is reused in place, the metadata ALREADY lives at the destination — copying would be
a (failing) copy-onto-self, so the existing tree is reused instead.

### ⚑⚑ What stays at the standalone root is RESOLVED, never a leaf name

The sweep decides, for each child of the root, "is this kanibako's or the user's". It answers in two
parts, and the split is the whole design:

```_STANDALONE_FIXED_ARTIFACTS```
The four names NO key can repoint: `box_data/` (spec §2c fixes the standalone box dir), the root
`workset.yaml` and `box.yaml` (drift I), and `.kanibako.lock`. A name test is correct for these
because there is nothing for a name to be wrong about.

```_STANDALONE_ROOT_DIR_KEYS``` / ```def _standalone_root_artifacts(root: Path) -> list[tuple[str, Path, bool]]```
Everything else the root owns is a declared, repointable `workset.*` DIRECTORY key —
`workspaces`, `vault_ro`, `vault_rw`, `canon` — and each is RESOLVED off one `workset.yaml` read,
returned as `(key, path, repointed)`. `boxes` is absent because §2c fixes the box dir; `logs`,
`template` and `channelroot` are absent because standalone does not materialize them (each
resolver's own docstring is the source). The literal `vault/` skeleton parent is appended when it is
on disk — exactly the tail `standalone_vault_teardown` appends, and for its reason: no key names it.

⚑ This replaced a frozenset of LEAF NAMES, which could not express a repoint. A root carrying
`workset.vault_ro: store/ro` had its `store/` swept into the workspace dir, because the root child
is `store` — a name no list held — and the box then resolved its vault to an empty directory.
`workset.canon` was never listed at all, so a round trip through `convert --default` and back swept
kanibako's own canon tree with NO repoint involved. And an ABSOLUTE repoint is not representable in
a name set at all: with `workset.workspaces` pointed outside the root, the literal
`root / "workspace"` destination filled a directory the box never opens.

⚑ `_artifact_claiming` uses an ANCESTOR test, not equality — `store/ro` is not itself a child of the
root; `store` is, and it HOLDS the arm.

⚑ [R144]: a keep whose resolved path DIFFERS from that key's default is one the USER authored, and
it is REPORTED by key on stderr (`Note: left <path> at the standalone root — workset.vault_ro
resolves inside it.`). Default-layout keeps stay silent, as they always have.

⚑ An unresolvable repoint RAISES (`SettingsError`, naming the key and the token) instead of
guessing. A root whose layout keys do not answer is a root whose children cannot be told apart, and
the sweep MOVES USER DATA — so the refusal lands before `_to_standalone` has copied or written
anything, the same call order `standalone_vault_teardown` demands for the same reason.

```def _consolidate_workspace_subdir(root: Path, workspace_subdir: Path, unwind: _Unwind) -> None```
Move the project's top-level files into the standalone workspace dir.

Drift H: a standalone box's live workspace is a SUBDIR of the root, not the root itself. On an
in-place convert the user's project files currently sit AT the root; everything that is not a
kanibako artifact (above) is relocated into that subdir so the BIND SOURCE matches the resolved
layout. A no-op when there is nothing to move — an empty root, files already consolidated, or a
`workset.workspaces` that resolves AT the root (the workspace already IS the root, and moving each
child onto itself would raise). Each move is pushed onto *unwind* so a later failure restores the
original placement.

⚑ The destination is `_resolve_standalone_workspaces`, computed in `_to_standalone` — the SAME key
`resolve_standalone_project` reads to answer `project_path`. Two answers for one box is the defect
class this file has already paid for at the vault arm and the channel partition. A null there
refuses (`refuse_null_workspaces`); `_validate` refused such a root first, so this only keeps a
null from ever reading as the default.

⚑ *root* ALWAYS holds the project's current files at this point in the convert — in-place: the
source dir; relocating: the copy STEP 2 made at *dest*; external-in-place: the external dir that is
BECOMING the standalone root. That uniformity is what lets one call serve every transition.

```def _undo_consolidate(src_dir: Path, dest_dir: Path, moved: list[Path]) -> None```
Best-effort reversal of EITHER sweep — *moved*'s leaves go back from *src_dir* to *dest_dir*.
⚑ *dest_dir* is RE-CREATED first: the unconsolidate direction removes it (with any repoint parents)
once emptied, so a restore would otherwise land nowhere and every move would fail silently.
Every entry is tried, even past a `KeyboardInterrupt` (re-raised at the end, as `_Unwind.run`
does); an entry left behind gets `Note: <name> did not go back to <dest_dir>; it is at <src>`.
`_restore_primary_rows` and `_restore_standalone_rows` hold an interrupt the same way, with a `Note`
per registry row it left unrestored. `_restore_standalone_rows` also Notes a row an ordinary error
left unrestored; in `_restore_primary_rows` the `_safe_*` helpers swallow such an error silently.

```def _unconsolidate_workspace_subdir(workspace_subdir: Path, root: Path, unwind: _Unwind) -> None```
Lift the standalone workspace dir's contents back up to *root*.

The inverse of `_consolidate_workspace_subdir`, used when converting OUT of standalone in place: the
workspace files return to the project root (where a non-standalone box roots them) and the
now-empty subdir is removed so the converted project keeps no stray one. A no-op when the subdir is
absent or empty; each move is pushed onto *unwind*. The caller first runs `_hold_root_gitignore`:
the user's `.gitignore` wins the root, lines the root file held beyond kanibako's are appended to it,
and a rollback restores both files.

⚑ *root* is the caller's READ root, never a parent counted off *workspace_subdir* — see the
standalone arm of **STEP 2**. The removal walks UP from the workspace dir, so the directories a
repoint interposed (`workspaces: @meta.workset.path/nested/deep` leaves `nested/`) go too: they
existed only to hold the workspace, and the root `workset.yaml` that named them is about to be
unlinked. `rmdir` IS the emptiness test, so a parent the user keeps their own files in stops the
walk.

```def _to_standalone(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, new_name: str, root: Path, requested_name: str = "") -> ProjectState```
Convert/relocate the project so it becomes standalone (in-tree metadata).

⚑⚑ The parameter is `root`, and it is the caller's `new_workspace`: standalone is the ONE target
mode where the live workspace is not the project dir, so what arrives is the ROOT. It was named
`new_workspace` and read as the root, and an in-place standalone RENAME then handed it the box's
own workspace — laying a second complete standalone tree inside the first, after which
`_remove_old_metadata` tore out the original's `box_data/` (home included) and vault, because from
the destination's position they belonged to some other box.

⚑ **`reused_in_place`** — `dst_metadata` and the source's `box_metadata_dir` are the SAME directory
— governs three things, and each is a different failure without it: the metadata copy is SKIPPED (a
`copytree` onto itself raises), the consolidate sweep is SKIPPED (the sweep exists because an
in-place convert's project files sit AT the root; on a root that is ALREADY this box's, they sit in
the workspace dir and everything beside them is kanibako's — starting with the root `.gitignore`
this function writes, which the sweep would relocate into the user's workspace), and
`_remove_old_metadata` is passed `preserve_root` so the teardown does not delete the box that was
just re-established.

A standalone box's identity is the canonical opaque `<kuid>_<leaf>`, matching `create --standalone`
/ `duplicate --standalone`. Standalone boxes are NOT members of any workset's `boxes:` table; they
are registered in `registry.standalone`.

Convert ESTABLISHES the box uniformly via `establish_standalone`, which HONORS an explicit `--name`
through `box_identity.resolve_standalone_name`:

* a verbatim canonical id is used if free, and REFUSED if taken;
* a non-canonical `--name` becomes a fresh `<kuid>_<sanitized name>`;
* NO `--name` generates a fresh canonical id from the root basename.

It writes `workset.kuid` into `<root>/workset.yaml` — that write MATERIALIZES the detection marker,
and it is the ONLY key laid there; NO `mode` is persisted anywhere (see the drift note under
**STEPS 3+4**) — then registers the box, with an unwind to drop the registration on failure.
⚑ It is handed `state.box_authored_vault`: it writes straight into the box tier
`_deliver_carried_box_settings` just laid down, so the resolved value would undo that carry and pin
the source workset's default on a box that has LEFT it. The new root `workset.yaml` carries only
`workset.kuid`, so the standalone box's resolved value IS what it authored. `_remove_old_metadata`
purges the source's prior registry entry so it does not dangle — and for BOTH non-standalone modes
that entry is the same thing: the source workset's own `boxes:` row.

⚑ **The two arms reach it by different doors and land on ONE carrier.** A `primary` source goes
`unregister_primary_box_name` → `settings/paths.py::_unregister_workset_box_membership`; a workset
member goes `release_project`, which unlinks an external member's discoverability link and then calls that SAME
helper as its last durable step. The helper resolves the per-workset registry FILE
(`resolve_workset_registry_path` — a `workset.registry` repoint wins, else
`<workset_root>/registry.yaml`) and calls `workset_registry.unregister_workset_box`. So the thing
dropped is a `boxes:` row in a `registry.yaml`, never a free-standing per-mode index.

⚑ `new_name` is only a *requested* name: the source's name is passed as the default when the caller
gave no `--name` (i.e. `new_name == state.name`), in which case it is NOT treated as a user
assertion and a fresh canonical id is generated instead. This is exactly why the plan carries
`requested_name` separately (R1/R3).

⚑ **ORDER:** consolidate the source's top-level files into the resolved workspace dir FIRST, THEN
lay down the kanibako artifacts — otherwise the artifacts get swept into the subdir with everything
else. ⚑ The destination comes from `_resolve_standalone_workspaces(root, …)`, so it is the same
directory the box will later resolve; see the sweep's own section above.

⚑⚑ **The `box.yaml` landing in `box_data/` must NOT be deleted as an "orphan".** It IS the
destination's BOX TIER now (spec §2c ALL PROJECTS: `meta.box.settings = @meta.box.path/box.yaml`,
and `@meta.box.path` for a standalone IS `box_data/`). It used to be deleted here on the theory that
the box meta lived only at the ROOT — that theory is the RETIRED model, and deleting the file now
discards the box's settings. Detection is unaffected either way: it reads the ROOT file
(`system-design` § "Detection & import"), which
`establish_standalone` writes.

⚑ The vault `.gitignore` goes through `settings/paths.py::write_vault_gitignore`, handed the arm
`establish_standalone` just RESOLVED against this root's `workset.yaml`. This site used to write it
on `(<root>/vault).is_dir()` alone — a position answering a key, and the fourth sighting of the class
`722c3d59` cured in `_init_common`: the skeleton is the `ro` arm's DEFAULT parent, so it can sit on
disk while `workset.vault_rw` points elsewhere and the file's `rw/` names nothing.

```def _to_workset(state: ProjectState, std: StandardPaths, config: BootstrapConfig, unwind: _Unwind, *, target_ws: Workset, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None) -> ProjectState```
Convert/relocate the project into *target_ws* (std-aware external wiring).

`source_for_add` is the path `add_project` records and decides external wiring from: the in-tree
workspace dir for an internal landing, the live external workspace path for an external one.

**Whether to copy the workspace tree:**

* internal landing where the workspace is NOT already the in-tree dir ⇒ copy. When relocating into
  the ws via STEP 2 the tree was already moved to `dest == workspaces/<name>`, so it must not be
  copied again; nor when the SOURCE leaf is that dir (same workset, same name) — a copy onto itself;
* external ⇒ never copy.

⚑ After the copy, an in-tree WORKSET source registers `_retire_old_workspace(source leaf, landing, landing)`
on success — the `convert --name` in-place rename (shape S17), which STEP 2 never sees. Other sources
keep their tree: an in-place convert out of primary or standalone deleted nothing before, and still
does not.

⚑⚑ **ws→ws re-root: the SOURCE workset must RELEASE the project BEFORE the target registers it.**
The connection record is 1:1, so an external source still mapped to the OLD workset would collide
with `add_project`'s "already connected" guard. The source registration is therefore dropped first —
which clears the per-workset connection record and the discoverability symlink, and NEVER touches
the user's external dir.

⚑ That release is `release_project` + `remove_member_store`: it DELETES the source's box tree and
vault leaves — never its workspace leaf — so the forward copy would have nothing to read from. A
`tempfile` STASH of the source metadata (including the shell) and vault is taken first, and both the
forward copy and the unwind restore read the stash rather than the live paths. `_restore_source` is
pushed before the release starts, and the stash is discarded only on success or after a clean
restore (see **Rollbacks delete only what the op created**). The source workspace stays in place
until STEP 5's retire; `_restore_source`'s `add_project` re-adopts it.

⚑ The target unwind (`_unwind_target_member`) deletes a target leaf only when it did not exist
before `add_project`: on a same-workset, same-name move `workspaces/<new_name>` is the source's own
workspace.

`add_project` (std-aware) registers the project, creates the skeleton dirs, and — for an external
landing — writes the markers.

⚑ `add_project(..., force=True)`: a convert/move/duplicate INTO a workset is a DELIBERATE absorb, so
it must override the standalone-marker connect guard (B2a) — the source of a standalone→workset
convert still carries its in-place `box_data/` marker at this point, because the marker is removed
LATER in the convert. `force` affects only a standalone-marked source, so it is a no-op for the
non-standalone move/duplicate cases.

⚑ `_deliver_carried_box_settings` is called even though the metadata was just copied: the copy is
mode-shaped, and the delivery is what guarantees the source's box tier lands at the file the
DESTINATION reads as its box tier (M-8) — with the source's `workset:` identity stripped.

⚑ `_remove_old_metadata` is skipped when the source was a workset — the registration was ALREADY
released above, and cleaning again would double-remove.

⚑⚑ **The returned state's vault comes from `resolve_workset_vault_pair(target_ws.root)`, never from
`target_ws.vault_dir / "ro"`.** `add_project` above creates the per-box leaves under the target
workset's RESOLVED arms; composing off `vault_dir` handed back a state naming a directory the box
does not use — two answers for one box. Nothing reads those fields into a deletion TODAY (the state
flows to `_relocate_channel_partition` and the summary print), which is why this was latent rather
than destructive; it is still the wrong path to hand a caller.

```def _state_ws_token(state: ProjectState) -> str```
Return the channel-partition workset-name token for *state*.

`__PRIMARY__` for primary mode, the named workset's name for named mode, `__STANDALONE__` for
standalone. Mirrors `kanibako.channels.channels.workset_name_token` but reads off the lifecycle
`ProjectState` (mode + loaded `ws`) rather than a `ProjectPaths`. Raises `ValueError` when a named
box is missing its workset — the caller treats that as "nothing to relocate" and warns.

```def _relocate_channel_partition(old: ProjectState, new: ProjectState, std: StandardPaths) -> None```
Best-effort relocate THIS box's OWN channel partition (D-M10, §6).

*old* is the pre-convert identity, *new* the FINAL post-convert identity. See **Channel partition
relocation** for the address model, the A9 ordering requirement, and the swallow-and-warn policy. An
unchanged address is an idempotent no-op, and an existing destination is never clobbered.

```def _safe_unregister(std: StandardPaths, name: str) -> None```
Swallowing wrapper around `unregister_primary_box_name`.

```def _safe_register_membership(std: StandardPaths, name: str, workspace: Path) -> None```
Best-effort re-register *name* → *workspace* in the PRIMARY membership.

The failure-window restore for **FIX1**: it writes the RAW membership entry directly, with no
cross-kind/same-kind guard, so restoring the source box's OWN prior registration is unconditional.
Errors are swallowed — the unwind stack is best-effort restore.

```def _member_leaves(ws: Workset, name: str) -> tuple[Path | None, Path, Path, Path]```
Member *name*'s four leaves: `workspaces/<name>`, `boxes/<name>`, and the per-box leaves under the
RESOLVED vault arms, as `add_project` creates them. The workspace leaf is `None` under a null
`workset.workspaces` (no dir to hold it); `_existing_member_leaves` and `_unwind_target_member` skip it.

```def _existing_member_leaves(ws: Workset, name: str) -> frozenset[Path]```
Those of `_member_leaves` already on disk; a dangling link counts.

```def _unwind_target_member(ws: Workset, name: str, existed: frozenset[Path]) -> None```
Undo a target registration (`_to_workset`, `copy_into_workset`): `release_project` with
`keep_link=True`, then each leaf NOT in *existed* — so a pre-existing `workspaces/<name>` symlink
survives a same-workset rollback. See **Rollbacks delete only what the op created**. A failed release or a leaf it
cannot remove is reported in a `Note`; the other leaves still go, even past a `KeyboardInterrupt`,
which is re-raised at the end.

```def _dispose_stash(stash: Path) -> None```
Delete a ws→ws stash through `remove_box_tree`; a `False` prints a `Note` naming it, because the
stash holds a copy of the box home and may hold credentials.

```def _retire_old_store(ws: Workset, name: str) -> None```
`remove_member_store` for a workset source that was converted out — an `on_success` action only.
A raise does not propagate; `_report_store_leftovers` names what is left.

```def _report_store_leftovers(ws: Workset, name: str, err: OSError | None = None) -> None```
Print `Note: could not remove the old store of '<name>'[: <err>]; left <paths>` for each of the
member's store leaves still on disk. `remove_box_tree` reports a failure by returning `False`, which
`remove_member_store` does not pass on, so the disk is the only witness.

```def _ownership_from_args(args) -> str | _Sentinel```
Map the uniform target flags (`--default` / `--standalone` / `--workset`) to an ownership value, or
`UNCHANGED` when none is given.

The three flags are mutually exclusive, enforced by an argparse mutually-exclusive group;
`--workset` carries the workset name.

```def _lower_name(args) -> str | None```
Return the user's `--name` folded to lowercase (R2), or `None`.

Every box name is lowercase, so a user-supplied `--name` is silently lowercased on acceptance — no
rejection of mixed-case input. After folding, the name is validated against the §Design 8 blocklist
(NEW and renamed boxes are held to the constraint); an invalid name raises `ProjectError`.

```def _make_confirm(force: bool, summary: str)```
Return a `Callable[[], bool]` for `execute_lifecycle`'s *confirm*.

With *force* the op proceeds without prompting (returns `None`). Otherwise it prints *summary* and
prompts; a non-`yes` answer returns False and the engine aborts.

```def _load_env()```
Load the config + `StandardPaths` pair the three CLI entry points all need.

```def _abort_if_locked(state: ProjectState, force: bool) -> bool```
Refuse a destructive relocation while a box may be running.

`move` / `convert` copy then `rmtree` the source workspace, which for a RUNNING box would delete the
live bind-mounted directory out from under it. Mirrors `box duplicate`'s lock pre-flight
(`_duplicate.py`): if a session HOLDS the project's `.kanibako.lock` (a non-blocking `flock`
probe), warn and abort unless *force*. The file outlives its session, so an unheld one passes.
Returns True when the caller should abort (and has been warned).

```def run_remap(args) -> int```
`box remap <old> [<new>]` — records-only relocation.

The folder has ALREADY moved on disk; this updates kanibako's recorded path and markers to reflect
the new location. It does NOT move files and never changes ownership. *new* defaults to `./`.

```def run_move(args) -> int```
`box move <old> <new>` (alias `mv`) — physically relocate files.

BOTH paths are required. An optional target flag (`--default` / `--standalone` / `--workset`) also
changes ownership. ⚑ REFUSES an external-connected project outright: its workspace is the user's own
directory, so the message redirects to `box remap` (records) or `box convert` (ownership).
A retry of a move that already landed — *old* is no box, and *new* is a box registered under its
name that matches the flags — says `Nothing to do` (`_completed_move`) instead of the resolve error.
Nothing records where a box came from, so a mistyped *old* beside an existing box gets the same
answer; the message says only what is on disk.

```def run_convert(args) -> int```
`box convert [<old>] (--default|--standalone|--workset <ws>) [--move [path]]`.

Change a project's ownership/mode. In-place by default for all modes; `--move <path>` relocates,
bare `--move` moves into the target workset (valid ONLY with `--workset`). `--name` renames in the
target.

argparse stores the `_BARE_MOVE` sentinel for a bare `--move`, a path string for `--move <path>`,
and `None` when absent — which is why the sentinel is a module-level `const` object rather than a
magic string.

```_BARE_MOVE```
argparse `const` sentinel for a bare `--move` (no path argument). Defined at the END of the module,
after the entry points that compare against it.
