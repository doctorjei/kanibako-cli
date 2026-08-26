# Box Resolve — deriving a box's identity from the registries and the layout

`box_resolve` answers one question: *given a directory, is there a box here, and if so what is it
called and how was it composed?* It answers it from the REGISTRIES — the per-workset
`workset_registry` box membership plus the global `registry_store` standalone index — and from the
on-disk LAYOUT. Nothing else. There is no marker file in the user's repository saying "a box lives
here"; the registries collectively ARE the reverse index.

Every helper in the module is PURE. Each takes the resolved
`kanibako.settings.paths.StandardPaths`, the `KanibakoConfig`, and the target directory
EXPLICITLY — no hidden global reads — and none of them writes. That is deliberate: identity
derivation is consulted from several call sites and must never have a side effect that the second
caller inherits.

⚑ The three box modes are exactly `primary`, `named` and `standalone`. "Default" appears in this
module in one place only, as the NAME of the primary workset (`_PRIMARY_WORKSET_NAME`), never as a
mode.

## Where the rules come from

The design source is `plans/settings-conformance-registry-DESIGN.md`, and its letters are cited
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
* **D4** — the standalone signal is the FILE's existence, not a `project.mode` field. That field is
  going away.
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

`standalone_settings_present` is a PRESENCE check and nothing more: the standalone meta dir and the
box settings file must BOTH exist —

```
(project_dir/STANDALONE_META_DIR).is_dir() and (project_dir/BOX_META_FILE).is_file()
```

It mirrors `kanibako.settings.paths._is_standalone_meta_dir`, but deliberately does NOT read
`project.mode`. Under D4 the existence of the file is the signal; re-introducing a read of
`project.mode` here would re-couple the module to a field that is being retired.

This is the highest-precedence detection signal. A box's own in-place settings file is its
authoritative self-declaration of standalone identity, and it OVERRIDES any workset determination —
a workset must not be able to "steal" a box that has declared itself standalone (Jei).

## The mode precedence — D3-mode, first match wins

`detect_box_mode` tries four cases in order:

1. **In-place settings file present → `standalone`.** The self-declaration described above.
2. **Enumerate the worksets and scan** their per-workset registries for a `boxes:` entry whose PATH
   equals *project_dir* → that workset's mode, `primary` or `named` (D10).
3. **Otherwise the existing `kanibako.settings.paths.detect_project_mode` treewalk** — composed, not
   duplicated. A `named` or `standalone` result passes straight through. A `primary` result does
   NOT: it is `detect_project_mode`'s NO-MARKER default (its case 4), and in the new model primary
   membership is authoritative via the registry scan in case 2. So an unregistered directory is not
   an existing box and the function returns `None`, which is what sends the caller down its create
   path.

   That same `primary → None` collapse also swallows `detect_project_mode`'s case-2
   genuine-primary result. It is safe because new-model primary membership now lives SOLELY in the
   per-workset registry — the global `projects:` section was RETIRED in the clean split of
   2026-07-08.
4. **Otherwise `None`** — not a box.

## Scanning for an owner

`_find_owning_box` walks every workset from `_enumerate_worksets`, resolves that workset's
per-workset registry path (honoring a `workset.registry` repoint via the workset's own settings),
loads its `boxes:` membership, and returns the entry whose PATH equals *project_dir*. BOTH sides are
`resolve()`d so that symlinked, relative and trailing-slash spellings of the same directory compare
equal. `None` when no workset owns the directory.

Its `config` parameter is unused — it is there for signature parity with the other entry points;
the enumeration is sourced entirely from `StandardPaths`.

## External connect, and members stranded by a repoint

`find_connected_external_box` resolves *project_dir* — or an ANCESTOR of it — to a box registered
OUTSIDE the current composition. Two distinct situations land here: a genuine external connect, and
a member registered before a `workset.workspaces` repoint that now sits outside the workspaces
directory.

It enumerates every NAMED workset from the global `worksets:` section, scans each one's
per-workset `boxes:` membership (again honoring a `workset.registry` repoint), and returns the
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
by its REGISTERED path. The root-wide skip stranded exactly those members — bifrost A0, 2026-08-02.

`resolve_workset_workspaces` guards non-mapping settings documents itself, and it consumes the same
`settings` value the registry-path resolver above it consumes, so the scan does not need its own
mapping check.

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

`enable_vault` is intentionally NOT sourced here. It is the settable `box.enable_vault` key, already
handled in P2.

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

⚑ The standalone branch sources everything from the DETECTED box root (`result.project_root`), NOT
from the passed-in *project_dir*. The two diverge when standalone is detected by the treewalk from
a SUBDIRECTORY — case 3 finds the marker at an ancestor. Using *project_dir* would anchor the name,
the workspace and the registration lookup on the wrong directory. The orphan branch at the end of
the function mirrors this for the same reason.

## History — what this module replaced

`box_resolve` is the replacement for the legacy `read_project_meta` derivation, which read
`project:` and `resolved:` sections out of an on-disk meta file.

It arrived as additive phase-P4 infrastructure and became the LIVE identity source in P5a, when the
former `read_project_meta` consumers were pointed here. The legacy on-disk-meta helpers
(`read_project_meta` and `write_project_meta`) were then DELETED in P8c, once sparse create (P8b)
stopped writing the `project:`/`resolved:` sections they read.
