# Migrating to kanibako 1.6.0

> This is a **manual** runbook: 1.6.0 ships **no migration code**. You edit your
> on-disk state from the old (≤ pre-revamp) layout to the new layout *before*
> installing 1.6.0. The sections below describe each old→new change as a set of
> numbered steps with before→after tables and tree diagrams.

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
| Per-box meta file | `project.yaml` (mode/layout/paths/...) | per-box `settings.yaml` (`[project]` + `[resolved]` sections); all modes (§9) |
| Registry | `names.yaml` + `worksets.yaml` + `connected.yaml` | one `registry.yaml` (`@system.registry`) |
| Detection | registry-driven | **on-disk authoritative**, walk-detected, drop-in importable |
| Comm system | single `~/comms/` mount (`mailbox/<box>`, `broadcast.log`) | **channels** — 5 types under `~/channels/` (`mailboxes/<ws>/<box>`, `chat/broadcast.md`) (§7) |
| Templates | shell-variant tree + CLAUDE.md merge + host-config import | **layered seed-once** (base→agent→workset); host-config import **removed** (§8) |
| Per-agent YAML section | `crab:` | `agent:` (§9) |
| Box-side vault dest | `~/share-ro` / `~/share-rw` | `~/vault/ro` / `~/vault/rw` (§4.7, §9) |
| Agent selection | arbitrary auto-pick among installed agents | cascade + installed-count rule; **2+ agents with no choice = error** (§10) |
| Choosing a default agent | `kanibako system config system.default_agent …` | `kanibako setup` / edit the file — `system.*` is file-only (§10) |
| Targeting a non-cwd box | `refresh -p/--project` | `--box <name-or-path>` (universal); `-p/--project` removed (§10) |

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
| `[shared].<name>` (cache dirs) | `<scope>.caches.<name>` — **see ⚑⚑ below** |
| `env.<VAR>` (`.env` files) | `<scope>.env.<VAR>` — **see ⚑ below** |
| `resource.<path>` | **DROPPED** (subsumed by the box/workset category keys) |

Additional categories: `<scope>.masks` (list of box paths to hide via tmpfs),
`<scope>.shared` (rw across all instances in scope), `<scope>.synced` (two-way
cred sync, applied by the agent plugin).

⚑⚑ **THE `[shared]` CACHE TABLE IS RETIRED → the `caches` category.** The old
`[shared]` table carried an *implicit* scope (it meant different things depending
on which file it appeared in). The replacement makes scope **explicit in the key**:

| Old `[shared]` location | Meant | New key |
|---|---|---|
| `[shared]` in the config cascade (`kanibako.yaml` / workset `config.yaml` / `settings.yaml`) | GLOBAL cache, mounted from `shared/global/<name>` | `system.caches.<name>` |
| `[shared]` in an agent file (`agents/<agent>/settings.yaml`, formerly `agents/<agent>.yaml` / `crabs/<agent>.yaml`) | per-agent cache, mounted from `shared/<agent>/<name>` | `agent.<agent>.caches.<name>` |

Two behavioral changes you must account for when hand-migrating:

- **The host source is now SPELLED OUT, not auto-rooted.** The old `[shared]`
  value was only the *box-side* path; the host dir was implicitly
  `<shared-store>/global/<name>` (or `<shared-store>/<agent>/<name>`). A `caches`
  entry names BOTH sides explicitly (`host_src: box_dest`) — there is no longer a
  shared-store dir under which sources are auto-located. Point each cache at the
  real host directory you want mounted.
- **Lazy → guarantee-create.** The legacy `[shared]` block mounted a cache *only
  if the host dir already existed* (a missing dir was silently skipped). The
  `caches` category emitter (rw) **creates a missing source** before binding it.
  If you relied on the lazy "skip when absent" behavior, drop the key instead.

There is **no migration code** — move your `[shared]` entries to `caches` keys by
hand (and migrate any existing on-disk cache dirs yourself if you want to keep
their contents).

⚑ **Claude `plugins` + `cache` MOVED on disk (no migration code).** They were
served from the retired global shared store at `<data>/shared/<agent>/plugins`;
they are now AGENT-scope `agent.claude.shared.{plugins,cache}` entries rooted at
the per-agent store dir — host `<data>/agents/claude/{plugins,cache}`, bound rw
to `~/.claude/{plugins,cache}` in the box. The top-level `<data>/shared/` dir no
longer exists. If you want to keep installed plugins, move
`<data>/shared/<agent>/plugins` → `<data>/agents/claude/plugins` by hand (else a
fresh empty dir is created on next launch).

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

### 2.5 Per-agent settings file moved INTO the store dir

The per-agent settings file (`name`, `run_args`, `model`, `env`, … — the
`agent.<agent>` cascade tier) now lives **inside** the per-agent store directory
as `settings.yaml`, instead of as a sibling file next to it. This makes
`agents/<agent>/` a uniform store dir (alongside `template/`, and the
`plugins/`/`cache/` stores) rather than a `<agent>.yaml` file sitting next to an
`<agent>/` directory.

| Old | New |
|---|---|
| `@system.data/agents/<agent>.yaml` (sibling file) | `@system.data/agents/<agent>/settings.yaml` (inside the store dir) |

This is the `agent.<agent>.meta.settings` key (= `@agent.<agent>.meta.path/settings.yaml`,
where `@agent.<agent>.meta.path` = `@system.data/agents/<agent>`).

**Manual move (no automatic migration):** for each per-agent file you already
have, move it into the matching store dir, e.g.

```sh
cd "${XDG_DATA_HOME:-$HOME/.local/share}/kanibako/agents"
for f in *.yaml; do
  [ -e "$f" ] || continue
  name="${f%.yaml}"
  mkdir -p "$name"
  mv "$f" "$name/settings.yaml"
done
```

(The default `general.yaml` likewise becomes `general/settings.yaml`.) If you
skip the move, kanibako simply regenerates a fresh default settings file in the
new location and your old `<agent>.yaml` overrides are ignored until moved.

> `agent.<agent>.meta.name` (the plugin's identifier, e.g. `claude`) is now
> REQUIRED — every agent plugin must declare a non-empty name; one that does not
> is rejected at launch rather than silently misbehaving.

---

## 3. `system.*` structural reorg

`system.path.*` becomes `system.*` (the `.path` infix is dropped) and the tree is
restructured. The PRIMARY workset (§4) absorbs the old top-level box/log/vault dirs.

### 3.1 Key renames / new / deleted

| Old `system.path.*` | New `system.*` | Notes |
|---|---|---|
| `system.path.data` | `system.data` | rename only |
| `system.path.crabs` | `system.agents` | + crab→agent (§2) |
| `system.path.comms` | `system.channels` | renamed + rebuilt (see §7) |
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
| — | `system.channels.{commons,chat,broadcast,mailboxes,share}` | NEW sub-keys (detailed in §7) |

Also **deleted from the top level** (now under the PRIMARY workset): `system.boxes`,
`system.logs`, `system.vault_ro`, `system.vault_rw`.

### 3.2 `system.default_agent` (renamed setting)

The old default-agent selector `system.agent` is renamed to **`system.default_agent`**
(to avoid the one-character clash with the `system.agents` store directory). It is a
**setting** (behavior), not config — it lives in the settings file set despite its
`system.*` name. `box.agent` falls back to it.

The system tier of these behavior settings now lives in **`global/settings.yaml`**
(`@system.settings`), separate from the `~/.config/kanibako.yaml` CONFIG file (which
holds only `system.*` layout/path keys). The `kanibako system config` command READS /
SHOWS `system.*` keys (e.g. `system.default_agent`, `system.data`) but — as of the W1
overhaul (see §10) — **refuses to SET them**: all `system.*`-prefixed keys are
file-only. Non-`system.` settings (e.g. `model`) stay CLI-settable at the global tier
and are written to `global/settings.yaml`. **No automatic migration:** if you
previously set `system.default_agent` in `kanibako.yaml`, choose it with
**`kanibako setup`** (which writes it for you) or move the `[agent.default]` table
into `global/settings.yaml` by hand — a stale `[agent]` table in `kanibako.yaml` is
no longer read by the system settings tier. See **§10** for the full file-only rule
and the new agent-resolution behavior.

### 3.2a Box-level `[paths]` keys removed

The old box-level `[paths]` config table is gone. None of its keys are settable any
more (`config set paths.* ` is rejected as unknown):

| Old key | Status | Notes |
|---|---|---|
| `paths.shell` | **DELETED** (dead) | nothing read it |
| `paths.vault` | **DELETED** | superseded by the vault bindings (`box.bindings.{ro,rw}.vault`) |
| `paths.shared` | **DELETED** | was the shared-store dir-name leaf. The top-level `<data>/shared/` store is gone entirely (§1.5): per-agent plugins/caches now live under the per-agent store `agents/<agent>/{plugins,cache}` (`agent.<agent>.shared.*`). There is no shared-store dir to rename. |

Remove any `[paths]` table from your config/settings files.

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

**Per-box state-dir leaf renamed `shell` → `home`.** The on-disk per-box state
directory is now `boxes/<box>/home/` (was `boxes/<box>/shell/`) for PRIMARY and NAMED
boxes, matching STANDALONE (which already used `home`). No auto-migration: manually
`mv boxes/<box>/shell boxes/<box>/home` for each existing box. (The persisted
`box.shell` *setting* — the login shell — and the stored-path `shell` metadata key are
unchanged; only the directory leaf moved.)

### 4.4 NAMED workset layout

```
~/code/<wsname>/               ← workset.meta.root
├── settings.yaml              ← workset.meta.settings
├── boxes/<box>/{home/ → ~/ , settings.yaml}
├── workspaces/<box>/          → ~/workspace
├── vault/{ro,rw}/<box>/       → ~/vault/{ro,rw}
└── logs/<box>.jsonl
```

**NAMED workset vault path order changed `vault/<box>/{ro,rw}` → `vault/{ro,rw}/<box>`.**
The ro/rw split now nests ABOVE the box name, matching PRIMARY and STANDALONE. No
auto-migration: for each named-workset box, move `vault/<box>/ro` → `vault/ro/<box>` and
`vault/<box>/rw` → `vault/rw/<box>`.

**NAMED workset name uniqueness:** a workset name is now a user-typed shared address
and must be unique. On a collision at create/import time, kanibako **refuses** (it
does not auto-suffix). The names `__PRIMARY__` and `__STANDALONE__` (and legacy
`default`) are **reserved** and cannot be used.

**Workset files consolidated `workset.yaml` + `config.yaml` → one `settings.yaml`.**
A NAMED workset previously kept its identity/marker/project-list in a
`<root>/workset.yaml` and its cascade settings (image, `agent.*`, `workset.bindings.*`,
`standalone`, `enable_vault`) in a separate `<root>/config.yaml`. Both now live in a
**single `<root>/settings.yaml`** = `@workset.meta.settings`, mirroring a box's
`settings.yaml` which carries `box.meta.*` alongside its settings:

| Old file → key | New location |
| --- | --- |
| `<root>/workset.yaml` (`name`, `created`, `group_auth`, `projects`) | `<root>/settings.yaml` under `workset.meta.*` |
| `<root>/config.yaml` (`box.*`, `agent.*`, `workset.bindings.*`, `standalone`, `enable_vault`) | `<root>/settings.yaml` (top level — unchanged key shapes) |

The identity lives under the `workset.meta` table so it never collides with the
cascade-settings tables in the same file; the settings readers ignore `workset.meta`
and the identity reader ignores everything else. **Detection** of a drop-in/unregistered
NAMED workset root now keys on `<root>/settings.yaml` carrying a `workset.meta` identity
(was: presence of `workset.yaml`). No auto-migration: for each existing NAMED workset,
fold `workset.yaml`'s keys under a `workset:`→`meta:` table in `settings.yaml` and merge
the old `config.yaml` keys in at the top level, then remove both old files. (The
default/synthesized workset is unaffected: its `group_auth` still lives in the
data-root `config.yaml` `[project]` section, which is the system/default config file,
not a NAMED-workset file.)

### 4.5 STANDALONE layout & identity

Standalone metadata moves from the in-tree `.kanibako`/`kanibako` dotdir to the
project root: the box `settings.yaml` lives **at the root**, the live workspace is
a **`workspace/` subdir** (NOT the root itself), and a `box_data/` marker dir
holds the agent home + the helper log.

**Before**:

```
~/scratch/myproj/
├── .kanibako/   (or kanibako/)   ← metadata dotdir
└── ...                           ← project files mounted directly as ~/workspace
```

**After**:

```
~/scratch/myproj/             ← @workset.meta.root  (workset.meta.name: __STANDALONE__)
├── settings.yaml             ← box.meta.settings   (box metadata; AT THE ROOT)
├── workspace/                → ~/workspace          (a SUBDIR, not the root)
├── box_data/                 ├─ home/ → ~/          └─ <box.name>.jsonl   (helper log)
└── vault/{ro,rw}/            → ~/vault/{ro,rw}
```

⚑ **Two changes from earlier 1.6.0 dev builds (drift H + I):**

- **`settings.yaml` moved from `box_data/settings.yaml` to `<root>/settings.yaml`.**
  The `box_data/` directory is now ONLY the marker dir + home + helper log; the box
  metadata file is at the project root, alongside `workspace/` and `vault/`.
- **The workspace is now a `<root>/workspace/` subdir, not the project root.** The
  root holds the kanibako artifacts (`settings.yaml`, `box_data/`, `vault/`); your
  actual project files live under `workspace/` (mounted as `~/workspace`).

⚑ **The standalone walk marker is now a `box_data/` directory PLUS a
`<root>/settings.yaml`** declaring `mode: standalone`. (A NAMED workset root also
carries `<root>/settings.yaml`, but with a `workset.meta` identity and NO
`box_data/` dir, so the two never collide.) The old in-tree `.kanibako`/`kanibako`
dotdir marker is gone. When hand-editing a standalone tree, place `settings.yaml`
at the root, keep a `box_data/` dir beside it, and put your files under
`workspace/`. Drop any `layout:` field; the mode token stays `standalone`.

⚑ **No automatic migration (pre-public):** there is no on-disk migrator. To move a
pre-existing standalone tree to the new shape by hand: move `box_data/settings.yaml`
up to `<root>/settings.yaml`, create `<root>/workspace/` and move your project files
into it (leaving `settings.yaml`, `box_data/`, and `vault/` at the root), and keep
the `<box>.jsonl` helper log inside `box_data/`.

**Standalone box identity** is now `<random24>_<leaf>` — a 24-bit random token plus a
sanitized, length-capped leaf of the project dir name (e.g. `a1b2c3_myproj`). The
random token is regenerated on a whole-name collision. Standalone boxes are now
**registered** in `registry.yaml` (a `standalone` section), where they previously
were not.

### 4.6 `project.yaml` → per-box `settings.yaml`

The old per-project `project.yaml` (mode/layout/workspace/shell/vault_ro/vault_rw/
group_auth/metadata/...) is replaced by a per-box **`settings.yaml`** in **every**
mode. Its on-disk shape (the `[project]` + `[resolved]` sections it actually carries)
is detailed in §9 — read that section before hand-editing it.

Drop `layout` entirely; translate `mode` per §4.1; the path fields are derived from
the fixed per-mode tables, not user-edited. (Where the file lives: primary →
`@system.primary_workset/boxes/<box>/settings.yaml`; named →
`<wsroot>/boxes/<box>/settings.yaml`; standalone → `<root>/settings.yaml` (at the
project root, NOT inside `box_data/` — see §4.5).)

### 4.7 Box-side vault path moved: `~/share-ro` / `~/share-rw` → `~/vault/ro` / `~/vault/rw`

⚑ **User-visible box-layout break.** Inside the box, the vault is now mounted at
`~/vault/ro` (read-only) and `~/vault/rw` (read-write) — the keyspace §2c box dests.
Previously it was mounted at the legacy `~/share-ro` / `~/share-rw`. The host-side
vault SOURCE (`@workset.vault_{ro,rw}`) is unchanged.

⚑ The old in-workspace vault MASK (`~/workspace/vault`, an unconditional read-only
tmpfs) is **REMOVED** in 1.6.0. It existed only to hide the vault back when the vault
lived inside the workspace; now that the vault lives outside the workspace there is
nothing in `~/workspace` to mask, so no mask is applied by default. Boxes can still
declare explicit tmpfs masks via the `box.masks` (or `<scope>.masks`) category.

Any in-box scripts, aliases, or agent instructions that reference `~/share-ro` /
`~/share-rw` must be updated to `~/vault/ro` / `~/vault/rw`. (Host-side snapshot
tooling — `kanibako vault snapshot/restore` — is unaffected; it operates on the
host vault rw directory, not the box dest.)

### 4.8 Helper message log relocated: shared `data/logs/<id>/` → per-box, per-mode

The per-box helper message log (the host source of the read-only
`$XDG_STATE_HOME/kanibako/helpers.jsonl` bind inside the box) previously landed in a
SINGLE shared host location for every mode:
`@system.data/logs/<box-or-hash>/helper-messages.jsonl`. It now lives inside each
box's own workset/box tree, one file per box:

| Mode | New host helper-log path |
|---|---|
| PRIMARY | `@system.primary_workset/logs/<box>.jsonl` |
| NAMED | `@workset.meta.root/logs/<box>.jsonl` |
| STANDALONE | `@workset.meta.root/box_data/<box>.jsonl` |

The box-side dest is unchanged (`$XDG_STATE_HOME/kanibako/helpers.jsonl`, read-only),
and the in-box `kanibako box helper log` command is unaffected. NAMED worksets now get
a `logs/` dir created at workset-creation time (alongside `boxes/`, `workspaces/`,
`vault/`). The shared top-level `data/logs/` tree (and the `.../helper-messages.jsonl`
filename) is gone. No auto-migration: the log is regenerated on the next launch; to
preserve history, move `data/logs/<id>/helper-messages.jsonl` to the per-mode path
above (renaming it `<box>.jsonl`). The standalone log now stays inside `box_data/`,
so a moved/imported standalone tree carries its helper log with it.

---

## 5. Registry consolidation

The separate name/registry stores merge into one `registry.yaml` at
`@system.registry` (`@system.global/registry.yaml`).

| Old file | New `registry.yaml` section |
|---|---|
| `{data}/names.yaml` `[projects]` | `projects:` |
| `{data}/names.yaml` `[worksets]` | `worksets:` |
| `{data}/worksets.yaml` (== `ws_hints`) | `worksets:` / `workset_roots:` (name→root) |
| `{data}/connected.yaml` | `connected:` |
| (standalone boxes — previously unregistered) | `standalone:` (NEW) |
| `{data}/rigs.yaml` | `rigs:` |
| `{data}/image-shells.yaml` | `image_shells:` |

Steps:

1. Create `@system.global/` if it does not exist.
2. Merge the contents of `names.yaml`, `worksets.yaml`, `connected.yaml`,
   `rigs.yaml`, and `image-shells.yaml` into the appropriate sections of
   `@system.global/registry.yaml`.
3. Remove the old `names.yaml` / `worksets.yaml` / `connected.yaml` /
   `rigs.yaml` / `image-shells.yaml`.

The registry is now a **derived, rebuildable index** — losing it no longer orphans
boxes (see §6). On purge, names are now unregistered (no dangling entries), and a
same-name convert reuses the existing name instead of auto-suffixing.

---

## 6. Drop-in detection & import (NEW behavior)

On-disk metadata is now **authoritative**; the registry is just a rebuildable index.
All three modes are **self-describing on disk and drop-in importable.**

What this means for you:

- **Detection is an ancestor-walk**, not a registry lookup. Standalone is detected by
  walking up for a `box_data/` dir + a root `settings.yaml` with `mode: standalone`;
  named by a workset-root `settings.yaml` carrying `workset.meta`; primary by
  reconciling the central boxes dir against the registry.
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

## 7. Channels (the comm-system rebuild)

The single legacy `comms` mount is replaced by the **channels** system: 5 channel
types across 2 scopes (system + workset), surfaced in-box under `~/channels/` and
`~/channels/workset/`, with per-instance partitioning keyed by the workset name.

### 7.1 Key + path renames

| Old `system.path.comms` | New `system.channels.*` |
|---|---|
| `system.path.comms` (one dir) | `system.channels` (`@system.data/channels`) + sub-keys below |
| — | `system.channels.commons` (`@system.channels/commons`) |
| — | `system.channels.chat` (`@system.channels/chat`; dir of `*.md` logs) |
| — | `system.channels.broadcast` (`@system.channels.chat/broadcast.md`) |
| — | `system.channels.mailboxes` (`@system.channels/mailboxes`; partitioned `/<ws>/<box>`) |
| — | `system.channels.share` (`@system.channels/share`; partitioned `/<ws>/<box>`) |

(The `system.path.comms` → `system.channels` rename is also listed in §3.1; this
section details the sub-keys and the in-box layout.)

### 7.2 The 5 channel types

| Type | Owner | Other-box perms\* | Where (host) |
|---|---|---|---|
| **Mailbox** | a box | write-only\* | system `mailboxes/<ws>/<box>` |
| **Share** | a box | read-only\* | system `share/<ws>/<box>` + workset `channels/share/<box>` |
| **Commons** | a scope | read-write | `commons/` (system + workset) |
| **Chat** | a scope | read-append\* | `chat/*.md` (system + workset); default `general.md` |
| **Broadcast** | a scope | read-append\* | `chat/broadcast.md` (system + workset) |

\* Permissions are **by convention, not enforced** in 1.6.0 — every channel is
read-write-mounted. Any box can technically read or overwrite any other box's
mailbox/share/commons/chat. This is the deliberate single-operator box↔box trust
stance; box↔HOST isolation is unaffected. (Future helper-mediated enforcement will
tighten the write paths without moving the in-box paths.)

### 7.3 In-box layout: `~/comms/` → `~/channels/`

**Before** (single mount):

```
~/comms/
├── mailbox/<box>/      ← flat, keyed by box name
└── broadcast.log       ← top-level, .log
```

**After** (the channels tree):

```
~/channels/                      ~/channels/workset/   (primary/named only; standalone OMITS)
├── commons/                     ├── commons/
├── chat/                        ├── chat/
│   ├── general.md               │   ├── general.md
│   └── broadcast.md             │   └── broadcast.md
├── share/                       └── share/
├── mailboxes/<ws>/<box>/
└── inbox/                       ← own mailbox alias (== mailboxes/<ws>/<self>)
```

⚑ Three structural breaks to migrate by hand:

1. **`~/comms/mailbox/<box>/` → `~/channels/mailboxes/<ws>/<box>/`.** Mailboxes are
   now plural (`mailboxes/`) and **partitioned by workset name** first. `<ws>` is
   the workset-name token: `__PRIMARY__` (primary mode), the workset name (named
   mode), or `__STANDALONE__` (standalone). Move each old `mailbox/<box>` dir to
   `mailboxes/<ws>/<box>` under the host channels root. (Workset-name uniqueness —
   §4.4 — is what makes the `<ws>` partition unambiguous.)
2. **`broadcast.log` → `chat/broadcast.md`.** Both a **location** change (now inside
   the `chat/` dir) and a **format** change (`.log` → `.md`). Move the old
   `broadcast.log` content into `chat/broadcast.md`.
3. **Own inbox.** A box's own mailbox is additionally surfaced at `~/channels/inbox`
   (the same host dir as `~/channels/mailboxes/<ws>/<self>`). No migration needed —
   it is created on box launch.

The host roots live under `@system.channels` (system scope) and
`<wsroot>/channels` (workset scope, primary/named only). The Share type also has a
**system** publication dir (`share/<ws>/<box>`) and, for primary/named boxes, a
**workset-local** one (`channels/share/<box>`).

### 7.4 Box-side helper socket / log dest (XDG-aware)

The in-box helper socket and message-log destinations are now XDG-aware (they
honor `$XDG_STATE_HOME` if it is set and absolute, else fall back to
`~/.local/state`):

| Old (hardcoded) | New (XDG-aware) |
|---|---|
| `/home/agent/.local/state/kanibako/helper.sock` | `$XDG_STATE_HOME/kanibako/helper.sock` |
| `/home/agent/.local/state/kanibako/helper-messages.jsonl` | `$XDG_STATE_HOME/kanibako/helpers.jsonl` |

The in-box message-log filename changed `helper-messages.jsonl` → `helpers.jsonl`.
The **host-side** log filename (`<box>.jsonl`, under the workset/box logs dir) is
unchanged. In-box tooling/scripts that referenced the old literal socket/log paths
should use the XDG-derived path. No host-side hand-migration is required — these are
recreated per box launch.

The **host-side** helper socket basename is now `<box>-<ws>.sock` (was `<box>.sock`),
where `<ws>` is the workset-name token (`__PRIMARY__` / `<named>` / `__STANDALONE__`),
so a project name reused across worksets gets a distinct socket. It lives under
`@system.runtime` and is recreated per launch — no hand-migration needed.

### 7.5 Move / convert relocates the owning box's partition (best-effort)

A box's mailbox/share partition key is the workset name, so moving a box between
worksets (or converting between modes) changes its channel address. `box move` /
`box convert` now relocate the box's **own** mailbox + system-share partition to the
new address on a **best-effort** basis. Stale cross-box references to the box's old
address may break — there is **no forwarding marker**. Scope-owned channels
(commons/chat) are not relocated; the box simply stops mounting the old workset's
local channels and starts mounting the new one's.

---

## 8. Templates & host-config removal

Three overlapping ad-hoc seeding mechanisms (the shell-variant template, the
CLAUDE.md instruction merge, and the per-agent host-config import) collapse into
**one layered seed-once** model. This is the headline behavior break in this
section: **your host agent config no longer flows into boxes.**

### 8.1 Layered seed-once template

On box creation, three template layers are copied into the box home `~/` in order
(later overlays earlier; absent layers are skipped), **once** — never re-seeded, so
any edits you make inside a box afterward survive:

```
1. base    @system.base_template          → ~/    (always)
2. agent   @agent.<agent>.template         → ~/    (= @system.agents/<agent>/template; if box.agent set)
3. workset @workset.template               → ~/    (= <wsroot>/template; optional, primary/named only)
```

Per-file rule: plain ordered copy, **last layer wins**, seed-once. There is **no
per-file merge of any file** (see the CLAUDE.md change below).

### 8.2 Content moves (a rename, not a loss)

| Old on-disk content | New location |
|---|---|
| `templates/<agent>/standard/*` | `@agent.<agent>.template` (= `@system.agents/<agent>/template`) |
| `templates/general/{base,standard}/*` | `@system.base_template` (flat — no `general/`, no variant subdir) |

Hand-move your existing template content accordingly. The base template is now
**flat** (no `general/base` vs `general/standard` split).

### 8.3 Shell-variant selector dropped

The template-variant selector (`crab.shell` / `template_name`, default `"standard"`)
is **gone**. There is one fixed `@agent.<agent>.template` per agent — no variant
subdirectory. `box.shell` now means **only the login shell**. (If a need for
variants reappears it can return later as a creation-time `box create --template
<variant>` flag, not a cascade setting.) Remove any `shell:`/`template_name:`
variant key from your agent config.

### 8.4 CLAUDE.md is now a plain template file

The instruction-merge machinery (section-marker concatenation of base / template /
project layers of `CLAUDE.md`) is **deleted**. `CLAUDE.md` is now an ordinary
template file: plain ordered copy, last-wins, seed-once. Base-layer guidance lives
in a separate non-colliding file (`INSTRUCTIONS.md`) so it never clobbers the agent
template's `CLAUDE.md`. If you relied on the merge markers, fold your content
directly into the appropriate template-layer `CLAUDE.md`.

### 8.5 ⚑ Host-config IMPORT removed (the headline break)

kanibako no longer copies your **host** agent config into a fresh box. Each agent's
import is removed:

| Agent | Host source no longer imported | What replaces it |
|---|---|---|
| claude | `~/.claude.json` (the `oauthAccount` / `hasCompletedOnboarding` / `installMethod` allowlist) | a curated static `.claude.json` onboarding stub (`{"hasCompletedOnboarding": true}`) in the claude template + your synced `~/.claude/.credentials.json` |
| codex | `~/.codex/config.toml` | a curated `config.toml` in the codex template (or codex's built-in defaults) |
| goose | `~/.config/goose/config.yaml` (the provider/model/**extensions**/**instructions** allowlist) | provider/model via the `agent.goose.env.GOOSE_PROVIDER` / `GOOSE_MODEL` settings; extensions/instructions = the **curated goose template set** |

What you must now do instead:

- **claude:** authentication flows from the **synced** `~/.claude/.credentials.json`
  (still synced every launch) plus the template's onboarding stub. Your host
  `~/.claude.json` settings (including `oauthAccount`) no longer seed the box.
- **goose:** set provider/model as settings (`agent.goose.env.GOOSE_PROVIDER`,
  `agent.goose.env.GOOSE_MODEL`) rather than relying on your host `config.yaml`.
  ⚑ **Your host goose `extensions` and `instructions` are NOT carried into boxes** —
  this is an accepted loss; boxes use the template's curated set. Place any extension
  config you want in boxes into the goose template (`@agent.goose.template`).
- **codex:** codex runs on built-in defaults; place any custom config in the codex
  template (`@agent.codex.template`).
- **any agent:** to ship custom per-agent config into boxes, put it in that agent's
  template dir (`@system.agents/<agent>/template`) — it seeds via layer 2.

**Credential SYNC** (separate from host-config import): claude `.credentials.json`,
codex `auth.json`, and goose `secrets.yaml` are two-way synced on every launch.

> **Update (post-1.6.0):** goose additionally two-way syncs `~/.config/goose/config.yaml`
> and `~/.config/goose/custom_providers/`. This is *not* the removed host-config
> import (which seeded your host config into a fresh box at init). Instead, when you
> run `goose configure` **inside a box**, the provider/model selection and any custom
> provider definition now persist back to the host so the host-side auth check sees a
> configured goose on the next start (no repeated `goose configure` prompt) — parity
> with how claude/codex write their config back. The values these files reference are
> env-var *names*; the secret value still lives only in `secrets.yaml`.

---

## 9. Agent descriptors, the per-box meta file & box-side vault dest

The agent descriptor model is finalized in 1.6.0; most of it is internal (the
plugin contract). The user-visible pieces are three on-disk / box-layout changes.

### 9.1 Per-agent YAML section `crab:` → `agent:`

The top-level section token in a per-agent YAML file is renamed from `crab` to
`agent` (the last on-disk `crab` token in the config layer):

```yaml
# Before                         # After
crab:                            agent:
  model: opus                      model: opus
```

⚑ **Hard break, no back-read.** A file with a `crab:` section is not recognized
until you rename the section to `agent:`. (This is in addition to the cascade-level
and key renames in §2.)

### 9.2 Per-box meta file `project.yaml` → `settings.yaml`

The per-box metadata file is renamed `project.yaml` → **`settings.yaml`** in **every**
mode (primary, named, and standalone). See §4.6 for where each mode's file lives;
§4.5 for the standalone walk marker (`box_data/` dir + `<root>/settings.yaml`).

⚑ **What the file actually contains (on-disk format).** The per-box `settings.yaml`
stores construct-time box metadata in two YAML sections, `project:` and `resolved:`
— these are the *physical* on-disk shape you would see if you opened the file. (The
keyspace documents this metadata as the logical `box.meta.*` / `workset.meta.*`
model; the on-disk layout uses these two sections rather than nested `box.meta.*`
tables. The logical keyspace names are the model; the sections below are the disk
reality.)

```yaml
project:
  mode: primary            # primary | named | standalone  (was project.mode; NO layout field)
  enable_vault: true
  group_auth: true
  name: <box name>
resolved:
  workspace: <project dir>
  shell: <login shell>
  vault_ro: <host vault ro path>
  vault_rw: <host vault rw path>
  metadata: <metadata path>
  project_hash: <hash>
```

When migrating an old `project.yaml`: rename the file to `settings.yaml`, **drop any
`layout:` field**, translate `mode` per §4.1, and keep the `project:` / `resolved:`
section layout shown above. You normally do not hand-edit the `resolved:` section —
it is derived construct-time state, regenerated by kanibako. The `name` under
`project:` is what lets a moved/copied tree keep its identity on drop-in import (§6).

### 9.3 Box-side vault dest `~/share-ro` / `~/share-rw` → `~/vault/ro` / `~/vault/rw`

This change is detailed in **§4.7** — cross-reference it, not duplicated here. In
short: inside the box, the vault is now mounted at `~/vault/ro` / `~/vault/rw`
(previously `~/share-ro` / `~/share-rw`). Update any in-box scripts, aliases, or
agent instructions accordingly. The host-side vault source is unchanged; the old
in-workspace `~/workspace/vault` tmpfs mask is REMOVED (see §4.7).

### 9.4 Host agent-config import removed

The removal of host agent-config import (claude `.claude.json`/`oauthAccount`, codex
`config.toml`, goose `extensions`/`instructions`) is detailed in **§8.5** —
cross-reference it. It is the user-facing behavior change that the finalized
descriptors carry; the descriptor model change itself is internal.

---

## 10. Agent selection, blanket flags & file-only `system.*`

The 1.6.0 pre-public clean-house also overhauls how an agent is chosen and how a
command targets a box. These are **breaking** and require action on upgrade.

### 10.1 No agent is auto-picked when 2+ are installed (BREAKING)

Previously, `kanibako` (i.e. `start`) with no explicit or configured agent would
**arbitrarily launch one of the installed agents** — the first in plugin-discovery
order (the "goose-by-luck" footgun, since the `kanibako` meta-package installs all
three). That arbitrary pick is **removed**.

The new resolution cascade (highest precedence first) is
`--agent > box > workset > system default`. If nothing resolves, the **installed
count** decides — with **no ordering and no tie-break**:

| Installed agents | Behavior |
|---|---|
| exactly 1 | used implicitly (unambiguous — no change for single-agent users) |
| 0 | **error**: install a plugin (or use `kanibako shell`) |
| **2+** | **error**: pick one — run `kanibako setup` or pass `--agent <name>` |

**What you must do:** if you run the meta-package (2+ agents) and relied on the
implicit pick, choose a default once with **`kanibako setup`**, or pass
`--agent <name>` per invocation. This applies uniformly to every agent-requiring
command (`start`, `box start`, `agent reauth`, …) — not just launch.

`kanibako shell` is the **sole** no-agent path: it never resolves an agent and never
errors on agent selection (the emergency-recovery hatch into the container).

### 10.2 `setup` now selects a default agent

`kanibako setup` gained an interactive step (the **only** interactive prompt in the
CLI): on a TTY it lists detected agents in a numbered menu and writes your pick as
the host-global default. A "skip" option is offered; with 2+ agents installed skip is
**gated** — it warns that a naked launch will then fail and requires an explicit
`y`/`yes`, otherwise it re-prompts. With exactly one agent, skip is harmless.

- Non-interactive: `kanibako setup --agent <name>` (validated against installed
  plugins; unknown name is an actionable error, no prompt).
- Non-TTY without `--agent` (CI / headless): selection is skipped gracefully — no
  prompt — with a note to set it later.

`setup` also records a **completion marker** (`system.setup_completed`, the build
version). Agent-requiring commands print a **non-blocking** stderr nudge when setup
has never been run (or the recorded version predates a setup-affecting change), then
proceed — it never blocks the single-agent happy path.

### 10.3 `system.*` config keys are file-only (BREAKING)

`kanibako system config system.<key> <value>` (and the reset path) no longer SET any
`system.`-prefixed key — including `system.default_agent`. The CLI still **reads and
shows** them; it refuses to set them and points you at the config file:

- To set the default agent: run **`kanibako setup`** (it writes it for you), or edit
  `global/settings.yaml`'s `[agent.default] default_agent` directly.
- To change a structural path (e.g. `system.data`): edit `~/.config/kanibako.yaml`.

Non-`system.`-prefixed settings (e.g. `model`, `box.image`) remain CLI-settable at
every scope, including the global tier (`kanibako system config model=opus`).

**What you must do:** replace any scripted `kanibako system config system.*=…`
invocations with a `setup` run or a direct file edit.

### 10.4 Blanket `--agent` and `--box` flags

Two flags now parse on every command (passing one to an unrelated command is an
error, not a silent no-op):

- **`--agent <name>`** — uniform, top-precedence, **ephemeral** (this invocation only)
  agent override. Pulls that agent's whole config; never persisted.
- **`--box <name-or-path>`** — universal **subject/anchor** selector: act on a box
  that is not your cwd. The value is a path OR a box name (**name takes precedence**);
  it replaces the need to `cd` into the box. It coexists with the box-command
  destination group (`--default/--standalone/--workset` on `move`/`convert`), which is
  an orthogonal axis: `kanibako box convert --box mybox --standalone` works.

### 10.5 `-p`/`--project` removed (clean break)

The old `refresh -p/--project` flag is **removed outright** with no deprecation alias.
Use `--box <name-or-path>` instead.

### 10.6 Box-name constraints (NEW)

New box names (at creation / `--name`) are validated against a blocklist. Allowed:
unicode letters/digits plus interior `_`, `-`, `.`. Blocked: control characters, all
whitespace, every ASCII punctuation char except `_ - .`, the names `.` and `..`, a
leading `-` or `.`, a trailing `.` or whitespace, and length over 64. Uppercase ASCII
folds to lowercase. Pre-existing non-conforming boxes still resolve but are **flagged**
(warned), not rejected — rename them at your convenience.

## 11. Removed deprecation shims (rename-class; clean break)

The transitional aliases that bridged the old naming to the frozen
`box` / `agent` / `rig` model are **removed outright** — there are no deprecation
warnings or fallbacks left. Update to the canonical forms:

| Old (removed)                     | Use instead                                  |
|-----------------------------------|----------------------------------------------|
| `kanibako image ...`              | `kanibako rig ...`                            |
| `kanibako container ...`          | `kanibako box ...`                            |
| `kanibako rig create <name>`      | `kanibako rig extend <name> --from <rig>`     |
| `kanibako rig create --template T`| `kanibako rig prep T`                         |
| `kanibako rig rebuild [<img>]`    | `kanibako rig prep [<name>] --force`          |
| `... config image[=...]`          | `... config box.image[=...]`                  |
| `... config agent[=...]`          | `... config box.agent[=...]`                  |

The `image` / `agent` short config-key names no longer resolve to anything; only
the canonical `box.image` / `box.agent` keys are recognized (setting an `image`
or `agent` key is now an "unknown key" error). Image-freshness notices that used
to suggest `kanibako rig rebuild` now point to `kanibako rig update`.

---

## 12. Removed data-relocation shims (clean break; no auto-migration)

The auto-handlers that silently moved or read your old on-disk state are
**removed outright**. They may strand state created by older versions — migrate
it by hand using the tables below.

### 12.1 Snapshot `.tar.xz` archives → directory snapshots

Vault snapshots are now **directory snapshots only** (`reflink` / `hardlink`).
The legacy compressed-archive format is no longer created, listed, restored, or
pruned; a leftover `*.tar.xz` file under `<vault>/.versions/` is simply ignored
(not shown by `kanibako box vault list`, never picked up by `prune`).

To recover data from an old archive, extract it manually:

```bash
# Recover a legacy archive into a directory snapshot (named by its timestamp).
cd <vault>/.versions
mkdir 20260221T103000Z
tar -xJf 20260221T103000Z.tar.xz -C 20260221T103000Z
rm 20260221T103000Z.tar.xz      # optional: it is otherwise ignored
```

After that the directory is a normal snapshot, restorable with
`kanibako box vault restore 20260221T103000Z`.

### 12.2 Config & env files at old locations

`config_file_path` now resolves **only** the current location, and the global
`env` file is no longer auto-moved. If you have files at the old paths, move
them yourself:

| What        | Old location (no longer read/moved)        | Current location                |
|-------------|--------------------------------------------|---------------------------------|
| Main config | `$XDG_CONFIG_HOME/kanibako/kanibako.yaml`  | `$XDG_CONFIG_HOME/kanibako.yaml`|
| Global env  | `$XDG_CONFIG_HOME/kanibako/env`            | `<data>/env` (`@system.data/env`) |

```bash
mv ~/.config/kanibako/kanibako.yaml ~/.config/kanibako.yaml
mv ~/.config/kanibako/env "$(kanibako system config system.data)/env"
```

(`$XDG_CONFIG_HOME` defaults to `~/.config`. Adjust if you set it explicitly.)
