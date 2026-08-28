# Writing Target Plugins

This guide explains how to create a kanibako target plugin so that kanibako
can launch your preferred AI coding agent inside a box (container).

## Overview

Kanibako is agent-agnostic.  All agent-specific logic lives in **target
plugins** — Python classes that subclass `kanibako.targets.base.Target`.
Kanibako discovers installed targets at runtime via Python entry points and
by scanning the `kanibako.plugins` namespace package.

As of 1.6.0 the plugin system is **descriptor-only**.  A target exposes a
single declarative `PluginDescriptor` (via the `descriptor` property) and
kanibako's core assembles everything from it: the launch argv, the bind
mounts that deliver the agent binary, the container environment, and the
credential-sync lifecycle.  The old per-method launch hooks
(`build_cli_args`, `binary_mounts`, `init_home`, `generate_crab_config`) and
the `ResourceMapping` / `ResourceScope` / `resource_mappings()` abstraction
have been **removed**.

A target is responsible for:

1. **Detecting** the agent binary on the host (`detect`)
2. **Describing** its launch contract declaratively (`descriptor`)
3. **Checking authentication** before launch (`check_auth`, optional)
4. **Transforming** credential payloads where a plain copy won't do
   (`transform_cred`, optional)

Everything else — argv assembly, binary delivery binds, container env, and
the credential-sync engine — is driven by core from the descriptor.

## The `Target` ABC

All targets subclass `kanibako.targets.base.Target`.  Only a few members are
abstract or commonly overridden:

```python
from kanibako.targets.base import Target, AgentInstall, PluginDescriptor

class MyTarget(Target):
    @property
    def name(self) -> str: ...              # abstract: short id, e.g. "myagent"
    @property
    def display_name(self) -> str: ...      # abstract: e.g. "My Agent"
    def detect(self) -> AgentInstall | None: ...  # abstract

    @property
    def descriptor(self) -> PluginDescriptor | None: ...  # the declarative contract
```

Optional overrides (sensible defaults provided by the base class):

| Member | Default | When to override |
|---|---|---|
| `has_binary` | `True` | Set `False` for a pure pip/python tool with no host binary |
| `check_auth()` | `True` | Validate host auth before launch |
| `prepare_host(install, *, auto_auth, data_path)` | no-op | Touch the host before mounts (binary update, auth refresh) — set `descriptor.host_prep=True` to enable |
| `default_common()` | `{}` | Declare default `agent.<agent>.common` / `agent.<agent>.caches` binds |
| `default_category_binds()` | `{}` | Declare default `@`-ref-sourced binds in any agent-scope category |
| `default_seeds()` | `{}` | Declare default `agent.<agent>.seeded` copy-once seeds |
| `default_envs()` | `{}` | Declare default `agent.<agent>.env.<VAR>` environment variables |
| `setting_descriptors()` | `[]` | Advertise runtime settings (key, default, choices) |
| `generate_agent_config()` | `AgentConfig(name=display_name)` | Agent-specific config defaults |
| `default_entrypoint` | `None` (bash) | The box entrypoint binary name |
| `should_retry_new_session(output)` | `False` | Detect a failed `--continue` and retry fresh |
| `config_dir_name` | `.{name}` | The agent's config dir under home |
| `transform_cred(spec, src, dst, direction)` | plain copy | Filter/merge a `filtered=True` cred file |

## The `PluginDescriptor`

`PluginDescriptor` (in `kanibako.targets.base`) is the heart of a plugin.
Core's assembly and credsync engines read it to launch the agent.

```python
@dataclass(frozen=True)
class PluginDescriptor:
    command: tuple[str, ...]                      # box argv prefix, e.g. ("codex",)
    bindings: tuple[Binding, ...]                 # all bound elements; ordered; >= 1
    mode: dict[str, tuple[str, ...]]             # interactive modes: {"start": (...), "continue": (...)}
    operations: dict[str, Operation] = {}        # standalone ops, e.g. {"exec": ...}
    access_realization: AccessRealization | None = None  # per-tier `access` realization
    settings: tuple[SettingArg, ...] = ()        # value-bearing settings -> flag/env
                                                 # (no env field: static variables are
                                                 #  settings keys — see default_envs())
    cred_files: tuple[CredFileSpec, ...] = ()    # credential/config file lifecycle
    host_prep: bool = False                      # call prepare_host() before mounts
    init_dirs: tuple[str, ...] = ()              # home-relative dirs to mkdir (e.g. (".codex",))
```

### Supporting types

**`Binding(key, origin, box_dest, kind, scope, ro=True, literal_src=None)`** —
one bound element that delivers the agent into the box.

- `key` — your stable name for this binding, used in errors; not a settings key.
- `origin` — a `HostSrcOrigin`: `LAUNCHER` / `INSTALL_DIR` / `BINARY` (taken
  from the detected `AgentInstall`) or `LITERAL` (use `literal_src`).
- `box_dest` — absolute path inside the box (e.g.
  `/home/agent/.local/bin/codex`).
- `kind` — `BindKind.FILE` or `BindKind.DIR`.
- `scope` — a `BindScope`: `AGENT_CRITICAL` (delivery is essential —
  source-exists is safe-fail, bound read-only as-is with inode pinning) or
  `AGENT` (a per-agent share, best-effort, may be rw).

**`Mode` entries** — `mode` maps an *interactive* launch mode name to the argv
fragment appended after `command`.  Always provide `"start"` (new session) and
`"continue"` (resume last).  There is no dedicated resume *picker* mode; `-R` /
`--resume` falls through to `continue`.

**`Operation(fragment)`** — a standalone, session-less invocation spliced after
`command` (e.g. `{"exec": Operation(("exec",))}` for headless runs).

**`SettingArg(setting_key, channel, flag=(), env_var="")`** — routes a
value-bearing setting (e.g. `model`) to a `Channel.FLAG` argv flag
(`("--model",)`) or a `Channel.ENV` environment variable (`"GOOSE_MODEL"`).

**`AccessRealization(channel, env_var="", restricted=None, editing=None,
full=None, setting_key="")`** — how *your* harness realizes each `access`
permission tier (`restricted | editing | full`).  One `channel` for the whole
harness: `Channel.FLAG` (argv, claude/codex) or `Channel.ENV` (`env_var`, goose
`GOOSE_MODE`).  Each tier field is an `AccessTierRow(flag=(), env_value="")` or
`None`.

- **`None` = this harness CANNOT render that tier.**  The launch then REFUSES,
  naming the tiers you *can* render — it never substitutes a neighbour (goose
  declares no `editing`).
- **An EMPTY row = "emit nothing, deliberately"** — correct on the FLAG channel
  for a harness whose own default already prompts (claude/codex `restricted`).
  On the ENV channel an empty row is REFUSED at load: leaving the variable unset
  means the harness's own default, which on that channel is the permissive one.
- A non-empty `setting_key` makes the tier a persisted, cascade-resolved key
  (all three shipped agents use `"access"`); empty means the per-launch
  `-S` / `-A` flags only.

> ⚑ This block was named `SafeBypass` / `safe_bypass:` before v1.8.0, when it was
> a two-polarity `-A`/`-S` toggle.  There is no alias: a defaults file still
> using the old key is refused by name at descriptor load.

**`CredFileSpec(home_rel, host_rel, cadence=SYNC, mtime_gate=True,
filtered=False)`** — one credential/config file's lifecycle.

- `cadence` — `Cadence.SYNC` (bidirectional, mtime-gated each launch — for
  tokens/credentials) or `Cadence.SEED_ONCE` (one-way host→box at init).
- `filtered` — when `True`, core calls your `transform_cred` hook instead of a
  wholesale copy (use it to allowlist portable fields or merge).

**`Binding` host source resolution.** Core represents each binding as an
`agent.<agent>.bindings.{ro,rw}` entry keyed by `box_dest` and sourced from
`origin`, so a user repoints one by re-declaring that destination in the agent's
settings file.  The arms are dest-keyed; `key` addresses nothing.

**`AgentInstall(name, binary, install_dir, launcher=None)`** — where the agent
lives on the host.  `binary` is the executable path; `install_dir` is the
install tree root; `launcher` is the optional on-disk entrypoint the plugin
owns and binds as-is.

## A complete example

The shipped **Codex** plugin is the canonical descriptor-only reference (it was
written *after* the interface, proving the contract generalizes).  Its
descriptor:

```python
from kanibako.targets.base import (
    AccessRealization, AccessTierRow, AgentInstall, BindKind, Binding, BindScope,
    Cadence, Channel, CredFileSpec, HostSrcOrigin, Operation, PluginDescriptor,
    SettingArg, Target, TargetSetting,
)

_CODEX_DESCRIPTOR = PluginDescriptor(
    command=("codex",),
    bindings=(
        Binding(
            "binary", HostSrcOrigin.BINARY,
            "/home/agent/.local/bin/codex",
            BindKind.FILE, BindScope.AGENT_CRITICAL, ro=True,
        ),
    ),
    mode={"start": (), "continue": ("resume", "--last")},
    operations={"exec": Operation(("exec",))},
    access_realization=AccessRealization(
        Channel.FLAG,
        restricted=AccessTierRow(),                       # codex already prompts
        editing=AccessTierRow(flag=("-s", "workspace-write")),
        full=AccessTierRow(
            flag=("--dangerously-bypass-approvals-and-sandbox",),
        ),
        setting_key="access",
    ),
    settings=(SettingArg("model", Channel.FLAG, flag=("--model",)),),
    cred_files=(
        CredFileSpec(".codex/auth.json", ".codex/auth.json",
                     cadence=Cadence.SYNC, mtime_gate=True, filtered=False),
    ),
    init_dirs=(".codex",),
)


class CodexTarget(Target):
    @property
    def name(self) -> str:
        return "codex"

    @property
    def display_name(self) -> str:
        return "OpenAI Codex CLI"

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return _CODEX_DESCRIPTOR

    @property
    def default_entrypoint(self) -> str | None:
        return "codex"

    def detect(self) -> AgentInstall | None:
        import shutil
        from pathlib import Path
        path = shutil.which("codex")
        if not path:
            return None
        binary = Path(path).resolve()
        return AgentInstall(name="codex", binary=binary, install_dir=binary.parent)

    def setting_descriptors(self) -> list[TargetSetting]:
        return [TargetSetting(key="model", description="Model to use", default="gpt-5.5")]
```

Codex overrides nothing else: both its credential files are wholesale copies
(so no `transform_cred`), the descriptor's `init_dirs` creates `.codex`, and
core assembles its argv / binds / env / credential sync from the descriptor.

For agents whose host config or auth files mix portable and non-portable
fields, set `filtered=True` on the relevant `CredFileSpec` and override
`transform_cred` to allowlist or merge (Claude and Goose do this).  Note that
in 1.6.0 kanibako no longer imports your **host** agent config into a box — a
box's non-credential config comes from the agent's curated template
(`@agent.<agent>.template`), not the host.

## Method reference

### `name` / `display_name` (properties, abstract)

`name` is the short machine-readable identifier (`"codex"`, `"goose"`), used in
configuration (`box.agent=codex`) and entry-point registration; it must be
unique.  `display_name` is the human-readable name shown in status output.

### `detect() -> AgentInstall | None`

Auto-detect the agent on the host.  Return an `AgentInstall` describing the
binary location and install root, or `None` if the agent is not installed.
This is usually the only genuinely agent-specific procedural code a plugin
needs.

```python
import shutil
from pathlib import Path

def detect(self) -> AgentInstall | None:
    path = shutil.which("myagent")
    if not path:
        return None
    binary = Path(path).resolve()
    return AgentInstall(name="myagent", binary=binary, install_dir=binary.parent)
```

For agents with a fixed contract path (claude, goose), anchor to that path
rather than `$PATH` to avoid PATH-injection of the binary you bind into the
box.  For agents whose install location is genuinely user-chosen (codex), a
`$PATH` lookup is appropriate — verify the resolved file before trusting it.

### `descriptor` (property)

Return the agent's `PluginDescriptor` (see above).  The default returns `None`,
which is reserved for the built-in `NoAgentTarget` (plain shell, no agent
binary, no credentials).  Every real agent plugin returns a descriptor.

### `check_auth() -> bool`

Called before box launch.  Return `True` if the agent is authenticated on the
host (or if you cannot tell — stay lenient and don't block the launch), `False`
to abort.  The default returns `True`.

### `prepare_host(install, *, auto_auth, data_path) -> None`

Plugin-owned pre-launch host work, run before mounts are built (enable it with
`descriptor.host_prep=True`).  Use it for agent-specific host preparation such
as a synchronous binary-update gate or host auth refresh.  Must not crash the
launch — log and swallow failures.

### `default_common()` / `default_seeds()` / `default_category_binds()`

Declare the directories and files your agent needs kanibako to mount or copy
into a box.  All three return the same shape — a **terminal category key**
mapped to that category's whole **destination-keyed** map:

```
{ "agent.<agent>.<category>": { box_dest: (host_src,),          # no options
                                box_dest: (host_src, "ro") } }  # explicit option
```

`<category>` is one of `common`, `caches`, `seeded`, `synced`, `bindings.ro`,
`bindings.rw`.  The **box destination is the entry's identity** — there is no
entry-name field, and a `key:` in a defaults file is refused rather than
ignored.  `host_src` and `box_dest` stay independent, so a user can repoint the
source without moving the destination.

The three hooks differ only in what they are *for*, and in rooting:

| Hook | Declares | Source rooting |
|---|---|---|
| `default_common()` | your agent's shared dirs (claude's `plugins`, `cache`) | a bare leaf is **rooted at your agent store**: `plugins` → `@meta.agent.<agent>.path/common/plugins` |
| `default_seeds()` | copy-once-at-create seeds — a **copy**, never a mount | none applied; give a self-resolving source |
| `default_category_binds()` | any agent-scope category, sourced from an `@`-ref | **none** — a bare-relative source is refused, not rooted |

An already self-resolving `host_src` (absolute, `~`, `$var`, or an `@`-ref) is
always stored verbatim.  In a **defaults file** `box_dest` is a `$GUEST_HOME`
(or `~`) expression, expanded and normalised by the loader.  From **Python**
there is no expander — `$GUEST_HOME` is not a variable kanibako resolves, and a
launch that meets one fails with `Unknown variable: $GUEST_HOME` — so write the
guest path in full (`/home/agent/.claude/x`).  Either way kanibako owns the host
layout only; the guest path is always spelled out.

These land as the **agent level's declared floor**, under every settings file, so
a user overrides one by writing the same key in a nearer file — the system file
or the agent's own.  An `agent.<agent>.*` key written in a workset or box file is
dropped at assembly with a warning; a box overrides its agent through a
`pref.agent.<agent>.<key>` request instead.  To *remove* one of your entries
rather than repoint it, a user present-`null`s that destination inside the value
(`pref.agent.<agent>.common: {<box_dest>: null}`).  All three default to `{}`.

**Prefer the defaults file to Python.**  The shipped plugins declare `common:`
and `env:` in their `<agent>-defaults.yaml` and read them with the loaders, which
apply the rooting, the `$GUEST_HOME` expansion, the destination normalisation
and — for `env:` — the closed-keyspace check for you:

```python
from kanibako.settings.agent_defaults import load_common, load_category_binds

def default_common(self) -> dict[str, BindArm]:
    return load_common(_DEFAULTS_PACKAGE, _DEFAULTS_FILE, self.name)
```

```yaml
common:
  - host_src: plugins                          # rooted -> agents/<agent>/common/plugins
    box_dest: "$GUEST_HOME/.claude/plugins"
```

`default_seeds()` has no loader — return the mapping from Python.

#### Persona nodes: declare against your own name

Declare everything against **your harness name** (`self.name`).  When a box runs
a *persona* of your agent (`navigator+claude`), kanibako re-keys your table to
that node and **re-roots any source under your agent store onto the persona's
own store**, where it creates a symlink back to yours.  So the persona shares
your content by default, and a user who replaces that symlink with a real
directory gives the persona its own — which is why the source is re-rooted
rather than pointed straight at your store.  A source that is *not* under your
store (an absolute path, a `~`, an unrelated `@`-ref) is left exactly as you
wrote it: that is you naming a specific path, not your store.

⚑ **Do not declare the same box destination twice.**  Declaring one destination
under the **same category key** from two hooks refuses the launch, naming both —
a dest-keyed category holds one entry per destination, so the second would
silently replace the first.  Across categories the rule depends on what each side
is: two `bindings` arms at one destination refuse, and an abstract category
(`common` / `caches` / `seeded`) extending onto a destination a `bindings` entry
already holds refuses, both naming the two declarations.  Two *abstract*
categories at one destination are the residual case — reached only once both
refusals above have been checked — and resolve last-wins with a warning naming
what was dropped.  Layering at one destination is expressed across *scopes*,
never twice within your own declarations.

### `default_envs() -> dict[str, str]`

Declare environment variables your agent needs, as
`agent.<agent>.env.<VAR>` keys mapped to string values (`load_envs` reads an
`env:` section for you).  They are ordinary settings keys, not a private
channel: a user overrides one by writing the same key in a nearer file, and
naming the same variable at a second scope refuses the launch naming both.
Values must be strings — quote `"1"` and `"true"` — and `$GUEST_HOME` is
expanded at load.  Static variables belong here, not in the descriptor.

### `setting_descriptors() -> list[TargetSetting]`

Advertise runtime settings (key, default, optional `choices`).  Users override
them per-box via `kanibako box set`.  Constrained settings (non-empty
`choices`) reject out-of-range values at the CLI; freeform settings accept any
value.

```python
def setting_descriptors(self) -> list[TargetSetting]:
    return [
        TargetSetting(key="model", description="AI model", default="default-model"),
        TargetSetting(key="access", description="Permission mode",
                      default="permissive", choices=("permissive", "default")),
    ]
```

> **The shipped plugins do not write these values in code.**  Each ships a
> `<agent>-defaults.yaml` whose top-level `behavior:` section declares one row per
> key (`key` / `description` / `default`, optional `choices`), and
> `kanibako.settings.agent_defaults.load_behavior()` turns it into the tuple
> `setting_descriptors()` returns.  `default:` is mandatory there — an empty floor
> is written `""` — so an omitted value is a load-time refusal rather than a silent
> empty string.  Declaring the values in the file keeps one declaration site for a
> floor every box of that agent starts from.  A hand-written list as above still
> works; the loader is the route to prefer.

### `transform_cred(spec, src, dst, direction) -> None`

Called by the credential-sync engine only for `CredFileSpec`s with
`filtered=True`.  `direction` is `"in"` (host→box: seed/refresh) or `"out"`
(box→host: writeback).  `src` is `None` when no source is available — decide
whether to write a default `dst` or do nothing.  The default is a plain copy
when `src` exists; override to allowlist or merge (e.g. allowlist portable
fields out of an auth file, or merge an OAuth blob).

### `generate_agent_config() -> AgentConfig`

Return a default `AgentConfig` for this target (agent-specific state knobs,
etc.).  The base returns `AgentConfig(name=self.display_name)`.

## Discovery and registration

Kanibako discovers targets in two ways:

### 1. Entry points (pip-installed plugins)

Register your target class under the `kanibako.agents` group in your
`pyproject.toml`:

```toml
[project.entry-points."kanibako.agents"]
myagent = "my_package:MyTarget"
```

The entry-point **name** (left of `=`) is the target identifier, matching
`Target.name`.  The **value** (right of `=`) points to the `Target` subclass.

### 2. Namespace scan (bind-mounted plugins)

Kanibako also scans `kanibako.plugins.*` for `Target` subclasses.  This lets a
plugin that lives under `kanibako/plugins/` be discovered automatically — even
without pip metadata — and travel with kanibako's bind-mount into nested boxes.
This is how the shipped Claude/Goose/Codex plugins work
(`packages/agent-*/src/kanibako/plugins/<name>/`).

Entry-point-discovered targets take priority; the namespace scan only adds
targets not already found via entry points.

### Resolution

When a user runs `kanibako start`, kanibako calls `discover_targets()`, which
loads all registered entry points and scans `kanibako.plugins.*`, then resolves
the active agent via `agent_select.select_agent()` — the cascade `--agent > box
pref > workset pref > system.agent`, where a box/workset REQUESTS an agent with
`pref.system.agent`.  When nothing in the cascade resolves,
the **installed-agent count** decides (no ordering, no tie-break): exactly one
installed target is used implicitly; **zero or 2+ raise an `AgentResolutionError`**
(install a plugin, or run `kanibako setup` / pass `--agent` to pick one).  This
replaces the older "call `detect()` on each target and fall back to the first" —
`detect()` is now only used to populate the `setup` menu and the diagnostics report,
not to silently pick a launch target.  `kanibako shell` bypasses agent resolution
entirely (a plain shell with no agent binary or credentials).

Select a target for a box explicitly:

```bash
kanibako box set box.agent=myagent
```

## Packaging

### Standalone package (recommended for third-party plugins)

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

[project.entry-points."kanibako.agents"]
myagent = "kanibako_target_myagent:MyTarget"

[tool.setuptools.packages.find]
where = ["src"]
```

Install in development mode with `pip install -e kanibako-target-myagent/`.

### Namespace plugin (for bind-mount propagation)

If your plugin needs to travel with kanibako's bind-mount into nested boxes,
place it under the `kanibako.plugins` namespace instead:

```
packages/plugin-myagent/
  pyproject.toml
  src/
    kanibako/              # no __init__.py (structural only)
      plugins/             # no __init__.py (structural only)
        myagent/
          __init__.py      # exports MyTarget
          target.py
```

The `kanibako/` and `plugins/` directories must **not** have `__init__.py`
files — the base package owns those.  Only your leaf package (`myagent/`) gets
an `__init__.py`.

## Testing

Use `unittest.mock.patch` to mock `shutil.which` and filesystem state, and
`tmp_path` for isolated homes.  The shipped plugins' tests
(`packages/agent-*/tests/`) are the canonical patterns: assert what your
`detect()` resolves, and assert the shape of your `descriptor` (its
`command`, `mode`, `bindings`, `settings`, `cred_files`).

```python
from unittest.mock import patch
from kanibako_target_myagent import MyTarget

def test_detect_found(tmp_path):
    binary = tmp_path / "myagent"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with patch("shutil.which", return_value=str(binary)):
        result = MyTarget().detect()
    assert result is not None and result.name == "myagent"

def test_descriptor_modes():
    desc = MyTarget().descriptor
    assert desc is not None
    assert "start" in desc.mode and "continue" in desc.mode
```

## Box environment

Your agent runs inside a rootless Podman (or Docker) box with:

- **Home directory**: `/home/agent`
- **Working directory**: `/home/agent/workspace` (bind-mounted project)
- **PATH includes**: `/home/agent/.local/bin`
- **User**: `agent` (non-root, UID mapped to host user)
- **Network**: available
- **Vault**:
  - `/home/agent/vault/ro/` — read-only vault (shared files from host)
  - `/home/agent/vault/rw/` — read-write vault
- **Channels**: `/home/agent/channels/` — the inter-box channel tree

Deliver your binary into `/home/agent/.local/bin/` (executable) and larger
install trees under `/home/agent/.local/share/` via descriptor `bindings`.
