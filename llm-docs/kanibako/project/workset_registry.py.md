# The Per-Workset Registry — `boxes:` membership, and the name anchors it yields

`workset_registry` is the read/write layer for one file per workset: the resolved `workset.registry`
key, whose default is `@meta.workset.path/registry.yaml` == `<workset_root>/registry.yaml`. That file
holds the workset's **box membership**, and nothing else this module writes.

It is a document layer, not a policy layer. It loads the `boxes:` section, writes it back atomically,
and offers small typed helpers over it — register, unregister, forward lookup, reverse lookup. It
decides nothing about modes, seeding or name validity; those live in `kanibako.settings.paths`,
`kanibako.project.names` and the settings keyspace.

## The file layout

```yaml
boxes:
  mybox: /abs/path/to/mybox
  other: /abs/path/to/other
```

The entry KEY is the box name (the `<leaf>` for a workset box); the value is the box's path.

## Why this section is authoritative

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
workspace mappings, which is what replaced the retired global `projects` section in
`registry_store`. Primary is non-exceptional otherwise: its location is derivable from the Layer-1
`config.primary_workset`.

⚑ **This is why a per-workset `registry.yaml` must never be templated or copied between worksets.**
The elaboration of that hazard lives with the code that could commit it: `install_workset_template`
in `launch/templates.py` carries a whitelist precisely so template CONTENT cannot plant a
`settings.yaml` or a `registry.yaml` at the workset path.

## The path contract

Every public function takes the resolved per-workset registry FILE path. That is the single source of
the registry location: the resolver (`resolve_workset_registry_path`) runs once, up front, and
nothing downstream reconstructs the path from a workset root. A user who repoints `workset.registry`
is therefore honored end-to-end.

An absent file yields an empty membership rather than an error, so a workset that has never
registered a box reads clean.

## Write discipline

Writes go through `config_io.dump_doc` — temp file plus `os.replace`, so a crash mid-write cannot
leave a torn registry — and they **preserve every sibling section untouched**: the raw document is
read back and only `boxes:` is swapped. That is what lets a future per-workset `connected:` or
marker section coexist in the same file. (The global `connected:` index is already gone; connections
live in each workset's `boxes:` entries, design D10.)

A write never scaffolds a section it was not asked to write. `unregister_workset_box` in particular
short-circuits before touching disk when the file is absent or the name is not present, so an
unregister of a non-member cannot conjure an empty `boxes:` block into existence.

Keys are sorted on write, for stable diffs — matching the name-section writer in `registry_store`.

## Reading a present-but-null section

`_load_boxes_raw` coerces with `... or {}` rather than relying on `dict.get`'s default. The case is
real: YAML `boxes:\n` parses to the key being PRESENT with value `None`, so the `{}` default never
applies and a bare `dict(...)` call would raise. `registry_store` carries the same guard for the same
reason.

`_load_boxes_raw` returns the pair `(full_doc, boxes)` because the two have different jobs —
`full_doc` is the raw document a write needs in order to preserve siblings, `boxes` is the normalized
`{name: path_str}` membership a caller reads.

## The workspace-path uniqueness invariant (the Bug A durable fix)

Within a workset's `boxes:` section, **a workspace path maps to EXACTLY ONE box name.**
`register_workset_box` enforces it: if the incoming path is already registered under a DIFFERENT
name, it raises `ProjectError` rather than mint a second entry pointing at the same workspace. That
duplicate-minting was the root cause that surfaced to the user as duplicate `box list` rows.

The guard is deliberately narrow, and it obstructs neither legitimate flow:

| flow | same name? | same path? | result |
|---|---|---|---|
| re-register | yes | yes | idempotent — the pair is rewritten unchanged |
| a MOVED box | yes | no | the stored path is overwritten |
| a duplicate | no | yes | **refused**, `ProjectError` |

### Why the comparison is resolved-path aware

`_same_workspace` tries exact string equality first, because that is the common case — callers store
already-resolved paths. It then falls back to comparing `Path.resolve()` results, so a normalization
or symlink difference between a stored value and a re-registering caller still counts as the SAME
workspace. That drift is exactly what let Bug A slip past a naive equality check and mint the
duplicate.

`Path.resolve` is called non-strict here, so a not-yet-existing path never raises; the `OSError` /
`RuntimeError` arm covers the remaining resolution failures (a symlink loop, an unreadable
component) by answering "not the same" rather than propagating.

## Reverse lookup, and the drift it catches

`reverse_lookup_workset_box` is the reverse of the name → path membership map, and it shares
`_same_workspace`, so it matches a symlink or normalization alias of the stored path for the same
reason `register_workset_box` refuses one.

Its caller is the registration layer — `resolve_project`'s Guard 2 in `settings/paths.py` — which
uses it to reuse an already-registered box name instead of minting a duplicate. That is defense in
depth alongside the refusal above: the per-workset `boxes:` membership is what `list` and
`box_resolve` actually read, so a reverse lookup here catches drift the GLOBAL name registry has
already lost — for instance a purge that dropped the global name but left this membership behind.

## Resolving the registry path

`resolve_workset_registry_path` is a pure function of its inputs — no global state; the caller passes
the workset root and that workset's settings document. Two arms:

* **`workset.registry` is SET** in the settings mapping — the routed nested slot
  `workset: {registry: <path>}`, the same location `config set workset.registry=<path>` writes. It is
  honored: `~` expands, an absolute path is used as-is, and a RELATIVE repoint anchors under the
  workset root, deterministically, like the sibling path keys.
* **otherwise** the default `<workset_root>/registry.yaml`, == `@meta.workset.path/registry.yaml`.

`None` for the settings document, or a `workset` table that is not a mapping, falls through to the
default. A standalone box has no per-workset registry at all (spec §2c: `workset.{registry,template}`
resolve to `<None>` for a lone box), so this resolver is never reached on that path.

`project/workset.py` mirrors this resolver at its own no-snapshot seam for the resolved workset dir
keys (`workset.workspaces` / `workset.channelroot`), and says so in the banner above
`load_workset_settings_doc`.

---

## FALSE CLAIM FOUND AND DROPPED (llm-docs pass, 2026-08-20)

Recorded here rather than relocated, because relocating a drifted claim launders it into a document
that reads as current.

**"This is ADDITIVE infrastructure (settings-conformance phase P3): nothing consumes it yet — the
launch/create cutover that moves box membership onto per-workset registries is P4/P5. It changes no
existing flow."** — the whole paragraph is now false. The cutover happened. Measured with
`command grep -rn` over `src/` and `packages/`: `settings/paths.py` calls
`resolve_workset_registry_path` at five sites, and wraps the membership verbs in
`_register_workset_box_membership`, `_unregister_workset_box_membership`,
`_workset_box_name_for_workspace` and the workset-box path/list helpers, which `resolve_project`
and the box-listing paths then use; `project/workset.py` registers membership during workset box
creation and rolls it back on failure. The module is on the live create, register, resolve and list
paths. **Dropped.**

### Completeness sweep

Everything else in the pre-pass source was relocated in substance into a section above, or was
duplication of a sentence that survives elsewhere in this document. The three named duplications:

1. The module docstring stated the `workset.registry` default path (`@meta.workset.path/registry.yaml`
   == `<workset_root>/registry.yaml`) and `resolve_workset_registry_path`'s docstring stated it
   again. Surviving carrier: "Resolving the registry path" above, plus the H1 paragraph.
2. "Atomic; preserves every sibling section" appeared in the module docstring and again in
   `_write_boxes`, `register_workset_box` and `unregister_workset_box`. Surviving carrier: "Write
   discipline" above.
3. `_same_workspace`'s resolved-path rationale was stated in its own docstring and again, in the
   same words, in `reverse_lookup_workset_box`'s. Surviving carrier: "Why the comparison is
   resolved-path aware" above.

**Two `⚑` one-liners were KEPT IN SOURCE** under the keep test, because deleting either would let a
future edit break something silently at that exact line:

* above `_load_boxes_raw`'s `... or {}` — it reads like a redundant belt-and-braces default and is
  not one;
* above `register_workset_box`'s refusal loop — it looks like an over-strict check a future reader
  would relax, and relaxing it re-opens Bug A.

The authoritative-membership statement was kept in the module docstring as a correctness warning, per
the keep rule: a reader who believes this file is a derived cache will treat it as safe to
regenerate, template or hand-edit.
