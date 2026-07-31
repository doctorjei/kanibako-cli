# Getting Started with Kanibako

A five-minute path from install to your first agent session. For the full
reference, see [README.md](README.md).

## What is Kanibako?

Kanibako runs AI coding agents (Claude Code, Codex, Goose) inside isolated,
persistent containers -- one per project. Your host credentials are forwarded
in and your project files are bind-mounted, so the agent has real tools, real
files, and real network access without touching the rest of your host.

**The value prop:** `cd` into a project and run `kanibako`. Setup, image pulls,
credential syncing, and teardown are automatic -- no `docker run`, no volume
flags, no Containerfile. State persists between runs, so you always pick up
where you left off.

A little vocabulary you'll see throughout:

- **box** -- the per-project container (the sandbox your agent runs in)
- **rig** -- the container image a box is built from
- **crab** -- the agent configured in a box (**C**ontained **R**untime
  **A**gent in a **B**ox)

## Prerequisites

- **A container runtime** -- [Podman](https://podman.io/) 4.3+ (recommended) or
  Docker. It just needs to be installed; Kanibako drives it for you.
- **At least one supported agent installed on the host** -- e.g.
  [Claude Code](https://docs.anthropic.com/en/docs/claude-code) -- and you
  should already be logged in to it, since Kanibako forwards those credentials
  into the box.
- **Python 3.11+ and pip** (or `pipx`/`uv`) to install Kanibako.

## Install

Kanibako is currently a pre-release, so include `--pre` to get the current
release candidate (this always installs the newest rc — no version to keep in
sync):

```bash
# with uv (recommended — isolated tool install)
uv tool install --prerelease allow kanibako

# with pipx (isolated CLI install)
pipx install --pip-args=--pre kanibako

# or with pip
pip install --pre kanibako
```

To pin an exact candidate instead, append the version, e.g.
`pip install --pre kanibako==1.7.0rc12`. Once the stable `1.7.0` is out,
`pip install kanibako` (no `--pre`) will just work.

The `kanibako` meta-package installs the CLI **plus all three agent plugins**
(Claude, Codex, Goose). Because more than one agent ends up installed, you'll
**pick which one to use** in the next step (or per-run with `--agent <name>`).

> Prefer the agent-agnostic base with no agent plugins? Install `kanibako-cli`
> alone -- you'll get plain shell boxes. See [Installation](README.md#installation).

## First-time setup

Run the setup wizard once. It detects your container runtime and installed
agents, and lets you pick a default agent (stored as `system.agent`):

```bash
kanibako setup
```

On a terminal this shows a numbered menu of detected agents. To choose
non-interactively:

```bash
kanibako setup --agent claude
```

To check that your environment is healthy (runtime, images, agents, storage):

```bash
kanibako system diagnose
```

## Your first session

`cd` into any project directory and run `kanibako`:

```bash
cd ~/my-project
kanibako
```

On the **first** run in a directory, Kanibako will:

1. Pull the base container rig (once -- cached afterwards)
2. Create an isolated box for this project
3. Forward your agent credentials into the box
4. Drop you into an agent session inside the container

Your project files appear inside the box at `~/workspace/`. When you exit,
the project files and agent state are preserved.

Come back later and just run it again:

```bash
cd ~/my-project
kanibako          # resumes your previous conversation
kanibako -N       # or start a fresh conversation
```

## Everyday commands

| Command | What it does |
|---------|--------------|
| `kanibako` | Start or resume the agent session in the current directory |
| `kanibako -N` | Start a **new** conversation |
| `kanibako -C` | **Continue** the most recent conversation (the default) |
| `kanibako shell` | Open a plain bash shell in the box (no agent) |
| `kanibako shell -- <cmd>` | Run a one-shot command in the box, e.g. `kanibako shell -- echo hi` |
| `kanibako stop` | Stop the running container (`--all` stops every box) |
| `kanibako list` | List all your projects (`-a` includes orphans, `-q` names only) |
| `kanibako ps` | List active (running) boxes |
| `kanibako rm <project>` | Remove a project (`--purge` also deletes metadata) |

A few useful flags on `kanibako` (the `start` command): `-M <model>` to
override the model for one run, `-S` for a more restricted (secure) run,
`--image <rig>` to launch on a specific rig, and `--ephemeral` to run without
the persistent tmux wrapper. See [Common Flags](README.md#common-flags).

## Key concepts (brief)

- **box** -- the per-project container that holds home, config, and credentials.
- **rig** -- the container image a box runs on (default: `kanibako-oci`).
- **crab** -- the agent configured to run inside the box.

Kanibako organizes a box's state in one of three **project modes**, inferred
automatically:

- **Default (primary workset)** -- the implicit group; any directory you launch
  in joins it, keyed by box name. You never name or create it.
- **Workset (named)** -- a named project group rooted at a directory you pick,
  with shared settings and credentials inherited by member boxes.
- **Standalone** -- fully self-contained; all state lives inside the project
  directory itself, making it portable/importable.

See [Project Modes](README.md#project-modes) for depth.

**Custom rig teaser:** need compiled-language toolchains? Build a bundled
template and launch on it:

```bash
kanibako rig prep systems                     # C/C++ + Rust toolchain
kanibako --image kanibako-template-systems
```

See [Container Rigs](README.md#container-rigs).

## Configuration basics

Kanibako has one set of config verbs -- `set` / `get` / `show` / `reset` --
that work at every scope: `box`, `workset`, `agent`, and `system`:

```bash
kanibako box show                    # box overrides
kanibako box show --effective        # resolved values (inherited + overrides)
kanibako box get model               # read one key
kanibako box set model=sonnet        # set one key on this box
kanibako box reset model             # remove the override

kanibako workset set default model=opus   # default for primary-workset boxes
kanibako agent set claude model=opus      # default for all boxes using this agent
kanibako system set model=opus            # global settings default
```

The **default agent** is chosen via `kanibako setup` (not `system set`).
Structural layout paths live in `~/.config/kanibako.yaml` and are edited
directly. See [Configuration](README.md#configuration).

## Troubleshooting

- **"pick one" error on launch.** With the meta-package all three agents are
  installed, so a bare `kanibako` won't guess. Choose a default with
  `kanibako setup`, or override per-run with `kanibako --agent <name>`. See
  [Agent Selection](README.md#agent-selection).
- **First run looks slow.** The initial launch pulls the base rig once; it's
  cached afterwards, so later runs are fast.
- **Something seems off with your environment.** Run `kanibako system diagnose`
  to check runtime, images, agents, and storage.

## Next steps

- [README.md](README.md) -- the full reference (commands, flags, modes, vault,
  helpers, SSH integration).
- [Agent Selection](README.md#agent-selection) -- how the agent for a command
  is resolved.
- [Container Rigs](README.md#container-rigs) -- base rigs and building your own.
- [Configuration](README.md#configuration) -- the full settings model.
- [docs/architecture.md](docs/architecture.md) -- module-by-module internals.
- [docs/writing-targets.md](docs/writing-targets.md) -- write your own agent
  plugin.
