# Kanibako

Run AI coding agents in isolated, per-project sandboxes.  Kanibako gives
agents a safe place to work with real tools, real files, and real network
access — without risking your host system.  No Docker or Podman experience
required; kanibako handles all container operations behind the scenes.

Just `cd` into a project and run `kanibako`.  Each project gets its own
environment with its own shell config, credentials, and agent state that
persist across sessions.  Kanibako uses Podman or Docker under the hood,
but you never need to touch container commands yourself — setup, image
pulls, credential syncing, and teardown are all automatic.

Claude Code is supported via the built-in plugin; other agents can be added
via `pip install`.

## Features

- **Automatic sandboxing** — no Docker or Podman experience required;
  kanibako manages all container operations for you, no root needed
- **Per-project isolation** — each project gets its own shell, config, and
  credentials, keyed by directory hash
- **Three project modes** — account-centric (default), working set (grouped),
  and decentralized (self-contained)
- **Session continuity** — `kanibako start` defaults to `--continue`, picking
  up where you left off
- **Credential forwarding** — host credentials are synced into the container
  shell and written back after each session (path depends on agent plugin)
- **Vault** — per-project read-only and read-write shared directories, with
  automatic tar.xz snapshots before each launch
- **Shell customization** — per-project environment variables (`kanibako env`)
  and drop-in init scripts (`shell.d/`)
- **Agent configuration** — per-agent TOML config with template variant,
  default args, state knobs, env vars, and shared caches; per-project setting
  overrides via `box settings`
- **Shell templates** — layered home directory templates applied on first
  project init, with agent-specific and general variants
- **Shared caches** — global download caches (pip, cargo, npm, etc.) shared
  across projects; agent-level caches via agent TOML
- **Target plugin system** — agent-agnostic core (`kanibako-base` Python
  package); Claude Code plugin (`kanibako-plugin-claude`) is installed by default
- **Image freshness checks** — non-blocking digest comparison against GHCR on
  startup (24h cache)
- **Persistent sessions** — `kanibako connect` runs agents in tmux-backed
  containers that survive SSH disconnects; reattach seamlessly
- **Concurrency lock** — prevents two sessions from running in the same
  project simultaneously

## Prerequisites

- Python 3.11+
- [Podman](https://podman.io/) (recommended) or Docker — just needs to be
  installed; kanibako manages all container operations automatically
- An AI coding agent installed on the host (e.g.
  [Claude Code](https://docs.anthropic.com/en/docs/claude-code))

## Installation

```bash
# Standard install (base + Claude plugin)
uv tool install kanibako
# — or —
pip install kanibako

# Base only (no agent plugins — agent-agnostic shell mode)
pip install kanibako-base

# Development install
git clone https://github.com/doctorjei/kanibako.git
cd kanibako
pip install -e '.[dev]' -e packages/plugin-claude/

# First-time setup (creates config + pulls image)
kanibako setup
```

## Quick Start

```bash
# Start an agent session in the current directory
cd ~/my-project
kanibako

# Start with a specific image
kanibako -i kanibako-min:latest

# Open a plain bash shell (no agent)
kanibako shell

# Run a one-shot command in the container
kanibako shell -- echo hello

# Resume a previous conversation
kanibako resume
```

That's it — no `docker run`, no volume flags, no Containerfile.  On first run,
kanibako automatically pulls the container image, sets up the project
environment, and syncs your credentials.  Subsequent runs pick up where you
left off.

## Example: Python Project

The default `kanibako-oci` image (based on droste-fiber) includes Python, git,
gh, nano, jq, ripgrep, tmux, podman, and common dev tools.  This is enough for
most Python, JavaScript, and general scripting work.

```bash
# 1. Install kanibako and run first-time setup
pip install kanibako
kanibako setup

# 2. Create or clone a project
mkdir ~/my-flask-app && cd ~/my-flask-app
git init
# (or: git clone https://github.com/you/my-flask-app.git && cd my-flask-app)

# 3. Launch — that's it
kanibako
```

On the first launch, kanibako will:
- Pull the base container image (once, cached afterwards)
- Create an isolated environment for this project
- Copy your agent credentials into the sandbox
- Drop you into an agent session inside the container

The agent sees your project files in `~/workspace/` and has full access to
Python, git, and the other base tools.  When you exit, your project files
and agent state are preserved — next time you run `kanibako` in the same
directory, it picks up where you left off.

```bash
# Later: come back to the same project
cd ~/my-flask-app
kanibako              # resumes your previous session
kanibako -N           # or start a fresh conversation
```

## Example: C/Rust Project (template)

For projects that need compiled languages, create a template with the
toolchains you need:

```bash
# 1. Create a template with C/C++ and Rust
kanibako template create systems
# (inside: sudo apt install build-essential cmake gdb && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)
# exit when done

# 2. Use it for your project
cd ~/my-rust-project
kanibako -i kanibako-template-systems
```

After the first run, kanibako remembers the image choice for this project,
so you can just run `kanibako` next time.

See [Container Images](#container-images) for the base images and template
system.

## Commands

| Command | Description |
|---------|-------------|
| `kanibako [start]` | Launch an agent session in a container |
| `kanibako shell [-- cmd ...]` | Open a bash shell, or run a one-shot command |
| `kanibako resume` | Resume with the agent's conversation picker |
| `kanibako connect <project> [-N\|-S\|-i]` | Connect to a persistent session (remote access) |
| `kanibako stop [path\|--all]` | Stop running container(s) |
| `kanibako status` | Show project status (mode, paths, lock, image) |
| `kanibako config [key [value]]` | Get/set per-project configuration |
| `kanibako image [list\|rebuild]` | Manage container images |
| `kanibako box [list\|info\|orphan\|get\|set\|resource\|settings\|archive\|restore\|purge\|migrate\|duplicate\|forget]` | Project management |
| `kanibako workset [create\|list\|delete\|add\|remove\|info\|auth]` | Working set management |
| `kanibako init [path] [--local] [-i IMAGE]` | Initialize a kanibako project |
| `kanibako vault [snapshot\|list\|restore\|prune]` | Vault snapshot management |
| `kanibako env [list\|set\|get\|unset]` | Environment variable management |
| `kanibako shared [init\|list]` | Shared cache management |
| `kanibako helper [spawn\|list\|stop\|cleanup\|respawn\|send\|broadcast\|log]` | Child kanibako spawning and messaging |
| `kanibako template [create\|list\|delete]` | User template image management |
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
kanibako init --local              # in the current directory
kanibako init --local ~/myproj     # create and initialize a new directory
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

All images are built on [droste](https://github.com/doctorjei/droste) tiers
(Debian 13) with a thin kanibako layer on top (agent user, gh, ripgrep,
directory scaffolding).  The AI agent binary is mounted from the host.

| Image | Droste Base | Role |
|-------|-------------|------|
| `kanibako-min` | droste-seed | Minimal agent container |
| `kanibako-oci` | droste-fiber | Agent container + nested OCI host |
| `kanibako-lxc` | droste-thread | LXC system container host (via [kento](https://github.com/doctorjei/kento)) |
| `kanibako-vm` | droste-hair | VM host (via [kento](https://github.com/doctorjei/kento) + [tenkei](https://github.com/doctorjei/tenkei)) |

`kanibako-oci` is the default.  It includes podman and rootless container
infrastructure, so it can both run agents directly and host nested kanibako
containers.

### Ecosystem: droste, kento, kanibako

These three tools form a complementary stack:

- **[droste](https://github.com/doctorjei/droste)** builds layered OCI images
  in four tiers (seed → fiber → thread → hair), from minimal process containers
  up to full VM-bootable images.
- **[kento](https://github.com/doctorjei/kento)** converts OCI images into LXC
  system containers or QEMU VMs by mounting overlayfs directly from Podman's
  layer store — no image conversion or export needed.  On Proxmox hosts, kento
  auto-detects PVE and creates containers visible in the web UI.
- **kanibako** runs AI agents inside OCI containers with per-project isolation.

A typical deployment uses all three: droste builds the base images, kento stands
up `kanibako-lxc` (or `kanibako-vm`) as the always-on host, and kanibako runs
`kanibako-oci` agent containers nested inside it.

```
Any Linux host (kento installed)
  └── kanibako-lxc (LXC via kento) or kanibako-vm (QEMU VM via kento)
        └── rootless Podman
              ├── kanibako-oci (agent 1)
              ├── kanibako-oci (agent 2)
              └── kanibako-oci (agent 3)
```

Images are pulled automatically from GHCR on first use.  If the pull fails,
kanibako falls back to a local build from the bundled Containerfiles.

```bash
kanibako image list                   # show local images
kanibako image rebuild                # rebuild current project's image
kanibako image rebuild --all          # rebuild all known images
```

### Templates

Instead of pre-built toolchain variants, create custom templates by
installing tools interactively and committing the result:

```bash
kanibako template create jvm          # start from kanibako-oci, install tools
# (inside container: apt install openjdk-21-jdk maven, etc.)
# exit when done

kanibako template list                # show local templates
kanibako init --template jvm          # use the template for a new project
kanibako template delete jvm          # remove a template
```

Templates are standard OCI images — push them to any registry for sharing:

```bash
podman push kanibako-template-jvm ghcr.io/myorg/kanibako-template-jvm
```

## Host Deployment

For always-on deployments, use [kento](https://github.com/doctorjei/kento) to
stand up `kanibako-lxc` or `kanibako-vm` as the host.  Kento reads OCI images
directly from Podman's layer store — no export or conversion step.

### LXC host (Proxmox or standalone)

```bash
# Pull the image
podman pull ghcr.io/doctorjei/kanibako-lxc:latest

# Create and start the LXC (auto-detects Proxmox)
sudo kento container create kanibako-lxc --name kanibako-host
sudo kento container start kanibako-host
```

### VM host (QEMU)

```bash
podman pull ghcr.io/doctorjei/kanibako-vm:latest
sudo kento container create kanibako-vm --name kanibako-host --vm
sudo kento container start kanibako-host
```

### OCI nested host (alternative)

`kanibako-oci` also serves as a host container — it includes rootless
podman, so you can run kanibako itself inside it and spawn nested agent
containers.  This is useful when kento is not available.

### Pull and run (OCI nested host)

```bash
podman pull ghcr.io/doctorjei/kanibako-oci:latest

# Run with nested podman support
podman run --privileged -it \
    -v kanibako-data:/home/agent/.local/share/kanibako \
    -v kanibako-config:/home/agent/.config \
    ghcr.io/doctorjei/kanibako-oci:latest
```

The `--privileged` flag is required for rootless podman to work inside the
container. Alternatively, use `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined`
for a narrower permission set.

Install kanibako and plugins inside the host container:

```bash
pip install kanibako    # installs kanibako-base + kanibako-plugin-claude
kanibako setup
```

### Persistent state

Mount named volumes or host directories to preserve state across restarts:

| Mount target | Purpose |
|------|---------|
| `/home/agent/.local/share/kanibako` | Project state, agent configs, names |
| `/home/agent/.config` | kanibako.toml, podman storage config |
| `/home/agent/workspace` | Optional: bind a host project directory |

### Building locally

```bash
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-fiber:latest \
    -t kanibako-oci src/kanibako/containers/
```

## VM Variant

For bare-metal or VM deployments (Proxmox, KVM/libvirt, VirtualBox), kanibako
ships an Ansible playbook and per-provider creation scripts.  The playbook
mirrors the base + host Containerfiles — same packages, same user setup, same
rootless podman configuration.

### Ansible playbook (standalone)

Run directly against any Ubuntu host:

```bash
ansible-playbook host-definitions/ansible/playbook.yml \
    -i 'myhost,' -u root

# With Claude plugin
ansible-playbook host-definitions/ansible/playbook.yml \
    -i 'myhost,' -u root -e install_claude_plugin=true
```

### Proxmox

```bash
host-definitions/vm/create-proxmox-vm.sh \
    --ssh-key ~/.ssh/id_ed25519.pub --start

# With Claude plugin, custom resources
host-definitions/vm/create-proxmox-vm.sh \
    --ssh-key ~/.ssh/id.pub --claude \
    --memory 8192 --cores 4 --disk-size 64G --start
```

Key flags: `--vmid`, `--name`, `--memory`, `--cores`, `--disk-size`,
`--storage`, `--bridge`, `--ssh-key`, `--claude`, `--repo`, `--branch`,
`--start`.

### KVM / libvirt

Prerequisites: `virt-install`, `qemu-img`, `cloud-localds`
(`sudo apt install virtinst qemu-utils cloud-image-utils`).

```bash
host-definitions/vm/create-libvirt-vm.sh \
    --ssh-key ~/.ssh/id_ed25519.pub

# With Claude plugin
host-definitions/vm/create-libvirt-vm.sh \
    --ssh-key ~/.ssh/id.pub --claude --name my-kanibako
```

Key flags: `--name`, `--memory`, `--vcpus`, `--disk-size`, `--network`,
`--ssh-key`, `--claude`, `--repo`, `--branch`.

### Vagrant (VirtualBox)

No host-side Ansible needed — uses `ansible_local` provisioner inside the VM.

```bash
cd host-definitions/vm
vagrant up

# With Claude plugin
KANIBAKO_CLAUDE=true vagrant up
```

### After provisioning

All methods produce the same result: an Ubuntu VM with an `agent` user
(UID 1000), rootless podman, and kanibako installed.  SSH in and use kanibako
normally:

```bash
ssh agent@<vm-ip>
kanibako setup
cd ~/my-project && kanibako
```

## Smoke Tests

A portable smoke test suite validates that a deployed kanibako host
(container, LXC, or VM) is correctly configured.  The tests run on the
host itself — no external dependencies required.

```bash
# Run all tests
host-definitions/smoke-tests/smoke-test.sh

# Run specific tests
host-definitions/smoke-tests/smoke-test.sh 01 02

# List available tests
host-definitions/smoke-tests/smoke-test.sh --list
```

| Test | What it checks |
|------|---------------|
| `01-environment` | agent user, subuid/subgid, required packages (tmux, git, curl, python3), optional packages (rg, gh) |
| `02-podman` | rootless podman, storage driver, pull/run |
| `03-kanibako-cli` | kanibako installed, --version, --help, image list |
| `04-container-launch` | init, one-shot shell exec, stop/cleanup |
| `05-persistent-state` | files persist across container runs |
| `06-credentials` | agent plugin detection, credential path |
| `07-helpers` | comms directory mounted inside container |
| `08-networking` | DNS resolution, internet access from container |

Tests use TAP-style output with color (respects `NO_COLOR`).  Exit code
is 0 if all tests pass, 1 if any fail.

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

**Example directory layout** (for agent `claude`, variant `standard`):

```
templates/
├── general/
│   ├── base/              ← layer 1: always copied (common skeleton)
│   │   ├── .bashrc
│   │   └── .profile
│   └── standard/          ← layer 2 fallback (if no agent-specific dir)
└── claude/
    └── standard/          ← layer 2 preferred (agent-specific)
        ├── .claude/
        │   └── settings.json
        └── playbook/
            └── ONBOARD.md
```

**Important:** files go inside `claude/standard/`, not directly in `claude/`.
Placing files in `templates/claude/` (without the variant subdirectory) will
have no effect — the resolver looks for `templates/{agent}/{variant}/`.

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
kanibako init --local --no-vault         # decentralized project without vault
kanibako init --local ~/p --no-vault     # new directory, no vault
```

## Target Plugin System

Kanibako is agent-agnostic.  All agent-specific logic lives in **target
plugins** — Python classes that implement the `Target` abstract base class.
Claude Code is supported via `kanibako-plugin-claude` (installed by the
`kanibako` meta-package); other agents can be added as pip packages.
Plugins under the `kanibako.plugins` namespace are also discovered
automatically — even without pip metadata — so they travel with kanibako's
bind-mount into nested containers.
Install `kanibako-base` for agent-agnostic operation.
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

### Project settings

Use `box get` / `box set` to inspect and override per-project paths and settings
stored in `project.toml`:

```bash
kanibako box get shell                # print current shell path
kanibako box get layout               # print layout (simple/default/robust)
kanibako box set layout robust        # switch to robust layout
kanibako box set auth distinct        # disable credential sharing
kanibako box set shell /custom/shell  # override shell path (must be absolute)
kanibako box set vault_enabled false  # disable vault
```

Settable keys: `shell`, `vault_ro`, `vault_rw`, `layout`, `vault_enabled`, `auth`.
Readable keys include all settable keys plus: `mode`, `metadata`, `project_hash`,
`global_shared`, `local_shared`.

### Resource scoping

Agent resources (plugins, settings, caches, session data) have a default sharing
scope defined by the target plugin. Use `box resource` to view and override scopes
per-project:

```bash
kanibako box resource list            # show all resources with default/effective scope
kanibako box resource set plugins/ project  # make plugins project-local
kanibako box resource set settings.json shared  # share settings across projects
kanibako box resource unset plugins/  # remove override, revert to default
```

Scopes: `shared` (bind-mounted from global shared dir), `seeded` (copied from
shared on first init, then project-local), `project` (no sharing).

### Target settings

Target plugins declare runtime settings (like model and permission mode) with
defaults and optional constrained choices.  Use `box settings` to view effective
values and override them per-project:

```bash
kanibako box settings list             # show settings with default/effective/source
kanibako box settings get model        # print effective value
kanibako box settings set model sonnet # per-project override
kanibako box settings set access default  # constrained: permissive or default
kanibako box settings unset model      # remove override, revert to agent/default
```

Effective value resolution (highest wins):
1. Per-project override (`[target_settings]` in project.toml)
2. Agent config state (`[state]` in agent TOML)
3. Target plugin default (from `setting_descriptors()`)

| Key | Default | Description |
|-----|---------|-------------|
| `container_image` | `ghcr.io/doctorjei/kanibako-oci:latest` | Container image |
| `target_name` | `""` (auto-detect) | Agent target plugin (falls back to `no_agent` if none detected) |
| `paths_data_path` | `""` (XDG default) | Override data directory root |

## Helper spawning

Kanibako containers can spawn child instances for parallel workloads.
Each child gets its own directory tree, peer communication channels,
and spawn budget. Helpers are enabled by default — the host runs a
Unix socket hub alongside the director container, and helpers connect
to it for orchestration and messaging.

```bash
# Spawning and lifecycle
kanibako helper spawn                 # spawn a child with default budget
kanibako helper spawn --model sonnet  # child uses a different model
kanibako helper spawn --depth 2 --breadth 3  # custom spawn limits
kanibako helper list                  # show all helpers with status
kanibako helper stop 1                # stop helper 1
kanibako helper respawn 1             # relaunch a stopped helper
kanibako helper cleanup 1             # stop and remove helper 1
kanibako helper cleanup 1 --cascade   # also remove all descendants

# Messaging
kanibako helper send 1 "Analyze the auth module"   # send to helper 1
kanibako helper broadcast "Starting tests"          # send to all helpers

# Conversation log
kanibako helper log                   # display full message log
kanibako helper log --follow          # tail log in real-time
kanibako helper log --from 1          # filter by helper number
kanibako helper log --last 10         # show last 10 entries

# Opt out
kanibako start --no-helpers           # launch without helper support
```

**Architecture:** The kanibako CLI is bind-mounted into every container
(director and helpers), so `kanibako helper spawn/send/broadcast/log`
works inside containers. Each helper launches with `helper-init.sh` as
its entrypoint — the script registers with the hub, sources broadcast
startup scripts, then execs the agent command.

Two communication layers work together:
- **Directories** — file sharing (workspace, vault, peers, broadcast).
  Persistent, async. Good for sharing code, configs, results.
- **Socket** — control plane (spawn/stop) + real-time messaging
  (peer-to-peer, parent-child, broadcast). The host listener acts as
  a central message router.

**Logging:** All inter-agent messages are logged to a JSONL file on the
host. Each entry records sender, recipient(s), timestamp, and message
content. View the conversation in real-time with `kanibako helper log --follow`:
```
12:35:10  [0 → 1]  Analyze the auth module and report back.
12:36:45  [1 → 0]  Found 3 issues in the token refresh flow.
12:37:00  [0 → *]  Starting integration tests.
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

## Persistent Sessions & Remote Access

`kanibako connect` creates a persistent container session that survives
SSH disconnects. The container runs tmux as PID 1 — detaching or losing
the connection leaves the agent running. Reconnecting reattaches to the
same tmux session.

```bash
# Connect by project name (no cd required)
kanibako connect myproject

# Connect to a workset project
kanibako connect client/webapp

# Start a new conversation in a persistent session
kanibako connect myproject -N

# List available projects and their status
kanibako connect --list

# Stop a persistent session
kanibako stop -p /path/to/project
```

**Lifecycle:**
- First `connect` → creates a detached container with tmux, then attaches
- Subsequent `connect` → reattaches to the running container
- SSH disconnect → container keeps running; reconnect with `connect`
- `kanibako stop` → stops and removes the persistent container
- Agent exits → tmux session ends → container stops

**Interactive guard:** If a persistent container exists for a project,
`kanibako start` refuses to launch (to prevent conflicts). Use
`kanibako connect` to reattach or `kanibako stop` to end it.

### SSH integration

Set up SSH forced commands to map SSH keys directly to projects.
Each key connects to a specific project — no shell access needed.

**Per-key routing** in `~/.ssh/authorized_keys`:

```
command="kanibako connect myproject" ssh-ed25519 AAAA... user@laptop-myproject
command="kanibako connect client/webapp" ssh-ed25519 AAAA... user@laptop-webapp
```

**Dedicated SSH config** on the client:

```
Host myproject
    HostName remote-server.example.com
    User kanibako
    IdentityFile ~/.ssh/id_myproject
```

Then just `ssh myproject` to connect directly to the Claude session.

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
- The kanibako user only needs access to `kanibako connect` — no shell
  required (`ForceCommand` handles routing)
- Credentials are refreshed on every reattach; if tokens expire, the
  agent prompts for re-auth via URL

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v                    # unit tests (1395)
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
| `container.py` | Container runtime (detect, pull, build, run, stop, detach) |
| `shellenv.py` | Environment variable file handling |
| `snapshots.py` | Vault snapshot engine |
| `workset.py` | Working set data model and persistence |
| `credentials.py` | Credential sync between host and container |
| `freshness.py` | Non-blocking image digest comparison |
| `names.py` | Project name registry (names.toml): register, resolve, assign |
| `agents.py` | Agent TOML config: load, write, per-agent settings |
| `templates.py` | Shell template resolution and application |
| `targets/` | Agent plugin system (Target ABC + NoAgentTarget; ClaudeTarget in `kanibako-plugin-claude`) |
| `plugins/` | Namespace package for built-in and bind-mounted plugins |
| `helpers.py` | B-ary numbering, spawn budget, directory/channel creation |
| `helper_listener.py` | Host-side hub: socket server, message routing, logging |
| `helper_client.py` | Container-side socket client for hub communication |
| `commands/` | CLI subcommand implementations |
| `containers/` | Bundled Containerfiles |
| `scripts/` | Bundled scripts: `helper-init.sh` (entrypoint wrapper), `kanibako-entry` (container CLI) |

## License

See [LICENSE](LICENSE.md) for details.

## Credits

LLMs were used as a tool in the development of this software.
