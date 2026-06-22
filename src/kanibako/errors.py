"""Kanibako error hierarchy."""


class KanibakoError(Exception):
    """Base exception for all kanibako errors."""


class ConfigError(KanibakoError):
    """Configuration file missing or malformed."""


class ProjectError(KanibakoError):
    """Project path does not exist or cannot be resolved."""


class ContainerError(KanibakoError):
    """Container runtime or image operation failed."""


class ArchiveError(KanibakoError):
    """Archive creation, extraction, or validation failed."""


class GitError(KanibakoError):
    """Git check failed (uncommitted changes, unpushed commits, etc.)."""


class WorksetError(KanibakoError):
    """Workset creation, loading, or manipulation failed."""


class UserCancelled(KanibakoError):
    """User cancelled an interactive prompt."""


class SubjectConflictError(KanibakoError):
    """A positional box subject and ``--box`` named DIFFERENT targets (§Design 8)."""


class AgentResolutionError(KanibakoError):
    """Agent could not be resolved for an agent-requiring command.

    The ``str()`` of the exception (and its subclasses) IS the user-facing
    message — callers surface it verbatim.
    """


class NoAgentSelectedError(AgentResolutionError):
    """Gate-2a: 2+ agents installed but none was chosen (no default)."""


class NoAgentInstalledError(AgentResolutionError):
    """Gate-2b: zero agent plugins are installed."""


class AgentNotInstalledError(AgentResolutionError):
    """A name resolved (cascade/default) but that agent adapter is not installed."""
