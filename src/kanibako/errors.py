"""Kanibako error hierarchy."""


class KanibakoError(Exception):
    """Base exception for all kanibako errors."""


class ConfigError(KanibakoError):
    """Configuration file missing or malformed."""


class CategoryCollisionError(ConfigError):
    """Two category declarations target one resolved ``box_dest`` (spec §0).

    A user CONFIGURATION fault (hence a :class:`ConfigError`, like the
    ``synced``↔``binding`` raise it joins), carried STRUCTURED so tests assert on
    fields rather than on message text and so a CLI seam can enrich the rendered
    text with the scope→file mapping the pure resolver does not know.

    *kind* discriminates the §0 table row that fired:

    ``"binding_vs_binding"``
        Row 1 — two ``bindings.{ro,rw}`` (or a ``bindings.*`` and a
        ``secret_path``) at one destination. ERROR always, any scope, any mode.
    ``"extension_onto_occupied"``
        Row 3 — an ABSTRACT declaration (``common`` / ``caches``) deriving a
        binding onto a destination an explicit binding already occupies. The
        base survives; the EXTENSION is refused.
    ``"synced_vs_binding"``
        The pre-existing copy-vs-mount rule (spec §0 L119-124), unchanged by the
        collision table — a ``synced`` COPY cannot override a live MOUNT.

    *box_dest* is the collision key. *entries* is the ordered tuple of
    ``(key, host_src)`` pairs that participate, declaration key first — the
    rendered message names them in that order.
    """

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
    """A template/seed copy tried to write OUTSIDE its scope's allowed surface.

    Raised by the one shared copier (:func:`kanibako.templates.copy_tree`) on any of
    the four enforcement points spec §2a requires of it:

    * an entry whose first path component is not in the SCOPE'S WHITELIST
      (deny-by-default — an unlisted entry is an ERROR, never a silent skip);
    * a resolved DESTINATION outside the scope store root (the ``..`` escape);
    * a SOURCE entry that is a symlink (``x -> ~/.ssh/id_ed25519`` would otherwise
      have its TARGET's bytes copied into a box home — the exfiltration §2a's "USER
      DATA AND SECRETS" note is about);
    * a DESTINATION whose real path escapes the destination subtree through a
      symlinked intermediate directory.

    It is a hard refusal rather than a skip because the whitelist is a CORRECTNESS
    property, not a style rule: a template that could plant ``settings.yaml`` at a
    scope root would be planting ``meta.<scope>.settings``, the cascade's own last
    word, and at workset scope the same escape reaches ``registry.yaml`` (the
    AUTHORITATIVE box membership), ``auth/``, ``vault/`` and ``workspaces/`` — the
    user's credentials and code.
    """


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
