# The Per-Workset Registry — membership, and the name anchors it yields

`workset_registry` is the read/write layer for one file per workset: the resolved `workset.registry`
key, whose default is `@meta.workset.path/registry.yaml` == `<workset_root>/registry.yaml`. That file
holds the workset's **membership**, and nothing else.

It is a document layer, not a policy layer. It loads its one section, writes it back atomically, and
offers small typed helpers over it — register, unregister, forward lookup, reverse lookup. It decides
nothing about modes, seeding or name validity; those live in `kanibako.settings.paths`,
`kanibako.project.names` and the settings keyspace.

## The file layout

```yaml
boxes:                      # box membership — the WHOLE file
  mybox: /abs/path/to/mybox
  other: /abs/path/to/other
```

The entry KEY is the box name (the `<leaf>` for a workset box) and the value is that box's workspace
path: the external dir for a connect, `workspaces/<name>` for an in-tree member.

⚑ Every write rebuilds the file with `boxes:` alone, sorted by name; any other top-level section is
dropped and printed as a warning naming the file (`_write_doc` → `registry_store.write_registry_doc`).
`_SECTIONS` and `_SORTED_SECTIONS` are one-element tuples, kept as tuples so a sibling-owned section
can be added without reshaping the writer.

## ⚑⚑ Why identity does NOT live here

system-design §Detect's registry-borne rule is about MEMBERSHIP: *"Members live in `registry.yaml`'s
`boxes:`, keyed by name."* A workset's IDENTITY is its `name → root` entry in the GLOBAL registry's
`worksets:` section (`@config.registry`) — the entry `workset create` writes and `kanibako workset
list` reads. Nothing under the workset root repeats it, and `project/workset.py` takes the name as an
argument rather than reading one.

⚑⚑ **THE PATH IS RECORDED EXACTLY ONCE.** A `workset:` identity table or a `projects:` map (a second
copy of every member path) HARD-REFUSES (`_refuse_retired_registry_sections`,
`LegacyRegistryIdentityError`), with by-hand fix steps: two copies of one path drift, and a
disconnect that drops one row orphans the other. v1.6.0/v1.7.x roots carry neither shape; theirs is
the `settings.yaml` table `project/workset.py` refuses.

⚑ There is no detection input here any more. A directory is a NAMED workset root **iff** the global
registry says so, which is also what keeps the PRIMARY workset root — same file, same `boxes:`
shape — from ever being mistaken for a named one.

## Why the membership section is authoritative

⚑ **The `boxes:` membership is the AUTHORITATIVE source of box names, not a cache of them.** Two
design consequences follow, and both run the direction that surprises people:

* **Names come OUT of it (design D1b).** Reading the entry keys is what YIELDS the `meta.box.name`
  anchors at resolution. There is no literal `meta.box.name` field stored anywhere — the key IS the
  anchor. The keyspace spec says so at the `workset.registry` row: *"Holds this workset's box
  MEMBERSHIP as `name: path` entries — the AUTHORITATIVE source of box names (its entry KEYS YIELD
  the RO `meta.box.name` anchors; NO literal `meta.box.name` field is stored)."*
* **Membership IS the registration signal (design D3-auth).** A box present here belongs to the
  workset, and the box directories follow the registry — not the other way round. Nothing scans a
  workset root for box dirs to decide who is a member.

The PRIMARY workset's instance of this file additionally holds the primary-mode name → EXTERNAL
workspace mappings; there is no global `projects` section in `registry_store`. Primary is non-exceptional otherwise: its location is derivable from the Layer-1
`config.primary_workset`.

⚑ **This is why a per-workset `registry.yaml` must never be templated or copied between worksets.**
The elaboration of that hazard lives with the code that could commit it: `install_workset_template`
in `launch/templates.py` carries a whitelist precisely so template CONTENT cannot plant a
`workset.yaml` or a `registry.yaml` at the workset path.

## The path contract

Every public function takes the resolved per-workset registry FILE path. That is the single source of
the registry location: the resolver (`resolve_workset_registry_path`) runs once, up front, and
nothing downstream reconstructs the path from a workset root. A user who repoints `workset.registry`
is therefore honored end-to-end.

An absent file yields an empty membership rather than an error, so a workset that has never
registered a box reads clean.

## Write discipline

Writes go through `registry_store.write_registry_doc` (atomic, via `config_io.dump_doc`: temp file
plus `os.replace`, so a crash mid-write cannot leave a torn registry), under `REGISTRY_HEADER`, and
every read-modify-write runs under `rmw_lock(registry_path)`. Every write rebuilds the file with
`boxes:` alone; any other top-level section is dropped and printed as a warning naming the file.

A write never scaffolds a section it was not asked to write. `unregister_workset_box` in particular
short-circuits before touching disk when the file is absent or the name is not present, so an
unregister of a non-member cannot conjure an empty `boxes:` block into existence.

Name-keyed sections are sorted on write, for stable diffs — matching the name-section writer in
`registry_store`.

## Reading a present-but-null section

`_section` coerces with `... or {}` rather than relying on `dict.get`'s default. The case is real:
YAML `boxes:\n` parses to the key being PRESENT with value `None`, so the `{}` default never applies
and a bare `dict(...)` call would raise. `registry_store` carries the same guard for the same reason.
Every section reader goes through `_section`, so it is written once.

`_load_boxes_raw` returns the pair `(full_doc, boxes)` because the two have different jobs —
`full_doc` is the raw document a write needs in order to preserve siblings, `boxes` is the normalized
`{name: path_str}` membership a caller reads.

⚑ `_load_raw` is **THE ONE READ SEAM**, which is why the retired-section refusal lives there: every
read of a per-workset registry passes through it, so no caller can slip past a `workset:` or
`projects:` section by reading some other way. A non-mapping document (a scalar YAML file) is handed
straight back; `_section` already tolerates it.

## The workspace-path uniqueness invariant

Within a workset's `boxes:` section, **a workspace path maps to EXACTLY ONE box name.**
`register_workset_box` enforces it: if the incoming path is already registered under a DIFFERENT
name, it raises `ProjectError` rather than mint a second entry pointing at the same workspace, which
would surface as duplicate `box list` rows.

The guard is deliberately narrow, and it obstructs neither legitimate flow:

| flow | same name? | same path? | result |
|---|---|---|---|
| re-register | yes | yes | idempotent — the pair is rewritten unchanged |
| a MOVED box | yes | no | the stored path is overwritten |
| a duplicate | no | yes | **refused**, `ProjectError` |

The self test is case-blind (spec §0 NAMING RULES): `Foo` against a stored `foo` is the SAME box,
so it is not a second registration, and the case-variant row is dropped rather than left beside
the new one. `unregister_workset_box` and `workset_box_path` also find a name case-blind; the table
keeps the spelling it was registered under.

### Paths compare literally

`_same_workspace` is `literal_path(a) == literal_path(b)`: links are unfollowed, so a symlink alias
of a stored path is a different workspace.

## Reverse lookup, and the drift it catches

`reverse_lookup_workset_box` is the reverse of the name → path membership map, and it shares
`_same_workspace`, so it matches exactly the paths `register_workset_box` treats as one workspace.

Its caller is the registration layer — `resolve_project`'s Guard 2 in `settings/paths.py` — which
uses it to reuse an already-registered box name instead of minting a duplicate. That is defense in
depth alongside the refusal above: the per-workset `boxes:` membership is what `list` and
`box_resolve` actually read, so a reverse lookup here catches drift the GLOBAL name registry has
already lost — for instance a purge that dropped the global name but left this membership behind.

## Resolving the registry path

`resolve_workset_registry_path(workset_root, workset_settings, *, early)` is a pure function of its
inputs — no global state; the caller passes the workset root, that workset's settings document, and
the required `early=` scope (the system tier). It is one face of the no-snapshot route
(`early_repoint` → `resolve_workset_dir_key`):

* **`workset.registry` is SET** — in the root's `workset.yaml` (the nested slot
  `workset: {registry: <path>}`), else at the system tier. The raw value may carry `@`-refs,
  `$XDG_*`, or `~`, resolved by `resolve_workset_dir_key`'s token grammar; a bare-relative value
  is REFUSED ([R147]), and a value that cannot resolve without the launch snapshot raises
  `SettingsError` naming the key, the file, and the token.
* **A present `null` REFUSES**: `SettingsError(ERR_CONFIG_NULL_PATH)`, naming the key and the file.
  This file is the only source of box names, so the default would read a file the user says is not
  there.
* **otherwise** the default `<workset_root>/registry.yaml`, == `@meta.workset.path/registry.yaml`. A standalone box has no per-workset registry at all (spec §2c: `workset.{registry,template}`
resolve to `<None>` for a lone box), so this resolver is never reached on that path.

`project/workset.py` mirrors this resolver at its own no-snapshot seam for the resolved workset dir
keys (`workset.workspaces` / `workset.channelroot`), and says so in the banner above
`load_workset_settings_doc`.
