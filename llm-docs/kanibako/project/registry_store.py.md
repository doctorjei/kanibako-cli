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
  k3xy0_proj: /home/user/src/proj      # box.name → root

deregistered:
  # box.name → retained-recovery blob for a box removed by ``rm`` without ``--purge``.

rigs:
  corp/base:1.0: {kind: prefab, ...}

image_shells:
  sha256:abc...: /bin/bash
```

Every write puts `REGISTRY_HEADER` atop the file: the file is generated, a top-level section
kanibako does not know is dropped with a warning, and every comment is lost.

`worksets` carries the workset name → root registry, used both for name-based lookups AND to
discover and list worksets.

`standalone` maps a registered standalone box's canonical `<kuid>_<leaf>` name to its root path
string.

`rigs` carries added-rig records keyed by rig name; the `rig_registry` module owns its shape.
`image_shells` maps an image store key to its captured login shell; the `shells` module owns its
shape. Both sections are
read and written by their owning modules through `load_section` / `save_section`, which preserve
sibling sections; this module passes their values through verbatim and never interprets them.

## What is deliberately absent

**There is no global `connected:` external-connect index.** Connections live in each workset's
per-workset registry as a `boxes:` entry (design D10).

**There is no `projects` section.** A PRIMARY box's identity lives SOLELY in the primary workset's
per-workset `boxes:` membership (`@config.primary_workset/registry.yaml`, via
`kanibako.project.workset_registry`), which is the AUTHORITATIVE source of box names (spec §2c's
`workset.registry` row). `_SECTIONS` is the retirement mechanism: a section absent from it is
neither surfaced by the loader nor rewritten, so a stale `projects` block is dropped on the next
write and printed as a warning naming the file (`write_registry_doc`) — no migration, no legacy
read.

**There is NO `seeded` section, and there must not be one.** Registry MEMBERSHIP is itself the seed
signal: a box present here — a `standalone` entry, a NAMED workset-local list entry, or a PRIMARY
per-workset `boxes:` membership — was seeded at its `create` (seed-then-register:
`~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0 "Seed-time vs cascade"; `~/canon/workbook/specs/system-design-1.8.0.md` § "Detection &
import").

⚑ **The implication runs ONE WAY: present ⇒ seeded, never the converse.** A standalone box created
without `--register` (§D4a) is seeded and ABSENT from the registry until `box register` indexes it —
which is exactly why that verb is seed-free.

## The path contract

Every public function takes the resolved `config.registry` FILE path (callers pass `std.registry`),
which is the single source of the registry location. A user who repoints `config.registry` is
honored end-to-end; nothing here reconstructs the path from `config.data`.

No on-disk migration is performed — the old files are NOT read. A fresh tree, with no
`registry.yaml` at all, yields empty sections rather than an error. Writes are atomic, via
`config_io.dump_doc` (temp file plus `os.replace`), and every read-modify-write runs under
`rmw_lock(registry)`.

`write_registry_doc` is THE ONE registry writer, shared with the per-workset registries: it writes
the given sections in order under `REGISTRY_HEADER`, sorts the requested ones, and drops every
other top-level section — from the document or only on disk — printing each with a warning naming
the file. Never refused, never lost silently.

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

Standalone boxes are self-describing on disk — the root `workset.yaml` stores `workset.registry:
null` — so `registry.standalone` is a *derived* index, not the truth. It maps the box's
`<kuid>_<leaf>` name to its root path string, and it backs the whole-name collision check and
`box register`.

`standalone_box_names` is that collision domain as a set. Name lookups are case-blind (spec §0
NAMING RULES; `identifiers.find_identifier`), and a key is stored as given:

* `standalone_root` is the one name → root read.
* `register_standalone` is idempotent for a matching `(box_name, root)` pair and overwrites the
  stored root when the same name re-registers a different root, which is a moved box. It REPLACES a
  case-variant row for the same box (`_drop_case_twin`), and drops a stale `deregistered` row
  parking the same directory, since a purge by that row would delete the live box's metadata.
* `unregister_standalone` is a no-op when the name is absent; it removes the STORED spelling.

Reverse lookups by root:

* `standalone_name_for_root` — the name registered at exactly *root* (literal compare).
* `standalone_name_for_same_dir` — that, else a row whose root is the same directory by any
  spelling (`os.path.samefile`).
* `standalone_identity_holder` — the row at ANOTHER spelling of *root*'s identity: one directory,
  or one shared `workset.yaml` file (a link, bind mount, or hard link). A registered *root* whose
  own leaf is not a link keeps its identity against a holder whose leaf is. These are SAFETY
  tests, never identity lookups ([R188]): identity stays literal.

## The `deregistered` section

When `rm` runs without `--purge`, the box's metadata is retained on disk and a small recovery blob
is parked in this section, so that a later `rm <name> --purge` — or, in I2, a `register` readopt —
can find it BY NAME. The active membership is already gone at that point, so name → path resolution
against the live registry would miss it entirely; this section is the only remaining handle.

Each entry carries `kind` (`primary` | `standalone`), `workspace` (the project/workspace path, used
for readopt and create-conflict detection in later increments), `metadata` (the box dir a later
`--purge` deletes — for a primary box `std.boxes/<name>`, for a standalone box its in-tree root),
optionally `image`, and `deregistered_at`, which `register_deregistered` stamps itself.
`register_deregistered` overwrites any existing entry for the name, and replaces a case-variant
row, so a re-`rm` refreshes the retained blob. `unregister_deregistered` (case-blind) returns
whether anything was removed, which is what makes purge idempotent.

* `deregistered_matches_for_workspace(registry, path)` returns EVERY entry naming *path* as its
  `workspace` or `metadata` (same directory, any spelling). A list, not a first match: a rewritten
  PRIMARY and a STANDALONE rooted there can both name one path.
* `deregistered_standalone_for_dir(registry, root)` filters that list to the standalone entry
  parking *root*; `rm <path> --purge` uses it so the purge also drops the parked entry.

### Why the key is a bare name

The section is a single FLAT map keyed by the bare box NAME, with `kind` stored INSIDE each entry
rather than promoted into the key. Box names come from a single validated namespace — primary boxes
from the primary-workset `boxes:` membership, standalone boxes as canonical `<kuid>_<leaf>` names —
and the user-facing recovery verbs (`rm <name> --purge`, `register <name>`) are keyed by that bare
name. So a flat name → entry map matches the lookup exactly, and it keeps the YAML round-trip clean:
tuple keys do not serialize.

The per-kind teardown/readopt routing reads `kind` out of the entry, so no composite key is needed.
If a real primary/standalone name collision domain ever emerges, the entry already carries `kind`
and the key can be promoted to `"<kind>/<name>"` locally.

### Lookup is pure; `list` drops only empty entries

`lookup_deregistered` is a pure, case-blind read, so callers get a predictable lookup.

`list_deregistered` drops an entry only when its `metadata` is empty (there is no recovery target
at all), and persists the pruned section under `rmw_lock` whenever anything was removed. An entry
whose directory is gone STAYS: `box list` prints its `rm --purge` cure, and that purge needs the
entry to drop it.

### `_metadata_definitively_gone` — why not `Path.exists()`

Callers that ask whether a box dir is gone (the purge paths in `commands/box/_parser.py` and
`_lifecycle.py`, and `project/workset.py`) must NOT act on a TRANSIENT filesystem error. A plain
`Path.exists()` collapses "the dir is genuinely gone" and "I could not stat it — permission or I/O
error" into the same `False`, so an ambiguous error would false-drop a still-present box and lose
the only handle left for `register` or `purge`.

The two cases are told apart by `utils.stat_unless_gone`. Only `ENOENT` (no such file) and `ENOTDIR`
(a path component is not a directory) prove the target is really gone. Any other error — `EACCES`,
`EIO`, `ESTALE`, and so on — is treated as "present, cannot confirm removal".
`os.stat` follows symlinks, so a dangling symlink reads as gone.
