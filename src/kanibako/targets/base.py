"""Target base classes: ABC for agent targets, Mount and AgentInstall dataclasses."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig


@dataclass(frozen=True)
class TargetSetting:
    """Declares a runtime setting that a target plugin supports.

    Used by ``setting_descriptors()`` to advertise what settings exist,
    their defaults, and (optionally) valid choices.
    """

    key: str                     # Setting key in agent state dict (e.g. "model")
    description: str             # Human-readable description
    default: str = ""            # Default value when not overridden
    choices: tuple[str, ...] = ()  # Valid values; empty = freeform


@dataclass(frozen=True)
class Mount:
    """A volume mount for a container."""

    source: Path
    destination: str
    options: str = ""  # e.g. "ro"

    def to_volume_arg(self) -> str:
        """Return the -v argument string for podman/docker."""
        base = f"{self.source}:{self.destination}"
        return f"{base}:{self.options}" if self.options else base


@dataclass
class AgentInstall:
    """Information about an agent installation on the host."""

    name: str  # e.g. "claude"
    binary: Path  # host symlink/path to agent binary
    install_dir: Path  # root of agent installation
    # Optional host launcher path (the on-disk entrypoint the plugin owns and
    # binds into the box AS-IS, e.g. ~/.local/bin/claude).  Anchored to the
    # agent's contract path by the plugin rather than resolved via $PATH.
    # Defaults to None so agents that don't set it are unaffected.
    launcher: Path | None = None


class BindKind(Enum):
    """Whether a binding mounts a single file or a directory."""

    FILE = "file"
    DIR = "dir"


class HostSrcOrigin(Enum):
    """Where a binding's DEFAULT host source path comes from (before any user cascade override)."""

    LAUNCHER = "launcher"        # AgentInstall.launcher (detection-derived)
    INSTALL_DIR = "install_dir"  # AgentInstall.install_dir
    BINARY = "binary"            # AgentInstall.binary
    LITERAL = "literal"          # a fixed Path in the descriptor (literal_src)


class BindScope(Enum):
    """How widely a binding applies + its failure semantics."""

    AGENT_CRITICAL = "agent_critical"  # delivery essential (binary/launcher/share); source-exists SAFE-FAIL; ro
    AGENT = "agent"                    # agent-level share (e.g. plugins): per-agent across worksets; overridable; may be rw


@dataclass(frozen=True)
class Binding:
    """One bound element (delivery binary/launcher/share or an agent share).

    The resolved host source = user cascade override (agent.<name>.binding.<key>) ELSE the *origin*:
    a detection field (LAUNCHER/INSTALL_DIR/BINARY) or literal_src (LITERAL).  AGENT_CRITICAL bindings
    keep source-exists safe-fail + bind-as-is inode-pin + core dest-symlink clearing; AGENT shares are
    best-effort (a missing/suppressed share is fine).
    """

    key: str                          # stable override key -> agent.<name>.binding.<key>
    origin: HostSrcOrigin
    box_dest: str
    kind: BindKind
    scope: BindScope
    ro: bool = True
    literal_src: Path | None = None   # only when origin == LITERAL


class Channel(Enum):
    """Where a value-bearing arg is emitted: an argv flag or an environment variable."""

    FLAG = "flag"
    ENV = "env"


@dataclass(frozen=True)
class SettingArg:
    """A value-bearing crab setting routed to an argv flag OR an env var (e.g. model, provider)."""

    setting_key: str                  # crab setting supplying the value ("model", "provider")
    channel: Channel
    flag: tuple[str, ...] = ()        # FLAG form, e.g. ("--model",)
    env_var: str = ""                 # ENV form, e.g. "GOOSE_MODEL"


@dataclass(frozen=True)
class SafeBypass:
    """The safe-mode TOGGLE, with SYMMETRIC emissions for both polarities.

    Two independent emissions, selected by the resolved effective safe-mode:

    * safe-mode OFF (bypass ON, ``-A``/autonomous): emit the UNSAFE form —
      ``flag`` (FLAG channel) or ``env_var=env_value`` (ENV channel).
    * safe-mode ON (secure, ``-S``): emit the restrictive/SECURE form —
      ``secure_flag`` (FLAG channel) or ``env_var=secure_env_value`` (ENV
      channel).  Empty secure fields emit NOTHING on safe-ON, which is correct
      for an agent whose UNSET default is already safe (claude/codex); an agent
      whose unset default is UNSAFE (goose: ``GOOSE_MODE`` defaults to ``auto``)
      MUST set the secure field so ``-S`` actually restricts it.

    Special vs SettingArg: it's driven by the resolved effective safe-mode, not a plain setting value.
    *setting_key* is an OPTIONAL persisted default (claude "access"); empty = per-launch -A/-S only (goose/codex).
    """

    channel: Channel
    flag: tuple[str, ...] = ()        # emitted when effective safe-mode is OFF (FLAG channel)
    env_var: str = ""                 # ENV form (e.g. goose GOOSE_MODE -> value "auto")
    env_value: str = ""               # value to set for env_var when ENV channel + effective safe-mode is OFF (e.g. goose "auto")
    secure_env_value: str = ""        # value to set for env_var when ENV channel + effective safe-mode is ON (e.g. goose "approve"); empty = emit nothing on safe-ON
    secure_flag: tuple[str, ...] = ()  # FLAG form emitted when effective safe-mode is ON; empty = emit nothing on safe-ON (claude/codex default-safe)
    setting_key: str = ""


@dataclass(frozen=True)
class Operation:
    """A STANDALONE op invocation fragment (no session mode); e.g. exec/headless. Spliced after `command`."""

    fragment: tuple[str, ...]


class Cadence(Enum):
    """Credential/config file sync cadence."""

    SYNC = "sync"            # bidirectional, mtime-gated each launch (credentials/token files)
    SEED_ONCE = "seed_once"  # one-way host->project at init, never written back (config files)


@dataclass(frozen=True)
class CredFileSpec:
    """A credential/config file's lifecycle. The divergent filter/merge PAYLOAD stays a plugin hook (filtered=True)."""

    home_rel: str                     # path under the project shell home (e.g. ".claude/.credentials.json")
    host_rel: str                     # path under the host home (e.g. ".claude/.credentials.json")
    cadence: Cadence = Cadence.SYNC
    mtime_gate: bool = True           # only meaningful for SYNC
    filtered: bool = False            # True -> plugin transform_cred hook runs


@dataclass(frozen=True)
class PluginDescriptor:
    """Declarative data a plugin exposes via Target.descriptor. Divergent LOGIC stays in Target hook methods.

    Maps onto the per-agent keyspace (``settings-keyspace-1.6.0-target.md`` §2d),
    keyed by ``@agent.<agent>.meta.name`` (the plugin's ``name`` property).  A few
    §2d keys are *informational* — they describe where core derives a path, not a
    descriptor field: ``agent.<agent>.path`` (``@system.agents/<name>``, derived in
    core), ``agent.<agent>.template`` (the layer-2 seed source, owned by the
    templates layer), and ``agent.<agent>.transform`` (a binary-patch cache label;
    claude's tweakcc is a bespoke path, not a descriptor hook).  The ``synced``
    category in §2d is the spec VIEW of ``cred_files`` (realized by the credsync
    engine); ``critical`` is the set of ``AGENT_CRITICAL`` ``bindings`` keys.
    """

    command: tuple[str, ...]                       # box argv prefix (e.g. ("claude",))
    bindings: tuple[Binding, ...]                  # ALL bound elements; ordered; >=1
    mode: dict[str, tuple[str, ...]]               # INTERACTIVE launch ONLY: {"start": (...), "continue": (...)}
    operations: dict[str, Operation] = field(default_factory=dict)  # pass-1: {"exec": ...}; standalone, no mode
    safe_bypass: SafeBypass | None = None
    settings: tuple[SettingArg, ...] = ()
    container_env: dict[str, str] = field(default_factory=dict)
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False           # True -> core calls Target.prepare_host before mounts
    init_dirs: tuple[str, ...] = ()   # extra dirs to mkdir in the project home (home-relative), e.g. (".claude",)


def _validate_agent_binary(binary: Path) -> str | None:
    """Validate that *binary* is a usable host agent executable.

    Returns a short, human-readable REASON string when the binary is
    unusable, or ``None`` when it looks fine.  The check runs on the HOST
    path (``AgentInstall.binary``) at launch time, before the container is
    mounted/run.

    Deliberately LENIENT to avoid false positives on legitimate native
    binaries OR shebang wrappers — it fails only when the path is:

    * missing,
    * zero bytes (the documented 0-byte/corrupt-binary incident), or
    * not marked executable (``os.access(..., os.X_OK)`` is False).

    An optional extra guard rejects a file whose first bytes are all-NUL
    (a clear sign of a truncated/corrupt download) without requiring ELF
    magic — so native binaries and ``#!`` wrappers both pass.
    """
    try:
        path = Path(binary)
    except TypeError:
        return f"invalid binary path: {binary!r}"

    if not path.exists():
        return f"binary not found at {path}"
    if not path.is_file():
        return f"binary path is not a regular file at {path}"

    try:
        size = path.stat().st_size
    except OSError as e:
        return f"cannot stat binary at {path}: {e}"
    if size == 0:
        return f"binary is empty (0 bytes) at {path}"

    if not os.access(path, os.X_OK):
        return f"binary present but not executable at {path}"

    # Lenient corruption check: a non-empty file whose leading bytes are all
    # NUL is not a valid native binary or shebang wrapper.  We read only a
    # few bytes and never reject on read failure (stay lenient).
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
        if head and set(head) == {0}:
            return f"binary appears corrupt (leading bytes are all NUL) at {path}"
    except OSError:
        pass

    return None


class Target(ABC):
    """Abstract base class for agent targets.

    A target encapsulates all agent-specific logic: detection, binary mounting,
    home directory initialization, credential management, and CLI argument
    building.  Kanibako's core is agent-agnostic; all agent knowledge lives
    in Target implementations.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this target (e.g. 'claude')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g. 'Claude Code')."""
        ...

    @abstractmethod
    def detect(self) -> AgentInstall | None:
        """Detect the agent installation on the host.

        Returns an AgentInstall if found, or None if the agent is not installed.
        """
        ...

    @property
    def has_binary(self) -> bool:
        """Whether this target requires a host-installed binary."""
        return True

    @property
    def descriptor(self) -> "PluginDescriptor | None":
        """Declarative plugin descriptor; None only for the built-in no-agent shell.

        kanibako core assembles launch argv, bindings, container env, and
        credential sync declaratively from this descriptor (the descriptor-only
        plugin system; the legacy per-method launch hooks were removed for the
        public release).  Every shipped agent plugin returns a descriptor; the
        sole descriptor-less target is :class:`~kanibako.targets.no_agent.NoAgentTarget`,
        which launches a plain shell with no agent argv and no delivery binds.
        """
        return None

    def check_auth(self) -> bool:
        """Check if the agent is authenticated. Returns True if ok."""
        return True

    def prepare_host(self, install: "AgentInstall", *, auto_auth: bool, data_path: Path) -> None:
        """Plugin-owned pre-launch host preparation.

        Called by core ``start.py`` once a host install is detected, BEFORE
        mounts are built, so the plugin can own everything agent-specific that
        must touch the host before launch (e.g. updating the host binary to a
        stable version, refreshing host auth with the right environment).

        Core stays agent-agnostic: it just invokes this hook.  Implementations
        MUST NOT crash the launch — a failure here should be logged and
        swallowed; a hard auth/binary failure is surfaced separately via
        ``check_auth`` / ``_validate_agent_binary``.

        *install* is the detected :class:`AgentInstall`; *auto_auth* indicates
        whether automated browser auth should be attempted; *data_path* is the
        kanibako data dir (for auth cookie storage).  Default: no-op.
        """
        return None

    def default_shares(self) -> dict[str, str]:
        """Declare default AGENT-scope shares/caches for this agent.

        Returns a mapping of full scoped category keys
        (``agent.shared.<name>`` / ``agent.caches.<name>``) to
        ``host_src:box_dest`` bind expressions. These are injected as the AGENT
        level's *declared defaults* (``default_categories``) in the category
        resolver — a user can override or suppress (terminal "") any of them at
        a more-specific level. The default returns {} (no shares).
        """
        return {}

    def default_seeds(self) -> dict[str, str]:
        """Declare default copy-once-at-init seeds for this agent.

        Returns a mapping of full seed keys (``agent.seeded.<name>``) to
        ``host_src:box_dest`` expressions, injected as the AGENT level's declared
        defaults (``default_categories``) in the category resolver. A user can
        override or suppress (terminal "" or the "empty" sentinel) any of them at
        a more-specific level. The default returns {} (no seeds). No target ships
        a default seed yet.
        """
        return {}

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare what runtime settings this target supports.

        Returns a list of TargetSetting entries describing the key name,
        default value, valid choices, and human-readable description.

        The default returns an empty list (no declared settings).
        """
        return []

    def generate_agent_config(self) -> AgentConfig:
        """Return a default AgentConfig for this target.

        Subclasses should override to provide agent-specific defaults
        (template variant, state knobs, shared caches, etc.).
        """
        from kanibako.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate crab-state values into CLI args and env vars.

        Returns ``(cli_args, env_vars)``.  Base implementation ignores all
        state keys.  Subclasses override to handle known keys.
        """
        return [], {}

    @property
    def default_entrypoint(self) -> str | None:
        """Binary name for container entrypoint. None = use bash."""
        return None

    def should_retry_new_session(self, output: str) -> bool:
        """Check if agent output indicates ``--continue`` failed and a new session should be started."""
        return False

    @property
    def setup_entrypoint(self) -> str | None:
        """Container entrypoint (binary) for the one-time interactive setup.

        ``None`` (the default) means the target declares no setup step; the
        auth-probe setup branch in ``start.py`` (and ``agent reauth``) is skipped
        entirely — a failed :meth:`check_auth` then errors out as before.  A
        target that needs an in-box setup (e.g. goose -> ``goose configure``)
        returns its setup binary here and the sub-command in :attr:`setup_args`.
        When :meth:`check_auth` fails for such a target, ``start.py`` runs this
        command INTERACTIVELY in the box (inherits stdio) so the user can complete
        configuration/login in-box, then proceeds with the normal launch.  Setup
        runs in box-state, which persists across reattach (1.6.0 "no host-config
        import" design).
        """
        return None

    @property
    def setup_args(self) -> list[str]:
        """Arguments for the one-time setup command (see :attr:`setup_entrypoint`)."""
        return []

    @property
    def config_dir_name(self) -> str:
        """Agent config dir relative to home (e.g. '.claude'). Default: '.{name}'."""
        return f".{self.name}"

    def credential_check_path(self, home: Path) -> Path | None:
        """Path to check for credential existence, or None."""
        return None

    def invalidate_credentials(self, home: Path) -> None:
        """Remove credential files when switching to distinct auth. Default: no-op."""

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        """Transform a FILTERED credential/config file between host and project.

        Called by the credential-sync engine ONLY for specs with ``filtered=True``.
        *direction* is ``"in"`` (host->project: seed/refresh) or ``"out"``
        (project->host: writeback).  *src* is ``None`` when no source file is
        available (e.g. distinct auth, or the host file is absent at seed time) —
        the plugin decides whether to write a default ``dst`` or do nothing.

        Default: plain copy when *src* exists (so a plugin that flags a file
        ``filtered`` but doesn't override gets a sensible wholesale copy).
        Plugins override to filter/merge (claude claudeAiOauth merge + .claude.json
        allowlist; goose config.yaml allowlist).
        """
        import shutil
        if src is not None and Path(src).is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

    def refresh_credentials(self, home: Path) -> None:
        """Refresh agent credentials from host into the project home.

        Default: no-op.  Descriptor-native plugins sync creds via
        ``descriptor.cred_files`` (core's credsync engine); legacy plugins override.
        """
        return None

    def writeback_credentials(self, home: Path) -> None:
        """Write back credentials from project home to host.

        Default: no-op.  Descriptor-native plugins sync creds via
        ``descriptor.cred_files`` (core's credsync engine); legacy plugins override.
        """
        return None

    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None:
        """Plugin-specific post-session writeback BEYOND ``cred_files`` specs.

        Called by core on every session-end path (clean exit, detach, reattach-
        exit, ``kanibako stop``) AFTER the descriptor ``cred_files`` writeback,
        for state that can't be modelled as a SYNC ``CredFileSpec``.  The motivating
        case is claude's ``~/.claude.json`` ``oauthAccount``: the box's login writes
        the account block there, and it must reach the host, but the file can't be a
        normal SYNC spec because that would also IMPORT host->project (removed in
        1.6.0) AND a wholesale copy would clobber host-specific ``machineID`` /
        ``userID`` / ``projects``.  So the plugin MERGES just its own keys back.

        Default: no-op.  MUST be defensive — never raise on a malformed/absent file
        (core wraps it but a clean teardown is the contract).
        """
        return None
