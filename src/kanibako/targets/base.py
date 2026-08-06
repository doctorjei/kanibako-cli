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

# A STRUCTURED category bind default (spec §2a "REPRESENTATION"): a 2- or
# 3-element ``(host_src, box_dest[, options])`` tuple — NOT a colon-joined
# string. Emitted by ``default_common()`` / ``default_seeds()`` and consumed by
# :func:`kanibako.settings.settings_resolve.unpack_bind` through the category resolver.
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
class AccessTierRow:
    """How ONE ``access`` tier is REALIZED for one harness (spec §2d, R-41).

    A row is the harness's answer to *"what do I emit to run at this tier?"*, in
    whichever channel its :class:`AccessRealization` declares:

    * FLAG channel — *flag* is the argv fragment (``("--permission-mode",
      "acceptEdits")``).
    * ENV channel — *env_value* is the value for the ``AccessRealization.env_var``
      (goose ``GOOSE_MODE=approve``).

    An EMPTY row (both fields empty) means **emit nothing, deliberately** — the
    correct realization for a tier a default-safe harness already runs at with no
    argument (claude/codex ``restricted``).  It is NOT the same as having no row:
    a MISSING tier means the harness CANNOT render that tier and the launch
    REFUSES (see :meth:`AccessRealization.row`).  That distinction is the whole reason
    the rows are optional rather than defaulted — for an agent whose unset
    default is UNSAFE (goose's ``GOOSE_MODE`` defaults to ``auto``), "emit
    nothing" and "cannot render" would otherwise both silently mean *permissive*.
    """

    flag: tuple[str, ...] = ()   # FLAG channel emission ( () = emit nothing )
    env_value: str = ""          # ENV channel emission ( "" = emit nothing )


@dataclass(frozen=True)
class AccessRealization:
    """The per-harness realization of the ``access`` permission TIER (spec §2d).

    ⚑ **R-41 generalized this from a two-polarity toggle to per-tier ROWS.** It
    used to carry one unsafe emission plus one "secure" emission, selected by a
    bool; the permission axis is now the three-valued key ``access``
    (``restricted | editing | full``, default ``full``), so each harness declares
    one :class:`AccessTierRow` per tier it can render.

    * *channel* — where every row of this harness is emitted: ``FLAG`` (argv,
      claude/codex) or ``ENV`` (``env_var``, goose ``GOOSE_MODE``).  ONE channel
      per harness, as before: a harness that expressed one tier as a flag and
      another as an env var would be two mechanisms for one axis.
    * *restricted* / *editing* / *full* — the tier rows.  ``None`` = **this
      harness cannot render this tier**; the launch then REFUSES, naming the
      tiers it CAN render (never a silent substitution, and never a fall-through
      to the permissive arm).  The fields are named for the tiers because the
      tier set is CLOSED by the spec (a fourth tier is a spec edit, not a data
      change).
    * *setting_key* — the persisted key the launch reader redeems (all three
      shipped agents = ``"access"``, spec §2d, default ``full``); empty =
      per-launch ``-S``/``-A`` only.

    Special vs :class:`SettingArg`: it is driven by the resolved permission tier,
    not by a plain setting value.

    ⚑ NAME: this was ``SafeBypass`` (descriptor field / defaults-file key
    ``safe_bypass``) up to and including v1.8.0rc1 — the name of the two-polarity
    toggle R-41 replaced, which had outlived its meaning: nothing here is a
    "bypass" (``restricted`` is the opposite of one), and the class is the
    harness's REALIZATION of the access tiers.  Renamed with NO alias: a defaults
    file still spelling ``safe_bypass:`` is REFUSED by name at descriptor load
    (:func:`~kanibako.settings.agent_defaults.load_descriptor`) rather than
    ignored as an unknown key, because being ignored would mean launching with no
    permission emission at all.
    """

    channel: Channel
    env_var: str = ""            # ENV form's variable name (e.g. goose GOOSE_MODE)
    restricted: "AccessTierRow | None" = None   # None = unrenderable by this harness
    editing: "AccessTierRow | None" = None      # None = unrenderable by this harness
    full: "AccessTierRow | None" = None         # None = unrenderable by this harness
    setting_key: str = ""

    def row(self, tier: str) -> "AccessTierRow | None":
        """The :class:`AccessTierRow` for *tier*, or ``None`` when unrenderable.

        ``None`` covers BOTH "this harness declared no row for that tier"
        (goose ``editing``) and "that is not a tier at all" — the caller refuses
        either way, which is the safe collapse: an unknown tier must never reach
        an emission.
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
        """The tiers this harness CAN render, least→most permissive.

        Used by the launch refusal to name the legal alternatives for THIS
        agent rather than the abstract enum.  The ORDER comes from
        :data:`~kanibako.settings.settings_keyspace.ACCESS_TIERS`, the one
        declaration of the tier vocabulary — this class spells the tier NAMES as
        fields (that is what makes a missing row a declaration, not a lookup
        miss), but it does not get to have its own opinion about their order.
        """
        return tuple(t for t in ACCESS_TIERS if self.renders(t))


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
      var).  It is the ONLY thing that picks between the two pre-flight gates in
      ``start.py``.
    * *wire_api* — the config-file harness's model-provider wire protocol (codex
      ``[model_providers.<id>].wire_api``); default ``"responses"`` (Codex removed
      the ``"chat"`` wire, openai/codex#7782).  Ignored for ``"env"`` delivery.
    * *provider_pin* — ``(setting_key, value)`` pairs FORCE-applied to the launch's
      effective setting state WHENEVER this persona resolves an active endpoint (so a
      harness whose endpoint requires a specific provider can't be misconfigured).
      Goose pins ``("provider", "openai")`` → the descriptor's ``provider``→
      ``GOOSE_PROVIDER`` :class:`SettingArg` then emits ``GOOSE_PROVIDER=openai`` in
      the box.  Empty (claude/codex) = no pin (byte-identical); a BARE box (no active
      endpoint) is never touched.
    * *model_required* — whether a persona with a resolved endpoint but NO
      cascade-resolved model is a hard error.  A MISSING model is not automatically
      invalid — some endpoints need no model spec — so absence means "this persona
      needs none" unless the harness VETOES here.  Claude keeps the default ``False``
      (its model rides its own channels / harness default); goose and codex both set
      ``True`` (a third-party OpenAI-compatible endpoint has no meaningful default,
      and a config-file harness cannot express "no model" at all).

    A target with NO :class:`PersonaSpec` (``descriptor.persona is None``) resolves
    through the legacy fallback in ``start.py``'s ``_persona_wiring``, which spells
    out the claude shape EXPLICITLY (ENV endpoint delivery + ``ANTHROPIC_AUTH_TOKEN``
    token var, no provider pin, no model gate) — byte-identical to before this seam
    existed.  That explicit spelling is what keeps the fallback claude-shaped: the
    FIELD defaults below are the DECLARED-NOTHING defaults.
    """

    token_var: str = ""
    endpoint_delivery: str = "env"   # "env" | "config_file"
    wire_api: str = "responses"      # config-file harness wire; codex dropped "chat" (openai/codex#7782)
    provider_pin: tuple[tuple[str, str], ...] = ()  # setting pins when endpoint active
    model_required: bool = False     # harness VETO: error if endpoint set but no model


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
    status (→ ``INCONCLUSIVE``).  The response body is not read — the status alone
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


class PersonaSettings(NamedTuple):
    """Persona-SPECIFIC values EXTRACTED from a rendered harness config.

    The persona-grata store (``$XDG_CONFIG_HOME/personas/<pid>/<hid>/``) lays
    down a harness-NATIVE config file (codex ``config.toml``, claude
    ``settings.json``); :meth:`Target.read_persona_settings` parses the one it
    understands into this harness-NEUTRAL record, which the auto-import maps
    onto the agent keyspace (``self.endpoint`` / ``self.model`` /
    ``self.secret_path.<auth_env>``) and whose ``env`` rides the launch as
    plain passthrough.  Distinct from :class:`PersonaSpec`, which declares HOW
    a harness DELIVERS these values into the box — this is the values
    themselves, read back OUT of a rendered config.

    ``env`` is the harness config's env block MINUS the two single-source vars
    (the base URL and the bearer token), which ride their own channels —
    ``endpoint`` here and the secret-path bind respectively — and so must never
    be duplicated into the passthrough.  A config entry whose value is not a
    string cannot be delivered as an env value (a JSON number/bool/null would
    be ``str()``'d into a Python repr); its NAME lands in ``env_dropped`` so a
    caller can warn.  Nothing is ever dropped silently.
    """

    endpoint: str | None   # the alternate base URL (codex base_url / claude ANTHROPIC_BASE_URL)
    model: str | None      # the provider model id, when the config names one
    auth_env: str          # env var the bearer token is exported as (codex env_key /
                           # claude's fixed ANTHROPIC_AUTH_TOKEN)
    # Defaults are IMMUTABLE by construction: a bare ``{}``/``[]`` on a
    # NamedTuple field is a single object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})   # passthrough env block (no single-source vars)
    env_dropped: tuple[str, ...] = ()               # NAMES skipped: value was not a string


class PersonaReadOutcome(NamedTuple):
    """The TRI-STATE result of :meth:`Target.read_persona_settings`.

    Splitting "no reader" from "unusable config" is the whole point: a reject
    must name its OWN cause instead of collapsing into a bare ``None`` the
    caller can only report as a vague "no usable config".

    * *settings* non-``None``, *reject_reason* ``None`` — a USABLE persona
      config was read;
    * *settings* ``None``, *reject_reason* a human-readable SPECIFIC cause
      (naming the offending file and what was wrong with it) — the config is
      PRESENT but UNUSABLE; the caller reports the reason VERBATIM;
    * BOTH ``None`` — this harness has NO persona reader at all (today: goose
      and :class:`~kanibako.targets.no_agent.NoAgentTarget`, which inherit the
      base no-op).  Not a complaint about any file.

    ``settings`` and ``reject_reason`` are never both non-``None``.
    """

    settings: PersonaSettings | None
    reject_reason: str | None


class PersonaProbeVerdict(Enum):
    """The FOUR distinct answers :meth:`Target.verify_persona` can give.

    Same defect this names as :class:`PersonaReadOutcome` did for the reader: a
    bare ``None`` collapsed "the probe ran and could not decide" together with
    "no probe ever ran, and none ever will for this input", so the only report a
    caller could make covered both — and warned, forever, about configurations
    that are perfectly valid (a harness that implements no probe at all; an
    endpoint that turns out to need a model this persona does not name).  Each
    arm is now its own name.
    """

    PASS = "pass"                        # 2xx — the endpoint accepted the token
    REJECTED = "rejected"                # 401/403 — a POSITIVE auth reject
    INCONCLUSIVE = "inconclusive"        # probed, could not decide (blip/ambiguous)
    NOT_APPLICABLE = "not_applicable"    # no probe was attempted, and none will be


class PersonaProbeOutcome(NamedTuple):
    """The result of :meth:`Target.verify_persona` — a verdict + a NAMED cause.

    * ``PASS`` — the endpoint accepted the token and answered;
    * ``REJECTED`` — the endpoint POSITIVELY refused it (401/403);
    * ``INCONCLUSIVE`` — the probe WAS attempted and could not decide: the
      endpoint was unreachable, or answered something that is neither an accept
      nor an auth reject.  This is a TRANSIENT, reportable condition — the one
      the launch warning exists for (DESIGN §5b: never punish a launch for a
      blip, but do say the endpoint went unanswered);
    * ``NOT_APPLICABLE`` — nothing was learned about the token, and nothing
      will be for this input: the harness implements no probe, the token could
      not be read, or the endpoint answered that it requires a model this
      persona does not name (:func:`probe_outcome_no_model`).  There is nothing
      wrong and nothing a user could do, so a caller reports it to the LOG, not
      to the user.  ⚑ "The persona names no model" is NOT on this list on its
      own: such a persona is probed with the field OMITTED, because the
      endpoint may not need one.

    *reason* is a human-readable clause naming the specific cause, set for the
    two non-answer arms and ``None`` for ``PASS``/``REJECTED`` (which name
    themselves).  It is written to be interpolated into a sentence, e.g.
    ``f"could not verify … ({outcome.reason})"``.
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
    """Map an HTTP probe *status* onto a :class:`PersonaProbeOutcome`.

    Covers only the arms an ATTEMPTED HTTP probe can reach — every caller has
    already decided a request was possible, so ``NOT_APPLICABLE`` never comes
    from here:

    * 2xx — the endpoint accepted the token and answered → ``PASS``;
    * 401 / 403 — a POSITIVE auth reject → ``REJECTED``;
    * ``None`` (transport failure: DNS, refused, TLS, timeout, bad URL) →
      ``INCONCLUSIVE`` "unreachable";
    * anything else (404 wrong path, 429 rate-limit — the token was accepted,
      5xx, a 3xx we refuse to follow) → ``INCONCLUSIVE`` naming the status.
      Never punish a launch for an endpoint blip (DESIGN §5b).
    """
    if status is None:
        return PersonaProbeOutcome.inconclusive(
            "the endpoint could not be reached"
        )
    if 200 <= status < 300:
        return PersonaProbeOutcome.passed()
    if status in (401, 403):
        return PersonaProbeOutcome.rejected()
    return PersonaProbeOutcome.inconclusive(
        f"the endpoint answered HTTP {status}, which is neither an accept "
        f"nor an auth reject"
    )


#: The statuses an endpoint answers when the request is well-formed EXCEPT for a
#: missing ``model`` field: 400 (the ``invalid_request_error`` the reference
#: anthropic/OpenAI APIs return) and 422 (the validation status FastAPI/vLLM-style
#: OpenAI-compatible servers use for the same thing).  Consulted ONLY by
#: :func:`probe_outcome_no_model`.
_MODEL_REQUIRED_STATUSES = (400, 422)


def probe_outcome_no_model(status: int | None) -> PersonaProbeOutcome:
    """:func:`probe_outcome`, for a probe that deliberately OMITTED ``model``.

    A persona that names no model is NOT invalid: a persona endpoint is a
    third-party anthropic-/OpenAI-compatible provider, not the reference API, and
    such a server may serve exactly one model or apply its own default.  So the
    probe ASKS rather than declining to run — declining would let a DEAD token
    sail past the launch gate and 401 inside the box, which is the one protection
    the per-launch probe exists to give.

    The answer then needs one reading :func:`probe_outcome` cannot make, because
    only the caller knows the field was left out:

    * ``PASS`` / ``REJECTED`` / ``INCONCLUSIVE`` are UNCHANGED.  ⚑ An auth reject
      is an auth reject whether or not a model was named — preserving that arm
      for a model-less persona is the entire point of probing one.
    * a MODEL-REQUIRED answer (:data:`_MODEL_REQUIRED_STATUSES`) becomes
      ``NOT_APPLICABLE``, not a warning: it says this endpoint needs a model and
      this persona names none, so NOTHING was learned about the token, and the
      harness may still supply its own default at runtime.  It must neither
      block a launch nor nag on every one.

    ⚑ The status set is an INFERENCE — the wire does not say "you omitted the
    model" — so it is deliberately narrow and applies ONLY when the caller
    omitted the field.  It never widens :func:`probe_outcome`'s own mapping.
    """
    if status in _MODEL_REQUIRED_STATUSES:
        return PersonaProbeOutcome.not_applicable(
            f"the endpoint requires a model in the request (HTTP {status}) and "
            f"the persona names none"
        )
    return probe_outcome(status)


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

    Maps onto the per-agent keyspace (``settings-keyspace-1.8.0.md`` §2d),
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
    access_realization: AccessRealization | None = None
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

    def rom_root(self) -> Path | None:
        """Locate this plugin's packaged BIBLE CHAPTER root (``<pkg>/data/rom``).

        The source of the ``box.bindings.ro.canon_bible_agent`` bind (spec §2c),
        which CORE emits from the resolved target — see
        :func:`kanibako.settings.core_defaults.rom_agent_default_categories`.  The plugin's
        ``data/rom`` IS the chapter root: it ships ``directives/ROM_AGENT.md``, not
        a deep ``canon/bible/agent/...`` mirror, so CONTAINMENT holds by
        construction (a plugin cannot place a file outside its own chapter, so
        there is nothing to guard and no silently-ignored out-of-chapter file).

        The package is derived from ``sys.modules[type(self).__module__].__package__``
        rather than from :attr:`name`: ``name`` is the HARNESS name and only happens
        to match ``kanibako.plugins.<name>`` for the first-party plugins, while
        ``__package__`` is correct whether the Target class lives in
        ``<pkg>/target.py`` or in ``<pkg>/__init__.py`` (a naive
        ``__module__.rsplit(".", 1)[0]`` is wrong for the latter).

        Returns ``None`` on ANY failure — no such module entry, no ``__package__``,
        an unimportable/absent package, or no ``data/rom`` directory.  Directory
        plugins (``~/.local/share/kanibako/plugins/``, ``{project}/.kanibako/
        plugins/``) are not ``kanibako.plugins.*`` packages and simply resolve to
        ``None``, which is the right answer for them.  Additive and defaulted, so
        no plugin needs to override it and no plugin interface breaks.
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

    def default_category_binds(self) -> dict[str, BindDefault]:
        """Declare default AGENT-scope ``@``-ref-sourced category binds.

        Returns a mapping of DISCRIMINATED scoped category keys
        (``agent.<agent>.bindings.ro.<name>``) to
        STRUCTURED bind tuples ``(meta_ref, box_dest[, "ro"])`` (spec §2a) whose
        HOST SOURCE is an ``@``-ref STRING resolved by the launch category cascade —
        the AGENT-scope mirror of :mod:`kanibako.settings.core_defaults`'s ``meta_ref`` bind
        shape.  These are injected as the AGENT level's declared defaults
        (``default_categories``) alongside :meth:`default_common`; a user can
        override or suppress (terminal "") any of them at a more-specific level.

        A plugin owns its own harness-slot ``box_dest`` while an ``@``-ref source
        keeps core agent-agnostic.  (The former per-agent instructions bind was
        retired — the box guide now ships in the packaged CANON, bound ro at
        ``~/canon/bible`` + launch-flatten — so no first-party plugin declares one
        today.  A plugin's own bible chapter is NOT declared here either: it is the
        INTERNAL ``canon_bible_agent`` bind core emits from :meth:`rom_root`, kept
        out of the agent keyspace precisely so it stays unrepointable like the rest
        of the book.)  The default returns ``{}`` (no category binds).
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
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    def apply_state(self, state: dict[str, str]) -> tuple[list[str], dict[str, str]]:
        """Translate agent-state values into CLI args and env vars.

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
        self, *, config_root: Path, access: str,
    ) -> bool:
        """Persist the box's resolved ``access`` TIER onto this agent's
        PANEL-visible config surface under *config_root*.

        *config_root* is the box home as seen from the HOST (``proj.shell_path``)
        — the launch call site passes it unconditionally for EVERY agent, and
        each implementation appends its own config surface beneath it (claude
        ``.claude/settings.json``, goose ``.config/goose/config.yaml``, codex
        ``.codex/config.toml``).  The VS Code panel spawns its OWN in-box agent
        WITHOUT kanibako's launch env, so the box's configured permission tier
        must be PERSISTED onto the agent's native config surface to reach it;
        this hook is that delivery, keyed on the CASCADE-resolved ``access``
        (never the per-launch ``-S``/``-A`` flags — spec §1A's projected-surface
        exception: the projection OUTLIVES the launch).

        *access* is one of ``restricted`` / ``editing`` / ``full``.  An
        implementation MUST render every tier explicitly and MUST NOT fall
        through to the permissive arm for a tier it does not recognise; the
        launch has already refused a tier this agent's descriptor cannot render,
        so an unexpected value here is a BUG and should raise rather than
        default.

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
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed this agent's instruction-delivery SessionStart hook (plus any
        coupled managed config) onto its NATIVE config surface under
        *config_root*.

        *config_root* is the box home as seen from the HOST (``proj.shell_path``);
        see :meth:`deliver_panel_permissions`.  Box-side literals an
        implementation needs (e.g. codex's in-box config path and cwd for its
        trust keys) are derived by the PLUGIN from the core
        :data:`~kanibako.settings.settings_resolve.GUEST_HOME` constant — deliberately
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

    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome:
        """Extract persona values from a rendered harness config in *config_dir*.

        *config_dir* is a persona-grata store entry's harness dir
        (``$XDG_CONFIG_HOME/personas/<pid>/<hid>/``) holding this harness's
        NATIVE config file.  A plugin that models the store's rendering parses
        it into a :class:`PersonaSettings`; the auto-import maps that onto the
        agent keyspace.  FAIL-SOFT contract (UNCHANGED): absent / unreadable /
        malformed / missing-required-keys config never raises — but it now
        returns an outcome carrying the SPECIFIC reject reason rather than a
        bare ``None``, so the caller can report WHY instead of guessing.  See
        :class:`PersonaReadOutcome` for the tri-state.  Default:
        ``PersonaReadOutcome(None, None)`` — harness has no persona reader yet
        (goose/no_agent; add per-harness later or stay a no-op), which is NOT
        a reject.  Pure read; never writes, never reads the token file.
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

        The persona verify probe (DESIGN §3b): a FEW-token genuine completion
        round-trip against the persona's endpoint, harness-API specific
        (anthropic messages vs OpenAI responses wire).  Returns a
        :class:`PersonaProbeOutcome`, whose four arms separate the two things a
        single "unverifiable" used to merge — a probe that RAN and could not
        decide (``INCONCLUSIVE``) from one that learned nothing about the token
        and never will for this input (``NOT_APPLICABLE``, carrying the named
        cause).

        ⚑ *model* MAY be ``None``: a persona that names none is valid, and an
        implementation must probe it with the ``model`` field OMITTED rather
        than decline — never with a placeholder or default id (see
        :func:`probe_outcome_no_model`).

        ⚑ The OUTCOME is all this reports; what to do with it belongs to the
        caller, and there is no longer any "last-known-good" for it to protect
        (the verified swap that persisted store values into the agent settings
        file is GONE — the store is resolved live at every launch).  The two
        callers deliberately answer it DIFFERENTLY: the per-LAUNCH probe treats
        ``REJECTED`` as a hard error (a token the provider rejects cannot work,
        and saying so beats an in-box 401), while the CREATE-path probe is
        WARN-ONLY on both answered-but-not-PASS verdicts so a fixable token
        never blocks a create.  BOTH are silent on ``NOT_APPLICABLE``: it names
        a valid configuration the user cannot act on.

        Contract: NEVER raises; SHORT *timeout* (a launch must not hang on a
        blip); the token value is read TRANSIENTLY for the request only —
        never logged, never persisted, never returned.  Default:
        ``NOT_APPLICABLE`` — this harness has no probe (goose/no_agent inherit
        it; add a per-harness one later).
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
