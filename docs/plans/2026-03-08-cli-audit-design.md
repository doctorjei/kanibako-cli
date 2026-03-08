# CLI Audit Design — kanibako 1.0

Date: 2026-03-08
Status: DESIGN COMPLETE — ready for implementation plan

## Terminology

Consistent naming for project modes throughout the CLI and docs:

| Internal name (code) | User-facing name | Meaning |
|---|---|---|
| `local` | local | Uses the user's home directory for shared state (credentials, config) |
| `standalone` | standalone | Everything self-contained in the project directory |
| `workset` | workset | Projects grouped under a shared root |

Old names (pre-1.0): "account-centric" / "AC" → local. "decentralized" → standalone. "working set" → workset.

## Design Principles

1. **Follow docker/podman patterns** where direct analogues exist
2. **1.0 is a clean slate** — rename freely, no backwards compatibility
3. **Hierarchy**: box → image → workset → agent → system (narrow to wide scope)
4. **Standard lifecycle commands** shared across all levels: `create`, `list`/`ls`, `info`/`inspect`, `rm`/`delete`, `config`
5. **Config precedence**: box (project) > workset > agent > system
6. **No `--global` flag** — use `system config` explicitly for global settings
7. **`ls` is an alias for `list`** everywhere (podman pattern)
8. **`inspect` is an alias for `info`** everywhere (docker pattern)
9. **`delete` is an alias for `rm`** everywhere

## Top-Level Aliases

Convenience shortcuts for common `box` operations (like `docker run` → `docker container run`):

| Alias | Maps to | Summary |
|---|---|---|
| `start` | `box start` | Launch agent session |
| `stop` | `box stop` | Stop container |
| `shell` | `box shell` | Open bash / run command |
| `ps` | `box ps` | List projects with status |
| `create` | `box create` | Create a project |
| `rm` | `box rm` | Remove a project |

## Management Commands (Nouns)

| Command | Summary |
|---|---|
| `box` | Project management |
| `workset` | Project grouping |
| `agent` | Agent operations |
| `image` | Container images |
| `system` | Global config + self-update |

---

## `box` — Project Management

### Flag Conventions

- **Uppercase short flags** = agent flags: `-N`, `-C`, `-R`, `-M`, `-A`, `-S`
- **Lowercase short flags** = infrastructure flags: `-e`
- **Long-only flags** for edge cases: `--persistent`, `--ephemeral`, `--entrypoint`, `--image`, `--no-helpers`
- **`[project]` positional** replaces old `-p`/`--project` flag (optional, defaults to cwd)

### Agent Flags (on `start`)

These are configurable as project defaults (via `box config`) and overridable per-run:

| Short | Long | Config key | Default | Summary |
|---|---|---|---|---|
| `-N` | `--new` | `start_mode` | `continue` | New session |
| `-C` | `--continue` | `start_mode` | `continue` | Continue session |
| `-R` | `--resume` | `start_mode` | `continue` | Resume/pick conversation |
| `-M` | `--model` | `model` | platform default | Model override |
| `-A` | `--autonomous` | `autonomous` | `true` | Enable autonomy override |
| `-S` | `--secure` | `autonomous` | `true` | Disable autonomy override |

`-N`, `-C`, `-R` are mutually exclusive. `-A`, `-S` are mutually exclusive.

### Infrastructure Flags (on `start` and `shell`)

| Short | Long | Summary |
|---|---|---|
| `-e` | `--env` | Per-run env var, `KEY=VALUE`, repeatable |
| | `--image` | Container image override (no short form; `-i` dropped to avoid docker confusion) |
| | `--entrypoint` | Override binary (replaces old `-c`/`--command` and `-E`/`--entrypoint`) |
| | `--persistent` | Use tmux session wrapper (config default) |
| | `--ephemeral` | No tmux, session dies with terminal |
| | `--no-helpers` | Disable helper spawning |

Notes:
- `start` has agent flags + infrastructure flags + `-- args` for agent passthrough
- `shell` has infrastructure flags + `-- cmd` for one-shot commands
- `start` and `shell` are variants — both launch containers, different default entrypoint
- `stop` stops either variant
- Session persistence (`persistent`/`ephemeral`) is a config setting, flags override per-run
- tmux is default; if tmux not installed, fall back to ephemeral with warning
- tmux status bar should show Ctrl-B detach hint

### Run Cycle Commands

| Subcommand | Arguments | Flags | Summary |
|---|---|---|---|
| `start` | `[project]` | Agent flags + infra flags + `-- args` | Launch agent session |
| `stop` | `[project]` | `--all`, `--force` | Stop container (`--all` stops all running, confirms unless `--force`) |
| `shell` | `[project]` | Infra flags + `-- cmd` | Open bash / run command |
| `ps` | | `--all`, `-q`/`--quiet` | List running projects (`--all` includes stopped) |

### Standard Lifecycle Commands

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `create` | `[path]`, `--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth` | New project (top-level alias: `create`) |
| `list` / `ls` | `--all`, `--orphan`, `-q`/`--quiet` | List projects (default: healthy only) |
| `info` / `inspect` | `[project]` | Detailed project view (merges old `status` + `box info`) |
| `rm` / `delete` | `<project>`, `--purge`, `--force` | Remove project (top-level alias: `rm`) |
| `config` | See config section below | Project configuration |

`box list` behavior:
- `box list` — healthy projects only (valid workspaces)
- `box list --all` — everything including orphans
- `box list --orphan` — only orphans

`box rm` behavior:
- `box rm <project>` — unregister from names.toml. Prints: "Metadata still present at /path. Run `kanibako box rm <project> --purge` to delete."
- `box rm <project> --purge` — unregister + delete metadata. Confirms unless `--force`.

### Config Interface

Unified config at every level (`box config`, `workset config`, `agent config`, `system config`).
Uses `key=value` syntax for set operations to keep the `[project]` positional unambiguous:

| Usage | Behavior |
|---|---|
| `config [project]` | Show overrides at this level |
| `config [project] --effective` | Show resolved values including inherited defaults |
| `config [project] <key>` | Show effective value of one key |
| `config [project] <key>=<value>` | Set value |
| `config [project] <key> --local` | Set resource to project-isolated (resource keys only) |
| `config [project] --reset <key>` | Remove override, back to default |

Disambiguation: if the first non-flag argument contains `=`, it's a set operation. Otherwise,
check against known config key names. If it matches a known key, it's a get. Otherwise it's
a project name. This allows `box config model` (get key from cwd project) and
`box config api-server model` (get key from named project) to coexist.

`--local` flag:
- Only applies to `resource.*` keys
- Mutually exclusive with providing a value
- Can appear before or after the key
- At box level: isolate this resource to this project (project-local)
- At workset/agent/system level: set the default to project-local for all projects inheriting this config

Resource config (replaces old `box resource` subcommand):
- `box config resource.plugins=/path/to/dir` — custom shared path
- `box config resource.plugins --local` — project-isolated
- `box config --reset resource.plugins` — back to default
- Resources are either shared (path value) or project-isolated (empty/--local)
- SEEDED scope is a future feature

Environment variables (replaces old `env` top-level command):
- Persistent: `box config env.MY_VAR=value`
- Per-run: `start -e MY_VAR=value` (like `docker run -e`)

Config settings include:
- `start_mode` (continue/new/resume)
- `autonomous` (true/false)
- `model` (agent model name)
- `persistence` (persistent/ephemeral)
- `image` (container image)
- `auth` (shared/distinct)
- `vault.enabled`, `vault.ro`, `vault.rw`
- `resource.*` (resource path overrides)
- `env.*` (persistent environment variables)

### Relocation Commands

Grouped visually in help text under "Relocation" heading, flat under `box`:

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `move` | `[project] <dest>` | Relocate project (error if outside workset) |
| `duplicate` | `<source> [dest]`, `--name`, `--bare`, `--force` | Copy project |
| `archive` | `[project]`, `--as-local`, `--as-standalone`, `--force` | Pack for storage (error for single workset project) |
| `extract` | `<archive> [project]`, `--name`, `--force` | Unpack from archive (confirms if target exists) |

`--bare` on `duplicate`: copy only kanibako metadata (registration, config, vault symlinks),
don't touch the workspace directory. Useful for pointing kanibako at an existing directory.

`--name` on `duplicate`/`extract`: override auto-derived project name (default: directory basename).
Same pattern as `box create --name`.

`--force` on `archive`: overwrite existing archive file. Without `--force`, errors if file exists.

Future features on `duplicate`: `--standalone`, `--no-vault`, `--distinct-auth`

### Data Commands

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `vault snapshot` | `[project]` | Create vault snapshot |
| `vault list` / `vault ls` | `[project]`, `-q`/`--quiet` | List snapshots |
| `vault restore` | `<name> [project]`, `--force` | Restore snapshot (confirms) |
| `vault prune` | `[project]`, `--keep N`, `--force` | Delete old snapshots (confirms) |

Vault settings in config: `vault.enabled`, `vault.ro`, `vault.rw`

---

## `image` — Container Images

Absorbs old `template` command. No distinction between "template" and "normal" images in the
CLI — all images are just images. The distinction is **recoverability**:

- **Registry-backed**: `rebuild` pulls fresh, `rm` warns but recoverable
- **Local with stored Containerfile**: `rebuild` recreates from Containerfile, `rm` warns but recoverable
- **Local, no Containerfile**: `rm` warns "cannot be recovered"

### Standard Lifecycle

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `create` | `<name>`, `--base <image>`, `--always-commit` / `--no-commit-on-error` | Create image interactively |
| `list` / `ls` | `-q`/`--quiet` | List all images |
| `info` / `inspect` | `<image>` | Image details (source, size, recoverability) |
| `rm` / `delete` | `<image>`, `--force` | Delete image (confirms with recoverability context) |

`info`/`inspect` should show whether an image is local-only vs registry-backed (ties into `rm` safety).

### Image-Specific

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `rebuild` | `[image]`, `--all` | Rebuild from registry or stored Containerfile |

`rebuild` auto-detects the image source: registry-backed images are pulled fresh, locally-created
images are rebuilt from stored Containerfile. No flag needed — provenance is in image metadata.

No `image config` for now — images don't have meaningful settings at this level. Can add later.

### Future Features

- `image export` — share/export local images
- Containerfile store for local template rebuild support

---

## `workset` — Project Grouping

Peer of `box`, not a child. Docker-aligned naming.

### Standard Lifecycle

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `create` | `[path]`, `--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth` | Create workset |
| `list` / `ls` | `-q`/`--quiet` | List worksets |
| `info` / `inspect` | `<workset>` | Workset details (root, auth mode, projects list) |
| `rm` / `delete` | `<workset>`, `--purge`, `--force` | Delete workset |
| `config` | `<workset> <key>[=<value>]`, `--reset`, `--effective` | Workset-level config (absorbs `workset auth`) |

`rm` behavior:
- Errors if workset still has projects (like `docker network rm` with connected containers)
- Confirms before deletion, `--force` to skip
- `--purge` also removes files on disk

### Workset-Specific

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `connect` | `<workset> [source]`, `--name` | Add project to workset (was `add`) |
| `disconnect` | `<workset> <project>`, `--force` | Remove project from workset, converts to local (was `remove`) |

`connect` — `[source]` defaults to cwd, follows `[project]` positional pattern.
`disconnect` — converts disconnected project to local mode (1.0 default).
Confirms before disconnect, `--force` to skip.

`workset config` supports all the same keys as `box config`, acting as defaults for projects
in the workset. Projects inherit unless they override via `box config`.

Valid keys: `start_mode`, `autonomous`, `model`, `persistence`, `image`, `auth`,
`vault.enabled`, `vault.ro`, `vault.rw`, `resource.*`, `env.*`

Deferred note: unify config interface between workset and box where possible

### Multi-Agent Worksets

Worksets (and system config) support agent-namespaced keys for agent-specific defaults:
- `workset config myworkset claude.model=sonnet` — applies to Claude projects in this workset
- `workset config myworkset aider.model=gpt-6000` — applies to Aider projects in this workset
- `workset config myworkset auth=distinct` — agent-agnostic, applies to all projects

Same pattern at system level: `system config claude.model=opus`

At box (project) level, no namespace needed — a project uses one agent.

Precedence for agent-specific keys (e.g., model for a Claude project):
box > workset `claude.*` > workset generic > agent > system `claude.*` > system generic

---

## `agent` — Agent Operations

### Standard Lifecycle

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `list` / `ls` | `-q`/`--quiet` | List configured agents |
| `info` / `inspect` | `<agent>` | Agent details |
| `config` | `<key>[=<value>]`, `--reset`, `--effective` | Agent-level config (defaults for projects) |

### Agent-Specific

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `reauth` | `[project]` | Refresh credentials (errors if project given but auth is shared) |
| `helper spawn` | `--depth`, `--breadth`, `--model`, `--image` | Spawn child instance |
| `helper list` / `helper ls` | `-q`/`--quiet` | List helpers |
| `helper stop` | `<number>` | Stop a helper |
| `helper cleanup` | `<number>`, `--cascade` | Clean up helper |
| `helper respawn` | `<number>` | Respawn helper |
| `helper send` | `<number> <message>` | Message a helper |
| `helper broadcast` | `<message>` | Message all helpers |
| `helper log` | `-f`, `--from`, `--tail` | View helper logs |
| `fork` | `<name>` | Fork project (copy state, register, user attaches separately) |

`helper spawn` flags should align with `box create` where applicable (e.g., `--image`).
Helpers are **standalone** by default — all state in one directory, cleanup = delete directory.
No central metadata, no names.toml entries. Discoverable via `agent helper list` only.

`helper log` flags:
- `-f` / `--follow` — tail log in real-time (like `tail -f`)
- `--from <number>` — filter messages from a specific helper
- `--tail <number>` — show last N entries (docker convention)

`fork` creates a copy of the current project state (workspace + metadata) as a new project.
Issued by the agent from inside the container. User attaches via `start` in another terminal.

### Agent Config

Sets defaults that projects inherit. Supports all the same keys as `box config` and `workset config`:
`start_mode`, `autonomous`, `model`, `persistence`, `image`, `auth`,
`vault.enabled`, `vault.ro`, `vault.rw`, `resource.*`, `env.*`

Plus agent-only settings (no project override):
- Agent name/ID
- Credential paths / auth mechanics (managed by reauth, not user-configurable)

Project-default settings (overridable via `box config` or `workset config`):
- Model, autonomy, start mode, persistence, auth mode, resource paths, shell variant

Note: at workset, agent, and global levels, some settings are system-managed and not directly
user-settable. Auth is the primary example but the pattern should accommodate others.

---

## `system` — Global Configuration

### Standard Lifecycle

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `info` / `inspect` | | System details (version, install method, config path, data path, container runtime) |
| `config` | `<key>[=<value>]`, `--reset`, `--effective` | Global configuration |

### System-Specific

| Subcommand | Arguments/Flags | Summary |
|---|---|---|
| `upgrade` | `--check` | Self-update (detects install method; errors if unknown) |

`system config` supports all non-agent project keys as global defaults:
`start_mode`, `autonomous`, `model`, `persistence`, `image`, `auth`,
`vault.enabled`, `vault.ro`, `vault.rw`, `resource.*`, `env.*`

Plus system-only keys (infrastructure, not inherited by projects):
`data_path`, `paths_vault`, `paths_comms`, `target_name`, shared caches

### Config Reset (All Levels)

Applies to `box config`, `workset config`, `agent config`, `system config`:
- `config --reset <key>` — reset one key, confirms (`--force` to skip)
- `config --reset --all` — reset all config at this level, confirms, `--force` to skip
- `config --reset` (no key, no `--all`) — error: "specify a key or use `--all`"

`system config --reset --all` replaces old `remove` command (global config reset).

`upgrade` must detect installation method (pip, pipx, uv, git) and use the appropriate
upgrade mechanism. If method cannot be determined, error with guidance.

---

## Commands Deleted

| Old command | Replacement |
|---|---|
| `setup` | Lazy init on first run (config, dirs, shell completion registration) |
| `remove` | `system config --reset` |
| `resume` | `start -R` / `--resume` |
| `connect` | Merged into `start` (tmux default) |
| `connect --list` | `ps` |
| `status` | `box info` / `box inspect` |
| `config` (top-level) | `box config` |
| `env` (top-level) | `box config env.*` + `start -e` |
| `shared` (top-level) | Absorbed into unified resource model under config |
| `template` (top-level) | `image create` / `image rm` |
| `box migrate` | Deleted entirely |
| `box forget` | `box rm` |
| `box purge` | `box rm --purge` |
| `box orphan` | `box list --orphan` |
| `box get/set` | `box config` |
| `box settings` | `agent config` |
| `box resource` | `box config resource.*` |
| `init` (top-level) | `box create` |

## Flags Deleted

| Old flag | Replacement |
|---|---|
| `-p` / `--project` | `[project]` positional argument |
| `--safe` (old long form) | `-S` / `--secure` (renamed, kept as short flag) |
| `-E` / `--entrypoint` (hidden) | `--entrypoint` (visible, replaces `-c`) |
| `-c` / `--command` | `--entrypoint` |
| `-i` (short form) | `--image` (long only, avoids docker `-i` confusion) |
| `--global` | Use `system config` explicitly |

## Future Features (Not Blocking 1.0 Design)

### Commands
- `exec` — run command in already-running container (like `docker exec`)
- `ps` filtering — `--running`, `--stopped`, etc.
- `image export` — share/export local images
- `image config` — if image-level settings become needed
- Agent `create`/`rm` — explicit agent lifecycle management
- `box create --workset <name>` — create project directly within a workset

### Flags / Options
- `stop --time` — grace period before SIGKILL (docker/podman pattern)
- SEEDED resource scope — copy-on-first-use initialization for resources
- `--env-file` flag on `start` — load env vars from file (like `docker run --env-file`)
- Workset `disconnect --as-local` / `--as-standalone` — choose mode on disconnect (default: local)
- `duplicate --standalone` / `--no-vault` / `--distinct-auth` — mode conversion on duplicate
- `--from` flag to further merge shell/start

### Infrastructure
- Docker/podman CLI compatibility test suite — explicit verification
- pipx/uv compatibility verification (package + plugin bindings)
- Shell completion auto-registration when uv/pipx support it
- Containerfile store for local template rebuild support
- start/shell as explicit variants of one another (shared implementation)
- `system upgrade` — smart detection of more install methods beyond pip/git

## Help Text Organization

Four groups under `box` help output:
1. **Run cycle**: start, stop, shell, ps
2. **Standard lifecycle**: create, list, info, rm, config
3. **Relocation**: move, duplicate, archive, extract
4. **Data**: vault

The #46 help text alignment bug should be addressed AFTER this audit is implemented.

## Config File Sections

Resource overrides in project.toml distinguished by origin:
- `[resources]` — general resources (pip, npm, cargo caches)
- `[agent_resources]` — target plugin-defined resources (plugins/, etc.)

Users don't need to know the distinction — CLI treats them uniformly. `--reset` knows which default to restore based on origin.

## First-Run / Lazy Init (Replaces `setup`)

No explicit `setup` command. On first run of any kanibako command:
1. Create global config file (`kanibako.toml`) if missing
2. Create data directories (containers, templates, comms, agents)
3. Register shell completion
4. Pull/build base container image on first `start` (not on first help/config command)

tmux is checked at runtime — if not installed, warn and fall back to ephemeral mode.

## tmux Integration

- `start` defaults to tmux-wrapped session (`persistent` config default)
- `--ephemeral` flag for no tmux
- If tmux not installed: warn and fall back to ephemeral
- tmux status bar shows Ctrl-B detach hint
- tmux is a soft dependency (documented, runtime-checked)
- Detach: Ctrl-B d. Reattach: `kanibako start [project]` (detects existing session)
- If container running but no tmux session: error with guidance to use `stop`

## Pre-1.0 Requirements (Not CLI Design But Blocking 1.0)

- **Agent instruction set**: Create documentation/prompts for agents explaining what kanibako
  can do and how to use it from inside the container (fork, helper, comms, etc.)

## Open Items (Resolve Before Finalizing)

- ~~`image` command details not yet reviewed~~ — DONE
- ~~`extract` confirm/warn if target exists~~ — DONE (confirm by default, `--force` to skip). Track in implementation plan.
- ~~Vault confirmations~~ — DONE (restore + prune confirm, `--force` to skip)
- ~~Multi-agent workset defaults~~ — DONE (agent-namespaced keys at workset/system level)
- ~~`--local` and similar convenience flags~~ — DONE (see Convenience Flag Review below)
- System-managed settings — auth is primary example; pattern noted in arch, full list TBD. Do not code into a corner.
- ~~Cross-command flag consistency review~~ — DONE (see below)

## Convenience Flag Review (Resolved)

Review session 2026-03-08. All items resolved:

| # | Issue | Resolution |
|---|---|---|
| 1 | `--local` overload (3 meanings) | Dropped from `image rebuild` (auto-resolve). Kept on `create` and `config resource.*` — consistent "project-scoped" meaning. |
| 2 | `[project]` positional on config | `key=value` syntax for set. Known-key heuristic for get disambiguation. |
| 3 | `-A` boolean polarity | `-A`/`--autonomous` + `-S`/`--secure` mutually exclusive pair. Both uppercase agent flags. |
| 4 | `--bare` undefined | Already in source: "copy only metadata, don't touch workspace." Documented. |
| 5 | `--as-ac` jargon | Terminology rename: AC → local, decentralized → standalone, working set → workset. Flags: `--as-local`/`--as-standalone`. `box create --standalone` replaces `--local`. |
| 6 | `image rebuild --local` | Dropped. Auto-resolve from image metadata. |
| 7 | `shell` persistence flags | Keep — same defaults and flags as `start`. Uniform. |
| 8 | `workset create` config flags | Full set: `--standalone`, `--image`, `--no-vault`, `--distinct-auth`. Mirrors `box create`. |
| 9 | `--name` on create commands | Added to `box create`. `workset create` aligned to path-primary pattern with `--name`. Added to `box extract` for consistency with `duplicate`. |
| 10 | `--quiet`/`-q` | Added to all `list`/`ls` and `ps` commands (box, workset, agent, image). Outputs names only, one per line. |
| 11 | `reauth` shared auth | Error if project arg given but auth is shared. |
| 12 | `--name` on `extract` | Added for consistency with `duplicate`. |

## Cross-Command Flag Consistency Review (Resolved)

Review session 2026-03-08. Compared flags within command groups and across tiers.

### Gaps Found and Resolved

| # | Gap | Resolution |
|---|---|---|
| 1 | `stop` missing `--all` | Added. Bulk stop is a natural operation even without bulk start. |
| 2 | `stop` missing `--force`/`--time` | Deferred to future features. Docker `--time` pattern. |
| 3 | `archive` missing `--force` | Added — error on existing file, `--force` to overwrite. |
| 4 | `vault list` missing `-q/--quiet` | Added. |
| 5 | `helper list` missing `-q/--quiet` | Added. |
| 6 | `config --local` at workset/agent/system | Documented — "set default to project-local" is valid at all levels. |

### Confirmed Consistent (No Gaps)

- **`create` across tiers**: box and workset aligned (path-primary, `--name`, same config flags). Image create is structurally different — separate flags expected.
- **`rm` across tiers**: `--force` everywhere. `--purge` on box/workset (metadata split). Image rm has no metadata split.
- **`config` across tiers**: `key=value`, `--effective`, `--reset`, `--all`, `--force` consistent at all four levels.
- **`info` across tiers**: just `[target]`, no extra flags. Clean.
- **`--force`**: always means "skip confirmation." Consistent everywhere.
- **`--purge`**: always means "also delete associated data." Consistent on box rm and workset rm.
- **`-q/--quiet`**: now on all list/ls/ps commands across all tiers.

## Final UX Review (Resolved)

Review by subagent, 2026-03-08. Decisions:

| # | Issue | Resolution |
|---|---|---|
| M2 | `init` not in Deleted Commands table | Added. Clean break, no hidden alias. |
| M3 | `box ps` vs `box list` overlap with `--all` | OK — `--all` consistently means "don't exclude anything." Help text should state what the default filter is. |
| M4 | `reauth` under `agent` but takes `[project]` | Keep — splitting into two commands would be more confusing. Placement is correct (agent-scoped operation). |
| M5 | Config disambiguation edge case | Not ambiguous — config uses `key=value` (with `=`). Project names should be restricted to a safe character subset. |
| M6 | `-A` help text when default is `true` | Already addressed — `-A` is an override when config default is `-S`. Help text should be explicit. |
| m2 | `archive --as-local/--as-standalone` | Mode baked into archive, flags allow override at archive time. Workset mode may need special handling. |
| m4 | `stop --all` no confirmation | Added confirmation, `--force` to skip. Consistent with other bulk operations. |
| m6 | `vault snapshot` breaks `create` pattern | Keep — "snapshot" is a verb. `vault create` would need `create-snapshot`, which is worse. |
| m8 | No `init` alias for `box create` | Clean break. `init` added to Deleted Commands table. |
