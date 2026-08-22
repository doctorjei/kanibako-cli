# Worksets — the named group of projects, its data model and its persistence

⚠️ **This mirror is COMPLETE for displaced prose** (relocation pass, 2026-08-18). A symbol absent
below carried nothing worth displacing — never "does not exist". The source keeps one-line
descriptors and `⚑` markers; the reasons live here.

A *workset* is a named group of projects whose persistent state lives under a single root directory
chosen by the user. This module is two things at once: the in-memory model (`Workset`,
`WorksetProject`), and the CRUD that writes it to disk and to the global registry. It is also one
half of a documented import cycle, and several of its public mutators touch more than one file — so
most of the notes below are about ORDER and about which of two similar-looking spellings is the real
one.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §2c (per-mode default tables, the workset-root
layout), §3.3 (the `workset.*` dir keys are "real and USED — not hard-coded"), §5 (seed at
registration). ⚑ **The spec is the LIVE authority; read it first.**

## 🛑 The workset-root layout — corrected against the spec

The module docstring carried an ASCII layout tree. A layout diagram is a claim about the world that
nothing tests, and this one had drifted. **Corrected form, every line checked against the code that
creates or resolves it:**

```
{root}/
    registry.yaml           ← workset IDENTITY (`workset:`) + box membership (`boxes:`)
                              + project records (`projects:`)  (`workset.registry`)
    settings.yaml           ← the workset's cascade settings — SETTINGS ONLY, and
                              OPTIONAL: a fresh root does not have one
    boxes/{name}/           ← per-box metadata dir (`meta.box.path`)
        home/               ← agent home (mounted as /home/agent)
        settings.yaml       ← the BOX cascade tier (`meta.box.settings`)
        .kanibako.lock      ← concurrency lock
    workspaces/{name}/      ← per-box workspace (source tree) — the DEFAULT composition only
    vault/ro/{name}/        ← per-box read-only vault
    vault/rw/{name}/        ← per-box read-write vault
    logs/{name}.jsonl       ← per-box helper message log
    auth/                   ← the per-workset credential dir (`workset.auth.path`)
    channels/               ← workset-local channels (`workset.channelroot`)
```

Three notes the diagram cannot carry:

* ⚑ **`create_workset` makes only FOUR DIRS plus `registry.yaml`** — `boxes/`, the resolved
  workspaces dir, `vault/`, `logs/`, and the registry file that carries the identity. `settings.yaml`,
  `auth/` and `channels/` are created lazily by the paths that own them (a `workset set`/`share add`
  write, the plugin's auth dir, the launch-path channel guarantee-create). Their absence from a fresh
  root is correct, not a bug — and for `settings.yaml` it is the NORMAL case.
* ⚑ **`workspaces/` is the DEFAULT, not the layout.** `workset.workspaces` is a repointable key
  (see below), and the PRIMARY per-mode default is `<None>` — primary boxes have EXTERNAL
  workspaces. Reading `<root>/workspaces` as a fixed fact is exactly the mistake the resolver exists
  to prevent.
* ⚑ **`boxes/` and `logs/` are ALSO spec keys** (`workset.boxes` = `@meta.workset.path/boxes`,
  `workset.logs` = `@meta.workset.path/logs`) and this module HARD-CODES both — `BOXES_DIR_NAME`
  and the `"logs"` literal in `Workset.logs_dir`. That is a known delta against §3.3's
  "real and USED — not hard-coded" ruling, which was discharged for `workspaces`/`channelroot` and
  not for these two. Recorded here so nobody re-derives it; changing it is not a doc-pass edit.

## ⚑⚑ The import cycle — this module is one half of it

`project/workset.py` imports `StandardPaths` from `settings/paths.py` **at module scope**. That is
the FORWARD edge. The reverse edge is the cycle-breaker: `settings/paths.py` imports `project.names`
at module scope but **DEFERS `workset`, `workset_registry`, `registry_store` and `import_reconcile`
into function bodies**, and keeps a decoupled primitive workset view rather than the concrete
`Workset`.

**Consequences that are easy to break:**

* A module-scope `from kanibako.project import workset` added to `settings/paths.py` reinstates the
  cycle. The deferrals there are load-bearing, and it is precisely why every package `__init__.py`
  in the tree is import-free — a facade would drag all five in eagerly and turn a real `ImportError`
  guard into silence.
* The function-body `from kanibako.settings.paths import …` statements scattered through THIS file
  (`load_primary_boxes`, `warn_legacy_primary_settings`, `_register_workset_box_membership`,
  `_unregister_workset_box_membership`) are **not** cycle-motivated: `settings.paths` is already
  fully loaded by the module-scope `StandardPaths` import above them. They are historical. Do not
  "fix" the module-scope import by pushing it into a function on the theory that it closes a cycle —
  it does not, and the deferral that matters lives on the other side.
* `kanibako.launch.box_resolve` imports `resolve_workset_workspaces` from here inside a function
  body, so the resolver below is on a deferred edge in the other direction too.

## Resolved workset dir keys (`workset.workspaces` / `workset.channelroot`)

These mirror `kanibako.project.workset_registry.resolve_workset_registry_path` — the established
seam for contexts WITHOUT a launch snapshot (registry lookups, mode detection, workset CRUD): a pure
function of the workset root + its settings document, honoring the routed nested slot
`workset: {<leaf>: …}` (the same location a settings-file edit — or a future
`config set workset workset.<leaf>=…` — writes) and falling back to the spec default.

**Repoint semantics MATCH the registry resolver's:** `~` expands; an absolute path is used as-is; a
relative repoint anchors under the workset root (deterministic, like the sibling path keys).

The three leaf constants exist so the spec's per-mode default formula `@meta.workset.path/<leaf>` is
spelled ONCE. §3.3's ruling is that these keys are "real and USED — not hard-coded"; the per-mode
default TABLE is the source, never a second literal at a consumer site.

* **`workset.workspaces`** — spec §2c NAMED default `@meta.workset.path/workspaces`. PRIMARY
  declares `<None>` (primary boxes have EXTERNAL workspaces), but the synthesized default workset
  keeps composing the same leaf — that is today's behavior, recorded as such. STANDALONE's default
  is the SINGULAR `@meta.workset.path/workspace` arm, which is what the `standalone=` flag on
  `resolve_workset_workspaces` selects (ruled 10, 2026-08-02).
* **`workset.channelroot`** — spec §2c ALL-WORKSETS default `@meta.workset.path/channels`.
  Standalone has no workset channels (the key is `<None>` there), so callers gate on mode before
  calling the resolver.

⚑ **The repoint SLOT is the same key in every mode** — only the default FORMULA varies. A mode flag
selects a default, never a different storage location.

## Failure-consistency: the LIFO unwind stack

Several public mutators touch more than one file — a root `registry.yaml` plus the global registry,
or a symlink + a per-workset `boxes:` connection record + the durable identity write. Individual
writes are torn-file-safe (atomic temp + `os.replace`, via `config_io.dump_doc`), but a crash
*between* steps could strand a half-applied cross-file state: the registry says X while disk says Y,
an orphan symlink, or an external path locked out by a dangling connection record.

The stack mirrors the pattern in `commands/box/_lifecycle.py::_Unwind`: each forward step pushes a
compensating action; on any exception they run in reverse (best-effort, secondary failures
swallowed) and the original exception re-raises, leaving the op either fully applied or fully rolled
back. The sequences are short — two pushes for `create_workset`, up to seven for `add_project` — so
this stays deliberately small rather than a generic framework. ⚑ It is the SIMPLER of the two
`_Unwind`s: no `on_success` list and no `finish`.

## ⚑⚑ Identity is REGISTRY-BORNE; the settings file is settings only

**system-design §Detect: "Identity and membership are REGISTRY-BORNE at every level — a workset's
name, `created` and membership live in `registry.yaml` exactly as a box's `meta.box.name` does; a
settings file carries SETTINGS ONLY, is sparse, and MAY BE ABSENT."** Worksets are the level that
took longest to follow it. The identity is now the `workset:` table of the root `registry.yaml`,
beside the `boxes:` membership and the `projects:` records that moved there with it.

Three consequences, each of which someone will otherwise re-derive:

* **A workset root need not have a `settings.yaml` at all.** `create_workset` writes none. Every
  read path must therefore tolerate its absence rather than raising — `load_workset_settings_doc`
  returns `None`, and `config_io.load_doc` returns `{}`.
* **The read-the-file-first guard is gone from the identity write.** It existed because identity
  shared a file with user settings, and an identity writer that replaced the whole document would
  silently drop them. `workset_registry.save_workset_record` still does a read-modify-write, but for
  a DIFFERENT reason: `registry.yaml`'s `boxes:` section has its own independent writers.
* **The settings file is no longer a detection input.** `read_workset_identity` reads
  `registry.yaml`. It still opens `settings.yaml` first — but only to refuse the retired shape, and
  to resolve a `workset.registry` repoint.

Same filename as `BOX_META_FILE` (`settings/config.py`, also `"settings.yaml"`) **by design** — both
are the "settings.yaml at the entity's root" convention.

## The global registry

The global registry is the `worksets` SECTION of the single registry document located by
`config.registry` (spec §1: `@config.data/global/registry.yaml`), reached as `StandardPaths.registry`.

⚑ **There is ONE section and ONE file.** This is the SAME `worksets` section the human-name index
(`project/names.py`) reads and writes — the former duplicate `workset_roots` section was collapsed
onto it (2026-06-29f), and the former separate `names.yaml` / `worksets.yaml` FILES are no longer
read or written at all (`project/registry_store.py` module docstring; asserted by
`tests/test_project/test_workset.py`). `register_name` / `unregister_name` are the SOLE WRITERS;
`_load_registry` is the read helper serving discovery / list / lookup.

That collapse is why `create_workset` has a single registration step rather than two, and why
`delete_workset` has ONE entry to remove with no list-vs-index split to heal.

⚑ On-disk metadata is AUTHORITATIVE; this registry is a derived, rebuildable INDEX (spec) — never
the sole source of truth.

## Connected (external) boxes — D10, per-workset, no global index

An externally-connected box is a NAMED workset's per-workset `boxes:` entry whose registered PATH is
the EXTERNAL directory. **The per-workset registries collectively ARE the reverse index** (D10
enumerate-and-scan); resolution goes through `launch/box_resolve.find_connected_external_box`.

The former global `connected:` section — and `_find_connected_project` / `_load_connected` /
`_write_connected` — are GONE. (Verified absent 2026-08-18; the only surviving mentions are
`box_resolve`'s own "this REPLACES …" note and three test docstrings describing the old return
shape.)

⚑ **Sparse create (P8b / Option A): connecting an external box writes NO `settings.yaml`.** The
connection record IS the per-workset `boxes:` entry (`name → EXTERNAL path`), and `box_resolve` reads
that entry for BOTH identity and the workspace override. There is no workspace-override file, and no
identity block, anywhere on the connect path.

## Constants

```python
WORKSET_SETTINGS_FILE = "settings.yaml"
```
The workset-tier cascade settings file at the workset root — **SETTINGS ONLY, and OPTIONAL**. See
**Identity is REGISTRY-BORNE**, above. (Renamed from `WORKSET_META_FILE` when the identity left it:
the old name asserted a role the file no longer has.)

```python
BOXES_DIR_NAME = "boxes"
```
The BOX-tree leaf under a workset root. Named because THREE sites need it and only one of them has a
`Workset` instance to ask:

* `Workset.projects_dir` — the one that does;
* `create_workset`, building the skeleton before any `Workset` exists;
* `delete_workset`, which holds a bare root `Path` from the registry and must clear each member box
  tree through the unshare-aware deleter before the plain `rmtree`.

*(The pre-relocation comment said "two places" and named the third as `remove_workset` — a function
that has never existed under that name. See the false-claim list.)*

```python
_WORKSPACES_LEAF = "workspaces"
_STANDALONE_WORKSPACE_LEAF = "workspace"
_CHANNELROOT_LEAF = "channels"
```
The per-mode default leaves, spelled once. See **Resolved workset dir keys**.

```python
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"
```
Identity of the synthesized "default" workset (the group of default-mode projects). ⚑ This workset
is VIRTUAL — it is never written to disk.

```python
RESERVED_WORKSET_NAMES = frozenset({DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS, "__PRIMARY__", "__STANDALONE__"})
```
Sentinel names reserved by the three-mode model (spec §2c): they address the PRIMARY and STANDALONE
channel partitions and must never be a user-chosen NAMED-workset name. The legacy
`default` / `__default__` pair (now meaning "primary") is reserved too.

⚑ **A workset name is a user-typed shared-channel address**, which is why collisions here are
REFUSED rather than auto-resolved — the same reason `create_workset` refuses a duplicate rather than
auto-suffixing.

## Classes

```python
class _Unwind
```
LIFO stack of compensating actions for fail-consistent mutations. See **Failure-consistency**.

```python
def push(self, action: Callable[[], None]) -> None
def run(self) -> None
```

```python
@dataclass
class WorksetProject
```
A project registered inside a workset.

The unified per-project record (B7): **identity + path ONLY.** ⚑ There is no `seeded` field —
registry MEMBERSHIP (presence in this list) is itself the seed signal. A box was seeded when
`create` added it; `connect` adds the record WITHOUT seeding, because the external directory already
holds the user's content. Spec §0/§5, "Seed = at registration" and "One per-project record".

`source_path` is the original project path, kept for reference / cloning.

```python
@dataclass
class Workset
```
In-memory representation of a workset.

`workspaces_repoint` is the RAW `workset.workspaces` repoint captured from the root `settings.yaml`
(the routed `workset: {workspaces: …}` slot) at load/synthesis time. `None` = unset, so the spec
default composes in `workspaces_dir`.

⚑ **A direct construction — a fresh `create_workset`, or a test — has no settings yet**, so the
default applies, which is exactly the resolved value at that moment. This is why the field can
default to `None` without a separate "not yet loaded" state.

```python
@property
def projects_dir(self) -> Path      # {root}/boxes
@property
def workspaces_dir(self) -> Path    # the RESOLVED workset.workspaces
@property
def vault_dir(self) -> Path         # {root}/vault
@property
def logs_dir(self) -> Path          # {root}/logs
@property
def settings_path(self) -> Path     # {root}/settings.yaml — may NOT exist
@property
def registry_path(self) -> Path     # the RESOLVED workset.registry
```

⚑ `workspaces_dir` and `registry_path` are the RESOLVED ones (§3.3: real and USED) — the repoint is
honored and the default is spelled once, in the resolver machinery. `registry_path` re-reads
`settings.yaml` on each access to pick up a `workset.registry` repoint, matching the five equivalent
helpers in `settings/paths.py`; it is not cached in a field the way `workspaces_repoint` is.

*(The old "toml" names — `toml_path`, `_write_workset_toml`, `_load_workset_toml` — said "toml" and
operated on YAML. They were retired with this move; all config files in this project are YAML.)*

## Functions

```python
def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None
```
Best-effort read of *root*'s workset `settings.yaml` document.

Returns the raw document mapping, or `None` when the file is absent, unreadable, or not a mapping —
**mirroring `read_workset_identity`'s failure shape**, so a broken or (routinely) ABSENT settings
file degrades a repoint to the default composition instead of crashing the detection / lookup paths
that call it.

```python
def _workset_path_repoint(workset_settings: Mapping[str, Any] | None, leaf: str) -> str | None
```
Return the RAW `workset.<leaf>` repoint from a workset settings doc.

Reads the routed nested slot `workset: {<leaf>: …}`. `None` — or a non-mapping table, or an empty
value — means unset, and the caller falls back to the default formula.

```python
def _apply_workset_dir_repoint(workset_root: Path, repoint: str | None, default_leaf: str) -> Path
```
Apply a workset dir-key *repoint*, or the `<root>/<default_leaf>` default.

Same repoint semantics as `resolve_workset_registry_path`; see **Resolved workset dir keys**.

```python
def resolve_workset_workspaces(workset_root: Path, workset_settings: Mapping[str, Any] | None, *, standalone: bool = False) -> Path
```
Return the resolved `workset.workspaces` dir for a workset.

Honors a set `workset: {workspaces: …}`; else the spec default `@meta.workset.path/workspaces` ==
`<root>/workspaces`.

*standalone* selects the STANDALONE arm of the per-mode default table (spec §2e / ruled 10,
2026-08-02): the degenerate one-box workset defaults to the singular `@meta.workset.path/workspace`
== `<root>/workspace`. ⚑ The repoint SLOT is the same `workset: {workspaces: …}` key either way —
only the default formula varies by mode.

```python
def resolve_workset_channelroot(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
```
Return the resolved `workset.channelroot` for a workset (primary/named).

Honors a set `workset: {channelroot: …}`; else the spec default `@meta.workset.path/channels`.
⚑ Standalone has NO workset channels (the key is `<None>` there) — **callers gate on mode before
calling this**; the function itself will happily compose a path that should not exist.

```python
@contextmanager
def _journal_connect(journal: Path | None, box_path: Path, *, name: str, workset: str | None = None, workspace: str | None = None)
```
Bracket a `connect` (workset-membership) register with a journal entry.

The J2 write-ahead for the register-only CONNECT flow: write an `op: connect` entry → (the
membership write, the `with` body) → clear the entry.

* **Connect NEVER seeds** — it registers an externally-existing dir whose content is already the
  user's — so this bracket has NO seed step.
* The entry is cleared immediately after the membership write. **HARD INVARIANT: registered ⇒ no
  pending entry.**
* If the body raises, the entry is LEFT (incomplete) and the exception propagates;
  `add_project`'s `_Unwind` rolls back the in-process effects.
* A `None` *journal* is a no-op bracket (plain write), preserving pre-J2 behavior.
* *box_path* is the host-side box dir (`ws.projects_dir / name`), the uniform J1/J2 key.

⚑ **The only caller is `commands/workset_cmd.py::run_connect`**, and that is enforced by
`tests/test_import_recovery.py::test_no_journal_connect_call_in_lifecycle_module`. See
`add_project` below for why the bracket lives at the command and not at the seam.

```python
def is_reserved_workset_name(name: str) -> bool
```
Return True if *name* is a reserved sentinel (cannot be a NAMED workset).

```python
def _write_workset_identity(ws: Workset) -> None
```
Persist *ws*'s identity (`workset:`) and project records (`projects:`) into the root `registry.yaml`,
through `workset_registry.save_workset_record`.

⚑ **The read-the-file-first guard that used to live here is GONE**, and its absence is the point.
It existed because identity shared `settings.yaml` with the workset's cascade settings (`box.*` /
`agent.*` / `workset.bindings.*`), so a whole-document write would have dropped them. Those tables
are written by `workset config` and, for the two `workset.bindings.{ro,rw}` arms specifically, by
`workset share` — and this function no longer opens that file at all. `save_workset_record` still
does a read-modify-write of `registry.yaml`, for the unrelated reason that the `boxes:` section has
its own writers.

⚑ **Credential sharing is NOT an identity key.** It is an ordinary settable cascade key
(`workset.auth.share_allowed`) stored in the workset's settings cascade like any other setting. A
note is kept at the code because the identity dict is exactly where someone would put it back.

```python
def _load_workset_identity(root: Path) -> Workset
```
Read the workset identity (`workset:`) and project records (`projects:`) from *root*'s
`registry.yaml`.

Raises `WorksetError` when there is no identity table or it has no `name` — and
`LegacyWorksetIdentityError` (out of `read_workset_identity`) for a root still keeping its identity
in `settings.yaml`, so the LOAD path gets the named cure rather than the generic "no identity"
message. The `workset.workspaces` repoint is still read from `settings.yaml`, where it belongs, so
`workspaces_dir` composes the resolved key.

⚑ Projects arrive **sorted by name**, because the registry section is a name-keyed map written
sorted. The retired list-shaped table preserved insertion order.

```python
def _refuse_retired_workset_identity(root: Path) -> None
```
RAISE `LegacyWorksetIdentityError` when *root*'s `settings.yaml` still carries the workset identity
table, in EITHER retired spelling: `workset.meta` (v1.6.0/v1.7.x) or `meta.workset` (the unreleased
v1.8.0 tree). The message names the spelling actually found.

⚑ **DETECTED ONLY SO IT CAN BE DIAGNOSED.** v1.6.0 and v1.7.x wrote `workset: {meta: …}` into every
named workset root on disk, and v1.8.0 is a clean break: there is no compat read and no
auto-migration. Reading past the table is exactly the silent failure — a legacy root has no
`registry.yaml` identity, so `read_workset_identity` would return `None`, detection would call it
"not a workset root", and the ancestor walk would fall through to another mode with no error at all.
The refusal names the file, the retired location, the `registry.yaml` DESTINATION and the target
shape; the shipped guide is MIGRATION.md §2.43.

⚑ Two things the check deliberately does NOT do: a `workset.meta` that is a SCALAR is not the
identity table and does not refuse, and the refusal fires **unconditionally** when a retired table
is present — a root carrying identity in BOTH files refuses rather than picking one, because a stale
identity beside a live one is ambiguous, not migrated.

```python
def read_workset_identity(root: Path) -> dict | None
```
Return the `workset:` identity table from *root*'s `registry.yaml`.

`None` when the registry is missing / unreadable / carries no `workset:` table — i.e. *root* is not
a NAMED-workset root. ⚑ **The `except Exception` guard wraps the REGISTRY READ ONLY**, so it can
never swallow `_refuse_retired_workset_identity` (called BEFORE it): a corrupt file is still `None`,
a legacy root still refuses. That ordering is the contract — the retired check runs first, since a
legacy root's registry read returns `None` and would re-open the silent path.

⚑⚑ **This is THE detection primitive:** a directory is a NAMED workset root **iff** its
`registry.yaml` carries a `workset:` table with a name. That is why the PRIMARY workset root — whose
registry has `boxes:` but never `workset:` — is not mistaken for a named root. `settings/paths.py`'s
upward root walk calls it directly (checked first at each level, because the registry identity is
the more specific one). Changing what this returns changes where every box thinks it lives. The walk
does not catch, so the refusal propagates to the user as an `Error:` line.

```python
def _load_registry(std: StandardPaths) -> dict[str, Path]
```
Return `{name: root_path}` from the global worksets registry. See **The global registry**.

```python
def create_workset(name: str, root: Path, std: StandardPaths, force: bool = False) -> Workset
```
Create a new workset directory structure and register it globally.

Raises `WorksetError` if *root* already exists or the name is already registered.

**The three refusals, in the order they fire, and why each is where it is:**

1. **Reserved sentinel** — see `RESERVED_WORKSET_NAMES`.
2. **Same-kind uniqueness (D-B3)** — a NAMED workset's name is a user-typed shared-channel address,
   so it must be unique across all registered worksets. Refuse on collision, **never auto-suffix**,
   and point the user at the existing root. ⚑ `--force` NEVER bypasses this one.
3. **Cross-kind name guard** (per-kind name policy, Jei 2026-07-08) — a new workset whose name
   collides with an existing PRIMARY BOX name would be shadowed by the box in bare-name resolution
   (the box wins). Refused UNLESS *force*, and refused **BEFORE any on-disk side effect**.

**The multi-step create:** disk skeleton + root `registry.yaml`, then the global registry
registration. A crash between them would leave orphan dirs that are not in the registry. Forward
effects are tracked on an `_Unwind` and reversed on any failure, so the create is all-or-nothing.

⚑ The workspaces dir routes through `resolve_workset_workspaces` rather than a literal — a fresh
create has no settings file at all, so the resolver yields the spec default under *root*, but the
call site stays correct if that default ever moves.

⚑ The registration is a SINGLE `register_name(..., section="worksets")`: that one section serves
BOTH name-based lookups AND workset discovery / list. See **The global registry** for the collapse
that made it one call.

```python
def load_workset(root: Path) -> Workset
```
Load a workset from its root directory.

Raises `WorksetError` if the directory is missing or the root `registry.yaml` carries no `workset:`
identity.

⚑ Carries a legacy migration: an old `kanibako/` subdir is renamed to `boxes/` when `boxes/` does
not yet exist, with a notice on stderr.

```python
def list_worksets(std: StandardPaths) -> dict[str, Path]
```
Return `{name: root_path}` for all registered worksets.

⚑ **ONLY the on-disk registry** — the synthesized default workset is never injected here. A caller
that wants "every workset including default" must add it deliberately.

```python
def default_workset(std: StandardPaths) -> Workset
```
Synthesize the default workset (the group of default-mode projects).

The default workset is VIRTUAL. Its members are the default-mode boxes in the PRIMARY per-workset
`boxes:` membership — the sole store since the global `projects:` section retired (2026-07-08). It
roots at `@config.primary_workset` (spec §2c: PRIMARY `meta.workset.path`), so its settings/env
files derive from `root` exactly like a named workset's (F4).

⚑ **NEVER persisted to disk:** no identity write, and the primary root's `registry.yaml` carries
only the `boxes:` membership, never a `workset:` identity table. That is what keeps
`read_workset_identity` from mistaking the primary root for a NAMED workset root — and it is why
`created` is `""` here: there is no identity to read, so there is nothing to fabricate.

⚑ After the identity move, primary's members and a named workset's projects come from the SAME file
in the SAME shape. The two modes now differ only in being the default and in their default paths.

⚑ Credential sharing is a normal settable cascade key (`workset.auth.share_allowed`) resolved
through the settings pipeline — **not** a `Workset` field.

⚑ PRIMARY honors a `workset.workspaces` repoint from the primary workset's own `settings.yaml`, like
a named workset (unset → the same default composition as before). Note the spec declares PRIMARY's
`workset.workspaces` default as `<None>`; composing the leaf here is today's behavior.

```python
def resolve_workset_name(name: str, std: StandardPaths) -> Workset
```
Resolve a workset *name* to a `Workset`.

`default` / `__default__` resolve to the synthesized default workset; any other name is looked up in
the on-disk registry. Raises `WorksetError` if the name is not registered.

```python
def delete_workset(name: str, std: StandardPaths, *, remove_files: bool = False) -> Path
```
Unregister a workset and optionally remove its directory tree. Returns the deleted root path.

**Order is the whole content of this function:**

* The registry entry goes FIRST, and there is only ONE (the `workset_roots` half was collapsed away
  — see **The global registry**). Idempotent: a missing entry is a no-op.
* ⚑ **The irreversible step is LAST**, only after the registry is clean.

⚑⚑ **BOX TREES FIRST (J-7).** The workset root contains `boxes/<name>/home/canon`, a root-owned 555
skeleton per member box. A whole-root `shutil.rmtree` raises `PermissionError` as soon as it reaches
one — leaving the workset HALF-DELETED **after** its registry entries are already gone, which is
unrecoverable by re-running. Clearing each box tree through `runtime.container.remove_box_tree`
(which escalates, up to an unshare) first leaves only ordinary user content for the plain `rmtree`.
Symlinked entries under `boxes/` are skipped so the escalation never follows a link out of the tree.

```python
def add_project(ws: Workset, name: str, source_path: Path, std: StandardPaths | None = None, force: bool = False) -> WorksetProject
```
Add a project to a workset; creates the per-project subdirectories.

**Internal vs external.** When *source_path* resolves OUTSIDE the workset root **and** *std* is
provided, the project is CONNECTED to that external directory, which becomes the live workspace.
`workspaces/{name}` is then created as a **SYMLINK** to the external dir — **discoverability only,
never mounted** — and the box is registered in the workset's per-workset registry
(`boxes: {name → external path}`, the D10 connection record) so launches from the external path
resolve back to this workset. Sources inside the workset tree keep the normal behavior: a real
`workspaces/{name}` directory.

⚑ **No `settings.yaml` is written on either path.** See **Connected (external) boxes** for the
sparse-create ruling; the pre-relocation docstring claimed a `workspace` override file here and it
was false.

### The up-front validation block (external only)

All of it runs **BEFORE any directory is created**, because a refusal after a mutation is a
half-connected box:

* **Nested inside another registered workset** — the dir would be shadowed by ordinary in-tree
  location detection and mis-resolve. Refused with both workset names in the message.
* **⚑ Already connected** (or nested under something that is) — the connection record is 1:1, so
  this is a mapping conflict. Refused, naming the box and workset that hold it.
* **⚑ In-place standalone MARKER present (D3-mode #1)** — `box_data/` + `settings.yaml` is the box's
  AUTHORITATIVE self-declaration of standalone identity. Connecting it would write a per-workset
  `boxes:` entry and let resolution report it as a `named` workset box: a silent **steal**, leaving
  the box dual-registered (global `standalone:` AND the workset `boxes:`). Refused unless *force*.

  ⚑ The guard ALONE fixes the steal: with no `boxes:` entry written, resolution falls through to the
  marker. And only the EXTERNAL connect path writes that entry, which is why the guard scopes there —
  an internal add creates a real workspace dir and never registers a `boxes:` membership.

Internal sources and std-less callers (e.g. `migrate`) skip this block entirely.

### The mutation block

Multi-step: the external case touches a symlink + the per-workset `boxes:` connection record + the
durable root `settings.yaml` write. A crash between steps could strand a symlink or record with no
identity entry — an external path locked out by a dangling connection record — or vice versa.
Forward effects go on an `_Unwind`; success behavior is identical to the pre-`_Unwind` code.

* **Box dir** — always real. `exist_ok=True` keeps it idempotent, and the unwind only `rmtree`s dirs
  we may have created (guarded on a pre-existence check).
* **Vault** — ro/rw nest ABOVE the box name (`vault/{ro,rw}/<name>`) to match PRIMARY and
  STANDALONE. ⚑ The unwind removes the per-box `ro`/`rw` LEAVES only — **never** the shared
  `vault/ro` / `vault/rw` parents, which hold every box's vault.
* **External arm** — symlink, then the `boxes:` registration (idempotent; overwrites a moved box).
* **`--force` absorbing a self-declared standalone box** — the registration is **MOVED**, not
  duplicated: a box lives in EXACTLY ONE registry. The global `standalone:` entry is dropped so the
  box becomes SOLELY a workset box.

  ⚑ The in-place `box_data/` marker STAYS (intrinsic identity). While the `boxes:` entry exists,
  `detect_project_mode` step 1 (`find_connected_external_box`) fires BEFORE the standalone-marker
  check, so it resolves as a workset box and `import_standalone` never re-registers it. On
  `disconnect` — the `boxes:` entry gone — the marker walk re-imports it as standalone: **a clean
  round-trip.** `standalone_name_for_root` returns `None` when the box was never registered as
  standalone, so the whole branch is a no-op then (still no dual-reg).
* **Durable registry write LAST.** If it fails, the unwind removes the symlink, the per-workset
  registration and the dirs above, leaving no orphaned connection record.

### ⚑⚑ Why the J2 journal bracket is NOT here

`add_project` is also the membership-write seam for the DEFERRED move / convert / duplicate
pipelines (`commands/box/_lifecycle.py`), which must **NOT** journal a `connect` op — their
in-process `_Unwind` covers them, and full journaling for them is a later block. Scoping the entry
to `commands/workset_cmd.py::run_connect` ensures only an ACTUAL connect emits a `connect` entry.
The write-ahead ORDER still holds: `run_connect` writes the entry BEFORE this call and clears it
immediately after the call returns.

```python
def _detach_project(ws: Workset, name: str) -> None
```
Drop *name* from the in-memory project list (compensating action).

```python
def remove_project(ws: Workset, name: str, *, remove_files: bool = False, std: StandardPaths | None = None) -> WorksetProject
```
Remove a project from a workset. Raises `WorksetError` if no project with *name* exists.

When *std* is provided, the external-connect markers written by `add_project` are undone
**regardless of *remove_files***: the box's per-workset `boxes:` connection record (D10 — present
only when its recorded path is EXTERNAL) is dropped, and the `workspaces/{name}` symlink is
unlinked. ⚑ **Unlinking a symlink removes ONLY the link — never the user's external source
directory.** With *remove_files* the workset-side per-project directories go too (symlinks unlinked,
real dirs `rmtree`'d); the external source is still left intact.

### ⚑⚑ Failure-consistency ORDERING — the reverse of `add_project`

Clean the per-workset connection record and the discoverability symlink **BEFORE** the durable root
`settings.yaml` write, so the registry removal — the step that makes the project "gone" — is the LAST
durable one. A crash mid-cleanup then leaves the project still in `settings.yaml`: a **re-runnable**
state. The alternative ordering would remove it from the registry while the `boxes:` connection
record still pointed at it, **locking out the external path** with nothing left to name it. All the
cleanups are idempotent, so a re-run finds nothing to do.

⚑ The external-record test is on the RECORD's path, not on the argument: an in-tree box's membership
entry is left untouched, matching the pre-`std` behavior where only external connects were cleaned.

⚑ The `workspaces/{name}` symlink is unlinked regardless of *remove_files* so a discoverability
symlink never dangles.

### ⚑ THE BOX TREE NEEDS THE UNSHARE ESCALATION (J-7)

`projects_dir/<name>` holds the box HOME, and every R1b box home carries the canon skeleton:
root-owned and 555. `shutil.rmtree` raises `PermissionError` on a tree containing 555 directories
**EVEN WHEN THE CALLER OWNS THEM**, so this breaks in BOTH the protected and the degraded state. It
therefore goes through `remove_box_tree`. The workspace and vault dirs are ordinary user content and
stay on the plain path — **the escalation is scoped to what actually needs it.**

⚑ Vault nests `ro`/`rw` ABOVE the box name, so only the per-box leaves are removed — never the
shared `vault/ro` / `vault/rw` parents.

---

## 🛑 False and drifted claims found in this pass

Nine claims. Eight were **dropped or corrected here, never relocated as-is**; #3 was re-judged — the
source was right and the CODE was corrected instead.

| # | Where | The claim | Verdict |
|---|-------|-----------|---------|
| 1 | module docstring | "A global registry at `$XDG_DATA_HOME/kanibako/worksets.yaml` maps workset names to root paths" | **FALSE.** `worksets.yaml` is not read or written anywhere — `registry_store`'s own docstring says it and `names.yaml` "are no longer read or written", and `tests/test_project/test_workset.py:155` ASSERTS the file does not exist. The real location is the `worksets` section of `config.registry` = `@config.data/global/registry.yaml`. **Corrected.** |
| 2 | `create_workset` inline comment (×3 spellings) | "the global worksets.yaml/names.yaml registries", "then the names.yaml index", "a worksets.yaml entry with no names.yaml index (bare-name resolution fails while `workset list` works)" | **FALSE, same drift as #1, and it describes a CONTROL FLOW that does not exist.** The two files were collapsed into one section, so the code has ONE registration call, not two, and the described three-step crash window has only two steps. **Corrected.** |
| 3 | module docstring layout tree | "`settings.yaml` ← workset identity (`meta.workset.*`)" | **TRUE at the time — the CODE was the defect.** Docstring and code disagreed inside one file; that pass sided with the code and wrote `workset.meta`, which was the wrong half. ⚑ **SUPERSEDED 2026-08-22:** the identity does not live in `settings.yaml` in EITHER spelling any more — it is the `workset:` table of `registry.yaml`. Both spellings now HARD-REFUSE. See **Identity is REGISTRY-BORNE**. |
| 4 | module docstring layout tree | "The layout is: …" followed by six entries | **INCOMPLETE.** Omits `registry.yaml` (`workset.registry`), `auth/` (`workset.auth.path`) and `channels/` (`workset.channelroot`) — all three are in the spec's own workset-root layout (§2c and the template-whitelist block) and all three exist at real roots. Presented as the layout, it teaches a wrong root shape. **Corrected, with a note on which four `create_workset` actually makes.** |
| 5 | `BOXES_DIR_NAME` comment | "two places need it … `Workset.projects_dir` (below) and **`remove_workset`**" | **FALSE on both counts.** There is no `remove_workset` in the codebase (`grep` over `src/` + `tests/`: zero hits); the function is `delete_workset`. And there are THREE use sites, not two — `create_workset` also uses it, also without a `Workset` instance. **Corrected.** |
| 6 | `add_project` docstring | "a `workspace` override is written into the project's `settings.yaml`" | **FALSE, and self-contradicted 120 lines later.** The external arm writes no `settings.yaml` at all; the inline comment on the same path states "Sparse create (P8b/Option A): NO settings.yaml identity is written for the connected box — the connection record IS the per-workset `boxes:` entry". A reader trusting the docstring would go looking for a file that is never created. **DROPPED**, and the sparse-create ruling recorded instead. |
| 7 | `_Unwind` block comment | "These sequences are short (2-5 steps)" | **FALSE number.** `add_project` pushes up to SEVEN (box dir, vault ro, vault rw, symlink, membership, standalone restore, detach). **Corrected to the measured range.** |
| 8 | `RESERVED_WORKSET_NAMES` comment | "the three-mode model (**TARGET** §2c)" | **STALE CITATION.** There is no "TARGET" document; the sentinels are declared in `specs/settings-keyspace-1.8.0.md` §2c. A §-number with no valid filename is how the wrong file gets read. **Corrected to the spec filename.** |
| 9 | `Connected (external) boxes` banner | a full section-header comment block with **no code beneath it** | Its content was TRUE (verified: `_find_connected_project` / `_load_connected` / `_write_connected` and the global `connected:` section are all absent from `src/`), but a banner over an empty region is a structural false signal — it reads as if it introduces the code that follows, which belongs to `create_workset`. **Content relocated here; the orphan banner removed.** |

### Deltas found but NOT changed (code, not prose — reported for the director)

* **`workset.boxes` and `workset.logs` are declared, repointable spec keys** (`@meta.workset.path/boxes`,
  `@meta.workset.path/logs`) that this module HARD-CODES as `BOXES_DIR_NAME` and a `"logs"` literal.
  §3.3's "real and USED — not hard-coded" ruling was discharged for `workspaces` / `channelroot` and
  not for these two. Recorded in the layout section above.
* **`load_workset` spells `"boxes"` as a literal** (`new_subdir = root / "boxes"`) two lines after
  the module defines `BOXES_DIR_NAME` for exactly that purpose.
* **The in-function `from kanibako.settings.paths import …` statements are not cycle-motivated** —
  `settings.paths` is already imported at module scope. See **The import cycle**.
