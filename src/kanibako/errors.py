"""The kanibako error hierarchy: what ``cli.py`` catches (not every exception in the tree)."""


# ⚑ ``cli.py`` catches ONLY this base (plus UserCanceled): a class added here that does not
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


class BoxNameError(ProjectError):
    """A new box name breaks the box-name rule; each caller adds its cure."""

    def __init__(self, message: str, name: str) -> None:
        super().__init__(message)
        self.name = name

    def with_cure(self, cure: str) -> str:
        return f"{self} {cure}"


class DerivedBoxNameError(BoxNameError):
    """A box name taken from a directory breaks the box-name rule."""


class AmbiguousNameError(ProjectError):
    """A bare box NAME matched several boxes — exclude when catching ProjectError."""


class ContainerError(KanibakoError):
    """Container runtime or image operation failed."""


class PackagingError(KanibakoError):
    """kanibako's packaged data is missing or incomplete; the install is broken."""


class ArchiveError(KanibakoError):
    """Archive creation, extraction, or validation failed (no in-tree raiser)."""


class GitError(KanibakoError):
    """Git check failed (uncommitted changes, unpushed commits, etc.)."""


class WorksetError(KanibakoError):
    """Workset creation, loading, or manipulation failed."""


class LegacyWorksetIdentityError(WorksetError):
    """A workset root's ``workset.yaml`` still carries a RETIRED workset identity table."""


class ReservedWorksetNameError(WorksetError):
    """A registered workset carries a name 1.8 reserves; the message carries the cure."""


class LegacyRegistryIdentityError(WorksetError):
    """A per-workset ``registry.yaml`` still carries a RETIRED ``workset:``/``projects:`` section."""


class UserCanceled(KanibakoError):
    """User canceled an interactive prompt."""


class SubjectConflictError(KanibakoError):
    """A positional box subject and ``--box`` named DIFFERENT targets (§Design 8)."""


class AgentResolutionError(KanibakoError):
    """Agent could not be resolved for an agent-requiring command."""


class AgentUnsetError(AgentResolutionError):
    """``system.agent`` is UNSET at every tier: setup has never chosen one (spec §2b).

    ⚑ The PAIR with :class:`AgentNoDefaultError`, and they are not interchangeable —
    that is the whole of the 2026-09-19 ruling. UNSET means *nothing has ever set the
    key*, so the cure is ``kanibako setup``; present-``None`` means *a settings file
    deliberately declined to name a default*, so the cure is naming one. Neither ever
    auto-selects: the installed-agent COUNT decides nothing.
    """


class AgentNoDefaultError(AgentResolutionError):
    """``system.agent`` resolved to present-``None``: no default is set (spec §2b).

    ⚑ Reachable BY TYPO — YAML reads ``null``, ``Null``, ``NULL``, ``~`` and a bare
    key with nothing after the colon as Python ``None`` — so the message names the
    spellings. (``None``/``none`` are STRINGS and fail as an unknown agent instead,
    which is why this message must not read like that one.)
    """


class AgentNotInstalledError(AgentResolutionError):
    """A name resolved (explicit/cascade/default) but that agent adapter is not installed."""
