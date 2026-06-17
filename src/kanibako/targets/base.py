"""Target base classes: ABC for agent targets, Mount and AgentInstall dataclasses."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.crabs import CrabConfig


class ResourceScope(Enum):
    """How an agent resource is shared across projects."""

    SHARED = "shared"    # Shared across a workset (or the default workset)
    PROJECT = "project"  # Per-project, starts fresh
    SEEDED = "seeded"    # Per-project, seeded from workset template at creation


@dataclass(frozen=True)
class ResourceMapping:
    """Maps an agent resource path to its sharing scope.

    *base* anchors *path*: empty keeps the current behavior (relative to the
    agent config dir); non-empty roots *path* at the project home under that
    prefix (e.g. ".local/share/goose/sessions").
    """

    path: str                    # Relative path within agent home (e.g. "plugins/")
    scope: ResourceScope         # How this resource is shared
    description: str = ""        # Human-readable description
    base: str = ""               # anchor for `path`: "" = relative to the agent config dir (current behavior);
                                 # non-empty = relative to the project home under this prefix (e.g. ".local/share/goose/sessions")


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
    SHARED_STORE = "shared_store"  # kanibako shared storage, agent-namespaced (global_shared/<agent_id>/<src_rel>)
    LITERAL = "literal"          # a fixed Path in the descriptor (literal_src)


class BindScope(Enum):
    """How widely a binding applies + its failure semantics."""

    AGENT_CRITICAL = "agent_critical"  # delivery essential (binary/launcher/share); source-exists SAFE-FAIL; ro
    AGENT = "agent"                    # agent-level share (e.g. plugins): per-agent across worksets; overridable; may be rw


@dataclass(frozen=True)
class Binding:
    """One bound element (delivery binary/launcher/share or an agent share).

    The resolved host source = user cascade override (crab.<name>.binding.<key>) ELSE the *origin*:
    a detection field (LAUNCHER/INSTALL_DIR/BINARY), shared-store/<agent_id>/<src_rel> (SHARED_STORE),
    or literal_src (LITERAL).  AGENT_CRITICAL bindings keep source-exists safe-fail + bind-as-is inode-pin
    + core dest-symlink clearing; AGENT shares are best-effort (a missing/suppressed share is fine).
    """

    key: str                          # stable override key -> crab.<name>.binding.<key>
    origin: HostSrcOrigin
    box_dest: str
    kind: BindKind
    scope: BindScope
    ro: bool = True
    src_rel: str = ""                 # rel path under the shared store (SHARED_STORE only); ignored otherwise
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
    """The safe-mode TOGGLE (emitted when effective safe-mode is OFF).

    Special vs SettingArg: it's driven by the resolved effective safe-mode, not a plain setting value.
    *setting_key* is an OPTIONAL persisted default (claude "access"); empty = per-launch -A/-S only (goose/codex).
    """

    channel: Channel
    flag: tuple[str, ...] = ()        # emitted when effective safe-mode is OFF (FLAG channel)
    env_var: str = ""                 # ENV form (e.g. goose GOOSE_MODE -> value "auto")
    env_value: str = ""               # value to set for env_var when ENV channel + effective safe-mode is OFF (e.g. goose "auto")
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
    """Declarative data a plugin exposes via Target.descriptor. Divergent LOGIC stays in Target hook methods."""

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

    @abstractmethod
    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        """Return volume mounts needed to make the agent binary available in the container."""
        ...

    @abstractmethod
    def init_home(self, home: Path, *, group_auth: bool = True) -> None:
        """Initialize agent-specific files in the project home directory.

        Called after kanibako core creates .bashrc/.profile.  The target
        should create its own config directories and files (e.g. .claude/).

        *group_auth* is ``True`` (copy credentials from host) or ``False``
        (skip credential copy — project manages its own credentials).
        """
        ...

    @property
    def has_binary(self) -> bool:
        """Whether this target requires a host-installed binary."""
        return True

    @property
    def descriptor(self) -> "PluginDescriptor | None":
        """Declarative plugin descriptor, or None for legacy plugins.

        When non-None, kanibako core assembles launch argv, bindings, container
        env, and credential sync declaratively from this descriptor instead of
        the legacy per-method hooks (build_cli_args / binary_mounts / ...).
        Default None keeps every existing plugin on the legacy path unchanged.
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

    def resource_mappings(self) -> list[ResourceMapping]:
        """Declare how agent resources are shared across projects.

        Returns a list of ResourceMapping entries describing which paths
        within the agent's home directory are shared, project-scoped, or
        seeded from workset defaults.

        The default returns an empty list, meaning all agent resources
        are treated as project-scoped (the current behavior).

        Paths are relative to the agent's config directory within the
        project shell (e.g. ".claude/" for ClaudeTarget).
        """
        return []

    def default_shares(self) -> dict[str, str]:
        """Declare default scoped shares for this agent's crab.

        Returns a mapping of full scoped-share keys
        ({scope}.path.share_{ro,rw}.{name}) to host_src:guest_dest bind
        expressions. These become the CRAB level's *declared defaults* in the
        share resolver — a user can override or suppress (terminal "") any of
        them at a more-specific level. The default returns {} (no shares).
        """
        return {}

    def default_seeds(self) -> dict[str, str]:
        """Declare default copy-once-at-init seeds for this agent's crab.

        Returns a mapping of full seed keys ({scope}.path.seeded.{name}) to
        host_src:guest_dest expressions, injected as the CRAB level's declared
        defaults in the seed resolver. A user can override or suppress (terminal
        "" or the "empty" sentinel) any of them at a more-specific level. The
        default returns {} (no seeds). No target ships a default seed yet.
        """
        return {}

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare what runtime settings this target supports.

        Returns a list of TargetSetting entries describing the key name,
        default value, valid choices, and human-readable description.

        The default returns an empty list (no declared settings).
        """
        return []

    def generate_crab_config(self) -> CrabConfig:
        """Return a default CrabConfig for this target.

        Subclasses should override to provide agent-specific defaults
        (template variant, state knobs, shared caches, etc.).
        """
        from kanibako.crabs import CrabConfig as _CrabConfig

        return _CrabConfig(name=self.display_name)

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
    def config_dir_name(self) -> str:
        """Agent config dir relative to home (e.g. '.claude'). Default: '.{name}'."""
        return f".{self.name}"

    def instruction_files(self) -> list[str]:
        """Return filenames that should be layered across template levels.

        These files are merged (concatenated with section markers) from
        three layers: kanibako base, template, and user project.  Each
        filename is relative to the agent's config dir within the shell
        directory (e.g. ``"CLAUDE.md"`` lives at ``shell/.claude/CLAUDE.md``).

        The default returns an empty list (no instruction files merged).
        """
        return []

    def credential_check_path(self, home: Path) -> Path | None:
        """Path to check for credential existence, or None."""
        return None

    def invalidate_credentials(self, home: Path) -> None:
        """Remove credential files when switching to distinct auth. Default: no-op."""

    @abstractmethod
    def refresh_credentials(self, home: Path) -> None:
        """Refresh agent credentials from host into the project home."""
        ...

    @abstractmethod
    def writeback_credentials(self, home: Path) -> None:
        """Write back credentials from project home to host."""
        ...

    @abstractmethod
    def build_cli_args(
        self,
        *,
        safe_mode: bool,
        resume_mode: bool,
        new_session: bool,
        is_new_project: bool,
        extra_args: list[str],
    ) -> list[str]:
        """Build command-line arguments for the agent entrypoint."""
        ...
