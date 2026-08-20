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

**Workset scope** — three type roots under the resolved `workset.channelroot` (default
`@meta.workset.path/channels`): `common`, `chat`, `share`. These exist for PRIMARY and NAMED modes
ONLY. Standalone has no workset-local channels — its `workset.channelroot` is `<None>` per the
TARGET, so `workset_channel_paths` returns `None` and `~/channels/workset/*` is omitted (A10).
`has_workset_channels` is the single predicate for that split; `launch/templates.py` uses it to
decide whether the workset mount exists at all.

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

* `inbox` == `@system.channels.mailboxes/<ws>/<box>` — this box's own mailbox dir, also surfaced
  in-box at `~/channels/inbox`.
* `share_global` == `@system.channels.share/<ws>/<box>` — this box's own system-scope publication
  dir.
* `share_workset` == `@workset.channels.share/<box>` — this box's own workset-scope publication
  dir; `None` for standalone.

It raises `ValueError` on a nameless box rather than deriving an address ending in an empty
segment. That raise is depended upon: `commands/start.py` carries comments at its ephemeral/no-name
paths noting that `box_channel_addresses` RAISES `"box has no name"`, and arranges for a name to be
resolved before calling it.

### The four carriers

* `SystemPartition` — `mailboxes` == `@system.channels.mailboxes/<ws>`, `share` ==
  `@system.channels.share/<ws>`. These are the *parents* under which each box gets its own `<box>`
  subdir.
* `WorksetChannels` — the workset-local roots: `root`, `common`, `chat` (plus the reserved
  `broadcast.md` and the default `general.md` inside it), and `share` (whose per-box subdirs are
  `meta.box.share_workset`).
* `BoxChannelAddresses` — the `meta.box.*` addresses above.
* `OwnPartition` — this box's own system-scope dirs, `mailbox` and `share_global`, addressed by raw
  `(ws_token, box_name)`.

## `own_partition_dirs` — the raw-token primitive behind the addresses (6d)

`own_partition_dirs` is the lower-level primitive underneath `box_channel_addresses`: it takes the
workset-name token and box name directly, with no `ProjectPaths`. That is what the move/convert
relocation in `commands/box/_lifecycle.py` needs: `_relocate_channel_partition` must compute BOTH
the OLD and the NEW partition for a box being moved between worksets, and it works from a pair of
`ProjectState`s (via its own `_state_ws_token`), never from a resolved `ProjectPaths`. It mirrors
`system_partition` plus the `meta.box.{inbox, share_global}` joins (TARGET §2c), so the relocation
and the launch path cannot address the partition differently.

Relocation itself is best-effort by contract (spec §2f, D-M10): a box's partition key is
`@meta.workset.name`, so moving it changes its channel address; its OWN mailbox and share move,
and stale cross-box references to the old address may break, with no forwarding marker.

## `workset.channelroot` is resolved, never hard-coded (§3.3)

`workset_channel_paths` does not join `@meta.workset.path/channels` itself. It calls
`resolve_workset_channelroot(ws_root, load_workset_settings_doc(ws_root))` from
`project/workset.py`, so a repoint of `workset.channelroot` in the workset's own `settings.yaml`
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
