"""Target base classes: ABC for agent targets, Mount and AgentInstall dataclasses."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig
    from kanibako.vscode_config import CodexModelProvider

# A STRUCTURED category bind default (spec §2a "REPRESENTATION"): a 2- or
# 3-element ``(host_src, box_dest[, options])`` tuple — NOT a colon-joined
# string. Emitted by ``default_common()`` / ``default_seeds()`` and consumed by
# :func:`kanibako.settings_resolve.unpack_bind` through the category resolver.
BindDefault = tuple[str, str] | tuple[str, str, str]


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

    The resolved host source = user cascade override (agent.<name>.bindings.{ro,rw}.<key>) ELSE the *origin*:
    a detection field (LAUNCHER/INSTALL_DIR/BINARY) or literal_src (LITERAL).  AGENT_CRITICAL bindings
    keep source-exists safe-fail + bind-as-is inode-pin + core dest-symlink clearing; AGENT shares are
    best-effort (a missing/suppressed share is fine).
    """

    key: str                          # stable override key -> agent.<name>.bindings.{ro,rw}.<key>
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
    *setting_key* names the persisted default key the launch reader redeems (all
    three shipped agents = ``"auto_approve"``, spec §2d L556, default True); empty =
    per-launch ``-A``/``-S`` only.
    """

    channel: Channel
    flag: tuple[str, ...] = ()        # emitted when effective safe-mode is OFF (FLAG channel)
    env_var: str = ""                 # ENV form (e.g. goose GOOSE_MODE -> value "auto")
    env_value: str = ""               # value to set for env_var when ENV channel + effective safe-mode is OFF (e.g. goose "auto")
    secure_env_value: str = ""        # value to set for env_var when ENV channel + effective safe-mode is ON (e.g. goose "approve"); empty = emit nothing on safe-ON
    secure_flag: tuple[str, ...] = ()  # FLAG form emitted when effective safe-mode is ON; empty = emit nothing on safe-ON (claude/codex default-safe)
    setting_key: str = ""


@dataclass(frozen=True)
class PersonaSpec:
    """How a PERSONA's alternate endpoint + bearer token are DELIVERED for this harness.

    A persona (``agent.<persona>℘<harness>``) points the harness at a third-party
    model endpoint with a bearer token.  HOW those two values reach the box is
    HARNESS-specific, so the plugin declares it here (consulted by the persona
    preflight in ``start.py`` instead of the old claude-hardcoded constants):

    * *token_var* — the ``secret_path`` key (== the in-box env var) that carries the
      bearer token.  Claude uses the FIXED ``ANTHROPIC_AUTH_TOKEN``.  A config-file
      harness (codex) leaves this EMPTY → DYNAMIC: the single configured
      ``secret_path`` key IS the token var, and it doubles as the model-provider
      ``env_key`` (so the config-generated provider reads the same env).
    * *endpoint_delivery* — ``"env"`` (claude: the endpoint rides the descriptor's
      ``endpoint``→``ANTHROPIC_BASE_URL`` ENV :class:`SettingArg`) or ``"config_file"``
      (codex: the endpoint is written into ``~/.codex/config.toml``'s
      ``[model_providers.<id>]`` block by the launch config generator — NOT an env
      var).  ``"config_file"`` ALSO disables the claude-shaped B3 host-dir auto-adopt
      (MVP keyspace-config only).
    * *wire_api* — the config-file harness's model-provider wire protocol (codex
      ``[model_providers.<id>].wire_api``); default ``"responses"`` (Codex removed
      the ``"chat"`` wire, openai/codex#7782).  Ignored for ``"env"`` delivery.
    * *host_dir_adopt* — whether an ENV-delivery persona with an UNSET keyspace
      endpoint may auto-adopt a config from the CLAUDE-shaped host dir
      ``~/.config/claude/<persona>/`` (the B3 gate in ``start.py``).  Claude is the
      ONLY harness whose class-setup script writes that dir, so claude keeps the
      default ``True``; any OTHER env-delivery harness (goose) sets ``False`` so it
      resolves from the KEYSPACE only and errors with a HARNESS-appropriate
      keyspace-config message instead of consulting/erroring against claude's dir.
      A config-file harness (codex) is unaffected either way — B3's ENV gate already
      excludes it — but it declares ``False`` too for correctness/clarity.
    * *provider_pin* — ``(setting_key, value)`` pairs FORCE-applied to the launch's
      effective setting state WHENEVER this persona resolves an active endpoint (so a
      harness whose endpoint requires a specific provider can't be misconfigured).
      Goose pins ``("provider", "openai")`` → the descriptor's ``provider``→
      ``GOOSE_PROVIDER`` :class:`SettingArg` then emits ``GOOSE_PROVIDER=openai`` in
      the box.  Empty (claude/codex) = no pin (byte-identical); a BARE box (no active
      endpoint) is never touched.
    * *model_required* — whether an ENV-delivery persona with a resolved endpoint but
      NO cascade-resolved model is a hard error (parity with the codex config-file
      model gate).  Claude keeps the default ``False`` (its model rides its own
      channels / harness default); goose sets ``True`` (a third-party OpenAI-compatible
      endpoint has no meaningful default model).

    A target with NO :class:`PersonaSpec` (``descriptor.persona is None``) resolves
    exactly as claude did before this seam existed: ENV endpoint delivery +
    ``ANTHROPIC_AUTH_TOKEN`` token var, B3 host-dir adopt, no provider pin, no model
    gate (byte-identical fallback).
    """

    token_var: str = ""
    endpoint_delivery: str = "env"   # "env" | "config_file"
    wire_api: str = "responses"      # config-file harness wire; codex dropped "chat" (openai/codex#7782)
    host_dir_adopt: bool = True      # env-delivery B3 host-dir auto-adopt (claude only)
    provider_pin: tuple[tuple[str, str], ...] = ()  # setting pins when endpoint active
    model_required: bool = False     # error if endpoint set but no model (goose parity)


def http_probe_status(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout: float,
) -> int | None:
    """POST *body* as JSON to *url*; return the HTTP status, else ``None``.

    The shared transport for :meth:`Target.verify_persona` probes.  Returns the
    integer status for ANY HTTP response (an error status like 401 is a real
    ANSWER from the endpoint, not a transport failure) and ``None`` on any
    transport-level failure (DNS, refused, TLS, timeout, malformed URL, …) —
    the probe's "unreachable / can't-tell" shape.  NEVER raises, and NEVER logs
    the request (*headers* carry a bearer token).  Redirects are NOT followed:
    urllib would re-send EVERY header — the ``Authorization`` bearer included —
    to the redirect target, possibly cross-origin; a 3xx comes back as its
    status (→ unverifiable).  The response body is not read — the status alone
    answers "does this endpoint accept this token".
    """
    import json as _json
    import urllib.error
    import urllib.request

    class _NoRedirects(urllib.request.HTTPRedirectHandler):
        """Refuse every redirect (token hygiene — see the docstring)."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            return None

    data = _json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        for key, value in headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(_NoRedirects)
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        # URLError / socket.timeout / ConnectionError / ssl / ValueError (bad
        # URL) — all transport shapes; the probe contract is never-raises.
        return None


def probe_verdict(status: int | None) -> bool | None:
    """Map an HTTP probe *status* to the tri-state persona-verify verdict.

    * 2xx — the endpoint accepted the token and answered → ``True`` (PASS);
    * 401 / 403 — a POSITIVE auth reject → ``False`` (FAIL);
    * anything else (404 wrong path, 429 rate-limit — the token was accepted,
      5xx, or ``None`` transport failure) → ``None`` (UNVERIFIABLE / can't-tell:
      never punish a launch for an endpoint blip — DESIGN §5b).
    """
    if status is None:
        return None
    if 200 <= status < 300:
        return True
    if status in (401, 403):
        return False
    return None


class PersonaSettings(NamedTuple):
    """Persona-SPECIFIC values EXTRACTED from a rendered harness config.

    The persona-grata store (``$XDG_CONFIG_HOME/personas/<pid>/<hid>/``) lays
    down a harness-NATIVE config file (codex ``config.toml``, claude
    ``settings.json``); :meth:`Target.read_persona_settings` parses the one it
    understands into this harness-NEUTRAL triple, which the auto-import maps
    onto the agent keyspace (``self.endpoint`` / ``self.model`` /
    ``self.secret_path.<auth_env>``).  Distinct from :class:`PersonaSpec`,
    which declares HOW a harness DELIVERS these values into the box — this is
    the values themselves, read back OUT of a rendered config.
    """

    endpoint: str | None   # the alternate base URL (codex base_url / claude ANTHROPIC_BASE_URL)
    model: str | None      # the provider model id, when the config names one
    auth_env: str          # env var the bearer token is exported as (codex env_key /
                           # claude's fixed ANTHROPIC_AUTH_TOKEN)


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
    is_dir: bool = False              # True -> spec is a DIRECTORY (recursive copy, no mtime gate, no filter)


@dataclass(frozen=True)
class PluginDescriptor:
    """Declarative data a plugin exposes via Target.descriptor. Divergent LOGIC stays in Target hook methods.

    Maps onto the per-agent keyspace (``settings-keyspace-1.6.0-target.md`` §2d),
    keyed by ``@meta.agent.<agent>.name`` (the plugin's ``name`` property).  A few
    §2d keys are *informational* — they describe where core derives a path, not a
    descriptor field: ``agent.<agent>.path`` (``@config.agents/<name>``, derived in
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
    persona: "PersonaSpec | None" = None   # harness-specific persona endpoint/token
                                           # delivery (None = claude-style env +
                                           # ANTHROPIC_AUTH_TOKEN fallback).
    container_env: dict[str, str] = field(default_factory=dict)
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False           # True -> core calls Target.prepare_host before mounts
    init_dirs: tuple[str, ...] = ()   # extra dirs to mkdir in the project home (home-relative), e.g. (".claude",)
    auth_share_support: bool = False  # RO CAPABILITY (spec §2d): does this agent SUPPORT shared credentials?
                                      # Materialized as meta.agent.<agent>.auth.share_support (plugin-set, not overridable).
    vscode_extension: str | None = None  # VS Code Marketplace extension id auto-installed into the box on
                                         # attach (`kanibako code`), e.g. "anthropic.claude-code"; None = no
                                         # editor extension (the agent ships no VS Code integration).


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

    def default_common(self) -> dict[str, BindDefault]:
        """Declare default AGENT-scope common/caches binds for this agent.

        Returns a mapping of full DISCRIMINATED scoped category keys
        (``agent.<agent>.common.<name>`` / ``agent.<agent>.caches.<name>``) to STRUCTURED bind
        pairs ``(host_src, box_dest[, options])`` (spec §2a — a tuple, NOT a
        colon-joined string). These are injected as the AGENT level's *declared
        defaults* (``default_categories``) in the category resolver — a user can
        override or suppress (terminal "") any of them at a more-specific level.
        The default returns {} (no declared entries).
        """
        return {}

    def default_seeds(self) -> dict[str, BindDefault]:
        """Declare default copy-once-at-init seeds for this agent.

        Returns a mapping of full DISCRIMINATED seed keys
        (``agent.<agent>.seeded.<name>``) to
        STRUCTURED bind pairs ``(host_src, box_dest[, options])`` (spec §2a — a
        tuple, NOT a colon-joined string), injected as the AGENT level's declared
        defaults (``default_categories``) in the category resolver. A user can
        override or suppress (terminal "" or the "empty" sentinel) any of them at
        a more-specific level. The default returns {} (no seeds). No target ships
        a default seed yet.
        """
        return {}

    def default_category_binds(self) -> dict[str, BindDefault]:
        """Declare default AGENT-scope ``@``-ref-sourced category binds.

        Returns a mapping of DISCRIMINATED scoped category keys
        (``agent.<agent>.bindings.ro.<name>``) to
        STRUCTURED bind tuples ``(meta_ref, box_dest[, "ro"])`` (spec §2a) whose
        HOST SOURCE is an ``@``-ref STRING resolved by the launch category cascade —
        the AGENT-scope mirror of :mod:`kanibako.core_defaults`'s ``meta_ref`` bind
        shape.  These are injected as the AGENT level's declared defaults
        (``default_categories``) alongside :meth:`default_common`; a user can
        override or suppress (terminal "") any of them at a more-specific level.

        A plugin owns its own harness-slot ``box_dest`` while an ``@``-ref source
        keeps core agent-agnostic.  (The former per-agent instructions bind was
        retired — the box guide now ships via the RO ``~/playbook/kanibako`` bundle
        + launch-flatten — so no first-party plugin declares one today.)  The
        default returns ``{}`` (no category binds).
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

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether this agent has a session to resume under *home*.

        *home* is the box home directory as seen from the HOST (the home bind
        source — the same seam :meth:`credential_check_path` and the credsync
        hooks receive as ``proj.shell_path``).  ``start.py`` consults this at
        the continue-vs-new seam: when the DEFAULT continue mode was selected
        but the target positively reports nothing to resume, it builds the
        new-session command directly instead of ATTEMPTING a doomed resume
        (whose fast-dying container races the attach path into a raw runtime
        error).  The launch-time crash-and-retry net was REMOVED (the dead-pane
        dependency it relied on is gone), so this hook is now the SOLE
        continue-vs-fresh guard: a target with a doomable resume MUST override
        it to positively detect an empty store.

        Implementations read HOST-side state only — no container exec.  Return
        ``False`` only on a positive determination that no resumable session
        exists (a wrong ``False`` silently drops a real conversation).  Default
        ``True``: an agent that does not override keeps the always-attempt-
        continue behavior byte-identical.
        """
        return True

    def should_run_setup(self, output: str) -> bool:
        """Check if a launched session's output proves the config did NOT take.

        The LAUNCH is ground truth for a bootable config: a clean ``check_auth``
        probe (host-side) and even a clean in-box setup exit code cannot
        guarantee the agent will actually start (partial-config case).  After the
        in-box setup has run and the real session has launched, ``start.py``
        consults this matcher against the captured session logs.  A match means
        the agent reported it is still not configured/authenticated (e.g. goose's
        "Goose is not configured. Run 'goose configure' to set up."); ``start.py``
        then surfaces a clear error and returns.

        BOUNDED: setup already ran ONCE this invocation, so a post-launch match
        only ERRORS — it never loops back into setup.  Default ``False`` (claude
        has no setup step, so it never re-triggers this path).
        """
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

    def deliver_panel_permissions(
        self, *, config_root: Path, auto_approve: bool,
    ) -> bool:
        """Persist the box's resolved ``auto_approve`` onto this agent's
        PANEL-visible config surface under *config_root*.

        *config_root* is the box home as seen from the HOST (``proj.shell_path``)
        — the launch call site passes it unconditionally for EVERY agent, and
        each implementation appends its own config surface beneath it (claude
        ``.claude/settings.json``, goose ``.config/goose/config.yaml``, codex
        ``.codex/config.toml``).  The VS Code panel spawns its OWN in-box agent
        WITHOUT kanibako's launch env, so the box's configured yolo must be
        PERSISTED onto the agent's native config surface to reach it; this hook
        is that delivery, keyed on the PERSISTED ``auto_approve`` (never the
        per-launch ``-A``/``-S`` flags).

        Best-effort contract: the caller wraps the call, so a failure never
        blocks the launch — but implementations should still be merge-preserving
        and idempotent.  Returns whether a write occurred.  Default: no-op
        (``False``) — an agent with no panel-permission surface simply inherits
        it.
        """
        return False

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        auto_approve: bool,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed this agent's instruction-delivery SessionStart hook (plus any
        coupled managed config) onto its NATIVE config surface under
        *config_root*.

        *config_root* is the box home as seen from the HOST (``proj.shell_path``);
        see :meth:`deliver_panel_permissions`.  Box-side literals an
        implementation needs (e.g. codex's in-box config path and cwd for its
        trust keys) are derived by the PLUGIN from the core
        :data:`~kanibako.settings_resolve.GUEST_HOME` constant — deliberately
        NOT seam parameters while they are constants with a single consumer; if
        the in-box workdir ever becomes key-configurable, promote an
        agent-agnostic ``box_workdir`` parameter here instead of letting plugins
        drift.

        *model_provider* is the launch's resolved persona model-provider bundle
        (``None`` for bare / non-persona launches — the write must then be
        byte-identical to a provider-less one).  Today only codex consumes it;
        the type generalizes when the emitter bodies move into the plugins
        (findings T2.3 / persona phase 2).

        Best-effort contract as :meth:`deliver_panel_permissions`.  Returns
        whether a write occurred.  Default: no-op (``False``) — an agent with no
        directive-hook surface (goose, no-agent shell) inherits it.
        """
        return False

    def reattach_config_notice(self) -> str | None:
        """A heads-up to print when REATTACHING to an ALREADY-RUNNING box.

        The launch-time delivery seams (:meth:`deliver_panel_permissions` /
        :meth:`deliver_directive_hook`) re-materialise this agent's native config
        surface only on (re)start of a STOPPED box; a reattach to a live box
        early-returns and does NOT re-deliver, and it is unsafe to rewrite config
        under an app-server that already read it.  So an agent whose config is a
        RECONCILED PROJECTION (D1: codex's ``config.toml`` model/provider/approval)
        returns a one-line notice that config changes won't take effect until the
        box is restarted; core prints it on the reattach path (never rewriting the
        live file, reconciling on next start).  Default ``None`` — no notice (an
        agent with no launch-projected config surface inherits it).
        """
        return None

    def read_persona_settings(self, config_dir: Path) -> PersonaSettings | None:
        """Extract persona values from a rendered harness config in *config_dir*.

        *config_dir* is a persona-grata store entry's harness dir
        (``$XDG_CONFIG_HOME/personas/<pid>/<hid>/``) holding this harness's
        NATIVE config file.  A plugin that models the store's rendering parses
        it into a :class:`PersonaSettings`; the auto-import maps that onto the
        agent keyspace.  FAIL-SOFT contract: absent / unreadable / malformed /
        missing-required-keys config → ``None`` (never raises) — the caller
        warns and falls through.  Default: ``None`` (harness has no persona
        reader yet — goose/no_agent; add per-harness later or stay a no-op).
        Pure read; never writes, never reads the token file.
        """
        return None

    def verify_persona(
        self,
        endpoint: str,
        token_path: Path,
        model: str | None,
        *,
        timeout: float = 5.0,
    ) -> bool | None:
        """Probe *endpoint* with the token at *token_path* — a minimal real ack.

        The persona verified-swap probe (DESIGN §2b/§3b): a FEW-token genuine
        completion round-trip against the persona's endpoint, harness-API
        specific (anthropic messages vs OpenAI responses wire).  TRI-STATE:

        * ``True``  — PASS: the endpoint accepted the token and responded;
        * ``False`` — FAIL: a POSITIVE auth reject (401/403) — the caller keeps
          the last-known-good values;
        * ``None``  — UNVERIFIABLE: no probe implemented for this harness, the
          endpoint is unreachable, the token/model is unavailable, or the
          answer is ambiguous.  NOT pass/fail — the caller applies the DESIGN
          §5b rules (keep last-known-good; first-ever → candidate unverified).

        Contract: NEVER raises; SHORT *timeout* (a launch must not hang on a
        blip); the token value is read TRANSIENTLY for the request only —
        never logged, never persisted, never returned.  Default: ``None``
        (base has no wire knowledge — goose/no_agent add theirs later).
        """
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
