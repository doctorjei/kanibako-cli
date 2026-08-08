# Kanibako (蟹箱)

Safe, persistent workspaces for AI coding agents.

Don't be crabby -- pick up where you left off.

---

Kanibako gives AI coding agents a safe place to work -- real tools, real files,
real network access -- without risking your host system.  Each project gets its
own isolated container with persistent state: shell config, credentials, and
agent sessions that survive reboots and SSH disconnects.

CRAB: **C**ontained **R**untime **A**gent in a **B**ox.

New here? Start with [GETTING_STARTED.md](GETTING_STARTED.md) for a five-minute
walkthrough from install to your first agent session.

No Docker or Podman experience required.  Just `cd` into a project and run
`kanibako`.  Setup, rig pulls, credential syncing, and teardown are automatic.
Claude Code, Codex, and Goose ship as first-class agent plugins (all installed
by the `kanibako` meta-package); other agents can be added as plugins.

## Quick Start

```bash
# Install & Setup
pipx install kanibako   # uv and pip also work!
kanibako setup

# Create a box for your project - a one-time step per project 'box':
cd ~/my-project && kanibako create

# Launch the agent session -- that's it!
kanibako

# Optional - launch in VS Code (new, experimental):
kanibako code
```

## Features

- **Seamless Isolation** -- rootless containers with no host access, so its safe to give agents autonomy
- **Automatic Sandboxing** -- no Docker or Podman experience required - it's all automated
- **Session Continuity** -- `kanibako start` by default to picks up where you left off
- **Painless Persistence** -- boxes launch in tmux by default - so you can detach and leave agents running
- **Credential Forwarding** -- host credentials optionally synced into the box from host
- **Customized, Per-Box** -- each box gets its own home, config, & credentials (multiple sharing modes)
- **Layered Customizations** -- per box/workset/agent and global customizations (settings, scripts, etc.)
- **Nuanced Custrols** --per box/workset/agent and global environment, mount, and share controls
- **Helper / Subagents** -- subagent spawning system for distributed workloads
- **Diagnostics** -- `kanibako system diagnose` (runtime, images, agents, & storage)
- **Plugins** -- agent-agnostic core (`kanibako-cli`) with custom plguin system for others harnesses

## Using Kanibako
See the [Quick Start](#quick-start) Guide for get started right away; below are the nitty-gritty details.

### Prerequisites

- Python 3.11+
- [Podman](https://podman.io/) 4.3+ (recommended) or Docker (Containers managed automatically)
- An agent harness or full agent system (e.g., Claude Code, Codex, or Goose)

### Install from Source

```bash
git clone https://github.com/doctorjei/kanibako-cli.git
cd kanibako-cli
pip install -e '.[dev]' -e packages/agent-claude/ -e packages/agent-codex/ -e packages/agent-goose/
```

On first use, Kanibako automatically creates its config and data directories.
Run `kanibako setup` to verify your environment and pick a default agent. If you
have **more than one** agent harness installed (the meta-package ships all three),
you **must** choose one — either with `setup` or per-run with `--agent <name>` —
otherwise `kanibako` will error rather than guess. With a single agent installed
it is used automatically. See [Agent Selection](#agent-selection).

No `docker run`, no volume flags, no Containerfile. The first launch pulls the
container rig and copies in your agent credentials; after that, `kanibako` in the
same directory picks up right where you left off.

More ways to launch:

```bash
kanibako -N                            # start a fresh conversation
kanibako shell                         # plain bash shell, no agent
kanibako shell -- echo hello           # run a one-shot command in the box
kanibako --agent codex                 # choose the agent for this run
kanibako --image kanibako-min:latest   # launch on a specific rig
```

Creating a box is deliberate: a launch (`kanibako` / `start` / `code` / `shell`)
never invents one, so a typo'd path or wrong directory can't silently make a box.

### Example: Python Project

The default `kanibako-oci` rig (based on droste-fiber) includes Python, git,
gh, nano, jq, ripgrep, tmux, Podman, and common dev tools.  This is enough for
most Python, JavaScript, and general scripting work.

```bash
pipx install kanibako                       # installation
mkdir ~/my-flask-app && cd ~/my-flask-app   # Create project
git init                                    # New git repo (alt: clone a project)
kanibako create                             # Create new box for project (one-time)
kanibako                                    # launch (app done!)
```

`kanibako create` builds an isolated environment. On first launch, Kanibako will automatically...
- Pull the base container rig (once, cached afterwards)
- Copy your agent credentials into the sandbox
- Drop you into an agent session inside the container

The agent sees your project files in `~/workspace/` and has full access to standard tools. On exit,
project files and agent state are preserved; on next run, `kanibako` will pick up where you left off.

```bash
# Come back to the same project latyer...
cd ~/my-flask-app
kanibako              # resumes your previous session
kanibako -N           # or start a fresh conversation
```

### Example: C/Rust Project (custom rig)

For projects that need compiled languages, create a custom rig with needed tools:

```bash
kanibako rig prep systems                          # Build bundled C/C++ + Rust toolchain template
cd ~/my-rust-project                               # Enter the project directory
kanibako create --image kanibako-template-systems  # Create a box using the systems template
kanibako                                           # Start & enter the box
```

See [Container Rigs](#container-rigs) for the base rigs and custom rig creation.

## Commands

Kanibako organizes commands into four management groups plus eight top-level
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
| `kanibako code [project]` | *(top-level only)* | Open VS Code attached to the box (see [VS Code Integration](#vs-code-integration)) |

### Management Commands

| Command | Description |
|---------|-------------|
| `box` | Box lifecycle (create, list, start, stop, shell, config, diagnose, helper, fork, archive, ...) |
| `rig` | Rig management -- container images (create, list, info, rm, rebuild) |
| `workset` | Project grouping (create, list, connect, disconnect, config, ...) |
| `agent` | Agent management (list, info, config, reauth) |
| `system` | Global configuration, diagnostics, and self-update |

**Aliases:** `image` -> `rig`, `container` -> `box`

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
| `box create [path]` | Create project (`--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth`, `--allow-home`) |
| `box list` / `box ls` | List projects (`--all`, `--orphan`, `-q`) |
| `box info` / `box inspect` | Project details (mode, paths, lock, rig) |
| `box rm` / `box delete` | Remove project (`--purge` deletes metadata, `--force` skips confirm) |
| `box set` / `box get` / `box show` / `box reset` | View or modify project configuration |
| `box diagnose [project]` | Check project box health |

**Relocation & conversion:**

| Subcommand | Description |
|------------|-------------|
| `box remap <old> [<new>]` | Update kanibako's recorded path after you moved the folder yourself (records only, no file move; `<new>` defaults to `./`) |
| `box move <old> <new>` / `box mv` | Physically relocate the workspace (both paths required; a target flag also changes ownership) |
| `box convert [<old>] (--default \| --standalone \| --workset <ws>)` | Change ownership/mode (in-place by default; `--move [path]` relocates, bare `--move` moves into the target workset; `--name` renames) |
| `box duplicate <source> [dest]` | Copy project (`--name`, `--bare`, `--force`) |
| `box archive [project]` | Pack session data to .txz (`--all`, `--allow-uncommitted`, `--allow-unpushed`, `--force`) |
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
| `rig prep [name]` / `rig prepare` | Materialize a rig: build a bundled template or pull a prefab (`--force` to re-prep, `--all` for every local rig) |
| `rig add <source>` | Register a foreign rig (prefab image ref/tar or Containerfile); does not pull/build — run `rig prep` after (`--name`, `--as`, `--force`) |
| `rig extend <name> --from <rig>` | Build a custom rig interactively from a foundation rig, committed as `kanibako-rig-<name>` (`--always-commit`, `--no-commit-on-error`) |
| `rig export <name>` | Export an extended rig to a portable `.rig.tgz` (`--out`) |
| `rig import <file>` | Import an extended rig from a `.rig.tgz` |
| `rig list` / `rig ls` | List available rigs (`-q`, `--json`) |
| `rig info` / `rig inspect` | Rig details (source, size, recoverability) |
| `rig rm` / `rig delete` | Remove rig (`--force`) |
| `rig diagnose` | Check rig (image) status |

### `workset` Subcommands

| Subcommand | Description |
|------------|-------------|
| `workset create [path]` | Create working set (`--name`, `--standalone`, `--image`, `--no-vault`, `--distinct-auth`) |
| `workset list` / `workset ls` | List working sets (`-q`) |
| `workset info` / `workset inspect` | Working set details |
| `workset rm` / `workset delete` | Remove working set (`--purge`, `--force`) |
| `workset set` / `workset get` / `workset show` / `workset reset` | View or modify workset configuration (use `workset set default <key>=<value>` for default-workset defaults) |
| `workset connect <workset> [source]` | Add project to working set (`--name`) |
| `workset disconnect <workset> <project>` | Remove project from working set (`--force`) |

### `agent` Subcommands

| Subcommand | Description |
|------------|-------------|
| `agent list` / `agent ls` | List configured agents (`-q`) |
| `agent info` / `agent inspect` | Agent configuration details |
| `agent set` / `agent get` / `agent show` / `agent reset` | View or modify agent configuration |
| `agent reauth [project]` | Refresh credentials |

### `box helper` / `box fork` Subcommands

The runtime helper and fork verbs (formerly under `crab`) now live under `box`:

| Subcommand | Description |
|------------|-------------|
| `box helper spawn` | Spawn child instance (`--depth`, `--breadth`, `--model`, `--image`) |
| `box helper list` / `helper ls` | List helpers (`-q`) |
| `box helper stop <n>` | Stop a helper |
| `box helper respawn <n>` | Respawn a stopped helper |
| `box helper cleanup <n>` | Clean up helper (`--cascade`) |
| `box helper send <n> <msg>` | Message a helper |
| `box helper broadcast <msg>` | Message all helpers |
| `box helper log` | View message log (`-f`, `--from`, `--tail`) |
| `box fork <name>` | Fork project into a new directory |
| `box diagnose` | Check box + agent status and configuration |

### `system` Subcommands

| Subcommand | Description |
|------------|-------------|
| `system info` / `system inspect` | System details (version, runtime, paths) |
| `system set` / `system get` / `system show` / `system reset` | View or modify global configuration |
| `system upgrade` | Self-update (`--check` for dry run) |
| `system diagnose` | Check system health (runtime, images, agents, storage) |

## Preview / Experimental Features
- **Vault snapshots** -- per-box read-only and read-write shared
  directories with smart snapshot strategy detection (reflink, hardlink,
  or tar.xz depending on filesystem)
- Build rigs interactively with: kanibako rig extend my-systems --from kanibako-oci)

## VS Code Integration

> **⚠ Experimental.** VS Code integration is **experimental** in this release.
> The commands below work, but multi-surface behavior is still hardening — in
> particular, kanibako does **not yet enforce a single active agent per box**.
> Running the agent **panel** and a **CLI agent** on the *same box at the same
> time* silently **forks the session**: both write the same agent history and
> one surface's turns can be lost with no warning. **Use one agent surface per
> box at a time** until single-writer enforcement lands.
>
> **Codex-specific sharpness:** the Codex panel's sidebar lists the box's
> recorded CLI sessions, and **one click resumes a CLI session in a second
> process** — the exact silent-fork above, one click away (codex records
> sessions with no cross-process locking). Codex offers no knob to hide the
> CLI's sessions from the panel, so this is a documented limit: while a CLI
> codex runs in the box, don't resume its session from the panel sidebar
> (start a new panel conversation instead). The box's permission parity
> (`approval_policy`/`sandbox_mode`) and kanibako's managed hooks reach the
> panel automatically via the shared `~/.codex/config.toml`.

`kanibako code [project]` opens your **host** VS Code attached to the box
(Dev Containers "Attach to Running Container"), at the box's `~/workspace`.
The box is auto-started detached if it isn't running, and it stays up when
you close the window. The box agent's editor extension is installed into the
attached window automatically.

Requirements: the `code` CLI on your PATH, the Dev Containers extension
(`ms-vscode-remote.remote-containers`), and
`"dev.containers.dockerPath": "podman"` in your VS Code user settings.
`kanibako system diagnose` checks all three.

### Remote boxes

```bash
kanibako code --remote <host> <project>
```

attaches your **local** VS Code to a box on a **remote** kanibako host —
no relay service, no VS Code on the remote host. `<host>` is an opaque SSH
destination resolved by your own `~/.ssh/config`. kanibako runs the box
lifecycle on the remote host over plain SSH, and the container-engine leg
rides a kanibako-owned SSH tunnel: one OpenSSH `ControlPersist` master
forwards the remote rootless podman socket to a local unix socket, and local
podman dials that socket. podman's own golang `ssh:` transport is **not**
used, so no `ssh-agent` is required, your `~/.ssh/config` is fully honored
(aliases, ProxyJump, ports, `IdentityFile`), and per-call SSH handshake
overhead is gone once the first call warms the tunnel.

Requirements:

- the same kanibako **version** on both hosts, plus a local `code` CLI and
  local `podman` (the `--remote` client);
- the rootless podman API socket on the remote host:

  ```bash
  systemctl --user enable --now podman.socket
  loginctl enable-linger "$USER"
  ```

On first `--remote` use, kanibako asks to point
`dev.containers.dockerPath` at its dispatch wrapper (local attaches are
unaffected — the wrapper is a pass-through to `podman` except for remote
attach windows).

If a remote attach fails, check the dispatch log at
`~/.local/state/kanibako/vscode-remote/dispatch.log` (or under
`$XDG_STATE_HOME`) — one line per engine call showing the resolved route and
context.

> **First attach:** the first time VS Code attaches to a given container it
> shows a workspace-trust dialog you must click through. In headless or
> automated setups this can look like a silent hang — bring the VS Code
> window forward and confirm the trust prompt to proceed.

## Common Flags

### Agent Flags (on `start`)

| Flag | Description |
|------|-------------|
| `-N, --new` | Start a new conversation |
| `-C, --continue` | Continue the most recent conversation (default) |
| `-R, --resume` | Accepted for compatibility; resolves to `--continue` (the in-session resume picker remains reachable from within the agent) |
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
| `--restart` | Stop the box first, then start it fresh (`start` only) — the way to apply flags to a box that is already running |

⚑ **Against a box that is already RUNNING, most of these are refused, not applied.** A running
container keeps the creation-time settings and the agent session it was launched with, so
`--image`, `-e` (except where something in the box will actually apply it, below), `--no-helpers`,
`--no-auto-auth`, `--browser`, `--share-images`, an explicit `--persistent`/`--ephemeral`, and the
agent flags `-N -C -R -M -A -S` produce an error naming the cure rather than being silently
dropped. Use `kanibako --restart [box]` to stop and relaunch with them in force. Two exceptions:
`--detach`/`--print-container`/`--warm-only` are honoured; and anything that starts a **second
process inside the running box** — `--entrypoint CMD`, or `kanibako shell --persistent` at a box
that is running an agent — runs with `-e` applied, so `-e` is refused only where nothing would
apply it.

### Global Flags

These parse on every command (passing one to a command it doesn't apply to is an
error, not a silent no-op).

| Flag | Description |
|------|-------------|
| `-v, --verbose` | Show debug output (target detection, container command) |
| `--agent NAME` | Top-precedence, ephemeral (this-invocation) agent override; wins over the cascade. See [Agent Selection](#agent-selection). |
| `--box NAME-OR-PATH` | Universal subject/anchor selector -- act on a box that isn't your cwd, by box name (precedence) or path. See [Agent Selection](#agent-selection). |

> **`setup` keeps its own `--agent`** flag (it persists the chosen default rather
> than overriding for one run).

#### `--box`: operate on any box

`--box` substitutes for being in the box's directory: `kanibako stop --box myproj`,
`kanibako box set --box myproj model=opus`. The value is a **box name (resolved
first) or a path**. It is the *subject* the command acts on, and stays orthogonal to
the `move`/`convert` *destination* group, so they coexist:

```bash
kanibako box convert --box mybox --standalone   # convert box "mybox" to standalone
```

When both a positional target and `--box` are given: same target → warn + continue;
different → error.

**Box-name rules.** New names (creation / `--name`) allow unicode letters/digits plus
interior `_ - .`; blocked are control chars, whitespace, ASCII punctuation other than
`_ - .`, `.`/`..`, a leading `-`/`.`, a trailing `.`/whitespace, and length over 64.
Uppercase ASCII folds to lowercase. Pre-existing non-conforming names still resolve
but are flagged.

## Agent Selection

Which agent a command uses is resolved by a single cascade (highest precedence
first):

```
--agent  >  box pref  >  workset pref  >  system.agent
```

A box or workset REQUESTS an agent with `pref.system.agent` (a request to set a key
that resolves earlier than the file making it); `system.agent` is the host-global
default and `--agent` is the ephemeral per-launch override. `kanibako create --agent
<name>` persists the request into the new box, so a plain `kanibako start` runs it.

If a name resolves, it is used (and an error is raised if that agent's plugin
isn't installed). If **nothing** resolves, the **installed-agent count** decides --
with no ordering and no tie-break:

| Installed agents | Behavior |
|---|---|
| exactly 1 | used implicitly (unambiguous) |
| 0 | error -- install an agent plugin (or use `kanibako shell`) |
| **2+** | error -- pick one with `kanibako setup` or `--agent <name>` |

> **Behavior change (1.6.0).** Earlier versions would arbitrarily launch the
> *first* installed agent when none was chosen. With the meta-package (all three
> agents installed) that produced a surprising, machine-dependent pick. Now 2+
> installed agents with no choice is an **error** -- you select deliberately. A
> single installed agent still launches with no extra step.

This resolution is **uniform** across every agent-requiring command (`start`,
`box start`, `agent reauth`, ...). `kanibako shell` is the **sole** exception: it
needs no agent and never errors on resolution -- the way to reach a box's container
when no agent is configured.

### Choosing a default agent

`kanibako setup` is where you pick the host-global default (`system.agent`).
On a TTY it shows a numbered menu of detected agents (the only interactive prompt in
the CLI); `setup --agent <name>` sets it non-interactively. A "skip" option is
offered -- with 2+ agents it warns that a bare launch will then fail and asks you to
confirm. Non-TTY runs (CI / headless) skip the prompt gracefully.

`setup` records a completion marker; agent-requiring commands print a non-blocking
nudge to run `setup` if it has never been run, then proceed.

Because `system.*` keys are **file-only** (see [Configuration](#configuration)),
the default agent is *not* settable via `kanibako system set` -- use `setup` or
edit `global/settings.yaml` directly.

## Project Modes

Kanibako supports three ways to organize box state (`box.mode`).  The mode is
inferred automatically from context.

Two of those modes -- **primary** and **named** -- are flavors of the same idea:
a **workset**, a shared root that holds the boxes, vaults, channels, and a
group-level settings/auth tier that member boxes inherit.  The primary workset
is simply the implicit default group; a named workset is rooted at a directory
you choose.  **Standalone** is the odd one out -- its state lives inside the
project directory rather than in a group root.

### Primary workset

The default group: a real directory at `$XDG_DATA_HOME/kanibako/primary_workset`,
keyed by box name.  You never name or create it -- just `cd` into any directory
and run `kanibako`, and the box joins the primary workset.

The primary workset is addressable through the same `workset` commands as named
worksets (workset name token `__PRIMARY__`), so primary-workset settings use the
ordinary `workset set default` mechanism (see [Configuration](#configuration)):

```bash
kanibako workset set default model=opus          # default for ALL primary-mode boxes
kanibako workset set default group_auth=false    # distinct credentials by default
```

The names `__PRIMARY__` / `__STANDALONE__` (and legacy `default`) are reserved
and cannot be used as a named-workset name.

```
$XDG_DATA_HOME/kanibako/primary_workset/
├── settings.yaml
├── boxes/{name}/{home/ → ~/ , settings.yaml}
├── vault/{ro,rw}/{name}/                        → ~/vault/{ro,rw}
└── logs/{name}.jsonl
# the box WORKSPACE stays external: your real project dir → ~/workspace
```

### Named workset

A workset is a *named* project group rooted at a directory you pick.  It groups
related projects under one human-readable root, with a single `<root>/settings.yaml`
(identity + member list + a group-level settings/auth tier that member boxes
inherit; see [Configuration](#configuration)).  A workset name is a shared
address and must be unique -- a collision at create/import time is refused.

```bash
kanibako workset create ~/worksets/research --name my-research
kanibako workset connect my-research ~/repos/paper-a --name paper-a
cd ~/worksets/research/workspaces/paper-a
kanibako
```

```
{workset}/
├── settings.yaml
├── boxes/{name}/{home/ → ~/ , settings.yaml}
├── workspaces/{name}/                  → ~/workspace
├── vault/{ro,rw}/{name}/               → ~/vault/{ro,rw}
└── logs/{name}.jsonl
```

### Standalone

A separate mode -- not a workset.  All state lives inside the project directory
itself, alongside the workspace.  Fully portable (drop-in importable).

```bash
kanibako create --standalone           # in the current directory
kanibako create --standalone ~/myproj  # create and initialize a new directory
```

```
{project}/                       ← project root
├── settings.yaml                ← box metadata (at the root)
├── workspace/                   → ~/workspace  (a subdir, not the root)
├── box_data/{home/ → ~/ , {name}.jsonl}
└── vault/{ro,rw}/               → ~/vault/{ro,rw}
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

Base rigs are pulled automatically from GHCR on first use; they are
**pull-only** (Kanibako does not build them locally). If a pull fails, Kanibako
reports an actionable error. To use a custom base, build it yourself from the
[kanibako-images](https://github.com/doctorjei/kanibako-images) repo and point
Kanibako at the resulting local image via `--image` / `box_image`. Toolchain
*templates* still build locally (they layer on a pulled base).

```bash
kanibako rig list                     # show local rigs
kanibako rig prep jvm                  # build the bundled jvm template (or pull a prefab)
kanibako rig prep --force             # re-prep the configured rig
kanibako rig prep --all               # update all known local rigs
```

### Custom Rigs

There are two ways to make a custom rig: prep a bundled toolchain template
(`rig prep`), or build one interactively from a foundation rig and commit it
(`rig extend`).

#### Bundled templates

Kanibako ships a set of curated toolchain templates that layer on a base rig.
List them with `rig list` (shown under "Example templates"):

| Template | Toolchain |
|----------|-----------|
| `jvm` | Java, Kotlin, Maven (JVM toolchain) |
| `systems` | C/C++, Rust, cross-compilation toolchain |
| `android` | Android SDK command-line tools + NDK |
| `dotnet` | .NET SDK (LTS) |
| `js` | Node tooling: yarn, pnpm, bun, TypeScript |

Build one with `rig prep <template>`.  The bundled template is built
**locally on your host** -- non-interactively on its declared base -- producing a
local image named `kanibako-template-<template>`:

```bash
kanibako rig prep jvm                  # build jvm toolchain on the template's declared base
# -> local image kanibako-template-jvm
```

Each template is tied to a single declared base via an `ARG BASE_IMAGE` line in
its Containerfile (default `kanibako-oci`).  There is no per-variant matrix -- a
template targets one base; to build on a different flavor, fork its
Containerfile.

These bundled templates are **not published to any registry**.  CI builds them
and runs their toolchain smoke checks (each Containerfile lists smoke commands
via a `# kanibako-template-check:` header) so that they are verified to build and
run, but the resulting images stay local to whoever preps them.

New templates are discovered automatically: dropping a
`Containerfile.template-<name>` (with a `# kanibako-template: <description>`
header, and optionally a `# kanibako-template-check: <cmd>` smoke-check header)
into the package makes it show up in `rig list`, become buildable via
`rig prep <name>`, and get build+smoke-verified by CI (not
published) -- no code or workflow edits needed.

#### Interactive rigs

`rig extend` drops you into an interactive container on a foundation rig (auto-
prepped if needed); install tools by hand, then commit the result on exit as an
extended rig (`kanibako-rig-<name>`):

```bash
kanibako rig extend custom --from kanibako-oci   # start from oci, install tools
# (inside container: apt install openjdk-21-jdk maven, etc.)
# exit when done

kanibako rig list                     # show local rigs
kanibako rig rm custom                # remove a custom rig
```

Custom rigs are standard OCI images -- push them to any registry for sharing:

```bash
podman push kanibako-template-custom ghcr.io/myorg/kanibako-template-custom
```

## Container Layout

Inside the container, the agent sees:

```
/home/agent/                 persistent home (bind mount)
  |- .bashrc                shell config (with shell.d sourcing)
  |- .profile               login profile
  |- .shell.d/              drop-in init scripts (*.sh)
  |- .claude/               agent credentials
  |- .claude.json            agent settings
  |- workspace/             project files (bind mount)
  |- vault/ro/              read-only vault (bind mount, optional)
  |- vault/rw/              read-write vault (bind mount, optional)
  '- channels/             inter-box channel tree
```

## Shell Customization

### Environment variables

Set per-project or global environment variables that are passed to the
container:

```bash
# Persistent (stored in the scope's settings file)
kanibako box set box.env.EDITOR=vim             # box-level
kanibako workset set workset.env.EDITOR=vim     # workset-level
kanibako system set system.env.EDITOR=nano      # global (all boxes)
kanibako box get box.env.EDITOR                 # show one value

# Per-run (not persisted)
kanibako start -e EDITOR=vim -e DEBUG=1
```

The scope is part of the key: `<scope>.env.<VAR>`, where `<scope>` is
`system`, `workset` or `box` (an agent's own vars are
`agent.<agent>.env.<VAR>`).  The most specific scope wins, so a box var
overrides a workset one, which overrides the global one.

### Custom prompt

The shell prompt is controlled by the `KANIBAKO_PS1` environment variable:

```bash
kanibako box set box.env.KANIBAKO_PS1="(myproject) \u:\w\$ "
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

## Agent Configuration

Each agent gets a YAML configuration file inside its per-agent store directory
at `$XDG_DATA_HOME/kanibako/agents/{agent}/settings.yaml`.  The file is
generated automatically on first use (via the target plugin's
`generate_agent_config()` method) and can be edited afterwards.

```yaml
agent:
  name: "Claude Code"
  run_args: []              # extra CLI args prepended on every launch
  model: "opus"             # agent-specific state knobs (e.g. --model for Claude)
  access: "permissive"
env:
  # KEY: "value"            # raw env vars injected into the box
tweakcc:
  # enabled: false          # enable tweakcc binary patching
  # config: "~/.tweakcc/config.json"  # external tweakcc config file
```

**Sections:**
- `agent:` -- identity and defaults (`name`, `run_args`) plus runtime state
  knobs translated by the target plugin into CLI args and env vars (e.g. Claude
  maps `model` -> `--model`). Effective state resolves across the settings
  cascade `system < agent.<agent> < workset < box` with the target's declared
  defaults as the floor.
- `env:` -- environment variables injected into the box
- `tweakcc:` -- optional tweakcc integration for binary patching
  (see [docs/tweakcc.md](docs/tweakcc.md))

Per-agent common dirs/caches are declared by the plugin (`agent.<agent>.common` /
`agent.<agent>.caches` — one key per category, holding a map keyed by box destination) and
served from the per-agent store dir (`agents/<agent>/{common,caches}/<name>`).

Manage agent settings via the CLI:

```bash
kanibako agent list                    # list configured agents
kanibako agent get claude model        # show the agent's model
kanibako agent set claude model=sonnet # set agent-level default
```

## Box Templates

Box templates provide **layered seed-once** initialization for a new box.
Three layers are copied into the box STORE in order (later overlays earlier;
absent layers are skipped), **once** -- edits you make inside a box afterward
are never overwritten.

Each layer has a `box/` subtree with the same two entries, and each entry has
its own destination:

```
                                            ->  <box_dir>/home       (delivered at ~/)
1. system   @system.template/box/...        ->  <box_dir>/canon/handbook
2. agent    @agent.<agent>.template/box/... (= agents/<agent>/template/box; if an agent is set)
3. workset  @workset.template/box/...       (= <wsroot>/template/box; optional, primary/named)
```

- `box/home/**` seeds the box HOME -- what you see at `~/` inside the box,
  including `~/canon/notebook` and `~/canon/workbook`, the two books the box
  owns and can write.
- `box/canon/handbook/**` seeds the box's HANDBOOK CHAPTER at `@box.canon`,
  which is a **sibling** of the home, not inside it. It is bound back into the
  box read-only at `~/canon/handbook/box`.

⚑ `@box.canon` is not `~/canon`: the first is the box's contribution to the
canon (on the host), the second is the assembled canon (in the box).

Per-file rule: plain ordered copy, **last layer wins**, seed-once. There is no
per-file merge of any file.

The old shell-variant selector (`crab.shell` / `template_name`) is gone -- there
is one fixed `template/` dir per agent, with no variant subdirectory.  `box.shell`
now means only the login shell (see [Configuration](#configuration)).

**Example agent store layout** (for agent `claude`):

```
agents/claude/
|- template/box/home/
|   |- .claude/
|   |   '- settings.json
|   '- .claude.json
'- canon/handbook/
    '- directives/
        '- SYS_AGENT.md      # this agent's handbook chapter
```

To ship custom per-agent config into boxes, put it under that agent's
`template/box/home/`; it seeds via layer 2. To give the agent its own standing
directives, put them under `canon/handbook/`.

## Vault

Each box has optional read-only and read-write shared directories, mounted
inside the box at `~/vault/ro` and `~/vault/rw`:

- **vault/ro/** -- files visible inside the box but not writable
  (documentation, reference data, prompt libraries)
- **vault/rw/** -- files that persist across sessions and can be modified
  (databases, build caches, generated artifacts)

The host-side vault lives under the workset (`vault/{ro,rw}/<box>`); inside the
box the local `~/workspace/vault` path is masked by a read-only tmpfs, so the
agent cannot see or modify host vault metadata.

### Snapshots

Kanibako automatically creates a snapshot of the read-write vault before each
box launch.  The snapshot strategy is detected per-project: reflink
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
plugins** -- Python classes that subclass the `Target` abstract base class and
expose a declarative `PluginDescriptor` (the plugin system is descriptor-only).
Claude, Codex, and Goose ship via `kanibako-agent-{claude,codex,goose}`
(installed by the `kanibako` meta-package); other agents can be added as pip
packages.  Install `kanibako-cli` alone for agent-agnostic operation.
If no agent is detected, Kanibako falls back to `no_agent` -- a plain shell
with no agent binary or credentials.

**Shipped agents:**
- **Claude Code** -- `kanibako-agent-claude`
- **OpenAI Codex CLI** -- `kanibako-agent-codex`
- **Goose** -- `kanibako-agent-goose`

A target handles:
1. Detecting the agent binary on the host (`detect`)
2. Describing the launch contract declaratively (`descriptor`) -- core
   assembles argv, delivery binds, container env, and credential sync from it

### Three-tier plugin discovery

Kanibako discovers target plugins from three sources, checked in order.
Later sources override earlier ones when two plugins register the same name.

| Tier | Location | Use case |
|------|----------|----------|
| 1. Entry points | `kanibako.agents` entry point group + `kanibako.plugins.*` namespace scan | Pip-installed packages and bind-mounted plugins in nested containers |
| 2. User directory | `~/.local/share/kanibako/plugins/*.py` | Personal plugins shared across all projects |
| 3. Project directory | `{project}/.kanibako/plugins/*.py` | Project-specific plugins |

Drop a `.py` file containing a `Target` subclass into the user or project
plugins directory and Kanibako picks it up automatically -- no packaging or
`pip install` needed.  Files starting with `_` are skipped.

**Security note:** file-drop plugins run with the same permissions as
Kanibako itself.  Only place files you trust in plugin directories.

See [docs/writing-targets.md](docs/writing-targets.md) for the full developer
guide.

```bash
# Install a third-party target
pip install kanibako-target-myagent

# Use a specific target
kanibako box set box.agent=myagent
kanibako start

# (`crab_name` is gone -- select the agent via box.agent)
```

## Configuration

Kanibako splits **config** (layout -- *where things live*, the `system.*`
namespace) from **settings** (behavior -- `agent.*` / `box.*` / `workset.*` plus
the category keys).  They live in separate file sets.

**Settings** follow a 6-tier cascade (box wins among the normal tiers;
`*_required` is an absolute admin cap above box), below CLI flags:

```
CLI flag > settings_required > box > workset > agent.<agent> > system > settings_base
```

**Config** (`system.*`) is read from the config file set
(`config_base < ~/.config/kanibako.yaml < config_required`).  Config keys are
**file-only** -- the CLI reads and shows them but refuses to set them (edit the
file directly); `setup` and programmatic writers still write them.

All settings levels share the same four verbs -- `set` / `get` / `show` /
`reset`:

```bash
# Box level
kanibako box show                       # show box overrides
kanibako box show --effective           # show resolved values (inherited + overrides)
kanibako box get model                  # get one key
kanibako box set model=sonnet           # set one key
kanibako box reset model                # remove override, back to default

# Workset level (group defaults inherited by member boxes)
kanibako workset set <workset> model=opus
kanibako workset set default model=opus      # primary-workset default

# Agent level (defaults for all boxes using this agent)
kanibako agent set claude model=opus

# System level (global settings defaults)
kanibako system set model=opus
kanibako system reset --all             # reset all global settings
```

`system.*` LAYOUT-PATH keys are **file-only**: the CLI shows them but refuses to
set/reset them, pointing you at the config file. `system.agent` is NOT one of those —
it is an ordinary system-scope setting, so `kanibako system set
system.agent=<name>` works (as does `kanibako setup`). Edit structural paths in
`~/.config/kanibako_config.yaml` directly.

### Files

All kanibako config/settings files are YAML.

- **Config (global)**: `$XDG_CONFIG_HOME/kanibako.yaml` (`system.*` layout only)
- **System settings**: `$XDG_DATA_HOME/kanibako/global/settings.yaml`
- **Workset settings**: `<workset_root>/settings.yaml`
- **Per-agent settings**: `$XDG_DATA_HOME/kanibako/agents/{agent}/settings.yaml`
- **Per-box settings**: `boxes/{name}/settings.yaml` (standalone: `<root>/settings.yaml`)
- **Template root**: `$XDG_DATA_HOME/kanibako/global/template/` (the `box`,
  `workset` and `agent` moulds new stores are stamped from)
- **System handbook**: `$XDG_DATA_HOME/kanibako/global/canon/handbook/` -- your
  own system-wide directives, delivered read-only into every box

### Common settings keys

| Key | Default | Description |
|-----|---------|-------------|
| `start_mode` | `continue` | Default start mode (continue/new) |
| `model` | platform default | Agent model name |
| `autonomous` | `true` | Run with full permissions (autonomy) |
| `box.image` | `kanibako-oci:latest` | Container rig |
| `box.shell` | `$KANIBAKO_SHELL` | Login shell for a no-agent box (`kanibako start` with no agent, `kanibako shell`); resolved `box.shell` → `$KANIBAKO_SHELL` → the image's recorded login shell → `sh` |
| `box.agent` | (resolved) | Agent target plugin for this box; part of the resolution cascade (see [Agent Selection](#agent-selection)) |
| `box.share_images` | | Share host images into the box |
| `group_auth` | `true` | Shared credentials across the group (`true`) vs. per-box (`false`) |
| `enable_vault` | `true` | Enable vault directories |
| `env.*` | | Persistent environment variables (`<scope>.env.<VAR>`) |
| `<scope>.bindings.ro` / `.rw` | | Scoped bind-mounts. ⚑ **Settings-file only** — one key per arm, holding a map keyed by box destination. There is no `.<name>` sub-key and no `config set` route; `config get` reads it |
| `<scope>.caches` | | Scoped cache mounts — same shape: one key, `{box_dest: [host_src[, options]]}`, settings-file only |
| `<scope>.common` | | Shared dirs mounted rw — same shape |
| `<scope>.seeded` | | Copy-once seeds applied at box init — same shape; a COPY, not a mount |
| `<scope>.synced` | | Per-launch mtime-gated copies — same shape; a COPY, not a mount |

### Global config file

The global config (`~/.config/kanibako.yaml`) holds only `system.*` layout keys
(the `.path` infix is gone).  Values may use the resolver grammar -- `@`-refs,
`$XDG_*`, `~`:

```yaml
system:
  data: "$XDG_DATA_HOME/kanibako"         # data root
  agents: "@system.data/agents"           # per-agent store
  primary_workset: "@system.data/primary_workset"
  channels: "@system.data/channels"
  global: "@system.data/global"           # settings.yaml + registry.yaml
```

Behavior defaults (`box.*`, agent settings, caches) go in
`global/settings.yaml`, not in the config file.

## Helper Spawning

Kanibako containers can spawn child instances for parallel workloads.
Each child gets its own directory tree, peer communication channels,
and spawn budget. Helpers are enabled by default -- the host runs a
Unix socket hub alongside the director container, and helpers connect
to it for orchestration and messaging.

```bash
# Spawning and lifecycle
kanibako box helper spawn                 # spawn a child with default budget
kanibako box helper spawn --model sonnet  # child uses a different model
kanibako box helper spawn --depth 2 --breadth 3  # custom spawn limits
kanibako box helper list                  # show all helpers with status
kanibako box helper stop 1                # stop helper 1
kanibako box helper respawn 1             # relaunch a stopped helper
kanibako box helper cleanup 1             # stop and remove helper 1
kanibako box helper cleanup 1 --cascade   # also remove all descendants

# Messaging
kanibako box helper send 1 "Analyze the auth module"
kanibako box helper broadcast "Starting tests"

# Conversation log
kanibako box helper log                   # display full message log
kanibako box helper log --follow          # tail log in real-time
kanibako box helper log --from 1          # filter by helper number
kanibako box helper log --tail 10         # show last 10 entries

# Opt out
kanibako start --no-helpers                 # launch without helper support
```

**Architecture:** The Kanibako CLI is bind-mounted into every container
(director and helpers), so `kanibako box helper spawn/send/broadcast/log`
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
content. View the conversation in real-time with `kanibako box helper log --follow`:
```
12:35:10  [0 -> 1]  Analyze the auth module and report back.
12:36:45  [1 -> 0]  Found 3 issues in the token refresh flow.
12:37:00  [0 -> *]  Starting integration tests.
```

**Spawn budget:** Each helper gets a depth/breadth budget controlling
how many levels deep it can spawn and how many siblings are allowed.
Depth decrements with each level. The budget is written as a read-only
config (`spawn.yaml`) inside the child, enforced at spawn time.

**Peer channels:** Helpers communicate through shared directories.
Each pair of siblings gets three channels (A-reads, B-reads, shared-rw).
A broadcast channel (`all/`) is available to all helpers.

**Directory layout** (inside a container):
```
~/helpers/
  1/                    # helper 1 root
    workspace/          # helper's working directory
    vault/ro/           # read-only vault share
    vault/rw/           # read-write vault share
    playbook/scripts/   # helper-init.sh (entrypoint wrapper)
    peers/              # symlinks to peer channels
    all -> ../all/      # broadcast channel
    spawn.yaml          # RO spawn budget
    state.json          # status, model, depth, peers
  all/ro/               # broadcast read-only
  all/rw/               # broadcast read-write
  channels/             # raw peer channel directories
$XDG_STATE_HOME/kanibako/
  helper.sock           # hub socket (mounted from host)
  helpers.jsonl         # message log (mounted read-only)
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

# A reattach does ONLY what reattaching needs: it does not rebuild or pull an
# image, probe the launch baseline, or verify a persona endpoint over the
# network -- none of which could affect the session it is attaching to.
# Flags that the running container cannot adopt are refused rather than
# ignored; restart the box to apply them:
kanibako --restart -N myproject     # stop, then start a NEW conversation

# Run a command as a second process inside the running box:
kanibako start --entrypoint htop myproject

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
pip install -e ".[dev]" -e packages/agent-claude/ -e packages/agent-codex/ -e packages/agent-goose/

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

**Overview:** Kanibako's core (`kanibako-cli`) handles container lifecycle,
project state, configuration, and plugin discovery.  Agent-specific logic
lives in target plugins (e.g. `kanibako-agent-claude`).  The CLI is an
argparse tree in `cli.py` that delegates to command modules in `commands/`.
Configuration flows through a unified engine (`config_interface.py`) that
supports get/set/reset/show at every level (box, workset, agent, system).

## License

See [LICENSE](LICENSE.md) for details.

## Credits

LLMs were used as a tool in the development of this software.
