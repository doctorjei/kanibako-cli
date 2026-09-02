# Path Defaults — the declared tables, leaf names and message text behind `paths.py`

_no logic, no I/O: only the values `settings/paths.py` resolves and the words it prints_

⚠️ **RELOCATION PASS, 2026-08-18.** Every explanatory comment that used to live in
`src/kanibako/settings/messages.py` is here; the source keeps one-line descriptors and `⚑`
markers only. Absence of a symbol below means "nothing was displaced from it", never "does not
exist". **Claims found FALSE against the code and the spec were DROPPED rather than moved** — each is
recorded under [Dropped as false](#dropped-as-false) so nobody re-derives them from git history.

**Authority:** `~/canon/workbook/specs/settings-keyspace-1.8.0.md` — §1 (the two-layer path
foundation and its XDG resolution rule) and §2g (the `system.*` path tier); the standalone
detection marker is `system-design-1.8.0.md` § "Detection & import". ⚑ **The specs are the LIVE
authority; read them first.**

## What the module is

A declaration file. It holds the XDG variable names and their spec defaults, the two path-key
default tables (Layer-1 `config.*`, Layer-2 `system.*`), the directory- and file-name leaves the
layout tier composes, the status tokens the workset listing emits, the whole user-visible MESSAGE
catalogue, and the box shell-file contents.

It has **no functions, no classes and no docstrings** — the entire file is module-level assignment.
That is the point: `paths.py` is the resolver and this is the table it resolves, so a value can be
read, diffed and changed without opening 1,400 lines of resolution logic. It imports nothing at all,
which is what lets it sit under `paths.py` with no cycle risk.

⚑ **`paths.py` is very nearly the only consumer.** Every `MSG_*` / `WARN_*` / `ERR_*` constant, and
every name leaf except `STANDALONE_META_DIR`, is used in `settings/paths.py` and nowhere else; most
reach the rest of the tree only because `paths.py` re-exports them. `STANDALONE_META_DIR` is the
exception, imported directly by `commands/box/{_lifecycle,_parser,_duplicate}.py`,
`commands/clean.py` and `launch/box_resolve.py`. Treat "used once" here as normal, not as dead
weight: these exist to be a single spelling, not to have many callers.

⚑ **The message strings are USER-VISIBLE OUTPUT.** Every `MSG_*` / `WARN_*` / `ERR_*` literal is
printed or logged verbatim. Editing one is a user-facing change, not a comment change.

### ⚑ Where this overlaps `paths.py.md` — read that one for the RESOLVER, this one for the TABLE

`llm-docs/kanibako/settings/paths.py.md` carries "Layer 1 — the CONFIG-key FOUNDATION", "Layer 2 —
system-scope SETTINGS keys that are PATHS" and an `_XDG_SPEC_DEFAULTS` section. Those three tables
live HERE, not in `paths.py`, and this file is their home; `paths.py.md`'s sections describe how
`resolve_config_paths` / `resolve_system_paths` / `host_xdg_map` CONSUME them. Two of the three
descriptions there have drifted (see the note at the end of
[Dropped as false](#dropped-as-false)) — boarded, not fixed here, because that file belongs to
another pass.

## The XDG base directories

```python
XDG_DATA_HOME · XDG_CONFIG_HOME · XDG_RUNTIME_DIR · XDG_STATE_HOME · XDG_CACHE_HOME
```
The five freedesktop Base Directory variable NAMES, as constants rather than literals.

They are names, not values: each is used both as an `os.environ` lookup key and as a dict key into
the table below, so a typo in either role becomes a lookup that silently misses rather than a
`NameError`.

```python
XDG_SPEC_DEFAULTS: dict[str, str]     # {var name -> home-relative default suffix}
```
Spec defaults for the XDG base dirs that HAVE one.

Spec §1: *"use the env var iff set and absolute (a relative value is invalid → ignored → use
default), else the spec default: `XDG_DATA_HOME`→`~/.local/share` · `XDG_CONFIG_HOME`→`~/.config` ·
`XDG_STATE_HOME`→`~/.local/state` · `XDG_CACHE_HOME`→`~/.cache`."* The suffixes are stored
HOME-RELATIVE, with no leading `~` or `/`, because `resolve_xdg` joins them onto `Path.home()`.

⚑⚑ **`XDG_RUNTIME_DIR` is deliberately ABSENT, and adding it would break spec conformance
SILENTLY.** The freedesktop spec defines no default for it, so `paths.resolve_xdg` takes
`spec_default_suffix=None` for that one variable and routes to `_fallback_runtime_dir` — which picks
`/run/user/<uid>/kanibako` when that base is usable, else a 0700 `mkdtemp`, and **warns either way**
(never silent, by design: the fallback is not the user's real runtime dir, and helper sockets land
there). A row added here would be picked up by `host_xdg_map`'s loop over `XDG_SPEC_DEFAULTS.items()`,
resolve against an invented default, and then be overwritten by the explicit
`xdg_map[XDG_RUNTIME_DIR] = resolve_xdg(XDG_RUNTIME_DIR, None)` line that follows — so the warning
path would still run and nothing would look broken, while the invented default stayed live for every
other reader of the table. That is why the absence carries a marker at the line.

## The two-layer path foundation

Spec §1's **TERMINOLOGY**, which is exactly what the two tables below are: *"Config keys = the
Layer-1 bootstrap paths, prefix `config.*`, living in `kanibako_config.yaml`, resolved by the flat
foundation. Settings keys = the Layer-2 keyspace (`system.*` / `workset.*` / `box.*` / `agent.*`),
resolved by `assemble→merge→expand`."* (on what is now `kanibako.cfg`)

```python
CONFIG_PATH_DEFAULTS: dict[str, str]      # Layer 1 — spec §1
```
The bootstrap CONFIG keys and their defaults.

Bootstrap keys read from `kanibako.cfg`, resolved FLAT — **not** by the keyspace pipeline.
Chicken-and-egg: the pipeline needs these resolved to find its own input files, so
`paths.resolve_config_paths` runs them through a SINGLE-level `LevelView` with no cascade. `@config.*`
refs resolve against THIS set; `$XDG_*` against the environment.

⚑ **The set may GROW, and the spec refuses to fix its size**: *"The Layer-1 set is exactly the config
keys in the table below — no fixed count is stated; the set may grow."* `config.journal` was added
2026-06-30b, after the set was otherwise "finalized" on 2026-06-29f — the finalization is a DATE, not
a count. **Do not write a count into prose about this table.** One was written here and had been
false for the entire life of `config.journal`.

⚑ **The SIZE IS PINNED BY A TEST, and that test is the live count**:
`tests/test_settings/test_manifest_conformance.py` asserts `len(CONFIG_PATH_DEFAULTS) == 6` and
`len(SYSTEM_PATH_DEFAULTS) == 11` against the manifest. Adding or removing a key here fails it by
design — the gate that makes a new key a deliberate act. Read the count from that assertion, never
from a comment.

⚑ There is no `config.global` key. Its children inline `@config.data/global/...`, and the `global/`
dir is created on demand by the atomic writer when those files are first written.

`config.journal` is the LIFECYCLE JOURNAL — a write-ahead log of in-flight box-lifecycle ops
(create/import) for crash recovery, normally EMPTY. It sits beside `config.registry` and is the
TRANSIENT truth next to the registry's steady-state truth. It is config-LOCATED rather than a
resolution-pipeline prerequisite: lifecycle recovery runs before and around registration, so the
journal must be locatable before the keyspace is up. See `kanibako.launch.journal`.

```python
SYSTEM_PATH_DEFAULTS: dict[str, str]      # Layer 2 — spec §1/§2g
```
The system-scope SETTINGS keys that are PATHS, and their defaults.

These are SETTINGS keys (system tier), **not** bootstrap config. They resolve the normal way at
launch (`assemble→merge→expand`) — but the flat resolver ALSO materializes them into `StandardPaths`,
the host-side path surface, by resolving their `@config.*` refs against the Layer-1 foundation
(`paths.resolve_system_paths`, whose `lookup` branches on the `config.` prefix: spec §1A / JC-2).

⚑ **A default here `@`-refs a Layer-1 config key, an XDG base, OR ANOTHER KEY IN THIS SAME TABLE.**
The third case is not an exception, it is the channels block: the four channel type-roots ref
`@system.channelroot`, and `system.channels.broadcast` refs `@system.channels.chat`. Spec §1 declares
exactly that shape — the type-roots *"default under `@system.channelroot`; broadcast under
`@system.channels.chat`"*. **Declaration order is therefore load-bearing inside the channels block**:
`chat` is declared before `broadcast` because `broadcast` resolves against it.

⚑ **`system.channelroot` is Layer 2, not Layer 1.** It moved out of the bootstrap set (2026-06-29f)
because it is not a load-the-keyspace prerequisite: its on-disk skeleton is created on the LAUNCH
path (the category guarantee-create for the type-root dirs, plus the chat-log seeding), never at
setup. That move is what unified the whole channel subtree — `channelroot` plus `system.channels.*` —
into one layer.

⚑ **THIS TABLE IS THE FLOOR, NOT THE STORE (corrected 2026-08-23).** Every key here is CLI-settable
at the system scope: `config set` / `reset` / `get` route it through `config_keys._KEY_ROUTES` to
the `system:` table of the SYSTEM SETTINGS file, which the cascade layers OVER these defaults. It
used to refuse the whole family as "structural layout config" and send the user to the
`kanibako.cfg` `[system]` table — a spec violation (§2g declares them Layer-2 settings keys;
§2a names `system.template` among the CLI-settable). Adding a row here therefore also needs a
`_KEY_ROUTES` entry and a `KNOWN_CONFIG_KEYS` spelling, or the new key sets nowhere.

⚑ **STILL OPEN:** `load_system_config` resolves this table from the CONFIG file set alone, so every
`StandardPaths` field keeps the floor value while the cascade sees the repoint. Closing that means
moving where the `[system]` table lives, not another routing change.

⚑ `system.template` and `system.canon` are deliberately ROOTS rather than leaves — the box-home seed
is `@system.template/box/home` and the box-bound handbook is `@system.canon/handbook`, so each root
leaves room for further subtrees without minting a new key (spec §2g).

### ⚑⚑ Both tables have a SECOND spelling elsewhere, and it is a known duplication

`settings/config.py` carries its own literals for these keys and says so at the site: *"⚑ These
literals DUPLICATE paths.CONFIG_PATH_DEFAULTS / SYSTEM_PATH_DEFAULTS — every edit here needs the
matching edit there."* The pointer exists only on that side; there is nothing at this end saying an
edit here obliges an edit there. `settings/defaults_inventory.py` registers both tables by name
(`"bootstrap.py (config tier)"` / `"(system tier)"`), so the inventory is a third reader.
**Before adding, renaming or removing a key in either table, grep `config.py` too.**

## File and directory name leaves

```python
SETTINGS_FILE · PROFILE_FILE · BASHRC_FILE · SHELL_D_FILE · IGNORE_FILE
BOXES_PATH · HOME_PATH · KANIBAKO_PATH · LOGS_PATH · RO_PATH · RW_PATH · VAULT_PATH
```
The single-segment names the layout tier composes paths from.

Named rather than inlined so the same leaf, spelled at a dozen sites, cannot drift into two
spellings. `KANIBAKO_PATH` carries a second obligation: it is also the leaf in
`CONFIG_PATH_DEFAULTS["config.data"]`'s own default (`$XDG_DATA_HOME/kanibako`), so the two agree by
construction rather than by coincidence.

```python
RUN_USER_UID_PATH = "/run/user/%d"
```
The per-uid runtime base, as a `%d` FORMAT string.

⚑⚑ **It is CONCATENATED INTO `WARN_RUNDIR_UNUSABLE`**, which puts a `%d` in the middle of that
message's `%s` run. Changing this literal's conversion — or dropping the `%d` — silently changes that
warning's argument contract; see [the message catalogue](#the-message-catalogue).

```python
STANDALONE_META_DIR = 'box_data'
```
The STANDALONE box-store dir name — `@meta.box.path`, and half the detection marker.

Standalone detection (`system-design-1.8.0.md` § "Detection & import") is an ANCESTOR WALK looking
for two things together: *"an ancestor with a `box_data/` marker DIR (the LOCATOR) +
`workset.yaml` declaring standalone."* This constant is the first half. Renaming it breaks detection
for every already-created standalone box on disk — a data-layout change, not a code change.

⚑ It is **not a bare marker dir** (spec §4): it is the real box store, holding `home/`, the
by-default-ABSENT `box.yaml`, and the helper-log JSONL. `@workset.boxes` resolves to it too, as
the empty leaf.

⚑ **This constant is the ONLY carrier of the literal**, and `project/import_reconcile.py` imports it
at module scope. That is what makes the rename above a single-site edit: the ancestor walk's marker
and the J2 journal key are the same string by construction, not by two edits landing together.

## Status tokens

```python
STATUS_OK = "ok" · STATUS_MISSING = "missing" · STATUS_NO_DATA = "no-data"
```
The three status words the workset project listing emits.

They report the two-way presence check in `paths.iter_workset_projects`: the box's project DIR and
its WORKSPACE dir, resolved separately. `ok` = both present · `missing` = the project dir exists but
its workspace does not · `no-data` = no project dir at all. ⚑ The pair is asymmetric on purpose —
there is no token for "workspace without project dir", which falls into `no-data`.

## Registry tokens

```python
KIND_PROJECT = "project" · KIND_WORKSET = "workset"
```
The two entity kinds `paths.resolve_name` returns beside a resolved path.

A bare name can name either, and the two are separate namespaces, so the caller must branch on the
kind rather than assume — this token is what it branches on. (The consequence of the shared bare-name
surface is `ERR_PROJECT_DIR_IS_WS`, below.)

```python
UNREGISTERED_MARKER = "__unregistered__"
```
The stand-in boxes-dir segment for a default-mode project that is not registered.

`paths` returns `("", std.boxes / UNREGISTERED_MARKER)` for an unregistered workspace: the EMPTY
STRING is the real signal that there is no box name, and the marker only keeps the accompanying path
well-formed instead of pointing a caller at the boxes ROOT — which is a live directory full of other
boxes. ⚑ Callers must test the NAME, not the path. Nothing on disk is expected at the marker path.

## The message catalogue

Every literal below is USER-VISIBLE, and every one is a **printf-style `%`-format template** — not an
f-string and not `str.format`. Two consequences, stated once rather than at each line:

* **The `WARN_*` templates are handed to `logger.warning(TEMPLATE, *args)` UNFORMATTED**, so the
  logging module interpolates lazily. A count mismatch between the template's conversions and the
  call's arguments does not raise at the call site — it surfaces as a logging-internal error, or as a
  message that renders wrong. The `MSG_*` / `ERR_*` constants are the other shape, applied eagerly
  with `%` at the raise/print site, where a mismatch IS a `TypeError`.
* **Positional, so ORDER IS THE CONTRACT.** Adjacent same-typed arguments can be swapped with no
  error at all; the message simply lies. Several templates repeat one argument at two positions,
  which is the case a reader is most likely to mis-count — hence the per-constant argument note the
  source keeps at each line.

⚑ The multi-line templates are built with **explicit `+` concatenation**, not implicit adjacent-string
juxtaposition. That matters in a file that is nothing but literals: implicit juxtaposition turns a
MISSING COMMA between two entries into a silent concatenation, and explicit `+` does not.

### One-time-setup and progress

```python
MSG_OTS_KB_INIT       # "[One Time Setup] Initializing kanibako in %s... "
MSG_OTS_WS_PROJ_INIT  # "[One Time Setup] Initializing workset project in %s..."
MSG_DONE              # "done."
```
The first-run progress line and its terminator.

⚑ `MSG_OTS_KB_INIT` ends in a TRAILING SPACE and no newline; it is printed with `end=""` and
`MSG_DONE` completes the line. Stripping that trailing space — or adding a newline — breaks the
pairing visually, and nothing tests the join. ⚑ Both go to **stderr**, not stdout, so a progress
notice never contaminates parseable output.

### XDG resolution warnings

```python
WARN_RELATIVE_XDG     # (var_name, value)
WARN_FALLBACK_RT_DIR  # (var_name, chosen_dir, var_name)
WARN_RUNDIR_UNUSABLE  # (var_name, uid, chosen_dir, var_name)
```
The three ways an XDG base dir fails to resolve cleanly.

* `WARN_RELATIVE_XDG` uses **`%r` for the value**, not `%s` — a rejected relative path is being quoted
  back at the user as data, and an empty or whitespace-only value has to stay visible.
* Both runtime-dir warnings **repeat `var_name` at the LAST position**: the message names the variable
  that was unset, then names it again in the cure. Three and four arguments respectively, not two and
  three.
* ⚑ `WARN_RUNDIR_UNUSABLE` embeds `RUN_USER_UID_PATH`, so its conversions run **`%s`, `%d`, `%s`,
  `%s`** — the `%d` is the uid and it sits SECOND. This is the one template whose argument types are
  not uniform, and the `%d` is invisible at the line because it arrives through concatenation.

### Workset and box advisories

```python
WARN_WS_NO_ROOT        # (workset name, root path)
WARN_WS_BAD_LOAD       # (workset name, exception)
WARN_WS_BOX_BAD_NAME   # (box name, the REASON it fails)
WARN_BOX_BAD_KUID      # (the KUID, box name)
WARN_BOX_NO_VAULT      # (box name, expected vault path)
```
Conditions that DEGRADE a box rather than stop it.

⚑ **All five say the thing still works, and that is the design.** Each names a defect, states that
resolution or launch proceeds anyway, and gives a concrete way to silence it (*"rename it when
convenient"* · *"fix workset.kuid or set workset.skip_kuid_check=true"* · *"recreate the directory or
set box.enable_vault=false"*). A warning a user can neither act on nor silence becomes noise on every
launch, which is what these are shaped to avoid — so a NEW warning here owes a cure clause too. The
two `WARN_WS_*` pair go to stderr via `print`; the three box advisories go through the logger.

⚑ `WARN_BOX_BAD_KUID` takes the KUID FIRST and the box name second — the reverse of the other four,
which all lead with the entity. Two `%s` of the same type, so a swap renders a plausible-looking
wrong message and nothing catches it.

⚑ `WARN_WS_BOX_BAD_NAME`'s second argument is the reason string from
`launch.box_identity.box_name_reason`, not the rules themselves. The name is WARNED about, never
rejected: `validate_box_name` enforces the same blocklist at CREATION, while a name already on disk
only ever gets this advisory (`_flag_nonconforming`). That asymmetry is the point — tightening the
blocklist must not strand existing boxes.

⚑ `WARN_BOX_NO_VAULT`'s second argument is specifically the **RW** vault path, though the message
says "vault" unqualified; and its first falls back to the project PATH when the box has no name.

### Errors — settings and config

```python
ERR_SETTINGS_BAD_PATH       # ("config" | "system", key)
ERR_SETTINGS_BAD_REF        # ("" | "config", ref)
ERR_CONFIG_NO_FILE          # (config file path)
ERR_CONFIG_LAYER1_SETTINGS  # (the Layer-1 file path, the offending keys)
```
Unresolvable path-tier keys and refs, plus the Layer-1 file's own contract.

⚑ **`ERR_CONFIG_LAYER1_SETTINGS`'s CURE IS ORDERED, and the order is load-bearing** (Jei,
2026-08-31). Every verb resolves its paths through `config.bootstrap_config_paths`, `system set`
included, so a message that led with the command would send the user to a command that refuses for
this same reason. It leads with the hand-edit: delete the lines, then set what you meant. Its second
argument is the offending keys, dotted and sorted, one per line — the file itself is the first.

⚑ **The first argument of each is a LAYER DISCRIMINATOR, not a value.** `ERR_SETTINGS_BAD_PATH`'s is
literally `"config"` or `"system"`, naming which of the two layers failed. `ERR_SETTINGS_BAD_REF`'s is
`""` or `"config"`, and it is spliced INSIDE the `@`-sigil — `"Unknown @%s-reference: %s"` renders
either `Unknown @-reference:` or `Unknown @config-reference:`. That empty string is load-bearing
punctuation; passing a plain scope name there would produce `@system-reference` for a ref that is not
prefix-qualified.

⚑ `ERR_SETTINGS_BAD_PATH` is raised on a branch the code marks unreachable — every key in both tables
has a default, so `resolve_value` cannot come back unset. It guards a future table entry declared
WITHOUT one; it is not a live path, and a test that reaches it is testing the guard, not the resolver.

### Errors — projects and worksets

```python
ERR_PROJECT_NO_PATH     # (the path that does not exist)
ERR_PROJECT_NEW_HOME    # (no arguments)
ERR_PROJECT_REG_HOME    # (no arguments)
ERR_PROJECT_NAME_USED   # (name)
ERR_PROJECT_DIR_IS_WS   # (name)
ERR_WORKSET_NO_PROJECT  # (project name, workset name)
ERR_WORKSET_NO_WORKSET  # (project dir)
ERR_WORKSET_WS_NOT_BOX  # (name, name — the SAME value twice)
ERR_WORKSET_NOT_IN_BOX  # (workset name, workspaces dir)
```
Refusals and not-found errors for the project/workset layer.

⚑ **`ERR_PROJECT_NEW_HOME` and `ERR_PROJECT_REG_HOME` are the `$HOME` GUARD, and they take no
arguments at all.** Rooting a project at `$HOME` would mount the entire home directory as the
workspace — the reason both refuse. They are two messages rather than one because the escape hatch
differs: creation has one (`kanibako create --standalone ~ --allow-home`, spelled out in the message)
and registration has none, so `ERR_PROJECT_REG_HOME` deliberately offers no cure. ⚑ Neither is a
template — adding a `%s` to either changes a bare `raise ProjectError(CONST)` into a broken one at
two call sites that pass nothing.

⚑ `ERR_WORKSET_WS_NOT_BOX` interpolates the SAME name at BOTH positions — once to say what was named,
once inside the suggested `'%s/<project>'` spelling. Two arguments, one value; a reader counting
distinct values supplies one argument and gets a `TypeError` at format time.

⚑ `ERR_PROJECT_DIR_IS_WS` is the **shadowing** refusal, and it is not a namespace collision: box and
workset names are separate namespaces (see `KIND_PROJECT` / `KIND_WORKSET`), so the name is legal.
What it refuses is that the resulting BARE name would resolve to the box and hide the workset in
bare-name lookups. `--force` proceeds anyway, which is why the message names the flag.

⚑ `ERR_PROJECT_NO_PATH` is raised from TWO sites with different values — a project path and a
standalone ROOT. Its note says "the path that does not exist" rather than naming one of them.

## The box shell files

```python
_SHELL_D_SOURCE_LINE · BASHRC_CONTENTS · SHELL_D_CONTENTS · PROFILE_CONTENTS
```
The `.bashrc` / `.profile` / `.shell.d` content written into a box home.

`_SHELL_D_SOURCE_LINE` is the `~/.shell.d/*.sh` user/template extension point: a `for` loop that
sources every readable `.sh` fragment and then `unset`s its loop variable. It is module-private and
SPLICED into both `BASHRC_CONTENTS` and `SHELL_D_CONTENTS` with `%s`, so the two can never disagree
about how fragments get sourced.

⚑⚑ **INTERACTIVE ONLY — it never reaches the agent.** `.bashrc` is read by an interactive shell; the
agent process is exec'd, not shelled, so anything a user puts in `~/.shell.d/` is invisible to it.
The route for values the AGENT must see is the keyspace: `env.<VAR>` and `secret_path.<VAR>`. This is
a recurring wrong guess — a user who "exported it in `.shell.d`" and finds the agent cannot see it
has not hit a bug.

⚑ `PROFILE_CONTENTS` sources `~/.bashrc` from `.profile` so a LOGIN shell picks up the same
environment as an interactive one; bash reads only `.profile` in the login case and would otherwise
skip the `.shell.d` chain entirely.

⚑ `BASHRC_CONTENTS`' prompt is `"${KANIBAKO_PS1:-(kanibako) \u@\h:\w\$ }"` — a parameter-expansion
DEFAULT, not an assignment, so a `KANIBAKO_PS1` supplied through `env.KANIBAKO_PS1` wins and the
box-marker prompt is only the fallback. The backslashes are doubled in the Python source because it
is a regular string, not a raw one.

⚑ **`SHELL_D_FILE`'s value is an UPGRADE trigger, not just a name.** `paths._upgrade_shell` decides
whether an existing box's `.bashrc` already has the seam by testing for the substring
`SHELL_D_FILE + "/"` — i.e. `.shell.d/` — and its own comment at that line explains why the trailing
slash is load-bearing. So renaming the DIRECTORY makes the test miss on every box already on disk and
appends a second sourcing block to each of their `.bashrc` files; rewording the rest of
`_SHELL_D_SOURCE_LINE`, or the `"# Source user init scripts"` header the two contents constants
share, changes nothing about detection.

---

## Dropped as false

Prose in the pre-pass source was checked against the code and the spec. These claims were **deleted,
not relocated** — relocating a drifted claim launders it into a document that reads as current.
Recorded here so nobody restores them from git history.

| # | site | the claim | what the code / spec says |
|---|---|---|---|
| 1 | `CONFIG_PATH_DEFAULTS` banner | *"config keys finalized at 5"* | The table it introduces has **SIX** entries — `tests/test_settings/test_manifest_conformance.py` asserts `len(CONFIG_PATH_DEFAULTS) == 6` — and the spec explicitly declines to state a count: *"no fixed count is stated; the set may grow"* (annotations §1: *"`journal` was added 2026-06-30b"*). The "finalized" stamp is a DATE (2026-06-29f), not a size. False on both halves, and self-contradicted by the dict two lines below it, which even carries its own comment for the sixth key. |
| 2 | `SYSTEM_PATH_DEFAULTS` banner | *"each `@`-refs a Layer-1 config key or an XDG base"* | **Five of the eleven entries ref neither** — `system.channels.{common,chat,mailboxes,share}` ref `@system.channelroot` and `system.channels.broadcast` refs `@system.channels.chat`, all Layer-2 keys in this same table. Spec §1 declares that third shape directly. Replaced with a statement that includes it, plus the declaration-order consequence the old wording hid. |
| 3 | the channels sub-comment | *"the type-roots derive from `system.channelroot`"* | True of four of the five; `system.channels.broadcast` derives from `@system.channels.chat`, which is what makes the block order-dependent. Spec §1: *"default under `@system.channelroot`; **broadcast under `@system.channels.chat`**"*. Corrected in place rather than dropped. |
| 4 | line 1 (the module header) | *"Variables instead of hardcoded values."* | Not false, but vacuous — it states the reason ANY constant exists and says nothing about this module. Replaced with a one-line descriptor naming what the file holds. Recorded here because it is the file's oldest and least re-read line, and a reader looking for a scope claim will find its replacement, not this. |

⚑ **The same two stale claims as #1 and #2 also live in `llm-docs/kanibako/settings/paths.py.md`** (a
different file, and another pass's): its "Layer 1" section says *"The **5** bootstrap CONFIG keys"*
while naming `config.journal` in the very next paragraph, and its "Layer 2" section repeats *"each
`@`-refs a Layer-1 config key (or an XDG base)"*. That file also heads its section
`### _XDG_SPEC_DEFAULTS` — leading underscore — for a constant that is PUBLIC and no longer lives in
`paths.py` at all. **Boarded, not fixed here.**

## Kept in the source, reworded — the per-message argument notes

The eleven `# Was {field}` trailing comments on the message constants were **checked against every
call site and found ACCURATE** — no drift. They are not relocated, because they are the file's only
statement of what each `%s` means and a positional swap is silent (the KEEP TEST, met at the exact
line). They were reworded from the historical *"Was `{ws_name}`, `{root}`"* — which describes a
`str.format` spelling that no longer exists anywhere in the tree, and reads to a fresh reader like a
note about something removed — into a live argument contract in the file's `%`-format terms. Same
information, one line each, no claim about the past.
