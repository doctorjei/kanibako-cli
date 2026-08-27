# The Consolidated Name Registry — one `registry.yaml` behind every global name store

`registry_store` is the read/write layer for a single file at `@config.registry`
(`@config.data/global/registry.yaml` == `{data_path}/global/registry.yaml`). That one file backs
every kanibako *global* name store: which worksets exist and where their roots are, which
standalone boxes are registered, which boxes have been deregistered but not yet purged, which rigs
have been added, and which login shell each image was captured with.

It is a document layer, not a policy layer. It loads sections, writes sections back atomically, and
offers small typed helpers over the two sections it owns outright (`standalone` and
`deregistered`). It decides nothing about names, membership or modes — those live in
`kanibako.project.workset_registry`, `kanibako.project.names` and the settings keyspace.

## The file layout

The file has these top-level sections:

```yaml
worksets:
  clientwork: /home/user/worksets/client

standalone:
  # box.name → root, populated by sub-step 5d; empty for now.

deregistered:
  # box.name → retained-recovery blob for a box removed by ``rm`` without ``--purge``.

rigs:
  corp/base:1.0: {kind: prefab, ...}   # formerly rigs.yaml

image_shells:
  sha256:abc...: /bin/bash             # formerly image-shells.yaml
```

`worksets` carries the workset name → root registry, used both for name-based lookups AND to
discover and list worksets. The former separate `worksets.yaml` and its duplicate `workset_roots`
were collapsed onto this single section (2026-06-29f).

`standalone` maps a registered standalone box's canonical `<kuid>_<leaf>` name to its root path
string.

`rigs` carries the former `rigs.yaml` payload — added-rig records keyed by rig name; the
`rig_registry` module owns its shape. `image_shells` carries the former `image-shells.yaml` map,
image store key → captured login shell, whose shape the `shells` module owns. Both sections are
read and written by their owning modules through `load_section` / `save_section`, which preserve
sibling sections; this module passes their values through verbatim and never interprets them.

## What this file replaced, and what is deliberately absent

The consolidation replaced the former separate files `names.yaml` (projects plus worksets) and
`worksets.yaml` (workset name → root). Neither is read or written any more.

**The global `connected:` external-connect index is GONE.** Connections now live in each workset's
per-workset registry as a `boxes:` entry (design D10).

**The `projects` section is RETIRED** (clean split, 2026-07-08). It used to map a default-mode box
name to its external workspace. A PRIMARY box's identity now lives SOLELY in the primary workset's
per-workset `boxes:` membership (`@config.primary_workset/registry.yaml`, via
`kanibako.project.workset_registry`), which is the AUTHORITATIVE source of box names (spec L514).
The section is no longer loaded or written, and because it is absent from `_SECTIONS`, a stale
`projects` block left behind by an older install is simply dropped on the next `save_registry` — no
migration, no legacy read. Dropping it from that tuple is what does both jobs at once: it stops the
loader surfacing the section AND it discards the stale block on the next write.

**There is NO `seeded` section, and there must not be one.** Registry MEMBERSHIP is itself the seed
signal: a box present here — a `standalone` entry, a NAMED workset-local list entry, or a PRIMARY
per-workset `boxes:` membership — was seeded at its `create` (seed-then-register:
`settings-keyspace-1.8.0.md` §0 "Seed-time vs cascade"; `system-design-1.8.0.md` § "Detection &
import"). The former `seeded` flag section, and the first-launch gate that read it, are both
gone.

⚑ **The implication runs ONE WAY: present ⇒ seeded, never the converse.** A standalone box created
without `--register` (§D4a) is seeded and ABSENT from the registry until `box register` indexes it —
which is exactly why that verb is seed-free.

## The path contract

Every public function takes the resolved `config.registry` FILE path (callers pass `std.registry`),
which is the single source of the registry location. A user who repoints `config.registry` is
honored end-to-end; nothing here reconstructs the path from `config.data`.

No on-disk migration is performed — the old files are NOT read. A fresh tree, with no
`registry.yaml` at all, yields empty sections rather than an error. Writes are atomic, via
`config_io.dump_doc` (temp file plus `os.replace`).

`load_registry` always returns every canonical section key, present or not, so callers can index the
result directly without a `.get` default. `worksets` is normalized to `{name: path_str}`;
`standalone` is passed through as stored.

## Write-time key ordering

`save_registry` persists only the canonical sections, defaulting a missing one to empty, and sorts
the keys of two of them for stable diffs:

* `_NAME_SECTIONS` — name → path sections, sorted on write, matching the legacy `names.yaml`
  writer's shape. Today that is `worksets`.
* `_SORTED_BLOB_SECTIONS` — also name-keyed and also sorted, but the value is a blob rather than a
  bare path. `deregistered` is name → entry dict, so it sorts like a name section without the
  path-string coercion.

`save_section` is the read-modify-write form: it reads the current registry, swaps one section, and
writes the whole file, so the other sections survive.

## The `standalone` section

Standalone boxes are self-describing on disk — a `box_data/` marker sits under the project root — so
`registry.standalone` is a *derived* index, not the truth. It maps the box's `<kuid>_<leaf>` name to
its root path string, and it backs the whole-name collision check (D-M13) and the drop-in import
work in the next sub-step.

`standalone_box_names` is that collision domain as a set. `register_standalone` is idempotent for a
matching `(box_name, root)` pair and overwrites the stored root when the same name re-registers a
different root, which is a moved box. `unregister_standalone` is a no-op when the name is absent.
`standalone_name_for_root` is the reverse lookup: it lets a caller — for instance the drop-in-import
sub-step — check whether an on-disk standalone root is already registered and reuse its name.

## The `deregistered` section

When `rm` runs without `--purge`, the box's metadata is retained on disk and a small recovery blob
is parked in this section, so that a later `rm <name> --purge` — or, in I2, a `register` readopt —
can find it BY NAME. The active membership is already gone at that point, so name → path resolution
against the live registry would miss it entirely; this section is the only remaining handle.

Each entry carries `kind` (`primary` | `standalone`), `workspace` (the project/workspace path, used
for readopt and create-conflict detection in later increments), `metadata` (the box dir a later
`--purge` deletes — for a primary box `std.boxes/<name>`, for a standalone box its in-tree root),
and optionally `image` and `deregistered_at`, the latter stamped at the CLI seam.
`register_deregistered` overwrites any existing entry for the name, so a re-`rm` refreshes the
retained blob. `unregister_deregistered` returns whether anything was removed, which is what makes
purge idempotent.

### Why the key is a bare name

The section is a single FLAT map keyed by the bare box NAME, with `kind` stored INSIDE each entry
rather than promoted into the key. Box names come from a single validated namespace — primary boxes
from the primary-workset `boxes:` membership, standalone boxes as canonical `<kuid>_<leaf>` names —
and the user-facing recovery verbs (`rm <name> --purge`, `register <name>`) are keyed by that bare
name. So a flat name → entry map matches the lookup exactly, and it keeps the YAML round-trip clean:
tuple keys do not serialise.

The per-kind teardown/readopt routing reads `kind` out of the entry, so no composite key is needed.
If a real primary/standalone name collision domain ever emerges, the entry already carries `kind`
and the key can be promoted to `"<kind>/<name>"` locally.

### Lookup is pure; `list` self-heals

`lookup_deregistered` is a pure read. Self-healing — dropping entries whose metadata dir is gone —
happens at the `list` / `purge` seam (`list_deregistered` and the purge handler) and never here, so
callers get a predictable lookup.

`list_deregistered` drops an entry only when its `metadata` path is empty (there is no recovery
target at all) or DEFINITIVELY gone from disk (the box was deleted out-of-band), and persists the
pruned section whenever anything was removed. This is the `list` self-heal from the design, hardened
for the transient-filesystem case now that `box list` wires it.

### `_metadata_definitively_gone` — why not `Path.exists()`

The self-heal must NOT drop a recovery pointer because of a TRANSIENT filesystem error. A plain
`Path.exists()` collapses "the dir is genuinely gone" and "I could not stat it — permission or I/O
error" into the same `False`, so an ambiguous error would false-drop a still-present box and lose
the only handle left for `register` or `purge`.

The two cases are told apart by inspecting the `OSError`. Only `ENOENT` (no such file) and `ENOTDIR`
(a path component is not a directory) prove the target is really gone. Any other error — `EACCES`,
`EIO`, `ESTALE`, and so on — is treated as "present, cannot confirm removal", and the entry is KEPT.
`os.stat` follows symlinks, matching the previous `Path.exists()` semantics, so a dangling symlink
still reads as gone.
