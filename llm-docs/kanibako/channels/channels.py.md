# Channel Paths — the two scopes, and this box's partition addresses

`channels/channels.py` turns an already-resolved `ProjectPaths` (`proj`) plus `StandardPaths`
(`std`) into the host-side channel paths: the system-scope and workset-scope channel roots, and
the per-box mailbox/share addresses inside them. It is **pure derivation** — it computes paths and
changes no behavior: it creates no directories, touches no launch mounts, and seeds no files.

It arrived as sub-step 6a of Phase 6 of the 1.6.0 config/settings revamp. The bind production, the
`mkdir` and the file-seeding that consume these paths live in sub-step 6b, on the launch path.

## The two channel scopes (TARGET §1, §2c, §2f)

**System scope** — five type roots under `@system.channelroot`: `common`, `chat`, `broadcast`,
`mailboxes`, `share`. The two instance-owned types (`mailboxes`, `share`) are *partitioned* by the
workset-name token: `mailboxes/<ws>` and `share/<ws>`, where `<ws>` is `__PRIMARY__` | `<named>` |
`__STANDALONE__`.

⚑ **These partition roots apply to EVERY mode, standalone included (D-M9).** Do not gate
`system_partition` off the workset-local channels: a standalone box has no workset channels and
still has a `__STANDALONE__` partition. The one-line warning at that function is the guard against
re-introducing this.

**Workset scope** — the `workset.channels.*` family declares **SIX** leaves, and they split into two
groups with **different mode rules**:

* The four **workset-LOCAL** leaves default under the resolved `workset.channelroot` (default
  `@meta.workset.path/channels`): `common`, `chat`, `share`, and `broadcast` (which defaults to
  `@workset.channels.chat/broadcast.md` and names a FILE, not a dir). These exist for PRIMARY and
  NAMED modes ONLY. Standalone has no workset-local channels — its `workset.channelroot` is
  `<None>` per the TARGET, so `workset_channel_paths` returns `None` and `~/channels/workset/*` is
  omitted (A10). `has_workset_channels` is the single predicate for that split;
  `launch/templates.py` uses it to decide whether the workset mount exists at all.
* The two **ALL-PROJECTS** leaves, `mailboxes` and `share_global`, default to the SYSTEM partition
  (`@system.channels.mailboxes/@meta.workset.name`, `@system.channels.share/@meta.workset.name`)
  and exist in **every mode**, standalone included. They are `workset_partition_paths`, NOT
  `workset_channel_paths`, and reading them off the `None`-for-standalone helper is what left them
  installed by no floor in any mode.

⚑⚑ **EVERY LEAF RESOLVES THROUGH ITS OWN DECLARED KEY — never a join onto the root.** The joins
*are* the spec's defaults, which is why the un-keyed version looked correct for so long: it
produced the right paths and obeyed no key. What it cost (R-35, ratified "fix the CODE"): `chat`
became a split carrier — the `~/channels/workset/chat` bind followed the override while
`start.py._seed_channel_files` kept joining `<channelroot>/chat`, so a repoint mounted one
directory and seeded the chat logs into another, mounted nowhere, while the bible tells every agent
its logs live at `~/channels/workset/chat`. `share` had the same split, latent. `broadcast`,
`mailboxes` and `share_global` had no consumer at all: `config set` took the value, `config get`
read it back, and nothing changed.

The repoint reader takes its FILE SLOT from `config_keys._KEY_ROUTES`, the same table `config set`
writes through, so the slot read and the slot written cannot drift. A repoint's own grammar
(`@`-refs, `$XDG_*`, `~`, the relative anchor, the refusal that names the key) belongs entirely to
the one pre-snapshot route, `settings/workset_dirkeys.resolve_workset_dir_key` — this module adds
no second grammar. What it *does* own is each key's DEFAULT, because those hang off the resolved
channel root or the system partition, which is the one thing that route cannot supply.

`general.md` is the ONE leaf still joined by hand (`CHAT_GENERAL_LEAF`): no key declares the
default chat log. It is joined onto the RESOLVED chat dir, and its sibling `broadcast.md` must
never be joined the same way, because that one IS a key.

## The token and the root are DERIVED here, not carried on `ProjectPaths` (A8)

Neither the workset-name token nor the workset root is carried verbatim on `ProjectPaths` — the
legacy comms block used only `proj.name`. Per the design-review A8 resolution they are derived
HERE from `proj.mode` + `proj.group`, rather than widening the resolver's public shape:

| mode | workset root (`@meta.workset.path`) | token (`@meta.workset.name`) |
|---|---|---|
| PRIMARY | `@config.primary_workset` (`std.primary_workset`) | `__PRIMARY__` |
| NAMED | `proj.group.root` | `proj.group.name` |
| STANDALONE | `proj.metadata_path` | `__STANDALONE__` |

The standalone row is the one that surprises: for a standalone box `metadata_path` **is** the
project root, and the workspace is a `workspace/` subdir beneath it — so `project_path` is not the
root and must not be substituted here.

`WS_TOKEN_PRIMARY` and `WS_TOKEN_STANDALONE` are the reserved tokens standing for the PRIMARY and
STANDALONE pseudo-worksets. A named workset may not use either; Phase 5 (5e) reserves them at
workset-create time, so the derivation can treat a `<named>` token as unambiguous.

Both derivations raise `ValueError` when a NAMED box is missing its workset group or name: the
partition key would otherwise be silently wrong, which addresses a box's mail to the wrong place.

## The addresses — `meta.box.*` (TARGET §2c)

`box_channel_addresses` produces this box's own dirs *within* the partitioned roots, from the
workset token plus the box name (`proj.name`):

⚑ All three hang off the KEYS, which is the manifest's own spelling of them — not off
`system_partition`. Reading the partition directly is what let a user repoint `mailboxes`, watch
`config get` read the new value back, and still have their inbox mounted at the old address.

* `inbox` == `@workset.channels.mailboxes/<box>` — this box's own mailbox dir, also surfaced
  in-box at `~/channels/inbox`.
* `share_global` == `@workset.channels.share_global/<box>` — this box's own system-scope
  publication dir.
* `share_workset` == `@workset.channels.share/<box>` — this box's own workset-scope publication
  dir; `None` for standalone.

It raises `ValueError` on a nameless box rather than deriving an address ending in an empty
segment. That raise is depended upon: `commands/start.py` carries comments at its ephemeral/no-name
paths noting that `box_channel_addresses` RAISES `"box has no name"`, and arranges for a name to be
resolved before calling it.

### The five carriers

* `SystemPartition` — `mailboxes` == `@system.channels.mailboxes/<ws>`, `share` ==
  `@system.channels.share/<ws>`. These are the *parents* under which each box gets its own `<box>`
  subdir. ⚑ **It is the DEFAULT of the two ALL-PROJECTS keys, not the keys themselves.**
* `WorksetPartition` — the same pair of paths reached THROUGH
  `workset.channels.{mailboxes,share_global}`, so a repoint shows up here and not in
  `SystemPartition`. Same shape, different question; do not collapse the two. Built in exactly one
  place, `partition_key_paths(std, ws_token, ws_root)`; `workset_partition_paths` (from a resolved
  `ProjectPaths`) and `own_partition_dirs` (from raw relocation inputs) are its two entry points,
  and they are two entry points rather than two derivations because they used to be two
  derivations and answered differently.
* `WorksetChannels` — the workset-local leaves: `root`, `common`, `chat`, `chat_broadcast` (the
  `broadcast` KEY), `chat_general` (the non-key `general.md` inside the resolved chat dir), and
  `share` (whose per-box subdirs are `meta.box.share_workset`).
* `BoxChannelAddresses` — the `meta.box.*` addresses above.
* `OwnPartition` — this box's own dirs, `mailbox` and `share_global`, addressed by raw
  `(ws_token, ws_root, box_name)`.

## `own_partition_dirs` — the raw-input primitive behind the addresses (6d)

`own_partition_dirs` is the lower-level primitive underneath `box_channel_addresses`: it takes the
workset-name token, the workset ROOT and the box name directly, with no `ProjectPaths`. That is
what the move/convert relocation in `commands/box/_lifecycle.py` needs: `_relocate_channel_partition`
must compute BOTH the OLD and the NEW partition for a box being moved between worksets, and it
works from a pair of `ProjectState`s (via its own `_state_ws_token` and `_state_ws_root`), never
from a resolved `ProjectPaths`.

⚑⚑ **THE GAP THAT USED TO BE HERE IS CLOSED (2026-08-26), and how it read is worth keeping.** This
function took `(std, ws_token)` alone, which is exactly enough to build the partition's DEFAULT and
not enough to read a `workset.channels.{mailboxes,share_global}` repoint — while
`box_channel_addresses` has routed through those keys since R-35. So a workset that repointed
`mailboxes` had its boxes MOUNTED at the repointed address, and a `box move` out of it moved the
default directory: an empty one, leaving every message the box had received stranded at an address
no longer registered to it. It was not "two paths disagreeing by accident" — it was one of them
consulting a key the other could not see, and the fix is to let it see: `ws_root` is now a REQUIRED
keyword, and both entry points resolve through the single `partition_key_paths`. An OPTIONAL root
would have reproduced the old behaviour for any caller who omitted it, silently, which is the shape
of the bug rather than a mitigation of it.

⚑ Reading a key put a REFUSING resolver on the relocation path for the first time —
`partition_key_paths` raises (naming the key) on a repoint it cannot resolve, as every pre-snapshot
key read does. `_relocate_channel_partition` catches that, warns and skips: it runs AFTER the files
have moved, and a settings error in a best-effort cleanup step must not abort a lifecycle operation
that is otherwise complete.

Relocation itself is best-effort by contract (spec §2f, D-M10): a box's partition key is
`@meta.workset.name`, so moving it changes its channel address; its OWN mailbox and share move,
and stale cross-box references to the old address may break, with no forwarding marker.

## `workset.channelroot` is resolved, never hard-coded (§3.3)

`workset_channel_paths` does not join `@meta.workset.path/channels` itself. It calls
`resolve_workset_channelroot(ws_root, load_workset_settings_doc(ws_root))` from
`project/workset.py`, so a repoint of `workset.channelroot` in the workset's own `workset.yaml`
is honored — the spec rules that key must be *"real and USED — not hard-coded"*. The default is
what the join would have produced; the resolution is what makes the key mean anything.

Per A3 these are derived HELPERS, not new fields on `ProjectPaths`. The module's imports of
`kanibako.settings.paths` (`BoxMode`) and `kanibako.project.workset` are therefore function-level:
`paths.py` imports this module, so a module-scope import would close a cycle, and keeping the
imports inside the functions also keeps this pure-derivation module import-light at load. The
`TYPE_CHECKING` block exists for the same reason.

## Who calls this

* `settings/core_defaults.py` — `box_channel_addresses` + `workset_channel_paths` feed the
  `meta.box.*` / `workset.channels.*` defaults.
* `commands/start.py` — the launch path takes `workset_name_token`, `box_channel_addresses` and
  `workset_channel_paths`. Its comments state that the token is SINGLE-SOURCED here precisely so
  the token used for the runtime metadata and the token used for the partition cannot drift.
* `settings/settings_launch.py` — documents the same single-sourcing at the launch seam.
* `commands/box/_lifecycle.py` — `own_partition_dirs` plus both reserved tokens, for move/convert.
* `launch/templates.py` — `has_workset_channels`, to gate the workset-local template layer.

Import it as the SUBMODULE (`from kanibako.channels.channels import box_channel_addresses`); the
package `__init__.py` re-exports nothing, by design.

## Permissions on these paths are CONVENTIONS, not guarantees

This module derives paths only, but a reader arriving from the spec's channel table should not read
"write-only mailbox" or "read-only share" as something the paths enforce.

🛑 **Spec §2f states the "Other-crab perms" column is ASPIRATIONAL, not enforced (D-M11).** Under
the current option (A) every channel is `rw`-bound: any box can technically read or overwrite any
other box's mailbox, share, common or chat. This is the deliberate single-operator box↔box trust
stance — box↔HOST isolation is unaffected. Real access limits land later with option (C), which
keeps the guest PATHS but flips `rw*`→`ro` and routes writes through the helper socket. Do not
write a comment, doc or error message that promises the current partitioning enforces anything.

---

## Completeness sweep (relocation pass, 2026-08-20)

Everything removed from the source is carried above, with two deliberate duplication drops:

1. **The second lazy-import rationale** in `workset_channel_paths` (*"mirrors the module's other
   function-level imports; keeps this pure-derivation module import-light at load"*) — the same
   fact as the note at `workset_name_token`'s import and the `TYPE_CHECKING` comment, both of which
   survive in source; the full reason is under `workset.channelroot is resolved` above.
2. **`OwnPartition`'s restatement of its own two path formulas**, which are already spelled at
   `SystemPartition` and in `box_channel_addresses`'s address list.

Every symbol keeps a one-line docstring. The `⚑` D-M9 warning at `system_partition` and the
standalone-root note at `workset_root` stayed in source under the keep test: each marks the exact
line where a plausible edit would silently break something.
