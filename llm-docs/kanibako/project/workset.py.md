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
layout), §3.3 (the `workset.*` dir keys are "real and USED — not hard-coded"); and
`specs/system-design-1.8.0.md` § "Detection & import" (seed at registration), which the keyspec's
§5 migrated into whole on 2026-08-20 — the keyspec section is now a pointer, not a carrier.
⚑ **The spec is the LIVE authority; read it first.**

## 🛑 The workset-root layout — corrected against the spec

The module docstring carried an ASCII layout tree. A layout diagram is a claim about the world that
nothing tests, and this one had drifted. **Corrected form, every line checked against the code that
creates or resolves it:**

```
{root}/
    registry.yaml           ← box MEMBERSHIP (`boxes:`), flat `name: path` — and that is
                              the whole of the file  (`workset.registry`)
    workset.yaml            ← the workset's cascade settings — SETTINGS ONLY, and
                              OPTIONAL: a fresh root does not have one
    boxes/{name}/           ← per-box metadata dir (`meta.box.path`)
        home/               ← agent home (mounted as /home/agent)
        box.yaml            ← the BOX cascade tier (`meta.box.settings`)
        .kanibako.lock      ← concurrency lock
    workspaces/{name}/      ← per-box workspace (source tree) — the DEFAULT composition only
    vault/ro/{name}/        ← per-box read-only vault  (`workset.vault_ro`, DEFAULT only)
    vault/rw/{name}/        ← per-box read-write vault (`workset.vault_rw`, DEFAULT only)
    logs/{name}.jsonl       ← per-box helper message log
    auth/                   ← the per-workset credential dir (`workset.auth.path`)
    channels/               ← workset-local channels (`workset.channelroot`)
```

Three notes the diagram cannot carry:

* ⚑ **`create_workset` makes FOUR DIRS AND NO FILES** — `boxes/`, the resolved workspaces dir,
  `vault/` and `logs/`. There is no `registry.yaml` (no members yet, so no membership to record)
  and no `workset.yaml`. Both, plus
  `auth/` and `channels/`, are created lazily by the paths that own them (a `workset set`/`share add`
  write, the plugin's auth dir, the launch-path channel guarantee-create). Their absence from a fresh
  root is correct, not a bug — and for `workset.yaml` it is the NORMAL case.
* ⚑ **`workspaces/` is the DEFAULT, not the layout.** `workset.workspaces` is a repointable key
  (see below), and the PRIMARY per-mode default is `<None>` — primary boxes have EXTERNAL
  workspaces. Reading `<root>/workspaces` as a fixed fact is exactly the mistake the resolver exists
  to prevent.
* ⚑ **`boxes/` and `logs/` are ALSO repointable spec keys** (`@meta.workset.path/{boxes,logs}`),
  resolved by the detection locator and composed as default leaves by the `Workset` convenience
  properties — the KNOWN GAP recorded under **Resolved workset dir keys**. Reading either leaf off
  this diagram as a fixed fact is the same mistake `workspaces/` invites.

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
  (`load_primary_boxes`, `_register_workset_box_membership`,
  `_unregister_workset_box_membership`) are **not** cycle-motivated: `settings.paths` is already
  fully loaded by the module-scope `StandardPaths` import above them. They are historical. Do not
  "fix" the module-scope import by pushing it into a function on the theory that it closes a cycle —
  it does not, and the deferral that matters lives on the other side.
* `kanibako.launch.box_resolve` imports `resolve_workset_workspaces` from here inside a function
  body, so the resolvers below are on a deferred edge in the other direction too.
  `launch.templates._workset_stamp_dirs` has the SAME shape, importing `resolve_workset_canon` and
  `resolve_workset_template` in its body for the workset stamp. Both are `launch` → `project` edges;
  the direction that must never appear is the reverse, `project` → `launch`.

## Resolved workset dir keys — eight faces here, plus the registry

`workset.{workspaces,boxes,logs,channelroot,canon,template,vault_ro,vault_rw}` each get a thin per-key face in this
module, and `workset_registry.resolve_workset_registry_path` is another on the same route. A face
does two things and no more: read the RAW repoint out of the routed nested slot
`workset: {<leaf>: …}` (the location a settings-file edit — or `config set` at workset scope —
writes), and name the spec DEFAULT leaf. Everything else belongs to the ONE no-snapshot route,
`settings/workset_dirkeys.resolve_workset_dir_key`, which owns the token grammar
(`@meta.workset.path`, `$XDG_*`, `~`, plus any ref the FACE itself can already answer and hands in
as `extra_refs`), anchors a still-relative result under the workset root, and REFUSES by name any
value that would need the launch snapshot. ⚑ Do not give one of these faces a private expansion
again. ⚑ `extra_refs` has exactly one user — `resolve_workset_logs(..., standalone=True)`, below —
and the bar for a second is that the caller HOLDS the value, not that it knows the formula.

They are read on the DETECTION / paths side — the pass that FINDS the workset a snapshot will later
be built for — which is why that route exists separately from `settings_expand.expand` at all.

The leaf constants exist so the spec's per-mode default formula `@meta.workset.path/<leaf>` is
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
* **`workset.canon`** — default `@meta.workset.path/canon`, and spec `:962` declares it UNIFORM IN
  EVERY MODE, deliberately not a per-mode key, so a lone standalone root carries the tier too.
* **`workset.template`** — default `@meta.workset.path/template`; `<None>` in standalone (spec
  `:936`), which has no future boxes for a workset template to seed. Its sibling above still
  applies there — the two halves do not travel together.
* **`workset.boxes` / `workset.logs`** — defaults `@meta.workset.path/{boxes,logs}`. Resolved by
  the DETECTION locator (`_workset_skeleton_dirs`), by the `Workset.projects_dir` / `logs_dir`
  convenience properties, and by `delete_workset`'s unshare pre-pass — so a repointed root is both
  FOUND and WRITTEN TO in the same place. ⚑ The intermediate state, closed 2026-08-29, was worse
  than either endpoint: detection alone resolved the keys while the properties composed the default
  leaf, so a repointed root was **detected but mislocated** — found by the walk, then created,
  moved and purged under `<root>/boxes`. Two carriers of one shape, and the quieter kind of broken.
  ⚑ **The launch seam was always the correct owner** (`settings_launch`, `meta.box.path |
  @workset.boxes/@meta.box.name`); these properties are FACES on that answer, not a second one.
  ⚑ **BOTH take a `standalone=` flag, and it selects a DEFAULT LEAF** — the same shape as
  `resolve_workset_workspaces`. Spec §2c gives standalone `workset.boxes` the leaf `box_data`
  (not `boxes`) and `workset.logs` the value `@meta.box.path`, which for a lone box is
  `@workset.boxes` with no name leaf. So `resolve_workset_logs(..., standalone=True)` resolves
  `workset.boxes` with ITS standalone leaf first and hands it to the route as `extra_refs`, so the
  host-side writer (`settings/paths.py::helper_log_path`) and the `helpers.jsonl` MOUNT name one
  file in standalone too — the last arm of **migration M-14**, closed 2026-08-30. 🛑 The `boxes`
  flag does NOT make the standalone box STORE repointable end to end: home, the vault teardown,
  `clean --purge` and standalone DETECTION all still compose the literal `box_data`, which
  `system-design-1.8.0.md` makes a spec clause. It selects a default leaf so the logs default can
  chain through it, and nothing more. A repointed
  standalone log lands outside `box_data/` and therefore survives `box rm --purge`, which removes
  `box_data/` wholesale; that is a retained path, documented in MIGRATION.md, not a wider rmtree.
  🛑 STILL OPEN: box trees under a `workset.boxes` the user pointed OUTSIDE the root survive
  `workset rm --purge`, deliberately — `delete_workset`'s loop is a pre-pass for `rmtree(root)`, so
  it is owed only to what that call reaches. Closing it needs a retained-path report, not a wider
  rmtree (the line `standalone_vault_teardown` already draws).

⚑ **The repoint SLOT is the same key in every mode** — only the default FORMULA varies. A mode flag
selects a default, never a different storage location.

## Failure-consistency: the LIFO unwind stack

Several public mutators touch more than one file — a root `registry.yaml` plus the global registry,
or a symlink + box dirs + the durable `boxes:` membership write. Individual
writes are torn-file-safe (atomic temp + `os.replace`, via `config_io.dump_doc`), but a crash
*between* steps could strand a half-applied cross-file state: the registry says X while disk says Y,
an orphan symlink, or an external path locked out by a dangling connection record.

The stack mirrors the pattern in `commands/box/_lifecycle.py::_Unwind`: each forward step pushes a
compensating action; on any exception they run in reverse (best-effort, secondary failures
swallowed) and the original exception re-raises, leaving the op either fully applied or fully rolled
back. The sequences are short — two pushes for `create_workset`, up to seven for `add_project` — so
this stays deliberately small rather than a generic framework. ⚑ It is the SIMPLER of the two
`_Unwind`s: no `on_success` list and no `finish`.

## ⚑⚑ Identity is the GLOBAL registry; nothing under the root records a name

A workset's identity is its `name → root` entry in the `worksets:` section of the global registry
(`@config.registry`). That entry — written by `register_name` at `create_workset` and read by
`list_worksets` — is the whole of it. **Nothing under the workset root repeats it**, in either file,
and system-design §Detect's registry-borne rule covers MEMBERSHIP only: *"Members live in
`registry.yaml`'s `boxes:`, keyed by name."*

Four consequences, each of which someone will otherwise re-derive:

* **A workset root need not have a `workset.yaml` OR a `registry.yaml`.** `create_workset` writes
  neither. Every read path must therefore tolerate absence rather than raising —
  `load_workset_settings_doc` returns `None`, `load_workset_boxes` returns `{}`, and
  `config_io.load_doc` returns `{}`.
* **`load_workset` takes the name as an ARGUMENT.** There is nothing on disk to read it from, and
  every caller already has it: each one reached the root through the global registry's mapping.
  The signature is where the model is visible, so do not add a lookup behind it.
* **A workset is still FOUND on disk — naming and detection are two questions ([R139]).** The
  ancestor walk's NAMED arm tests `is_workset_skeleton`: the four dirs `create_workset` stamps,
  present together. Presence-only and name-free, exactly like `_is_standalone_meta_dir`. When it
  matches an unregistered root, `import_named_workset` registers it under the root's **leaf
  directory basename** — the same default `workset create` has always applied to a workset created
  without `--name`. So a workset tree copied to a new machine re-registers itself on first resolve,
  as a standalone box's does. 🛑 The reasoning that says otherwise — *"it has no name on disk, so
  it cannot be found"* — is the mistake that deleted this once: a fact about where the NAME lives is
  not a fact about whether the THING is findable.
* **`created` is gone.** Not moved — dropped. Nothing records when a workset was made.

Named for its TIER, as `BOX_META_FILE` is (`settings/config.py`: `"workset.yaml"` vs `"box.yaml"`)
— the filename says which scope's settings the file at that root holds ([R140]).

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

⚑ **Sparse create (P8b / Option A): connecting an external box writes NO `workset.yaml`.** The
connection record IS the per-workset `boxes:` entry (`name → EXTERNAL path`), and `box_resolve` reads
that entry for BOTH identity and the workspace override. There is no workspace-override file, and no
identity block, anywhere on the connect path.

## Constants

```python
WORKSET_META_FILE = "workset.yaml"   # imported from settings/config.py
```
The workset-tier cascade settings file at the workset root — **SETTINGS ONLY, and OPTIONAL**. See
**Identity is REGISTRY-BORNE**, above: the name is the TIER's, and is NOT a claim that the
workset's identity lives in the file.

```python
BOXES_DIR_NAME = "boxes"
```
The DEFAULT box-tree leaf under a workset root — ⚑ what `workset.boxes` falls back to, never a path
component. Since the collapse it has exactly ONE use: the `default_leaf` argument of
`resolve_workset_boxes`. Every consumer goes through that resolver instead:

* `Workset.projects_dir`, the property the store sites compose off;
* `_workset_skeleton_dirs`, which `create_workset` stamps and `is_workset_skeleton` tests;
* `delete_workset`, which holds a bare root `Path` from the registry and must clear each member box
  tree through the unshare-aware deleter before the plain `rmtree`.

⚑ A tripwire (`tests/test_project/test_workset.py::test_no_boxes_or_logs_literal_join_remains_at_the_sites`)
bans a `/ BOXES_DIR_NAME` or `/ "boxes"` join — and the `logs` equivalents — from this file,
`settings/paths.py` and `project/names.py`. It bans the CONSTANT as well as the string: `pw /
BOXES_PATH` in `settings/paths.py` was one of the defects, and a string-only pin would have passed it.

*(The pre-relocation comment said "two places" and named the third as `remove_workset` — a function
that has never existed under that name. See the false-claim list.)*

```python
_WORKSPACES_LEAF   _STANDALONE_WORKSPACE_LEAF   _CHANNELROOT_LEAF   _LOGS_LEAF
_CANON_LEAF = "canon"
_TEMPLATE_LEAF = "template"
```
The per-mode default leaves, spelled once. See **Resolved workset dir keys**. ⚑ The first four, and
`BOXES_DIR_NAME`, are ALIASES for `settings/bootstrap.py` — the designated path-literal file, where
the values are materialized and nowhere else. The local names stay because the call sites read
better with them.

`_CANON_LEAF` / `_TEMPLATE_LEAF` are the two that are spelled HERE rather than there, and the test
is a second consumer: no other module spells either word for a workset, because the one consumer —
the workset stamp — asks the resolver instead of joining a leaf. They belong beside their five
siblings in `bootstrap` the moment a SECOND module needs them.

⚑ They are the WORKSET-scope spelling of two entries `templates.SCOPE_WHITELISTS["workset"]`
permits, and `templates.AGENT_TEMPLATE_STORE_REL` — the same word at AGENT scope — stays separate:
an agent store's `template/` is a fixed store leaf, a workset's is the repointable
`workset.template`. Importing one for the other would conflate two keys as well as invert the
dependency.

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
holds the user's content. `settings-keyspace-1.8.0.md` §0 "Seed-time vs cascade" (registry
MEMBERSHIP is itself the seed signal); `system-design-1.8.0.md` § "Detection & import", "Seed = at
registration" and "One per-project record".

`source_path` is the member's REAL workspace — the `boxes:` row's value, and the one place the
registry records it: the external dir for a connect, `workspaces/<name>` for an in-tree member. ⚑ It
is NOT the caller's `source_path` argument to `add_project`; recording that for an in-tree connect
wrote a path the box never ran on, which is what `workset info` then printed.

```python
@dataclass
class Workset
```
In-memory representation of a workset.

`workspaces_repoint` is the RAW `workset.workspaces` repoint captured from the root `workset.yaml`
(the routed `workset: {workspaces: …}` slot) at load/synthesis time. `None` = unset, so the spec
default composes in `workspaces_dir`.

⚑ **A direct construction — a fresh `create_workset`, or a test — has no settings yet**, so the
default applies, which is exactly the resolved value at that moment. This is why the field can
default to `None` without a separate "not yet loaded" state.

```python
@property
def projects_dir(self) -> Path      # the RESOLVED workset.boxes
@property
def workspaces_dir(self) -> Path    # the RESOLVED workset.workspaces
@property
def vault_dir(self) -> Path         # {root}/vault — the SKELETON dir, a NON-KEY
@property
def vault_ro_dir(self) -> Path      # the RESOLVED workset.vault_ro
@property
def vault_rw_dir(self) -> Path      # the RESOLVED workset.vault_rw
@property
def logs_dir(self) -> Path          # the RESOLVED workset.logs
@property
def settings_path(self) -> Path     # {root}/workset.yaml — may NOT exist
@property
def registry_path(self) -> Path     # the RESOLVED workset.registry
```

⚑ **`vault_dir` is the last composed one, and correctly so** — no key names it (§3.3: real and
USED; every other leaf here honors its repoint and spells its default once, in the resolver
machinery). `projects_dir`, `logs_dir`, `vault_ro_dir`, `vault_rw_dir` and `registry_path` all
RE-READ `workset.yaml` on each access rather than caching, which is the opposite choice from
`workspaces_dir`'s `workspaces_repoint` field. **Deliberate:** a cached field is populated only by
the two constructors in this module, so a hand-built or test-built `Workset` silently gets the
default leaf — a footgun that, for `boxes`, means writing a box tree to the wrong store. Re-reading
is correct by construction. ⚑ Where a caller uses one of these in a LOOP it hoists the property
first (`iter_workset_projects`, `clean.py`'s purge): one read per workset, so every member is
judged against the same document — the same reason `resolve_workset_vault_pair` exists.

*(The old "toml" names — `toml_path`, `_write_workset_toml`, `_load_workset_toml` — said "toml" and
operated on YAML. They were retired with this move; all config files in this project are YAML.)*

## Functions

```python
def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None
```
Best-effort read of *root*'s workset `workset.yaml` document.

Returns the raw document mapping, or `None` when the file is absent, unreadable, or not a mapping —
**mirroring `load_workset_boxes`'s failure shape**, so a broken or (routinely) ABSENT settings
file degrades a repoint to the default composition instead of crashing the detection / lookup paths
that call it.

```python
def _workset_path_repoint(workset_settings: Mapping[str, Any] | None, leaf: str) -> str | None
```
Return the RAW `workset.<leaf>` repoint from a workset settings doc.

Reads the routed nested slot `workset: {<leaf>: …}`. `None` — or a non-mapping table, or an empty
value — means unset, and the caller falls back to the default formula.

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
def resolve_workset_canon(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_template(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
```
The resolved `workset.canon` / `workset.template` dirs — the two leaves the WORKSET STAMP writes.

Their one caller is `launch.templates._workset_stamp_dirs`, which imports both inside its function
body and reads the root's `workset.yaml` ONCE for the pair. `canon` is uniform in every mode;
`template` is primary/named only, and the stamp's standalone arm skips it accordingly. See
**Resolved workset dir keys**.

```python
def resolve_workset_vault_ro(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_vault_rw(workset_root: Path, workset_settings: Mapping[str, Any] | None) -> Path
def resolve_workset_vault_pair(workset_root: Path) -> tuple[Path, Path]
```
The resolved `workset.vault_ro` / `workset.vault_rw` dirs — **the box's vault, in every mode.**

Defaults are `@meta.workset.path/vault/{ro,rw}` — the only TWO-SEGMENT default leaves on the route,
because the spec declares no `workset.vault` parent for them to hang off. ⚑⚑ **Both keys are
declared ONCE FOR EVERY MODE** (§2c ALL PROJECTS, R-29): there is NO `standalone: <None>`
carve-out, unlike `template`, `registry`, `auth.path` and the channel keys. Only the box BIND
differs per mode — primary and named take a `/@meta.box.name` leaf, a lone box takes the arm
itself. Do not add a mode parameter here; the variation is downstream.

`resolve_workset_vault_pair` is the form callers should reach for: every consumer wants BOTH arms,
and reading `workset.yaml` once per arm opens a window for the two to disagree about the same
document. Its callers are `add_project`, `remove_project`, `settings.paths._standalone_box_paths`
and `commands.box._lifecycle._to_workset`; `settings.paths.resolve_system_paths` uses the two faces
directly because it already holds the read.

⚑ **The arms are independent.** Repointing `vault_ro` alone leaves `vault_rw` at its default, and
nothing may treat the two as sharing a parent — that assumption is exactly what the pre-fix
`ws.vault_dir / "ro"` composition encoded, and it made both keys settable-but-ignored.

```python
def standalone_vault_teardown(root: Path) -> tuple[list[Path], list[Path]]
```
The `(removable, retained)` split a STANDALONE teardown must use — `commands.box._parser`'s
`_teardown_standalone_box`, `commands.clean._purge_one` and
`commands.box._lifecycle._remove_old_metadata`'s standalone arm all go through it.

⚑⚑ **Why a SPLIT and not a list.** A standalone box's vault IS the resolved arm — there is no
per-box leaf under it (`settings.paths._standalone_box_paths`). So "delete this box's vault" and
"delete the directory `workset.vault_ro` names" are the SAME ACT here, and they are NOT the same
act in named or primary mode, where only a `<box-name>` leaf is ever removed and the arm outlives
every box on it. An arm STRICTLY BELOW `root` is kanibako's own skeleton inside the tree the
teardown is already clearing, so it goes with the box; an arm the user pointed OUTSIDE `root` (or
AT it — `vault_ro: .` would nominate the whole project dir) is the USER'S STORE and is never
`rm -rf`ed on their behalf. **Callers must PRINT the retained paths:** the defect this replaced was
not that a repointed vault survived, it was that it survived SILENTLY.

The literal `vault/` skeleton parent is appended to *removable* when it is on disk, so the default
layout — whose `.gitignore` lives there, and which is what every pre-repoint box has — is cleared
exactly as before.

🛑 **Call it BEFORE the teardown deletes or unlinks anything.** The root `workset.yaml` is the
standalone workset tier and the only carrier of the repoint, so resolving after it is gone answers
the composed default; and an unresolvable repoint RAISES `SettingsError` naming the key, which must
land while the box is still whole rather than behind a half-finished purge.

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
def _load_workset(root: Path, name: str) -> Workset
```
Build the `Workset` for the globally-registered *name* rooted at *root*.

*name* is the caller's, from the global registry's `worksets:` mapping. Members come from
`registry.yaml`'s `boxes:` section, which is the whole of what that file holds; the
`workset.workspaces` repoint comes from `workset.yaml`, where it belongs, so `workspaces_dir`
composes the resolved key.

Raises `LegacyWorksetIdentityError` for a root still carrying a retired identity table, so the LOAD
path gets the named cure. ⚑ **The load path is where a 1.6/1.7 user hits that refusal**, not
detection: their workset IS globally registered (`workset create` registered it), so `_check_workset`
resolves the directory perfectly well and the very next step is the one that refuses.

⚑ There is no "not a workset root" failure left. A bare directory loads as a workset with no
members, because membership is what can be absent — the workset itself is asserted by the registry.

⚑ Members arrive **sorted by name**, because `boxes:` is a name-keyed map written sorted. The
retired list-shaped table preserved insertion order.

```python
def refuse_retired_workset_identity(root: Path) -> None
```
RAISE `LegacyWorksetIdentityError` when *root*'s `workset.yaml` still carries a workset identity
table, in EITHER retired spelling: `workset.meta` (v1.6.0/v1.7.x) or `meta.workset` (the unreleased
v1.8.0 tree). The message names the spelling actually found, and its tail differs per spelling
because what is left to delete differs.

⚑ **DETECTED ONLY SO IT CAN BE DIAGNOSED.** v1.6.0 and v1.7.x wrote `workset: {meta: …}` into every
named workset root on disk, and v1.8.0 is a clean break: there is no compat read and no
auto-migration. Reading past the table is exactly the silent failure — 1.8.0 takes the file for
ordinary settings, drops the `meta` table with a generic unsettable-namespace warning and never
looks at the `projects` list beside it, so the workset's members stop resolving with nothing printed
to say why. The refusal names the file, the retired location, the `registry.yaml` membership
destination and the global registry as the real home of the name; the shipped guide is
MIGRATION.md §2.43.

⚑ Two things the check deliberately does NOT do: a `workset.meta` that is a SCALAR is not the
identity table and does not refuse, and an unparseable file is a miss rather than a refusal
(`load_workset_settings_doc` swallows the parse error), because a file kanibako cannot read is not
evidence of a retired shape.

⚑ **PUBLIC, and called from two places:** `_load_workset`, and `settings/paths.py`'s ancestor walk,
which now calls it for the diagnostic ALONE — there is no marker to find there any more, so a legacy
root is refused by name on the way past rather than walked over in silence. Neither caller catches,
so the refusal reaches the user as an `Error:` line.

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
def load_workset(root: Path, name: str) -> Workset
```
Load the workset registered as *name* at *root*.

*name* is REQUIRED — the caller has it, because the global registry's `worksets:` mapping is how it
reached *root*, and that mapping is the only record of the name there is. Raises `WorksetError` if
the directory is missing; the resolved root then goes to `_load_workset`, which does the rest.

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

⚑ **NEVER persisted to disk:** nothing is written, and it is never in the global `worksets:`
section — which is precisely why it cannot be confused with a named workset.

⚑ Primary's members and a named workset's members come from the SAME file in the SAME shape — the
root `registry.yaml`'s flat `boxes:` section. The two modes now differ only in being the default and
in their default paths.

⚑ Credential sharing is a normal settable cascade key (`workset.auth.share_allowed`) resolved
through the settings pipeline — **not** a `Workset` field.

⚑ PRIMARY honors a `workset.workspaces` repoint from the primary workset's own `workset.yaml`, like
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

⚑ **No `workset.yaml` is written on either path.** See **Connected (external) boxes** for the
sparse-create ruling; the pre-relocation docstring claimed a `workspace` override file here and it
was false.

### The up-front validation block (external only)

All of it runs **BEFORE any directory is created**, because a refusal after a mutation is a
half-connected box:

* **Nested inside another registered workset** — the dir would be shadowed by ordinary in-tree
  location detection and mis-resolve. Refused with both workset names in the message.
* **⚑ Already connected** (or nested under something that is) — the connection record is 1:1, so
  this is a mapping conflict. Refused, naming the box and workset that hold it.
* **⚑ In-place standalone MARKER present (D3-mode #1)** — `box_data/` + `workset.yaml` is the box's
  AUTHORITATIVE self-declaration of standalone identity. Connecting it would write a per-workset
  `boxes:` entry and let resolution report it as a `named` workset box: a silent **steal**, leaving
  the box dual-registered (global `standalone:` AND the workset `boxes:`). Refused unless *force*.

  ⚑ The guard ALONE fixes the steal: with no `boxes:` entry written, resolution falls through to the
  marker. And only the EXTERNAL connect path writes that entry, which is why the guard scopes there —
  an internal add creates a real workspace dir and never registers a `boxes:` membership.

Internal sources and std-less callers (e.g. `migrate`) skip this block entirely.

### The mutation block

Multi-step: the external case touches the box/vault dirs + a symlink + the durable per-workset
`boxes:` write into `registry.yaml`. A crash between steps could strand a symlink or a dir with no
membership row — or an external path locked out by a dangling connection record.
Forward effects go on an `_Unwind`; success behavior is identical to the pre-`_Unwind` code.

* **Box dir** — always real. `exist_ok=True` keeps it idempotent, and the unwind only `rmtree`s dirs
  we may have created (guarded on a pre-existence check).
* **Vault** — ro/rw nest ABOVE the box name (`<resolved arm>/<name>`) to match PRIMARY and
  STANDALONE. ⚑ The two arms come from `resolve_workset_vault_pair(ws.root)` — ONE `workset.yaml`
  read for both — not from `ws.vault_dir / "ro"`, which would answer a repointable key with a
  composed literal. ⚑ The unwind removes the per-box LEAVES only — **never** the shared arms,
  which hold every box's vault.
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

The external-connect markers written by `add_project` are undone **regardless of *remove_files***:
the box's per-workset `boxes:` membership row (D10) is dropped, and the `workspaces/{name}` symlink
is unlinked. ⚑ **Unlinking a symlink removes ONLY the link — never the user's external source
directory.** With *remove_files* the workset-side per-project directories go too (symlinks unlinked,
real dirs `rmtree`'d); the external source is still left intact.

### ⚑⚑ Failure-consistency ORDERING — the reverse of `add_project`

Unlink the discoverability symlink **BEFORE** the durable `boxes:` removal from `registry.yaml`, so
the membership drop — the step that makes the project "gone" — is the LAST durable one. A crash
mid-cleanup then leaves the member still registered: a **re-runnable** state. The reverse ordering
would drop the row while the symlink still pointed at the external dir, **locking out the external
path** with nothing left to name it. All the cleanups are idempotent, so a re-run finds nothing to
do.

⚑ The drop is UNCONDITIONAL, and *std* is now accepted unused (caller parity): dropping only
EXTERNAL rows orphaned an in-tree disconnect's row, and that orphan then tripped the
workspace-uniqueness refusal — locking that workspace out of its own workset under any name.

⚑ The `workspaces/{name}` symlink is unlinked regardless of *remove_files* so a discoverability
symlink never dangles.

### ⚑ THE BOX TREE NEEDS THE UNSHARE ESCALATION (J-7)

`projects_dir/<name>` holds the box HOME, and every R1b box home carries the canon skeleton:
root-owned and 555. `shutil.rmtree` raises `PermissionError` on a tree containing 555 directories
**EVEN WHEN THE CALLER OWNS THEM**, so this breaks in BOTH the protected and the degraded state. It
therefore goes through `remove_box_tree`. The workspace and vault dirs are ordinary user content and
stay on the plain path — **the escalation is scoped to what actually needs it.**

⚑ Vault nests `ro`/`rw` ABOVE the box name, so only the per-box leaves are removed — never the
shared arms. ⚑⚑ `remove_project` resolves those arms through `resolve_workset_vault_pair` FOR THE
SAME REASON `add_project` does, and the two must never diverge: deleting the composed default while
the box's real vault sits at a repoint would orphan the user's data AND remove a directory the box
never used.

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
| 5 | `BOXES_DIR_NAME` comment | "two places need it … `Workset.projects_dir` (below) and **`remove_workset`**" | **FALSE on both counts.** There is no `remove_workset` in the codebase (`grep` over `src/` + `tests/`: zero hits); the function is `delete_workset`. And there were THREE use sites, not two — `create_workset` also used it, also without a `Workset` instance. **Corrected**, then SUPERSEDED 2026-08-29: the constant is now the resolver's `default_leaf` argument and nothing else, so the count is ONE and the question of "which sites need the bare leaf" no longer arises. |
| 6 | `add_project` docstring | "a `workspace` override is written into the project's `settings.yaml`" | **FALSE, and self-contradicted 120 lines later.** The external arm writes no `settings.yaml` at all; the inline comment on the same path states "Sparse create (P8b/Option A): NO settings.yaml identity is written for the connected box — the connection record IS the per-workset `boxes:` entry". A reader trusting the docstring would go looking for a file that is never created. **DROPPED**, and the sparse-create ruling recorded instead. |
| 7 | `_Unwind` block comment | "These sequences are short (2-5 steps)" | **FALSE number.** `add_project` pushes up to SEVEN (box dir, vault ro, vault rw, symlink, membership, standalone restore, detach). **Corrected to the measured range.** |
| 8 | `RESERVED_WORKSET_NAMES` comment | "the three-mode model (**TARGET** §2c)" | **STALE CITATION.** There is no "TARGET" document; the sentinels are declared in `specs/settings-keyspace-1.8.0.md` §2c. A §-number with no valid filename is how the wrong file gets read. **Corrected to the spec filename.** |
| 9 | `Connected (external) boxes` banner | a full section-header comment block with **no code beneath it** | Its content was TRUE (verified: `_find_connected_project` / `_load_connected` / `_write_connected` and the global `connected:` section are all absent from `src/`), but a banner over an empty region is a structural false signal — it reads as if it introduces the code that follows, which belongs to `create_workset`. **Content relocated here; the orphan banner removed.** |

### Deltas found but NOT changed (code, not prose — reported for the director)

* **The in-function `from kanibako.settings.paths import …` statements are not cycle-motivated** —
  `settings.paths` is already imported at module scope. See **The import cycle**.
