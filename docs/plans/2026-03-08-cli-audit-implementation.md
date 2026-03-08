# CLI Audit (#47) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure kanibako's entire CLI surface for 1.0 — five management commands
(box/image/workset/agent/system), six top-level aliases, unified config interface,
terminology rename, and deleted/merged commands.

**Architecture:** Bottom-up rewrite of the CLI layer. Core business logic (paths.py,
config.py, names.py, container.py, workset.py, snapshots.py, helpers.py) stays largely
intact. The CLI surface (cli.py, commands/*) is restructured around five noun-based
management commands with consistent subcommand patterns (create/list/info/rm/config).

**Tech Stack:** Python 3.11+, argparse, TOML (tomllib/tomli_w), pytest, mypy, ruff

**Design doc:** `docs/plans/2026-03-08-cli-audit-design.md` — READ THIS for all decisions.

---

## Micro-Phase Protocol

Each phase follows the auto-loop pattern:
1. Read plan + devnotes to orient
2. Implement changes (read → edit → test)
3. Run full test suite: `~/.venv/bin/pytest tests/ -x -q`
4. Run type check: `~/.venv/bin/mypy src/kanibako/ --ignore-missing-imports`
5. Run lint: `~/.venv/bin/ruff check src/ tests/`
6. Update this plan file (mark phase done, note any deviations)
7. Update devnotes
8. Commit with `Co-Authored-By: Kirobo <kirobo@bmail.club>`
9. Check context — if <90%, continue to next phase; if ≥90%, stop

**Test strategy:** Each phase must end with ALL tests passing. Update/add/remove tests
as part of the phase, not in a separate pass. If a phase restructures a command, the
old tests for that command are updated in the same commit.

**Subagent usage:** Use subagents liberally for parallelizable work:
- **Explore agents** for codebase research (finding references, understanding patterns)
- **Worktree-isolated agents** for independent phases when no dependencies exist
- **Code review agents** after each phase to catch issues before committing
- Within a phase, dispatch parallel subagents for independent file edits or test updates
  when the changes don't interact

---

## Phase 1: Terminology Rename

**Status:** DONE

**Goal:** Rename internal project mode terminology:
- `account_centric` → `local`
- `decentralized` → `standalone`
- `working_set` / `workset` mode stays as `workset` (already correct)

**Files:**
- Modify: `src/kanibako/paths.py` — `ProjectMode` enum values (~line 27)
- Modify: `src/kanibako/config.py` — any string references to old mode names
- Modify: `src/kanibako/commands/start.py` — user-facing strings
- Modify: `src/kanibako/commands/init.py` — user-facing strings, `--local` help text
- Modify: `src/kanibako/commands/status.py` — mode display
- Modify: `src/kanibako/commands/box/_parser.py` — mode references
- Modify: `src/kanibako/commands/box/_migrate.py` — mode conversion logic
- Modify: `src/kanibako/commands/box/_duplicate.py` — mode references
- Modify: `src/kanibako/commands/connect.py` — mode references
- Modify: `src/kanibako/commands/archive.py` — mode references
- Modify: `src/kanibako/commands/restore.py` — mode references
- Modify: `src/kanibako/commands/workset_cmd.py` — mode references
- Modify: All test files referencing `ProjectMode.account_centric` or `ProjectMode.decentralized`

**Steps:**
1. Rename `ProjectMode.account_centric` → `ProjectMode.local`,
   `ProjectMode.decentralized` → `ProjectMode.standalone` in `paths.py`
2. Add backward compat in TOML reading: when loading `project.toml`, map old values
   (`"account_centric"` → `"local"`, `"decentralized"` → `"standalone"`) so existing
   projects keep working. Do this in `_load_project_toml()` or wherever mode is read.
3. Update all user-facing strings: "account-centric" → "local",
   "decentralized" → "standalone" in print/log messages
4. Update all `--local` flag help text (init.py currently says "decentralized mode")
   to say "standalone mode"
5. Search-and-replace `ProjectMode.account_centric` → `ProjectMode.local` and
   `ProjectMode.decentralized` → `ProjectMode.standalone` across ALL test files
6. Update `detect_project_mode()` return values
7. Run full test suite + mypy + ruff

**Acceptance:** All 1395 unit tests pass. Old project.toml files with
`mode = "account_centric"` still load correctly.

---

## Phase 2: Config Interface Engine

**Status:** DONE

**Goal:** Build a reusable config interface module that all management commands
(box/workset/agent/system) will share. This is the foundation for unified
`config` subcommands.

**Files:**
- Create: `src/kanibako/config_interface.py` (~200 lines)
- Create: `tests/test_config_interface.py` (~300 lines)

**Design (from design doc):**
- `key=value` syntax for set operations (argparse detects `=` in argument)
- Known-key heuristic for get disambiguation (known key → get; unknown → project name)
- `--effective` shows resolved values including inherited defaults
- `--reset <key>` removes override at this level
- `--reset --all` removes all overrides at this level (confirms)
- `--force` skips confirmation on reset
- `--local` on resource keys means project-isolated

**Config key registry:**
```python
KNOWN_CONFIG_KEYS = {
    "start_mode", "autonomous", "model", "persistence", "image", "auth",
    "vault.enabled", "vault.ro", "vault.rw",
}
# Plus dynamic prefixes: "resource.*", "env.*"
```

**Implementation:**
1. Define `KNOWN_CONFIG_KEYS` set and `DYNAMIC_PREFIXES` (`resource.`, `env.`)
2. Write `is_known_key(arg)` — returns True if arg matches a known key or dynamic prefix
3. Write `parse_config_args(args)` → `ConfigAction` (get/set/show/reset)
   - If arg contains `=`: set operation
   - If arg matches known key: get operation
   - Otherwise: treat as project name
4. Write `get_config_value(level, key)` — reads from appropriate TOML (project/workset/agent/system)
5. Write `set_config_value(level, key, value)` — writes to appropriate TOML
6. Write `reset_config_value(level, key)` — removes override
7. Write `show_config(level, effective=False)` — displays all keys at this level
8. Write `add_config_parser(parent_parser)` — adds config subcommand with correct flags
9. Write tests: parse logic, get/set/reset, env.* keys, resource.* keys, effective mode

**Notes:**
- This module provides the engine; it does NOT register any CLI commands yet.
  Each management command's phase will wire this into their parser.
- `env.*` operations delegate to shellenv.py (read/write .env files)
- `resource.*` operations delegate to resource_overrides in project.toml
- Regular keys update the appropriate section of project.toml / kanibako.toml

**Acceptance:** New module exists with full test coverage. No CLI changes yet. All
existing tests still pass.

---

## Phase 3: `box create` (Replaces `init`)

**Status:** DONE

**Goal:** Add `box create` subcommand, remove top-level `init` command.

**Files:**
- Modify: `src/kanibako/commands/box/_parser.py` — add `create` subcommand
- Modify: `src/kanibako/cli.py` — remove `init` from `_SUBCOMMANDS`, remove import
- Delete: `src/kanibako/commands/init.py` (move logic to box)
- Modify: `tests/test_init_cmd.py` → `tests/test_commands/test_box_create.py`

**New CLI:**
```
kanibako box create [path] [--name NAME] [--standalone] [--image IMAGE]
                    [--no-vault] [--distinct-auth]
```

**Changes from old `init`:**
- `--local` renamed to `--standalone` (terminology rename)
- `--name` added (override auto-derived project name from path basename)
- `-i`/`--image` changes to `--image` (long only, no short form)
- Moved under `box` subcommand
- `_write_project_gitignore()` helper moves to box package or stays importable

**Steps:**
1. Add `_add_create_parser()` to `box/_parser.py` with new flags
2. Add `_run_create()` function implementing the create logic (moved from init.py)
3. Wire into box subcommands
4. Remove `add_init_parser` and `run_init` references from `cli.py`
5. Delete `commands/init.py` (but keep `_write_project_gitignore` accessible — move
   it to a shared location like `utils.py` or the box package `__init__.py` since
   `_duplicate.py` and `_migrate.py` import it)
6. Rename/update `test_init_cmd.py` → test `box create` instead
7. Update any other test files that invoke `init` (e.g., integration tests)
8. Run full suite + mypy + ruff

**Acceptance:** `kanibako box create` works. `kanibako init` no longer exists. All
tests pass.

---

## Phase 4: `box list` + `box info` + `box rm`

**Status:** DONE

**Goal:** Restructure box lifecycle commands: unified `list`, merged `info`, merged `rm`.

**Files:**
- Modify: `src/kanibako/commands/box/_parser.py` — rewrite `list`, add `info`, rewrite `rm`
- Modify: `src/kanibako/cli.py` — remove `status` from `_SUBCOMMANDS`
- Delete: `src/kanibako/commands/status.py` (merged into `box info`)
- Modify: `tests/test_commands/test_box.py` — update list/forget tests
- Modify: `tests/test_status.py` → update to test `box info`
- Modify: `tests/test_box_commands.py` — update for rm

**New CLI:**
```
kanibako box list [--all] [--orphan] [-q/--quiet]
kanibako box info [project]                         # aliases: inspect
kanibako box rm <project> [--purge] [--force]       # aliases: delete
```

**Changes:**
- `box list`: Add `--all` (includes orphans), `--orphan` (only orphans), `-q/--quiet`
  (names only, one per line). Default: healthy projects only. Absorbs old `box orphan`.
- `box info`: Merge old `status` output + old `box info` output into one view.
  Takes `[project]` positional (not `-p`). Show: name, mode, paths, image, container
  status, credentials age, vault status, config overrides.
- `box rm`: Rename from `forget`. `box rm <project>` unregisters. `--purge` also
  deletes metadata. `--force` skips confirmation on purge. Hint in output:
  "Metadata still present at /path. Run `kanibako box rm <project> --purge` to delete."
- Remove: `box orphan` (absorbed into `list --orphan`), `box forget` (renamed to `rm`),
  top-level `status` (merged into `box info`)

**Steps:**
1. Rewrite `_add_list_parser()` with new flags. Update `run_list()` to support
   `--all`, `--orphan`, `-q`. Keep old orphan detection logic.
2. Add `_add_info_parser()` with `[project]` positional. Implement `_run_info()`
   combining logic from `status.py:run_status()` and old `box/_parser.py:run_info()`.
   Add `inspect` alias.
3. Rename forget → rm. Add `_add_rm_parser()`. Reuse forget/purge logic.
   Add `delete` alias.
4. Remove `_add_orphan_parser()` and `run_orphan()`.
5. Remove `_add_forget_parser()` and `run_forget()`.
6. Remove `status` command from cli.py.
7. Update tests: box list tests, status tests (→ box info), box forget tests (→ box rm).
8. Run full suite + mypy + ruff

**Acceptance:** `box list`, `box info`, `box rm` work as designed. Old `status`,
`box orphan`, `box forget` removed. All tests pass.

---

## Phase 5: `box config`

**Status:** NOT STARTED

**Goal:** Unified `box config` subcommand replacing box get/set, box settings,
box resource, top-level config, and top-level env commands.

**Files:**
- Modify: `src/kanibako/commands/box/_parser.py` — add `config` subcommand
- Modify: `src/kanibako/config_interface.py` — wire into box-level operations
- Modify: `src/kanibako/cli.py` — remove `config`, `env`, `shared` from `_SUBCOMMANDS`
- Delete: `src/kanibako/commands/config_cmd.py`
- Delete: `src/kanibako/commands/env_cmd.py`
- Delete: `src/kanibako/commands/shared_cmd.py`
- Modify: `tests/test_commands/test_config_cmd.py` → rewrite for `box config`
- Modify: `tests/test_env_cmd.py` → merge into box config tests
- Modify: `tests/test_commands/test_shared.py` → merge into box config tests

**New CLI:**
```
kanibako box config [project]                    # show overrides
kanibako box config [project] --effective        # show resolved
kanibako box config [project] <key>              # get value
kanibako box config [project] <key>=<value>      # set value
kanibako box config [project] <key> --local      # isolate resource
kanibako box config [project] --reset <key>      # reset one key
kanibako box config [project] --reset --all      # reset all (confirms)
```

**Changes:**
- Remove: `box get/set`, `box settings list/get/set/unset`, `box resource list/set/unset`
- Remove: top-level `config` command (config_cmd.py)
- Remove: top-level `env` command (env_cmd.py)
- Remove: top-level `shared` command (shared_cmd.py)
- `env.*` keys: `box config env.MY_VAR=value` → writes to project .env file
  (delegates to shellenv.py). `box config env.MY_VAR` → reads from merged env.
- `resource.*` keys: `box config resource.plugins=/path` → writes to
  `[resource_overrides]` in project.toml. `--local` → sets to project-isolated.
- Regular keys: `model`, `start_mode`, `autonomous`, `persistence`, `image`,
  `auth`, `vault.enabled`, `vault.ro`, `vault.rw` → stored in project.toml
  sections (`[project]`, `[container]`, `[target_settings]`).

**Steps:**
1. Add `_add_config_parser()` to box/_parser.py. Use argparse with `[project]`
   positional, `key_value` positional (optional), `--effective`, `--reset`,
   `--all`, `--force`, `--local` flags.
2. Implement `_run_config()` using config_interface.py engine.
   - Route `env.*` to shellenv operations
   - Route `resource.*` to resource_overrides operations
   - Route other keys to project.toml sections
3. Remove old subcommands: `get`, `set`, `settings`, `resource` from box parser.
4. Remove config_cmd.py, env_cmd.py, shared_cmd.py. Update cli.py.
5. Update tests. Rewrite config_cmd tests for new syntax. Merge env and shared tests.
6. Run full suite + mypy + ruff

**Acceptance:** `box config` provides a unified interface for all project-level
settings. All old config/env/shared commands removed. Tests pass.

---

## Phase 6: `box start` + Agent Flags + Merge Resume

**Status:** NOT STARTED

**Goal:** Restructure `start` command under `box start`, add full agent flag set,
merge `resume` into `start -R`.

**Files:**
- Modify: `src/kanibako/commands/start.py` — rewrite parser, update `_run_container()`
- Modify: `src/kanibako/commands/box/_parser.py` — register `start` as box subcommand
- Modify: `src/kanibako/cli.py` — remove `resume` from `_SUBCOMMANDS`
- Delete: resume-related parser/function from start.py
- Modify: `tests/test_commands/test_start.py` — update flag tests
- Modify: `tests/test_commands/test_start_extended.py` — update
- Modify: `tests/test_cli.py` — update for removed resume

**New CLI:**
```
kanibako box start [project] [-N/--new] [-C/--continue] [-R/--resume]
                   [-M/--model MODEL] [-A/--autonomous] [-S/--secure]
                   [-e/--env KEY=VALUE] [--image IMAGE] [--entrypoint CMD]
                   [--persistent] [--ephemeral] [--no-helpers]
                   [-- agent_args...]
```

**Changes:**
- `[project]` positional replaces `-p/--project`
- `-C/--continue` (new, explicit flag for continue mode — was the implicit default)
- `-R/--resume` (new, replaces separate `resume` command)
- `-M/--model` (new, model override)
- `-A/--autonomous` (replaces the implicit default, explicit flag)
- `-S/--secure` (renamed from `--safe`, short flag stays uppercase)
- `-N/-C/-R` mutually exclusive group
- `-A/-S` mutually exclusive group
- `--entrypoint` replaces `-c/--command` and `-E/--entrypoint` (visible, long only)
- `--image` replaces `-i/--image` (long only)
- `--persistent`/`--ephemeral` (new, session persistence mode)
- `-e/--env KEY=VALUE` (new, per-run env var, repeatable)
- Remove `resume` command entirely (→ `start -R`)

**Steps:**
1. Rewrite `add_start_parser()`: new flag names, mutually exclusive groups,
   `[project]` positional, `-- args` REMAINDER.
2. Update `_run_container()` to handle new flag names:
   - Map `-N/-C/-R` to internal mode: `args.start_mode` ∈ {new, continue, resume}
   - Map `-M` to model override
   - Map `-A/-S` to autonomous/safe mode
   - Map `--entrypoint` (replaces old `-c`)
   - Map `--persistent/--ephemeral` (default from config)
   - Map `-e` repeatable to extra env vars
3. Remove `add_resume_parser()` and `run_resume()`.
4. Register `start` as a box subcommand in `_parser.py` (delegates to start.py functions).
5. Remove `resume` from cli.py `_SUBCOMMANDS`.
6. Update all start/resume tests for new flag names.
7. Run full suite + mypy + ruff

**Acceptance:** `box start` works with new flag set. `resume` command removed.
`start -R` replaces old `resume`. All tests pass.

---

## Phase 7: Merge `connect` + Session Persistence

**Status:** NOT STARTED

**Goal:** Merge `connect` command behavior into `box start` with tmux-based
session persistence as default. Remove `connect` command.

**Files:**
- Modify: `src/kanibako/commands/start.py` — tmux session handling
- Modify: `src/kanibako/cli.py` — remove `connect` from `_SUBCOMMANDS`
- Delete: `src/kanibako/commands/connect.py`
- Modify: `tests/test_commands/test_connect.py` → rewrite as start persistence tests

**Changes:**
- `start` defaults to `--persistent` (tmux-wrapped session):
  - If tmux available: wrap container in tmux session, detach Ctrl-B d, reattach on
    subsequent `start` of same project
  - If tmux not installed: warn and fall back to ephemeral
  - `--ephemeral` flag for no tmux (direct foreground run, current behavior)
- Reattach logic: if container running and tmux session exists, attach to it.
  If container running but no tmux session, error with guidance.
- `connect --list` functionality moves to `box ps` (Phase 8)
- Remove `connect` command entirely

**Steps:**
1. Add tmux session detection to `_run_container()`:
   - Check if tmux is installed (`shutil.which("tmux")`)
   - Default persistent=True when tmux available, False otherwise
   - `--persistent` / `--ephemeral` override the default
2. Implement tmux session management:
   - `_tmux_session_name(project_name)` → deterministic session name
   - Start: `tmux new-session -d -s NAME -- kanibako-run ...`
   - Attach: `tmux attach-session -t NAME`
   - Detect existing: `tmux has-session -t NAME`
3. Move reattach logic from connect.py into start's persistent path.
4. Remove connect.py and its CLI registration.
5. Update tests: remove connect tests, add persistence/tmux tests.
6. Run full suite + mypy + ruff

**Acceptance:** `start` defaults to tmux-persistent sessions. `connect` removed.
Reattach works via `start`. All tests pass.

---

## Phase 8: `box stop` + `box shell` + `box ps`

**Status:** NOT STARTED

**Goal:** Restructure stop/shell under box, add new `box ps` command.

**Files:**
- Modify: `src/kanibako/commands/stop.py` — `[project]` positional, `--force` confirm
- Modify: `src/kanibako/commands/start.py` — shell parser updates
- Modify: `src/kanibako/commands/box/_parser.py` — register stop/shell/ps
- Modify: `tests/test_commands/test_stop.py` — update for new flags
- New tests for `box ps`

**New CLI:**
```
kanibako box stop [project] [--all] [--force]
kanibako box shell [project] [-e/--env KEY=VALUE] [--image IMAGE]
                   [--entrypoint CMD] [--persistent] [--ephemeral]
                   [--no-helpers] [-- cmd...]
kanibako box ps [--all] [-q/--quiet]
```

**Changes:**
- `stop`: `[project]` positional (replaces path positional). `--all` now confirms
  unless `--force`.
- `shell`: Same infrastructure flags as `start` (no agent flags). `[project]` positional
  replaces `-p`. `--entrypoint` replaces `-c`.
- `ps` (new): List running projects with status. `--all` includes stopped.
  `-q` outputs names only. Pulls from container runtime listing +
  names.toml cross-reference.

**Steps:**
1. Update `stop.py`: `[project]` positional, add `--force`, add confirmation on `--all`.
2. Update shell parser in `start.py`: `[project]` positional, `--entrypoint`, `-e`, etc.
3. Add `_add_ps_parser()` and `_run_ps()` in box/_parser.py:
   - List all kanibako containers via `runtime.list_running()`
   - Cross-reference with names.toml for project names
   - `--all`: include stopped containers
   - `-q`: names only, one per line
4. Register stop, shell, ps as box subcommands.
5. Update tests.
6. Run full suite + mypy + ruff

**Acceptance:** `box stop`, `box shell`, `box ps` work. All tests pass.

---

## Phase 9: `box` Relocation + Vault

**Status:** NOT STARTED

**Goal:** Restructure relocation commands (move/duplicate/archive/extract) and
nest vault under `box vault`.

**Files:**
- Modify: `src/kanibako/commands/box/_parser.py` — add `move`, restructure archive/extract
- Modify: `src/kanibako/commands/box/_duplicate.py` — flag updates
- Modify: `src/kanibako/commands/archive.py` — flag updates (`--as-local`, `--as-standalone`)
- Modify: `src/kanibako/commands/restore.py` → becomes `extract` logic
- Modify: `src/kanibako/commands/vault_cmd.py` — nest under box
- Modify: `src/kanibako/cli.py` — remove top-level `vault`
- Modify: relevant test files

**New CLI:**
```
kanibako box move [project] <dest>
kanibako box duplicate <source> [dest] [--name NAME] [--bare] [--force]
kanibako box archive [project] [--as-local] [--as-standalone] [--force]
kanibako box extract <archive> [path] [--name NAME] [--force]
kanibako box vault snapshot [project]
kanibako box vault list [project] [-q/--quiet]
kanibako box vault restore <name> [project] [--force]
kanibako box vault prune [project] [--keep N] [--force]
```

**Changes:**
- `box move` (new): relocate project workspace. Error if project is inside a workset
  (use workset-level operations). Update names.toml path, recreate vault symlinks.
- `archive`: `--as-ac`/`--as-decentralized` → `--as-local`/`--as-standalone`. Add `--force`
  (overwrite existing archive).
- `restore` → `extract`: rename for clarity. `[project]` becomes `[path]`.
  Add `--name` for override. Add `--force` to skip confirmation.
- `vault`: move from top-level to `box vault`. Add `-q/--quiet` to `vault list`.
  `[project]` positional replaces `-p`.
- `duplicate`: Update flags. `[project]` positional pattern.
- Remove: top-level `vault` command. Remove `box purge` (merged into `box rm --purge`
  in Phase 4). Remove `box restore` (becomes `box extract`).

**Steps:**
1. Implement `box move`: resolve project, validate not in workset, move workspace dir,
   update names.toml, update project.toml paths, recreate vault symlinks.
2. Rename restore → extract in parser registration. Update flag names.
3. Update archive flags for new terminology.
4. Update duplicate flags for `[project]` positional.
5. Nest vault_cmd under `box vault`. Change `-p` to `[project]` positional.
   Add `-q` to vault list. Remove from cli.py top-level.
6. Wire all into box help text groups (Relocation, Data).
7. Update tests.
8. Run full suite + mypy + ruff

**Acceptance:** All box relocation and vault commands work under new structure.
Top-level `vault` removed. All tests pass.

---

## Phase 10: `image` Restructure

**Status:** NOT STARTED

**Goal:** Restructure image command: absorb template, add create/info/rm.

**Files:**
- Modify: `src/kanibako/commands/image.py` — add create/info/rm, restructure list/rebuild
- Modify: `src/kanibako/cli.py` — remove `template` from `_SUBCOMMANDS`
- Delete: `src/kanibako/commands/template_cmd.py` (absorbed into image)
- Modify: `tests/test_commands/test_image.py` — update
- Modify: `tests/test_commands/test_template_cmd.py` → merge into image tests

**New CLI:**
```
kanibako image create <name> [--base IMAGE] [--always-commit] [--no-commit-on-error]
kanibako image list [-q/--quiet]
kanibako image info <image>                          # aliases: inspect
kanibako image rm <image> [--force]                  # aliases: delete
kanibako image rebuild [image] [--all]
```

**Changes:**
- `image create` absorbs `template create` logic. Same workflow: run interactive
  container from base, commit on exit. Image name: `kanibako-template-<name>`.
- `image list`: Remove `-p` flag. Show all images (built-in variants + local templates
  + remote registry). Add `-q/--quiet` (image names only).
- `image info` (new): Show image details — source (registry/local/Containerfile),
  size, creation date, recoverability status.
- `image rm` (new): Delete local image. Confirm with recoverability context
  ("Registry-backed: recoverable via rebuild" vs "Local only: cannot be recovered").
  `--force` skips confirmation.
- `image rebuild`: Remove `--local` flag (auto-detect from image metadata).
- Remove: top-level `template` command entirely.

**Steps:**
1. Move template create logic from template_cmd.py into image.py as `_run_create()`.
2. Add `_add_info_parser()` and `_run_info()` — query image metadata via container runtime.
3. Add `_add_rm_parser()` and `_run_rm()` — `runtime.remove_image()` with confirmation.
4. Update `_add_list_parser()` — remove `-p`, add `-q/--quiet`.
5. Update `_add_rebuild_parser()` — remove `--local` (auto-detect via `_IMAGE_BASE_MAP`).
6. Add aliases: `inspect` for `info`, `delete` for `rm`.
7. Remove template_cmd.py and its CLI registration.
8. Update tests.
9. Run full suite + mypy + ruff

**Acceptance:** `image create/list/info/rm/rebuild` work. `template` removed. Tests pass.

---

## Phase 11: `workset` Restructure

**Status:** NOT STARTED

**Goal:** Restructure workset command with docker-aligned naming and config support.

**Files:**
- Modify: `src/kanibako/commands/workset_cmd.py` — rename subcommands, add config
- Modify: `tests/test_commands/test_workset_cmd.py` — update

**New CLI:**
```
kanibako workset create [path] [--name NAME] [--standalone] [--image IMAGE]
                        [--no-vault] [--distinct-auth]
kanibako workset list [-q/--quiet]                    # aliases: ls
kanibako workset info <workset>                       # aliases: inspect
kanibako workset rm <workset> [--purge] [--force]     # aliases: delete
kanibako workset config <workset> [<key>[=<value>]] [--effective] [--reset]
                        [--all] [--force] [--local]
kanibako workset connect <workset> [source] [--name NAME]
kanibako workset disconnect <workset> <project> [--force]
```

**Changes:**
- `create`: path-primary pattern (was `name path`). `--name` for override.
  Add `--standalone`, `--image`, `--no-vault`, `--distinct-auth` flags.
- `list`: Add `-q/--quiet`, `ls` alias.
- `info`: Add `inspect` alias. Replaces old `workset info`.
- `rm`: Rename from `delete`. Errors if workset has projects. `--purge` for files.
  `--force` skips confirmation. Add `delete` alias.
- `config` (new): Wire config_interface.py. Replaces `workset auth`.
  Supports same key set as box config (acts as defaults for member projects).
  Agent-namespaced keys: `workset config myws claude.model=sonnet`.
- `connect`: Rename from `add`. Same behavior.
- `disconnect`: Rename from `remove`. Converts project to local mode. Confirms.

**Steps:**
1. Rename `add` parser/function → `connect`.
2. Rename `remove` parser/function → `disconnect`.
3. Rename `delete` parser/function → `rm`. Add `delete` alias.
4. Update `create` to path-primary pattern with `--name` and additional flags.
5. Add `config` subcommand using config_interface.py. Handle agent-namespaced keys.
6. Add `-q/--quiet` to `list`. Add `ls`, `inspect` aliases.
7. Remove `auth` subcommand (absorbed into `config`).
8. Update tests.
9. Run full suite + mypy + ruff

**Acceptance:** `workset` commands work with new naming. Config support added.
`auth` command removed. All tests pass.

---

## Phase 12: `agent` Command (New)

**Status:** NOT STARTED

**Goal:** Create new `agent` management command. Move reauth, helper, fork under it.

**Files:**
- Create: `src/kanibako/commands/agent_cmd.py` (~250 lines)
- Modify: `src/kanibako/commands/helper_cmd.py` — nest under agent
- Modify: `src/kanibako/commands/fork_cmd.py` — nest under agent
- Modify: `src/kanibako/commands/refresh_credentials.py` — nest under agent
- Modify: `src/kanibako/cli.py` — register `agent`, remove `reauth`/`helper`/`fork`
- Create: `tests/test_commands/test_agent_cmd.py`
- Modify: `tests/test_helper_cmd.py` — update invocation paths
- Modify: `tests/test_fork.py` — update invocation paths

**New CLI:**
```
kanibako agent list [-q/--quiet]                    # aliases: ls
kanibako agent info <agent>                         # aliases: inspect
kanibako agent config <key>[=<value>] [--effective] [--reset] [--all] [--force]
kanibako agent reauth [project]
kanibako agent helper spawn [--depth N] [--breadth N] [--model M] [--image I]
kanibako agent helper list [-q/--quiet]             # aliases: ls
kanibako agent helper stop <N>
kanibako agent helper cleanup <N> [--cascade]
kanibako agent helper respawn <N>
kanibako agent helper send <N> <message>
kanibako agent helper broadcast <message>
kanibako agent helper log [-f] [--from N] [--tail N]
kanibako agent fork <name>
```

**Changes:**
- `agent list`: List configured agents from `{data_path}/agents/`. `-q` for names only.
- `agent info`: Agent details from TOML (name, shell variant, default args, state, env).
- `agent config`: Wire config_interface.py at agent level. Sets defaults inherited by projects.
- `reauth`: Move from top-level. `[project]` positional replaces `-p`. Error if project
  given but auth is shared.
- `helper`: Move from top-level. `helper log --tail N` replaces old `--last N`.
  `helper list` gets `-q/--quiet`.
- `fork`: Move from top-level. No flag changes.
- Remove: top-level `reauth`, `helper`, `fork` commands.
- Keep `helper` and `fork` exempt from config check (they run inside containers).

**Steps:**
1. Create `agent_cmd.py` with `add_parser(subparsers)`.
2. Implement `agent list`, `agent info` reading from agents dir.
3. Wire `agent config` using config_interface.py at agent level.
4. Nest helper_cmd under `agent helper` (import and delegate).
5. Nest fork_cmd under `agent fork` (import and delegate).
6. Move reauth logic under `agent reauth`. Update `[project]` positional.
7. Update cli.py: register `agent`, remove `reauth`/`helper`/`fork`.
8. Keep `agent helper` and `agent fork` exempt from config check.
9. Update helper/fork/reauth tests for new command paths.
10. Add tests for agent list/info/config.
11. Run full suite + mypy + ruff

**Acceptance:** `agent` command tree works. Old top-level commands removed. Tests pass.

---

## Phase 13: `system` Command + Lazy Init

**Status:** NOT STARTED

**Goal:** Create new `system` management command. Replace `setup`/`remove` with
lazy init and `system config --reset`.

**Files:**
- Create: `src/kanibako/commands/system_cmd.py` (~200 lines)
- Modify: `src/kanibako/cli.py` — register `system`, remove `setup`/`remove`
- Delete: `src/kanibako/commands/install.py` (setup → lazy init)
- Delete: `src/kanibako/commands/remove.py` (→ system config --reset --all)
- Modify: `src/kanibako/commands/upgrade.py` — nest under system
- Modify: `tests/test_commands/test_install.py` → rewrite as lazy init tests
- Modify: `tests/test_commands/test_remove.py` → rewrite as system config tests
- Modify: `tests/test_commands/test_upgrade.py` → update paths

**New CLI:**
```
kanibako system info                               # aliases: inspect
kanibako system config [<key>[=<value>]] [--effective] [--reset] [--all] [--force]
kanibako system upgrade [--check]
```

**Changes:**
- `system info`: Show version, install method, config path, data path, container
  runtime (type + version), python version.
- `system config`: Wire config_interface.py at system level. Replaces top-level `config`.
  Supports all non-agent project keys as global defaults + system-only keys
  (`data_path`, `paths_vault`, `paths_comms`, `target_name`, shared caches).
  Agent-namespaced keys: `system config claude.model=opus`.
  `--reset --all` replaces old `remove` command functionality.
- `system upgrade`: Move from top-level. No changes to logic.
- **Lazy init**: Instead of `setup` command, do lazy initialization on first run of any
  kanibako command that needs config (create data dirs, write default kanibako.toml).
  Implement as a check in `cli.py` main flow (or a `_ensure_initialized()` helper).
  Image pull deferred to first `start`/`shell` (don't pull on help/config commands).
- Remove: `setup`, `remove` commands.
- Remove setup/remove from config-check exempt list.

**Steps:**
1. Create `system_cmd.py` with `add_parser(subparsers)`.
2. Implement `system info` reading from config, runtime, package version.
3. Wire `system config` using config_interface.py at system level.
4. Move upgrade logic under `system upgrade`.
5. Implement `_ensure_initialized()` in cli.py or config.py:
   - Check if kanibako.toml exists
   - If not: create config file with defaults, create data directories, register
     shell completion (if argcomplete available)
   - Run before any command that needs config (skip for `system info`, `--help`)
6. Remove install.py, remove.py from codebase and cli.py.
7. Update tests.
8. Run full suite + mypy + ruff

**Acceptance:** `system` command tree works. Lazy init works on first run.
`setup` and `remove` removed. Tests pass.

---

## Phase 14: Top-Level Aliases + Final Cleanup + Docs

**Status:** NOT STARTED

**Goal:** Wire top-level aliases, clean up cli.py, update documentation.

**Files:**
- Modify: `src/kanibako/cli.py` — new `_SUBCOMMANDS`, top-level aliases, clean flow
- Modify: `src/kanibako/commands/box/_parser.py` — help text groups
- Modify: `README.md` — full command table update
- Modify: `docs/writing-targets.md` — if CLI examples changed
- Modify: `tests/test_cli.py` — update for new command structure
- Modify: `tests/test_cli_exitcodes.py` — update

**New top-level commands:**
```
kanibako start [project] ...        →  box start
kanibako stop [project] ...         →  box stop
kanibako shell [project] ...        →  box shell
kanibako ps ...                     →  box ps
kanibako create [path] ...          →  box create
kanibako rm <project> ...           →  box rm
kanibako box ...                    →  project management
kanibako image ...                  →  container images
kanibako workset ...                →  project grouping
kanibako agent ...                  →  agent operations
kanibako system ...                 →  global config + self-update
```

**Changes:**
- Top-level aliases: register `start`, `stop`, `shell`, `ps`, `create`, `rm` as
  top-level subparsers that delegate to `box` subcommand functions.
- Default command: if first arg is not a known subcommand, prepend `start` (keep
  current behavior: `kanibako /path/to/project` → `kanibako start /path/to/project`).
- `_SUBCOMMANDS` set: update to new command names only.
- `box` help text: organize into 4 groups (Run cycle, Standard lifecycle,
  Relocation, Data) using argparse group headers.
- Config-check exempt commands: update list for new structure (`agent helper`,
  `agent fork` run inside containers).
- README: complete command table rewrite. Examples updated.
- Remove all dead imports and references to deleted commands.

**Steps:**
1. Rewrite `_SUBCOMMANDS` in cli.py:
   ```python
   _SUBCOMMANDS = {
       "start", "stop", "shell", "ps", "create", "rm",
       "box", "image", "workset", "agent", "system",
   }
   ```
2. Register top-level aliases in `build_parser()`: each calls the box function directly.
3. Update default-command logic (prepend `start` if not a known command).
4. Add help text group headers to box parser.
5. Update config-check exempt list.
6. Full README rewrite: command table, examples, quick start.
7. Update docs/writing-targets.md if any CLI examples changed.
8. Final pass on all test files: search for any references to old command names.
9. Run full suite + mypy + ruff
10. Final commit.

**Acceptance:** Full CLI works as designed. All aliases functional. README current.
All tests pass. Ready for 1.0.

---

## Summary

| Phase | Description | Est. LOC changed |
|-------|-------------|-----------------|
| 1 | Terminology rename | ~200 |
| 2 | Config interface engine | ~500 (new) |
| 3 | `box create` | ~300 |
| 4 | `box list` + `info` + `rm` | ~400 |
| 5 | `box config` | ~600 |
| 6 | `box start` + agent flags | ~500 |
| 7 | Session persistence + connect merge | ~400 |
| 8 | `box stop` + `shell` + `ps` | ~300 |
| 9 | `box` relocation + vault | ~400 |
| 10 | `image` restructure | ~350 |
| 11 | `workset` restructure | ~350 |
| 12 | `agent` command | ~400 |
| 13 | `system` command + lazy init | ~400 |
| 14 | Top-level aliases + cleanup + docs | ~500 |
| **Total** | | **~5700** |

Estimated: 14 phases × ~1 context window each = 14 sessions.
