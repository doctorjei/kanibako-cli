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
  credentials
- **Three project modes** — local (default), workset (grouped),
  and standalone (self-contained)
- **Session continuity** — `kanibako start` defaults to `--continue`, picking
  up where you left off
- **Persistent sessions** — agents run in tmux-backed containers by default,
  surviving SSH disconnects; reattach with `kanibako start`
- **Credential forwarding** — host credentials are synced into the container
  shell and written back after each session (path depends on agent plugin)
- **Vault** — per-project read-only and read-write shared directories, with
  automatic tar.xz snapshots before each launch
- **Shell customization** — per-project environment variables (`box config
  env.*`) and drop-in init scripts (`shell.d/`)
- **Agent configuration** — per-agent TOML config with template variant,
  default args, state knobs, env vars, and shared caches; per-project setting
  overrides via `box config`
- **Shell templates** — layered home directory templates applied on first
  project init, with agent-specific and general variants
- **Shared caches** — global download caches (pip, cargo, npm, etc.) shared
  across projects; agent-level caches via agent TOML
- **tweakcc integration** — optional patching of agent binaries via tweakcc
  with config layering (agent defaults → external config → inline overrides),
  flock-based binary caching, and automatic propagation to helpers
- **Target plugin system** — agent-agnostic core (`kanibako-base` Python
  package); Claude Code plugin (`kanibako-plugin-claude`) is installed by default
- **Image freshness checks** — non-blocking digest comparison against GHCR on
  startup (24h cache)
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
```

On first use, kanibako automatically creates its config and data directories.
No explicit setup step is needed.

## Quick Start

```bash
# Start an agent session in the current directory
cd ~/my-project
kanibako

# Start with a specific image
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

That's it — no `docker run`, no volume flags, no Containerfile.  On first run,
kanibako automatically pulls the container image, sets up the project
environment, and syncs your credentials.  Subsequent runs pick up where you
left off.

## Example: Python Project

The default `kanibako-oci` image (based on droste-fiber) includes Python, git,
gh, nano, jq, ripgrep, tmux, podman, and common dev tools.  This is enough for
most Python, JavaScript, and general scripting work.

```bash
# 1. Install kanibako
pip install kanibako

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

## Example: C/Rust Project (custom image)

For projects that need compiled languages, create a custom image with the
toolchains you need:

```bash
# 1. Create a custom image with C/C++ and Rust
kanibako rig create systems
# (inside: sudo apt install build-essential cmake gdb && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh)
# exit when done

# 2. Use it for your project
cd ~/my-rust-project
kanibako --image kanibako-template-systems
```

After the first run, kanibako remembers the image choice for this project,
so you can just run `kanibako` next time.

See [Container Images](#container-images) for the base images and custom image
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
| `rig` | Rig management — container images (create, list, info, rm, rebuild) |
| `workset` | Project grouping (create, list, connect, disconnect, config, ...) |
| `crab` | Crab (agent) management (list, config, reauth, helper, fork) |
| `system` | Global configuration and self-update (info, config, upgrade) |

**Aliases:** `agent` → `crab`, `image` → `rig`, `container` → `box`

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
| `box info` / `box inspect` | Project details (mode, paths, lock, image) |
| `box rm` / `box delete` | Remove project (`--purge` deletes metadata, `--force` skips confirm) |
| `box config` | View or modify project configuration |

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

### `system` Subcommands

| Subcommand | Description |
|------------|-------------|
| `system info` / `system inspect` | System details (version, runtime, paths) |
| `system config` | View or modify global configuration |
| `system upgrade` | Self-update (`--check` for dry run) |

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
| `--image IMAGE` | Container image override |
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
kanibako rig list                     # show local rigs
kanibako rig rebuild                  # rebuild current project's rig
kanibako rig rebuild --all            # rebuild all known rigs
```

### Custom Images

Create custom images by installing tools interactively and committing
the result:

```bash
kanibako rig create jvm               # start from kanibako-oci, install tools
# (inside container: apt install openjdk-21-jdk maven, etc.)
# exit when done

kanibako rig list                     # show local rigs
kanibako rig rm jvm                   # remove a custom rig
```

Custom images are standard OCI images — push them to any registry for sharing:

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
# OCI container (default)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-fiber:latest \
    -t kanibako-oci src/kanibako/containers/

# LXC system container (requires VARIANT=lxc for systemd/networking fixes)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-thread:latest \
    --build-arg VARIANT=lxc \
    -t kanibako-lxc src/kanibako/containers/

# VM host (requires VARIANT=vm)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-hair:latest \
    --build-arg VARIANT=vm \
    -t kanibako-vm src/kanibako/containers/
```

The `VARIANT` build arg enables variant-specific configuration in the shared
Containerfile.  OCI builds don't need it (defaults to `oci`).  LXC builds
add systemd unit masking, DHCP networking, and cgroupfs for rootless Podman.
VM builds get the same LXC fixes plus a systemd entrypoint.

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

[tweakcc]
# enabled = false           # enable tweakcc binary patching
# config = "~/.tweakcc/config.json"  # external tweakcc config file
```

**Sections:**
- `[agent]` — identity and defaults (name, shell template variant, default CLI args)
- `[state]` — runtime behavior knobs translated by the target plugin into CLI
  args and env vars (e.g. Claude maps `model` → `--model`)
- `[env]` — environment variables injected into the container
- `[shared]` — agent-level shared cache paths (mounted from the per-agent
  shared directory, independent of global shared caches)
- `[tweakcc]` — optional tweakcc integration for binary patching (see below)

Manage agent settings via the CLI:

```bash
kanibako crab list                    # list configured crabs
kanibako crab config model            # show effective model
kanibako crab config model=sonnet     # set crab-level default
```

### tweakcc Integration

tweakcc patches Claude Code's embedded cli.js bundle to customize system
prompts, toolsets, and UI behavior.  When enabled in the agent config,
kanibako orchestrates the full patching lifecycle:

1. Computes a content hash of the host binary's embedded cli.js
2. Merges config layers: kanibako defaults → external config file → inline overrides
3. Checks the flock-based binary cache (at `$XDG_CACHE_HOME/kanibako/tweakcc/`)
4. On cache miss, copies the binary and invokes tweakcc to patch it
5. Mounts the cached patched binary into the container
6. Propagates the cache to helper containers

**Note:** tweakcc is a Node.js package and requires Node.js on the host (or
in the container where patching runs).  The patching invocation is under
active development — see the implementation plan for current status.

Enable in the agent TOML:

```toml
[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"
```

Inline settings override the external config:

```toml
[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"

[tweakcc.settings.misc]
mcpConnectionNonBlocking = true
```

If patching fails (missing tweakcc, bad binary, etc.), kanibako falls back
gracefully to the unpatched binary.

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

In local mode, vault directories live under your project and are hidden inside
the container via a read-only tmpfs overlay, so the agent cannot see or modify
vault metadata.

### Snapshots

Kanibako automatically creates a tar.xz snapshot of `share-rw/` before each
container launch.  Manage snapshots manually:

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
kanibako box config target_name=aider
kanibako start
```

## Configuration

```
Precedence: CLI flag > project.toml > workset config > agent config > kanibako.toml > defaults
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
- **Agents**: `$XDG_DATA_HOME/kanibako/agents/{id}.toml`
- **Templates**: `$XDG_DATA_HOME/kanibako/templates/`

### Configuration keys

| Key | Default | Description |
|-----|---------|-------------|
| `start_mode` | `continue` | Default start mode (continue/new/resume) |
| `model` | platform default | Agent model name |
| `autonomous` | `true` | Enable autonomy override |
| `persistence` | `persistent` | Session type (persistent/ephemeral) |
| `image` | `kanibako-oci:latest` | Container image |
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
agents = "agents"      # agent TOML subdirectory
shared = "shared"      # shared caches subdirectory
templates = "templates"

[shared]
pip = ".cache/pip"
cargo = ".cargo/registry"
npm = ".npm"
```

Shared caches are **lazy** — they are only mounted if the host directory exists.

## Helper Spawning

Kanibako containers can spawn child instances for parallel workloads.
Each child gets its own directory tree, peer communication channels,
and spawn budget. Helpers are enabled by default — the host runs a
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

**Architecture:** The kanibako CLI is bind-mounted into every container
(director and helpers), so `kanibako crab helper spawn/send/broadcast/log`
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
content. View the conversation in real-time with `kanibako crab helper log --follow`:
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

## Persistent Sessions

`kanibako start` runs agents in tmux by default (`--persistent` mode).
The container uses tmux as PID 1 — detaching or losing the connection
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
- First `start` → creates a detached container with tmux, then attaches
- Subsequent `start` → reattaches to the running container
- SSH disconnect → container keeps running; reconnect with `start`
- `kanibako stop` → stops and removes the container
- Agent exits → tmux session ends → container stops

### SSH integration

Set up SSH forced commands to map SSH keys directly to projects.
Each key connects to a specific project — no shell access needed.

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
- The kanibako user only needs access to `kanibako start` — no shell
  required (`ForceCommand` handles routing)
- Credentials are refreshed on every reattach; if tokens expire, the
  agent prompts for re-auth via URL

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v                    # unit tests (1813)
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

| Module | Role |
|--------|------|
| `cli.py` | Argparse tree, main() entry, `-v` flag |
| `log.py` | Logging setup (`-v` enables debug output) |
| `config.py` | TOML config loading, merge logic |
| `config_interface.py` | Unified config engine (get/set/reset/show for all levels) |
| `paths.py` | XDG resolution, mode detection, project init |
| `container.py` | Container runtime (detect, pull, build, run, stop, detach) |
| `shellenv.py` | Environment variable file handling |
| `snapshots.py` | Vault snapshot engine |
| `workset.py` | Working set data model and persistence |
| `names.py` | Project name registry (names.toml): register, resolve, assign |
| `agents.py` | Agent TOML config: load, write, per-agent settings |
| `templates.py` | Shell template resolution and application |
| `freshness.py` | Non-blocking image digest comparison |
| `targets/` | Agent plugin system (Target ABC + NoAgentTarget; ClaudeTarget in `kanibako-plugin-claude`) |
| `plugins/` | Namespace package for built-in and bind-mounted plugins |
| `auth_parser.py` | Parse OAuth URL and verification code from `claude auth login` output |
| `auth_browser.py` | Automated OAuth refresh via headless Playwright browser |
| `browser_state.py` | Persistent browser context (cookies, localStorage) for OAuth session reuse |
| `browser_sidecar.py` | On-demand headless Chrome container for agent web access |
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
