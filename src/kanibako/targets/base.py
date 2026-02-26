"""Target base classes: ABC for agent targets, Mount and AgentInstall dataclasses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kanibako.agents import AgentConfig


class ResourceScope(Enum):
    """How an agent resource is shared across projects."""

    SHARED = "shared"    # Shared at workset/account level
    PROJECT = "project"  # Per-project, starts fresh
    SEEDED = "seeded"    # Per-project, seeded from workset template at creation


@dataclass(frozen=True)
class ResourceMapping:
    """Maps an agent resource path to its sharing scope."""

    path: str                    # Relative path within agent home (e.g. "plugins/")
    scope: ResourceScope         # How this resource is shared
    description: str = ""        # Human-readable description


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
    def init_home(self, home: Path, *, auth: str = "shared") -> None:
        """Initialize agent-specific files in the project home directory.

        Called after kanibako core creates .bashrc/.profile.  The target
        should create its own config directories and files (e.g. .claude/).

        *auth* is ``"shared"`` (copy credentials from host) or ``"distinct"``
        (skip credential copy — project manages its own credentials).
        """
        ...

    @property
    def has_binary(self) -> bool:
        """Whether this target requires a host-installed binary."""
        return True

    def check_auth(self) -> bool:
        """Check if the agent is authenticated. Returns True if ok."""
        return True

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
        from kanibako.agents import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate ``[state]`` values into CLI args and env vars.

        Returns ``(cli_args, env_vars)``.  Base implementation ignores all
        state keys.  Subclasses override to handle known keys.
        """
        return [], {}

    @property
    def default_entrypoint(self) -> str | None:
        """Binary name for container entrypoint. None = use bash."""
        return None

    @property
    def config_dir_name(self) -> str:
        """Agent config dir relative to home (e.g. '.claude'). Default: '.{name}'."""
        return f".{self.name}"

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
