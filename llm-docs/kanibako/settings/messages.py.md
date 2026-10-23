# Messages — the user-visible text, status tokens and box shell-file contents

_no logic, no I/O: the words kanibako prints and the shell files it writes_

## What the module is

A declaration file: the status tokens the workset listing emits, the user-visible MESSAGE catalog,
and the box shell-file contents. Its only import is `RUN_USER_UID_PATH`, from
`settings/bootstrap.py`, which `WARN_RUNDIR_UNUSABLE` splices in; that is safe because `bootstrap`
imports nothing.

⚑ **PATH LITERALS DO NOT LIVE HERE.** The XDG table, the two path-key default tables, the file- and
directory-name leaves, `STANDALONE_META_DIR` and the registry tokens are in `settings/bootstrap.py`;
`llm-docs/kanibako/settings/bootstrap.py.md` explains them.

Many modules import from this one directly, `settings/paths.py` most of all. Treat "used once" here
as normal, not as dead weight: these constants exist to be a single spelling, not to have many
callers.

⚑ **The message strings are USER-VISIBLE OUTPUT.** Every `MSG_*` / `WARN_*` / `ERR_*` literal is
printed or logged verbatim. Editing one is a user-facing change, not a comment change.

## Status tokens

```python
STATUS_OK = "ok" · STATUS_MISSING = "missing" · STATUS_NO_DATA = "no-data"
```
The three status words the workset project listing emits.

They report the two-way presence check in `paths.iter_workset_projects`: the box's project DIR and
its WORKSPACE dir, resolved separately. `ok` = both present · `missing` = the project dir exists but
its workspace does not · `no-data` = no project dir at all. ⚑ The pair is asymmetric on purpose —
there is no token for "workspace without project dir", which falls into `no-data`.

## The message catalog

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
MSG_OTS_WS_PROJ_INIT  # "[One Time Setup] Initializing workset project in %s... "
MSG_DONE              # "done."
```
The first-run progress line and its terminator.

⚑ `MSG_OTS_KB_INIT` ends in a TRAILING SPACE and no newline; it is printed with `end=""` and
`MSG_DONE` completes the line. Stripping that trailing space — or adding a newline — breaks the
pairing visually, and nothing tests the join. ⚑ In one-time setup both go to **stderr**, not
stdout, so a progress notice never contaminates parseable output.

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
WARN_SA_SHADOWED_BY_PATH # (name, the path it resolved to, the shadowed box's root)
WARN_WS_BOX_BAD_NAME   # (box name, the REASON it fails)
WARN_BOX_BAD_KUID      # (the KUID, box name)
WARN_BOX_KUID_HELD     # (kuid, root file, holder box, holder root)
NOTE_BOX_NO_VAULT      # (box name, expected vault path)
WARN_BOX_VAULT_UNUSABLE # ("rw" | "ro", box name, vault path, the finding)
```
Conditions that DEGRADE a box rather than stop it.

⚑ **The three box advisories say the box still works, and name a cure.** `WARN_WS_BOX_BAD_NAME`,
`WARN_BOX_BAD_KUID` and `WARN_BOX_NO_VAULT` each name a defect, state that resolution or launch
proceeds anyway, and say how to clear it (*"Rename it."* · *"fix workset.kuid or set
workset.skip_kuid_check=true to silence this"* · *"restore the old one first, or set
box.enable_vault=false to silence"*); they go through the logger. A warning a user can neither act
on nor silence becomes noise on every launch — so a NEW advisory owes a cure clause too.
`WARN_BOX_VAULT_UNUSABLE` and `WARN_SA_SHADOWED_BY_PATH` carry one as well; `WARN_BOX_KUID_HELD`
reports that the box gets a new kuid and needs none. The two workset warnings, `WARN_WS_NO_ROOT`
and `WARN_WS_BAD_LOAD`, state the failure plainly (a missing root, a load exception) with no cure,
and go to stderr via `print`.

⚑ `WARN_BOX_BAD_KUID` takes the KUID FIRST and the box name second, while `WARN_WS_BOX_BAD_NAME`
and `WARN_BOX_NO_VAULT` lead with the box name. Two `%s` of the same type, so a swap renders a
plausible-looking wrong message and nothing catches it.

⚑ `WARN_WS_BOX_BAD_NAME`'s second argument is the reason string from
`launch.box_identity.box_name_reason`, not the rules themselves. The name is WARNED about, never
rejected: `validate_box_name` enforces the same box-name rule on NEW names (creation and `--name`),
while a name already on disk only ever gets this advisory (`_flag_nonconforming`). That asymmetry is
the point — tightening the rule must not strand existing boxes.

⚑ `WARN_BOX_NO_VAULT`'s second argument is specifically the **RW** vault path, though the message
says "vault" unqualified; and its first falls back to the project PATH when the box has no name.

### Errors — settings and config

```python
ERR_SETTINGS_BAD_PATH       # ("config" | "system", key)
ERR_SETTINGS_BAD_REF        # ("" | "config ", ref)
ERR_CONFIG_NO_FILE          # (config file path)
ERR_CONFIG_LAYER1_SETTINGS  # (the Layer-1 file path, the offending keys)
ERR_CONFIG_LAYER1_UNDECLARED # (the Layer-1 file path, the offending keys, the declared set)
ERR_CONFIG_LAYER1_TABLE     # (the Layer-1 file path, the offending value)
ERR_CONFIG_NULL_PATH_REASON # the shared reason clause
ERR_CONFIG_NULL_PATH_HEAD   # (the file path, the offending keys) — the read-time lead
ERR_CONFIG_NULL_PATH_CURE   # REASON + the read-time cure
ERR_CONFIG_NULL_PATH_SET_HEAD # (the offending keys) — the set door's lead
ERR_CONFIG_NULL_PATH        # HEAD + CURE: (the file path, the offending keys)
```
Unresolvable path-tier keys and refs, plus the Layer-1 file's own contract.
A `null` path key is refused, never read as the text `None`. The two path layers (`config.*` and
the `system.*` path tier) and the `set` door build that refusal through
`config.null_path_keys_error`, with membership from `config.refuses_null_path_key`. The composite
`ERR_CONFIG_NULL_PATH` is raised by a null `workset.boxes`
(`project.workset.resolve_workset_boxes`).

⚑⚑ **THE THREE LAYER-1 REFUSALS ARE ONE RULE FROM THREE SIDES** (spec §1: *"The Layer-1 set is
exactly the config keys in the table below"*). `…_SETTINGS` catches a table OUTSIDE `config:`;
`…_UNDECLARED` catches a leaf INSIDE it that names no config key; `…_TABLE` catches a `config:`
that carries a VALUE instead of a table. Without the middle one, `config.nonsense` would be
accepted in silence and dropped unread by `resolve_config_paths` while a bare `nonsense` is
refused. ⚑ `…_UNDECLARED`'s third argument is the DECLARED set itself, enumerated
from `bootstrap.CONFIG_PATH_DEFAULTS` at the raise site (P13), so a key joining §1 joins the
message.

⚑ **`ERR_CONFIG_LAYER1_SETTINGS`'s CURE IS ORDERED, and the order is load-bearing.** Every verb
resolves its paths through `config.bootstrap_config_paths`, `system set` included, so a message that
led with the command would send the user to a command that refuses for this same reason. It leads
with the hand-edit: delete the lines, then set what you meant. Its second argument is the offending
keys, dotted and sorted, one per line — the file itself is the first.

⚑ **The first argument of each is a LAYER DISCRIMINATOR, not a value.** `ERR_SETTINGS_BAD_PATH`'s is
literally `"config"` or `"system"`, naming which of the two layers failed. `ERR_SETTINGS_BAD_REF`'s is
`""` or `"config "` (trailing space), spliced before the word — `"Unknown %sreference: {%s}"` renders
`Unknown reference: {a.b}` or `Unknown config reference: {config.x}`. The empty string and the space
are load-bearing; a bare scope name there would produce `Unknown systemreference` for a ref that is not
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
ERR_WORKSET_NO_PROJECT  # (project name, workset name)
ERR_WORKSET_NO_WORKSET  # (project dir)
ERR_WORKSET_WS_NOT_BOX  # (name, name — the SAME value twice)
ERR_WORKSET_NOT_IN_BOX  # (workset name, workspaces dir — "<None>" under a null)
ERR_WORKSET_NULL_WORKSPACES  # (the workset.yaml path, what was refused)
ERR_STANDALONE_NULL_WORKSPACES  # (the workset.yaml path, what was refused)
ERR_NULL_WORKSPACE_BIND  # (the box label, the workset.yaml path)
```
Refusals and not-found errors for the project/workset layer. `ERR_WORKSET_NULL_WORKSPACES` is
`project.workset.refuse_null_workspaces`'s text: a null `workset.workspaces` HAS a meaning (no
workspace dir, spec §2c), so unlike `ERR_CONFIG_NULL_PATH` it names what cannot be created there.
`ERR_STANDALONE_NULL_WORKSPACES` is its standalone face, without the "connect a directory
outside it" cure. `ERR_NULL_WORKSPACE_BIND` is the LAUNCH face (`refuse_null_box_workspace`, Q106):
the workspace bind is mounted at every launch, so a box whose workspace resolves through the null
cannot run.

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
skip the `.shell.d` chain entirely. It first restores `$HOME/.local/bin` on `PATH` when missing,
because a login shell's `/etc/profile` drops it, and the agent binary lives there.

⚑ `BASHRC_CONTENTS`' prompt is `"${KANIBAKO_PS1:-$_ps1}"` — a parameter-expansion DEFAULT, not an
assignment, so a `KANIBAKO_PS1` supplied through `env.KANIBAKO_PS1` wins and the box-marker prompt
`(kanibako) \u@\h:\w$ ` is only the fallback, colored in an interactive shell on a terminal of 8+
colors. The backslashes are doubled in the Python source because it is a regular string, not a raw one.

⚑ **`bootstrap.SHELL_D_FILE`'s value is an UPGRADE trigger, not just a name.**
`paths._upgrade_shell` decides whether an existing box's `.bashrc` already has the seam by testing
for the substring `SHELL_D_FILE + "/"` — i.e. `.shell.d/` — and its own comment at that line
explains why the trailing slash is load-bearing. So renaming the DIRECTORY makes the test miss on
every box already on disk and appends a second sourcing block to each of their `.bashrc` files;
rewording the rest of `_SHELL_D_SOURCE_LINE`, or the `"# Source user init scripts"` header the two
contents constants share, changes nothing about detection.
