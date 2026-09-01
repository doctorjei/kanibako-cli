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

* **`primary`** — the implicit PRIMARY workset (formerly the synthesized *default* workset). Box
  state lives under `@config.primary_workset/boxes/<name>/`.
* **`named`** — a named workset. Box state lives under the workset's own `projects/<name>/`.
* **`standalone`** — all state lives inside the project directory itself.

### The standalone marker (`_STANDALONE_META_DIR`)

`box_data` is the STANDALONE box-store dir name. It is `@meta.box.path` for a standalone box (the
empty leaf of `@workset.boxes`, keyspec §2c) and HALF of the detection marker
(`system-design-1.8.0.md` § "Detection & import") — the other half is the ROOT `workset.yaml`.
Both must be present; a bare `box_data/` directory is not a marker.

It is defined at the TOP of the module, not inside the detection helper that used to own it, because
`_box_settings_files` needs it and it is a LAYOUT constant rather than a detail of detection.

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

`global` is ELIMINATED as a key — its children inline `@config.data/global/...`, and the `global/`
dir is created on demand by the atomic writer when those files are first written.

### `template` — the system TEMPLATE ROOT

M-11 rename of the former `system.base_template`; the default moved `global/base_template` →
`global/template` at the same time. Holding the ROOT rather than the box-seed dir directly is what
leaves room for further template subtrees without new keys.

⚑ The box-HOME seed is `template/box/home`, **NOT** the root and **NOT** `box/`: `box/` is the box
TEMPLATE root, holding `home/` (spec §2a layers 1–3, the `seeded` category) beside `canon/handbook/`
(the box handbook HOST template — not a seed since 2026-08-07g; see
`launch.templates.install_box_handbook_template`).

### `canon` — the SYSTEM-level CANON CONTRIBUTION root (spec §2g)

Its `handbook/` subtree supplies `SYS_CONTENTS.md` + the `general` chapter, bound RO into every box.

⚑ It names what this SCOPE CONTRIBUTES to the canon, NOT a copy of the assembled tree: `~/canon`
(guest) is the assembly, `@<scope>.canon` (host) is one scope's contribution to it.

### `journal` — the lifecycle journal

Write-ahead log of in-flight box-lifecycle ops, beside the registry
(`config.journal = @config.data/global/journal.yaml`). PATH-BASED on the resolved key (mirrors
`registry`; no reconstruction).

### `boxes`, `primary_vault_ro`, `primary_vault_rw`, `primary_logs`

`boxes` is the PRIMARY-workset box store, `@config.primary_workset/boxes`. Phase 5 moved it here
from the OLD `@config.data/boxes` location; the transitional `_boxes` pseudo-key + alias property
were retired with the `_migrate_settings_to_boxes` shim. Per-box metadata/shell live under
`boxes/<name>/`; the PRIMARY vault/logs live as siblings under the PRIMARY workset (see
`resolve_project`).

The vault + logs roots DEFAULT to `@config.primary_workset/vault/{ro,rw}` and
`@config.primary_workset/logs`. Phase 5 moved the PRIMARY vault out of the workspace into the
PRIMARY workset.

⚑⚑ **All four PRIMARY roots are RESOLVED, not composed.** The spec declares no `system.boxes`,
`system.logs`, `system.vault_ro` or `system.vault_rw` at all (`:335`) — these fields are SURROGATES
for the PRIMARY workset's `@workset.{boxes,logs,vault_ro,vault_rw}`, which are declared,
CLI-settable, repointable keys in EVERY mode (§2c ALL PROJECTS, R-29). The PRIMARY workset root is
an ordinary workset root carrying an ordinary `workset.yaml`, so it repoints exactly as a named one
does. `resolve_system_paths` reads that `workset.yaml` ONCE and routes all four through
`project.workset.resolve_workset_{boxes,logs,vault_ro,vault_rw}`. Resolving HERE rather than at
`_primary_box_paths` is deliberate: every consumer of `std.boxes` / `std.primary_logs` /
`std.primary_vault_*` (box create, `box rm`, `clean`, purge, the helper hub) then sees the ONE
answer, with no edit at any of those sites.

⚑ `boxes` and `primary_logs` joined the resolved set on 2026-08-29, with `Workset.projects_dir` /
`logs_dir` and the named arm of `helper_log_path`. Before that they composed `pw / BOXES_PATH` and
`pw / LOGS_PATH`, which is why the tripwire on those joins bans the CONSTANT and not just the
string.

## Groups and the workset tier

### `ProjectGroup`

Captures the default-vs-workset difference as *data* rather than control flow. The implicit default
group is the *default workset* (`is_default` is True), rooted at `@config.primary_workset` (spec
§2c: PRIMARY `meta.workset.path`); a named workset forms a non-default group rooted at the workset
root. Standalone projects belong to no group (`ProjectPaths.group` is None).

*local_shared_base* is the root under which the local-shared path lives (`base / "common"`): the
standard data path for the default group, the workset root for a workset group.

### `_WorksetRooted`

Structural type for "anything rooted at `@meta.workset.path`". Satisfied by both `ProjectGroup` (the
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

## The two-layer path foundation

### Layer 1 — the CONFIG-key FOUNDATION (`CONFIG_PATH_DEFAULTS`, spec §1)

The 5 bootstrap CONFIG keys live in `kanibako_config.yaml` (`.config` / `/etc`) and resolve via a
flat foundation resolver, **NOT** the keyspace pipeline — chicken-and-egg: the pipeline needs these
resolved to find its own input files. `config.global` is ELIMINATED; its children inline
`@config.data/global/...` (the `global/` dir is created on demand by the atomic writer when those
files are first written). `@config.*` refs resolve against THIS set; `$XDG_*` against the
environment.

`config.journal` is the LIFECYCLE JOURNAL (write-ahead log of in-flight box-lifecycle ops), beside
the registry. The registry is the steady-state truth; the journal is the transient truth (normally
empty). See `kanibako.launch.journal`.

### Layer 2 — system-scope SETTINGS keys that are PATHS (`SYSTEM_PATH_DEFAULTS`, spec §1/§2g)

These are SETTINGS keys (system tier), NOT bootstrap config: each `@`-refs a Layer-1 config key (or
an XDG base). They resolve the normal way at launch (assemble→merge→expand) — but the flat resolver
ALSO materializes them into `StandardPaths` (the legacy host-side path surface) by resolving
`@config.*` against the Layer-1 foundation. `channelroot` moved to Layer 2 (its skeleton is created
on the launch path, not at setup). The OLD per-leaf `boxes` location is resolved separately (see
`resolve_system_paths`) and is NOT a key here.

`system.template` — M-11 (2026-07-30): `system.base_template` → `system.template`, and the default
moved `global/base_template` → `global/template` at the same time. ⚑ The on-disk dir MOVES with it:
an existing install's populated (possibly user-edited) `global/base_template/` is ORPHANED by the
rename — setup re-installs packaged content at the new root and the old dir is left behind.
Documentation-only, deliberately: no code auto-migrates a user's store.

`system.canon` — the SYSTEM canon CONTRIBUTION root (spec §2g). Same indirection as
`system.template`: holding the ROOT leaves room for further canon subtrees without new keys, and its
`handbook/` is what binds into a box.

### Module-level singletons

`logger` is the module logger (`get_logger("paths")`); the XDG fallback warnings and the `_flag_*`
advisories go through it (the advisories use `get_logger(__name__)`).

### `_XDG_SPEC_DEFAULTS`

Spec defaults for the XDG base directories that HAVE one (freedesktop Base Directory spec).
⚑ `XDG_RUNTIME_DIR` is deliberately ABSENT — it has no spec default and is handled specially by
`resolve_xdg` (fallback + warn).

## Functions

```python
def workset_settings_path(group: _WorksetRooted | None) -> Path | None
```
THE workset-tier settings-file derivation (spec §2c ALL WORKSETS: `meta.workset.settings` =
`@meta.workset.path/workset.yaml`).

`group.root` carries `@meta.workset.path` for both modes — the PRIMARY workset roots at
`@config.primary_workset`, a NAMED workset at its own root — so the one expression serves every
caller (launch cascade, config verbs, `--effective` displays). `None` (no group = standalone) has no
workset tier file.

The two `@overload`s exist so a caller passing a concrete group gets `Path`, not `Path | None`.

```python
def _default_project_group(std: StandardPaths) -> ProjectGroup
```
The PRIMARY (default) workset's `ProjectGroup`.

Spec §2c: the PRIMARY workset roots at `@config.primary_workset` — the workset-tier settings/env
files derive from this root (F4). `local_shared_base` stays the data path (the legacy `shared/`
location).

```python
def box_tree_materialized(proj: ProjectPaths) -> bool
```
True when the box tree a `create` would materialize is ALREADY on disk.

The MODE-AWARE analogue of `is_new`, computable from a NON-materialising probe (`initialize=False`)
— which is the whole point: `is_new` is only set inside the `initialize=True` branch that does the
mutation, so a caller that wants to REFUSE before mutating cannot ask `is_new` and has to ask this
instead.

* PRIMARY / NAMED — the box dir IS `metadata_path`, and `resolve_project` gates `is_new` on exactly
  that dir, so with `initialize=True` this is precisely `not is_new`.
* STANDALONE — `metadata_path` is the USER'S OWN project root, which always exists (it is their
  runtime dir), so it says nothing. The marker is `<root>/box_data` — the same dir
  `resolve_standalone_project` gates `is_new` on.

⚑ For a BRAND-NEW primary box `_resolve_local_dir` misses and `metadata_path` is the
`__unregistered__` placeholder, which does not exist ⇒ False ⇒ the create proceeds. That coupling is
inherited from `resolve_project`'s own `is_new` gate, not introduced here.

The mode split is NOT restated here — `box_metadata_dir` already owns it, and it lands on exactly
the two dirs the two resolvers gate `is_new` on. One derivation, not a second copy that can drift.

```python
def _standalone_settings_files(root: Path) -> tuple[Path, Path]
```
The STANDALONE `(box_tier, workset_tier)` pair — BOTH always real paths.

The standalone arm of `_box_settings_files`, split out because it is the one mode whose workset tier
is unconditional (it is the project ROOT file, not a `ProjectGroup` lookup that can come back
`None`). Callers inside the standalone resolver need that stronger type — they read and write both
files — and getting it from the type system beats asserting it at each site. Still ONE derivation:
`_box_settings_files` delegates here rather than restating it.

```python
def box_metadata_dir(mode: BoxMode, metadata_path: Path) -> Path
```
The DIR holding a box's own metadata — home, session state, box tier.

Equals `metadata_path` for primary/named, but for STANDALONE `metadata_path` is the PROJECT ROOT,
whose box metadata lives one level down in `box_data/` (beside `workspace/` and `vault/`, which are
NOT box metadata).

⚑ Lifecycle ops (convert / move / duplicate) must copy from HERE, not from `metadata_path`. Copying
a standalone ROOT "as if it were box metadata" both drags the workspace + vault into the
destination's box dir AND delivers the source's WORKSET-tier file to the destination's BOX tier —
which after P2 means the real box settings (one level down) are never read again.

```python
def _box_settings_files(
    mode: BoxMode, metadata_path: Path, group: "ProjectGroup | None",
) -> tuple[Path, Path | None]
```
THE `(box_tier, workset_tier)` settings-file derivation (spec §2c).

ONE expression, spelled ONCE. `meta.box.settings` is the UNIFORM `@meta.box.path/box.yaml` in
EVERY mode (spec §2c ALL PROJECTS), so the box tier is ALWAYS a real path — never `None`:

* **primary / named** — box tier = the box's own `<metadata_path>/box.yaml` (`BOX_META_FILE`,
  which IS `@meta.box.path` for these modes); workset tier = `workset_settings_path(group)` (the
  workset root's `workset.yaml`).
* **standalone** — `@meta.box.path` is the `box_data/` marker dir (the empty leaf of
  `@workset.boxes`), so the box tier is `<root>/box_data/box.yaml` — **ABSENT BY DEFAULT**
  (spec §2c + §4 STANDALONE tree): an absent file is an empty tier, and `config_io.load_doc` yields
  `{}` for it, so a standalone box with no box file resolves byte-identically to one with no box
  tier at all. The workset tier is the ROOT `<root>/workset.yaml` — the file that plays the
  WORKSET tier for a degenerate one-box workset, and the file DETECTION reads
  (`system-design-1.8.0.md` § "Detection & import"; `box_resolve.standalone_settings_present`).
  A `box.*` key stored THERE still resolves for box scope via R2 downward-defaults (`box` ⊂
  `workset` in `SCOPE_CONTAINMENT` — the workset-tier read KEEPS `box.*`). That is DECLARED DESIGN
  (keyspec §2c), and it is also how a pre-P2 standalone box keeps working with no migration.

⚑ This pair is the SINGLE SOURCE for READ, WRITE **and** ANCHOR (M-8): the launch cascade's
`box_path`/`workset_path`, the `meta.box.settings` anchor, and the `config set` / `get` / `show` /
`reset` target all derive from here. A second, independent spelling of either path is the M-8 bug
("I set it and nothing changed") and must not be re-introduced.

⚑ The return type says `Path`, not `Path | None`, deliberately: the non-optional box tier is
TYPE-CHECKED, so mypy rejects any re-introduction of a `None` box tier and flags any stale `is None`
narrowing at a call site. **This one is kept as a marker in the source docstring**, because the
constraint lives at that exact signature.

Takes the three primitives rather than a `ProjectPaths` because the standalone/primary/named
resolvers must consult it WHILE that dataclass is still being constructed (`enable_vault` is read
before the instance exists). `box_workset_settings_paths` is the `ProjectPaths` adapter.

```python
def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]
```
The `ProjectPaths` ADAPTER over `_box_settings_files`.

The name every caller uses. Carries no logic of its own — the derivation lives in one place so the
pair cannot be spelled twice.

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
def _runtime_base_usable(base: Path) -> bool
```
True iff *base* is a directory we own and can write to.

Mirrors the freedesktop requirement that the runtime dir be owned by the user and writable.
Best-effort: any OS error → not usable.

```python
def xdg(env_var: str, default_suffix: str) -> Path
```
Resolve an XDG directory from environment or default under `$HOME`.

Backward-compatible thin wrapper over `resolve_xdg`: honors the env var only when set AND absolute
(a relative value is now ignored per the spec), else returns `$HOME/<default_suffix>`. Used by the
many call sites that just need a spec-backed XDG base dir.

```python
def host_xdg_map(data_home: Path | None = None) -> dict[str, str]
```
Build the canonical HOST-side `$XDG_*` map for a `ResolveCtx`.

THE single builder for the `xdg=` argument of every host-side
`kanibako.settings.settings_resolve.ResolveCtx` (Jei ruling 2026-07-02: XDG vars must have
fallbacks). Hand-rolled partial maps caused stored values like `$XDG_CACHE_HOME/kanibako` (the
setup-materialized `system.cache`) to raise `Variable $XDG_CACHE_HOME is not set in this context` at
expand time — the resolver reads ONLY this map, never the environment, so a missing key is
unrecoverable at the call site.

Every var in `_XDG_SPEC_DEFAULTS` resolves via the hardened `resolve_xdg` (env honored iff set AND
absolute; unset/empty/relative → the XDG-spec default under `$HOME`), plus `XDG_RUNTIME_DIR` (no
spec default: fallback + warn). *data_home* is an optional already-resolved `$XDG_DATA_HOME`
anchoring the default tree (the flat foundation/system resolver passes it); when None it resolves
from the environment like the rest.

```python
def resolve_config_paths(
    set_values: Mapping[str, str], *, data_home: Path, home: Path,
) -> dict[str, str]
```
Resolve the Layer-1 CONFIG-key foundation to concrete host paths.

*set_values* holds raw user-set `config.<leaf>` expressions (from the `kanibako_config.yaml` set).
Returns `{config.<key>: resolved_str}` for every key in `CONFIG_PATH_DEFAULTS` — the FOUNDATION
mapping injected into `kanibako.settings.settings_resolve.ResolveCtx.config` so `@config.*` refs
resolve there (spec §1A / JC-2). Flat by design (chicken-and-egg): the keyspace pipeline needs these
resolved to find its own input files, so they resolve OUTSIDE it, with `@config.*` refs chained
within this set only.

```python
def resolve_system_paths(
    set_values: Mapping[str, str], *, data_home: Path, home: Path,
) -> dict[str, Path]
```
Resolve the path tier to concrete host paths.

*set_values* holds raw user-set expressions keyed by their full dotted name — the MERGED config-file
set (both Layer-1 `config.<leaf>` and Layer-2 `system.<leaf>` keys, e.g. the global config's
`config_paths`). It is split by prefix: `config.*` seeds the Layer-1 foundation, `system.*` the
Layer-2 path settings. *data_home* is the already-resolved XDG data base exposed as
`$XDG_DATA_HOME`; *home* expands a leading `~`.

Returns `{full_dotted_key: Path}` for every Layer-1 `config.*` key AND every Layer-2 `system.*` key,
plus the derived PRIMARY-workset pseudo-keys `system._boxes` / `system._primary_vault_ro` /
`system._primary_vault_rw` / `system._primary_logs` (under `@config.primary_workset`). The
`system.*` defaults `@`-ref a Layer-1 config key, resolved against the foundation injected into
`ctx.config`.

Internal notes:

* The nested `lookup` implements the **resolver SPLIT** (spec §1A / JC-2): `@config.*` → the Layer-1
  foundation (`ctx.config`); `@system.*` → the system path set. Prefix-driven.
* `system.*` config paths are always scalar strings (no structured category leaves at this tier),
  which is why the now-`object`-typed value is narrowed with `str(...)` before expansion.
* The `system._*` pseudo-keys are the PRIMARY-workset box/vault/logs roots, derived from the
  resolved PRIMARY workset dir (`@config.primary_workset`). ⚑ `_primary_vault_ro` /
  `_primary_vault_rw` are RESOLVED through the workset dir-key route (one `workset.yaml` read,
  both arms), not composed — see the `primary_vault_*` field notes above. That is the only file
  read in this function, and it is best-effort: an ABSENT or unparseable `workset.yaml` yields the
  declared defaults, while an unresolvable `@`-ref REFUSES and names the key.

```python
def load_system_config(
    user_config_path: Path, *, data_home: Path, home: Path,
) -> dict[str, Path]
```
Resolve the path tier from the CONFIG file set **and the SYSTEM SETTINGS file**.

Three layers, read in cascade order so the most-authoritative present value of each set-value wins
**before** expression resolution:

1. `/etc/kanibako/config_base.yaml` — site-wide overridable defaults (least specific). **`config.*`
   only.**
2. *user_config_path* — the user's global `~/.config/kanibako_config.yaml` (overrides the base).
   **`config.*` only.**
3. `@config.settings` — the SYSTEM SETTINGS file's `system:` table, filtered to
   `SYSTEM_PATH_DEFAULTS` (most specific).

Missing files are skipped (each contributes nothing). The merged set-values are split by prefix into
the Layer-1 `config.*` foundation and the Layer-2 `system.*` path settings, then handed to
`resolve_system_paths`, which fills in the defaults and resolves `@`-/`$XDG_*`-references.

⚑⚑ **EACH FILE CONTRIBUTES EXACTLY ONE LAYER, AND THE CONFIG FILTER IS NEW (2026-08-26).** A
`system:` table hand-written into a CONFIG file used to enter `raw` here as a real (if lowest) layer
of the Layer-2 path tier — which made the bootstrap file a settings source in the one place it most
mattered, where every host path is decided. Jei: *"kanibako_config.yaml <-- cannot have settings.
Period."* This is the exact mirror of the filter layer 3 already had in the other direction (a
`config:` table in a SETTINGS file must never reach Layer 1, spec §1), and it is the same filter
`resolve_data_leaf` already applied to the same two files — one rule, two sites, and this was the
site that lacked it.

Back-compat: a user with only `~/.config/kanibako_config.yaml` (no `/etc` file) gets the base layer
empty, so the user file is the sole set-source.

⚑⚑ **LAYER 3 IS NEW (2026-08-23), AND ITS ABSENCE WAS A PARTIAL SUCCESS — the failure shape that is
worse than a refusal.** Spec §2g declares `system.{template,canon,cache,runtime,backup,channelroot}`
and `system.channels.*` SETTINGS keys, *"set in settings files at the `system` cascade level"*, and
`config set system.canon=…` writes them there. Reading the CONFIG files only meant that write
reached the launch cascade — binds, seeds, `show --effective` — and NEVER reached `StandardPaths`:
`std.canon` kept answering the default. Accepted, persisted, half-effective, and silent about it.
`tests/test_settings/test_repoint_reaches_std_paths.py` asserts the EFFECT rather than the
destination, because the destination pins were green the whole time.

⚑ **THE LAYER-1 RESOLVE RUNS TWICE, DELIBERATELY.** Locating the settings file IS `@config.settings`,
so the foundation must resolve before the file can be opened; `resolve_config_paths` is a pure dict
resolve over set-values already in hand, so the second pass reopens nothing.

⚑ **THE SETTINGS LAYER IS FILTERED TO `SYSTEM_PATH_DEFAULTS`** (P13 — derived from the table, not a
list at the call site). That file's `system:` table also holds `system.agent`, the
`auth`/`env`/`secret_path` families and the bind-shaped categories, none of which belong to the path
tier — and a `config:` table hand-written into a SETTINGS file must never reach Layer 1, which lives
in `kanibako_config.yaml` alone (spec §1). Both halves are pinned.

⚑ The `kanibako.settings.config` import inside the body is a lazy import that avoids a
`config` ↔ `paths` cycle at module load — do not hoist it.

`bootstrap_config_paths(...)` yields the CONFIG file's set-values keyed by their full dotted name
(`config.*` alone), or `{}` when the file is absent — so missing layers are skipped automatically.
🛑 It RAISES on a settings table in that file (2026-08-31), which is why every verb that resolves a
path is where a user with a stale `kanibako_config.yaml` first hears about it. The SETTINGS file's
`system.*` half is read by `config.system_path_set_values`, a separate walk over that file's
`system:` table.

```python
def host_config_map(std: StandardPaths) -> dict[str, str]
```
The resolved Layer-1 `config.*` foundation projected BACK onto its own dotted key names — the Layer-1
twin of `system_path_floor`, and the `config=` twin of `host_xdg_map`. A host-side `ResolveCtx` is
built from those two builders and nothing else; consumers put this in `ctx.config` so a stored
`@config.*` source resolves under the resolver SPLIT (spec §1A / JC-2).

⚑⚑ **IT IS ONE FUNCTION BECAUSE IT USED TO BE TWO HAND-WRITTEN MAPS, AND BOTH WERE THE SAME KEY
SHORT** — the `system_path_floor` story below, one layer down, found 2026-08-28.
`settings/agent_select.launch_resolve_ctx` and `commands/workset_cmd._print_effective_shares` each
wrote five string literals; `CONFIG_PATH_DEFAULTS` has declared six since `config.journal` was added
on 2026-06-30, the day after the original map was written on 2026-06-29. `config.journal` is
declared `layer: 1` with a real default in the keyspace manifest, is `set: file`, and is in
`config_keys.KNOWN_KEYS` — so `config set config.journal=…` was accepted and a binding sourced at
`@config.journal` resolved at SET time (`config_interface._path_tier_split()[0]` iterates the whole
resolve, so it always carried six) and hit `_ABSENT` at launch. The referring key was dropped with no
message and rc 0: accepted-then-silently-discarded, which is strictly worse than either side being
consistently wrong.

⚑ **THE KEY SET IS DERIVED, AND THE ATTRIBUTE NAME IS A RULE, NOT A COINCIDENCE.** Layer 1 names its
`StandardPaths` fields after its keys (`config.data` → `data`, `config.primary_workset` →
`primary_workset`, …), all six, which is exactly why this needs no alias at all where
`system_path_floor` needs exactly one — `system.channelroot` resolves into `std.channels`, the single
Layer-2 field whose name does not follow from its key. A Layer-1 key declared without the matching field raises
`AttributeError` on the next ctx build, at every launch. Silent omission is the failure this replaces;
a crash is the strictly better one.

⚑ `tests/test_settings/test_path_tier_parity.py` is the comparison that did not exist — it pins this
map against the set-time foundation across all three box modes plus the `agent_name=None` selection
pass, effect-based on both sides, with an anti-vacuity arm pinning the derived side to
`CONFIG_PATH_DEFAULTS` so a filter bug emptying both cannot pass.

```python
def system_path_floor(std: StandardPaths) -> dict[str, str]
```
The resolved Layer-2 `system.*` tier projected BACK onto its own dotted key names — the inverse of
what `resolve_system_paths` produced. Consumers fold it into a settings floor so a stored
`@system.*` source resolves; each value equals the corresponding `std` attribute, so an
`@`-ref-routed bind is byte-identical to a runtime-probed literal.

⚑⚑ **IT IS ONE FUNCTION BECAUSE IT USED TO BE TWO HAND-WRITTEN MAPS, AND BOTH WERE WRONG.**
`commands/start._launch_snapshot_inputs` (the launch snapshot) and
`commands/workset_cmd._print_effective_shares` (`workset share list --effective`) each wrote the
tier out inline, under paired comments saying they had to agree. The launch map omitted
`system.channels.broadcast` — a declared key with a manifest default, resolved into
`StandardPaths.channels_broadcast`, reaching no floor — so `@system.channels.broadcast` was
`__MISSING__` in every snapshot and a binding sourced at it was **dropped from the collapse with no
message and rc 0**. The display map omitted all five `system.channels.*` leaves, so a workset
binding sourcing `@system.channels.chat` mounted at launch and did not print. Two carriers of one
shape is the defect class; the repair is one carrier, not a better promise.

⚑⚑ **THE KEY SET IS `SYSTEM_PATH_DEFAULTS` ENTIRE — 11 keys since 2026-08-28, and it was 8.** The
`_FLOOR_ROOT_KEYS` tuple this replaced named three roots by hand and derived only the channel
leaves, which left `system.backup`, `system.cache` and `system.runtime` out: declared, carrying
manifest defaults, CLI-settable, and resolved by the SET-time tier
(`config_interface._path_tier_split`, which iterates the whole table) — so `config set` accepted a
binding sourced at `@system.cache` and the launch snapshot answered `__MISSING__`, dropping it with
no message and rc 0. Measured on the real snapshot before and after: those three go `__MISSING__` →
their resolved paths, the other eight are byte-identical, and the set-time/launch pair goes 11-vs-8
to 11-vs-11. [R143] is the authority — *"if it has a default value, yes, thay value should be placed
in the keystore"* — universal, no allowlist and no origin test.

⚑ **RESERVED AND REACHABLE ARE ORTHOGONAL, WHICH IS WHY THIS NEEDS NO DISCRIMINATOR.** Nothing reads
those three yet and nothing here gives them a consumer: *reserved* is a fact about consumers, this
floor is a fact about the keystore, and a reserved key still answers.

⚑ **`_FLOOR_FIELD_ALIASES` IS A SPELLING TABLE, NOT A MEMBERSHIP LIST**, and the distinction is the
point. Membership is the declared table; the alias map only carries the one key whose
`StandardPaths` field does not follow from its name (`system.channelroot` → `std.channels`, a field
older than the key). Everything else is `key.split(".", 1)[1].replace(".", "_")`. A key declared
without a matching field raises `AttributeError` at the next floor build — a membership list fails
by silence, this fails by crash, the same trade `host_config_map` makes one layer down.

⚑ **CONSUMERS, BOTH OF THEM, CHECKED IN THE SAME CHANGE.**
`commands/start._launch_snapshot_inputs` folds the map into `default_categories` through
`_merge_default_categories` as a deliberate late-injection override; the three additions are
scalars, so they take the last-wins arm, claim no category destination and trigger no origin
refusal, and being declared they pass `_refuse_undeclared_snapshot` — the snapshot gains three
`system.*` leaves and no top-level key. `commands/workset_cmd._print_effective_shares` folds the
same map into an `assemble_levels` floor and prints collapsed BINDINGS, never the floor, so
`workset share list --effective` gains no row from the widening — what changes is that a binding
sourced at one of the three now resolves instead of vanishing.
`tests/test_channels/test_system_channel_keys.py` held the by-name pin on the omission; it is
**inverted, not deleted**.

```python
def load_std_paths(config: BootstrapConfig | None = None) -> StandardPaths
```
Compute all standard kanibako directories.

If *config* is None, it is loaded from the config file (which must exist). Directories are created
as needed.

The system-level path tier (settings-framework `system.path.*`) is resolved from the CONFIG file
set: `/etc` `config_base` < user-global. A user with only `~/.config/kanibako_config.yaml` gets the
prior behavior (empty `/etc` layer). The state/cache paths track the data dir's leaf name (unchanged
behavior: default leaf `kanibako` under each XDG base).

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

Phase 5: PRIMARY boxes/vault/logs live under `@config.primary_workset` (the real PRIMARY-workset
dir); there is no layout axis. Per-box state is `boxes/<name>/` (metadata + shell) with the vault at
`@config.primary_workset/vault/{ro,rw}/<name>`.

*enable_vault* controls whether vault directories are created and mounted. Defaults to True for new
projects; existing projects read from `box.yaml`.

*register* (B3 interrupted-create journal): when False AND this call materializes a NEW box, the box
dir + meta are created and `is_new` is set, but the PRIMARY membership is NOT written — the caller
defers registration until AFTER the home seed (journal entry → seed → register → clear-entry) so the
invariant "registered ⟹ fully seeded" holds on the sole store. The picked name is still reserved
against a concurrent create via the directory-aware `pick_primary_box_name`. Defaults True (every
other caller registers inline, unchanged).

### `resolve_project` — the bare-token front door

If the user passed a bare token (no path separator) and no file/dir of that name exists in cwd, it
is tried as a registered project name. Resolution falls through to path resolution on a miss so the
eventual error stays informative.

### `resolve_project` — the registry reverse-lookup miss

**`register=True` (a normal resolve):** P8b/Option A — an unregistered on-disk PRIMARY box is NOT
auto-rediscovered. The registry is the SOLE identity authority; a primary box no longer
self-describes on disk (sparse create), so there is nothing on disk to re-import from.
`project_name` stays empty and the code falls through to the not-found/create path (a fresh name is
assigned when `initialize`; otherwise the miss surfaces downstream). The future `system recover` is
the remedy for a lost registry entry.

**`register=False` (a deferred-registration create/recovery resolve):** the box's name is read from
the pending CREATE JOURNAL entry whose recorded workspace == this workspace (P8b — identity no
longer self-describes in on-disk meta). Re-discovering a half-built box during a re-create must NOT
prematurely register it (the caller completes seed → register → clear-entry), so the resolved name
re-associates the on-disk dir directly (the registry is still empty, so `_resolve_local_dir` would
miss again).

### `resolve_project` — the registration-layer reverse-lookup (Bug A durable fix)

`_resolve_local_dir` now reverse-looks-up the PRIMARY membership itself (resolved-path aware), so
this arm is a belt-and-suspenders repeat: if the name is still unresolved, consult the
PRIMARY-workset `boxes:` membership — the SAME registry `register_workset_box`'s uniqueness guard
(Guard 1) writes and `list`/`box_resolve` read. Reusing the existing name/dir here keeps the create
branch from minting a duplicate entry + box dir for a workspace that is ALREADY a member (which
would otherwise let Guard 1 raise mid-create, after `_init_project` already committed the dir, with
no unwind → stranded half-box). The lookup is exception-guarded (a symlink-cycle/permission path
must not crash `resolve_project`) — matching `_same_workspace`'s own guarding. (`register=False`
deferred-create is handled by the journal block above and left untouched here.)

### `resolve_project` — B2b: the custom home/vault override is DROPPED

Option A, Jei-ruled: the per-box `meta["shell"]` / `["vault_ro"]` / `["vault_rw"]` custom-path
OVERRIDE is DROPPED. home/vault are now SOLELY the spec-derived default location
(`@meta.box.home` + `@workset.vault_{ro,rw}/@meta.box.name`); the launch routes the home/vault binds
through those `@`-refs. `@meta.box.home` is the RO DERIVED key, itself `@meta.box.path/home` — the
bind names the key and does not re-derive it.

A user customizing home/vault now sets the `box.bindings.{rw,ro}.{home,vault}` CASCADE override
(which wins naturally), NOT a stored shell path. The `shell` / `vault_*` fields are no longer written
to disk at all under sparse create (P8b/Option A).

### `resolve_project` — `enable_vault` (P5a)

An explicit param wins; otherwise the stored box-scope `box.enable_vault` (absent ⇒ True). Decoupled
from box identity — which now derives from the registries (`box_resolve`), not `read_project_meta`.

⚑ THE WORKSET TIER HERE IS THE PRIMARY WORKSET. The primary workset IS a workset (spec §2c gives it
the same `meta.workset.settings`), so spec §0's containment rule makes a `box.*` key stored there an
OVERRIDABLE downward default for every box it contains — the box tier still wins (`… < workset <
box`). What the create branch PERSISTS is the BOX-AUTHORED value, never the resolved one: see the
NAMED resolver for the full reasoning.

⚑⚑ **IT IS `config.resolve_box_enable_vault` SINCE 2026-08-29, NOT `read_box_enable_vault`.** The
resolved value now comes from the real cascade — `base < system < workset < box` — instead of two
hand-opened files, so a `kanibako system set box.enable_vault=false` reaches the box. It did not
before: the write was accepted, persisted and echoed back by `system get`, and every box still came
up with the vault created and mounted.

### `resolve_project` — the create branch

New project: SELECT a name first (no store write there), then create `boxes/{name}/` and register
the PRIMARY membership below. The former global `projects:` name registry retired (2026-07-08); the
primary per-workset `boxes:` membership is the SOLE store now.

An explicit override (e.g. `kanibako create --name X`) registers strictly; collisions error rather
than auto-suffix — so for a normal (`register=True`) create the override is validated against the
PRIMARY-box name domain (membership ∪ workset names) UP FRONT, before the dir is materialized. A
deferred (`register=False`) create leaves that domain check to the post-seed commit
(`_register_new_box`).

B3: whichever branch, the name is only SELECTED there (directory-aware, so a half-built box's dir
keeps its name reserved); the membership write happens once — eager for `register=True`, deferred to
the caller for `register=False` (invariant "registered ⟹ fully seeded").

The `elif project_name:` arm means the reverse-lookup above (Bug A) matched this workspace to an
ALREADY-registered box name — its dir is just missing here, so reuse the name and (re)register the
membership below (idempotent — same name → same path is a no-op overwrite).

**`_dir_existed` — creation ownership for the unwind.** It captures whether the box dir ALREADY
existed at its FINAL (post-`name_override` reassignment) path, BEFORE `_init_project` merges into it
(`mkdir(exist_ok=True)`). A `--name X` pointing at a pre-existing orphan `std.boxes/X` must NOT be
`rmtree`d on a Guard-1 raise — that would delete a pre-existing box's `home/` (credentials, session
state). The unwind only removes a dir THIS call created.

**Sparse create (P8b/Option A):** NO `project:`/`resolved:` identity is written — the box's identity
+ workspace live in the PRIMARY per-workset `boxes:` membership (`box_resolve` reads it). Only a
NON-default BOX-AUTHORED `box.enable_vault` is persisted, sparsely (above).

**Registering the PRIMARY membership** (name → external workspace) is the SOLE store since the
global `projects:` section retired. The PRIMARY workset is NON-EXCEPTIONAL (D0/D1): its registry is
anchored by `std.primary_workset`. Idempotent — `register_workset_box` overwrites a moved box's
path. `register=False` DEFERS this write to the caller's post-seed commit (`_register_new_box` →
`register_primary_box_name_if_absent`).

**Belt-and-suspenders unwind:** Guard 2 above normally pre-empts Guard 1 (the workspace-path
uniqueness check in `register_workset_box`) by reusing the existing name. If Guard 1 STILL refuses —
e.g. an explicit `--name` claims a workspace already a member under a different name —
`resolve_project` has no outer unwind, so roll back the box dir (ONLY if THIS call created it —
never delete a pre-existing orphan's `home/`) so a genuine invariant violation fails CLEAN (no
stranded half-box), then re-raise. The membership write is the sole store, so a Guard-1 raise leaves
nothing else to unwind.

**No box.yaml backfill (P8b/Option A):** a primary box's identity, workspace and
`box.enable_vault` all live in the registries now (not a self-describing `project:`/`resolved:` on
disk), so a box dir lacking a `box.yaml` is not a defect to repair on the recovery path. The
former pre-v0.8 backfill materialized an identity that no longer exists on disk.

```python
def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]
```
Find the boxes directory for a default-mode project.

Reverse-looks-up the project name in the PRIMARY per-workset `boxes:` membership (the sole store
since the global `projects:` section retired) and returns `(project_name, std.boxes/{name}/)`. The
membership reverse-lookup is resolved-path aware (via `primary_box_name_for_workspace`), so a
symlink/normalization alias of the stored workspace still matches.

Returns `("", empty_path)` when no name is registered — the caller (`resolve_project`) will assign a
name during initialization. The lookup is exception-guarded (an unresolvable registry/path must not
crash `resolve_project`) — matching the registration-layer Guard 2.

```python
def _primary_box_paths(
    std: StandardPaths, metadata_path: Path, box_name: str,
) -> tuple[Path, Path, Path]
```
Fixed PRIMARY-mode `(shell, vault_ro, vault_rw)` (no layout axis).

Shell lives under the per-box metadata dir (`boxes/<name>/home`); the vault lives under the PRIMARY
workset (`@config.primary_workset/vault/{ro,rw}/<name>` by default), NOT inside the user's
workspace. Phase 5 moved the PRIMARY vault out of the workspace so the PRIMARY workset owns
boxes/vault/logs just like a named workset. ⚑ This function composes only the per-box NAME LEAF —
the two arms arrive already resolved on `std.primary_vault_{ro,rw}`, so a `workset.vault_ro`
repoint in the PRIMARY workset's `workset.yaml` reaches here for free.

```python
def _workset_box_paths(
    metadata_path: Path, vault_ro_base: Path, vault_rw_base: Path, box_name: str,
) -> tuple[Path, Path, Path]
```
Fixed NAMED-mode `(shell, vault_ro, vault_rw)` (no layout axis).

Shell under the per-project box dir; vault under the workset's two RESOLVED vault arms
(`<vault_ro_base>/<box_name>`, `<vault_rw_base>/<box_name>`). ⚑⚑ **ONE PARAMETER PER ARM, and it
must stay that way.** The old signature took a single `vault_base` and composed `ro`/`rw` onto it —
that spelled `workset.vault_ro` and `workset.vault_rw` as one shared parent, which they are not:
each is an independently repointable key, so one base cannot answer both. The ro/rw split still
nests ABOVE the box name to match PRIMARY and STANDALONE; only the `@meta.box.name` leaf is
composed here, and that leaf is the whole per-mode variation (§2c).

```python
def _standalone_box_paths(root: Path) -> tuple[Path, Path, Path]
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
def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path
```
Per-box, per-mode HOST path for the helper message log.

The log is the host source of the read-only `helpers.jsonl` bind into the box; it lives inside the
box's own workset/box tree (never the old shared `@config.data/logs/<id>/` location):

* PRIMARY → the RESOLVED `workset.logs` of the primary root (`std.primary_logs`)
* NAMED → the RESOLVED `workset.logs` of `proj.group.root`
* STANDALONE → the RESOLVED `workset.logs` of the project root (default `@meta.box.path`, i.e.
  `box_data/`), read from the root `workset.yaml`

⚑⚑ **This function is the hub's WRITER, and the MOUNT it must agree with is the spec's own
spelling** `@workset.logs/@{meta.box.name}.jsonl` (`data/core-defaults.yaml`, the `helpers` table).
While an arm COMPOSED its directory the two disagreed the instant a user repointed `workset.logs`:
the mount moved and the writer did not, so the box read an empty file forever. That is the split
**migration M-14** records, and it is CLOSED in ALL THREE modes — PRIMARY and NAMED as of
2026-08-29, STANDALONE as of 2026-08-30. The standalone arm was the hard one: its declared default
`@meta.box.path` is itself a ref to `@workset.boxes`, which `settings/workset_dirkeys` refuses
because it runs before the launch snapshot exists. The fix is NOT chaining in the pre-snapshot
resolver — it is that in standalone, and only there, the caller already HOLDS the answer
(`meta.box.path | @workset.boxes`, no name leaf), so `resolve_workset_logs(..., standalone=True)`
resolves `workset.boxes` once and passes it as `extra_refs`. In primary/named the same ref would
need the construct-time `@meta.box.name` and keeps refusing.

🛑 A repointed standalone `workset.logs` puts the log OUTSIDE `box_data/`, and `box rm --purge`
tears a standalone box down by removing `box_data/` wholesale — so the log file survives it, the
same way a repointed vault arm does (`standalone_vault_teardown`). `kanibako clean --purge` unlinks
the log explicitly through this function and is unaffected. Documented as a retained path in
MIGRATION.md; do not widen the purge to chase it.

The caller is responsible for guarantee-creating the parent dir before the bind (L7). The box-side
dest is the PINNED `~/.kanibako/state/helpers.jsonl` (declared in `core-defaults.yaml`), NOT a
`$XDG_STATE_HOME` expression; see
`kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH` for why, and
`box_supervisor.project_pinned_xdg` for the post-boot XDG projection.

The standalone log defaults inside the `box_data/` marker dir (settings itself lives at the root, so
the default resolves to `metadata_path/box_data` rather than `metadata_path`) so the whole
standalone tree is drop-in portable unless the user repoints the key. For NAMED, the workset root
is carried on the project
group (`root=ws.root`); the `metadata_path.parent.parent` fallback beside it still assumes the
DEFAULT box layout and is unreachable from `resolve_workset_project`, which always supplies the
group.

```python
_SHELL_D_SOURCE_LINE: str
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
setting `PS1`, and sourcing `_SHELL_D_SOURCE_LINE`), a `.profile` that sources `.bashrc`, and the
`.shell.d/` drop-in directory. Each file is written only when absent.

```python
def _upgrade_shell(shell_path: Path) -> None
```
Keep the `.shell.d` sourcing seam current on an existing shell directory.

Ensures the user/template extension point stays wired on every launch, including shells created
before this seam existed. Idempotent — safe to call every launch. Creates `.shell.d/` if missing and
appends the source line to `.bashrc` if absent. No-op if *shell_path* does not exist yet.

```python
def _init_common(
    std: StandardPaths, metadata_path: Path, shell_path: Path,
    vault_ro_path: Path, vault_rw_path: Path, project_path: Path, *,
    enable_vault: bool = True, vault_root: Path | None = None,
) -> None
```
Shared first-time project setup: create directories, bootstrap shell.

Called by both `_init_project` (default) and `_init_standalone_project`. It performs every step
common to both modes: print message, create metadata and shell dirs, bootstrap the shell, and set up
vault directories when enabled. The shell dir is the one mounted as `/home/agent`; a `.gitignore` in
`vault/` excludes `rw` from version control.

⚑ **`vault_root` GATES THE `.gitignore`, NOTHING ELSE.** That file belongs to the `vault/` SKELETON
dir — the one non-key leaf a workset root carries — and it is written at `vault_ro_path.parent`.
Once `workset.vault_ro` became repointable that parent stopped being guaranteed to be the skeleton:
`vault_ro: ~/store` makes it `$HOME`, and the write would drop a stray `rw/`-ignoring `.gitignore`
into the user's home. So the write happens only while the resolved vault still sits inside
*vault_root* — `std.primary_workset` for PRIMARY, the standalone ROOT (i.e. `metadata_path.parent`,
since `metadata_path` is `box_data/`) for STANDALONE. `None` keeps the unconditional legacy
behaviour for any caller that cannot name a root. ⚑ With NO repoint every mode is byte-identical to
before: the resolved arms are still under their root, so the same file lands in the same place.

Credential copy is handled separately by `target.init_home()` in `start.py`, after template
application.

```python
def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None
```
Find the deepest registered default-mode project that is an ancestor of *target*.

Reads the PRIMARY per-workset `boxes:` membership (the sole store since the global `projects:`
section retired) and, for each entry whose registered workspace path is a prefix of *target*, checks
that `std.boxes/{name}/` actually exists on disk. Among all valid matches, the deepest (most path
components) wins. Returns the matched path or `None`.

```python
def _is_standalone_meta_dir(root: Path) -> bool
```
True only if *root* carries the standalone box MARKER (presence-based).

P5a: the marker is a `box_data/` directory under *root* PLUS a root `workset.yaml` AT THE ROOT
(`<root>/workset.yaml`, NOT inside `box_data/`), both PRESENT. The FILE's existence is the
standalone self-declaration (design D4) — the former `box.mode == "standalone"` field read is
DROPPED (that field is gone). A box's own in-place settings file is the highest-precedence,
authoritative standalone signal and OVERRIDES any workset determination (D3-mode #1); requiring both
parts keeps an unrelated `box_data/` directory from being mistaken for a marker. Delegates to
`box_resolve.standalone_settings_present` (the single definition of the presence check).

```python
def detect_project_mode(
    project_dir: Path, std: StandardPaths, config: BootstrapConfig,
) -> DetectionResult
```
Infer which project mode applies to *project_dir*.

Walks ancestor directories (up to `$HOME` or filesystem root) looking for project markers. Returns a
`DetectionResult` with the detected mode and the ancestor directory where the marker was found.

Detection order:

1. **Connected-external** — *project_dir* (or an ancestor) is an external directory bound to a
   workset by a live `boxes:` connection record. Runs FIRST so a force-connected box (which keeps
   its on-disk marker) resolves as its workset box, never re-imported as standalone.
2. **In-place standalone marker** — a `box_data/` + root `workset.yaml` marker AT *project_dir*
   declares it standalone (D3-mode #1); OVERRIDES workset tree membership. Imported (registered) on
   discovery.
3. **Workset** — *project_dir* lives inside a registered workset root (`workspaces/` subdirectory
   first, then the root itself).
4. **Default (name-based)** — one-pass scan of `names.yaml`; deepest registered path that is an
   ancestor of *project_dir* wins. Requires `boxes/{name}/` to exist on disk.
5. **Walk ancestors for on-disk markers** — a `box_data/` standalone marker, or an unregistered
   NAMED workset root (a `registry.yaml` carrying a `workset:` identity table). Both are drop-in
   *imported* on discovery (registered + an alert to stderr; a name collision REFUSES — see
   `kanibako.project.import_reconcile`).
6. **Default** — `primary` mode at the original *project_dir*.

### Why step 1 must run before step 2

The connected-external check MUST run BEFORE the standalone-marker check to preserve the
single-registry invariant. A FORCE-CONNECTED standalone box (absorbed via
`workset.add_project(force=True)`) deliberately KEEPS its on-disk `box_data/` + root `workset.yaml`
marker while its global `standalone:` registry entry is DROPPED and a per-workset `boxes:`
connection entry is written — the box lives in EXACTLY ONE registry (no dual registration). Such a
box is EXTERNAL (outside the workset tree), so the workset-tree check (step 3) does NOT match it;
the live connection is what claims it as its named workset box. Were the marker check to run first,
its `import_standalone` side-effect would re-register the box in `standalone:`, re-creating the very
dual registration that `--force` removed. D10: the per-workset registries collectively form the
reverse index, scanned by `box_resolve` (replaces the deleted global `connected:` index).

### Step 2 — marker-first precedence

A box's own in-place settings file is the highest-precedence, authoritative standalone
self-declaration and OVERRIDES any workset TREE determination — a workset (even one whose tree
physically CONTAINS this box) must NOT be able to "steal" a box that declares itself standalone. (A
LIVE connection is the one exception, resolved by step 1.) This mirrors the marker-first precedence
of `box_resolve.detect_box_mode` (its step 1) and keys on the SAME standalone-marker signal
(`box_data/` + root `workset.yaml`, via `_is_standalone_meta_dir` →
`box_resolve.standalone_settings_present`), so a workset/primary box (which never carries
`box_data/`) is unaffected. Only the resolved dir itself is inspected there (an ancestor marker is
still handled by the step-5 walk); this matches `detect_box_mode`, which likewise honors the
in-place marker only at `project_dir` before the workset scan. A GENUINE nested standalone (with NO
connection record) is authoritative on disk → import (register) on discovery, exactly as the step-5
walk does.

### Step 5 — the ancestor walk

On-disk metadata is authoritative; a discovered-but-unregistered entity is IMPORTED there (alert +
register; collision → refuse) so a dropped-in tree is re-discovered. The NAMED check runs first at
each level: a workset root may itself contain a `box_data/` dir, but its `registry.yaml`
`workset:` identity is the more specific one. The NAMED arm imports the unregistered workset
root (a `registry.yaml` carrying a `workset:` identity whose name is not in the registry) and
then re-runs the standard workset check, which is what actually resolves it. The STANDALONE arm is
presence-only since D4
(the former `box.mode == "standalone"` field read is DROPPED); a bare `box_data/` directory is not
enough — the root `workset.yaml` must be present too.

```python
def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None
```
Check whether *resolved_dir* is inside a registered workset.

Returns a `DetectionResult` if found, `None` otherwise. Checks `workspaces/` first (specific
project), then the workset root itself (inside workset but not necessarily a project workspace). The
`workspaces` location is the RESOLVED `workset.workspaces` (repoint honored; default
`@meta.workset.path/workspaces` — §3.3: real and USED).

```python
def _workset_box_name_for_workspace(ws_root: Path, workspace: str) -> str | None
```
Reverse-look-up *workspace* in *ws_root*'s per-workset `boxes:` membership.

Returns the registered box name (resolved-path aware) or `None`. Mirrors
`_register_workset_box_membership`'s registry-path resolution so both the write (Guard 1) and this
reverse-lookup (Guard 2) consult the SAME per-workset `boxes:` registry — the one `list` /
`box_resolve` read — closing the drift where a global-name miss let Guard 1 fire mid-create.

```python
def _workset_box_workspace_for_name(ws_root: Path, box_name: str) -> str | None
```
Forward-look-up *box_name* in *ws_root*'s per-workset `boxes:` membership.

Returns the REGISTERED workspace path (the `boxes:` entry VALUE) or `None`. The forward twin of
`_workset_box_name_for_workspace`, with the same registry-path resolution. The registered path is
authoritative (D1b/D3-auth) *wherever a composition epoch put it* — a member registered before a
`workset.workspaces` repoint keeps resolving to its recorded workspace, never re-derived from the
CURRENT composition (bifrost A0).

```python
def _register_workset_box_membership(
    ws_root: Path, box_name: str, workspace: Path,
) -> None
```
Register *box_name* → *workspace* in *ws_root*'s per-workset registry.

The P5a dual-register helper (D1/D3-auth): resolves the workset's `workset.registry` path (honoring
a repoint via its `workset.yaml`) and records the box's membership. Idempotent —
`register_workset_box` overwrites a moved box's stored path. Used for both NAMED worksets (`ws_root`
= the workset root) and the PRIMARY workset (`ws_root` = `std.primary_workset` — NON-EXCEPTIONAL per
D0/D1).

```python
def _unregister_workset_box_membership(ws_root: Path, box_name: str) -> None
```
Drop *box_name* from *ws_root*'s per-workset registry (compensating action).

The inverse of `_register_workset_box_membership`: resolves the workset's `workset.registry` path
(honoring a repoint via its `workset.yaml`) and removes the box's `boxes:` membership. Idempotent —
`unregister_workset_box` is a no-op when the file/entry is absent. Used to unwind a connect register
and to drop a disconnected external box's D10 connection record.

## The PRIMARY-box name registry

The primary per-workset `boxes:` membership is the SOLE store of default-mode (PRIMARY) box names
since the global `projects:` section retired (clean split, 2026-07-08). Membership is name →
EXTERNAL-workspace path in `@config.primary_workset/registry.yaml` (spec L514, via
`kanibako.project.workset_registry`).

These helpers mirror the retired `names.py` project-name API
(`pick`/`assign`/`register`/`unregister`/reverse-lookup) but on the primary membership, so callers
re-route store-for-store. The name-collision DOMAIN is primary membership names ∪ global workset
names (semantics preserved from the old `projects ∪ worksets` domain); the `$HOME` guard and
auto-suffix numbering are carried verbatim. Every function takes the primary workset root + the
global registry file explicitly (the same no-hidden-state convention as
`_register_workset_box_membership`).

```python
def load_primary_boxes(primary_workset: Path) -> dict[str, str]
```
Return the PRIMARY box membership as `{box_name: workspace_path_str}`.

Reverse of the old `read_names(...)['projects']` read: the primary per-workset `boxes:` membership
is the sole store now. *primary_workset* is `std.primary_workset`.

```python
def primary_box_name_for_workspace(primary_workset: Path, workspace: str) -> str | None
```
Return the PRIMARY box name registered for *workspace*, or `None`.

Resolved-path aware (via `_workset_box_name_for_workspace`), the membership replacement for the old
`lookup_by_path` projects-arm.

```python
def _primary_name_domain(primary_workset: Path, registry: Path) -> set[str]
```
The PRIMARY-box name collision domain: primary membership ∪ global worksets.

Preserves the retired `names.py` auto-name pair's cross-section domain (`projects ∪ worksets`) with
`projects` replaced by the primary membership. *registry* is `std.registry` (for the global
`worksets:` names); *primary_workset* is `std.primary_workset`.

```python
def check_primary_box_name_free(
    primary_workset: Path, registry: Path, name: str, workspace: str, *, force: bool = False,
) -> None
```
Raise `ProjectError` if *name* collides in the PRIMARY-box domain.

Mirrors `names.register_name`'s pre-write guards without writing: the `$HOME` guard on *workspace*
and the name-collision check across the PRIMARY-box domain (primary membership ∪ global worksets).
Used at the `--name` registration edge so a collision fails BEFORE the box dir is materialized.

Per-kind name policy (Jei 2026-07-08): box and workset names are SEPARATE namespaces. The collision
splits into two arms:

* **SAME-KIND** — *name* already names another PRIMARY box (primary membership). Unconditional: two
  primary boxes can NEVER share a name; *force* never bypasses it.
* **CROSS-KIND** — *name* is a global WORKSET name. A bare name that is both a box and a workset
  resolves deterministically to the box (shadowing the workset in bare-name lookups), so this
  refuses UNLESS *force*.

```python
def pick_primary_box_name(
    primary_workset: Path, registry: Path, workspace: str, boxes_dir: Path | None = None,
) -> str
```
Pick a collision-free PRIMARY box name from *workspace*'s basename (no write).

The membership-domain counterpart of the retired `names.py` picker: collisions append a number
(`name`, `name2`, ...); a candidate is rejected when it is in the PRIMARY-box domain (primary
membership ∪ global worksets) OR — when *boxes_dir* is supplied — when `boxes_dir/<candidate>`
already exists (the interrupted-create reservation guard). Performs no mutation.

```python
def register_primary_box_name(
    primary_workset: Path, registry: Path, name: str, workspace: Path | str, *, force: bool = False,
) -> None
```
Register *name* → *workspace* in the PRIMARY membership (with guards).

The membership counterpart of `names.register_name`: the `$HOME` guard + the PRIMARY-box-domain
name-collision check, then the actual write (which also enforces `register_workset_box`'s
one-box-per-workspace-path invariant). *force* is forwarded to `check_primary_box_name_free`: it
bypasses the CROSS-KIND (workset-name) refusal only — the SAME-KIND (another primary box) arm stays
unconditional.

```python
def register_primary_box_name_if_absent(
    primary_workset: Path, registry: Path, name: str, workspace: Path | str, *, force: bool = False,
) -> None
```
Idempotent `register_primary_box_name` for deferred-create recovery.

A no-op when *name* already maps to the SAME *workspace* in the primary membership (the
register→clear-entry recovery re-entry); any other state goes through `register_primary_box_name`
(which raises on a genuine collision). Mirrors `names.register_name_if_absent`. *force* is forwarded
(bypasses only the CROSS-KIND workset-name refusal).

```python
def assign_primary_box_name(
    primary_workset: Path, registry: Path, workspace: Path | str, boxes_dir: Path | None = None,
) -> str
```
Auto-assign + register a PRIMARY box name from *workspace*'s basename.

Equivalent to `pick_primary_box_name` followed by `register_primary_box_name` — the membership
counterpart of the retired `names.py` auto-namer.

```python
def unregister_primary_box_name(primary_workset: Path, name: str) -> None
```
Drop *name* from the PRIMARY membership — the membership counterpart of `unregister_name`. Thin
delegation to `_unregister_workset_box_membership`; idempotent.

```python
def resolve_workset_project(
    ws: WorksetSpec, project_name: str, std: StandardPaths, config: BootstrapConfig, *,
    initialize: bool = False, enable_vault: bool | None = None,
) -> ProjectPaths
```
Resolve per-project paths for a project inside a NAMED workset.

*ws* is a lightweight `WorksetSpec` describing the workset's name, root, auth mode, and registered
project names. Callers holding a full `Workset` object pass `WorksetSpec.from_workset(ws)`.

Phase 5: no layout axis — shell under the per-project box dir, vault under the workset's two
RESOLVED vault arms (`<ws.vault_ro_dir>/<name>`, `<ws.vault_rw_dir>/<name>`). Paths are name-based,
not hash-based. Raises `WorksetError` if *project_name* is not registered in *ws*.

### Workspace override (P7/D10)

The per-workset registry's `boxes:` membership is the SOLE authoritative name → workspace store
(D1b/D3-auth): when *project_name* is registered, its REGISTERED path IS the workspace — the
EXTERNAL dir for a connected box, `workspaces/<name>` for an in-tree box, and the OLD-composition
path for a member registered before a `workset.workspaces` repoint (bifrost A0: re-deriving that
member from the CURRENT composition strands it). An UNREGISTERED member (an in-tree connect before
its first start, or a fresh `initialize` create) falls back to the composed default, with the
`box_resolve` identity derivation preserved for any residual override.

### B2b and `enable_vault` in the NAMED resolver

B2b (Option A, Jei-ruled): the per-box `meta["shell"]` / `["vault_*"]` custom-path OVERRIDE is
DROPPED (mirrors the PRIMARY path) — home/vault are SOLELY the spec-derived default location,
customized via the `box.bindings` cascade. The workspace override above (an EXTERNAL-connected live
dir) is a SEPARATE concern and STAYS.

`enable_vault` (P5a): explicit param wins; else `box.enable_vault` (absent everywhere ⇒ True)
resolved through the CASCADE by `config.resolve_box_enable_vault` — `base < system < workset < box`.
⚑ The WORKSET tier is REQUIRED: `workset create --no-vault` writes the key there, where spec §0's
containment rule makes it an OVERRIDABLE default for the workset's boxes (the contained scope still
wins). Without it the flag is a silent no-op for every named box.

⚑⚑ **IT WAS `read_box_enable_vault(box_tier, default_from=workset_tier)` UNTIL 2026-08-29** — two
files, so `base` and `system` were dropped silently. A `kanibako system set box.enable_vault=false`
returned 0, persisted to `global/settings.yaml`, was echoed back by `system get`, and every box
still came up with the vault created and mounted.

⚑ **The resolved value is not the written one.** `actual_vault_enabled` resolves through the
cascade and drives the mounts; `box_authored_vault` reads the box tier ALONE (via
`read_box_enable_vault`, which no longer takes a fallback at all) and is the only one persisted,
since spec `:868` keeps the key sparse — persisting an inherited default would PIN it, turning an
overridable workset default into a box override later workset edits cannot reach.

### Sparse create + dual-register on the NAMED path

Sparse create (P8b/Option A): NO `project:`/`resolved:` identity — the box's name lives in the
workset's per-workset registry (the `boxes:` entry, which `box_resolve` reads) and its workspace
override in that same registry. Only a NON-default BOX-AUTHORED `box.enable_vault` is persisted.

P5a dual-register: record membership in the workset's per-workset registry (name → workspace) — the
SOLE on-disk identity record now that sparse create writes no `project:` entry. Sourced from the
resolved *project_path* so an external-connect override seeds the registry with the external dir
(the D10/P7 home for that record). Idempotent — overwrites a moved box's path.

### J2 connect self-heal

Symmetry with the import path: reaching that point means *project_name* IS a registered member of
*ws* (the membership guard raised otherwise), so a lingering `connect` entry for this box is a
register→clear-window stale entry — the box is already registered, so recovery == clear the entry
(NO re-register, NO seed). This restores "registered ⟹ no pending entry" eventually-on-resolve for
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
    std: StandardPaths, config: BootstrapConfig,
) -> list[tuple[Path, Path | None]]
```
Return `(metadata_path, project_path | None)` for every known project.

*project_path* is the box's workspace, sourced from the PRIMARY per-workset registry (`name →
workspace`). A box with no registry entry yields `None` (an un-registered / half-created box has no
resolvable workspace).

P8a: box → workspace comes SOLELY from the PRIMARY per-workset registry, the new-model source seeded
at create (`_register_new_box`) — the SAME source `box_resolve` reads. A box cannot be resolved from
its box DIR via `box_resolve.resolve_box_identity` (the registry is keyed by workspace PATH, not the
box dir), so the PRIMARY registry is read here directly (identical data). The transitional
`read_project_meta` (`settings.yaml` `resolved.workspace`) + legacy `project-path.txt` breadcrumb
fallbacks are DROPPED (P8a): a box absent from the registry has no workspace → `None`. (These are
PRIMARY boxes: `std.boxes` == `@config.primary_workset/boxes`, so the PRIMARY registry is the home.)

```python
def iter_workset_projects(
    std: StandardPaths, config: BootstrapConfig,
) -> list[tuple[str, _WorksetLike, list[tuple[str, str]]]]
```
Return workset project info for all registered worksets.

Each entry is `(workset_name, workset, [(project_name, status), ...])`. The workset object is a
concrete `kanibako.project.workset.Workset` typed structurally as `_WorksetLike` (so `paths.py` need
not import `workset`). Status is `"ok"`, `"missing"` (no workspace), or `"no-data"` (no project
dir).

The per-workset `boxes:` membership is loaded ONCE per workset — the authoritative name → workspace
store. Workspace presence is checked at the REGISTERED path when the member is registered: a member
registered under an OLD composition (before a `workset.workspaces` repoint) otherwise reads as
"missing" and vanishes from `list` (bifrost A0). An unregistered member falls back to the composed
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
`@meta.workset.path/workspaces` — §3.3: real and USED).

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

Uses `detect_project_mode` to walk ancestor directories and find the project root. The resolved
*project_root* (not the raw CWD) is passed to the appropriate resolver.

*register* (B3) is forwarded to the PRIMARY/STANDALONE resolvers so the `start` auto-create path can
defer registration until after the home seed (the NAMED branch never writes the name registry on
create, so the flag is a no-op there). Defaults True.

*name_override* is forwarded to the PRIMARY resolver only, which is the sole mode whose box name is
not derivable from the tree itself: a STANDALONE box carries its identity in its own root
`workset.yaml`, and a NAMED box takes its name from its workspace directory. `box extract --name`
is the caller that needs it (re-materializing an archived box under a chosen name).

### The CLI front door

A bare token (no path separator) that doesn't exist in cwd may be a registered project/workset name.
`resolve_project` also does this lookup, but `resolve_any_project` must do it FIRST — otherwise
`Path(raw).resolve()` path-ifies the name before `detect_project_mode` sees it.

**On a `ProjectError` miss:** a bare token that names NO known project/workset/workset-member box
AND has no path of that name on disk. Refuse to path-ify it to a nonexistent cwd-relative path —
doing so would resolve to an UNREGISTERED box with an empty name, minting a phantom
`kanibako-<hash>` container that no `list`/`ps` row corresponds to. Surface an honest error on the
READ path (`initialize` is False for stop/box/diagnose/…). The CREATE path (`initialize=True`) still
path-ifies so a new box can be materialized at the resolved location; and an existing-path or
qualified (`ws/proj`) spec never reaches here (guarded by the `"/" not in raw and not exists`
condition).

**On a hit:** `raw` is updated for BOTH kinds. A bare workset name resolves to the workset ROOT,
which `detect_project_mode` must see — without this, the name path-ifies to `cwd/<name>` and
resolution fails with a misleading "does not exist". A workset is not a single box, so it is still
rejected below — but with a clear, actionable message rather than the generic "inside a workset, cd
to a project" error: `box`/diagnose operate on a single project box, and a workset may contain zero
or many; there is no unambiguous representative.

**Qualified `workset/project` addressing:** a token containing a separator that is NOT an existing
path may be a qualified name (the form the bare-workset rejection suggests). It is resolved to the
project's workspace so `detect_project_mode` sees a single project box. A real relative path like
`src/foo` that happens not to exist is left untouched — it falls through to the path-ify behavior
and fails exactly as before.

```python
def resolve_box_target(
    std: StandardPaths, config: BootstrapConfig, value: str | None = None, *,
    initialize: bool = False, register: bool = True, warn: bool = True,
) -> ProjectPaths
```
Resolve a `--box` value (a box NAME or a path) to its `ProjectPaths`.

The single path-or-name resolver behind the `--box` selector and the
`start`/`shell`/`refresh`/`workset disconnect` targeting (§Design 8). Returns the SAME `ProjectPaths`
the positional-`project` path returns, so callers swap cleanly.

*value* is EITHER a box NAME or a filesystem path. **Box NAME takes precedence in ambiguous cases** —
names cannot contain `/` so true ambiguity is rare (a bare token that is both a registered name and
a relative directory in cwd resolves to the NAME). Resolution order:

1. **NAME first.** A bare token (no path separator) is tried as a name:
   * a **standalone box name** in `registry.standalone` (the canonical-id domain — closes the gap
     that `resolve_any_project` does NOT cover, since `resolve_name` only indexes the
     projects/worksets sections). Box names are lowercase (R2), so the query is case-folded for the
     lookup;
   * else the registry projects/worksets names + qualified `ws/project` names, which
     `resolve_any_project` already resolves.
2. **PATH otherwise.** Anything that is not a name (contains `/`, or no name matched) is resolved as
   a filesystem path via `resolve_any_project` — reusing the existing path-resolution + ancestor-walk
   discovery (`detect_project_mode`). No detection is reimplemented here.

A pre-existing box whose name does not satisfy the §Design 8 blocklist still resolves (the matcher
is structural, not policy-gated); FLAGGING that is the caller's job via
`kanibako.launch.box_identity.is_valid_box_name` — this resolver does not reject on name shape.

`None` / empty *value* resolves the cwd box (delegates to `resolve_any_project`), matching the
positional-`project` default.

*register* (B3) is forwarded to the PRIMARY/STANDALONE resolvers; `start` passes `register=False` so
an auto-created box defers registration until after its home seed (journal entry → seed → register →
clear-entry). Defaults True.

*warn* gates the non-conforming-name FLAG (`_flag_nonconforming`). A NON-materialising PROBE
(`initialize=False`) run purely to read a box's paths ahead of a second materialising resolve passes
`warn=False` so the name flag fires exactly ONCE (on the real resolve), never doubled. Defaults True.

```python
def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths
```
Warn (do NOT reject) when a resolved box's name violates the blocklist.

Pre-existing boxes created before the §Design 8 box-name constraint still resolve (the
canonical-id/registry matchers are structural, not policy-gated); but a non-conforming name is
FLAGGED on use so the drift is visible. Returns *proj* unchanged.

```python
def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths
```
Advisory (never fatal): flag a standalone box whose stored `workset.kuid` is a NON-sentinel value
that fails the kuid parity/charset check.

Fires ONLY when ALL hold (spec D9, INVERTED 2026-07-04):

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
whose vault directory is absent still resolves and launches; the missing vault is merely FLAGGED
(`warning: cannot find vault`). A box with `enable_vault` OFF expects no vault, so nothing is warned
(the `enable_vault` guard is load-bearing: without it every vault-disabled box would warn). Fires at
resolve time alongside the other `_flag` advisories. Returns *proj* unchanged.

```python
def establish_standalone(
    std: StandardPaths, root: Path, *, enable_vault: bool, name: str = "", register: bool = True,
) -> tuple[str, Path, Path, Path]
```
Establish a standalone box at *root*: identity + meta + registration.

The single shared core behind all three standalone paths (`create --standalone`, `convert
--standalone`, `duplicate --standalone`). It

1. derives the box identity via `box_identity.resolve_standalone_name` — a fresh canonical
   `<kuid>_<leaf>` (whole-name collision regen vs `registry.standalone`) when *name* is empty,
   otherwise honoring the supplied (lowercased) `--name`: a verbatim canonical id if free (else
   refuse), or a fresh prefix over the supplied string as the leaf;
2. writes the SPARSE settings, each key AT ITS OWN SCOPE'S TIER (M-8): the workset-scope
   `workset.kuid` into the ROOT `<root>/workset.yaml` (which MATERIALIZES that file — half the
   standalone detection marker, `system-design-1.8.0.md` § "Detection & import"), and a NON-default
   box-scope `box.enable_vault` into the BOX tier `<root>/box_data/box.yaml` — the SAME file
   `config set box.*` writes, so create and set can never disagree. A default-vault box therefore
   writes NO box-tier file at all, which is the spec's "ABSENT BY DEFAULT" (§2c + §4 STANDALONE
   tree). NO `project:`/`resolved:` identity is written — the name/mode/workspace derive from
   `registry.standalone` + the live kuid;
3. registers the box in `registry.standalone` (`box_name` → *root*).

*root* is the standalone project dir. The box-data dir (`root/box_data`) must already exist (each
caller creates/copies it before calling). Returns `(box_name, shell_path, vault_ro, vault_rw)` so
callers can build their result state without recomputing the table. Callers own their own
surrounding concerns (file copies, unwind registration, old-name unregister) — only the
identity/meta/register core lives here.

*register* (B3 interrupted-create journal): when False the identity is still resolved and the meta
file written, but the box is NOT registered in `registry.standalone` — the caller defers
registration until AFTER the home seed (journal entry → seed → register → clear-entry), so a crash
mid-seed leaves an UNregistered box that recovery resolves by its on-disk root. Defaults True (the
convert/duplicate lifecycle callers register inline, unchanged).

⚑ The ROOT `workset.yaml` (the standalone marker, alongside `box_data/`) is MATERIALIZED
unconditionally by the `workset.kuid` write, so moving the box key out to the box tier does NOT cost
the file detection needs.

The kuid is persisted as the settable `workset.kuid` key (P6d) via the SAME keystore sparse-write
engine `config set` uses — memory `[[settings-must-map-to-keystore-key]]`. The kuid IS the name's
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

Phase 5d/Part 3 (drift H+I): no layout axis. The project *root* (the runtime dir) is the standalone
workset root and holds, in fixed positions: `workset.yaml` (the workset tier, AT THE ROOT —
`metadata_path`), a `workspace/` subdir (the live workspace → `~/workspace` — the `project_path`;
the resolved `workset.workspaces`, default `@meta.workset.path/workspace`), a `box_data/` marker dir
holding `home/` + the `<box>.jsonl` helper log, and `vault/{ro,rw}/`. The box identity is
`<kuid>_<sanitized leaf>` (generated + registered in `registry.standalone` at create time; reused
from the stored meta after).

*register* (B3 interrupted-create marker): forwarded to `establish_standalone`; when False on a NEW
box the meta is written and `is_new` set but the box is NOT registered, so the caller can register
after the home seed. Defaults True (existing callers unchanged).

### Standalone resolution details

The hash + identity key off the ROOT (the standalone workset root), which is stable; the workspace
subdir is the bind source, not the identity.

`project_path` is the resolved `workset.workspaces` (ruled 10, 2026-08-02): the STANDALONE default
is `@meta.workset.path/workspace` == the `workspace/` subdir, and a set `workset: {workspaces: …}`
in the ROOT `workset.yaml` repoints it ("changeable from workset level", spec §2e).

The mode-aware tier pair comes from the ONE derivation (M-8): the BOX tier is
`box_data/box.yaml` (absent by default) and the WORKSET tier is the ROOT `workset.yaml` — the
file detection reads (`system-design-1.8.0.md` § "Detection & import") and where `workset.kuid`
lives.

⚑ STANDALONE paths are derived from the (current) root, never the stored absolutes: the DEFAULT
formulas are all `@`-anchored to the root (`@meta.workset.path/…`), so a default-shaped tree is
drop-in portable BY CONSTRUCTION — a moved/imported tree resolves against its new location. A stored
ABSOLUTE repoint (e.g. `workset.workspaces`) is the user's own choice and travels as written. The
`resolved.*` section in `settings.yaml` is advisory only (BUG#1 fix); home/vault always live at the
fixed `box_data/home` + `<root>/vault/{ro,rw}` positions.

`enable_vault` (P5a): explicit param wins; else the box-scope `box.enable_vault` resolved through
the CASCADE by `config.resolve_box_enable_vault` — `base < system < workset < box`, the BOX tier
being `box_data/box.yaml` and the WORKSET tier the ROOT `workset.yaml`. ⚑ The workset level is LIVE
DESIGN, not a compat path — keyspec §2c's STANDALONE block declares it ("Box values … still resolve
from the workset tier … as downward defaults when no box file exists"). It is ALSO why a pre-P2
standalone box, whose value was written to the root file, keeps working with no migration (M-8) — a
consequence of the rule, never its reason. ALL THREE resolvers go through the same cascade
(2026-08-29; they hand-opened two files each before that, and so could not see a base- or
system-tier value); standalone alone then PERSISTS the RESOLVED value — that write IS the M-8
migration, landing the root file's value at the box tier.

**Box identity name (P8a):** sourced from `box_resolve` for a MATERIALIZED standalone (`box_data/` +
`workset.yaml` present — the same gate `standalone_settings_present` uses). `box_resolve` composes
the name LIVE (P6d) as `<stored workset.kuid>_<live leaf>` — the kuid is the STABLE stored prefix
(from the box's OWN `workset.yaml`, design D6) and the leaf is re-derived from the CURRENT root
basename, so a moved standalone tree keeps its kuid identity while the leaf tracks the new dir (spec
2026-07-04); a pre-kuid box (no `workset.kuid` ⇒ SENTINEL) falls back to the registered
`standalone:` key, else the dir leaf. A not-yet-materialized root (no `box_data/`) yields `""` (the
create block assigns it authoritatively via `establish_standalone`). Replaces the transitional
`read_project_meta` `project.name` read.

The user's explicit `--name` is only meaningful when establishing a NEW box; it is ignored once the
box exists, since the stored identity is authoritative.

**The create gate:** not-yet-initialized iff the `box_data/` marker dir is absent (the root itself
always exists — it is the runtime dir). The requested `--name` is PRE-FLIGHTED BEFORE any FS
mutation so a doomed create (a verbatim-canonical name already taken) refuses up front rather than
leaving an orphaned half-created `box_data/` + `vault/` tree (BUG-A). `establish_standalone`
re-resolves the name authoritatively; the pre-flight only surfaces the refusable collision early.
The init block is only reached when no meta exists, so the identity is resolved fresh from the
user-supplied `--name` (empty → fresh canonical).

```python
def _init_standalone_project(
    std: StandardPaths, metadata_path: Path, shell_path: Path,
    vault_ro_path: Path, vault_rw_path: Path, project_path: Path, *,
    enable_vault: bool = True,
) -> None
```
First-time standalone project setup: all state inside project dir.

Unlike workset init, this *does* create vault directories and a `.gitignore` (vault lives inside the
user's project, likely a git repo). ⚑ It passes `metadata_path.parent` as `_init_common`'s
`vault_root` — `metadata_path` here is `box_data/`, so its parent is the standalone ROOT, which is
also the degenerate workset root that owns the `vault/` skeleton. A `workset.vault_ro` repointed
OUT of that root still gets its directory created; it just gets no `.gitignore` written beside it.

Credential copy is handled separately by `target.init_home()` in `start.py`, after template
application.

*metadata_path* is the `box_data/` marker dir (home + helper log); *project_path* is the
`workspace/` subdir (the live workspace), which is created here — it is a SUBDIR of the root (drift
H) — so the bind source exists.
