# Writing Target Plugins

This guide explains how to create a kanibako target plugin so that kanibako
can launch your preferred AI coding agent inside a container.

## Overview

Kanibako is agent-agnostic.  All agent-specific logic lives in **target
plugins** — Python classes that implement the `Target` abstract base class.
Kanibako discovers installed targets at runtime via Python entry points.

A target is responsible for:

1. **Detecting** the agent binary on the host
2. **Mounting** the agent binary/installation into the container
3. **Initializing** agent-specific config in the project home directory
4. **Checking authentication** before launch
5. **Syncing credentials** between host and project home
6. **Building CLI arguments** for the agent entrypoint

## The `Target` ABC

All targets subclass `kanibako.targets.base.Target`.  Here is the full
interface:

```python
from kanibako.targets.base import Target, AgentInstall, Mount

class MyTarget(Target):
    @property
    def name(self) -> str: ...
    @property
    def display_name(self) -> str: ...
    def detect(self) -> AgentInstall | None: ...
    def binary_mounts(self, install: AgentInstall) -> list[Mount]: ...
    def init_home(self, home: Path, *, auth: str = "shared") -> None: ...
    def check_auth(self) -> bool: ...
    def refresh_credentials(self, home: Path) -> None: ...
    def writeback_credentials(self, home: Path) -> None: ...
    def build_cli_args(self, *, safe_mode, resume_mode, new_session,
                       is_new_project, extra_args) -> list[str]: ...
    def generate_agent_config(self) -> AgentConfig: ...
    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]: ...
```

### Supporting dataclasses

**`Mount(source, destination, options="")`** — A single container volume
mount.  `source` is a host `Path`, `destination` is an absolute container
path string, and `options` is an optional mount option like `"ro"`.  Call
`mount.to_volume_arg()` to get the `-v` argument string.

**`AgentInstall(name, binary, install_dir)`** — Describes where the agent
lives on the host.  `binary` is the host path to the executable (may be a
symlink).  `install_dir` is the root of the installation tree.

## Method reference

### `name` (property)

Short machine-readable identifier for this target, e.g. `"claude"`,
`"aider"`, `"goose"`.  Used in configuration (`target_name = "aider"`) and
entry point registration.  Must be unique across all installed targets.

### `display_name` (property)

Human-readable name shown in status output, e.g. `"Claude Code"`,
`"Aider"`, `"Goose"`.

### `detect() -> AgentInstall | None`

Auto-detect the agent on the host system.  Return an `AgentInstall`
describing the binary location and installation root, or `None` if the
agent is not installed.

Typical implementation:

```python
import shutil
from pathlib import Path

def detect(self) -> AgentInstall | None:
    path = shutil.which("myagent")
    if not path:
        return None
    binary = Path(path)
    resolved = binary.resolve()
    # Find the installation root (agent-specific logic here)
    install_dir = resolved.parent
    return AgentInstall(name="myagent", binary=binary, install_dir=install_dir)
```

For agents installed via a package manager (npm, pip), you typically walk
up from the resolved binary to find the package root directory.  For single
standalone binaries, `install_dir` can equal `resolved.parent`.

### `binary_mounts(install: AgentInstall) -> list[Mount]`

Return volume mounts that make the agent binary available inside the
container.  The container's `PATH` includes `/home/agent/.local/bin/`, so
mount the main executable there.  Larger install trees go under
`/home/agent/.local/share/`.

```python
def binary_mounts(self, install: AgentInstall) -> list[Mount]:
    return [
        Mount(
            source=install.install_dir,
            destination="/home/agent/.local/share/myagent",
            options="ro",
        ),
        Mount(
            source=install.binary,
            destination="/home/agent/.local/bin/myagent",
            options="ro",
        ),
    ]
```

For a single standalone binary (no install tree), return only the binary
mount:

```python
def binary_mounts(self, install: AgentInstall) -> list[Mount]:
    return [
        Mount(
            source=install.binary.resolve(),
            destination="/home/agent/.local/bin/myagent",
            options="ro",
        ),
    ]
```

Mount everything read-only (`"ro"`) — the container should not modify the
host's agent installation.

For pip-installed Python tools that don't need binary mounting (they run
from the container's own Python environment), return an empty list.

### `init_home(home: Path, *, auth: str = "shared") -> None`

Initialize agent-specific configuration in the project home directory.
Called from `start.py` for new projects (`proj.is_new`), after shell
template application and target resolution.

The `auth` parameter indicates the project's authentication mode:
- `"shared"` — copy credentials from host (default)
- `"distinct"` — skip credential copy (project manages its own auth)

Always perform non-credential setup (config directories, default files)
regardless of auth mode.

Typical work: create config directories, copy/generate default config
files, copy credentials from host (if auth is shared).

```python
def init_home(self, home: Path, *, auth: str = "shared") -> None:
    config_dir = home / ".config" / "myagent"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    if not config_file.exists():
        config_file.write_text("{}\n")
    if auth != "distinct":
        # Copy credentials from host
        ...
```

### `check_auth() -> bool`

Called **before** container launch (after detection, before credential
sync).  Verify that the agent is authenticated on the host.  Return
`True` if authentication is valid, `False` if it failed.

The default implementation returns `True` (no-op).  Override this for
agents that require pre-launch auth validation.

For agents that use interactive login flows, `check_auth()` can trigger
a login attempt and return the result:

```python
def check_auth(self) -> bool:
    result = subprocess.run(
        ["myagent", "auth", "status"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True
    # Trigger interactive login
    subprocess.run(["myagent", "auth", "login"])
    # Re-check
    result = subprocess.run(
        ["myagent", "auth", "status"],
        capture_output=True, text=True,
    )
    return result.returncode == 0
```

For agents that use environment variables for API keys, this can be a
no-op (the default `return True` is sufficient).

### `refresh_credentials(home: Path) -> None`

Called **before** container launch (after `check_auth()`).  Copy or sync
credentials from the host into the project home so the agent can
authenticate inside the container.

Must handle the first-run case where credential files don't exist yet on
either side.  Keep the implementation simple — copy if source is newer, or
unconditionally overwrite.

For agents that use environment variables for API keys (e.g. `OPENAI_API_KEY`),
this can be a no-op.

```python
def refresh_credentials(self, home: Path) -> None:
    pass  # API keys passed via environment variables
```

### `writeback_credentials(home: Path) -> None`

Called **after** the container exits.  Copy any updated credentials from
the project home back to the host.  For example, if the agent refreshed an
OAuth token during the session.

Same first-run handling as `refresh_credentials()`.

```python
def writeback_credentials(self, home: Path) -> None:
    pass  # Nothing to write back
```

### `build_cli_args(...) -> list[str]`

Build the command-line arguments passed to the agent binary inside the
container.  Parameters:

| Parameter | Type | Meaning |
|---|---|---|
| `safe_mode` | `bool` | User requested safe/sandboxed mode (no auto-approve) |
| `resume_mode` | `bool` | Resume a previous conversation |
| `new_session` | `bool` | Force a new session (don't continue) |
| `is_new_project` | `bool` | First launch for this project |
| `extra_args` | `list[str]` | Raw extra arguments from the user's command line |

Map these to your agent's CLI flags.  Always pass through `extra_args` at
the end.

```python
def build_cli_args(self, *, safe_mode, resume_mode, new_session,
                   is_new_project, extra_args) -> list[str]:
    args = []
    if not safe_mode:
        args.append("--auto-approve")
    if resume_mode:
        args.append("--resume")
    args.extend(extra_args)
    return args
```

### `generate_agent_config() -> AgentConfig`

Return a default `AgentConfig` for this target.  Called on first use or during
`kanibako setup` to generate the agent TOML file.  The base implementation
returns an `AgentConfig` with `name` set to `self.display_name` and all other
fields at their defaults.

Subclasses should override to provide agent-specific defaults — template
variant, state knobs, shared cache paths, etc.

```python
from kanibako.agents import AgentConfig

def generate_agent_config(self) -> AgentConfig:
    return AgentConfig(
        name="MyAgent",
        shell="standard",
        state={"access": "permissive"},
        shared_caches={"plugins": ".config/myagent/plugins"},
    )
```

### `apply_state(state: dict[str, str]) -> tuple[list[str], dict[str, str]]`

Translate `[state]` section values from the agent TOML into CLI arguments and
environment variables.  Returns a tuple of `(cli_args, env_vars)`.

The base implementation returns `([], {})` — all state keys are silently
ignored.  Subclasses override to handle known keys.

For example, the built-in Claude target maps the `model` key to the
`--model` CLI flag:

```python
def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    cli_args: list[str] = []
    env_vars: dict[str, str] = {}
    if "model" in state:
        cli_args.extend(["--model", state["model"]])
    return cli_args, env_vars
```

### `resource_mappings() -> list[ResourceMapping]`

*Optional.* Declare how agent resources should be shared across projects.

Returns a list of `ResourceMapping` entries, each mapping a path within
the agent's config directory to a `ResourceScope`:

- `SHARED` — shared at the workset/account level (e.g. plugin binaries)
- `PROJECT` — per-project, starts fresh (e.g. conversation history)
- `SEEDED` — per-project, but seeded from the workset template at
  project creation (e.g. agent settings)

The default returns an empty list, meaning all agent resources are
treated as project-scoped.

```python
from kanibako.targets.base import ResourceMapping, ResourceScope

def resource_mappings(self) -> list[ResourceMapping]:
    return [
        ResourceMapping("plugins/", ResourceScope.SHARED, "Shared plugins"),
        ResourceMapping("config.json", ResourceScope.SEEDED, "Agent config"),
        ResourceMapping("history/", ResourceScope.PROJECT, "Session history"),
    ]
```

## Discovery and registration

Kanibako uses Python [entry points](https://packaging.python.org/en/latest/specifications/entry-points/)
to discover targets.  Register your target class under the
`kanibako.targets` group in your `pyproject.toml`:

```toml
[project.entry-points."kanibako.targets"]
myagent = "my_package:MyTarget"
```

The entry point **name** (left of `=`) is the target's identifier, matching
what `Target.name` returns.  The entry point **value** (right of `=`)
points to the `Target` subclass.

When a user runs `kanibako start`, kanibako calls `discover_targets()` which
loads all registered entry points.  If no `--target` is specified, kanibako
calls `detect()` on each target and uses the first one that returns an
`AgentInstall`.  If no target's `detect()` succeeds, kanibako falls back to
`NoAgentTarget` — a built-in target that launches a plain shell without any
agent binary or credentials.

## Packaging

Recommended package layout:

```
kanibako-target-myagent/
  pyproject.toml
  src/
    kanibako_target_myagent/
      __init__.py          # contains your Target subclass
  tests/
    test_myagent_target.py
  README.md
```

Minimal `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "kanibako-target-myagent"
version = "0.1.0"
description = "Kanibako target plugin for MyAgent"
requires-python = ">=3.11"
dependencies = ["kanibako"]

[project.entry-points."kanibako.targets"]
myagent = "kanibako_target_myagent:MyTarget"

[tool.setuptools.packages.find]
where = ["src"]
```

Install in development mode with:

```
pip install -e kanibako-target-myagent/
```

## Testing

Use `unittest.mock.patch` to mock `shutil.which` and filesystem state.
Use `tmp_path` for isolated home directories.  See `tests/test_targets/test_claude.py`
in the kanibako repository for the canonical test patterns.

Key patterns:

```python
from unittest.mock import patch
from kanibako.targets.base import AgentInstall
from kanibako_target_myagent import MyTarget

class TestDetect:
    def test_found(self, tmp_path):
        binary = tmp_path / "myagent"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        with patch("shutil.which", return_value=str(binary)):
            result = MyTarget().detect()

        assert result is not None
        assert result.name == "myagent"

    def test_not_found(self):
        with patch("shutil.which", return_value=None):
            assert MyTarget().detect() is None

class TestBuildCliArgs:
    def test_safe_mode(self):
        args = MyTarget().build_cli_args(
            safe_mode=True, resume_mode=False,
            new_session=False, is_new_project=False,
            extra_args=[],
        )
        assert "--auto-approve" not in args
```

## Container environment

Your agent runs inside a rootless Podman (or Docker) container with:

- **Home directory**: `/home/agent`
- **Working directory**: `/home/agent/workspace` (bind-mounted project)
- **PATH includes**: `/home/agent/.local/bin`
- **User**: `agent` (non-root, UID mapped to host user)
- **Network**: Available (the container has network access)
- **Shared volumes**:
  - `/home/agent/share-ro/` — read-only vault (shared files from host)
  - `/home/agent/share-rw/` — read-write vault

Your binary mounts go into `/home/agent/.local/bin/` (executables) and
`/home/agent/.local/share/` (larger install trees).

## Examples

See the `examples/` directory for three complete, graduated example plugins:

- **[kanibako-target-aider](../examples/kanibako-target-aider/)** — Minimal target (pip-installed Python CLI)
- **[kanibako-target-codex](../examples/kanibako-target-codex/)** — Moderate target (npm-installed Node.js CLI with credential sync)
- **[kanibako-target-goose](../examples/kanibako-target-goose/)** — Advanced target (compiled binary with YAML config filtering)
