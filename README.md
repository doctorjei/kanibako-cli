# Kanibako

Run [Claude Code](https://docs.anthropic.com/en/docs/claude-code) in rootless
containers with per-project isolation, credential forwarding, and session
continuity.

Kanibako wraps Podman or Docker to give each project its own sandboxed
environment.  The container is ephemeral — your shell config, agent state, and
credentials persist across sessions via bind mounts.  Claude Code (or any
supported AI agent) is mounted from the host, so the container images stay
small and toolchain-focused.

## Features

- **Rootless containers** — Podman (preferred) or Docker, no root required
- **Per-project isolation** — each project gets its own shell, config, and
  credentials, keyed by directory hash
- **Three project modes** — account-centric (default), working set (grouped),
  and decentralized (self-contained)
- **Session continuity** — `kanibako start` defaults to `--continue`, picking
  up where you left off
- **Credential forwarding** — host `~/.claude/` credentials are synced into
  the container shell and written back after each session
- **Vault** — per-project read-only and read-write shared directories, with
  automatic tar.xz snapshots before each launch
- **Shell customization** — per-project environment variables (`kanibako env`)
  and drop-in init scripts (`shell.d/`)
- **Agent configuration** — per-agent TOML config with template variant,
  default args, state knobs, env vars, and shared caches
- **Shell templates** — layered home directory templates applied on first
  project init, with agent-specific and general variants
- **Shared caches** — global download caches (pip, cargo, npm, etc.) shared
  across projects; agent-level caches via agent TOML
- **Target plugin system** — agent-agnostic; Claude Code is a built-in target,
  other agents can be added via `pip install`
- **Image freshness checks** — non-blocking digest comparison against GHCR on
  startup (24h cache)
- **Concurrency lock** — prevents two sessions from running in the same
  project simultaneously

## Prerequisites

- Python 3.11+
- [Podman](https://podman.io/) (recommended) or Docker
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed on
  the host

## Installation

```bash
# Clone and install in development mode
git clone https://github.com/doctorjei/kanibako.git
cd kanibako
pip install -e ".[dev]"

# First-time setup (creates config + pulls image)
kanibako setup
```

## Quick Start

```bash
# Start a Claude session in the current directory
cd ~/my-project
kanibako

# Start with a specific image
kanibako -i kanibako-systems:latest

# Open a plain bash shell (no agent)
kanibako shell

# Resume a previous conversation
kanibako resume
```

On first run in a new directory, kanibako initializes project state (shell
skeleton, credential copy, vault directories) and pulls the container image.
Subsequent runs reuse the existing state.

## Commands

| Command | Description |
|---------|-------------|
| `kanibako [start]` | Launch a Claude session in a container |
| `kanibako shell` | Open a bash shell in the container |
| `kanibako resume` | Resume with Claude's conversation picker |
| `kanibako stop [path\|--all]` | Stop running container(s) |
| `kanibako status` | Show project status (mode, paths, lock, image) |
| `kanibako config [key [value]]` | Get/set per-project configuration |
| `kanibako image [list\|rebuild]` | Manage container images |
| `kanibako box [list\|info\|orphan\|archive\|restore\|purge\|migrate\|duplicate]` | Project management |
| `kanibako workset [create\|list\|delete\|add\|remove\|info\|auth]` | Working set management |
| `kanibako init --local [-p DIR]` | Initialize decentralized project |
| `kanibako new --local <path>` | Create new decentralized project |
| `kanibako vault [snapshot\|list\|restore\|prune]` | Vault snapshot management |
| `kanibako env [list\|set\|get\|unset]` | Environment variable management |
| `kanibako shared [init\|list]` | Shared cache management |
| `kanibako setup` | Initial setup |
| `kanibako upgrade [--check]` | Update from git |
| `kanibako reauth` | Check auth and login if needed |

### Common flags

| Flag | Description |
|------|-------------|
| `-p, --project DIR` | Use DIR as the project directory (default: cwd) |
| `-i, --image IMAGE` | Use IMAGE for this run |
| `-N, --new` | Start a new conversation (skip `--continue`) |
| `-S, --safe` | Run without `--dangerously-skip-permissions` |
| `-c, --command CMD` | Use CMD as the container entrypoint |
| `--distinct-auth` | Use distinct credentials (no sync from host) |
| `-v, --verbose` | Show debug output (target detection, container command) |

## Project Modes

Kanibako supports three ways to organize project state.  The mode is inferred
automatically from context.

### Account-Centric (default)

Centralized store keyed by the SHA-256 hash of the project path.  Just `cd`
into any directory and run `kanibako`.

```
$XDG_DATA_HOME/kanibako/boxes/{hash}/          metadata + shell
{project}/vault/share-ro/                       read-only vault
{project}/vault/share-rw/                       read-write vault
```

### Working Set

Group related projects under a named working set with human-readable paths.

```bash
kanibako workset create my-research ~/worksets/research
kanibako workset add my-research ~/repos/paper-a --name paper-a
cd ~/worksets/research/workspaces/paper-a
kanibako
```

```
{workset}/boxes/{name}/             metadata + shell
{workset}/workspaces/{name}/        workspace
{workset}/vault/{name}/share-{ro,rw}/  vault
```

### Decentralized

All state lives inside the project directory itself.  Fully portable.

```bash
kanibako init --local         # in an existing directory
kanibako new --local ~/myproj # create a new one
```

```
{project}/.kanibako/          metadata + shell
{project}/vault/share-{ro,rw}/  vault
```

### Cross-mode migration

Convert between modes with `box migrate`:

```bash
kanibako box migrate --to decentralized          # AC -> decentralized
kanibako box migrate --to account-centric        # decentralized -> AC
kanibako box migrate --to workset --workset myws  # any -> workset
```

Or copy without modifying the source with `box duplicate --to ...`.

### Orphan detection

Find projects whose workspace directory no longer exists:

```bash
kanibako box orphan
```

Use `box migrate` to remap orphaned data to a new path, or `box purge` to
remove it.

## Container Images

All images are toolchain-only — the AI agent binary is mounted from the host.

| Image | Contents |
|-------|----------|
| `kanibako-base` | Python, nano, git, jq, ssh, gh, archives |
| `kanibako-systems` | + C/C++, Rust, assemblers, QEMU, debuggers |
| `kanibako-jvm` | + Java, Kotlin, Maven |
| `kanibako-android` | + Gradle, Android SDK (`sdkmanager` available) |
| `kanibako-ndk` | + Android NDK, systems toolchain |
| `kanibako-dotnet` | + .NET SDK 8.0 |
| `kanibako-behemoth` | All toolchains combined |

Images are pulled automatically from GHCR on first use.  If the pull fails,
kanibako falls back to a local build from the bundled Containerfiles.

```bash
kanibako image list                   # show local images
kanibako image rebuild                # rebuild current project's image
kanibako image rebuild --all          # rebuild all known images
```

## Container Layout

Inside the container, the agent sees:

```
/home/agent/                 persistent shell (bind mount)
  ├── .bashrc                shell config (with shell.d sourcing)
  ├── .profile               login profile
  ├── .shell.d/              drop-in init scripts (*.sh)
  ├── .claude/               agent credentials
  ├── .claude.json            agent settings
  ├── workspace/             project files (bind mount)
  ├── share-ro/              read-only vault (bind mount, optional)
  └── share-rw/              read-write vault (bind mount, optional)
```

## Shell Customization

### Environment variables

Set per-project or global environment variables that are passed to the
container via `-e` flags:

```bash
kanibako env set EDITOR vim              # project-level
kanibako env set --global EDITOR nano    # global (all projects)
kanibako env list                        # show merged (global + project)
kanibako env get EDITOR                  # show one value
kanibako env unset EDITOR                # remove from project
```

Project env vars override global ones.  Env files use Docker `.env` format
(one `KEY=VALUE` per line, `#` comments, no shell expansion).

### Custom prompt

The shell prompt is controlled by the `KANIBAKO_PS1` environment variable:

```bash
kanibako env set KANIBAKO_PS1 "(myproject) \u:\w\$ "
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

Existing shells from older kanibako versions are automatically upgraded to
support `shell.d/` on the next launch.

## Agent Configuration

Each agent instance gets a TOML configuration file at
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
```

**Sections:**
- `[agent]` — identity and defaults (name, shell template variant, default CLI args)
- `[state]` — runtime behavior knobs translated by the target plugin into CLI
  args and env vars (e.g. Claude maps `model` → `--model`)
- `[env]` — environment variables injected into the container
- `[shared]` — agent-level shared cache paths (mounted from the per-agent
  shared directory, independent of global shared caches)

## Shell Templates

Shell templates provide layered home directory initialization for new projects.
Templates live under `$XDG_DATA_HOME/kanibako/templates/` and are applied
once during project init.

**Resolution order** (for template variant `standard` and agent `claude`):
1. `templates/claude/standard/` — agent-specific template
2. `templates/general/standard/` — general fallback
3. None — no template files applied

The special variant `"empty"` always resolves to None (no files applied),
bypassing directory lookup entirely.  Use `shell = "empty"` in the agent
TOML to skip template initialization.

**Layering:**
1. `templates/general/base/` is copied first (common skeleton)
2. The resolved template overlays on top

The template variant is controlled by the `shell` field in the agent TOML
(defaults to `"standard"`).  To customize, create a directory under
`templates/` matching the desired structure and set `shell` in the agent TOML.

## Vault

Each project has optional read-only and read-write shared directories:

- **share-ro/** — files visible inside the container but not writable
  (documentation, reference data, prompt libraries)
- **share-rw/** — files that persist across sessions and can be modified
  (databases, build caches, generated artifacts)

In account-centric mode, vault directories live under your project and are
hidden inside the container via a read-only tmpfs overlay, so the agent cannot
see or modify vault metadata.

### Snapshots

Kanibako automatically creates a tar.xz snapshot of `share-rw/` before each
container launch.  Manage snapshots manually:

```bash
kanibako vault snapshot          # create a snapshot now
kanibako vault list              # show all snapshots
kanibako vault restore <name>    # restore from a snapshot
kanibako vault prune --keep 5    # keep only 5 most recent
```

### Disabling vault

```bash
kanibako init --local --no-vault   # decentralized project without vault
kanibako new --local ~/p --no-vault
```

## Target Plugin System

Kanibako is agent-agnostic.  All agent-specific logic lives in **target
plugins** — Python classes that implement the `Target` abstract base class.
Claude Code is the built-in target; other agents can be added as pip packages.
If no agent is detected, kanibako falls back to `no_agent` — a plain shell
with no agent binary or credentials.

A target handles:
1. Detecting the agent binary on the host
2. Mounting the binary/installation into the container
3. Syncing credentials between host and container
4. Building CLI arguments for the agent entrypoint

See [docs/writing-targets.md](docs/writing-targets.md) for the full developer
guide, and [examples/](examples/) for three graduated example plugins (Aider,
Codex CLI, Goose).

```bash
# Install a third-party target
pip install kanibako-target-aider

# Use a specific target
kanibako config target_name aider
kanibako start
```

## Configuration

```
Precedence: CLI flag > project.toml > kanibako.toml > hardcoded defaults
```

- **Global**: `$XDG_CONFIG_HOME/kanibako.toml`
- **Project**: `boxes/{hash}/project.toml`
- **Agents**: `$XDG_DATA_HOME/kanibako/agents/{id}.toml`
- **Templates**: `$XDG_DATA_HOME/kanibako/templates/`
- **Environment**: `$XDG_DATA_HOME/kanibako/env` (global),
  `boxes/{hash}/env` (project)

```bash
kanibako config --show              # show all resolved config
kanibako config image               # get current image
kanibako config image myimage:v2    # set project-level image
kanibako config --clear             # remove all project overrides
```

The global config supports a `[paths]` section to override data directory
layout, and a `[shared]` section for globally shared cache mounts:

```toml
[paths]
data_path = ""         # override XDG_DATA_HOME/kanibako
boxes = "boxes"        # project state subdirectory
agents = "agents"      # agent TOML subdirectory
shared = "shared"      # shared caches subdirectory
templates = "templates"

[shared]
pip = ".cache/pip"
cargo = ".cargo/registry"
npm = ".npm"
```

Shared caches are **lazy** — they are only mounted if the host directory exists.
Use `kanibako shared init` to create them:

```bash
kanibako shared init pip              # create global cache
kanibako shared init --agent claude plugins  # create agent-level cache
kanibako shared list                  # show configured caches and status
```

| Key | Default | Description |
|-----|---------|-------------|
| `container_image` | `ghcr.io/doctorjei/kanibako-base:latest` | Container image |
| `target_name` | `""` (auto-detect) | Agent target plugin (falls back to `no_agent` if none detected) |
| `paths_data_path` | `""` (XDG default) | Override data directory root |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v                    # unit tests (845)
pytest tests/ -v -m integration     # integration tests (35)

# Lint
ruff check src/ tests/

# Release
bump2version patch|minor|major      # auto-commits and tags
git push && git push --tags
```

## Architecture

| Module | Role |
|--------|------|
| `cli.py` | Argparse tree, main() entry, `-v` flag |
| `log.py` | Logging setup (`-v` enables debug output) |
| `config.py` | TOML config loading, merge logic |
| `paths.py` | XDG resolution, mode detection, project init |
| `container.py` | Container runtime (detect, pull, build, run, stop) |
| `shellenv.py` | Environment variable file handling |
| `snapshots.py` | Vault snapshot engine |
| `workset.py` | Working set data model and persistence |
| `credentials.py` | Credential sync between host and container |
| `freshness.py` | Non-blocking image digest comparison |
| `agents.py` | Agent TOML config: load, write, per-agent settings |
| `templates.py` | Shell template resolution and application |
| `targets/` | Agent plugin system (Target ABC + ClaudeTarget) |
| `commands/` | CLI subcommand implementations |
| `containers/` | Bundled Containerfiles |

## License

See [LICENSE](LICENSE) for details.

## Credits

LLMs were used as a tool in the development of this software.
