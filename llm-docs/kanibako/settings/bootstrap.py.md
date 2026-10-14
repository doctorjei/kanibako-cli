# Bootstrap path literals — the declared tables and leaf names behind `paths.py`

_no logic, no I/O: the one home for hardcoded path values_

**Authority:** `~/canon/workbook/specs/settings-keyspace-1.8.0.md` — §1 (the two-layer path
foundation and its XDG resolution rule) and §2g (the `system.*` path tier); the standalone
detection marker is `~/canon/workbook/specs/system-design-1.8.0.md` § "Detection & import".

## What the module is

A declaration file: the XDG variable names and their spec defaults, the bootstrap file names, the
two path-key default tables (Layer-1 `config.*`, Layer-2 `system.*`), the spawn-budget built-ins,
and the file- and directory-name leaves the layout tier composes. Every path literal in the tree
belongs here (CONVENTIONS P10 "derive from declared keys"); `settings/messages.py` holds the
message text and imports `RUN_USER_UID_PATH` back from here.

⚑ **IT MUST STAY IMPORT-FREE.** `settings/paths.py` imports it and `project/workset.py` imports
that, so an import added here can close a cycle. `tests/test_settings/test_paths.py`'s
`test_bootstrap_is_import_free` pins it.

`settings/paths.py` RESOLVES these tables; `paths.py.md` covers how.

## The XDG base directories

```python
XDG_DATA_HOME · XDG_CONFIG_HOME · XDG_RUNTIME_DIR · XDG_STATE_HOME · XDG_CACHE_HOME
```
The five freedesktop Base Directory variable NAMES, as constants rather than literals.

Each is used both as an `os.environ` lookup key and as a dict key into the table below, so a typo
in either role becomes a lookup that silently misses rather than a `NameError`.

```python
XDG_SPEC_DEFAULTS: dict[str, str]     # {var name -> home-relative default suffix}
```
Spec defaults for the XDG base dirs that HAVE one.

Spec §1: *"use the env var iff set and absolute (a relative value is invalid → ignored → use
default), else the spec default"*. The suffixes are stored HOME-RELATIVE, with no leading `~` or
`/`, because `paths.resolve_xdg` joins them onto `Path.home()`.

⚑⚑ **`XDG_RUNTIME_DIR` is deliberately ABSENT, and adding it would break spec conformance
SILENTLY.** The freedesktop spec defines no default for it, so `paths.resolve_xdg` takes
`spec_default_suffix=None` for that one variable and routes to `_fallback_runtime_dir`, which warns.
A row added here would be picked up by `paths.spec_default_xdg_map`'s loop, resolve against an
invented default, and then be overwritten in `host_xdg_map` by the explicit
`resolve_xdg(XDG_RUNTIME_DIR, None)` that follows — so the warning would still run and nothing would
look broken, while the invented default stayed live for every other reader of the table.

## The bootstrap files

```python
CONFIG_FILE = "kanibako.cfg" · LEGACY_CONFIG_FILE = "kanibako_config.yaml"
SITE_CONFIG_DIR = "/etc/kanibako"
SITE_CONFIG_FILE = "base.cfg" · SITE_SETTINGS_FILE = "settings_base.yaml"
```
The paths that cannot be key-derived: the user's bootstrap config file (under `$XDG_CONFIG_HOME`,
spec §1), its retired name, and the machine-wide site directory with its two base files. Compose
both site files from `SITE_CONFIG_DIR`; the directory is spelled once.

## The two-layer path foundation

The two tables are spec §1's two layers: Layer-1 config keys (`config.*`, in the `.cfg` files,
resolved by the flat foundation) and Layer-2 settings keys (resolved by `assemble→merge→expand`).

⚑ **NO COUNT IN PROSE.** Spec §1: *"no fixed count is stated; the set may grow."*
`tests/test_settings/test_manifest_conformance.py` (`TestPathDefaults`) asserts both tables' sizes;
adding or removing a key fails it by design, which makes a new key a deliberate act. Read the size
from that test, never from a comment.

```python
CONFIG_PATH_DEFAULTS: dict[str, str]      # Layer 1 — spec §1
```
The bootstrap CONFIG keys and their defaults, read from `kanibako.cfg` and resolved FLAT by
`paths.resolve_config_paths` — **not** by the keyspace pipeline, which needs these resolved to find
its own input files.

⚑ There is no `config.global` key. Its children inline `{config.data}/global/...`, and the
`global/` dir is created on demand by the atomic writer when those files are first written.

`config.journal` is the LIFECYCLE JOURNAL — a write-ahead log of in-flight box-lifecycle ops
(create/import) for crash recovery, normally EMPTY. It sits beside `config.registry` and is the
TRANSIENT truth next to the registry's steady-state truth. It is config-LOCATED because lifecycle
recovery runs before and around registration, so the journal must be locatable before the keyspace
is up. See `kanibako.launch.journal`.

```python
SYSTEM_PATH_DEFAULTS: dict[str, str]      # Layer 2 — spec §1/§2g
```
The system-scope SETTINGS keys that are PATHS, and their defaults.

These are SETTINGS keys (system tier), **not** bootstrap config. They resolve the normal way at
launch, and `paths.load_std_paths` ALSO materializes them into `StandardPaths`, the host-side path
surface (see `paths.py.md`).

⚑ **A default here refs a Layer-1 config key, an XDG base, OR ANOTHER KEY IN THIS SAME TABLE.** The
third case is the channels block: four channel type-roots ref `{system.channelroot}`, and
`system.channels.broadcast` refs `{system.channels.chat}`. Spec §1 declares that shape.
**Declaration order is therefore load-bearing inside the channels block**: `chat` is declared before
`broadcast` because `broadcast` resolves against it.

⚑ **THIS TABLE IS THE FLOOR, NOT THE STORE.** Every key here is CLI-settable at the system scope:
`config set` / `reset` / `get` route it through `config_keys._KEY_ROUTES` to the `system:` table of
the SYSTEM SETTINGS file, which the cascade layers OVER these defaults. Adding a row here therefore
also needs a `_KEY_ROUTES` entry and a `config_keys.KNOWN_CONFIG_KEYS` spelling, or the new key sets
nowhere. `tests/test_settings/test_repoint_reaches_std_paths.py` checks that a repoint of every key
in the table reaches `StandardPaths`.

⚑ `system.template` and `system.canon` are ROOTS rather than leaves, so each leaves room for
further subtrees without minting a new key (spec §2g). The box-bound handbook is
`{system.canon}/handbook`.

`settings/defaults_inventory.py` registers both tables by name (`"bootstrap.py (config tier)"` /
`"bootstrap.py (system tier)"`).

```python
SPAWN_BUDGET_DEFAULTS: dict[str, int]     # {"depth": …, "breadth": …}
```
The helper-hub spawn budget built-ins: how deep and how wide a box may spawn when no tier carries
the key. Keyed by LEAF because its two consumers want it two ways: `settings_launch` spells the
dotted keys for the launch floor, and `channels/helpers.py` takes the bare numbers for the in-box
resolve, which runs before any snapshot exists. Here rather than beside either consumer so the one
value has one carrier.

## File and directory name leaves

```python
PROFILE_FILE · BASHRC_FILE · SHELL_D_FILE · IGNORE_FILE
BOXES_PATH · CHANNELS_PATH · HOME_PATH · KANIBAKO_PATH · LOGS_PATH · RO_PATH · RW_PATH · VAULT_PATH
WORKSPACES_PATH · WORKSPACE_PATH
```
The single-segment names the layout tier composes paths from, named so one leaf spelled at many
sites cannot drift into two spellings.

⚑⚑ **`WORKSPACES_PATH` and `WORKSPACE_PATH` are DIFFERENT DIRS.** `workspaces` is the
primary/named workset's container of per-box workspaces; `workspace` is the STANDALONE box's single
one. Reading one for the other silently repoints every box of that mode.

⚑ `SHELL_D_FILE`'s value is also the `.bashrc` UPGRADE trigger; see `messages.py.md` § "The box
shell files" before renaming it.

`KANIBAKO_PATH` is also the leaf `paths.resolve_data_path` falls back to (`data_home /
KANIBAKO_PATH`), matching `config.data`'s default `{$XDG_DATA_HOME}/kanibako`. The two are separate
literals.

```python
RUN_USER_UID_PATH = "/run/user/%d"
```
The per-uid runtime base, as a `%d` FORMAT string.

⚑⚑ **It is CONCATENATED INTO `messages.WARN_RUNDIR_UNUSABLE`**, which puts a `%d` in the middle of
that message's `%s` run. Changing this literal's conversion — or dropping the `%d` — silently
changes that warning's argument contract; see `messages.py.md`.

```python
CREDS_WATCHER_LOG_SUFFIX · CREDS_WATCHER_LOCK_FILE · CREDS_LOCK_KEPT_WHY
```
The detached creds watcher's log suffix (`{workset.logs}/<box><suffix>`, beside the box's helper
log), its single-instance flock file in the box's `metadata_path`, and the one reason every verb
gives for leaving that file alone while a live watcher holds it.

```python
STANDALONE_META_DIR = 'box_data'
```
The STANDALONE box-store dir name — `{meta.box.path}`, and half the detection marker.

Standalone detection (system-design § "Detection & import") looks for an ancestor with a `box_data/`
marker DIR plus a `workset.yaml` declaring standalone. Renaming the constant breaks detection for
every standalone box already on disk — a data-layout change, not a code change.

⚑ It is **not a bare marker dir** (spec §4): it is the real box store, holding `home/` and the
by-default-ABSENT `box.yaml`. `{workset.boxes}` defaults to it in standalone mode
(`{meta.workset.path}/box_data`; `project/workset.py`'s `_STANDALONE_BOXES_LEAF`), so
`{meta.box.path}` is that same dir, and the standalone `workset.logs` default is `{workset.boxes}`.
Import the constant; never re-spell the string.

## Registry tokens

```python
UNREGISTERED_MARKER = "__unregistered__"
```
The stand-in boxes-dir segment for a default-mode project that is not registered.

`paths` returns `("", std.boxes / UNREGISTERED_MARKER)` for an unregistered workspace: the EMPTY
STRING is the real signal that there is no box name, and the marker only keeps the accompanying path
well-formed instead of pointing a caller at the boxes ROOT, a live directory full of other boxes.
⚑ Callers must test the NAME, not the path. Nothing on disk is expected at the marker path.

```python
KIND_PROJECT = "project" · KIND_WORKSET = "workset"
```
The two entity kinds `project.names.resolve_name` returns beside a resolved path. A bare name can
name either, and the two are separate namespaces, so the caller branches on the kind.
