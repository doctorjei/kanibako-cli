# Kanibako (蟹箱)

Safe, persistent workspaces for AI coding agents.

Don't be crabby -- pick up where you left off.

---

Kanibako gives AI coding agents a safe place to work -- real tools, real files,
real network access -- without risking your host system.  Each project gets its
own isolated container with persistent state: shell config, credentials, and
agent sessions that survive reboots and SSH disconnects.

CRAB: **C**ontained **R**untime **A**gent in a **B**ox.

No Docker or Podman experience required.  Just `cd` into a project and run
`kanibako`.  Setup, rig pulls, credential syncing, and teardown are automatic.
Claude Code is supported via the built-in plugin; other agents (Aider, Codex,
Goose) are available as [example plugins](examples/).

## Features

- **Safe by default** -- rootless containers with no host access; the sandbox
  is what makes it safe to give agents real autonomy
- **Automatic sandboxing** -- no Docker or Podman experience required;
  Kanibako manages all container operations for you
- **Session continuity** -- `kanibako start` defaults to `--continue`, picking
  up where you left off; persistent tmux sessions survive SSH disconnects
- **Per-project isolation** -- each project gets its own shell, config, and
  credentials (three modes: local, workset, standalone)
- **Credential forwarding** -- host credentials are synced into the container
  and written back after each session
- **Setup wizard** -- `kanibako setup` detects installed agents and checks
  your container runtime; no manual configuration needed
- **Diagnostics** -- `kanibako system diagnose` checks runtime, images,
  agents, and storage; `box diagnose`, `crab diagnose`, and `rig diagnose`
  drill into specific scopes
- **Vault snapshots** -- per-project read-only and read-write shared
  directories with smart snapshot strategy detection (reflink, hardlink,
  or tar.xz depending on filesystem)
- **Shell customization** -- per-project environment variables (`box config
  env.*`), drop-in init scripts (`shell.d/`), and layered home directory
  templates
- **Crab configuration** -- per-agent TOML config with template variant,
  default args, state knobs, env vars, and shared caches; per-project
  setting overrides via `box config`
- **Shared caches** -- global download caches (pip, cargo, npm, etc.)
  shared across projects; agent-level caches via crab TOML
- **Plugin system** -- agent-agnostic core (`kanibako-base`); Claude Code
  plugin (`kanibako-agent-claude`) ships by default; three-tier discovery
  (entry points, user directory, project directory)
- **Rig freshness checks** -- non-blocking digest comparison against GHCR
  on startup (24h cache)
- **Helper spawning** -- spawn child agent instances for parallel workloads
  with budget-controlled depth and breadth
- **Concurrency lock** -- prevents two sessions from running in the same
  project simultaneously

## Prerequisites

- Python 3.11+
- [Podman](https://podman.io/) (recommended) or Docker -- just needs to be
  installed; Kanibako manages all container operations automatically
- An AI coding agent installed on the host (e.g.
  [Claude Code](https://docs.anthropic.com/en/docs/claude-code))

## Installation

```bash
# Standard install (base + Claude plugin)
uv tool install kanibako
# -- or --
pip install kanibako

# Base only (no agent plugins -- agent-agnostic shell mode)
pip install kanibako-base

# Development install
git clone https://github.com/doctorjei/kanibako.git
cd kanibako
pip install -e '.[dev]' -e packages/agent-claude/
```

On first use, Kanibako automatically creates its config and data directories.
Run `kanibako setup` to verify your environment, or just dive in -- setup
runs automatically when needed.

## Quick Start

```bash
# Start an agent session in the current directory
cd ~/my-project
kanibako

# Start with a specific rig
kanibako --image kanibako-min:latest

# Open a plain bash shell (no agent)
kanibako shell

# Run a one-shot command in the container
kanibako shell -- echo hello

# Start a new conversation
kanibako -N

# Resume with the conversation picker
kanibako -R
```

That's it -- no `docker run`, no volume flags, no Containerfile.  On first run,
Kanibako automatically pulls the container rig, sets up the project
environment, and syncs your credentials.  Subsequent runs pick up where you
left off.

## Example: Python Project

The default `kanibako-oci` rig (based on droste-fiber) includes Python, git,
gh, nano, jq, ripgrep, tmux, Podman, and common dev tools.  This is enough for
most Python, JavaScript, and general scripting work.

```bash
# 1. Install kanibako
pip install kanibako

# 2. Create or clone a project
mkdir ~/my-flask-app && cd ~/my-flask-app
git init
# (or: git clone https://github.com/you/my-flask-app.git && cd my-flask-app)

# 3. Launch -- that's it
kanibako
```

On the first launch, Kanibako will:
- Pull the base container rig (once, cached afterwards)
- Create an isolated environment for this project
- Copy your agent credentials into the sandbox
- Drop you into an agent session inside the container

The agent sees your project files in `~/workspace/` and has full access to
Python, git, and the other base tools.  When you exit, your project files
and agent state are preserved -- next time you run `kanibako` in the same
directory, it picks up where you left off.

```bash
# Later: come back to the same project
cd ~/my-flask-app
kanibako              # resumes your previous session
kanibako -N           # or start a fresh conversation
```

## Example: C/Rust Project (custom rig)

For projects that need compiled languages, create a custom rig with the
toolchains you need:

```bash
# 1. Create a custom rig with C/C++ and Rust
kanibako rig create systems
# (inside: sudo apt install build-essential cmake gdb && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)
# exit when done

# 2. Use it for your project
cd ~/my-rust-project
kanibako --image kanibako-template-systems
```

After the first run, Kanibako remembers the rig choice for this project,
so you can just run `kanibako` next time.

See [Container Rigs](#container-rigs) for the base rigs and custom rig
creation.

## Commands

Kanibako organizes commands into four management groups plus seven top-level
shortcuts for common operations:

### Top-Level Shortcuts

| Shortcut | Maps to | Description |
|----------|---------|-------------|
| `kanibako [start] [project]` | `box start` | Launch agent session (default command) |
| `kanibako stop [project\|--all]` | `box stop` | Stop running container(s) |
| `kanibako shell [project] [-- cmd]` | `box shell` | Open a bash shell or run a one-shot command |
| `kanibako list [-a] [-q]` | `box list` | List all projects |
| `kanibako ps [-a] [-q]` | `box ps` | List active (running) boxes |
| `kanibako create [path]` | `box create` | Create a new project |
| `kanibako rm <project>` | `box rm` | Remove a project |

### Management Commands

| Command | Description |
|---------|-------------|
| `box` | Project lifecycle (create, list, start, stop, shell, config, archive, ...) |
| `rig` | Rig management -- container images (create, list, info, rm, rebuild) |
| `workset` | Project grouping (create, list, connect, disconnect, config, ...) |
| `crab` | Crab (agent) management (list, config, reauth, helper, fork) |
| `system` | Global configuration, diagnostics, and self-update |

**Aliases:** `agent` -> `crab`, `image` -> `rig`, `container` -> `box`

### `box` Subcommands

**Run cycle:**

| Subcommand | Description |
|------------|-------------|
| `box start [project]` | Launch agent session (agent flags + infra flags + `-- args`) |
| `box stop [project]` | Stop container (`--all` stops all, `--force` skips confirm) |
| `box shell [project]` | Open bash or run one-shot command (infra flags + `-- cmd`) |
| `box ps` | List active (running) boxes (`--all` includes stopped, `-q` names only) |

**Standard lifecycle:**

| Subcommand | Description |
|------------|-------------|
| `box create [path]` | Create project (`--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth`) |
| `box list` / `box ls` | List projects (`--all`, `--orphan`, `-q`) |
| `box info` / `box inspect` | Project details (mode, paths, lock, rig) |
| `box rm` / `box delete` | Remove project (`--purge` deletes metadata, `--force` skips confirm) |
| `box config` | View or modify project configuration |
| `box diagnose [project]` | Check project box health |

**Relocation:**

| Subcommand | Description |
|------------|-------------|
| `box move [project] <dest>` | Relocate project workspace |
| `box duplicate <source> [dest]` | Copy project (`--name`, `--bare`, `--force`) |
| `box archive [project]` | Pack session data to .txz (`--as-local`, `--as-standalone`, `--force`) |
| `box extract <archive> [dest]` | Unpack from archive (`--name`, `--force`) |

**Data:**

| Subcommand | Description |
|------------|-------------|
| `box vault snapshot` | Create a vault snapshot |
| `box vault list` / `vault ls` | List snapshots (`-q`) |
| `box vault restore <name>` | Restore from snapshot (`--force`) |
| `box vault prune` | Delete old snapshots (`--keep N`, `--force`) |

### `rig` Subcommands

| Subcommand | Description |
|------------|-------------|
| `rig create <name>` | Create rig interactively (`--base`, `--always-commit`, `--no-commit-on-error`) |
| `rig list` / `rig ls` | List available rigs (`-q`) |
| `rig info` / `rig inspect` | Rig details (source, size, recoverability) |
| `rig rm` / `rig delete` | Remove rig (`--force`) |
| `rig rebuild [rig]` | Rebuild from registry or stored Containerfile (`--all`) |
| `rig diagnose` | Check rig (image) status |

### `workset` Subcommands

| Subcommand | Description |
|------------|-------------|
| `workset create [path]` | Create working set (`--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth`) |
| `workset list` / `workset ls` | List working sets (`-q`) |
| `workset info` / `workset inspect` | Working set details |
| `workset rm` / `workset delete` | Remove working set (`--purge`, `--force`) |
| `workset config` | View or modify workset configuration |
| `workset connect <workset> [source]` | Add project to working set (`--name`) |
| `workset disconnect <workset> <project>` | Remove project from working set (`--force`) |

### `crab` Subcommands

| Subcommand | Description |
|------------|-------------|
| `crab list` / `crab ls` | List configured crabs (`-q`) |
| `crab info` / `crab inspect` | Crab configuration details |
| `crab config` | View or modify crab configuration |
| `crab reauth [project]` | Refresh credentials |
| `crab helper spawn` | Spawn child instance (`--depth`, `--breadth`, `--model`, `--image`) |
| `crab helper list` / `helper ls` | List helpers (`-q`) |
| `crab helper stop <n>` | Stop a helper |
| `crab helper respawn <n>` | Respawn a stopped helper |
| `crab helper cleanup <n>` | Clean up helper (`--cascade`) |
| `crab helper send <n> <msg>` | Message a helper |
| `crab helper broadcast <msg>` | Message all helpers |
| `crab helper log` | View message log (`-f`, `--from`, `--tail`) |
| `crab fork <name>` | Fork project into a new directory |
| `crab diagnose` | Check agent status and configuration |

### `system` Subcommands

| Subcommand | Description |
|------------|-------------|
| `system info` / `system inspect` | System details (version, runtime, paths) |
| `system config` | View or modify global configuration |
| `system upgrade` | Self-update (`--check` for dry run) |
| `system diagnose` | Check system health (runtime, images, agents, storage) |

## Common Flags

### Agent Flags (on `start`)

| Flag | Description |
|------|-------------|
| `-N, --new` | Start a new conversation |
| `-C, --continue` | Continue the most recent conversation (default) |
| `-R, --resume` | Resume with conversation picker |
| `-A, --autonomous` | Run with full permissions (default) |
| `-S, --secure` | Run without `--dangerously-skip-permissions` |
| `-M, --model MODEL` | Override the agent model for this run |

`-N`, `-C`, `-R` are mutually exclusive.  `-A`, `-S` are mutually exclusive.

### Infrastructure Flags (on `start` and `shell`)

| Flag | Description |
|------|-------------|
| `-e, --env KEY=VALUE` | Per-run environment variable (repeatable) |
| `--image IMAGE` | Container rig override |
| `--entrypoint CMD` | Override container entrypoint |
| `--persistent` | Use tmux session wrapper (default) |
| `--ephemeral` | No tmux, session dies with terminal |
| `--no-helpers` | Disable helper spawning |
| `--no-auto-auth` | Disable automated browser-based OAuth refresh |
| `--browser` | Launch a headless browser sidecar (`BROWSER_WS_ENDPOINT` injected) |

### Global Flags

| Flag | Description |
|------|-------------|
| `-v, --verbose` | Show debug output (target detection, container command) |

## Project Modes

Kanibako supports three ways to organize project state.  The mode is inferred
automatically from context.

### Local (default)

Centralized store keyed by project name.  Just `cd` into any directory and
run `kanibako`.

```
$XDG_DATA_HOME/kanibako/boxes/{name}/          metadata + shell
{project}/vault/share-ro/                      read-only vault
{project}/vault/share-rw/                      read-write vault
```

### Workset

Group related projects under a named working set with human-readable paths.

Worksets are stable and supported but not actively receiving new features.
For most use cases, local mode is simpler.

```bash
kanibako workset create ~/worksets/research --name my-research
kanibako workset connect my-research ~/repos/paper-a --name paper-a
cd ~/worksets/research/workspaces/paper-a
kanibako
```

```
{workset}/boxes/{name}/             metadata + shell
{workset}/workspaces/{name}/        workspace
{workset}/vault/{name}/share-{ro,rw}/  vault
```

### Standalone

All state lives inside the project directory itself.  Fully portable.

```bash
kanibako create --standalone           # in the current directory
kanibako create --standalone ~/myproj  # create and initialize a new directory
```

```
{project}/.kanibako/          metadata + shell
{project}/vault/share-{ro,rw}/  vault
```

### Orphan detection

Find projects whose workspace directory no longer exists:

```bash
kanibako box list --orphan
```

## Container Rigs

All rigs are built on [Droste](https://github.com/doctorjei/droste) tiers
(Debian 13) with a thin Kanibako layer on top (agent user, gh, ripgrep,
directory scaffolding).  The AI agent binary is mounted from the host.

| Rig | Droste Base | Role |
|-----|-------------|------|
| `kanibako-min` | droste-seed | Minimal agent container |
| `kanibako-oci` | droste-fiber | Agent container + nested OCI host |
| `kanibako-lxc` | droste-thread | LXC system container host (via [Kento](https://github.com/doctorjei/kento)) |
| `kanibako-vm` | droste-hair | VM host (via [Kento](https://github.com/doctorjei/kento) + [Tenkei](https://github.com/doctorjei/tenkei)) |

`kanibako-oci` is the default.  It includes Podman and rootless container
infrastructure, so it can both run agents directly and host nested Kanibako
containers.

### Ecosystem

Container rigs are built on [Droste](https://github.com/doctorjei/droste), a
layered OCI image builder.
[Kento](https://github.com/doctorjei/kento) converts them to LXC/VM hosts.

Rigs are pulled automatically from GHCR on first use.  If the pull fails,
Kanibako falls back to a local build from the bundled Containerfiles.

```bash
kanibako rig list                     # show local rigs
kanibako rig rebuild                  # rebuild current project's rig
kanibako rig rebuild --all            # rebuild all known rigs
```

### Custom Rigs

Create custom rigs by installing tools interactively and committing
the result:

```bash
kanibako rig create jvm               # start from kanibako-oci, install tools
# (inside container: apt install openjdk-21-jdk maven, etc.)
# exit when done

kanibako rig list                     # show local rigs
kanibako rig rm jvm                   # remove a custom rig
```

Custom rigs are standard OCI images -- push them to any registry for sharing:

```bash
podman push kanibako-template-jvm ghcr.io/myorg/kanibako-template-jvm
```

### Host Deployment

For always-on deployments using LXC, VMs, or nested OCI containers, see
[docs/host-deployment.md](docs/host-deployment.md).

## Container Layout

Inside the container, the agent sees:

```
/home/agent/                 persistent shell (bind mount)
  |- .bashrc                shell config (with shell.d sourcing)
  |- .profile               login profile
  |- .shell.d/              drop-in init scripts (*.sh)
  |- .claude/               agent credentials
  |- .claude.json            agent settings
  |- workspace/             project files (bind mount)
  |- share-ro/              read-only vault (bind mount, optional)
  '- share-rw/              read-write vault (bind mount, optional)
```

## Shell Customization

### Environment variables

Set per-project or global environment variables that are passed to the
container:

```bash
# Persistent (stored in project config)
kanibako box config env.EDITOR=vim           # project-level
kanibako system config env.EDITOR=nano       # global (all projects)
kanibako box config env.EDITOR               # show one value

# Per-run (not persisted)
kanibako start -e EDITOR=vim -e DEBUG=1
```

Project env vars override global ones.

### Custom prompt

The shell prompt is controlled by the `KANIBAKO_PS1` environment variable:

```bash
kanibako box config env.KANIBAKO_PS1="(myproject) \u:\w\$ "
```

### Init scripts

Drop `.sh` files into the `shell.d/` directory inside your project's shell
path.  They are sourced by `.bashrc` on every interactive shell startup:

```bash
# Find your shell path
kanibako box info

# Add a custom init script
echo 'export PATH="$HOME/.local/bin:$PATH"' > /path/to/shell/.shell.d/path.sh
echo 'alias ll="ls -la"' > /path/to/shell/.shell.d/aliases.sh
```

Existing shells from older Kanibako versions are automatically upgraded to
support `shell.d/` on the next launch.

## Crab Configuration

Each crab (agent instance) gets a TOML configuration file at
`$XDG_DATA_HOME/kanibako/agents/{id}.toml`.  The file is generated
automatically on first use (via the target plugin's `generate_agent_config()`
method) and can be edited afterwards.

```toml
[agent]
name = "Claude Code"
shell = "standard"          # template variant (see Shell Templates)
default_args = []           # extra CLI args prepended on every launch

[state]
model = "opus"              # target-specific knobs (e.g. --model for Claude)
access = "permissive"

[env]
# KEY = "value"             # raw env vars injected into the container

[shared]
# plugins = ".claude/plugins"  # agent-level shared cache paths

[tweakcc]
# enabled = false           # enable tweakcc binary patching
# config = "~/.tweakcc/config.json"  # external tweakcc config file
```

**Sections:**
- `[agent]` -- identity and defaults (name, shell template variant, default CLI args)
- `[state]` -- runtime behavior knobs translated by the target plugin into CLI
  args and env vars (e.g. Claude maps `model` -> `--model`)
- `[env]` -- environment variables injected into the container
- `[shared]` -- agent-level shared cache paths (mounted from the per-agent
  shared directory, independent of global shared caches)
- `[tweakcc]` -- optional tweakcc integration for binary patching
  (see [docs/tweakcc.md](docs/tweakcc.md))

Manage crab settings via the CLI:

```bash
kanibako crab list                    # list configured crabs
kanibako crab config model            # show effective model
kanibako crab config model=sonnet     # set crab-level default
```

## Shell Templates

Shell templates provide layered home directory initialization for new projects.
Templates live under `$XDG_DATA_HOME/kanibako/templates/` and are applied
once during project init.

**Resolution order** (for template variant `standard` and agent `claude`):
1. `templates/claude/standard/` -- agent-specific template
2. `templates/general/standard/` -- general fallback
3. None -- no template files applied

The special variant `"empty"` always resolves to None (no files applied),
bypassing directory lookup entirely.  Use `shell = "empty"` in the crab
TOML to skip template initialization.

**Layering:**
1. `templates/general/base/` is copied first (common skeleton)
2. The resolved template overlays on top

**Example directory layout** (for agent `claude`, variant `standard`):

```
templates/
|- general/
|   |- base/              <- layer 1: always copied (common skeleton)
|   |   |- .bashrc
|   |   '- .profile
|   '- standard/          <- layer 2 fallback (if no agent-specific dir)
'- claude/
    '- standard/          <- layer 2 preferred (agent-specific)
        |- .claude/
        |   '- settings.json
        '- playbook/
            '- ONBOARD.md
```

**Important:** files go inside `claude/standard/`, not directly in `claude/`.
Placing files in `templates/claude/` (without the variant subdirectory) will
have no effect -- the resolver looks for `templates/{agent}/{variant}/`.

The template variant is controlled by the `shell` field in the crab TOML
(defaults to `"standard"`).  To customize, create a directory under
`templates/` matching the desired structure and set `shell` in the crab TOML.

## Vault

Each project has optional read-only and read-write shared directories:

- **share-ro/** -- files visible inside the container but not writable
  (documentation, reference data, prompt libraries)
- **share-rw/** -- files that persist across sessions and can be modified
  (databases, build caches, generated artifacts)

In local mode, vault directories live under your project and are hidden inside
the container via a read-only tmpfs overlay, so the agent cannot see or modify
vault metadata.

### Snapshots

Kanibako automatically creates a snapshot of `share-rw/` before each
container launch.  The snapshot strategy is detected per-project: reflink
(instant copy-on-write on Btrfs/XFS), hardlink (fast for unchanged files),
or tar.xz (universal fallback).  Manage snapshots manually:

```bash
kanibako box vault snapshot          # create a snapshot now
kanibako box vault list              # show all snapshots
kanibako box vault restore <name>    # restore from a snapshot
kanibako box vault prune --keep 5    # keep only 5 most recent
```

### Disabling vault

```bash
kanibako create --standalone --no-vault          # standalone project without vault
kanibako create --standalone ~/p --no-vault      # new directory, no vault
```

## Target Plugin System

Kanibako is agent-agnostic.  All agent-specific logic lives in **target
plugins** -- Python classes that implement the `Target` abstract base class.
Claude Code is supported via `kanibako-agent-claude` (installed by the
`kanibako` meta-package); other agents can be added as pip packages.
Install `kanibako-base` for agent-agnostic operation.
If no agent is detected, Kanibako falls back to `no_agent` -- a plain shell
with no agent binary or credentials.

**Supported agents:**
- **Claude Code** -- built-in via `kanibako-agent-claude`
- **Aider, Codex CLI, Goose** -- example plugins in [examples/](examples/)

A target handles:
1. Detecting the agent binary on the host
2. Mounting the binary/installation into the container
3. Syncing credentials between host and container
4. Building CLI arguments for the agent entrypoint

### Three-tier plugin discovery

Kanibako discovers target plugins from three sources, checked in order.
Later sources override earlier ones when two plugins register the same name.

| Tier | Location | Use case |
|------|----------|----------|
| 1. Entry points | `kanibako.targets` entry point group + `kanibako.plugins.*` namespace scan | Pip-installed packages and bind-mounted plugins in nested containers |
| 2. User directory | `~/.local/share/kanibako/plugins/*.py` | Personal plugins shared across all projects |
| 3. Project directory | `{project}/.kanibako/plugins/*.py` | Project-specific plugins |

Drop a `.py` file containing a `Target` subclass into the user or project
plugins directory and Kanibako picks it up automatically -- no packaging or
`pip install` needed.  Files starting with `_` are skipped.

**Security note:** file-drop plugins run with the same permissions as
Kanibako itself.  Only place files you trust in plugin directories.

See [docs/writing-targets.md](docs/writing-targets.md) for the full developer
guide, and [examples/](examples/) for three graduated example plugins (Aider,
Codex CLI, Goose).

```bash
# Install a third-party target
pip install kanibako-target-aider

# Use a specific target
kanibako box config target_name=aider
kanibako start
```

## Configuration

```
Precedence: CLI flag > project.toml > workset config > crab config > kanibako.toml > defaults
```

All configuration levels share a unified interface:

```bash
# Box (project) level
kanibako box config                     # show project overrides
kanibako box config --effective         # show resolved values (inherited + overrides)
kanibako box config model               # get one key
kanibako box config model=sonnet        # set one key
kanibako box config --reset model       # remove override, back to default

# Workset level (defaults for projects in this workset)
kanibako workset config <workset> model=opus

# Crab level (defaults for all projects using this crab)
kanibako crab config model=opus

# System level (global defaults)
kanibako system config model=opus
kanibako system config --reset --all    # reset all global config
```

### Config files

- **Global**: `$XDG_CONFIG_HOME/kanibako.toml`
- **Project**: `boxes/{name}/project.toml`
- **Crabs**: `$XDG_DATA_HOME/kanibako/agents/{id}.toml`
- **Templates**: `$XDG_DATA_HOME/kanibako/templates/`

### Configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `start_mode` | `continue` | Default start mode (continue/new/resume) |
| `model` | platform default | Agent model name |
| `autonomous` | `true` | Enable autonomy override |
| `persistence` | `persistent` | Session type (persistent/ephemeral) |
| `image` | `kanibako-oci:latest` | Container rig |
| `auth` | `shared` | Credential mode (shared/distinct) |
| `vault.enabled` | `true` | Enable vault directories |
| `env.*` | | Persistent environment variables |
| `resource.*` | | Resource path overrides |
| `target_name` | (auto-detect) | Agent target plugin |

### Global config file

The global config supports a `[paths]` section to override data directory
layout, and a `[shared]` section for globally shared cache mounts:

```toml
[paths]
data_path = ""         # override XDG_DATA_HOME/kanibako
boxes = "boxes"        # project state subdirectory
agents = "agents"      # crab TOML subdirectory
shared = "shared"      # shared caches subdirectory
templates = "templates"

[shared]
pip = ".cache/pip"
cargo = ".cargo/registry"
npm = ".npm"
```

Shared caches are **lazy** -- they are only mounted if the host directory exists.

## Helper Spawning

Kanibako containers can spawn child instances for parallel workloads.
Each child gets its own directory tree, peer communication channels,
and spawn budget. Helpers are enabled by default -- the host runs a
Unix socket hub alongside the director container, and helpers connect
to it for orchestration and messaging.

```bash
# Spawning and lifecycle
kanibako crab helper spawn                 # spawn a child with default budget
kanibako crab helper spawn --model sonnet  # child uses a different model
kanibako crab helper spawn --depth 2 --breadth 3  # custom spawn limits
kanibako crab helper list                  # show all helpers with status
kanibako crab helper stop 1                # stop helper 1
kanibako crab helper respawn 1             # relaunch a stopped helper
kanibako crab helper cleanup 1             # stop and remove helper 1
kanibako crab helper cleanup 1 --cascade   # also remove all descendants

# Messaging
kanibako crab helper send 1 "Analyze the auth module"
kanibako crab helper broadcast "Starting tests"

# Conversation log
kanibako crab helper log                   # display full message log
kanibako crab helper log --follow          # tail log in real-time
kanibako crab helper log --from 1          # filter by helper number
kanibako crab helper log --tail 10         # show last 10 entries

# Opt out
kanibako start --no-helpers                 # launch without helper support
```

**Architecture:** The Kanibako CLI is bind-mounted into every container
(director and helpers), so `kanibako crab helper spawn/send/broadcast/log`
works inside containers. Each helper launches with `helper-init.sh` as
its entrypoint -- the script registers with the hub, sources broadcast
startup scripts, then execs the agent command.

Two communication layers work together:
- **Directories** -- file sharing (workspace, vault, peers, broadcast).
  Persistent, async. Good for sharing code, configs, results.
- **Socket** -- control plane (spawn/stop) + real-time messaging
  (peer-to-peer, parent-child, broadcast). The host listener acts as
  a central message router.

**Logging:** All inter-agent messages are logged to a JSONL file on the
host. Each entry records sender, recipient(s), timestamp, and message
content. View the conversation in real-time with `kanibako crab helper log --follow`:
```
12:35:10  [0 -> 1]  Analyze the auth module and report back.
12:36:45  [1 -> 0]  Found 3 issues in the token refresh flow.
12:37:00  [0 -> *]  Starting integration tests.
```

**Spawn budget:** Each helper gets a depth/breadth budget controlling
how many levels deep it can spawn and how many siblings are allowed.
Depth decrements with each level. The budget is written as a read-only
config (`spawn.toml`) inside the child, enforced at spawn time.

**Peer channels:** Helpers communicate through shared directories.
Each pair of siblings gets three channels (A-reads, B-reads, shared-rw).
A broadcast channel (`all/`) is available to all helpers.

**Directory layout** (inside a container):
```
~/helpers/
  1/                    # helper 1 root
    workspace/          # helper's working directory
    vault/share-ro/     # read-only vault share
    vault/share-rw/     # read-write vault share
    playbook/scripts/   # helper-init.sh (entrypoint wrapper)
    peers/              # symlinks to peer channels
    all -> ../all/      # broadcast channel
    spawn.toml          # RO spawn budget
    state.json          # status, model, depth, peers
  all/ro/               # broadcast read-only
  all/rw/               # broadcast read-write
  channels/             # raw peer channel directories
~/.kanibako/
  helper.sock           # hub socket (mounted from host)
  helper-messages.jsonl # message log (mounted read-only)
~/.local/bin/kanibako   # kanibako CLI (bind-mounted from host, ro)
```

## Persistent Sessions

`kanibako start` runs agents in tmux by default (`--persistent` mode).
The container uses tmux as PID 1 -- detaching or losing the connection
leaves the agent running. Running `kanibako start` again reattaches to
the same session.

```bash
# Start a session (tmux by default)
kanibako start myproject

# Detach: Ctrl-B d (agent keeps running)
# Reattach later:
kanibako start myproject

# Start without tmux (session dies when terminal closes)
kanibako start --ephemeral myproject

# List running projects
kanibako ps
```

**Lifecycle:**
- First `start` -> creates a detached container with tmux, then attaches
- Subsequent `start` -> reattaches to the running container
- SSH disconnect -> container keeps running; reconnect with `start`
- `kanibako stop` -> stops and removes the container
- Agent exits -> tmux session ends -> container stops

### SSH integration

Set up SSH forced commands to map SSH keys directly to projects.
Each key connects to a specific project -- no shell access needed.

**Per-key routing** in `~/.ssh/authorized_keys`:

```
command="kanibako start myproject" ssh-ed25519 AAAA... user@laptop-myproject
command="kanibako start client/webapp" ssh-ed25519 AAAA... user@laptop-webapp
```

**Dedicated SSH config** on the client:

```
Host myproject
    HostName remote-server.example.com
    User kanibako
    IdentityFile ~/.ssh/id_myproject
```

Then just `ssh myproject` to connect directly to the agent session.

**With a jump host / bastion:**

```
Host myproject
    HostName internal-server
    User kanibako
    IdentityFile ~/.ssh/id_myproject
    ProxyJump bastion.example.com
```

**Tips:**
- Use one SSH key per project for clean routing
- Set `PermitTTY yes` and `PermitOpen none` in `sshd_config` for the
  kanibako user to restrict access to terminal-only
- The kanibako user only needs access to `kanibako start` -- no shell
  required (`ForceCommand` handles routing)
- Credentials are refreshed on every reattach; if tokens expire, the
  agent prompts for re-auth via URL

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]" -e packages/agent-claude/

# Run tests
pytest tests/ -v                    # unit tests (1911)
pytest tests/ -v -m integration     # integration tests (35)

# Lint
ruff check src/ tests/

# Type checking
mypy src/kanibako/

# Release
bump2version patch|minor|major      # auto-commits and tags
git push && git push --tags
```

## Architecture

For the full module-by-module breakdown, see
[docs/architecture.md](docs/architecture.md).

**Overview:** Kanibako's core (`kanibako-base`) handles container lifecycle,
project state, configuration, and plugin discovery.  Agent-specific logic
lives in target plugins (e.g. `kanibako-agent-claude`).  The CLI is an
argparse tree in `cli.py` that delegates to command modules in `commands/`.
Configuration flows through a unified engine (`config_interface.py`) that
supports get/set/reset/show at every level (box, workset, crab, system).

## License

See [LICENSE](LICENSE.md) for details.

## Credits

LLMs were used as a tool in the development of this software.
