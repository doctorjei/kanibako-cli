# Standard & Project Paths
_XDG resolution, project hash computation, directory creation, and initialization_

This module owns the LAYOUT tier: where a box's state lives on the HOST. It resolves the XDG base
directories, the two-layer path foundation (Layer-1 `config.*` bootstrap keys, Layer-2 `system.*`
path settings), detects which of the three box MODES a directory is in, and resolves — and
optionally materializes — the per-box directory set for each mode.

⚑ **The deferred imports are LOAD-BEARING.** `paths.py` imports `StandardPaths` consumers at module
scope but DEFERS `kanibako.project.workset`, `kanibako.project.workset_registry`,
`kanibako.project.registry_store` and `kanibako.project.import_reconcile` into FUNCTION BODIES.
Those modules import `paths.py`; hoisting any of them to module scope re-creates the cycle. The
`_WorksetRooted` / `_WorksetLike` / `_WorksetProjectLike` protocols and the `WorksetSpec` primitive
exist for the same reason: they let this module speak about a workset without importing the concrete
`Workset` class.

## The Three Box Modes

`BoxMode` is surfaced as the `box.mode` token.

* **`primary`** — the implicit PRIMARY workset. Box state lives under
  `{config.primary_workset}/boxes/<name>/`.
* **`named`** — a named workset. Box state lives under the workset's own `projects/<name>/`.
* **`standalone`** — all state lives inside the project directory itself.

### The standalone marker (`STANDALONE_META_DIR`)

`box_data` is the STANDALONE box-store dir name. It is `{meta.box.path}` for a standalone box (the
empty leaf of `{workset.boxes}`, keyspec §2c) and HALF of the detection marker
(`~/canon/workbook/specs/system-design-1.8.0.md` § "Detection & import") — the other half is the ROOT `workset.yaml`.
Both must be present; a bare `box_data/` directory is not a marker.

It is a LAYOUT constant rather than a detail of detection, so it is defined in `settings/bootstrap`
and re-bound at the TOP of this module, not inside a detection helper.
`standalone_store_teardown_plan` reads it here; `commands/start` and `commands/box/_lifecycle`
import it from here.

### `DetectionResult`

*mode* is the detected box mode. *project_root* is the ancestor directory where the marker was
found — it may differ from the original *project_dir* when the user is in a subdirectory.

## `StandardPaths` — the host-side path surface

Resolved XDG and kanibako standard directory paths. The system-level derived directories split into
two roots:

* the **Layer-1 CONFIG-key foundation** (`config.*`: `data` / `settings` / `agents` /
  `primary_workset` / `registry` / `journal`), and
* the **Layer-2 `system.*` path settings** (`channelroot` / `template` / `canon` / `backup` /
  `cache` / `runtime`).

`global` is not a key — its children inline `{config.data}/global/...`, and the `global/` dir is
created on demand by the atomic writer when those files are first written.

### `template` — the system TEMPLATE ROOT

`system.template` defaults to `global/template` under `config.data`. Holding the ROOT rather than
the box-seed dir directly leaves room for further template subtrees without new keys.

⚑ The box-HOME seed is `template/box/home`, **NOT** the root and **NOT** `box/`: `box/` is the box
TEMPLATE root, holding `home/` (spec §2a layers 1–3, the `seeded` category) beside `canon/handbook/`
(the box handbook HOST template, not a seed; see
`launch.templates.install_box_handbook_template`).

### `canon` — the SYSTEM-level CANON CONTRIBUTION root (spec §2g)

Its `handbook/` subtree supplies `SYS_CONTENTS.md` + the `general` chapter, bound RO into every box.

⚑ It names what this SCOPE CONTRIBUTES to the canon, NOT a copy of the assembled tree: `~/canon`
(guest) is the assembly, `{<scope>.canon}` (host) is one scope's contribution to it.

### `journal` — the lifecycle journal

Write-ahead log of in-flight box-lifecycle ops, beside the registry
(`config.journal = {config.data}/global/journal.yaml`). PATH-BASED on the resolved key (mirrors
`registry`; no reconstruction).

### `boxes`, `primary_vault_ro`, `primary_vault_rw`, `primary_logs`

`boxes` is the PRIMARY-workset box store, `{config.primary_workset}/boxes`, a plain field. The
path-tier table carries it under the non-key name `_primary_boxes` (see `resolve_system_paths`).
Per-box metadata/shell live under `boxes/<name>/`; the PRIMARY vault/logs live as siblings under
the PRIMARY workset (see `resolve_project`).

The vault + logs roots DEFAULT to `{config.primary_workset}/vault/{ro,rw}` and
`{config.primary_workset}/logs`: the PRIMARY vault lives in the PRIMARY workset, not the workspace.

⚑⚑ **All four PRIMARY roots are RESOLVED, not composed.** The spec declares no `system.boxes`,
`system.logs`, `system.vault_ro` or `system.vault_rw` at all (§1) — these fields are SURROGATES
for the PRIMARY workset's `workset.{boxes,logs,vault_ro,vault_rw}`, which are declared,
CLI-settable, repointable keys in EVERY mode (§2c ALL PROJECTS, R-29). The PRIMARY workset root is
an ordinary workset root carrying an ordinary `workset.yaml`, so it repoints exactly as a named one
does. `resolve_system_paths` reads that `workset.yaml` ONCE and routes all four through
`project.workset.resolve_workset_{boxes,logs,vault_ro,vault_rw}`. Resolving HERE rather than at
`_primary_box_paths` is deliberate: every consumer of `std.boxes` / `std.primary_logs` /
`std.primary_vault_*` (box create, `box rm`, `clean`, purge, the helper hub) then sees the ONE
answer, with no edit at any of those sites.

`primary_logs` is `Path | None`: `None` when the PRIMARY `workset.logs` is a present `<None>`. The
path-tier table holds paths only, so `resolve_system_paths` OMITS `_primary_logs` in that case and
`load_std_paths` reads the omission as `None`.

⚑ `boxes` and `primary_logs` are in the resolved set, and `Workset.projects_dir` / `logs_dir` and
the named arm of `helper_log_path` read them. Composing `pw / BOXES_PATH` or `pw / LOGS_PATH`
instead is what the tripwire on those joins bans — the CONSTANT, not just the string.

## Groups and the workset tier

### `ProjectGroup`

Captures the default-vs-workset difference as *data* rather than control flow. The implicit default
group is the *default workset* (`is_default` is True), rooted at `{config.primary_workset}` (spec
§2c: PRIMARY `meta.workset.path`); a named workset forms a non-default group rooted at the workset
root. Standalone projects belong to no group (`ProjectPaths.group` is None).

`ProjectPaths.project_path` is `Path | None`: `None` ONLY for a standalone box whose root nulls
`workset.workspaces` (Q106). Such a box has no workspace, its `meta.box.workspace` is
`<None>` and its launch refuses (`commands/start._refuse_null_workspace_bind`). The readers that can
meet it: displays print `<None>` (`box info`, `box create`'s messages, `clean`, `archive`,
`extract`, `diagnose`); the lifecycle verbs (`box move`/`remap`/`convert`) refuse at
`_lifecycle._state_from_paths`; `duplicate` and `extract`'s git check treat it as no workspace; the
launch narrows it after the probe. Primary and named boxes always carry a path.

*local_shared_base* is the root under which the local-shared path lives (`base / "common"`): the
standard data path for the default group, the workset root for a workset group.

### `_WorksetRooted`

Structural type for "anything rooted at `{meta.workset.path}`". Satisfied by both `ProjectGroup` (the
launch-side view) and `kanibako.project.workset.Workset` (the workset-command view), so the workset
file derivations serve every caller through ONE expression.

### `_WorksetLike` / `_WorksetProjectLike`

Structural types for the attributes `WorksetSpec.from_workset` reads. They avoid importing the
concrete `kanibako.project.workset.Workset` into `paths.py` (which `workset.py` imports from,
creating a cycle).

### `WorksetSpec`

Primitive view of a workset, decoupled from `kanibako.project.workset.Workset`. Carries only the
values the path resolver and project listings need, so `paths.py` does not import the `workset`
module (which depends on `paths.py`). Callers holding a full `Workset` build one with
`from_workset`.
`workspaces_dir` is `Path | None`: `None` when the root nulls `workset.workspaces` (Q106).

## The two-layer path foundation

The two default tables and the XDG table live in `settings/bootstrap.py`, and
`llm-docs/kanibako/settings/bootstrap.py.md` explains them. This module RESOLVES them.

### Layer 1 — the CONFIG-key FOUNDATION (`CONFIG_PATH_DEFAULTS`, spec §1)

`resolve_config_paths` resolves the bootstrap CONFIG keys FLAT, over one `LevelView` whose defaults
are the table — **NOT** through the keyspace pipeline. Chicken-and-egg: the pipeline needs these
resolved to find its own input files. A `config.*` ref resolves against this set; `{$XDG_*}`
against `host_xdg_map(data_home)` unless the caller passes *xdg_vars*. `layer1_foundation` (behind
`resolve_data_path`) passes `spec_default_xdg_map` instead, so it creates nothing and an expression
over `{$XDG_RUNTIME_DIR}` does not resolve there.

### Layer 2 — system-scope SETTINGS keys that are PATHS (`SYSTEM_PATH_DEFAULTS`, spec §1/§2g)

SETTINGS keys (system tier), not bootstrap config. They resolve the normal way at launch
(assemble→merge→expand), and `load_std_paths` ALSO materializes them into `StandardPaths`, the
host-side path surface: `load_system_tier` reads their set values and `_resolve_system_tier` (also
behind `resolve_system_paths`) resolves their refs against the Layer-1 foundation. There is no
`system.boxes` key: `_resolve_system_tier` resolves `std.boxes`, the primary logs dir, and the
primary vault dirs from the PRIMARY workset's `workset.*` keys.

### Module-level singletons

`logger` is the module logger (`get_logger("paths")`); the XDG fallback warnings and the `_flag_*`
advisories go through it (the advisories use `get_logger(__name__)`).

### `XDG_SPEC_DEFAULTS` (imported from `settings/bootstrap`)

`spec_default_xdg_map` loops over it; `host_xdg_map` adds `XDG_RUNTIME_DIR`, which has no spec
default, through `resolve_xdg(XDG_RUNTIME_DIR, None)`. The table itself is in `bootstrap.py.md`.

## Functions

```python
def workset_settings_path(group: _WorksetRooted | None) -> Path | None
```
THE workset-tier settings-file derivation (spec §2c ALL WORKSETS: `meta.workset.settings` =
`{meta.workset.path}/workset.yaml`).

`group.root` carries `{meta.workset.path}` for both modes — the PRIMARY workset roots at
`{config.primary_workset}`, a NAMED workset at its own root — so the one expression serves every
caller (launch cascade, config verbs, `--effective` displays). `None` (no group = standalone) has no
workset tier file.

The two `@overload`s exist so a caller passing a concrete group gets `Path`, not `Path | None`.

```python
def _default_project_group(std: StandardPaths) -> ProjectGroup
```
The PRIMARY (default) workset's `ProjectGroup`.

Spec §2c: the PRIMARY workset roots at `{config.primary_workset}` — the workset-tier settings/env
files derive from this root (F4). `local_shared_base` is the data path.

```python
def box_tree_materialized(proj: ProjectPaths) -> bool
```
True when the box tree a `create` would materialize is ALREADY on disk.

The MODE-AWARE analog of `is_new`, computable from a NON-materializing probe (`initialize=False`)
— which is the whole point: `is_new` is only set inside the `initialize=True` branch that does the
mutation, so a caller that wants to REFUSE before mutating cannot ask `is_new` and has to ask this
instead.

* PRIMARY / NAMED — the box dir IS `metadata_path`, and `resolve_project` gates `is_new` on exactly
  that dir, so with `initialize=True` this is precisely `not is_new`.
* STANDALONE — `metadata_path` is the USER'S OWN project root, which always exists (it is their
  runtime dir), so it says nothing. The marker is the box's store, `standalone_box_store(root)`
  (`<root>/box_data` by default) — the same dir `resolve_standalone_project` gates `is_new` on.

⚑ For a BRAND-NEW primary box `_resolve_local_dir` misses and `metadata_path` is the
`__unregistered__` placeholder, which does not exist ⇒ False ⇒ the create proceeds. That coupling is
`resolve_project`'s own `is_new` gate, not this function's.

The mode split is NOT restated here — `box_metadata_dir` already owns it, and it lands on exactly
the two dirs the two resolvers gate `is_new` on. One derivation, not a second copy that can drift.

```python
def standalone_box_store(root: Path, *, early: EarlyScope, settings_doc: "Mapping[str, Any] | None" = None) -> Path
```
The RESOLVED `workset.boxes` of the standalone box rooted at *root* — ITS store
(`project.workset.resolve_workset_boxes`, `standalone=True`). `<root>/box_data` is only the default.
⭐ **THE ONE PLACE A STANDALONE STORE PATH IS ANSWERED**: every reader and deleter goes through here
or `box_metadata_dir`, which calls it; composing `box_data/` instead names a directory a repointed
box never uses. *settings_doc* is a `workset.yaml` the caller already read, so the resolver and its
own guard cannot answer two versions.

```python
def standalone_store_teardown_plan(root: Path, *, early: EarlyScope) -> tuple[Path | None, Path | None]
```
The standalone box store as `(removable, retained)` for a teardown — ONE split. ⚑⚑ Only the store
kanibako CREATED for this role is removable: the default `box_data/`, or a repoint that RESOLVES
back inside the root. A store the box's OWN `workset.yaml` repoints elsewhere is the USER'S OWN
directory, and no verb `rm -rf`s it on their behalf (`report_retained_store` names it instead); a
value inherited from the SYSTEM tier is not that nomination, so it stays removable. Both ends are
RESOLVED, so a `..` or a symlinked parent cannot read as a descendant while sitting outside the
root. One `workset.yaml` read feeds both the resolution and the tier guard. `None` on either arm
means nothing to act on (no store dir, or a symlinked one).

```python
def _standalone_settings_files(root: Path, *, early: EarlyScope) -> tuple[Path, Path]
```
The STANDALONE `(box_tier, workset_tier)` pair — BOTH always real paths.

The standalone arm of `_box_settings_files`, split out because it is the one mode whose workset tier
is unconditional (it is the project ROOT file, not a `ProjectGroup` lookup that can come back
`None`). Callers inside the standalone resolver need that stronger type — they read and write both
files — and getting it from the type system beats asserting it at each site. Still ONE derivation:
`_box_settings_files` delegates here rather than restating it.

```python
def box_metadata_dir(mode: BoxMode, metadata_path: Path, *, early: EarlyScope) -> Path
```
The DIR holding a box's own metadata — home, session state, box tier.

Equals `metadata_path` for primary/named, but for STANDALONE `metadata_path` is the PROJECT ROOT,
whose box metadata lives in its store, `standalone_box_store(root, early=early)` — `box_data/` by
default (beside `workspace/` and `vault/`, which are NOT box metadata). *early* is read on the
standalone arm only, and is required rather than optional-plus-assert.

⚑ Lifecycle ops (convert / move / duplicate) must copy from HERE, not from `metadata_path`. Copying
a standalone ROOT "as if it were box metadata" both drags the workspace + vault into the
destination's box dir AND delivers the source's WORKSET-tier file to the destination's BOX tier —
so the real box settings (in the store) are never read again.

```python
def _box_settings_files(
    mode: BoxMode, metadata_path: Path, group: "_WorksetRooted | None", *,
    early: EarlyScope | None = None,
) -> tuple[Path, Path | None]
```
THE `(box_tier, workset_tier)` settings-file derivation (spec §2c).

ONE expression, spelled ONCE. `meta.box.settings` is the UNIFORM `{meta.box.path}/box.yaml` in
EVERY mode (spec §2c ALL PROJECTS), so the box tier is ALWAYS a real path — never `None`:

* **primary / named** — box tier = the box's own `<metadata_path>/box.yaml` (`BOX_META_FILE`,
  which IS `{meta.box.path}` for these modes); workset tier = `workset_settings_path(group)` (the
  workset root's `workset.yaml`).
* **standalone** — `{meta.box.path}` is the box's store, the resolved `workset.boxes`
  (`standalone_box_store`; `<root>/box_data/` by default), so the box tier is `<store>/box.yaml` —
  **ABSENT BY DEFAULT**
  (spec §2c + §4 STANDALONE tree): an absent file is an empty tier, and `config_io.load_doc` yields
  `{}` for it, so a standalone box with no box file resolves byte-identically to one with no box
  tier at all. The workset tier is the ROOT `<root>/workset.yaml` — the file that plays the
  WORKSET tier for a degenerate one-box workset, and the file DETECTION reads
  (`~/canon/workbook/specs/system-design-1.8.0.md` § "Detection & import"; `box_resolve.stores_standalone_registry_null`).
  A `box.*` key stored THERE still resolves for box scope via R2 downward-defaults (`box` ⊂
  `workset` in `SCOPE_CONTAINMENT` — the workset-tier read KEEPS `box.*`). That is DECLARED DESIGN
  (keyspec §2c).

⚑ This pair is the SINGLE SOURCE for READ, WRITE **and** ANCHOR: the launch cascade's
`box_path`/`workset_path`, the `meta.box.settings` anchor, and the `config set` / `get` / `show` /
`reset` target all derive from here. A second, independent spelling of either path would let
`config set` write a file the launch never reads ("I set it and nothing changed").

⚑ The return type says `Path`, not `Path | None`, deliberately: the non-optional box tier is
TYPE-CHECKED, so mypy rejects a `None` box tier and flags any `is None` narrowing at a call site.
**This one is kept as a marker in the source docstring**, because the constraint lives at that exact
signature.

*group* is anything rooted at `{meta.workset.path}` — a `ProjectGroup` or a `WorksetSpec`, since the
NAMED resolver builds its group only at return time. *early* is required on the standalone arm,
which resolves the store (asserted).

Takes the primitives rather than a `ProjectPaths` because the standalone/primary/named
resolvers must consult it WHILE that dataclass is still being constructed (`enable_vault` is read
before the instance exists). `box_workset_settings_paths` is the `ProjectPaths` adapter.

```python
def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]
```
The `ProjectPaths` ADAPTER over `_box_settings_files`.

The name every caller uses. Carries no logic of its own — the derivation lives in one place so the
pair cannot be spelled twice.

```python
def resolve_box_enable_vault(global_path: Path, *, box_path: Path,
                             workset_path: Path | None) -> bool
```
Resolve `box.enable_vault` through its cascade, `base < system < workset < box`.

An absent key, or a winning `None`, gives the declared default (`True`); any other value is typed by
`config._typed_box_scalar`, so a stored `false` reads `False`. Callers:
`ProjectPaths.vault_enabled()`, the PRIMARY and STANDALONE resolvers on create, and
`commands/box/_lifecycle.py`.

```python
def _narrow_box_scalar_cascade(
    global_path: Path, *, workset_path: Path | None, box_path: Path | None,
) -> KeyStore
```
The cascade `resolve_box_enable_vault` reads: `assemble_levels` over a `ReadPurpose.NARROW` file
read, floored with `config.box_scalar_defaults_floor()`, then merged without the two agent levels.

⚑ It stops at the merge, so it skips the launch snapshot's whole-tree audit
(`settings_launch._refuse_undeclared_snapshot`). Path resolution runs under every verb, including
`kanibako box show`, which must still answer for a box whose file carries an undeclared entry
(`test_box_show_marks_a_hand_written_undeclared_entry`, `tests/test_commands/test_config_cmd.py`).
So `resolve_project` does not enforce the closed keyspace; the launch does.
`tests/test_settings/test_config.py::TestTheTwoBoxScalarResolvesAgree` pins its value equal to
`settings_launch.load_merged_config`'s at every tier.

```python
def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path
```
Resolve an XDG base directory per the freedesktop Base Directory spec.

The environment variable *var_name* is honored **iff it is set AND absolute**; a relative value is
INVALID per the spec and is ignored (we fall back to the default). When unset/invalid:

* For the dirs that have a spec default (*spec_default_suffix* is the suffix under `$HOME`, e.g.
  `".local/share"`), return `$HOME/<suffix>`.
* For `XDG_RUNTIME_DIR` (*spec_default_suffix* is `None`) there is NO spec default: fall back to a
  replacement dir with similar capabilities and **warn** — prefer `/run/user/<uid>/kanibako` when it
  is usable (writable, owned by us), else a `0700` temp dir.

An absolute env value is returned as-is (resolved); the runtime-dir fallback is the only case that
creates a directory.

```python
_runtime_fallback_cache: dict[tuple[str, str], Path]
```
Process-lifetime cache of the chosen runtime-dir fallback. Without it, a temp-dir fallback would
leak a NEW dir (and re-warn) on every `resolve_system_paths` call within a single process. Keyed by
`(var_name, env value)` so a test/process that later SETS the var is unaffected.

```python
def _fallback_runtime_dir(var_name: str) -> Path
```
Choose a replacement for an unset/invalid `XDG_RUNTIME_DIR` and warn.

Prefers `/run/user/<uid>/kanibako` when `/run/user/<uid>` exists and is a writable directory owned
by us; otherwise a `0700` per-user temp dir. Never substitutes silently — warns on first selection
(cached per process so repeated calls don't leak temp dirs or re-warn).

```python
def _runtime_base_usable(
    base: Path, *, follow_symlinks: bool = True, require_private: bool = False,
) -> bool
```
True iff *base* is a directory we own and can write to.

Mirrors the freedesktop requirement that the runtime dir be owned by the user and writable.
Best-effort: any OS error → not usable. ⚑ The owner test is spelled ONCE, here, for both callers.
*follow_symlinks* and *require_private* default to what the `/run/user` base gets (`stat`, which
follows a link, and no mode test). The stable temp dir — a PREDICTABLE name in a world-writable
root — passes both: `lstat`, so a planted symlink is refused as a symlink, and a mode test, so a
group- or other-accessible directory is refused.

```python
def xdg(env_var: str, default_suffix: str) -> Path
```
Resolve an XDG directory from environment or default under `$HOME`.

Thin wrapper over `resolve_xdg`: honors the env var only when set AND absolute (a relative value is
ignored per the spec), else returns `$HOME/<default_suffix>`. Used by the
many call sites that just need a spec-backed XDG base dir.

```python
def host_xdg_map(data_home: Path | None = None) -> dict[str, str]
```
Build the canonical HOST-side `{$XDG_*}` map for a `ResolveCtx`.

THE single builder for the `xdg=` argument of every host-side
`kanibako.settings.settings_resolve.ResolveCtx` ([Jei:2026-07-02]: XDG vars must have
fallbacks). A hand-rolled partial map would make a stored value like `{$XDG_CACHE_HOME}/kanibako` (the
setup-materialized `system.cache`) raise `Variable {$XDG_CACHE_HOME} is not set in this context` at
expand time — the resolver reads ONLY this map, never the environment, so a missing key is
unrecoverable at the call site.

Every var in `XDG_SPEC_DEFAULTS` resolves via the hardened `resolve_xdg` (env honored iff set AND
absolute; unset/empty/relative → the XDG-spec default under `$HOME`), plus `XDG_RUNTIME_DIR` (no
spec default: fallback + warn). *data_home* is an optional already-resolved `$XDG_DATA_HOME`
anchoring the default tree (the flat foundation/system resolver passes it); when None it resolves
from the environment like the rest.

```python
def resolve_config_paths(
    set_values: Mapping[str, str | None], *, data_home: Path, home: Path, xdg_vars: Mapping[str, str] | None = None,
) -> dict[str, str]
```
Resolve the Layer-1 CONFIG-key foundation to concrete host paths. *xdg_vars*, when given,
REPLACES the live `host_xdg_map(data_home)` build — the seam `resolve_data_path` uses to resolve
`config.data` without touching `XDG_RUNTIME_DIR`, whose fallback can mkdir.

*set_values* holds raw user-set `config.<leaf>` expressions (from the `kanibako.cfg` set).
Returns `{config.<key>: resolved_str}` for every key in `CONFIG_PATH_DEFAULTS` — the FOUNDATION
mapping injected into `kanibako.settings.settings_resolve.ResolveCtx.config` so `{config.*}` refs
resolve there (spec §1A / JC-2). Flat by design (chicken-and-egg): the keyspace pipeline needs these
resolved to find its own input files, so they resolve OUTSIDE it, with `{config.*}` refs chained
within this set only.

```python
def resolve_system_paths(
    set_values: Mapping[str, str | None], *, data_home: Path, home: Path,
) -> dict[str, Path]
```
Resolve the path tier to concrete host paths.

*set_values* holds raw user-set expressions keyed by their full dotted name — the MERGED config-file
set (both Layer-1 `config.<leaf>` and Layer-2 `system.<leaf>` keys, e.g. the global config's
`config_paths`). It is split by prefix: `config.*` seeds the Layer-1 foundation, `system.*` the
Layer-2 path settings. *data_home* is the already-resolved XDG data base exposed as
`$XDG_DATA_HOME`; *home* expands a leading `~`.

Returns `{name: Path}`: every Layer-1 `config.*` key and every Layer-2 `system.*` key under its full
dotted key, plus the derived PRIMARY-workset roots under the NON-KEY names `_primary_boxes` /
`_primary_vault_ro` / `_primary_vault_rw` / `_primary_logs` (under `{config.primary_workset}`). The
`system.*` defaults `@`-ref a Layer-1 config key, resolved against the foundation injected into
`ctx.config`.

Internal notes:

* The nested `lookup` implements the **resolver SPLIT** (spec §1A / JC-2): `{config.*}` → the Layer-1
  foundation (`ctx.config`); `{system.*}` → the system path set. Prefix-driven.
* `system.*` config paths are always scalar strings (no structured category leaves at this tier),
  which is why the `object`-typed value is narrowed with `str(...)` before expansion.
* The `_primary_*` entries are the PRIMARY-workset box/vault/logs roots, derived from the
  resolved PRIMARY workset dir (`{config.primary_workset}`). ⚑ `_primary_vault_ro` /
  `_primary_vault_rw` are RESOLVED through the workset dir-key route (one `workset.yaml` read,
  both arms), not composed — see the `primary_vault_*` field notes above. That is the only file
  read in this function, and it is best-effort: an ABSENT or unparseable `workset.yaml` yields the
  declared defaults, while an unresolvable `@`-ref REFUSES and names the key.
* ⚑ The `_primary_*` names are NOT keys and are not key-shaped on purpose: the manifest records
  the `system._*` spellings under `not_keys.code_residue` (code only, never spec-sanctioned),
  so a key-shaped name here is a stray in the closed keyspace (spec §0). A consumer filtering
  this table by `config.` / `system.` prefix never sees them.

```python
def load_system_config(
    user_config_path: Path, *, data_home: Path, home: Path, tolerate_bad_settings: bool = False,
) -> dict[str, Path]
```
Resolve the path tier from the CONFIG file set **and the SYSTEM SETTINGS file**.

Three layers, read in cascade order so the most-authoritative present value of each set-value wins
**before** expression resolution:

1. `/etc/kanibako/base.cfg` — site-wide overridable defaults (least specific). **`config.*`
   only.**
2. *user_config_path* — the user's global `~/.config/kanibako.cfg` (overrides the base).
   **`config.*` only.**
3. `{config.settings}` — the SYSTEM SETTINGS file's `system:` table, filtered to
   `SYSTEM_PATH_DEFAULTS` (most specific).

Missing files are skipped (each contributes nothing). With *tolerate_bad_settings*, a SYSTEM
settings file that does not load contributes nothing, and one whose `system:` table is refused still
contributes its early `workset.*` tier; the CONFIG files are read strictly either way
(`_path_tier_set_values`). The merged set-values are split by prefix into the Layer-1 `config.*`
foundation and the Layer-2 `system.*` path settings, then handed to `resolve_system_paths`, which
fills in the defaults and resolves `@`-/`$XDG_*`-references.

⚑⚑ **EACH FILE CONTRIBUTES EXACTLY ONE LAYER.** A `system:` table hand-written into a CONFIG file
never enters the Layer-2 path tier — *"kanibako_config.yaml <-- cannot have settings. Period."*
([Jei:2026-08-26], on the Layer-1 file, `kanibako.cfg`). It mirrors the filter layer 3 applies in
the other direction (a `config:` table in a SETTINGS file never reaches Layer 1, spec §1), and
`resolve_data_path` applies the same filter to the same two files.

A user with only `~/.config/kanibako.cfg` (no `/etc` file) gets the base layer empty, so the user
file is the sole set-source.

⚑⚑ **LAYER 3 IS LOAD-BEARING.** Spec §2g declares
`system.{template,canon,cache,runtime,backup,channelroot}` and `system.channels.*` SETTINGS keys,
*"set in settings files at the `system` cascade level"*, and `config set system.canon=…` writes them
there. Reading the CONFIG files alone would let that write reach the launch cascade — binds, seeds,
`show --effective` — and never `StandardPaths`: accepted, persisted, half-effective, and silent.
`tests/test_settings/test_repoint_reaches_std_paths.py` asserts the EFFECT rather than the
destination.

⚑ **THE LAYER-1 RESOLVE RUNS TWICE, DELIBERATELY.** Locating the settings file IS `{config.settings}`,
so the foundation must resolve before the file can be opened; `resolve_config_paths` is a pure dict
resolve over set-values already in hand, so the second pass reopens nothing.

⚑ **THE SETTINGS LAYER IS FILTERED TO `SYSTEM_PATH_DEFAULTS`** (P13 — derived from the table, not a
list at the call site). That file's `system:` table also holds `system.agent`, the
`auth`/`env`/`secret_path` families and the bind-shaped categories, none of which belong to the path
tier — and a `config:` table hand-written into a SETTINGS file must never reach Layer 1, which lives
in `kanibako.cfg` alone (spec §1). Both halves are pinned.

⚑ The `kanibako.settings.config` import inside the body is a lazy import that avoids a
`config` ↔ `paths` cycle at module load — do not hoist it.

`bootstrap_config_paths(...)` yields the CONFIG file's set-values keyed by their full dotted name
(`config.*` alone), or `{}` when the file is absent — so missing layers are skipped automatically.
🛑 It RAISES on a settings table in that file, which is why every verb that resolves a
path is where a user with a stale `kanibako.cfg` first hears about it. The SETTINGS file's
`system.*` half is read by `config.system_path_set_values`, a separate walk over that file's
`system:` table.

```python
def host_config_map(std: StandardPaths) -> dict[str, str]
```
The resolved Layer-1 `config.*` foundation projected BACK onto its own dotted key names — the Layer-1
twin of `system_path_floor`, and the `config=` twin of `host_xdg_map`. A host-side `ResolveCtx` is
built from those two builders and nothing else; consumers put this in `ctx.config` so a stored
`{config.*}` source resolves under the resolver SPLIT (spec §1A / JC-2).

⚑⚑ **ONE FUNCTION, DERIVED FROM `CONFIG_PATH_DEFAULTS`.** The host-side ctx builders
(`agent_select.host_resolve_ctx`, and `settings_launch`'s input builders) take their config map from
here. A hand-written map that misses one declared key (`config.journal`, say) lets `config set`
accept a binding sourced at that key — the set-time foundation iterates the whole resolve — while
the launch drops it with no message and rc 0: accepted-then-silently-discarded, which is strictly
worse than either side being consistently wrong.

⚑ **THE KEY SET IS DERIVED, AND THE ATTRIBUTE NAME IS A RULE, NOT A COINCIDENCE.** Layer 1 names its
`StandardPaths` fields after its keys (`config.data` → `data`, `config.primary_workset` →
`primary_workset`, …), all six, which is exactly why this needs no alias at all where
`system_path_floor` needs exactly one — `system.channelroot` resolves into `std.channels`, the single
Layer-2 field whose name does not follow from its key. A Layer-1 key declared without the matching field raises
`AttributeError` on the next ctx build, at every launch: a crash is strictly better than a silent
omission.

⚑ `tests/test_settings/test_path_tier_parity.py` pins this
map against the set-time foundation across all three box modes plus the `agent_name=None` selection
pass, effect-based on both sides, with an anti-vacuity arm pinning the derived side to
`CONFIG_PATH_DEFAULTS` so a filter bug emptying both cannot pass.

```python
def system_path_floor(std: StandardPaths) -> dict[str, str | None]
```
The resolved Layer-2 `system.*` tier projected BACK onto its own dotted key names — the inverse of
what `resolve_system_paths` produced. Consumers fold it into a settings floor so a stored
`{system.*}` source resolves; each value equals the corresponding `std` attribute, so an
`@`-ref-routed bind is byte-identical to a runtime-probed literal.

⚑⚑ **ONE FUNCTION, ONE CARRIER.** Every consumer takes the tier from here, through
`settings_launch.resolve_inputs` (`system_floor`) — the launch and the workset preview alike. Two
hand-written maps of one shape drift, and a key one misses is `__MISSING__` in the snapshot, so a
binding sourced at it is dropped from the collapse with no message and rc 0. Two carriers of one
shape is the defect class; the repair is one carrier, not a better promise.

⚑⚑ **THE KEY SET IS `SYSTEM_PATH_DEFAULTS` ENTIRE**, derived, with no hand-named roots: every
declared key with a manifest default — `system.backup`, `system.cache`, and `system.runtime`
included — reaches the floor, so the set-time tier (`config_interface._path_tier_split`, which
iterates the whole table) and the launch snapshot answer the same keys. [R143] is the authority —
*"if it has a default value, yes, thay value should be placed in the keystore"* — universal, no
allowlist and no origin test.

⚑ **RESERVED AND REACHABLE ARE ORTHOGONAL, WHICH IS WHY THIS NEEDS NO DISCRIMINATOR.** Nothing reads
`system.backup`, and nothing here gives it a consumer: *reserved* is a fact about consumers, this
floor is a fact about the keystore, and a reserved key still answers.

⚑ **`_FLOOR_FIELD_ALIASES` IS A SPELLING TABLE, NOT A MEMBERSHIP LIST**, and the distinction is the
point. Membership is the declared table; the alias map only carries the one key whose
`StandardPaths` field does not follow from its name (`system.channelroot` → `std.channels`).
Everything else is `key.split(".", 1)[1].replace(".", "_")`. A key declared without a matching field
raises `AttributeError` at the next floor build — a membership list fails by silence, this fails by
crash, the same trade `host_config_map` makes one layer down.

⚑ **CONSUMERS.** `settings_launch.resolve_inputs` carries the map (`system_floor`); each resolver
folds it into its `default_categories` through `_merge_default_categories` as a deliberate
late-injection override. The leaves are scalars, so they take the last-wins arm, claim no category
destination and trigger no origin refusal, and being declared they pass
`_refuse_undeclared_snapshot`.

```python
def load_std_paths(
    config: BootstrapConfig | None = None, *, tolerate_bad_settings: bool = False,
) -> StandardPaths
```
Compute all standard kanibako directories.

If *config* is None, it is loaded from the config file (which must exist). ⚑ RESOLVE-ONLY: it
creates NOTHING. Each store materializes at its own point of use, so one stored-but-unusable value
cannot brick every command — including the `reset` that would un-store it.
*tolerate_bad_settings* is forwarded to the path tier (see `load_system_config`).

The system-level path tier (settings-framework `system.path.*`) is resolved from the CONFIG file
set: `/etc` `base.cfg` < user-global. A user with only `~/.config/kanibako.cfg` gets an empty
`/etc` layer. Neither the cache nor the state root derives from the data
dir's leaf: `system.cache` and `system.state` are keys of their own, and this is where each one's
resolved value is materialized ([R166]).

```python
def resolve_project(
    std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None, *,
    initialize: bool = False, enable_vault: bool | None = None,
    name_override: str | None = None, register: bool = True,
) -> ProjectPaths
```
Resolve (and optionally initialize) per-project paths (PRIMARY mode).

When *initialize* is True (used by `start`), missing project directories are created and credential
templates are copied in. When False (used by subcommands like `archive`/`purge`), the paths are
merely computed.

PRIMARY boxes/vault/logs live under `{config.primary_workset}` (the real PRIMARY-workset
dir); there is no layout axis. Per-box state is `boxes/<name>/` (metadata + shell) with the vault at
`{config.primary_workset}/vault/{ro,rw}/<name>`.

*enable_vault* controls whether vault directories are created and mounted. Defaults to True for new
projects; existing projects read from `box.yaml`.

*register* (the interrupted-create journal): when False AND this call materializes a NEW box, the
box dir + meta are created and `is_new` is set, but the PRIMARY membership is NOT written — the
caller defers registration until AFTER the home seed (journal entry → seed → register → clear-entry)
so the invariant "registered ⟹ fully seeded" holds on the sole store. The picked name is still
reserved against a concurrent create via the directory-aware `pick_primary_box_name`. Defaults True
(every other caller registers inline).

### `resolve_project` — the bare-token front door

The designation goes through `resolve_designation` with `unknown_name_is_path=True`: an identifier
with no file/dir of that name in cwd is tried as a registered name, and a miss falls through to path
resolution so the eventual error stays informative.

### `resolve_project` — the registry reverse-lookup miss

**`register=True` (a normal resolve):** an unregistered on-disk PRIMARY box is NOT
auto-rediscovered. The registry is the SOLE identity authority; a primary box does not self-describe
on disk (sparse create), so there is nothing on disk to re-import from.
`project_name` stays empty and the code falls through to the not-found/create path (a fresh name is
assigned when `initialize`; otherwise the miss surfaces downstream). The future `system recover` is
the remedy for a lost registry entry.

**`register=False` (a deferred-registration create/recovery resolve):** the box's name is read from
the pending CREATE JOURNAL entry whose recorded workspace == this workspace (identity does
not self-describe in on-disk meta). Re-discovering a half-built box during a re-create must NOT
prematurely register it (the caller completes seed → register → clear-entry), so the resolved name
re-associates the on-disk dir directly (the registry is still empty, so `_resolve_local_dir` would
miss again).

### `resolve_project` — the registration-layer reverse-lookup

`_resolve_local_dir` reverse-looks-up the PRIMARY membership itself (resolved-path aware), so
this arm is a belt-and-suspenders repeat: if the name is still unresolved, consult the
PRIMARY-workset `boxes:` membership — the SAME registry `register_workset_box`'s uniqueness guard
(Guard 1) writes and `list`/`box_resolve` read. Reusing the existing name/dir here keeps the create
branch from minting a duplicate entry + box dir for a workspace that is ALREADY a member (which
would otherwise let Guard 1 raise mid-create, after `_init_project` already committed the dir, with
no unwind → stranded half-box). The lookup is exception-guarded (a symlink-cycle/permission path
must not crash `resolve_project`) — matching `_same_workspace`'s own guarding. (`register=False`
deferred-create is handled by the journal block above.)

### `resolve_project` — home and vault are not stored paths

[Jei:2026-06-29h]: there is no per-box custom home/vault path. home/vault are SOLELY the
spec-derived location (`{meta.box.home}` + `{workset.vault_<arm>}/{meta.box.name}`); the launch
routes the home/vault binds through those refs. `{meta.box.home}` is the RO DERIVED key, itself
`{meta.box.path}/home` — the bind names the key and does not re-derive it.

A user customizing home/vault sets the `box.bindings.{rw,ro}.{home,vault}` CASCADE override (which
wins naturally), NOT a stored shell path. Sparse create writes no `shell` / `vault_*` fields.

### `resolve_project` — `enable_vault`

An explicit param wins; otherwise the resolved box-scope `box.enable_vault` (absent ⇒ True). Box
identity derives from the registries (`box_resolve`), separately.

⚑ THE WORKSET TIER HERE IS THE PRIMARY WORKSET. The primary workset IS a workset (spec §2c gives it
the same `meta.workset.settings`), so spec §0's containment rule makes a `box.*` key stored there an
OVERRIDABLE downward default for every box it contains — the box tier still wins (`… < workset <
box`). What the create branch PERSISTS is the BOX-AUTHORED value, never the resolved one: see the
NAMED resolver for the full reasoning.

⚑⚑ **IT IS `resolve_box_enable_vault`.** The value comes from the real cascade —
`base < system < workset < box` — not from hand-opened files, so a
`kanibako system set box.enable_vault=false` reaches every box.

### `resolve_project` — the create branch

New project: SELECT a name first (no store write there), then create `boxes/{name}/` and register
the PRIMARY membership below. The primary per-workset `boxes:` membership is the SOLE store of
primary box names.

An explicit override (e.g. `kanibako create --name X`) registers strictly; collisions error rather
than auto-suffix — so for a normal (`register=True`) create the override is validated against the
PRIMARY-box name domain (primary boxes only, case-blind) UP FRONT, before the dir is materialized. A
deferred (`register=False`) create leaves that domain check to the post-seed commit
(`_register_new_box`).

Whichever branch, the name is only SELECTED there (directory-aware, so a half-built box's dir
keeps its name reserved); the membership write happens once — eager for `register=True`, deferred to
the caller for `register=False` (invariant "registered ⟹ fully seeded").

The `elif project_name:` arm means the reverse-lookup above matched this workspace to an
ALREADY-registered box name — its dir is just missing here, so reuse the name and (re)register the
membership below (idempotent — same name → same path is a no-op overwrite).

**`_dir_existed` — creation ownership for the unwind.** It captures whether the box dir ALREADY
existed at its FINAL (post-`name_override` reassignment) path, BEFORE `_init_project` merges into it
(`mkdir(exist_ok=True)`). A `--name X` pointing at a pre-existing orphan `std.boxes/X` must NOT be
`rmtree`d on a Guard-1 raise — that would delete a pre-existing box's `home/` (credentials, session
state). The unwind only removes a dir THIS call created.

**Sparse create:** NO `project:`/`resolved:` identity is written — the box's identity
+ workspace live in the PRIMARY per-workset `boxes:` membership (`box_resolve` reads it). Only a
NON-default BOX-AUTHORED `box.enable_vault` is persisted, sparsely (above).

**Registering the PRIMARY membership** (name → external workspace) is the SOLE store. The PRIMARY
workset is NON-EXCEPTIONAL (D0/D1): its registry is anchored by `std.primary_workset`. Idempotent —
`register_workset_box` overwrites a moved box's path. `register=False` DEFERS this write to the
caller's post-seed commit (`_register_new_box` → `register_primary_box_name_if_absent`).

**Belt-and-suspenders unwind:** Guard 2 above normally pre-empts Guard 1 (the workspace-path
uniqueness check in `register_workset_box`) by reusing the existing name. If Guard 1 STILL refuses —
e.g. an explicit `--name` claims a workspace already a member under a different name —
`resolve_project` has no outer unwind, so roll back the box dir (ONLY if THIS call created it —
never delete a pre-existing orphan's `home/`) so a genuine invariant violation fails CLEAN (no
stranded half-box), then re-raise. The membership write is the sole store, so a Guard-1 raise leaves
nothing else to unwind.

**No box.yaml backfill:** a primary box's identity and workspace live in the registries (not a
self-describing `project:`/`resolved:` on disk), and `box.enable_vault` is stored only when
authored, so a box dir lacking a `box.yaml` is not a defect to repair on the recovery path.

```python
def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]
```
Find the boxes directory for a default-mode project.

Reverse-looks-up the project name in the PRIMARY per-workset `boxes:` membership (the sole store)
and returns `(project_name, std.boxes/{name}/)`. The
membership reverse-lookup is resolved-path aware (via `primary_box_name_for_workspace`), so a
symlink/normalization alias of the stored workspace still matches.

Returns `("", empty_path)` when no name is registered — the caller (`resolve_project`) will assign a
name during initialization. The lookup is exception-guarded (an unresolvable registry/path must not
crash `resolve_project`) — matching the registration-layer Guard 2.

```python
def _primary_box_paths(
    std: StandardPaths, metadata_path: Path, box_name: str,
) -> tuple[Path, Path | None, Path | None]
```
Fixed PRIMARY-mode `(shell, vault_ro, vault_rw)` (no layout axis). A NULL vault arm yields `None`
for the per-box leaf too; none is invented.

Shell lives under the per-box metadata dir (`boxes/<name>/home`); the vault lives under the PRIMARY
workset (`{config.primary_workset}/vault/{ro,rw}/<name>` by default), NOT inside the user's
workspace: the PRIMARY workset owns boxes/vault/logs just like a named workset. ⚑ This function
composes only the per-box NAME LEAF — the two arms arrive already resolved on
`std.primary_vault_{ro,rw}`, so a `workset.vault_ro` repoint in the PRIMARY workset's `workset.yaml`
reaches here for free.

```python
def _workset_box_paths(
    metadata_path: Path, vault_ro_base: Path | None, vault_rw_base: Path | None, box_name: str,
) -> tuple[Path, Path | None, Path | None]
```
Fixed NAMED-mode `(shell, vault_ro, vault_rw)` (no layout axis). A `None` base (a null arm) yields
`None` for that arm.

Shell under the per-project box dir; vault under the workset's two RESOLVED vault arms
(`<vault_ro_base>/<box_name>`, `<vault_rw_base>/<box_name>`). ⚑⚑ **ONE PARAMETER PER ARM, and it
must stay that way.** A single base with `ro`/`rw` composed onto it would spell `workset.vault_ro`
and `workset.vault_rw` as one shared parent, which they are not: each is an independently
repointable key, so one base cannot answer both. The ro/rw split nests ABOVE the box name to match
PRIMARY and STANDALONE; only the `{meta.box.name}` leaf is composed here, and that leaf is the whole
per-mode variation (§2c).

```python
def _standalone_box_paths(root: Path, *, early: EarlyScope) -> tuple[Path, Path | None, Path | None]
```
Fixed STANDALONE-mode `(home, vault_ro, vault_rw)` (no layout axis).

All host state lives inside the project *root* BY DEFAULT: the agent home is `<root>/box_data/home`
(the `box_data/` marker dir also holds the `<box>.jsonl` helper log), and the vault defaults to
`<root>/vault/{ro,rw}` (per the §2c STANDALONE table). The workset-tier `workset.yaml` lives at
`<root>/workset.yaml` (the root, NOT `box_data/`) and the workspace is the `<root>/workspace`
subdir — both handled by the callers, not here.

⚑ STANDALONE roots a DEGENERATE WORKSET at *root*, so that same `<root>/workset.yaml` is the
workset tier, and its `workset.{vault_ro,vault_rw}` are resolved here through
`project.workset.resolve_workset_vault_pair` (one read, both arms). The two keys carry NO
standalone carve-out — they are declared once for every mode (§2c ALL PROJECTS, R-29); only the
BIND differs, and a lone box takes the arm itself with no name leaf. ⚑ This is the one place where
"standalone paths derive from the CURRENT root, never stored absolutes" admits an exception the
USER authored: a repointed arm may be an absolute path outside the root, which makes that tree no
longer drop-in portable. That is the user's own choice, expressed through a declared key.

```python
def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None
```
Per-box, per-mode HOST path for the helper message log; `None` when the box's `workset.logs` is a
present `<None>` (no logs dir, so no log and no `helpers.jsonl` bind).

The log is the host source of the read-only `helpers.jsonl` bind into the box; it lives inside the
box's own workset/box tree:

* PRIMARY → the RESOLVED `workset.logs` of the primary root (`std.primary_logs`)
* NAMED → the RESOLVED `workset.logs` of `proj.group.root`
* STANDALONE → the RESOLVED `workset.logs` of the project root (default `{workset.boxes}`, i.e.
  `box_data/`), read from the root `workset.yaml`

⚑⚑ **This function is the hub's WRITER, and the MOUNT it must agree with is the spec's own
spelling** `{workset.logs}/{meta.box.name}.jsonl` (`data/rom/settings/core-defaults.yaml`, the `helpers` table).
Every arm RESOLVES `workset.logs`, in ALL THREE modes: a composed directory would disagree with the
mount the instant a user repointed `workset.logs`, and the box would read an empty file forever. In
the standalone arm the declared default is `{workset.boxes}` (no name leaf), a ref the pre-snapshot
resolver must answer before the launch snapshot exists. `resolve_workset_logs(..., standalone=True)`
takes that default, and `settings/workset_dirkeys.resolve_workset_dir_key` resolves a ref to another
workset early key itself, with cycle protection. A ref needing the construct-time `{meta.box.name}`
is refused.

🛑 A repointed standalone `workset.logs` puts the log OUTSIDE the `box_data` directory, which
`box rm --purge` removes wholesale. So that teardown
(`commands/box/_parser.py::_teardown_standalone_box`) first deletes the box's log files BY NAME
through `remove_box_logs`, as `box purge` does; the directory the user nominated is left standing.

The caller is responsible for guarantee-creating the parent dir before the bind (L7). The box-side
dest is the PINNED `~/.kanibako/state/helpers.jsonl` (declared in `core-defaults.yaml`), NOT a
`{$XDG_STATE_HOME}` expression; see
`kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH` for why, and
`box_supervisor.project_pinned_xdg` for the post-boot XDG projection.

The standalone log defaults inside the `box_data/` marker dir (settings itself lives at the root, so
the default resolves to `metadata_path/box_data` rather than `metadata_path`) so the whole
standalone tree is drop-in portable unless the user repoints the key. For NAMED, the workset root
is carried on the project
group (`root=ws.root`); the `metadata_path.parent.parent` fallback beside it assumes the
DEFAULT box layout and is unreachable from `resolve_workset_project`, which always supplies the
group.

```python
def creds_watcher_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None
```
Per-box HOST log of the detached creds watcher: `<resolved workset.logs>/<box>.creds-watcher.log`
(suffix `bootstrap.CREDS_WATCHER_LOG_SUFFIX`), beside the helper log and resolved per mode the same
way. `commands.start._spawn_creds_watcher` appends the watcher's stderr to it, so it is where the
watcher's WARNING and ERROR records reach the user. Nothing is bound into the box from it.
`None` under a `<None>` `workset.logs`; the watcher's stderr then goes to `DEVNULL`.

```python
class BoxLogFiles(NamedTuple)          # helper: Path, creds_watcher: Path
def box_log_files(logs_dir: Path, box: str) -> BoxLogFiles
def remove_box_logs(logs_dir: Path | None, box: str, *, keep: Iterable[Path] = ()) -> list[Path]
def box_logs_location(std: StandardPaths, proj: ProjectPaths) -> tuple[Path | None, str]
def standalone_logs_dir(root: Path, *, early: EarlyScope) -> Path | None
```
The `None` logs dir is a present-`<None>` `workset.logs`: it holds no logs, so
`remove_box_logs(None, …)` returns `[]` and every delete path needs no guard of its own.

`box_log_files` is THE one place a box's files under `workset.logs` are named; both path functions
above derive from it, and `remove_box_logs` deletes whatever it lists (returning the files that
existed), sparing any file in *keep*. Every path that deletes a box's logs calls `remove_box_logs` —
`box purge` and `box purge --all` (`commands/clean.py`); both teardowns behind `box rm --purge` and
the deregistered-box purge, primary and standalone (`commands/box/_parser.py`); and `workset
disconnect --remove-files` (`commands/workset_cmd.py`, NOT `project.workset.remove_project`, which
also releases a box the lifecycle engine is MOVING) — so a new per-box log file is added to
`box_log_files` and nowhere else. `standalone_logs_dir(root)` is the standalone arm of
`box_logs_location`, for callers holding only the root. `box_logs_location` resolves `(logs dir, box
name)` for a `ProjectPaths` in any mode; the removal paths that hold no `ProjectPaths` pass the dir
and name they already resolved.

```python
SHELL_D_CONTENTS: str  # settings.messages, built from messages._SHELL_D_SOURCE_LINE
```
`~/.shell.d/*.sh` is a user/template extension point for customizing a box's INTERACTIVE shell. A
box user (or a seed/template) drops `*.sh` scripts into `~/.shell.d/` and kanibako guarantees
`.bashrc` sources them on every interactive shell startup — see README "Init scripts". This is an
intentional seam with no first-party producers by design: kanibako seeds the source line but never
writes the scripts themselves.

⚑ Scope note: the source line runs ONLY for an interactive `.bashrc` (a human attaching to the
box); it does NOT reach the agent (exec'd directly), the agent's `bash -c` tool calls
(non-interactive), or the launch env. To deliver env to the AGENT, use `env.<VAR>` / `secret_path`
(§2a/§2d) instead.

```python
def _bootstrap_shell(shell_path: Path) -> None
```
Write minimal shell skeleton files into a new shell directory: a `.bashrc` (sourcing `/etc/bashrc`,
setting `PS1`, and the `SHELL_D_CONTENTS` source line), a `.profile` that sources `.bashrc`, and the
`.shell.d/` drop-in directory. Each file is written only when absent.

```python
def _upgrade_shell(shell_path: Path) -> None
```
Keep the `.shell.d` sourcing seam current on an existing shell directory.

Ensures the user/template extension point stays wired on every launch, including shells created
without it. Idempotent — safe to call every launch. Creates `.shell.d/` if missing and
appends the source line to `.bashrc` if absent. No-op if *shell_path* does not exist yet.

```python
def _init_common(
    std: StandardPaths, metadata_path: Path, shell_path: Path,
    vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path, *,
    enable_vault: bool = True, vault_root: Path,
) -> None
```
Shared first-time project setup: create directories, bootstrap shell.

Called by both `_init_project` (default) and `_init_standalone_project`. It performs every step
common to both modes: print message, create metadata and shell dirs, bootstrap the shell, and set up
vault directories when enabled. The shell dir is the one mounted as `/home/agent`; the `.gitignore`
in `vault/` that excludes `rw` from version control is `write_vault_gitignore`'s, below, and
`vault_root` is REQUIRED because that function composes the skeleton off it.

Credential copy is handled separately by `target.init_home()` in `start.py`, after template
application.

```python
def write_vault_gitignore(vault_root: Path, vault_rw_path: Path) -> None
```
Write the vault skeleton's `.gitignore` while `workset.vault_rw` makes its claim true.

### ⚑⚑ The `.gitignore`'s directory is COMPOSED off `vault_root`, never POSITIONED off an arm

`vault_root` is the workset root that owns the `vault/` SKELETON dir — `std.primary_workset` for
PRIMARY, the standalone ROOT for STANDALONE — and the skeleton is `<vault_root>/vault`, the one
non-key leaf a workset root carries (`project/workset.py::_VAULT_LEAF`), the same file
`standalone_vault_teardown` clears.

⚑ It is NOT `vault_ro_path.parent`: `workset.vault_ro` is repointable, so that parent is the
skeleton only until the key moves. Three shapes show why:

* `vault_ro: {meta.workset.path}/store/ro` would name `<root>/store` — a directory no key gave us,
  INSIDE the root, so a containment guard would admit it and the `.gitignore` would land there
  while the real skeleton got none.
* In PRIMARY the arm carries a `/{meta.box.name}` leaf, so the parent would be the `ro` ARM ITSELF,
  where an `rw/` pattern matches nothing.
* `vault_ro: ~/store` would make it `$HOME`, which the composition makes unreachable.

⚑ The guard compares the RESOLVED `workset.vault_rw` against the composed skeleton, because the
file's whole content is a CLAIM that `rw/` is a child of it: a repoint out of the skeleton makes the
claim false, so nothing is written. STRICT — an arm pointed AT the skeleton is the user's rw store,
not its parent.

### ⚑⚑ ONE CARRIER FOR THREE WRITE SITES

`_init_common` here, `_lifecycle._to_standalone` and `_duplicate._duplicate_to_standalone` all call
`write_vault_gitignore`. Writing on `vault_dir.is_dir()` alone would be a POSITION answering a KEY.
The skeleton is the `ro` arm's
DEFAULT parent, so it sits on disk for every un-repointed box while `vault_rw` — an independent key —
may be anywhere; an existing skeleton is no evidence about where `rw` is (P10).

⚑ The skeleton must already be on disk. An absent one means no vault was laid at this root — a
`duplicate` never carries one — and inventing an empty `vault/` to hold the file would make this a
creator of the thing it only annotates. That existence check is a precondition for writing into a
directory, not a derivation of the root.

```python
def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None
```
Find the deepest registered default-mode project that is an ancestor of *target*.

Reads the PRIMARY per-workset `boxes:` membership (the sole store) and, for each entry whose
registered workspace path is a prefix of *target*, checks that `std.boxes/{name}/` actually exists
on disk. Among all valid matches, the deepest (most path components) wins. Returns the matched path
or `None`.

```python
def _is_standalone_meta_dir(root: Path, *, strict: bool = False) -> bool
```
True only if *root* carries the standalone box MARKER (presence-based). *strict* refuses a malformed
`workset.yaml` instead of reading it as no marker; `detect_project_mode`'s step 2 passes it.

The marker is a STORED `workset.registry: null` in the root `workset.yaml` AT *root*
(`<root>/workset.yaml`, NOT inside the box store), PRESENT. The FILE (carrying that null)
is the standalone self-declaration (design D4). The settings file OVERRIDES any workset tree
determination (D3-mode 3); reading the stored null keeps an unrelated `box_data/` from being
mistaken for a marker. Delegates to `box_resolve.stores_standalone_registry_null` (the single
definition of the presence check).

```python
def detect_project_mode(
    project_dir: Path, std: StandardPaths, config: BootstrapConfig,
) -> DetectionResult
```
Infer which project mode applies to *project_dir*.

⚑ `box_resolve.detect_box_mode` is the one carrier of the mode order (D3-mode); this treewalk is its
step 4, after the two registry checks and the in-place marker. Callers detect through it, not here.

Walks ancestor directories (up to `$HOME` or filesystem root) looking for project markers. Returns a
`DetectionResult` with the detected mode and the ancestor directory where the marker was found.

Detection order:

1. **Connected-external** — *project_dir* (or an ancestor) is an external directory bound to a
   workset by a live `boxes:` connection record. Runs FIRST so a force-connected box (which keeps
   its on-disk marker) resolves as its workset's `named` box; were step 2 first, it would resolve
   standalone.
2. **In-place standalone marker** — a root `workset.yaml` storing `registry: null` AT *project_dir*
   declares it standalone (D3-mode 3); OVERRIDES workset tree membership. Vetted, never
   registered (`import_reconcile.check_standalone`).
3. **Workset** — *project_dir* lives inside a registered workset root (`workspaces/` subdirectory
   first, then the root itself).
4. **Default (name-based)** — one-pass scan of the PRIMARY per-workset `boxes:` membership
   (`_find_local_ancestor` → `load_primary_boxes`); deepest registered path that is an ancestor of
   *project_dir* wins. Requires `boxes/{name}/` to exist on disk.
5. **Walk ancestors for on-disk markers** — at each level, a standalone marker (vetted, never
   registered), then a workset skeleton (an unregistered one is IMPORTED — see
   `kanibako.project.import_reconcile`).
6. **Default** — `primary` mode at the original *project_dir*.

### Why step 1 must run before step 2

The connected-external check MUST run BEFORE the standalone-marker check to preserve the
single-registry invariant. A FORCE-CONNECTED standalone box (absorbed via
`workset.add_project(force=True)`) deliberately KEEPS its on-disk root `workset.yaml`
marker while its global `standalone:` registry entry is REMOVED and a per-workset `boxes:`
connection entry is written — the box lives in EXACTLY ONE registry (no dual registration). Such a
box is EXTERNAL (outside the workset tree), so the workset-tree check (step 3) does NOT match it;
the live connection is what claims it as its named workset box. Were the marker check to run first,
the box would resolve standalone — the very dual identity that `--force` removes. D10: the
per-workset registries collectively form the reverse index, scanned by `box_resolve`; there is no
global `connected:` index.

### Step 2 — marker-first precedence

A box's own in-place settings file is its standalone self-declaration and OVERRIDES any workset
TREE determination — a workset (even one whose tree physically CONTAINS this box) must NOT be able to
"steal" a box that declares itself standalone. (A LIVE connection is the one exception, resolved by
step 1.) `box_resolve.detect_box_mode` checks the same marker as its D3-mode 3, after its two
registry checks and before it calls this treewalk. Both key on one signal (`workset.yaml` storing
`registry: null`, via `_is_standalone_meta_dir` → `box_resolve.stores_standalone_registry_null`);
workset/primary boxes don't store null. Only the resolved dir itself is inspected here; an ancestor
marker is the step-5 walk's. `import_reconcile.check_standalone` then vets the root but never
registers it. A malformed marker file is refused only after steps 3-4 find no registry claim on the
dir.

### Step 5 — the ancestor walk

At each level, first `project.workset.refuse_retired_workset_identity` refuses a v1.6/v1.7 root,
which would otherwise import under its leaf name with its retired identity unread. Then:

* **STANDALONE first** — the root `workset.yaml`'s own stored `registry: null`
  (`_is_standalone_meta_dir`; a bare `box_data/` is not the marker). `import_reconcile.check_standalone`
  vets it; a standalone box is never imported here.
* **NAMED** — the whole workset skeleton (`project.workset.is_workset_skeleton`). An unregistered root
  is imported (`import_reconcile.import_named_workset`, named for its leaf), then the standard
  workset check resolves it.

The walk stops at `$HOME` or the filesystem root.

```python
def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None
```
Check whether *resolved_dir* is inside a registered workset.

Returns a `DetectionResult` if found, `None` otherwise. Checks `workspaces/` first (specific
project), then the workset root itself (inside workset but not necessarily a project workspace). The
`workspaces` location is the RESOLVED `workset.workspaces` (repoint honored; default
`{meta.workset.path}/workspaces` — §3.3: real and USED), through
`project.workset.resolve_workspaces_locator`, so a null still finds members connected before it.

```python
def _workset_box_name_for_workspace(
    ws_root: Path, workspace: str, *, early: EarlyScope,
) -> str | None
```
Reverse-look-up *workspace* in *ws_root*'s per-workset `boxes:` membership.

Returns the registered box name (resolved-path aware) or `None`. Mirrors
`_register_workset_box_membership`'s registry-path resolution so both the write (Guard 1) and this
reverse-lookup (Guard 2) consult the SAME per-workset `boxes:` registry — the one `list` /
`box_resolve` read — so a global-name miss cannot let Guard 1 fire mid-create.

```python
def _workset_box_workspace_for_name(
    ws_root: Path, box_name: str, *, early: EarlyScope,
) -> str | None
```
Forward-look-up *box_name* in *ws_root*'s per-workset `boxes:` membership.

Returns the REGISTERED workspace path (the `boxes:` entry VALUE) or `None`. The forward twin of
`_workset_box_name_for_workspace`, with the same registry-path resolution. The registered path is
authoritative (D1b/D3-auth) *wherever a composition epoch put it* — a member registered before a
`workset.workspaces` repoint keeps resolving to its recorded workspace, never re-derived from the
CURRENT composition.

```python
def _register_workset_box_membership(
    ws_root: Path, box_name: str, workspace: Path, *, early: EarlyScope,
) -> None
```
Register *box_name* → *workspace* in *ws_root*'s per-workset registry.

The dual-register helper (D1/D3-auth): resolves the workset's `workset.registry` path (honoring
a repoint via its `workset.yaml`) and records the box's membership. Idempotent —
`register_workset_box` overwrites a moved box's stored path. Used for both NAMED worksets (`ws_root`
= the workset root) and the PRIMARY workset (`ws_root` = `std.primary_workset` — NON-EXCEPTIONAL per
D0/D1).

```python
def _unregister_workset_box_membership(ws_root: Path, box_name: str, *, early: EarlyScope) -> None
```
Drop *box_name* from *ws_root*'s per-workset registry (compensating action).

The inverse of `_register_workset_box_membership`: resolves the workset's `workset.registry` path
(honoring a repoint via its `workset.yaml`) and removes the box's `boxes:` membership. Idempotent —
`unregister_workset_box` is a no-op when the file/entry is absent. Used to drop a disconnected
external box's D10 connection record.

```python
def _workset_box_membership_undo(ws_root: Path, box_name: str, workspace: Path, *, early: EarlyScope) -> Callable[[], None]
```
The undo of a membership register, armed BEFORE the write so an interrupt just after it is still
undone. It snapshots the box's row at call time. The undo puts a prior row back as it was (the
register may have overwritten it: re-registration is idempotent, a move overwrites); with no prior
row it drops only a row that still holds *workspace*, so it never removes a row this call did not
write. `add_project` uses it.

## The PRIMARY-box name registry

The primary per-workset `boxes:` membership is the SOLE store of default-mode (PRIMARY) box names.
Membership is name → EXTERNAL-workspace path in the primary workset's registry file, the resolved
`workset.registry` (`project.workset_registry.resolve_workset_registry_path`; spec §2c).

The name domain is PRIMARY BOXES ONLY: worksets are not consulted, so a box and a workset may share
a name. Names compare CASE-BLIND (spec §0, NAMING RULES; `find_identifier`). Every function takes
the primary workset root explicitly and an `early: EarlyScope`, which the registry path's
resolution needs.

```python
def load_primary_boxes(primary_workset: Path, *, early: EarlyScope) -> dict[str, str]
```
Return the PRIMARY box membership as `{box_name: workspace_path_str}`. *primary_workset* is
`std.primary_workset`.

```python
def primary_box_name_for_workspace(primary_workset: Path, workspace: str, *, early: EarlyScope) -> str | None
```
Return the PRIMARY box name registered for *workspace*, or `None`. Resolved-path aware (via
`_workset_box_name_for_workspace`).

```python
def check_primary_box_name_free(primary_workset: Path, name: str, workspace: str, *, early: EarlyScope) -> None
```
Raise `ProjectError` if *name* is already a PRIMARY box's (case-blind), or if *workspace* is the
user's `$HOME`. Writes nothing. Used at the `--name` registration edge so a collision fails BEFORE
the box dir is materialized. Worksets are not consulted, and there is no override.

```python
def check_workspace_not_named_box(std: StandardPaths, workspace: str) -> None
```
Raise `ProjectError` when a NAMED box already holds *workspace* (no write), read through
`box_resolve.find_connected_external_box`, the one resolver that consults the per-workset `boxes:`
membership.

```python
def refuse_derived_box_name(base: str) -> str
```
The box name derived from directory name *base* (spec §0): `identifiers.derived_name` writes `_`
for each `-` and `.`; the directory keeps its spelling. Raises `DerivedBoxNameError` naming *base*
when the derived name fails the box-name rule (`launch.box_identity.box_name_reason`).

```python
def pick_primary_box_name(primary_workset: Path, workspace: str, boxes_dir: Path | None = None, *, early: EarlyScope, journal: Path | None = None) -> str
```
Pick a PRIMARY box name from *workspace*'s basename (no write): the base is
`refuse_derived_box_name(<basename> or "project")`, and `identifiers.first_free_name` appends a
number (`name`, `name2`, ...) while a candidate is taken. A candidate is taken when it names a
PRIMARY box (case-blind); or, with *boxes_dir*, when `boxes_dir/<candidate>` exists (a PATH probe,
never folded); or, with *journal* and *boxes_dir*, when the lifecycle journal holds a pending entry
for `boxes_dir/<candidate>` — an interrupted create may have claimed it before making its dir.

```python
def register_primary_box_name(primary_workset: Path, name: str, workspace: Path | str, *, early: EarlyScope) -> None
```
Register *name* → *workspace* in the PRIMARY membership: `check_primary_box_name_free`, then the
write (`_register_workset_box_membership`, which also enforces the one-box-per-workspace-path
invariant).

```python
def register_primary_box_name_if_absent(primary_workset: Path, name: str, workspace: Path | str, *, early: EarlyScope) -> None
```
Idempotent `register_primary_box_name` for deferred-create recovery: a no-op when *name*
(case-blind) already maps to the SAME *workspace*; any other state goes through
`register_primary_box_name`, which raises on a genuine collision.

```python
def assign_primary_box_name(primary_workset: Path, workspace: Path | str, boxes_dir: Path | None = None, *, early: EarlyScope) -> str
```
Auto-assign and register a PRIMARY box name from *workspace*'s basename: `pick_primary_box_name`,
then `register_primary_box_name`.

```python
def unregister_primary_box_name(primary_workset: Path, name: str, *, early: EarlyScope) -> None
```
Drop *name* from the PRIMARY membership. Thin delegation to `_unregister_workset_box_membership`;
idempotent.

```python
def resolve_workset_project(
    ws: WorksetSpec, project_name: str, std: StandardPaths, config: BootstrapConfig, *,
    initialize: bool = False, enable_vault: bool | None = None,
) -> ProjectPaths
```
Resolve per-project paths for a project inside a NAMED workset.

*ws* is a lightweight `WorksetSpec` describing the workset's name, root, auth mode, and registered
project names. Callers holding a full `Workset` object pass `WorksetSpec.from_workset(ws)`.

No layout axis — shell under the per-project box dir, vault under the workset's two
RESOLVED vault arms (`<ws.vault_ro_dir>/<name>`, `<ws.vault_rw_dir>/<name>`). Paths are name-based,
not hash-based. Raises `WorksetError` if *project_name* is not registered in *ws*.

### Workspace override (D10)

The per-workset registry's `boxes:` membership is the SOLE authoritative name → workspace store
(D1b/D3-auth): when *project_name* is registered, its REGISTERED path IS the workspace — the
EXTERNAL dir for a connected box, `workspaces/<name>` for an in-tree box, and the path of the
composition it was registered under for a member registered before a `workset.workspaces` repoint
(re-deriving that member from the CURRENT composition would strand it). An UNREGISTERED member (an
in-tree connect before its first start, or a fresh `initialize` create) falls back to the composed
default, with the `box_resolve` identity derivation kept for any residual override. Under a null
`workset.workspaces` there is no default to compose, so an unregistered member raises
`WorksetError` (`ERR_WORKSET_NULL_WORKSPACES`); a registered one resolves to its registered path.

### Home, vault, and `enable_vault` in the NAMED resolver

As on the PRIMARY path, there is no per-box custom home/vault path — home/vault are SOLELY the
spec-derived default location,
customized via the `box.bindings` cascade. The workspace override above (an EXTERNAL-connected live
dir) is a SEPARATE concern.

`enable_vault`: explicit param wins; else `box.enable_vault` (absent everywhere ⇒ True)
resolved through the CASCADE by `resolve_box_enable_vault` — `base < system < workset < box`.
⚑ The WORKSET tier is REQUIRED: `workset create --no-vault` writes the key there, where spec §0's
containment rule makes it an OVERRIDABLE default for the workset's boxes (the contained scope still
wins). Without it the flag is a silent no-op for every named box.

⚑⚑ **The whole cascade, not two files**: reading only the box and workset files would drop `base`
and `system` silently, so a `kanibako system set box.enable_vault=false` would persist, echo back,
and change no box.

⚑ **The resolved value is not the written one.** `ProjectPaths.vault_enabled()` resolves through the
cascade and drives the mounts; `authored_vault` reads the box tier ALONE (via
`read_box_enable_vault`, which takes no fallback) and is the only one persisted,
since spec §2b's `box.enable_vault` row keeps the key sparse — persisting an inherited default would
PIN it, turning an
overridable workset default into a box override later workset edits cannot reach.

### Sparse create + dual-register on the NAMED path

Sparse create: NO `project:`/`resolved:` identity — the box's name lives in the
workset's per-workset registry (the `boxes:` entry, which `box_resolve` reads) and its workspace
override in that same registry. Only a NON-default BOX-AUTHORED `box.enable_vault` is persisted.

Dual-register: record membership in the workset's per-workset registry (name → workspace) — the
SOLE on-disk identity record, since sparse create writes no `project:` entry. Sourced from the
resolved *project_path* so an external-connect override seeds the registry with the external dir
(the D10 home for that record). Idempotent — overwrites a moved box's path.

### J2 connect self-heal

Symmetry with the import path: reaching that point means *project_name* IS a registered member of
*ws* (the membership guard raised otherwise), so a lingering `connect` entry for this box is a
register→clear-window stale entry — the box is already registered, so recovery == clear the entry
(NO re-register, NO seed). This keeps "registered ⟹ no pending entry" eventually-on-resolve for
connect, matching `import_reconcile._clear_stale_import`. The key is the host-side box dir
(`Path(shell_path).parent` == `ws.projects_dir/project_name` == the connect-entry key). Guarded +
minimal: it only fires when `std.journal` exists AND a register-only (import/connect) entry is
actually pending for this exact key — so the normal workset-resolve hot path and J1's create entry
are untouched.

```python
def _init_workset_project(
    std: StandardPaths, metadata_path: Path, shell_path: Path,
) -> None
```
First-time workset project setup: bootstrap shell directory.

Does not create vault `.gitignore` files (vault lives under the workset root, not inside a user git
repo). The shell dir is the one mounted as `/home/agent`. Credential copy is handled separately by
`target.init_home()` in `start.py`, after template application.

```python
def iter_projects(
    std: StandardPaths, config: BootstrapConfig, *, looped: list[tuple[Path, Path | None]] | None = None,
) -> list[tuple[Path, Path | None]]
```
Return `(metadata_path, project_path | None)` for every known project. A caller that passes
*looped* gets each box dir that is a symlink loop appended there, in the same shape, instead of the
refusal.

*project_path* is the box's workspace, sourced from the PRIMARY per-workset registry (`name →
workspace`). A box with no registry entry yields `None` (an un-registered / half-created box has no
resolvable workspace).

Box → workspace comes SOLELY from the PRIMARY per-workset registry, seeded at create
(`_register_new_box`) — the SAME source `box_resolve` reads. A box cannot be resolved from its box
DIR via `box_resolve.resolve_box_identity` (the registry is keyed by workspace PATH, not the box
dir), so the PRIMARY registry is read here directly (identical data). There is no on-disk fallback:
a box absent from the registry has no workspace → `None`. (These are
PRIMARY boxes: `std.boxes` == `{config.primary_workset}/boxes`, so the PRIMARY registry is the home.)

```python
def iter_workset_projects(
    std: StandardPaths, config: BootstrapConfig,
) -> _WorksetProjectRows
```
`_WorksetProjectRows` is `list[tuple[str, _WorksetLike, list[tuple[str, str]]]]`.
Return workset project info for all registered worksets.

Each entry is `(workset_name, workset, [(project_name, status), ...])`. The workset object is a
concrete `kanibako.project.workset.Workset` typed structurally as `_WorksetLike` (so `paths.py` need
not import `workset`). Status is `"ok"`, `"missing"` (no workspace), or `"no-data"` (no project
dir).

The per-workset `boxes:` membership is loaded ONCE per workset — the authoritative name → workspace
store. Workspace presence is checked at the REGISTERED path when the member is registered: a member
registered under an earlier composition (before a `workset.workspaces` repoint) would otherwise
read as "missing" and vanish from `list`. An unregistered member falls back to the composed
location.

```python
def _find_workset_for_path(
    project_dir: Path, std: StandardPaths,
) -> tuple[_WorksetLike, str | None]
```
Return `(workset, project_name)` for a path inside a workset.

The returned object is a concrete `kanibako.project.workset.Workset` (typed structurally as
`_WorksetLike` to avoid importing `workset` into `paths.py`); callers that need a `WorksetSpec` for
`resolve_workset_project` wrap it via `WorksetSpec.from_workset`.

*project_dir* may be the workspace root, a subdirectory within it, or anywhere inside the workset
root. When *project_dir* is inside `workspaces/{name}/`, the project name is returned. When inside
the workset root but not in a specific workspace, `None` is returned as the project name. The
`workspaces` location is the RESOLVED `workset.workspaces` (repoint honored; default
`{meta.workset.path}/workspaces` — §3.3: real and USED), through
`project.workset.resolve_workspaces_locator`, so a null still finds members connected before it.

Raises `WorksetError` if *project_dir* does not belong to any registered workset.

```python
def _resolve_workset_or_connected(
    project_dir: Path, std: StandardPaths,
) -> tuple[_WorksetLike, str | None]
```
Resolve *project_dir* to its owning workset, honoring external connects.

Tries the in-tree lookup first (`_find_workset_for_path`). When that misses (raises `WorksetError`)
or lands on the workset root without a specific project (`proj_name is None`), falls back to the
connected-external redirect index (D10 enumerate-and-scan over the per-workset registries) so that a
path living *outside* any workset tree — e.g. an externally connected workspace — still resolves to
its `(workset, project_name)`. The lazy `box_resolve` import avoids a `paths` ↔ `box_resolve` import
cycle.

Raises `WorksetError` if neither lookup resolves a workset at all. When a workset is found but no
specific project is (`proj_name is None`), that is returned to the caller rather than raised —
callers preserve their own distinct "inside workset but not in a project" error.

```python
def resolve_any_project(
    std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None, *,
    initialize: bool = False, register: bool = True, name_override: str | None = None,
) -> ProjectPaths
```
Auto-detect project mode and resolve paths accordingly.

Detects the mode with `box_resolve.detect_box_or_default` — `detect_box_mode`'s D3-mode order, with
"not a box" read as `primary` AT the dir — and hands the detected *project_root* (not the raw CWD)
to that mode's resolver (`_resolve_designated_path`).

*register* is forwarded to the PRIMARY/STANDALONE resolvers so the `start` auto-create path can
defer registration until after the home seed (the NAMED branch never writes the name registry on
create, so the flag is a no-op there). Defaults True.

*name_override* is forwarded to the PRIMARY resolver only, which is the sole mode whose box name is
not derivable from the tree itself: a STANDALONE box carries its identity in its own root
`workset.yaml`, and a NAMED box takes its name from its workspace directory. `box extract --name`
is the caller that needs it (re-materializing an archived box under a chosen name).

### The CLI front door

The designation goes through `resolve_designation` before `Path(raw).resolve()` path-ifies it, so
detection sees a registered name's workspace. *initialize* is its `unknown_name_is_path`.

**On an unknown name with no such path:** on the READ path (`initialize` False) refuse rather than
path-ify — a nonexistent cwd-relative path resolves to an UNREGISTERED box with an empty name,
minting a phantom `kanibako-<hash>` container that no `list`/`ps` row corresponds to. The CREATE path
(`initialize=True`) still path-ifies so a new box can be materialized at the resolved location.

**On a bare workset name:** a workset is not a single box, so it is rejected with an actionable
message rather than the generic "inside a workset, cd to a project" error.

```python
def designation_route(value: str | None, *, name_first: bool = False) -> DesignationRoute
def resolve_designation(std: StandardPaths, value: str | None, *, unknown_name_is_path: bool,
                        name_first: bool = False) -> str
```
The one place that decides how a box designation (system-design § Detection & import, "Box
designation & workset path space") is resolved, and the one front door every path resolver
(`resolve_project`, `resolve_any_project`, `resolve_box_target`, `resolve_lifecycle_target`) and the
`start` no-box message share. The kind comes from `box_identity.classify_designation`: an
IDENTIFIER (a valid box name) is ambiguous between a name and a relative path; anything else that
can be a path is a PATH and is never looked up as a name, so `.hidden` or `foo.` resolve as paths. An
IDENTIFIER takes the NAME route when *name_first* or when no such path exists. A PATH of the form
`<workset>/<box>`, both segments IDENTIFIERs, that does not exist takes the QUALIFIED route; a
qualified miss falls through to the path unchanged. `box rm` and `box register` consult the
registry's name sections only on the NAME route (`name_first=True`). An INVALID designation (one containing NUL) raises `ProjectError`.

```python
def resolve_box_target(
    std: StandardPaths, config: BootstrapConfig, value: str | None = None, *,
    initialize: bool = False, register: bool = True, warn: bool = True,
) -> ProjectPaths
```
Resolve a `--box` value (a box NAME or a path) to its `ProjectPaths`.

The single path-or-name resolver behind the `--box` selector and the
`start`/`shell`/`refresh`/`workset disconnect` targeting. Returns the SAME `ProjectPaths`
the positional-`project` path returns, so callers swap cleanly.

*value* is EITHER a box NAME or a filesystem path. **Box NAME takes precedence in ambiguous cases**
(`resolve_designation(..., name_first=True)`): an IDENTIFIER that is both a registered name and a
relative directory in cwd resolves to the NAME. Resolution order:

1. **NAME first.** An IDENTIFIER designation is tried as a name:
   * a **standalone box name** in `registry.standalone` (the canonical-id domain — the case
     `resolve_any_project` does NOT cover, since `resolve_name` only indexes the
     projects/worksets sections). Box names are lowercase (R2), so the query is case-folded for the
     lookup;
   * else the registry projects/worksets names + qualified `ws/project` names, which
     `resolve_any_project` already resolves.
2. **PATH otherwise.** A PATH designation, or an identifier no name matched, is resolved as a
   filesystem path, through the same `_resolve_designated_path` as `resolve_any_project`
   (`box_resolve.detect_box_or_default`). No detection is reimplemented here.

A pre-existing box whose name fails the box-name rule (`launch.box_identity.box_name_reason`) is
not addressable by that name — the name is a PATH designation — but resolves by its path and is
FLAGGED (`_flag_nonconforming`); this resolver does not reject on name shape.

`None` / empty *value* resolves the cwd box, matching the positional-`project` default.

*register* is forwarded to the PRIMARY/STANDALONE resolvers; `start` passes `register=False` so
an auto-created box defers registration until after its home seed (journal entry → seed → register →
clear-entry). Defaults True.

*warn* gates the non-conforming-name FLAG (`_flag_nonconforming`). A NON-materializing PROBE
(`initialize=False`) run purely to read a box's paths ahead of a second materializing resolve passes
`warn=False` so the name flag fires exactly ONCE (on the real resolve), never doubled. Defaults True.

```python
def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths
```
Warn (do NOT reject) when a resolved box's name fails the box-name rule
(`launch.box_identity.box_name_reason`), through `WARN_WS_BOX_BAD_NAME`.

A box whose name fails the rule still resolves (the canonical-id/registry matchers are structural,
not policy-gated); `validate_box_name` enforces the rule on NEW names only, so a non-conforming name
already on disk is FLAGGED on use and the drift is visible. Returns *proj* unchanged.

```python
def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths
```
Advisory (never fatal): flag a standalone box whose stored `workset.kuid` is a NON-sentinel value
that fails the kuid parity/charset check.

Fires ONLY when ALL hold (spec D9):

* the box is STANDALONE (only standalone stores a real kuid);
* `workset.kuid` is NON-sentinel (`!= kuid.SENTINEL`) — the sentinel `"00000"` is the DEFAULT,
  EXEMPT even though `kuid.is_valid("00000")` is False by parity (the exemption is EXPLICIT, never
  inside `is_valid`);
* `workset.skip_kuid_check` is OFF (its DEFAULT is TRUE, so the warning is OPT-IN strictness);
* `kuid.is_valid` rejects the stored value.

The kuid is USER-EDITABLE, so a bad value is FLAGGED (`Warning: invalid KUID`), NEVER rejected.
Returns *proj* unchanged.

⚑ `workset.kuid` / `workset.skip_kuid_check` are WORKSET-scope keys, so they are read from the
WORKSET tier of the ONE pair — for standalone that is the ROOT `workset.yaml`, exactly where
`establish_standalone` writes them. Sourced from the pair rather than re-spelled, so this cannot
drift from the writer. The `settings_file is None` guard is UNREACHABLE for standalone (its workset
tier is the ROOT file, always a path); it exists so the reads below are TYPED, not to handle a real
case.

```python
def _flag_missing_vault(proj: ProjectPaths) -> ProjectPaths
```
Advisory (never fatal): warn when a box that EXPECTS a vault has none on disk (spec D5, the
NON-CRITICAL integrity tier).

A vault is OPTIONAL storage, not a launch prerequisite — so a box whose `enable_vault` is on but
whose vault directory is absent still resolves and launches, and the launch creates the directory
empty; the missing vault is merely FLAGGED (`warning: cannot find vault`). A box with
`enable_vault` OFF expects no vault, so nothing is warned (the `enable_vault` guard is
load-bearing: without it every vault-disabled box would warn). Fires at resolve time alongside the
other `_flag` advisories. Returns *proj* unchanged.

```python
def establish_standalone(
    std: StandardPaths, root: Path, *, enable_vault: bool | None, carry_kuid: str | None = None, own_name: str | None = None, register: bool = True,
) -> tuple[str, Path, Path | None, Path | None]
```
Establish a standalone box at *root*: identity + meta + registration.

The single shared core behind all three standalone paths (`create --standalone`, `convert
--standalone`, `duplicate --standalone`). It

1. derives the box identity from its DIRECTORY, never a caller-supplied name (there is no *name*
   parameter): `box_identity.make_standalone_box_name` mints a fresh canonical `<kuid>_<leaf>`
   (whole-name collision regen vs `registry.standalone`); with *carry_kuid* (a MOVING box),
   `box_identity.carry_standalone_name` keeps the kuid and re-derives the leaf from *root*,
   re-minting only when the whole name is taken by ANOTHER box — *own_name*, the box's own
   registered name, is not a collision;
2. writes the SPARSE settings, each key AT ITS OWN SCOPE'S TIER: the workset-scope
   `workset.kuid` into the ROOT `<root>/workset.yaml` (which MATERIALIZES that file — half the
   standalone detection marker, `~/canon/workbook/specs/system-design-1.8.0.md` § "Detection & import"), and a NON-default
   box-scope `box.enable_vault` (only when *enable_vault* is not `None`) into the BOX tier
   `<store>/box.yaml` (`_standalone_settings_files`; `<root>/box_data/` by default) — the SAME file
   `config set box.*` writes, so create and set can never disagree. A default-vault box therefore
   writes NO box-tier file at all, which is the spec's "ABSENT BY DEFAULT" (§2c + §4 STANDALONE
   tree). NO `project:`/`resolved:` identity is written — the name/mode/workspace derive from
   `registry.standalone` + the live kuid;
3. registers the box in `registry.standalone` (`box_name` → *root*).

*root* is the standalone project dir. The box store (`root/box_data` by default) must already exist
(each caller creates/copies it before calling). Returns `(box_name, shell_path, vault_ro, vault_rw)`
so callers can build their result state without recomputing the table. Callers own their own
surrounding concerns (file copies, unwind registration, old-name unregister) — only the
identity/meta/register core lives here.

*register* (the interrupted-create journal): when False the identity is still resolved and the meta
file written, but the box is NOT registered in `registry.standalone` — the caller defers
registration until AFTER the home seed (journal entry → seed → register → clear-entry), so a crash
mid-seed leaves an UNregistered box that recovery resolves by its on-disk root. Defaults True (the
convert/duplicate lifecycle callers register inline).

⚑ The ROOT `workset.yaml` (the standalone marker, alongside `box_data/`) is MATERIALIZED
unconditionally by the `workset.kuid` write, so keeping the box key at the box tier does NOT cost
the file detection needs.

The kuid is persisted as the settable `workset.kuid` key via the SAME keystore sparse-write
engine `config set` uses. The kuid IS the name's
prefix (`box_identity.standalone_kuid`); storing it makes it the STABLE cross-move handle (the
launch re-composes the name as `<stored kuid>_<live leaf>` so a moved box keeps its identity).

```python
def resolve_standalone_project(
    std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None, *,
    initialize: bool = False, enable_vault: bool | None = None, name: str = "",
    register: bool = True,
) -> ProjectPaths
```
Resolve (and optionally initialize) per-project paths for standalone mode.

All project state lives inside *project_dir* itself. No data is written to `$XDG_DATA_HOME`.

No layout axis. The project *root* (the runtime dir) is the standalone
workset root and holds, in fixed positions: `workset.yaml` (the workset tier, AT THE ROOT —
`metadata_path`), a `workspace/` subdir (the live workspace → `~/workspace` — the `project_path`;
the resolved `workset.workspaces`, default `{meta.workset.path}/workspace`), a `box_data/` marker dir
holding `home/` + the `<box>.jsonl` helper log, and `vault/{ro,rw}/`. The box identity is
`<kuid>_<sanitized leaf>` (generated + registered in `registry.standalone` at create time; reused
from the stored meta after).

*register* (the interrupted-create journal): forwarded to `establish_standalone`; when False on a
NEW box the meta is written and `is_new` set but the box is NOT registered, so the caller can
register after the home seed. Defaults True.

### Standalone resolution details

The hash + identity key off the ROOT (the standalone workset root), which is stable; the workspace
subdir is the bind source, not the identity.

`project_path` is the resolved `workset.workspaces` [Jei:2026-08-02]: the STANDALONE default
is `{meta.workset.path}/workspace` == the `workspace/` subdir, and a set `workset: {workspaces: …}`
in the ROOT `workset.yaml` repoints it ("changeable from workset level", spec §2e).
A `null` there makes it `None` (never the default subdir), and the `initialize` recovery then makes
no workspace.

The mode-aware tier pair comes from the ONE derivation: the BOX tier is `<store>/box.yaml`
(`box_data/box.yaml` by default; absent by default) and the WORKSET tier is the ROOT
`workset.yaml` — the file detection reads (`~/canon/workbook/specs/system-design-1.8.0.md`
§ "Detection & import") and where `workset.kuid` lives.

⚑ STANDALONE paths are derived from the (current) root, never the stored absolutes: the DEFAULT
formulas are all anchored to the root (`{meta.workset.path}/…`), so a default-shaped tree is
drop-in portable BY CONSTRUCTION — a moved/imported tree resolves against its new location. A stored
ABSOLUTE repoint (e.g. `workset.workspaces`) is the user's own choice and travels as written.

`enable_vault`: explicit param wins; else the box-scope `box.enable_vault` resolved through the
CASCADE by `resolve_box_enable_vault` — `base < system < workset < box`, the BOX tier being
`<store>/box.yaml` and the WORKSET tier the ROOT `workset.yaml`. ⚑ The workset level is DESIGN,
keyspec §2c's STANDALONE block ("Box values … still resolve from the workset tier … as downward
defaults when no box file exists"). ALL THREE resolvers go through the same cascade. The resolved
value decides only whether `initialize` lays the vault dirs; the create persists the explicit
*enable_vault* alone (`establish_standalone`, `None` writes nothing), never the resolved one.

**Box identity name:** sourced from `box_resolve` for a MATERIALIZED standalone — the
`workset.yaml`-stored `workset.registry: null` (`stores_standalone_registry_null`'s gate). `box_resolve` composes
the name LIVE as `<stored workset.kuid>_<live leaf>` — the kuid is the STABLE stored prefix
(from the box's OWN `workset.yaml`, design D6) and the leaf is re-derived from the CURRENT root
basename, so a moved standalone tree keeps its kuid identity while the leaf tracks the new dir; a
pre-kuid box (no `workset.kuid` ⇒ SENTINEL) falls back to the registered
`standalone:` key, else the dir leaf. A not-yet-materialized root (no box store) yields `""` (the
create block assigns it authoritatively via `establish_standalone`).

The user's explicit `--name` is only meaningful when establishing a NEW box; it is ignored once the
box exists, since the stored identity is authoritative.

**The create gate:** not-yet-initialized iff the `box_data/` marker dir is absent (the root itself
always exists — it is the runtime dir). The requested `--name` is PRE-FLIGHTED BEFORE any FS
mutation so a doomed create (a verbatim-canonical name already taken) refuses up front rather than
leaving an orphaned half-created `box_data/` + `vault/` tree. `establish_standalone`
re-resolves the name authoritatively; the pre-flight only surfaces the refusable collision early.
A null `workset.workspaces` in a pre-existing `<root>/workset.yaml` is pre-flighted the same way
(`project.workset.refuse_null_workspaces(..., standalone=True)`, Q96): there is no workspace dir to
create, so `box create --standalone` refuses before the first write.
The init block is only reached when no meta exists, so the identity is resolved fresh from the
user-supplied `--name` (empty → fresh canonical).

```python
def _init_standalone_project(
    std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path, *, enable_vault: bool = True, workset_root: Path,
) -> None
```
First-time standalone project setup: all state inside project dir.

Unlike workset init, this *does* create vault directories and a `.gitignore` (vault lives inside the
user's project, likely a git repo). ⚑ It passes *workset_root* — the standalone ROOT, the degenerate
workset root that owns the `vault/` skeleton — as `_init_common`'s `vault_root`, APART from
*metadata_path*: §2c declares `workset.boxes` *changeable from workset level*, so a repointed
store's parent is not the root, and deriving the root from it would stamp `write_vault_gitignore`
on the store. A `workset.vault_ro` repointed anywhere still gets its directory created — the
`.gitignore` never follows it, and never has anything written beside it.

Credential copy is handled separately by `target.init_home()` in `start.py`, after template
application.

*metadata_path* is the `box_data/` marker dir (home + helper log); *project_path* is the
`workspace/` subdir (the live workspace), which is created here — it is a SUBDIR of the root — so
the bind source exists.
