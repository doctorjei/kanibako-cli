"""The Target plugin contract: the ABC, its descriptor types, and the bind-shaped key map."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from kanibako.settings.settings_keyspace import ACCESS_TIERS

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig
    from kanibako.vscode.vscode_config import CodexModelProvider

# The map stored at ANY terminal bind-shaped key: `{box_dest: (host_src[, options])}`.
# ⚑ `BindDefault`, the NAME-keyed tuple, IS GONE — a plugin emitting one is refused BY NAME.
BindArm = dict[str, tuple[str, ...]]

# What `Target.default_category_binds` / `default_common` / `default_seeds` return.
CategoryBindDefaults = dict[str, BindArm]


@dataclass(frozen=True)
class TargetSetting:
    """A runtime setting a target advertises through `setting_descriptors`."""

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
    # Host entrypoint the plugin owns, bound in as-is; anchored, never resolved via $PATH
    launcher: Path | None = None


class BindKind(Enum):
    """Whether a binding mounts a single file or a directory."""

    FILE = "file"
    DIR = "dir"


class HostSrcOrigin(Enum):
    """Where a binding's default host source comes from, before any cascade override."""

    LAUNCHER = "launcher"        # AgentInstall.launcher (detection-derived)
    INSTALL_DIR = "install_dir"  # AgentInstall.install_dir
    BINARY = "binary"            # AgentInstall.binary
    LITERAL = "literal"          # a fixed Path in the descriptor (literal_src)


class BindScope(Enum):
    """How widely a binding applies + its failure semantics."""

    AGENT_CRITICAL = "agent_critical"  # delivery-essential; missing source safe-fails; ro
    AGENT = "agent"                    # agent-level share (plugins); overridable; may be rw


@dataclass(frozen=True)
class Binding:
    """One bound element: a delivery binary/launcher/share, or an agent share."""

    # ⚑ `key` is a SETTINGS-FILE-only override key; there is no CLI route, so document none.
    key: str                          # override key -> agent.<name>.bindings.{ro,rw}.<key>
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
    """A value-bearing agent setting routed to an argv flag or an env var (model, provider)."""

    setting_key: str                  # agent setting supplying the value ("model", "provider")
    channel: Channel
    flag: tuple[str, ...] = ()        # FLAG form, e.g. ("--model",)
    env_var: str = ""                 # ENV form, e.g. "GOOSE_MODEL"


@dataclass(frozen=True)
class AccessTierRow:
    """How one `access` tier is realized for one harness (spec §2d)."""

    # ⚑ An EMPTY row means emit nothing, DELIBERATELY; a MISSING row means cannot render.
    flag: tuple[str, ...] = ()   # FLAG channel emission ( () = emit nothing )
    env_value: str = ""          # ENV channel emission ( "" = emit nothing )


@dataclass(frozen=True)
class AccessRealization:
    """The per-harness realization of the `access` permission tier (spec §2d)."""

    channel: Channel
    env_var: str = ""            # ENV form's variable name (e.g. goose GOOSE_MODE)
    restricted: "AccessTierRow | None" = None   # None = unrenderable by this harness
    editing: "AccessTierRow | None" = None      # None = unrenderable by this harness
    full: "AccessTierRow | None" = None         # None = unrenderable by this harness
    setting_key: str = ""

    def row(self, tier: str) -> "AccessTierRow | None":
        """The row for *tier*, or `None` when unrenderable — an unknown tier collapses here too."""
        if tier == "restricted":
            return self.restricted
        if tier == "editing":
            return self.editing
        if tier == "full":
            return self.full
        return None

    def renders(self, tier: str) -> bool:
        """Can this harness render *tier* at all? (presence of a row)"""
        return self.row(tier) is not None

    def rendered_tiers(self) -> tuple[str, ...]:
        """The tiers this harness can render, least->most permissive (order from `ACCESS_TIERS`)."""
        return tuple(t for t in ACCESS_TIERS if self.renders(t))


@dataclass(frozen=True)
class PersonaSpec:
    """How a persona's alternate endpoint and bearer token are delivered for this harness."""

    # ⚑ That key's VALUE is THREE-STATE: configured / ABSENT (refuse) / present-null (KEYLESS).
    token_var: str = ""              # secret_path key == in-box env var; EMPTY = harness-dynamic
    endpoint_delivery: str = "env"   # "env" | "config_file"; picks the preflight gate in start.py
    wire_api: str = "responses"      # config-file wire; codex dropped "chat" (openai/codex#7782)
    provider_pin: tuple[tuple[str, str], ...] = ()  # setting pins when endpoint active
    # ⚑ A HARNESS-CAPABILITY veto, NARROWED by the present-null model key: env delivery can
    # omit a model, so a present-null SUPPRESSES this; a config-file harness conflict-refuses.
    model_required: bool = False     # harness VETO: error if endpoint set but no model


#: Cap on the provider's own words as echoed back to a user, in CHARACTERS.
_PROVIDER_TEXT_CAP = 300
#: Bytes taken off an error response before capping — a hostile body is never slurped.
_PROVIDER_READ_CAP = 8 * 1024


class ProbeResponse(NamedTuple):
    """What `http_probe` saw: an HTTP status (`None` = never reached) and the error text."""

    status: int | None
    # ⚑ Already normalized to ONE line, capped, and SCRUBBED of the bearer — see
    # `_provider_text`.  Safe to print verbatim; empty when there was nothing to read.
    body: str = ""


def _bearer_secrets(headers: Mapping[str, str]) -> tuple[str, ...]:
    """Every substring of *headers* that must never survive into printed text."""
    out: list[str] = []
    for name, value in headers.items():
        if name.lower() != "authorization" or not value:
            continue
        out.append(value)
        _, _, bearer = value.partition(" ")
        if bearer:
            out.append(bearer)
    return tuple(out)


def _provider_text(raw: bytes, headers: Mapping[str, str]) -> str:
    """Normalize an error body into ONE capped, token-scrubbed line.

    ⚑ THE SCRUB IS THIS FUNCTION'S JOB, NOT A CALLER'S, and that is why it lives beside
    the request: the bearer is in *headers* HERE and nowhere downstream, so a provider
    that echoes a credential back cannot reach any message a caller prints.  Scrubbing
    precedes the cap deliberately — truncating first could leave half a token standing.
    """
    text = raw.decode("utf-8", "replace")
    for secret in _bearer_secrets(headers):
        text = text.replace(secret, "<redacted>")
    text = " ".join(text.split())
    if len(text) > _PROVIDER_TEXT_CAP:
        text = text[:_PROVIDER_TEXT_CAP].rstrip() + "…"
    return text


def http_probe(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout: float,
) -> ProbeResponse:
    """POST *body* as JSON to *url*; return the HTTP status and the provider's error text.

    ⚑ NEVER raises, and never logs the request: *headers* carry a bearer token.
    ⚑ The returned text is scrubbed of that bearer (`_provider_text`), so the never-log
    property survives a provider that echoes the credential back in its own error.
    """
    import json as _json
    import urllib.error
    import urllib.request

    class _NoRedirects(urllib.request.HTTPRedirectHandler):
        """Refuse every redirect: urllib would re-send the `Authorization` bearer cross-origin."""

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
            return ProbeResponse(int(resp.status))
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(_PROVIDER_READ_CAP)
        except Exception:
            raw = b""
        return ProbeResponse(int(exc.code), _provider_text(raw, headers))
    except Exception:
        # Every transport shape (URLError / timeout / ssl / bad URL); the contract is never-raises.
        return ProbeResponse(None)


class PersonaSettings(NamedTuple):
    """Persona-specific values read back OUT of a rendered harness config."""

    endpoint: str | None   # the alternate base URL (codex base_url / claude ANTHROPIC_BASE_URL)
    model: str | None      # the provider model id, when the config names one
    auth_env: str          # env var the bearer token is exported as (codex env_key)
    # ⚑ MappingProxyType, not a bare `{}`: a mutable default is one object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})   # passthrough env block (no single-source vars)
    env_dropped: tuple[str, ...] = ()               # NAMES skipped: value was not a string


class PersonaReadOutcome(NamedTuple):
    """The tri-state result of `Target.read_persona_settings`."""

    settings: PersonaSettings | None   # non-None = a usable config was read
    reject_reason: str | None          # non-None = PRESENT but UNUSABLE; BOTH None = no reader


class PersonaProbeVerdict(Enum):
    """The four distinct answers `Target.verify_persona` can give."""

    PASS = "pass"                        # 2xx — the endpoint accepted the token
    REJECTED = "rejected"                # 401/403 — a POSITIVE auth reject
    INCONCLUSIVE = "inconclusive"        # probed, could not decide (blip/ambiguous)
    NOT_APPLICABLE = "not_applicable"    # no probe was attempted, and none will be


# Statuses at which the endpoint POSITIVELY refused the request it was handed.
# ⚑ NOT "the token is bad": neither code says WHICH input was at fault, and a 403 on a
# model the caller's team is not entitled to reads identically to a dead credential.
_REFUSAL_STATUSES = (401, 403)


def _tilde(path: Path) -> str:
    """*path* with the user's home abbreviated to `~` — DISPLAY only, never a resolve."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except (ValueError, RuntimeError, OSError):
        return str(path)


@dataclass(frozen=True)
class ProbeEvidence:
    """What a persona probe SENT and what came back — the facts a non-PASS verdict rests on.

    Filled in TWO halves: a plugin builds the request half before the call, and
    `probe_outcome` fills *status* / *provider_text* from the answer.  ⚑ It carries no
    secret by construction — *token_path* is a PATH, and *provider_text* was scrubbed of
    the bearer by `http_probe` before it could ever get here.

    ⚑ *model* is what went ON THE WIRE, which is not always what the user configured: a
    harness that resolves a tier alias through an env var records the id it SENT here and
    says where it came from in *model_origin*.
    """

    endpoint: str
    model: str | None = None        # None = the `model` key was deliberately OMITTED
    model_origin: str = ""          # "" = sent exactly as configured
    token_path: Path | None = None  # None = a KEYLESS probe (no `Authorization` header)
    status: int | None = None       # None = the endpoint was never reached
    provider_text: str = ""         # the provider's own words, capped and scrubbed

    def lines(self, indent: str = "  ") -> tuple[str, ...]:
        """The evidence block: one labeled line per input, then the provider's own words.

        ⚑ The closing sentence is emitted for a REFUSAL status ONLY, and it exists to stop
        the one wrong conclusion this block is here to prevent — that a refusal names the
        token.  For any other status the caller's own reason sentence already says what
        was seen, and a second interpretation would be guessing.
        """
        model = "(omitted)" if not self.model else self.model
        if self.model and self.model_origin:
            model = f"{self.model}  ({self.model_origin})"
        token = (
            "(none — this persona declares the endpoint keyless)"
            if self.token_path is None else _tilde(self.token_path)
        )
        out = [
            f"{indent}{'endpoint':<10}{self.endpoint}",
            f"{indent}{'model':<10}{model}",
            f"{indent}{'token':<10}{token}",
        ]
        if self.provider_text:
            out.append(f"{indent}{'provider:':<10}{self.provider_text}")
        if self.status in _REFUSAL_STATUSES:
            out.append(
                f"{indent}An HTTP {self.status} means the endpoint refused this request — "
                f"it does not say which input was at fault."
            )
        return tuple(out)

    def block(self, indent: str = "  ") -> str:
        """`lines`, joined — the form a printed message interpolates."""
        return "\n".join(self.lines(indent))


class PersonaProbeOutcome(NamedTuple):
    """The result of `Target.verify_persona`: a verdict, a named cause, and its evidence."""

    verdict: PersonaProbeVerdict
    reason: str | None = None   # set for the two non-answer arms; interpolates into a sentence
    # ⚑ REQUIRED for a REJECTED (`rejected` takes it positionally): a refusal a user cannot
    # see the inputs of is the message that sent them to fix a token that was never wrong.
    evidence: "ProbeEvidence | None" = None

    @classmethod
    def passed(cls) -> "PersonaProbeOutcome":
        """2xx: the endpoint accepted the request."""
        return cls(PersonaProbeVerdict.PASS)

    @classmethod
    def rejected(cls, evidence: ProbeEvidence) -> "PersonaProbeOutcome":
        """401/403: the endpoint positively refused the request *evidence* describes."""
        return cls(PersonaProbeVerdict.REJECTED, None, evidence)

    @classmethod
    def inconclusive(
        cls, reason: str, evidence: "ProbeEvidence | None" = None,
    ) -> "PersonaProbeOutcome":
        """The probe ran and could not decide; *reason* says what it saw.

        *evidence* is absent for the one arm that has none — a probe that never got as far
        as a request (a plugin that raised).
        """
        return cls(PersonaProbeVerdict.INCONCLUSIVE, reason, evidence)

    @classmethod
    def not_applicable(cls, reason: str) -> "PersonaProbeOutcome":
        """No probe was attempted; *reason* says why none is possible."""
        return cls(PersonaProbeVerdict.NOT_APPLICABLE, reason)

    def evidence_block(self, indent: str = "  ") -> str:
        """The evidence lines to append to a printed message, newline-led; `""` when none.

        ⚑ Empty unless the endpoint ANSWERED.  A probe that never reached it has no facts
        to lay out, and four lines restating the caller's own inputs is noise, not
        evidence.  THE single renderer for both the launch error and the create warning —
        one shape, so the two can never drift.
        """
        if self.evidence is None or self.evidence.status is None:
            return ""
        return "\n" + self.evidence.block(indent)


def probe_outcome(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome:
    """Map an HTTP probe *response* onto a `PersonaProbeOutcome`; `NOT_APPLICABLE` never comes here.

    *sent* is the request half of the evidence, built by the plugin that made the call;
    the answer half is filled in here so no plugin can forget to.
    """
    evidence = replace(
        sent, status=response.status, provider_text=response.body,
    )
    if response.status is None:
        return PersonaProbeOutcome.inconclusive(
            "the endpoint could not be reached", evidence
        )
    if 200 <= response.status < 300:
        return PersonaProbeOutcome.passed()
    if response.status in _REFUSAL_STATUSES:
        return PersonaProbeOutcome.rejected(evidence)
    return PersonaProbeOutcome.inconclusive(
        f"the endpoint answered HTTP {response.status}, which is neither an accept "
        f"nor an auth reject",
        evidence,
    )


# Statuses meaning "well-formed EXCEPT for a missing `model`"; consulted ONLY by the no-model probe.
# ⚑ An INFERENCE, deliberately narrow — the wire never says "you omitted the model".
_MODEL_REQUIRED_STATUSES = (400, 422)


def probe_outcome_no_model(
    response: ProbeResponse, sent: ProbeEvidence,
) -> PersonaProbeOutcome:
    """`probe_outcome`, for a probe that deliberately OMITTED `model`."""
    if response.status in _MODEL_REQUIRED_STATUSES:
        return PersonaProbeOutcome.not_applicable(
            f"the endpoint requires a model in the request (HTTP {response.status}) and "
            f"the persona names none"
        )
    return probe_outcome(response, sent)


@dataclass(frozen=True)
class Operation:
    """A standalone op fragment (exec/headless), no session mode; spliced after `command`."""

    fragment: tuple[str, ...]


class Cadence(Enum):
    """Credential/config file sync cadence."""

    SYNC = "sync"            # bidirectional, mtime-gated each launch (credentials/token files)
    SEED_ONCE = "seed_once"  # one-way host->project at init, never written back (config files)


@dataclass(frozen=True)
class CredFileSpec:
    """A credential/config file's lifecycle; the filter/merge payload stays a plugin hook."""

    home_rel: str                     # path under the project shell home (".claude/settings.json")
    host_rel: str                     # path under the host home (".claude/settings.json")
    cadence: Cadence = Cadence.SYNC
    mtime_gate: bool = True           # only meaningful for SYNC
    filtered: bool = False            # True -> plugin transform_cred hook runs
    is_dir: bool = False              # DIRECTORY: recursive copy, no mtime gate, no filter


@dataclass(frozen=True)
class PluginDescriptor:
    """Declarative data a plugin exposes via `Target.descriptor`; divergent LOGIC stays in hooks."""

    command: tuple[str, ...]                       # box argv prefix (e.g. ("claude",))
    bindings: tuple[Binding, ...]                  # ALL bound elements; ordered; >=1
    mode: dict[str, tuple[str, ...]]               # INTERACTIVE launch ONLY: {"start", "continue"}
    operations: dict[str, Operation] = field(default_factory=dict)  # standalone ops, no mode
    access_realization: AccessRealization | None = None
    settings: tuple[SettingArg, ...] = ()
    persona: "PersonaSpec | None" = None   # harness-specific persona endpoint/token delivery
                                           # (None = claude-style env + ANTHROPIC_AUTH_TOKEN)
    # ⚑ NO `container_env` FIELD — plugin env is `agent.<agent>.env.<VAR>`, and
    # `agent_defaults.load_descriptor` refuses `container_env:` BY NAME.
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False           # True -> core calls Target.prepare_host before mounts
    init_dirs: tuple[str, ...] = ()   # extra dirs to mkdir in the project home (home-relative)
    auth_share_support: bool = False  # RO CAPABILITY (spec §2d): supports shared credentials?
    vscode_extension: str | None = None  # extension id auto-installed on attach; None = none


def _validate_agent_binary(binary: Path) -> str | None:
    """Return a REASON string when *binary* is an unusable host executable, else `None`.

    ⚑ Deliberately LENIENT: ELF magic is NOT required (shebang wrappers are legitimate).
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

    # Lenient corruption check: leading bytes all NUL is neither a binary nor a shebang wrapper.
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
        if head and set(head) == {0}:
            return f"binary appears corrupt (leading bytes are all NUL) at {path}"
    except OSError:
        pass

    return None


class Target(ABC):
    """Abstract base class for agent targets — ALL agent knowledge lives in implementations.

    ⚑ The legacy per-METHOD launch hooks (`binary_mounts`, `init_home`, `build_cli_args`,
    `resource_mappings`, `apply_state`) are GONE — the launch is assembled from `descriptor`.
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
        """Detect the agent installation on the host; `None` if it is not installed."""
        ...

    @property
    def has_binary(self) -> bool:
        """Whether this target requires a host-installed binary."""
        return True

    @property
    def descriptor(self) -> "PluginDescriptor | None":
        """Declarative plugin descriptor; None ONLY for the built-in no-agent shell."""
        return None

    def check_auth(self) -> bool:
        """Check if the agent is authenticated. Returns True if ok."""
        return True

    def prepare_host(self, install: "AgentInstall", *, auto_auth: bool, data_path: Path) -> None:
        """Plugin-owned pre-launch host preparation, BEFORE mounts are built. Default: no-op.

        ⚑ Implementations MUST NOT crash the launch — log and swallow.
        """
        return None

    def default_common(self) -> dict[str, BindArm]:
        """Declare default AGENT-scope common/caches binds: TERMINAL key -> its whole `BindArm`."""
        return {}

    def default_seeds(self) -> dict[str, BindArm]:
        """Declare default copy-once-at-init seeds: `agent.<agent>.seeded` -> its whole `BindArm`.

        ⚑ ONE DEST SPACE, TWO DELIVERIES (spec §0): a `seeded` entry STAYS A COPY.
        """
        return {}

    def default_envs(self) -> dict[str, str]:
        """Declare default AGENT-scope env VARIABLES: `agent.<agent>.env.<VAR>` -> scalar value.

        ⚑ The ONLY route a plugin has for a STATIC variable; the write-once arbitration
        belongs to `store_collapse.collapse_env`, and there is none here.
        """
        return {}

    def rom_root(self) -> Path | None:
        """Locate this plugin's packaged BIBLE CHAPTER root (`<pkg>/data/rom`); `None` on ANY failure.

        ⚑ Derived from `__package__`, NOT from `name`: `name` is the HARNESS name and only
        happens to match `kanibako.plugins.<name>` for the first-party plugins.
        """
        import importlib.resources
        import sys

        try:
            module = sys.modules[type(self).__module__]
            package = getattr(module, "__package__", None)
            if not package:
                return None
            ref = importlib.resources.files(package).joinpath("data", "rom")
            path = Path(str(ref))
        except Exception:
            return None
        return path if path.is_dir() else None

    def default_category_binds(self) -> CategoryBindDefaults:
        """Declare default AGENT-scope `@`-ref-sourced category binds: TERMINAL key -> `BindArm`.

        ⚑ Every destination must be normalized with `settings_resolve.normalize_bind_dest` —
        the launch floor merge DEDUPES on these keys BEFORE anything parses them.
        ⚑ ONE SHAPE, TWO DELIVERIES: `seeded` and `synced` are COPIES and stay copies.
        ⚑ A retired name-keyed `agent.<agent>.<category>.<name>` key is REFUSED BY NAME at
        `settings_assemble._insert_dotted`; there is no shim.
        ⚑ A plugin's own bible chapter is NOT declared here — see `rom_root`.
        """
        return {}

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare what runtime settings this target supports, as `TargetSetting` entries."""
        return []

    def generate_agent_config(self) -> AgentConfig:
        """Return a default AgentConfig for this target."""
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    @property
    def default_entrypoint(self) -> str | None:
        """Binary name for container entrypoint. None = use bash."""
        return None

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether this agent has a session to resume under HOST-side *home*.

        ⚑ The SOLE continue-vs-fresh guard: return `False` ONLY on a POSITIVE determination
        that no resumable session exists — a wrong `False` silently drops a conversation.
        """
        return True

    def should_run_setup(self, output: str) -> bool:
        """Check if a launched session's output PROVES the config did NOT take.

        ⚑ BOUNDED: setup already ran ONCE this invocation, so a match only ERRORS — it
        never loops back into setup.
        """
        return False

    @property
    def setup_entrypoint(self) -> str | None:
        """Container entrypoint for the one-time interactive setup; `None` = no setup step."""
        return None

    @property
    def setup_args(self) -> list[str]:
        """Arguments for the one-time setup command (see `setup_entrypoint`)."""
        return []

    @property
    def config_dir_name(self) -> str:
        """Agent config dir relative to home (e.g. '.claude'). Default: '.{name}'."""
        return f".{self.name}"

    def credential_check_path(self, home: Path) -> Path | None:
        """Path to check for credential existence, or None."""
        return None

    def deliver_panel_permissions(
        self, *, config_root: Path, access: str,
    ) -> bool:
        """Persist the box's resolved `access` TIER onto this agent's panel-visible config.

        ⚑ Keyed on the CASCADE-resolved `access`, NEVER the per-launch `-S`/`-A` flags
        (spec §1A's projected-surface exception — the projection OUTLIVES the launch).
        ⚑ An implementation MUST render every tier explicitly and MUST NOT fall through to
        the permissive arm; an unrecognised value here is a BUG and should raise.
        Best-effort: the caller wraps the call. Returns whether a write occurred.
        """
        return False

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed this agent's SessionStart directive hook + any coupled managed config.

        ⚑ Box-side literals are derived by the PLUGIN from `settings_resolve.GUEST_HOME`,
        deliberately NOT seam parameters; if the in-box workdir ever becomes
        key-configurable, promote an agent-agnostic `box_workdir` parameter HERE.
        Best-effort as `deliver_panel_permissions`. Returns whether a write occurred.
        """
        return False

    def reattach_config_notice(self) -> str | None:
        """A heads-up to print when REATTACHING to an ALREADY-RUNNING box; `None` = no notice.

        ⚑ A reattach does NOT re-deliver config: it is unsafe to rewrite config under an
        app-server that already read it.
        """
        return None

    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome:
        """Extract persona values from a rendered harness config in *config_dir*.

        ⚑ FAIL-SOFT: NEVER raises; an unusable config returns a SPECIFIC reject reason,
        not a bare `None`. Pure read — never writes, never reads the token file.
        """
        return PersonaReadOutcome(settings=None, reject_reason=None)

    def verify_persona(
        self,
        endpoint: str,
        token_path: Path | None,
        model: str | None,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 5.0,
    ) -> PersonaProbeOutcome:
        """Probe *endpoint*, bearer-authed with the token at *token_path* — a minimal real ack.

        ⚑ *token_path* MAY be `None` (a KEYLESS persona): probe it with the `Authorization`
        header OMITTED rather than decline, and NEVER substitute a placeholder credential.
        ⚑ *model* MAY be `None`: probe with the field OMITTED, never a placeholder id.
        ⚑ *env* is the PERSONA-STORE passthrough env block — that store entry's own `env:`
        map, less the two single-source vars — and NOTHING ELSE.  🛑 IT IS NOT THE COLLAPSED
        `env` FAMILY the box receives: those slots are folded at `meta.assembly.env` by the
        WHOLE-BOX resolve, which runs AFTER this pre-flight, so a mapping the user set at
        the agent-file (`kanibako agent set <a> env.<VAR>=…`), workset or box rung — or on a
        keyspace-only persona, which has no store block at all — is ABSENT here.  A harness
        whose runtime resolves *model* through one of them (claude reads a tier alias through
        `ANTHROPIC_DEFAULT_<TIER>_MODEL`) MUST resolve it here too, or the probe asks the
        provider a question the box never asks and a valid persona is refused on the answer.
        ⚑ Where the mapping lives OUTSIDE this block the alias still goes out raw and that
        false `REJECTED` survives: a KNOWN UNCLOSED HOLE, not a contract this argument keeps.
        Resolving a mapping the USER wrote is not the substitution the rule above forbids;
        inventing one is.
        ⚑ Contract: NEVER raises; SHORT *timeout*; the token is read TRANSIENTLY for the
        request only — never logged, never persisted, never returned.
        """
        return PersonaProbeOutcome.not_applicable(
            f"the {self.name} harness implements no persona verify probe"
        )

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

        ⚑ Called ONLY for specs with `filtered=True`; *direction* is `"in"` (host->project)
        or `"out"`, and *src* is `None` when no source file is available.
        """
        import shutil
        if src is not None and Path(src).is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

    def refresh_credentials(self, home: Path) -> None:
        """Refresh agent credentials from host into the project home. Default: no-op."""
        return None

    def writeback_credentials(self, home: Path) -> None:
        """Write back credentials from project home to host. Default: no-op."""
        return None

    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None:
        """Plugin-specific post-session writeback BEYOND `cred_files` specs. Default: no-op.

        ⚑ MUST be defensive — never raise on a malformed or absent file; a clean teardown
        is the contract.
        """
        return None
