# Box Lifecycle — the transactional `remap` / `move` / `convert` engine

⚠️ **This mirror is COMPLETE for displaced prose** (relocation pass, 2026-08-11). A symbol absent
below carried nothing worth displacing — never "does not exist". The source keeps one-line
descriptors and `⚑` markers; the reasons live here.

🛑 **This module is DESTRUCTIVE.** It `copytree`s and then `rmtree`s a user's project directory, and
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
* **STEP 5** — there is no old workspace to clean up.

## `ProjectState` — the uniform descriptor

* `owner` is the canonical ownership token: `"primary"`, `"standalone"`, or `"workset:<name>"`.
* `ws` is the loaded `Workset` when the owner is a workset, else `None`.
* `is_external` is True when the live workspace lives OUTSIDE the owning workset's root — a
  connected-external project, i.e. the user's own directory. **Every destructive branch keys on
  this flag.**

## The canonical 5-step order

From the redesign DESIGN. `execute_lifecycle` runs it; `_run_steps` is the body.

1. **Validate everything up front** (`_validate`) — refuse early ⇒ zero partial state.
2. **Move files**, if relocating.
3. **Update location records / markers.**
4. **Apply the ownership / mode change** — re-root metadata/shell/vault, registry, names, rewrite
   `settings.yaml` mode + paths.
5. **Clean up the old** side — never the user's external source dir.

Steps 2–5 push compensating actions onto an unwind stack; on ANY exception the stack runs in
reverse to restore a consistent state, then re-raises. `confirm`, if given, is called AFTER
validation; returning False aborts cleanly (no changes) by raising `ProjectError`.

### ⚑ Why step 1 holds everything

Refusing early is what makes "zero partial state" a property of the ENGINE rather than of each
step's individual care. A refusal added inside `_run_steps` instead of `_validate` would fire after
files had already moved — which is precisely the failure the unwind stack exists to make survivable
and the ordering exists to make impossible. **New refusals belong in `_validate`.**

### STEP 2, arm by arm

The four arms are mutually exclusive and each answers a different question about what "relocating"
means for that source. ⚑ For a workset→workset re-root the *workspace* is not what relocates here
when the source is EXTERNAL; for an INTERNAL ws→ws the move IS required, and `_validate` has
already enforced that (`STUBBORN_INPLACE_MSG`). A standard move `copytree`s the workspace to `dest`.

* **`records_only`** — files presumed already at `dest`; copy and remove nothing.
* **internal relocate** — `copytree` the workspace to `dest`, push an `rmtree(dest)` unwind.
* **external relocate** — the "workspace" is the USER'S OWN directory. It is NEVER moved, only
  re-recorded; `dest` becomes the new recorded location when it is the destination of an
  internalizing move. Re-pointing an external project to some other external location is out of
  Phase-1 scope beyond the ws→ws repoint, which ownership handles.
* **in-place convert OUT of standalone** — the reverse of drift H (below): lift the
  `<root>/workspace/` files back up to the root and root the converted box there, so the project
  stays at the directory the user is standing in rather than a subdir.

### STEPS 3+4 are interleaved, deliberately

The destination metadata roots DEPEND on the target owner, so markers cannot be written before
ownership is decided. `_apply_ownership_and_markers` therefore does both at once: compute the new
metadata/shell/vault dirs for the target owner, copy them, write the destination `settings.yaml`,
update registry/names, then clean up the old side. It dispatches to exactly one of `_to_workset` /
`_to_standalone` / `_to_default`.

⚑ **A drifted claim, corrected rather than relocated (2026-08-11):** the pre-relocation prose said
this path writes a "rewritten `settings.yaml` (mode + workspace override + hash + markers)" — in
five places, including the module docstring and `run_remap`. **No hash is computed or written
anywhere in this module**, and under sparse identity (P8b) the only thing written to the
destination `settings.yaml` is the non-default `box.enable_vault`; mode and workspace override are
not persisted either. The word `hash` survived only in comments, never in code. Those claims were
DROPPED, not carried here.

### ⚑ STEP 4b runs AFTER identity is final (A9 / IN-8)

Channel relocation reads the box's address off `new_state`, not `state`, because a **standalone
convert REGENERATES the box name**. Run before identity is finalized, it would move the partition to
an address that is about to change. This is the one ordering constraint in the file that is not
about file safety.

### ⚑ STEP 5 is irreversible and NOT compensated

The final `rmtree(old_ws)` has no unwind push, and that is why it is last: everything has been
copied and recorded by the time it runs, so there is nothing left that a rollback would need it to
undo. It fires only for a real, INTERNAL move (`not records_only and relocating and dest and not
state.is_external`) and only when `old_ws` resolves differently from `dest`.

*(A pre-relocation comment here claimed the code "keep[s] a backup move for unwind safety". It does
not — there is no backup and no unwind push. The claim was DROPPED rather than relocated.)*

## The unwind stack (`_Unwind`)

A LIFO stack of compensating actions for failure-consistency. Each pushed action is a zero-arg
callable that reverses a forward step. On `run()` actions execute in REVERSE order, and individual
failures are swallowed — best-effort restore, so one bad unwind does not mask the rest.

`on_success` is the second list: actions that run only when the WHOLE operation succeeds. It exists
for scratch that must survive until completion but be discarded on success — today, the ws→ws stash.

⚑ **Reverse order is load-bearing, not incidental.** `_to_default`'s FIX1 restore relies on it: that
unwind runs BEFORE any later one, so a failed re-register leaves the source's `name -> old path`
mapping intact rather than orphaned.

## Mode shapes the steps must respect

### Drift H + drift I — the standalone layout

⚑ In `_to_standalone`, `new_workspace` IS the standalone ROOT — the project dir, not the live
workspace. (The returned `ProjectState.workspace_path` is the `workspace/` subdir; `metadata_path`
is the root.) A standalone box's tree roots at `<root>` with:

* `settings.yaml` **AT THE ROOT** (drift I — and `ProjectState.metadata_path` for a standalone IS
  that root),
* a `box_data/` marker dir (home + helper log) — the real box metadata dir,
* `vault/{ro,rw}/`,
* and the **live workspace as a `workspace/` SUBDIR** (drift H).

Every other mode roots its live workspace at the project dir itself. That single difference is the
source of the consolidate/unconsolidate pair, of `_STANDALONE_ROOT_ARTIFACTS`, and of the
`box_metadata_dir` rule below.

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
disabled across the move.

The same ruling is what makes `_default_state_from_meta` work: the PRIMARY-membership reverse-lookup
hit IS the existence signal, because identity no longer self-describes on disk — so there is no
`project.mode` presence gate to consult, and `enable_vault` is a plain box-scope `box.enable_vault`
read, decoupled from identity.

And it is why `_to_workset`'s EXTERNAL arm reads the recorded workspace back out of the per-workset
`boxes:` registry that `add_project` just wrote: under sparse create the box's own `settings.yaml`
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

* **PRIMARY vault** lives at `@config.primary_workset/vault/{ro,rw}/<name>` (Phase 5), so it is NOT
  under `metadata_path`. `_remove_old_metadata` removes the per-box `ro`/`rw` dirs explicitly and
  never their shared parent, which holds EVERY box's vault; the `relative_to(std.primary_workset)`
  check is what keeps the removal inside the primary tree.
* **Phase 5 / A7:** layouts are gone and the vault is never "hidden" inside the workspace, so the
  human-vault / project-vault discovery symlinks were deleted. There is nothing left to clean up.
* **B2b (Option A, Jei-ruled):** the per-box `meta["shell"]` / `meta["vault_*"]` custom-path
  OVERRIDE is dropped in `_default_state_from_meta` too, to stay CONSISTENT with the launch path
  (`resolve_project`), which derives home/vault SOLELY from the default location. Reading the stored
  override here would make stop/cleanup/move target a DIFFERENT home than the launch binds
  (JC-B2b-4).
* **Standalone registration** is `registry.standalone` (Phase 5d), NOT `names.yaml`. A standalone
  source's entry must be dropped on a move or a standalone→standalone move strands the old
  name → root mapping.

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

```def resolve_lifecycle_target(old: str | None, std: StandardPaths, config: KanibakoConfig | None = None) -> ProjectState```
Resolve an existing project (by path or name) to a `ProjectState`.

*old* may be a path or a registered project/workset-relative name; `None` means the current working
directory. Builds on the existing detectors and resolvers, and honors `meta["workspace"]` overrides
(external-connected projects) so the descriptor reflects the **live** workspace location. Raises
`ProjectError` / `WorksetError` when no project is found.

Three resolution front doors run before the path-ify, each mirroring `resolve_any_project`:

* ⚑ **Bare token** (no path separator) that does not exist in cwd may be a registered
  project/workset name. **This is essential for `remap` / `convert`**, where the folder has already
  moved: the on-disk path is stale but the NAME still resolves.
* ⚑ `raw` is updated for **BOTH** kinds. A bare workset name resolves to the workset ROOT, which
  `detect_project_mode` must see — without this the name path-ifies to `cwd/<name>` and resolution
  fails misleadingly.
* **Qualified `workset/project`** addressing — a token WITH a separator that is not an existing
  path. This is the form the bare-workset rejection suggests, so it must resolve. A real relative
  path that merely happens not to exist is left untouched and falls through to the path-ify,
  failing exactly as before.

A resolved bare WORKSET is then rejected outright: lifecycle ops act on a single project box, and a
workset is not one. The message is actionable (name a project inside it, or run from a project
workspace under it).

```def _default_state_from_meta(workspace: Path, std: StandardPaths) -> ProjectState | None```
Build a default-mode `ProjectState` from registered metadata.

Used by `remap` when the recorded workspace directory NO LONGER EXISTS on disk: the box is still
registered in the PRIMARY `boxes:` membership (path → name) and its metadata lives in
`boxes/<name>`. `resolve_project` requires the directory to exist, so this is the fallback. Returns
`None` when no such registration is found, so the caller can raise the normal error.

See **Sparse identity** for why the membership hit is the existence signal, and **B2b** for why
home/vault are the default location only.

```def _resolve_workset_state(raw_path: Path, std: StandardPaths, config: KanibakoConfig) -> ProjectState```
Resolve a workset project (internal or external-connected) to a state.

Falls back to `box_resolve.find_connected_external_box` when the path is not inside any workset
tree. EXTERNAL is then decided by the same test the rest of the module uses: the live workspace
lies outside the workset root.

```def _state_from_paths(owner: str, proj: ProjectPaths, *, ws: Workset | None, is_external: bool = False) -> ProjectState```
Adapter from `ProjectPaths` to `ProjectState`.

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
dirs back on any failure, then re-raises. `remove_project(remove_files=True, std=...)` is idempotent
and removes only workset-side dirs — never the user's external source.

```class _Unwind```
See **The unwind stack**, above.

```def push(self, action: Callable[[], None]) -> None```
```def on_success(self, action: Callable[[], None]) -> None```
```def run(self) -> None```
```def finish(self) -> None```

```def _resolve_target_workset(name: str, std: StandardPaths) -> Workset```
Load a named workset from the registry, or raise `WorksetError`.

```def _validate(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig, *, force: bool, cwd: Path) -> dict```
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
* **destination not already occupied** — `records_only` exempt, see above.
* **membership guard** — refuse landing the project inside a workset it is not (becoming) a member
  of. `relocating` is exactly `dest is not None`; the code tests `dest` directly so mypy narrows
  away the `None` for the `.resolve()`.
* **CWD-inside-old guard** — a move is copytree+rmtree, NOT a rename, so a shell sitting inside the
  source would be stranded on a removed directory. Refused unless `--force`; `records_only` exempt.
* **name not taken in the target workset.**
* **cross-kind name policy** on a DEFAULT-mode `--name` rename edge — F-7, above.

`requested_name` in the returned plan is the user's EXPLICIT `--name` (empty when not given).
⚑ Unlike `new_name`, which defaults to the source name, it distinguishes "no `--name`" from
"`--name <source-name>`" — the standalone target treats only a REAL `--name` as a user assertion
(R1/R3).

```def execute_lifecycle(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig | None = None, *, force: bool = False, confirm: Callable[[], bool] | None = None) -> ProjectState```
Apply *spec* to *state* transactionally and return the new state.

See **The canonical 5-step order**.

```def _run_steps(state: ProjectState, spec: TargetSpec, std: StandardPaths, config: KanibakoConfig, plan: dict, unwind: _Unwind) -> ProjectState```
The step body of `execute_lifecycle`.

Steps 2, 3+4, 4b and 5 in order; see the step-order section for each arm and each ordering
constraint.

```def _apply_ownership_and_markers(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, target_mode: BoxMode, target_ws: Workset | None, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None, requested_name: str = "", force: bool = False) -> ProjectState```
Re-root metadata/shell/vault into the target owner + rewrite markers.

Handles EVERY transition the same way: copy the source metadata into the target owner's metadata
root, write the destination `settings.yaml`, update registry/names, remove the old owner's
metadata. Returns the resulting `ProjectState`. Dispatch is purely on `target_mode`. (See the
drift note under **STEPS 3+4** for what that `settings.yaml` write actually contains.)

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
destination will actually read them — and a pre-P2 standalone source's root-stored `box.*` keys are
UNDERLAID rather than lost (`kanibako.settings.config.carried_box_settings`).

A no-op when the source carries nothing, so a box with no settings file still produces no
destination file — sparse, matching `create`.

⚑ `group=None` is deliberate: only the STANDALONE arm consults it, and standalone's workset tier is
derived from the ROOT, not from a `ProjectGroup` (which `ProjectState` does not carry). For
primary/named the workset tier is therefore `None` — no legacy underlay, so those modes stay
byte-identical to pre-P2.

```def _remove_old_metadata(state: ProjectState, std: StandardPaths, config: KanibakoConfig, *, preserve_name: str | None = None) -> None```
Remove the source project's metadata/shell (+ PRIMARY vault).

🛑 The three arms differ in what they are allowed to touch, and the difference is the whole point:

* **Standalone source** — removes the in-tree kanibako artifacts (the `box_data/` marker dir, the
  root `settings.yaml`, `vault/`) and **NOT the project root itself**. For a standalone the root IS
  `metadata_path`: deleting it would wipe the user's whole project directory AND the
  already-converted destination.
* **Primary source** — unregisters the name, removes the `boxes/` metadata dir, and removes the
  PRIMARY-workset vault dir that Phase 5 moved out of the workspace. `preserve_name` (L2) suppresses
  both when the converted box reuses its own name in place.
* **Workset source** — removes the workset registration (std-aware) so external markers and the
  per-workset connection record are cleaned. **The external source dir is NEVER deleted.**

```def _to_default(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str = "", force: bool = False) -> ProjectState```
Convert/relocate the project so its owner becomes the default workset.

See **Naming — the PRIMARY membership register** for the mint/unregister/register ordering, and
**M-8** for why the copy source is `box_metadata_dir` rather than `metadata_path`.

⚑ When the name is reused in place, the metadata ALREADY lives at the destination — copying would be
a (failing) copy-onto-self, so the existing tree is reused instead.

```_STANDALONE_ROOT_ARTIFACTS```
Top-level entries that are kanibako artifacts (NOT workspace content) and so must STAY at the
standalone root rather than move into the `workspace/` subdir during a convert (drift H
consolidation): `box_data/`, the `workspace` subdir being populated, `vault/`, the root
`settings.yaml` (drift I), the two legacy marker dirs `.kanibako` / `kanibako`, and
`.kanibako.lock`.

```def _consolidate_workspace_subdir(root: Path, workspace_subdir: Path, unwind: _Unwind) -> None```
Move the project's top-level files into the `workspace/` subdir.

Drift H: a standalone box's live workspace is the `<root>/workspace/` SUBDIR, not the root itself.
On an in-place convert the user's project files currently sit AT the root; everything that is not a
kanibako artifact (`_STANDALONE_ROOT_ARTIFACTS`) is relocated into the subdir so the BIND SOURCE
matches the resolved layout. A no-op when there is nothing to move — an empty root, or files already
consolidated. Each move is pushed onto *unwind* so a later failure restores the original placement.

⚑ *root* ALWAYS holds the project's current files at this point in the convert — in-place: the
source dir; relocating: the copy STEP 2 made at *dest*; external-in-place: the external dir that is
BECOMING the standalone root. That uniformity is what lets one call serve every transition.

```def _undo_consolidate(workspace_subdir: Path, root: Path, moved: list[Path]) -> None```
Best-effort reversal of `_consolidate_workspace_subdir`.

```def _unconsolidate_workspace_subdir(workspace_subdir: Path, root: Path, unwind: _Unwind) -> None```
Lift the `workspace/` subdir's contents back up to *root*.

The inverse of `_consolidate_workspace_subdir`, used when converting OUT of standalone in place: the
workspace files return to the project root (where a non-standalone box roots them) and the
now-empty subdir is removed so the converted project keeps no stray `workspace/`. A no-op when the
subdir is absent or empty; each move is pushed onto *unwind*.

```def _to_standalone(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, new_name: str, new_workspace: Path, requested_name: str = "") -> ProjectState```
Convert/relocate the project so it becomes standalone (in-tree metadata).

A standalone box's identity is the canonical opaque `<kuid>_<leaf>`, matching `create --standalone`
/ `duplicate --standalone`. Standalone boxes are NOT named via `names.yaml`; they are registered in
`registry.standalone`.

Convert ESTABLISHES the box uniformly via `establish_standalone`, which HONORS an explicit `--name`
through `box_identity.resolve_standalone_name`:

* a verbatim canonical id is used if free, and REFUSED if taken;
* a non-canonical `--name` becomes a fresh `<kuid>_<sanitized name>`;
* NO `--name` generates a fresh canonical id from the root basename.

It writes `<root>/settings.yaml` with `mode=standalone` and that identity, and registers the box —
with an unwind to drop the registration on failure. `_remove_old_metadata` purges the source's prior
registry entry (`names.yaml` for primary/named) so it does not dangle.

⚑ `new_name` is only a *requested* name: the source's name is passed as the default when the caller
gave no `--name` (i.e. `new_name == state.name`), in which case it is NOT treated as a user
assertion and a fresh canonical id is generated instead. This is exactly why the plan carries
`requested_name` separately (R1/R3).

⚑ **ORDER:** consolidate the source's top-level files into `workspace/` FIRST, THEN lay down the
kanibako artifacts — otherwise the artifacts get swept into the subdir with everything else.

⚑⚑ **The `settings.yaml` landing in `box_data/` must NOT be deleted as an "orphan".** It IS the
destination's BOX TIER now (spec §2c ALL PROJECTS: `meta.box.settings = @meta.box.path/settings.yaml`,
and `@meta.box.path` for a standalone IS `box_data/`). It used to be deleted here on the theory that
the box meta lived only at the ROOT — that theory is the RETIRED model, and deleting the file now
discards the box's settings. Detection is unaffected either way: it reads the ROOT file (§5), which
`establish_standalone` writes.

```def _to_workset(state: ProjectState, std: StandardPaths, config: KanibakoConfig, unwind: _Unwind, *, target_ws: Workset, new_name: str, new_workspace: Path, relocating: bool, dest: Path | None) -> ProjectState```
Convert/relocate the project into *target_ws* (std-aware external wiring).

`source_for_add` is the path `add_project` records and decides external wiring from: the in-tree
workspace dir for an internal landing, the live external workspace path for an external one.

**Whether to copy the workspace tree:**

* internal landing where the workspace is NOT already the in-tree dir ⇒ copy. When relocating into
  the ws via STEP 2 the tree was already moved to `dest == workspaces/<name>`, so it must not be
  copied again;
* external ⇒ never copy.

⚑⚑ **ws→ws re-root: the SOURCE workset must RELEASE the project BEFORE the target registers it.**
The connection record is 1:1, so an external source still mapped to the OLD workset would collide
with `add_project`'s "already connected" guard. The source registration is therefore dropped first —
which clears the per-workset connection record and the discoverability symlink, and NEVER touches
the user's external dir.

⚑ That release DELETES the source's metadata dirs, so the forward copy would have nothing to read
from. A `tempfile` STASH of the source metadata (including the shell) is taken first, and both the
forward copy and the unwind restore read the stash rather than the live paths. The stash is
discarded via `unwind.on_success`, so it survives exactly as long as a rollback might need it. For
an INTERNAL ws→ws move the workspace tree was already relocated in STEP 2, so `remove_project`'s
`remove_files` only sweeps the leftover skeleton dirs.

`add_project` (std-aware) registers the project, creates the skeleton dirs, and — for an external
landing — writes the markers.

⚑ `add_project(..., force=True)`: a convert/move/duplicate INTO a workset is a DELIBERATE absorb, so
it must override the standalone-marker connect guard (B2a) — the source of a standalone→workset
convert still carries its in-place `box_data/` marker at this point, because the marker is removed
LATER in the convert. `force` affects only a standalone-marked source, so it is a no-op for the
non-standalone move/duplicate cases.

⚑ `_deliver_carried_box_settings` is called even though the metadata was just copied: for a
STANDALONE source the copy CANNOT supply the box settings, since its box tier is a different file
from the root one a pre-P2 box stored them in (M-8).

⚑ `_remove_old_metadata` is skipped when the source was a workset — the registration was ALREADY
released above, and cleaning again would double-remove.

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

```def _safe_remove_project(ws: Workset, name: str, std: StandardPaths) -> None```
Swallowing wrapper around `remove_project` for the unwind stack.

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
(`_duplicate.py`): if the project's `.kanibako.lock` is present, warn and abort unless *force*.
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
