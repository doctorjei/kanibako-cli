"""Target base classes: ABC for agent targets, Mount and AgentInstall dataclasses."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from kanibako.settings.settings_keyspace import ACCESS_TIERS

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig
    from kanibako.vscode.vscode_config import CodexModelProvider

# The map stored at ANY terminal bind-shaped key — `<scope>.bindings.{ro,rw}` and,
# since 2026-08-08c, `<scope>.{caches,seeded,common,synced}`. Key is the box
# destination, normalized by `normalize_bind_dest`; value is `(host_src[, options])`.
# Not `core_defaults.BindArmTable`, which maps the terminal key -> the map.
#
# ⚑ `BindDefault` (the NAME-keyed `(host_src, box_dest[, options])` tuple) IS GONE.
# It was the value of a `<scope>.<category>.<name>` key, and no such key exists in
# any bind-shaped category any more: the destination is the identity, so it cannot
# also be a tuple element. A plugin still emitting one produces a key the reader
# refuses BY NAME (`settings_assemble._insert_dotted`) rather than a silent
# mis-bind — the refusal is the migration, v1.8.0 being a clean break.
BindArm = dict[str, tuple[str, ...]]

# What `Target.default_category_binds` / `default_common` / `default_seeds` return:
# a TERMINAL category key mapped to its whole `BindArm`.
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
    # Host entrypoint the plugin owns and binds into the box as-is (e.g.
    # ~/.local/bin/claude), anchored to the agent's contract path, never resolved via $PATH.
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
    """One bound element: a delivery binary/launcher/share, or an agent share.

    The resolved host source is the cascade override `agent.<name>.bindings.{ro,rw}.<key>`
    if set, else the *origin* — a detection field (LAUNCHER/INSTALL_DIR/BINARY) or
    `literal_src` (LITERAL). AGENT_CRITICAL bindings safe-fail when the source is missing
    and get bind-as-is inode pinning plus core dest-symlink clearing; AGENT shares are
    best-effort, so a missing or suppressed share is fine.

    The override key is settings-file-only: there is no `config set` / `config reset`
    route to it at any scope, so do not document one. The key is still declared, still
    read by the launch cascade, and still readable via `config get`; a user or a plugin's
    docs repoint a binding by hand-editing `agents/<node>/settings.yaml`.
    """

    key: str                          # stable override key -> agent.<name>.bindings.{ro,rw}.<key>
                                      # (a settings-file key; no CLI route)
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
    """How one `access` tier is realized for one harness (spec §2d).

    The emission for whichever channel this harness's `AccessRealization` declares:
    *flag* is the argv fragment for FLAG (`("--permission-mode", "acceptEdits")`),
    *env_value* is the value for ENV (goose `GOOSE_MODE=approve`).

    An EMPTY row (both fields empty) means emit nothing, deliberately — the right
    realization for a tier a default-safe harness already runs at with no argument
    (claude/codex `restricted`). A MISSING row means the harness cannot render that tier
    and the launch refuses (see `AccessRealization.row`). Keeping the two distinct is why
    the rows are optional rather than defaulted: for a harness whose unset default is
    unsafe (goose's `GOOSE_MODE` defaults to `auto`), "emit nothing" and "cannot render"
    would otherwise both silently mean permissive.
    """

    flag: tuple[str, ...] = ()   # FLAG channel emission ( () = emit nothing )
    env_value: str = ""          # ENV channel emission ( "" = emit nothing )


@dataclass(frozen=True)
class AccessRealization:
    """The per-harness realization of the `access` permission tier (spec §2d).

    * *channel* — where every row of this harness is emitted: FLAG (argv, claude/codex)
      or ENV (*env_var*, goose `GOOSE_MODE`). One channel per harness: expressing one
      tier as a flag and another as an env var would be two mechanisms for one axis.
    * *restricted* / *editing* / *full* — the tier rows. `None` means this harness cannot
      render that tier; the launch then refuses, naming the tiers it can render, rather
      than substituting silently or falling through to the permissive arm. The fields are
      named for the tiers because the spec closes the tier set.
    * *setting_key* — the persisted key the launch reader redeems (all three shipped
      agents use `"access"`, default `full`); empty = per-launch `-S`/`-A` only.

    Unlike a plain `SettingArg`, the value comes from the resolved permission tier.
    """

    channel: Channel
    env_var: str = ""            # ENV form's variable name (e.g. goose GOOSE_MODE)
    restricted: "AccessTierRow | None" = None   # None = unrenderable by this harness
    editing: "AccessTierRow | None" = None      # None = unrenderable by this harness
    full: "AccessTierRow | None" = None         # None = unrenderable by this harness
    setting_key: str = ""

    def row(self, tier: str) -> "AccessTierRow | None":
        """The row for *tier*, or `None` when unrenderable.

        `None` covers both "this harness declared no row for that tier" (goose `editing`)
        and "that is not a tier at all". The caller refuses either way, which is the safe
        collapse: an unknown tier must never reach an emission.
        """
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
        """The tiers this harness can render, least->most permissive.

        Used by the launch refusal to name the legal alternatives for THIS agent rather
        than the abstract enum. Order comes from `ACCESS_TIERS`, the one declaration of
        the tier vocabulary; this class spells the tier names as fields, which is what
        makes a missing row a declaration rather than a lookup miss, but it does not get
        its own opinion about their order.
        """
        return tuple(t for t in ACCESS_TIERS if self.renders(t))


@dataclass(frozen=True)
class PersonaSpec:
    """How a persona's alternate endpoint and bearer token are delivered for this harness.

    A persona (`agent.<persona>℘<harness>`) points the harness at a third-party model
    endpoint with a bearer token. How those two values reach the box is harness-specific,
    so the plugin declares it here, and the persona preflight in `start.py` consults it.

    * *token_var* — the `secret_path` key (== the in-box env var) carrying the bearer
      token. Claude uses the fixed `ANTHROPIC_AUTH_TOKEN`. A config-file harness (codex)
      leaves this EMPTY, which is dynamic: the single configured `secret_path` key is the
      token var, and it doubles as the model-provider `env_key`.
    * *endpoint_delivery* — `"env"` (claude: the endpoint rides the descriptor's
      `endpoint`->`ANTHROPIC_BASE_URL` ENV `SettingArg`) or `"config_file"` (codex: the
      launch config generator writes it into `~/.codex/config.toml`'s
      `[model_providers.<id>]` block, not an env var). This is the only thing that picks
      between the two preflight gates in `start.py`.
    * *wire_api* — the config-file harness's model-provider wire protocol
      (`[model_providers.<id>].wire_api`); default `"responses"`, since Codex removed the
      `"chat"` wire (openai/codex#7782). Ignored for `"env"` delivery.
    * *provider_pin* — `(setting_key, value)` pairs force-applied to the launch's
      effective setting state whenever this persona resolves an active endpoint, so a
      harness whose endpoint requires a specific provider cannot be misconfigured. Goose
      pins `("provider", "openai")`, and the descriptor's `provider`->`GOOSE_PROVIDER`
      `SettingArg` then emits it in the box. Empty (claude/codex) = no pin, byte-identical;
      a bare box with no active endpoint is never touched.
    * *model_required* — whether a persona with a resolved endpoint but NO cascade-resolved
      model is a hard error. A missing model is not automatically invalid, since some
      endpoints need no model spec, so absence means "this persona needs none" unless the
      harness vetoes here. Claude keeps `False` (its model rides its own channels); goose
      and codex set `True` — a third-party OpenAI-compatible endpoint has no meaningful
      default, and a config-file harness cannot express "no model" at all.

    A target with no `PersonaSpec` (`descriptor.persona is None`) resolves through the
    fallback in `start.py`'s `_persona_wiring`, which spells out the claude shape
    explicitly: ENV endpoint delivery, `ANTHROPIC_AUTH_TOKEN` token var, no provider pin,
    no model gate. That explicit spelling is what keeps the fallback claude-shaped; the
    field defaults below are the declared-nothing defaults.
    """

    token_var: str = ""
    endpoint_delivery: str = "env"   # "env" | "config_file"
    wire_api: str = "responses"      # config-file wire; codex dropped "chat" (openai/codex#7782)
    provider_pin: tuple[tuple[str, str], ...] = ()  # setting pins when endpoint active
    model_required: bool = False     # harness VETO: error if endpoint set but no model


def http_probe_status(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout: float,
) -> int | None:
    """POST *body* as JSON to *url*; return the HTTP status, else `None`.

    The shared transport for `Target.verify_persona` probes. Any HTTP response yields its
    integer status — an error status like 401 is a real ANSWER from the endpoint, not a
    transport failure — and `None` means a transport-level failure (DNS, refused, TLS,
    timeout, malformed URL), the probe's unreachable/can't-tell shape.

    Never raises, and never logs the request: *headers* carry a bearer token. Redirects
    are NOT followed, because urllib would re-send every header, the `Authorization`
    bearer included, to the redirect target, possibly cross-origin; a 3xx comes back as
    its status instead. The response body is not read — the status alone answers "does
    this endpoint accept this token".
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
        # URLError / socket.timeout / ConnectionError / ssl / ValueError (bad URL) — all
        # transport shapes, and the contract is never-raises.
        return None


class PersonaSettings(NamedTuple):
    """Persona-specific values extracted from a rendered harness config.

    The persona-grata store (`$XDG_CONFIG_HOME/personas/<pid>/<hid>/`) lays down a
    harness-native config file (codex `config.toml`, claude `settings.json`);
    `Target.read_persona_settings` parses the one it understands into this
    harness-neutral record, which the auto-import maps onto the agent keyspace
    (`self.endpoint` / `self.model` / `self.secret_path.<auth_env>`) and whose `env` rides
    the launch as plain passthrough. `PersonaSpec` declares HOW a harness delivers these
    values into a box; this is the values themselves, read back OUT of a rendered config.

    `env` is the harness config's env block MINUS the base URL and the bearer token,
    which ride their own channels — `endpoint` here and the secret-path bind — and so must
    never be duplicated into the passthrough. A config entry whose value is not a string
    cannot be delivered as an env value (a JSON number/bool/null would be `str()`'d into a
    Python repr), so its NAME lands in `env_dropped` for a caller to warn about. Nothing
    is ever dropped silently.
    """

    endpoint: str | None   # the alternate base URL (codex base_url / claude ANTHROPIC_BASE_URL)
    model: str | None      # the provider model id, when the config names one
    auth_env: str          # env var the bearer token is exported as (codex env_key /
                           # claude's fixed ANTHROPIC_AUTH_TOKEN)
    # MappingProxyType, not a bare `{}`/`[]`: a mutable default on a NamedTuple field is
    # one object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})   # passthrough env block (no single-source vars)
    env_dropped: tuple[str, ...] = ()               # NAMES skipped: value was not a string


class PersonaReadOutcome(NamedTuple):
    """The tri-state result of `Target.read_persona_settings`.

    * *settings* non-`None`, *reject_reason* `None` — a usable persona config was read;
    * *settings* `None`, *reject_reason* a specific human-readable cause naming the
      offending file and what was wrong with it — the config is PRESENT but UNUSABLE, and
      the caller reports the reason verbatim;
    * BOTH `None` — this harness has no persona reader at all (today goose and
      `no_agent.NoAgentTarget`, which inherit the base no-op). Not a complaint about any
      file.

    `settings` and `reject_reason` are never both non-`None`. Splitting "no reader" from
    "unusable config" is the point: a reject must name its own cause instead of collapsing
    into a bare `None` the caller can only report as a vague "no usable config".
    """

    settings: PersonaSettings | None
    reject_reason: str | None


class PersonaProbeVerdict(Enum):
    """The four distinct answers `Target.verify_persona` can give.

    Kept apart so a caller never reports "the probe ran and could not decide" and "no
    probe ever ran, and none ever will for this input" as the same thing — the latter
    covers configurations that are perfectly valid and that no user can act on.
    """

    PASS = "pass"                        # 2xx — the endpoint accepted the token
    REJECTED = "rejected"                # 401/403 — a POSITIVE auth reject
    INCONCLUSIVE = "inconclusive"        # probed, could not decide (blip/ambiguous)
    NOT_APPLICABLE = "not_applicable"    # no probe was attempted, and none will be


class PersonaProbeOutcome(NamedTuple):
    """The result of `Target.verify_persona`: a verdict plus a named cause.

    * `PASS` — the endpoint accepted the token and answered;
    * `REJECTED` — the endpoint positively refused it (401/403);
    * `INCONCLUSIVE` — the probe WAS attempted and could not decide: the endpoint was
      unreachable, or answered something that is neither an accept nor an auth reject.
      A transient, reportable condition, and the one the launch warning exists for
      (DESIGN §5b: never punish a launch for a blip, but do say the endpoint went
      unanswered);
    * `NOT_APPLICABLE` — nothing was learned about the token and nothing will be for this
      input: the harness implements no probe, the token could not be read, or the endpoint
      answered that it requires a model this persona does not name
      (`probe_outcome_no_model`). Nothing is wrong and there is nothing a user could do,
      so a caller reports it to the LOG, not to the user. "The persona names no model" is
      NOT on this list on its own: such a persona is probed with the field OMITTED,
      because the endpoint may not need one.

    *reason* is a human-readable clause naming the specific cause, set for the two
    non-answer arms and `None` for `PASS`/`REJECTED`, which name themselves. It is written
    to interpolate into a sentence, e.g. `f"could not verify … ({outcome.reason})"`.
    """

    verdict: PersonaProbeVerdict
    reason: str | None = None

    @classmethod
    def passed(cls) -> "PersonaProbeOutcome":
        """2xx: the endpoint accepted the token."""
        return cls(PersonaProbeVerdict.PASS)

    @classmethod
    def rejected(cls) -> "PersonaProbeOutcome":
        """401/403: the endpoint positively refused the token."""
        return cls(PersonaProbeVerdict.REJECTED)

    @classmethod
    def inconclusive(cls, reason: str) -> "PersonaProbeOutcome":
        """The probe ran and could not decide; *reason* says what it saw."""
        return cls(PersonaProbeVerdict.INCONCLUSIVE, reason)

    @classmethod
    def not_applicable(cls, reason: str) -> "PersonaProbeOutcome":
        """No probe was attempted; *reason* says why none is possible."""
        return cls(PersonaProbeVerdict.NOT_APPLICABLE, reason)


def probe_outcome(status: int | None) -> PersonaProbeOutcome:
    """Map an HTTP probe *status* onto a `PersonaProbeOutcome`.

    Covers only the arms an ATTEMPTED HTTP probe can reach — every caller has already
    decided a request was possible, so `NOT_APPLICABLE` never comes from here:

    * 2xx -> `PASS`; 401/403 -> `REJECTED`;
    * `None` (transport failure: DNS, refused, TLS, timeout, bad URL) -> `INCONCLUSIVE`
      "unreachable";
    * anything else (404 wrong path, 429 rate-limit — the token was accepted, 5xx, a 3xx
      we refuse to follow) -> `INCONCLUSIVE` naming the status. Never punish a launch for
      an endpoint blip (DESIGN §5b).
    """
    if status is None:
        return PersonaProbeOutcome.inconclusive("the endpoint could not be reached")
    if 200 <= status < 300:
        return PersonaProbeOutcome.passed()
    if status in (401, 403):
        return PersonaProbeOutcome.rejected()
    return PersonaProbeOutcome.inconclusive(
        f"the endpoint answered HTTP {status}, which is neither an accept nor an auth reject"
    )


# The statuses an endpoint answers when the request is well-formed EXCEPT for a missing
# `model` field: 400 (the `invalid_request_error` the reference anthropic/OpenAI APIs
# return) and 422 (the validation status FastAPI/vLLM-style OpenAI-compatible servers use
# for the same thing). Consulted ONLY by `probe_outcome_no_model`.
_MODEL_REQUIRED_STATUSES = (400, 422)


def probe_outcome_no_model(status: int | None) -> PersonaProbeOutcome:
    """`probe_outcome`, for a probe that deliberately OMITTED `model`.

    A persona that names no model is NOT invalid: a persona endpoint is a third-party
    anthropic-/OpenAI-compatible provider, not the reference API, and such a server may
    serve exactly one model or apply its own default. So the probe ASKS rather than
    declining to run — declining would let a dead token sail past the launch gate and 401
    inside the box, which is the one protection the per-launch probe exists to give.

    The answer then needs one reading `probe_outcome` cannot make, because only the caller
    knows the field was left out:

    * `PASS` / `REJECTED` / `INCONCLUSIVE` are UNCHANGED. An auth reject is an auth reject
      whether or not a model was named, and preserving that arm for a model-less persona
      is the entire point of probing one.
    * a model-required answer (`_MODEL_REQUIRED_STATUSES`) becomes `NOT_APPLICABLE` rather
      than a warning: it says this endpoint needs a model and this persona names none, so
      nothing was learned about the token, and the harness may still supply its own
      default at runtime. It must neither block a launch nor nag on every one.

    The status set is an INFERENCE — the wire does not say "you omitted the model" — so it
    is deliberately narrow and applies ONLY when the caller omitted the field. It never
    widens `probe_outcome`'s own mapping.
    """
    if status in _MODEL_REQUIRED_STATUSES:
        return PersonaProbeOutcome.not_applicable(
            f"the endpoint requires a model in the request (HTTP {status}) and "
            f"the persona names none"
        )
    return probe_outcome(status)


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
    """Declarative data a plugin exposes via `Target.descriptor`; divergent LOGIC stays in hooks.

    Maps onto the per-agent keyspace (`settings-keyspace-1.8.0.md` §2d), keyed by
    `@meta.agent.<agent>.name` (the plugin's `name` property). A few §2d keys are
    *informational* — they describe where core derives a path, not a descriptor field:
    `agent.<agent>.path` (`@config.agents/<name>`, derived in core),
    `agent.<agent>.template` (the layer-2 seed source, owned by the templates layer), and
    `agent.<agent>.transform` (NAMES which binary transform runs — a plugin declares its
    value through `setting_descriptors`, so it is a behavior SETTING, not a descriptor
    field, and it is realized on no channel). The `synced` category in §2d is the spec VIEW of
    `cred_files` (realized by the credsync engine); `critical` is the set of
    AGENT_CRITICAL `bindings` keys.
    """

    command: tuple[str, ...]                       # box argv prefix (e.g. ("claude",))
    bindings: tuple[Binding, ...]                  # ALL bound elements; ordered; >=1
    mode: dict[str, tuple[str, ...]]               # INTERACTIVE launch ONLY: {"start", "continue"}
    operations: dict[str, Operation] = field(default_factory=dict)  # standalone ops, no mode
    access_realization: AccessRealization | None = None
    settings: tuple[SettingArg, ...] = ()
    persona: "PersonaSpec | None" = None   # harness-specific persona endpoint/token delivery
                                           # (None = claude-style env + ANTHROPIC_AUTH_TOKEN)
    # ⚑ THERE IS NO `container_env` FIELD: a plugin's environment variables are
    # SETTINGS KEYS (`agent.<agent>.env.<VAR>`, `Target.default_envs`), declared at
    # the defaults file's top-level `env:` section and delivered by the launch's one
    # settings channel. A descriptor field would be a second, unoverridable route to
    # the same box env; `agent_defaults.load_descriptor` refuses `container_env:` by
    # name so a plugin still declaring one fails rather than losing its variables.
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False           # True -> core calls Target.prepare_host before mounts
    init_dirs: tuple[str, ...] = ()   # extra dirs to mkdir in the project home (home-relative)
    auth_share_support: bool = False  # RO CAPABILITY (spec §2d): does this agent SUPPORT shared
                                      # credentials? Materialized as
                                      # meta.agent.<agent>.auth.share_support (plugin-set).
    vscode_extension: str | None = None  # VS Code Marketplace extension id auto-installed into the
                                         # box on attach (`kanibako code`); None = the agent ships
                                         # no editor extension.


def _validate_agent_binary(binary: Path) -> str | None:
    """Validate that *binary* is a usable host agent executable.

    Returns a short, human-readable REASON string when the binary is unusable, or `None`
    when it looks fine. The check runs on the HOST path (`AgentInstall.binary`) at launch
    time, before the container is mounted/run.

    Deliberately LENIENT, to avoid false positives on legitimate native binaries OR
    shebang wrappers: it fails only when the path is missing, zero bytes (the documented
    0-byte/corrupt-binary incident), not marked executable, or begins with all-NUL bytes
    (a truncated/corrupt download). ELF magic is deliberately NOT required.
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

    # Lenient corruption check: a non-empty file whose leading bytes are all NUL is
    # neither a native binary nor a shebang wrapper. Read only a few bytes, and never
    # reject on a read failure.
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

    A target holds all agent-specific logic: detection, binary mounting, home
    initialization, credential management, CLI argument building. Kanibako's core is
    agent-agnostic; all agent knowledge lives in Target implementations.
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
        """Declarative plugin descriptor; None only for the built-in no-agent shell.

        Core assembles launch argv, bindings, container env and credential sync
        declaratively from this descriptor; the legacy per-method launch hooks were
        removed for the public release. Every shipped agent plugin returns a descriptor.
        The sole descriptor-less target is `no_agent.NoAgentTarget`, which launches a
        plain shell with no agent argv and no delivery binds.
        """
        return None

    def check_auth(self) -> bool:
        """Check if the agent is authenticated. Returns True if ok."""
        return True

    def prepare_host(self, install: "AgentInstall", *, auto_auth: bool, data_path: Path) -> None:
        """Plugin-owned pre-launch host preparation.

        Called by core `start.py` once a host install is detected and BEFORE mounts are
        built, so the plugin can own everything agent-specific that must touch the host
        first (updating the host binary to a stable version, refreshing host auth with the
        right environment). Core stays agent-agnostic: it just invokes this hook.

        Implementations MUST NOT crash the launch — a failure here should be logged and
        swallowed; a hard auth/binary failure is surfaced separately via `check_auth` /
        `_validate_agent_binary`.

        *install* is the detected `AgentInstall`; *auto_auth* says whether automated
        browser auth should be attempted; *data_path* is the kanibako data dir, for auth
        cookie storage. Default: no-op.
        """
        return None

    def default_common(self) -> dict[str, BindArm]:
        """Declare default AGENT-scope common/caches binds for this agent.

        Maps a DISCRIMINATED TERMINAL category key (`agent.<agent>.common` /
        `agent.<agent>.caches`) to its whole dest-keyed `BindArm`
        `{box_dest: (host_src[, options])}` — structured, not a colon-joined string.
        Injected as the AGENT level's declared defaults (`default_categories`) in the
        category resolver, so a user can override or suppress (terminal "") any entry
        at a more-specific level. The default returns {} (no declared entries).
        """
        return {}

    def default_seeds(self) -> dict[str, BindArm]:
        """Declare default copy-once-at-init seeds for this agent.

        Maps the DISCRIMINATED TERMINAL seed key (`agent.<agent>.seeded`) to its whole
        dest-keyed `BindArm`, injected as the AGENT level's declared defaults exactly as
        `default_common`; a user can override or suppress (terminal "" or the "empty"
        sentinel) any entry. The default returns {}; no target ships a seed yet.

        ⚑ A `seeded` dest is spelled GUEST-side like every other dest (spec §0
        "ONE DEST SPACE, TWO DELIVERIES") and RESOLVED to the box store when the
        copy runs. It STAYS A COPY — the shape it shares with `bindings` says how
        the entry is written down, never what is done with it.
        """
        return {}

    def default_envs(self) -> dict[str, str]:
        """Declare default AGENT-scope environment VARIABLES for this agent.

        Maps a DISCRIMINATED `agent.<agent>.env.<VAR>` KEY to its scalar value —
        declared as the AGENT level's defaults exactly as `default_common`, so the
        values reach the box through the ONE settings channel: a user overrides one
        by writing the SAME key in a nearer file, and the SAME variable named at a
        SECOND scope is a launch REFUSAL naming both keys
        (`store_collapse.collapse_env` — the write-once arbitration is the
        collapse's and there is none here). The default returns {}.

        ⚑ This is the ONLY route a plugin has for a STATIC variable: the launch folds
        the table into its default-categories floor, re-keyed to the ACTIVE NODE so a
        persona sees its harness's variables. A descriptor's ENV-channel `settings` /
        `access_realization` are a different job — they REALIZE a resolved value per
        launch, they do not declare one.

        A plugin that ships its declarations in its `<agent>-defaults.yaml` `env:`
        section gets them from `agent_defaults.load_envs`, which is what all three
        first-party plugins call.
        """
        return {}

    def rom_root(self) -> Path | None:
        """Locate this plugin's packaged BIBLE CHAPTER root (`<pkg>/data/rom`).

        The source of the `box.bindings.ro.canon_bible_agent` bind (spec §2c), which CORE
        emits from the resolved target — see `core_defaults.rom_agent_default_categories`.
        The plugin's `data/rom` IS the chapter root: it ships `directives/ROM_AGENT.md`,
        not a deep `canon/bible/agent/...` mirror, so CONTAINMENT holds by construction —
        a plugin cannot place a file outside its own chapter, so there is nothing to guard
        and no silently-ignored out-of-chapter file.

        The package is derived from `sys.modules[type(self).__module__].__package__` rather
        than from `name`: `name` is the HARNESS name and only happens to match
        `kanibako.plugins.<name>` for the first-party plugins, while `__package__` is
        correct whether the Target class lives in `<pkg>/target.py` or in
        `<pkg>/__init__.py` (a naive `__module__.rsplit(".", 1)[0]` is wrong for the
        latter).

        Returns `None` on ANY failure — no such module entry, no `__package__`, an
        unimportable/absent package, or no `data/rom` directory. Directory plugins
        (`~/.local/share/kanibako/plugins/`, `{project}/.kanibako/plugins/`) are not
        `kanibako.plugins.*` packages and simply resolve to `None`, which is the right
        answer for them.
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
        """Declare default AGENT-scope `@`-ref-sourced category binds.

        Returns a UNIFORM table (`CategoryBindDefaults`) of DISCRIMINATED scoped category
        keys — *agent* is the declaring plugin's own name, and the agent tier is always
        discriminated (spec §2d / §0: there is no bare `agent.<key>`). ONE shape, for
        every category:

        every key is TERMINAL — `agent.<agent>.bindings.{ro,rw}` for an ARMED category,
        `agent.<agent>.{caches,seeded,common,synced}` for the rest — and its whole VALUE
        is a `BindArm`, i.e. `{box_dest: (meta_ref[, "ro"])}`. The box DESTINATION is the
        KEY; there is no entry name. Each destination must be normalized with
        `settings_resolve.normalize_bind_dest`, because the launch floor merge in
        `commands.start` dedupes on these keys BEFORE anything parses them, so an
        un-normalized `~/x` and a `/home/agent/x` would collide at one mountpoint as two
        surviving entries. `core_defaults.add_bind` does the normalizing and enforces
        act-once: one category map admits ONE entry per destination.

        ⚑ ONE SHAPE, TWO DELIVERIES. `seeded` and `synced` are COPIES and stay copies —
        sharing the dest-keyed shape says how an entry is WRITTEN DOWN, never what is
        done with it.

        A plugin still returning a retired name-keyed
        `agent.<agent>.<category>.<name>` key is REFUSED by name at
        `settings_assemble._insert_dotted` when the launch floor is assembled, not
        silently ignored. There is no shim (v1.8.0 is a clean break).

        The HOST SOURCE stays a raw `@`-ref STRING; the launch category cascade folds this
        table into the floor and `expand` resolves the ref, so a plugin declares a bind to
        a shared source with NO per-harness path knowledge in core (spec §2d). These are
        injected as the AGENT level's declared defaults (`default_categories`) alongside
        `default_common`; a user can override or suppress (terminal "") any of them at a
        more-specific level, BY ITS DESTINATION, since that is now the key.

        A plugin owns its own harness-slot `box_dest` while an `@`-ref source keeps core
        agent-agnostic. A plugin's own bible chapter is NOT declared here: it is the
        INTERNAL `canon_bible_agent` bind core emits from `rom_root`, kept out of the agent
        keyspace precisely so it stays unrepointable like the rest of the book. The default
        returns `{}` (no category binds).

        A plugin that ships its declarations in its `<agent>-defaults.yaml`
        `category_binds:` section gets the arm shape for free from
        `agent_defaults.load_category_binds`, which is what all three first-party plugins
        call.
        """
        return {}

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare what runtime settings this target supports, as `TargetSetting` entries.

        The default returns an empty list (no declared settings).
        """
        return []

    def generate_agent_config(self) -> AgentConfig:
        """Return a default AgentConfig for this target.

        Subclasses override to provide agent-specific defaults (template variant, state
        knobs, shared caches).
        """
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate agent-state values into `(cli_args, env_vars)`.

        The base implementation ignores all state keys; subclasses override to handle the
        keys they know.
        """
        return [], {}

    @property
    def default_entrypoint(self) -> str | None:
        """Binary name for container entrypoint. None = use bash."""
        return None

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether this agent has a session to resume under *home*.

        *home* is the box home directory as seen from the HOST (the home bind source — the
        same seam `credential_check_path` and the credsync hooks receive as
        `proj.shell_path`). `start.py` consults this at the continue-vs-new seam: when the
        DEFAULT continue mode was selected but the target positively reports nothing to
        resume, it builds the new-session command directly instead of ATTEMPTING a doomed
        resume, whose fast-dying container races the attach path into a raw runtime error.
        This is now the SOLE continue-vs-fresh guard, the launch-time crash-and-retry net
        having been removed with the dead-pane dependency it relied on, so a target with a
        doomable resume MUST override it to positively detect an empty store.

        Implementations read HOST-side state only — no container exec. Return `False` only
        on a positive determination that no resumable session exists: a wrong `False`
        silently drops a real conversation. Default `True`, which keeps the
        always-attempt-continue behavior byte-identical for an agent that does not
        override.
        """
        return True

    def should_run_setup(self, output: str) -> bool:
        """Check if a launched session's output proves the config did NOT take.

        The LAUNCH is ground truth for a bootable config: a clean host-side `check_auth`
        probe, and even a clean in-box setup exit code, cannot guarantee the agent will
        actually start (the partial-config case). After the in-box setup has run and the
        real session has launched, `start.py` matches this against the captured session
        logs; a match means the agent reported it is still not configured (e.g. goose's
        "Goose is not configured. Run 'goose configure' to set up."), and `start.py`
        surfaces a clear error and returns.

        BOUNDED: setup already ran ONCE this invocation, so a post-launch match only
        ERRORS — it never loops back into setup. Default `False` (claude has no setup step).
        """
        return False

    @property
    def setup_entrypoint(self) -> str | None:
        """Container entrypoint (binary) for the one-time interactive setup.

        `None` (the default) means the target declares no setup step; the auth-probe setup
        branch in `start.py` and in `agent reauth` is skipped entirely, and a failed
        `check_auth` errors out. A target that needs an in-box setup (goose ->
        `goose configure`) returns its setup binary here and the sub-command in
        `setup_args`. When `check_auth` fails for such a target, `start.py` runs the
        command INTERACTIVELY in the box, inheriting stdio so the user can complete
        configuration in-box, then proceeds with the normal launch. Setup runs in
        box-state, which persists across reattach.
        """
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

        *config_root* is the box home as seen from the HOST (`proj.shell_path`); the launch
        call site passes it unconditionally for EVERY agent, and each implementation
        appends its own config surface beneath it (claude `.claude/settings.json`, goose
        `.config/goose/config.yaml`, codex `.codex/config.toml`). The VS Code panel spawns
        its OWN in-box agent WITHOUT kanibako's launch env, so the box's configured
        permission tier must be PERSISTED onto the agent's native config surface to reach
        it. This hook is that delivery, keyed on the CASCADE-resolved `access` and never on
        the per-launch `-S`/`-A` flags — spec §1A's projected-surface exception, because
        the projection OUTLIVES the launch.

        *access* is one of `restricted` / `editing` / `full`. An implementation MUST render
        every tier explicitly and MUST NOT fall through to the permissive arm for a tier it
        does not recognise; the launch has already refused a tier this agent's descriptor
        cannot render, so an unexpected value here is a BUG and should raise.

        Best-effort contract: the caller wraps the call, so a failure never blocks the
        launch — but implementations should still be merge-preserving and idempotent.
        Returns whether a write occurred. Default: no-op (`False`), inherited by an agent
        with no panel-permission surface.
        """
        return False

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed this agent's instruction-delivery SessionStart hook, plus any coupled
        managed config, onto its NATIVE config surface under *config_root*.

        *config_root* is the box home as seen from the HOST (`proj.shell_path`); see
        `deliver_panel_permissions`. Box-side literals an implementation needs (codex's
        in-box config path and cwd for its trust keys) are derived by the PLUGIN from the
        core `settings_resolve.GUEST_HOME` constant — deliberately NOT seam parameters
        while they are constants with a single consumer. If the in-box workdir ever becomes
        key-configurable, promote an agent-agnostic `box_workdir` parameter here instead of
        letting plugins drift.

        *model_provider* is the launch's resolved persona model-provider bundle, `None` for
        bare / non-persona launches — the write must then be byte-identical to a
        provider-less one. Today only codex consumes it; the type generalizes when the
        emitter bodies move into the plugins.

        Best-effort contract as `deliver_panel_permissions`. Returns whether a write
        occurred. Default: no-op (`False`), inherited by an agent with no directive-hook
        surface (goose, the no-agent shell).
        """
        return False

    def reattach_config_notice(self) -> str | None:
        """A heads-up to print when REATTACHING to an ALREADY-RUNNING box.

        The launch-time delivery seams (`deliver_panel_permissions` /
        `deliver_directive_hook`) re-materialise this agent's native config surface only on
        (re)start of a STOPPED box; a reattach to a live box early-returns and does NOT
        re-deliver, because it is unsafe to rewrite config under an app-server that already
        read it. So an agent whose config is a RECONCILED PROJECTION (codex's `config.toml`
        model/provider/approval) returns a one-line notice that config changes will not take
        effect until the box is restarted, and core prints it on the reattach path, never
        rewriting the live file. Default `None` — no notice.
        """
        return None

    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome:
        """Extract persona values from a rendered harness config in *config_dir*.

        *config_dir* is a persona-grata store entry's harness dir
        (`$XDG_CONFIG_HOME/personas/<pid>/<hid>/`) holding this harness's NATIVE config
        file. A plugin that models the store's rendering parses it into a
        `PersonaSettings`, and the auto-import maps that onto the agent keyspace.

        FAIL-SOFT contract: an absent, unreadable, malformed or missing-required-keys
        config never raises; it returns an outcome carrying the SPECIFIC reject reason
        rather than a bare `None`, so the caller can report WHY instead of guessing (see
        `PersonaReadOutcome`). Pure read: never writes, never reads the token file.
        Default `PersonaReadOutcome(None, None)` — this harness has no persona reader
        (goose/no_agent), which is NOT a reject.
        """
        return PersonaReadOutcome(settings=None, reject_reason=None)

    def verify_persona(
        self,
        endpoint: str,
        token_path: Path,
        model: str | None,
        *,
        timeout: float = 5.0,
    ) -> PersonaProbeOutcome:
        """Probe *endpoint* with the token at *token_path* — a minimal real ack.

        The persona verify probe (DESIGN §3b): a FEW-token genuine completion round-trip
        against the persona's endpoint, specific to the harness API (anthropic messages vs
        OpenAI responses wire). Returns a `PersonaProbeOutcome`, whose four arms separate a
        probe that RAN and could not decide (`INCONCLUSIVE`) from one that learned nothing
        about the token and never will for this input (`NOT_APPLICABLE`, carrying the named
        cause).

        *model* MAY be `None`: a persona that names none is valid, and an implementation
        must probe it with the `model` field OMITTED rather than decline — never with a
        placeholder or default id (see `probe_outcome_no_model`).

        The OUTCOME is all this reports; what to do with it belongs to the caller, and the
        two callers deliberately answer it DIFFERENTLY. The per-LAUNCH probe treats
        `REJECTED` as a hard error, because a token the provider rejects cannot work and
        saying so beats an in-box 401, while the CREATE-path probe is WARN-ONLY on both
        answered-but-not-PASS verdicts so a fixable token never blocks a create. BOTH are
        silent on `NOT_APPLICABLE`: it names a valid configuration the user cannot act on.

        Contract: NEVER raises; SHORT *timeout*, because a launch must not hang on a blip;
        the token value is read TRANSIENTLY for the request only — never logged, never
        persisted, never returned. Default `NOT_APPLICABLE` — this harness has no probe.
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

        Called by the credential-sync engine ONLY for specs with `filtered=True`.
        *direction* is `"in"` (host->project: seed/refresh) or `"out"` (project->host:
        writeback). *src* is `None` when no source file is available (distinct auth, or the
        host file absent at seed time) — the plugin decides whether to write a default
        *dst* or do nothing.

        Default: plain copy when *src* exists, so a plugin that flags a file `filtered` but
        does not override still gets a sensible wholesale copy. Plugins override to
        filter/merge (claude claudeAiOauth merge + .claude.json allowlist; goose
        config.yaml allowlist).
        """
        import shutil
        if src is not None and Path(src).is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

    def refresh_credentials(self, home: Path) -> None:
        """Refresh agent credentials from host into the project home.

        Default: no-op. Descriptor-native plugins sync creds via `descriptor.cred_files`
        (core's credsync engine); legacy plugins override.
        """
        return None

    def writeback_credentials(self, home: Path) -> None:
        """Write back credentials from project home to host.

        Default: no-op. Descriptor-native plugins sync creds via `descriptor.cred_files`
        (core's credsync engine); legacy plugins override.
        """
        return None

    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None:
        """Plugin-specific post-session writeback BEYOND `cred_files` specs.

        Called by core on every session-end path (clean exit, detach, reattach-exit,
        `kanibako stop`) AFTER the descriptor `cred_files` writeback, for state that cannot
        be modelled as a SYNC `CredFileSpec`. The motivating case is claude's
        `~/.claude.json` `oauthAccount`: the box's login writes the account block there and
        it must reach the host, but the file cannot be a normal SYNC spec, because that
        would also IMPORT host->project and a wholesale copy would clobber host-specific
        `machineID` / `userID` / `projects`. So the plugin MERGES just its own keys back.

        Default: no-op. MUST be defensive — never raise on a malformed or absent file; core
        wraps it, but a clean teardown is the contract.
        """
        return None
