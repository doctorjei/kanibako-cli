"""The kanibako error hierarchy: what ``cli.py`` catches (not every exception in the tree)."""


# ⚑ ``cli.py`` catches ONLY this base (plus UserCancelled): a class added here that does not
# subclass it reaches the user as a traceback, not an ``Error:`` line.
class KanibakoError(Exception):
    """Base of the kanibako error hierarchy — what ``cli.py`` catches."""


class ConfigError(KanibakoError):
    """Configuration missing, malformed, or refused."""


class CategoryCollisionError(ConfigError):
    """Two category declarations target one resolved ``box_dest`` (spec §0)."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        box_dest: str,
        entries: "tuple[tuple[str, str | None], ...]" = (),
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.box_dest = box_dest
        self.entries = entries


class TemplateScopeError(ConfigError):
    """A template/seed copy tried to write OUTSIDE its scope's allowed surface."""


class ProjectError(KanibakoError):
    """Project cannot be resolved, or its name/location is refused."""


class ContainerError(KanibakoError):
    """Container runtime or image operation failed."""


class ArchiveError(KanibakoError):
    """Archive creation, extraction, or validation failed (no in-tree raiser)."""


class GitError(KanibakoError):
    """Git check failed (uncommitted changes, unpushed commits, etc.)."""


class WorksetError(KanibakoError):
    """Workset creation, loading, or manipulation failed."""


class LegacyWorksetIdentityError(WorksetError):
    """A workset root still keeps its identity in ``settings.yaml``, not ``registry.yaml``."""


class UserCancelled(KanibakoError):
    """User cancelled an interactive prompt."""


class SubjectConflictError(KanibakoError):
    """A positional box subject and ``--box`` named DIFFERENT targets (§Design 8)."""


class AgentResolutionError(KanibakoError):
    """Agent could not be resolved for an agent-requiring command."""


class NoAgentSelectedError(AgentResolutionError):
    """Gate-2a: 2+ REAL agents installed but none was chosen (no default)."""


class NoAgentInstalledError(AgentResolutionError):
    """Gate-2b: zero REAL agent plugins are installed."""


class AgentNotInstalledError(AgentResolutionError):
    """A name resolved (explicit/cascade/default) but that agent adapter is not installed."""
