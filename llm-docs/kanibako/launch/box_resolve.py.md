# Box Resolve — deriving a box's identity from the registries and the layout

`box_resolve`'s main question is: *given a directory, is there a box here, and if so what is it
called and how was it composed?* It answers it from the REGISTRIES — the per-workset
`workset_registry` box membership plus the global `registry_store` standalone index — and from the
on-disk LAYOUT. Nothing else. There is no marker file in the user's repository saying "a box lives
here"; the registries collectively ARE the reverse index.

Each helper takes its inputs explicitly (the resolved `kanibako.settings.paths.StandardPaths`, the
`BootstrapConfig`, and the target directory) — no hidden global reads. `walk_box_mode` refuses
another path to a registered standalone ([R188]); `detect_box_mode` is that walk plus
`import_reconcile.reconcile`, which imports an unregistered workset root (and walks again) and
clears a registered standalone root's stale import entry.

The module also owns:

* **The marker-gone refusal.** `standalone_marker_error(root, name)` is the one message for a
  registered standalone whose `workset.yaml` no longer stores the marker (D4 state 3): a launch,
  `duplicate`, `move`, `convert`, and `remap` refuse with it, while `rm` and `info` still act on
  the box. `standalone_marker_refusal` builds its head (shared with `box register`),
  `standalone_marker_repair` gives the `(fault, cure)` pair, and
  `registered_standalone_marker_error` applies it when the standalone registry holds the root.
* **`recorded_workspaces_under(std, tree, *, standalone=True, primary=True)`** — every box whose
  RECORDED workspace is *tree* or lies under it, as `(owner, path)`. A safety test, so paths
  compare both literal and resolved.
* **`detect_box_or_default`** — `detect_box_mode`, with "not a box" reported as primary AT the
  directory (the create default).
* **`held_standalone_stores(registry, own_root)`** — `{store name: root}` for every OTHER
  registered standalone whose root holds a valid kuid; `snapshots.adopt_standalone_store` never
  moves one.
* **`unregistered_box_refusal(proj, belongs, name_cure)`** — the refusal head for the nameless
  primary, else `None`.

⚑ The three box modes are exactly `primary`, `named` and `standalone`. "Default" appears in this
module in one place only, as the NAME of the primary workset (`_PRIMARY_WORKSET_NAME`), never as a
mode.

## Where the rules come from

The design source is `~/canon/workbook/designs/settings-conformance-registry-DESIGN.md`, and its letters are cited
throughout the source because each one is a decision that would otherwise look arbitrary:

* **D0/D1** — the primary workset is NON-EXCEPTIONAL. It is anchored by `config.primary_workset`
  rather than listed in the global `worksets:` discovery section, which is why the enumeration has
  to yield it explicitly instead of just iterating the section.
* **D1b — the registry entry KEY *is* the box name.** Names are not stored in a `name:` field
  anywhere; the `name: path` mapping key is the name. Read the key, do not look for a field.
* **D3-mode** — the mode-detection PRECEDENCE, enumerated below.
* **D3-auth** — membership authority runs registry ⇒ dirs. The registry is authoritative and the
  directory layout follows it, so a workset-contained directory with no registry entry is a real
  detection result with `registered=False`, not an error.
* **D4** — the standalone signal is the root `registry.yaml` holding only `__STANDALONE__`. There
  is no `project.mode` field.
* **D10 — enumerate-and-scan.** All worksets are reachable up front, so their per-workset registries
  collectively form the reverse index. This is what removes the need for any marker in the user's
  repo, and it replaces the global `connected:` index together with
  `workset._find_connected_project`.
* **P6d** — the standalone name is composed LIVE from a stored kuid plus the current directory leaf.

## The primary workset's name

`_PRIMARY_WORKSET_NAME` is the string `"default"`. It mirrors the name `_default_project_group`
produces. Because the primary workset is anchored by `config.primary_workset` (== `std.primary_workset`)
and is not a row in the global `worksets:` section, `_enumerate_worksets` yields it first, by hand,
and then iterates the section for every NAMED workset.

## The standalone marker

`is_standalone_root` tests ONE thing: whether the dir's OWN `registry.yaml` holds the mark
(`settings.config.holds_standalone_mark`). The mark is the DEFINITION of standalone (system-design
§ Detection & import) — this root's file only, never the cascade — so a bare `box_data/` is not
enough. First it refuses a retired `workset.registry` the root's `workset.yaml` still stores
(`settings_assemble.refuse_retired_registry_key`): a pre-mark standalone's `registry: null` is
refused, naming the cure, rather than read as "not standalone".

It mirrors `kanibako.settings.paths._is_standalone_meta_dir`, but deliberately does NOT read
`project.mode`; re-introducing that read here would re-couple the module to a retired field.

A box's own in-place mark is its self-declaration of standalone identity. It OVERRIDES any
workset TREE determination — a workset must not be able to "steal" a box that has declared itself
standalone — but a registry entry AT the path outranks it (D3-mode, below).

## The mode precedence — D3-mode, first match wins

`walk_box_mode` is the one carrier of this order (system-design § Detection & import). Every path
compares literally (links unfollowed). It writes nothing but `list_worksets`'s drop
(`tests/test_detection_nowrite.py` runs it on a read-only tree).

1. **The path space's registry has a box AT *project_dir*** → that box's mode, `primary` or
   `named`. The path space is the named workset whose tree holds the dir, else primary
   (`_path_space_workset`, the lookup `create` uses).
2. **The standalone registry has a box AT *project_dir*** → `standalone`, marker or not. A root that
   lost its marker is that box, broken (D4 state 3), never "not a box".
3. **An in-place mark** (the marker above) → `standalone`.
4. **The `kanibako.settings.paths.walk_project_mode` treewalk** — composed, not duplicated. A
   `primary` result AT *project_dir* is its no-marker default, so not a box: `None`, which sends
   the caller down its create path; its step 5 stays a `Treewalk`. Any other result passes through, including a `primary` at an
   ANCESTOR (a subdirectory launch).

## Scanning for an owner

`_find_owning_box` walks every workset from `_enumerate_worksets` (only the one
`workset=` names, which case 1 passes), loads that workset's `boxes:` membership, and returns the entry whose PATH equals *project_dir*. Paths compare literally (links
unfollowed), so twins sharing a target stay two boxes. `None` when no workset owns the directory.

Its `config` parameter is unused — it is there for signature parity with the other entry points;
the enumeration is sourced entirely from `StandardPaths`.

## External connect, and members stranded by a repoint

`find_connected_external_box` resolves *project_dir* — or an ANCESTOR of it — to a box registered
OUTSIDE the current composition. Two distinct situations land here: a genuine external connect, and
a member registered before a `workset.workspaces` repoint that now sits outside the workspaces
directory.

It enumerates every NAMED workset from the global `worksets:` section, scans each one's
per-workset `boxes:` membership, and returns the
entry whose registered path is *project_dir* or a proper ancestor of it. DEEPEST registered path
wins — the same ancestor semantics the legacy `connected:` index used, so that launching from a
SUBDIRECTORY of a connected directory still resolves to the box.

The PRIMARY workset is skipped: external boxes in it were never in `connected:` and resolve by
their own name index instead.

⚑ **The skip inside the scan is "under the workset's CURRENT resolved `workset.workspaces` dir",
and it is deliberately NOT "under the workset root."** Boxes under the current workspaces dir — and
only those — are resolved by ordinary location detection, so skipping them here is correct.
Widening the skip to the whole workset root is not: the registry's `boxes:` membership is the SOLE
authoritative name → workspace store, and a member registered under an OLD composition (in-root,
before a `workset.workspaces` repoint) is invisible to the workspaces walk. It has to resolve HERE,
by its REGISTERED path. Under a null `workset.workspaces` the skip still uses the default leaf
(`project.workset.resolve_workspaces_locator`), where members connected before the null sit.
Detection calls `find_connected_member` (no skip; its mark peek reads `workset.yaml`), and
`path_space_worksets` (shared with `names._space_member`) gives the path space by root or
registered member path alone
(system-design § *Box designation*). The guards keep the skip.
Detection therefore loads no workset and raises none of `_load_workset`'s identity refusals:
the resolvers re-raise `errors.WORKSET_IDENTITY_REFUSALS`, and `refuse_path_space_identity`
serves `resolve_box_identity` and `box rm <path>`, which load none.

The scan does not need its own mapping check: `settings` comes from `config_io.load_doc`, which
returns a mapping or refuses the file by name.

## The identity dict

`resolve_box_identity` returns `{mode, name, workspace, registered}`, or `None` when *project_dir*
is not a box. The fields are sourced per D1b and D3-auth:

* **`mode`** — the `BoxMode` from `detect_box_mode`.
* **`name`** — the registry entry KEY (D1b). For a workset box that is the per-workset `boxes:`
  key. For a standalone box it is composed live, see below.
* **`workspace`** — the registry entry PATH for a workset box; the layout directory for a
  standalone or an orphan.
* **`registered`** — whether membership is present (D3-auth): the per-workset `boxes:` for a
  workset box, the global `standalone:` for a standalone box.

`enable_vault` is intentionally NOT sourced here. It is the settable `box.enable_vault` key.

The last branch covers a `named` result with NO registry entry — a workset-contained but
unregistered directory. Under D3-auth the registry is authoritative and the directories follow, so
such an orphan is reported with `registered=False` and with its name and workspace derived from the
layout, i.e. from the detected box root.

## Composing a standalone name (P6d)

For a standalone box the name is `<stored workset.kuid>_<live leaf>`. The kuid is the STABLE stored
prefix and the leaf tracks the CURRENT directory, so a standalone box that is MOVED keeps its
identity while its leaf follows the new directory. The kuid is read from the box's own
`workset.yaml` — the workset tier, for a standalone — via `read_workset_kuid`.

A pre-kuid box has no stored `workset.kuid` and reads back as `kuid.SENTINEL`. It falls back to the
registered `standalone:` registry KEY, and if that is absent too, to the directory leaf.

That rule is `standalone_box_name(box_root, registered_name)`, which `resolve_box_identity` calls.
It is public because a box's log files are named for this name: the standalone `box rm --purge`
teardown calls it BEFORE deleting the root settings file that carries the kuid, passing the stored
registry name, since by then the box's registry entry is already gone. When the current directory
name has no ASCII spelling it raises `DerivedBoxNameError` with its cure
(`CURE_MOVED_LEAF_NOT_ASCII`); there is no fallback name.

⚑ The standalone branch sources everything from the DETECTED box root (`result.project_root`), NOT
from the passed-in *project_dir*. The two diverge when standalone is detected by the treewalk from
a SUBDIRECTORY — case 3 finds the marker at an ancestor. Using *project_dir* would anchor the name,
the workspace and the registration lookup on the wrong directory. The orphan branch at the end of
the function mirrors this for the same reason.
