# Migrating to kanibako 1.6.0

> **DRAFT — covers the implemented phases of the config/settings revamp.**
> This is a **manual** runbook: 1.6.0 ships **no migration code**. You edit your
> on-disk state from the old (≤ pre-revamp) layout to the new layout *before*
> installing 1.6.0. The sections below describe each old→new change as a set of
> numbered steps with before→after tables and tree diagrams.
>
> The **Channels**, **Templates / host-config removal**, and **Agent descriptors**
> sections are **STUBS** — those phases (6/7/8) are not yet implemented and these
> sections will be filled when they land.

1.6.0 is **one breaking change set**. Read the whole document before touching
anything, take a backup of `~/.config/kanibako*` and `~/.local/share/kanibako/`,
then work top-to-bottom.

---

## 0. Quick orientation — what changed

| Area | Old (pre-1.6.0) | New (1.6.0) |
|---|---|---|
| Config files | one `kanibako.yaml` (paths + behavior mixed) | **config** files (`system.*` only) vs **settings** files (behavior), separate |
| Vocabulary | `crab` everywhere | `agent` (the tool) for config/dirs/commands; `crab` only = runtime concept |
| System paths | `system.path.*` | `system.*` (`.path` dropped), restructured |
| Modes | `ProjectMode{default,workset,standalone}` × `ProjectLayout{simple,default,robust}` | `box.mode{primary,named,standalone}`, **no layouts** |
| Primary store | scattered `boxes/`, `comms/`, `share_*/` under data root | **PRIMARY workset** is a real dir at `@system.primary_workset` |
| Per-project file | `project.yaml` (mode/layout/paths/...) | per-box `settings.yaml` + workset meta (**but see §4 note** — standalone metadata file is still named `project.yaml` for now) |
| Registry | `names.yaml` + `worksets.yaml` + `connected.yaml` | one `registry.yaml` (`@system.registry`) |
| Detection | registry-driven | **on-disk authoritative**, walk-detected, drop-in importable |

---

## 1. Config vs settings split

Behavior and layout used to live in the same `kanibako.yaml` cascade. They are now
two separate file sets.

### 1.1 CONFIG files — layout only (`system.*`)

`system.*` keys (WHERE things live) are now set **only** in config files:

```
/etc/kanibako/config_base.yaml      defaults (overridable)
~/.config/kanibako.yaml             user global
/etc/kanibako/config_required.yaml  mandatory (NOT overridable)
```

CONFIG precedence: `config_base < ~/.config/kanibako.yaml < config_required`.

### 1.2 SETTINGS files — behavior

`agent.*`, `box.*`, `workset.*` and the category keys (WHAT happens) are set **only**
in settings files:

```
/etc/kanibako/settings_base.yaml      defaults
<scope>/settings.yaml                 per-scope (system / workset / box)
/etc/kanibako/settings_required.yaml  mandatory cap
```

### 1.3 The 6-tier settings cascade (box wins; `*_required` is the cap)

```
settings_base  <  system  <  agent.<agent>  <  workset  <  box  <  settings_required
```

- **`box` wins** among the normal tiers.
- **`*_required.yaml` sits ABOVE box** — it is an absolute admin cap that overrides
  everything (standard lockdown semantics). "Box wins" applies only among the
  normal tiers.

### 1.4 Migration steps

1. Split your single `~/.config/kanibako.yaml`:
   - Keep all `system.*` keys in `~/.config/kanibako.yaml` (this is now a **config**
     file). Apply the renames in §3.
   - Move all behavior keys (`box.*`, agent settings, scoped shares/seeds, env) into
     a **settings** file at the appropriate scope. Apply the renames in §2 and the
     category mapping below.
2. The old `[system.path]` table and the behavior tables can no longer co-exist in
   one file. Anything that is not `system.*` must leave `~/.config/kanibako.yaml`.

### 1.5 Category keys — the unified path-delivery primitive

The old scoped shares/seeds (`<scope>.path.share_ro/share_rw/seeded.<name>`),
`shared.<name>` caches, and `env.<VAR>` collapse into one category set, available at
every scope (`system` / `agent.<agent>` / `workset` / `box`):

| Old | New category |
|---|---|
| `<scope>.path.share_ro.<name>` | `<scope>.bindings.ro.<name>` (`host_src: box_dest`) |
| `<scope>.path.share_rw.<name>` | `<scope>.bindings.rw.<name>` |
| `<scope>.path.seeded.<name>` | `<scope>.seeded.<name>` (one-time copy at init) |
| `shared.<name>` (cache dirs) | `<scope>.caches.<name>` (global at system, per-agent at agent scope) |
| `env.<VAR>` (`.env` files) | `<scope>.env.<VAR>` — **see ⚑ below** |
| `resource.<path>` | **DROPPED** (subsumed by the box/workset category keys) |

Additional categories: `<scope>.masks` (list of box paths to hide via tmpfs),
`<scope>.shared` (rw across all instances in scope), `<scope>.synced` (two-way
cred sync, applied by the agent plugin).

**Category precedence** (when two categories target the same `box_dest`, later wins):
`seed → cache → binding → shared → synced → masks`. `seed`/`synced` are file copies;
`cache`/`binding`/`shared`/`masks` are mounts. A `synced` and a `binding` naming the
same dest is a **config error** (a copy can't override a live mount).

⚑ **`.env` FILES ARE RETIRED.** Dropping a project `.env` file no longer works.
Move each var to `<scope>.env.<VAR>` in the scope's settings.yaml. Env precedence:
`system < agent < workset < box`, below CLI `-e`.

---

## 2. crab → agent rename

The tool, its config, its directories, and the config-facing commands are now
called **agent**. ("crab" survives only as the runtime concept and as internal
code symbols.) Apply every rename below to your on-disk files and your habits.

### 2.1 Keys

| Old key | New key |
|---|---|
| `box.crab` (alias `crab`) | `box.agent` (alias `agent`) |
| `crab.<agent>.<key>` | `agent.<agent>.<key>` |
| `crab.default.<key>` | `agent.default.<key>` |
| `crab.<agent>.binding.<key>` | `agent.<agent>.binding.<key>` |
| scope `crab` in `crab.path.share_*` / `crab.path.seeded.*` | scope `agent` → now `agent.<agent>.bindings.*` / `.seeded.*` (and see §1.5) |
| settings-cascade level `crab` | level `agent.<agent>` |
| `system.path.crabs` | `system.agents` (drop `.path` and re-spell; see §3) |

### 2.2 Directories

| Old | New |
|---|---|
| `@system.path.data/crabs/` (per-agent store) | `@system.data/agents/` (`<agent>/{plugins,cache,template,...}`) |

### 2.3 Environment / expansion variable

| Old | New |
|---|---|
| `$CRAB` (settings-reference var, = active agent name) | `$AGENT` |

### 2.4 CLI commands

The `crab` command is **CUT**. Its verbs split between `agent` (config) and `box`
(runtime):

| Old command | New command |
|---|---|
| `crab list` | `agent list` |
| `crab info` | `agent info` |
| `crab config` | `agent config` |
| `crab reauth` | `agent reauth` |
| `crab helper` | `box helper` |
| `crab fork` | `box fork` |
| `crab diagnose` | `box diagnose` |

Update any scripts, aliases, or muscle memory that invoked `crab ...`.

---

## 3. `system.*` structural reorg

`system.path.*` becomes `system.*` (the `.path` infix is dropped) and the tree is
restructured. The PRIMARY workset (§4) absorbs the old top-level box/log/vault dirs.

### 3.1 Key renames / new / deleted

| Old `system.path.*` | New `system.*` | Notes |
|---|---|---|
| `system.path.data` | `system.data` | rename only |
| `system.path.crabs` | `system.agents` | + crab→agent (§2) |
| `system.path.comms` | `system.channels` | renamed + rebuilt (see §7 stub) |
| `system.path.templates` | `system.base_template` | re-pointed to `@system.global/base_template` |
| `system.path.ws_hints` | `system.registry` | absorbed into the consolidated registry (§5) |
| `system.path.boxes` | **DELETED** | → `@system.primary_workset/boxes` (§4) |
| `system.path.share_ro` | **DELETED** | subsumed by `@workset.vault_ro` / category `shared` |
| `system.path.share_rw` | **DELETED** | subsumed by `@workset.vault_rw` / category `shared` |
| — | `system.backup` | NEW (`@system.data/backup`) |
| — | `system.global` | NEW (`@system.data/global`; holds `settings.yaml`, `registry.yaml`) |
| — | `system.settings` | NEW (`@system.global/settings.yaml`, the "system"-tier settings file) |
| — | `system.primary_workset` | NEW (`@system.data/primary_workset`; the PRIMARY workset root) |
| — | `system.cache` | NEW (`$XDG_CACHE_HOME/kanibako`; **not** under data) |
| — | `system.runtime` | NEW (`$XDG_RUNTIME_DIR/kanibako`; helper sockets; **not** under data) |
| — | `system.channels.{commons,chat,broadcast,mailboxes,share}` | NEW skeleton (filled in §7 stub) |

Also **deleted from the top level** (now under the PRIMARY workset): `system.boxes`,
`system.logs`, `system.vault_ro`, `system.vault_rw`.

### 3.2 `system.default_agent` (renamed setting)

The old default-agent selector `system.agent` is renamed to **`system.default_agent`**
(to avoid the one-character clash with the `system.agents` store directory). It is a
**setting** (behavior), not config — it lives in the settings file set despite its
`system.*` name. `box.agent` falls back to it.

### 3.3 XDG resolution

Every `$XDG_*` default resolves per the freedesktop Base Directory spec: honor the
var iff set **and absolute** (a relative value is ignored → spec default), else:
`XDG_DATA_HOME→~/.local/share`, `XDG_CONFIG_HOME→~/.config`,
`XDG_STATE_HOME→~/.local/state`, `XDG_CACHE_HOME→~/.cache`.
⚑ `XDG_RUNTIME_DIR` has **no** spec default; when unset, kanibako falls back to a
suitable dir and **warns** — it is not silently substituted.

### 3.4 New `@system.data` tree

```
$XDG_DATA_HOME/kanibako/
├── global/            ├─ base_template/   ├─ settings.yaml   └─ registry.yaml
├── agents/            └─ <agent>/{plugins, cache, template/}
├── primary_workset/   ├─ settings.yaml  ├─ boxes/<box>/{home,settings.yaml}
│                       ├─ vault/{ro,rw}/<box>/   └─ logs/<box>.jsonl
├── channels/          ├─ commons   ├─ chat/   ├─ mailboxes/<ws>/<box>   └─ share/<ws>/<box>
└── backup/
# siblings OUTSIDE data:  $XDG_CACHE_HOME/kanibako   ·   $XDG_RUNTIME_DIR/kanibako
```

---

## 4. Worksets & modes

The old two-axis model (`ProjectMode` × `ProjectLayout`) becomes a single
three-mode model and **layouts are removed**.

### 4.1 Mode rename

| Old `ProjectMode` / `project.yaml mode` | New `box.mode` | `workset.meta.name` |
|---|---|---|
| `default` (synthesized `__default__` workset) | `primary` | `__PRIMARY__` |
| `workset` (a named workset) | `named` | `<your workset name>` |
| `standalone` | `standalone` | `__STANDALONE__` |

⚑ The old `project.yaml mode="default"` is **not back-read** — there is a hard break.
A pre-existing box keyed to the old vocabulary will not be recognized until you
convert it.

### 4.2 Layouts removed

`ProjectLayout{simple,default,robust}` is **gone**. Remove every `layout:` field
from your on-disk metadata. There is now one fixed per-mode path policy:

- The PRIMARY-mode "vault inside the workspace" arrangements (simple/default) and the
  robust **human-vault symlinks** are removed. Vault is **always** at
  `@workset.vault_{ro,rw}`.

### 4.3 PRIMARY workset is now a real directory

The PRIMARY (formerly "default") workset is no longer virtual. Its boxes, vault, and
logs move out of the scattered data-root locations into one tree under
`@system.primary_workset`.

**Before** (scattered under the data root):

```
$XDG_DATA_HOME/kanibako/
├── boxes/<box>/{shell, vault, ...}     # box home + maybe in-tree vault
├── comms/
└── share_ro/  share_rw/
```

**After**:

```
$XDG_DATA_HOME/kanibako/primary_workset/   ← @system.primary_workset (= @workset.meta.root)
├── settings.yaml
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── vault/{ro,rw}/<box>/                    → ~/vault/{ro,rw}
└── logs/<box>.jsonl
# the box WORKSPACE stays external: box.meta.workspace = your real project dir → ~/workspace
```

Move each primary box's home dir to `primary_workset/boxes/<box>/home/`, its vault to
`primary_workset/vault/{ro,rw}/<box>/`, and its log to `primary_workset/logs/<box>.jsonl`.

### 4.4 NAMED workset layout

```
~/code/<wsname>/               ← workset.meta.root
├── settings.yaml              ← workset.meta.settings
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── workspaces/<box>/          → ~/workspace
├── vault/{ro,rw}/<box>/       → ~/vault/{ro,rw}
└── logs/<box>.jsonl
```

**NAMED workset name uniqueness:** a workset name is now a user-typed shared address
and must be unique. On a collision at create/import time, kanibako **refuses** (it
does not auto-suffix). The names `__PRIMARY__` and `__STANDALONE__` (and legacy
`default`) are **reserved** and cannot be used.

### 4.5 STANDALONE layout & identity

Standalone metadata moves from the in-tree `.kanibako`/`kanibako` dotdir into a
`box_data/` directory under the project root.

**Before**:

```
~/scratch/myproj/
├── .kanibako/   (or kanibako/)   ← metadata dotdir
└── ...
```

**After**:

```
~/scratch/myproj/             ← @workset.meta.root  (workset.meta.name: __STANDALONE__)
├── project.yaml              ← box metadata  (see ⚑ note below)
├── workspace/                → ~/workspace
├── box_data/                 ├─ home/ → ~/   └─ <box.name>.jsonl   (helper log)
└── vault/{ro,rw}/            → ~/vault/{ro,rw}
```

**Standalone box identity** is now `<random24>_<leaf>` — a 24-bit random token plus a
sanitized, length-capped leaf of the project dir name (e.g. `a1b2c3_myproj`). The
random token is regenerated on a whole-name collision. Standalone boxes are now
**registered** in `registry.yaml` (a `standalone` section), where they previously
were not.

⚑ **Metadata file name (current state):** the standalone metadata file is still
named **`project.yaml`** on disk (with `mode: standalone` inside), not `settings.yaml`.
The `project.yaml`→`settings.yaml` / `box.mode` rename is a later mechanical pass.
When hand-editing a standalone tree today, edit `project.yaml` and place it under the
project root as shown above. (Drop the `layout:` field; the mode token stays
`standalone`.)

### 4.6 `project.yaml` → per-box settings + workset meta

For **primary** and **named** modes, the old per-project `project.yaml`
(mode/layout/workspace/shell/vault_ro/vault_rw/group_auth/metadata/...) is replaced by:

- a per-box `settings.yaml` carrying `box.meta.*` (name, workspace, settings path), and
- the workset's `settings.yaml` carrying `workset.meta.*`.

Drop `layout` entirely; translate `mode` per §4.1; the path fields are now derived
from the fixed per-mode tables, not stored. (For **standalone**, see the ⚑ note in
§4.5 — the file is still `project.yaml` for now.)

### 4.7 Box-side vault path moved: `~/share-ro` / `~/share-rw` → `~/vault/ro` / `~/vault/rw`

⚑ **User-visible box-layout break.** Inside the box, the vault is now mounted at
`~/vault/ro` (read-only) and `~/vault/rw` (read-write) — the keyspace §2c box dests.
Previously it was mounted at the legacy `~/share-ro` / `~/share-rw`. The host-side
vault SOURCE (`@workset.vault_{ro,rw}`) and the local vault MASK
(`~/workspace/vault`, a tmpfs) are unchanged.

Any in-box scripts, aliases, or agent instructions that reference `~/share-ro` /
`~/share-rw` must be updated to `~/vault/ro` / `~/vault/rw`. (Host-side snapshot
tooling — `kanibako vault snapshot/restore` — is unaffected; it operates on the
host vault rw directory, not the box dest.)

---

## 5. Registry consolidation

Three separate stores merge into one `registry.yaml` at `@system.registry`
(`@system.global/registry.yaml`).

| Old file | New `registry.yaml` section |
|---|---|
| `{data}/names.yaml` `[projects]` | `projects:` |
| `{data}/names.yaml` `[worksets]` | `worksets:` |
| `{data}/worksets.yaml` (== `ws_hints`) | `worksets:` / `workset_roots:` (name→root) |
| `{data}/connected.yaml` | `connected:` |
| (standalone boxes — previously unregistered) | `standalone:` (NEW) |

Steps:

1. Create `@system.global/` if it does not exist.
2. Merge the contents of `names.yaml`, `worksets.yaml`, and `connected.yaml` into the
   appropriate sections of `@system.global/registry.yaml`.
3. Remove the old `names.yaml` / `worksets.yaml` / `connected.yaml`.

The registry is now a **derived, rebuildable index** — losing it no longer orphans
boxes (see §6). On purge, names are now unregistered (no dangling entries), and a
same-name convert reuses the existing name instead of auto-suffixing.

---

## 6. Drop-in detection & import (NEW behavior)

On-disk metadata is now **authoritative**; the registry is just a rebuildable index.
All three modes are **self-describing on disk and drop-in importable.**

What this means for you:

- **Detection is an ancestor-walk**, not a registry lookup. Standalone is detected by
  walking up for a `box_data/` marker + box metadata; named by a workset-root
  `settings.yaml`; primary by reconciling the central boxes dir against the registry.
- **You can move or copy a box/workset/project tree** to a new location or machine and
  kanibako re-discovers it.
- **Import is automatic with an alert, no confirmation.** When kanibako finds an
  on-disk entity that is not in the registry, it registers it, tells you it was
  imported, and proceeds.
- **Name collision = refuse.** If an import's name collides with an
  already-registered project/workset, kanibako refuses, leaves the tree untouched,
  and prints a clear error. (A future `rename` mechanism — not in 1.6.0 — will
  resolve collisions.)

Practical upshot for migration: after you have hand-moved trees into the new layout
(§3–§5), you do **not** strictly need to hand-edit the registry for every box — a
correctly laid-out tree will be detected and imported on access. The registry merge in
§5 is still recommended so existing names/roots are preserved and collisions are
avoided.

---

## 7. Channels (STUB — Phase 6, not yet implemented)

> **TODO: fill when Phase 6 lands.**
>
> The comm system (`comms` → `channels`) is being rebuilt. Expected user-visible
> changes to document here: `system.path.comms` → `system.channels.*` paths;
> in-box `~/comms/` → `~/channels/`; broadcast log `broadcast.log` → `broadcast.md`;
> mailbox/share partitioning by workset name; `INSTRUCTIONS.md` references updated
> from `~/comms/...` to `~/channels/...`. Do not hand-migrate channels until this
> section is filled.

---

## 8. Templates / host-config removal (STUB — Phase 7, not yet implemented)

> **TODO: fill when Phase 7 lands.**
>
> Expected changes to document here: per-scope layered template seed
> (base → agent → workset, copied once at box creation); removal of host-config
> import (claude `.claude.json` onboarding, codex `config.toml`, goose `config.yaml`
> are no longer host-copied — behavior flows from settings + the curated template);
> the shell-variant template selector is removed; old `templates/<agent>/standard`
> content becomes `@agent.<agent>.template`.

---

## 9. Agent descriptors (STUB — Phase 8, not yet implemented)

> **TODO: fill when Phase 8 lands.**
>
> Document here any user-visible `agent.<agent>.*` key changes that finalize with the
> plugin descriptors (per-agent bindings/creds/caps/env), once Phase 8 is implemented.
