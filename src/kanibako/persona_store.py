"""Live persona inputs, never persisted.

Classification serves configuration. Registered reads capture native config
and keyless state; legacy reads resolve token pointers. No credential reads or
projection I/O.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple, Protocol

from urllib.parse import urlsplit

from kanibako.agent_ref import (
  AgentIdentity,
  agent_address_node,
  reserved_pseudo_agent_reason,
  display_agent_ref,
  harness_of,
  persona_of,
)
from kanibako.errors import ConfigError
from kanibako.identifiers import agent_node_case, find_identifier, name_flaw
from kanibako.settings.config_io import read_doc_text_capped
from kanibako.utils.xdg import user_config_home
from kanibako.targets.base import (
  PERSONA_DEFAULT_MODEL,
  PersonaReadOutcome,
  _PersonaDefaultModel,
  _REDACTED,
  _scrub_endpoint_userinfo,
)

if TYPE_CHECKING:
  from kanibako.settings.config import AgentResolution

#: Read cap for ``.secret_path`` (bytes).  Anything larger is a malformed store,
#: rejected WITHOUT slurping it into memory: fail warnable, never OOM.
_POINTER_CAP = 16 * 1024


class PersonaInputTarget(Protocol):
  @property
  def name(self) -> str: ...

  def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome: ...

  def project_persona_settings(
    self, config: Mapping[str, object], *, source: str, keyless: bool = False
  ) -> PersonaReadOutcome: ...


@dataclass(frozen=True)
class PersonaEntry:
  """A located persona-grata store entry for one ``<pid>/<hid>`` pair."""

  node: str  # canonical node-name "<pid>℘<hid>"
  persona: str  # pid (identity segment)
  harness: str  # hid (harness segment)
  persona_dir: Path  # <root>/<pid>/  (anchors relative .secret_path values)
  config_dir: Path  # <root>/<pid>/<hid>/  (the rendered harness config dir)


class SecretPathResult(NamedTuple):
  """Outcome of resolving a persona's ``.secret_path`` token pointer.

  Exactly one field is set: *path* (ABSOLUTE host path) on success, else
  *error* — :func:`resolve_secret_path` never raises through.
  """

  path: Path | None
  error: str | None


def persona_store_root() -> Path:
  """The persona-grata discovery root: ``$XDG_CONFIG_HOME/personas/``.

  FIXED by design (DESIGN §5a — not configurable); the single builder for the
  store path, over the internal :func:`kanibako.utils.xdg.user_config_home`
  resolver (keyspec §1A declares the finished config file,
  ``meta.runtime.user.config``, and no ``config_home`` key, so directory users
  route through here, not through a key).
  """
  return user_config_home() / "personas"


def locate_entry(ref: str) -> PersonaEntry | None:
  """Locate the store entry for an agent *ref*, iff one exists.

  ``None`` is a clean "not a persona": the ref is BARE (node == harness) — the
  built-in ``shell`` pseudo-agent included, which a lookup ADDRESSES
  (:func:`~kanibako.agent_ref.parse_agent_address`) — or the store dir
  ``<root>/<pid>/<hid>/`` is absent — store PRESENCE decides
  persona-vs-plain (DESIGN §4), so the caller falls through to normal agent
  handling.  A MALFORMED ref instead raises
  :class:`~kanibako.errors.ConfigError` from ``parse_agent_address``: a bad ref is
  a user error, not a store miss.

  ⚑ THE HARNESS FOLDS, THE PERSONA DOES NOT (keyspec §0
  "An agent's canonical case is carried by its _name_"): the ``<hid>`` directory is
  looked up by NODE, through :func:`~kanibako.agent_ref.agent_address_node`, so
  ``navigator+Claude`` finds ``<root>/navigator/claude/`` — the directory the launch
  looks up, since it builds its node from the lowercase ``target.name``.

  ⚑ PATH TRAVERSAL is handled upstream, not here.  ``.`` stopped being a legal
  segment character on 2026-08-04, so ``..+claude`` and ``navigator+..`` RAISE
  from ``parse_agent_address`` on the first line below.  There is deliberately no
  second dot-check here: one charset, enforced in one place (llm-doc).
  """
  node = agent_address_node(ref)
  harness = harness_of(node)
  if harness == node:
    return None  # bare agent: no persona segment -> never a store entry
  persona = persona_of(node)
  root = persona_store_root()
  persona_dir = root / persona
  config_dir = persona_dir / harness
  try:
    if not config_dir.is_dir():
      return None
  except OSError:
    return None
  return PersonaEntry(
    node=node,
    persona=persona,
    harness=harness,
    persona_dir=persona_dir,
    config_dir=config_dir,
  )


def resolve_secret_path(entry: PersonaEntry) -> SecretPathResult:
  """Resolve ``<pid>/.secret_path`` to an ABSOLUTE host token path.

  The pointer file holds EXACTLY ONE line (DESIGN §2).  ``$VAR`` then ``~``
  are expanded — the same order as the launch-side ``_secret_pointer_usable``
  — an absolute result is used as-is, and a RELATIVE one is anchored to
  *entry.persona_dir*, the directory ``.secret_path`` sits in.  An absolute
  HOST path is required because the token is mounted arm's-length into the
  box, where a relative anchor is meaningless (DESIGN §3).

  The token file itself is NEVER opened or stat'd here: the result is a
  pointer, and it resolves even when the token does not exist yet — usability
  is the launch gate's job.

  Every malformed-pointer shape yields ``SecretPathResult(None, <reason>)``
  for the caller to warn on; this never raises through.  Full case list in
  ``llm-docs/kanibako/persona_store.py.md``.
  """
  pointer = entry.persona_dir / ".secret_path"
  try:
    # capped: one path line, never a slurp
    raw = read_doc_text_capped(pointer, cap=_POINTER_CAP, encoding="utf-8")
  except FileNotFoundError:
    return SecretPathResult(None, f"no .secret_path file at {pointer}")
  except UnicodeDecodeError:
    return SecretPathResult(None, f"{pointer} is not valid UTF-8 text")
  except OSError as exc:
    return SecretPathResult(None, f"cannot read {pointer}: {exc}")
  if len(raw) > _POINTER_CAP:
    return SecretPathResult(
      None,
      f"{pointer} is too large to be a token path (one line expected)",
    )

  # Exactly one line: strip ONE trailing newline (LF or CRLF), then any
  # remaining newline means a real second line (the file is malformed).
  body = raw[:-1] if raw.endswith("\n") else raw
  body = body[:-1] if body.endswith("\r") else body
  if "\n" in body or "\r" in body:
    return SecretPathResult(
      None,
      f"{pointer} must hold exactly one line (the token path)",
    )
  line = body.strip()
  if not line:
    return SecretPathResult(None, f"{pointer} is empty")

  try:
    expanded = Path(os.path.expandvars(line)).expanduser()
    if expanded.is_absolute():
      return SecretPathResult(expanded.resolve(), None)
    return SecretPathResult((entry.persona_dir / expanded).resolve(), None)
  except (OSError, RuntimeError, ValueError) as exc:
    # expanduser -> RuntimeError (unresolvable ~user); resolve -> ValueError
    # (embedded NUL) / OSError.  All are malformed-pointer shapes; the
    # never-raises-through contract holds.
    return SecretPathResult(None, f"cannot resolve {pointer}: {exc}")


# --------------------------------------------------------------------------
# The LIVE bundle: one store read -> the values ONE launch resolves against.
# --------------------------------------------------------------------------


class PersonaBundle(NamedTuple):
  """The harness-NEUTRAL persona values for ONE launch — LIVE, never persisted.

  Everything :func:`read_persona_bundle` got out of the store in a single
  read: a VALUE for one launch, not a cached view.  Nothing here is written to
  any file.  Field-by-field notes are in
  ``llm-docs/kanibako/persona_store.py.md``.

  *token_error* is a SOFT condition — the rest of the bundle is still usable,
  so a caller warns and carries on rather than refusing the launch.

  ⚑ *reject_reason* and *no_reader* mirror the two non-success arms of
  :class:`~kanibako.targets.base.PersonaReadOutcome` ONE-FOR-ONE, and keeping
  them apart is load-bearing: a launch HARD-ERRORS on *reject_reason* (a
  config the harness could read and refused), but must NOT error on
  *no_reader* — a goose persona is configured entirely through the keyspace
  and merely happens to own a store directory, so refusing it would break
  every such launch.  Both render ``{}``; only the DIAGNOSIS differs.
  """

  endpoint: str | None = None  # the harness config's alternate base URL
  # None = the config names no model; PERSONA_DEFAULT_MODEL = it writes null; "" = it writes "".
  model: str | None | _PersonaDefaultModel = None
  auth_env: str | None = None  # env var the token exports as; NAMES the secret_path.<auth_env>
  # Defaults are IMMUTABLE by construction: a bare ``{}`` on a NamedTuple
  # field is ONE object shared by every instance.
  env: Mapping[str, str] = MappingProxyType({})  # passthrough block, base-URL/token stripped
  env_dropped: tuple[str, ...] = ()  # names SKIPPED as undeliverable — never silent
  token_path: Path | None = None  # resolved ABSOLUTE host token path
  token_error: str | None = None  # why it did not resolve (set iff token_path is None)
  reject_reason: str | None = None  # the harness READ this config and REFUSED it
  no_reader: bool = False  # harness has NO persona reader (goose, ShellTarget)

  @property
  def request_model(self) -> str | None:
    """The model to name in a request: *model* when it is a non-empty ID, else ``None``.

    The ONE carrier of that rule for the raw store readers (the create-time probe,
    ``scripts/keyspec-extract.py``): a ``null`` and a ``""`` model both name none.
    """
    return self.model if isinstance(self.model, str) and self.model else None

  def to_persona_values(self) -> dict[str, str | None]:
    """Render this bundle as the UN-DISCRIMINATED persona-values mapping.

    The shape ``build_launch_snapshot(persona_values=…)`` takes.  The keys
    are deliberately NOT discriminated onto an agent — the store knows a
    persona, not a cascade; ``settings_launch._persona_partial`` wraps them
    under the active agent slot.  *reject_reason* and *no_reader* both
    render ``{}``.

    ⚑ EMPTINESS IS HANDLED PER VALUE CLASS, and the asymmetry is the point:

    * *endpoint* and the token path are OMITTED when absent or empty.  This
      mapping is a cascade LEVEL, so emitting ``""`` would not mean "unset" —
      it would OVERRIDE ``agent.default`` and every rung below with emptiness.
    * *model* has four states, and only absent (``None``) is omitted: the
      marker :data:`~kanibako.targets.base.PERSONA_DEFAULT_MODEL` renders as a
      present ``None`` (the persona's default), and a ``str``, ``""`` included,
      passes as itself (no model).  ``null`` and ``""`` are SET values that beat
      ``agent.default.model``.
    * an ``env.<VAR>`` value passes through VERBATIM, empty string
      INCLUDED: the reader already ruled it deliverable by keeping it out of
      *env_dropped*, so dropping it here would be an invisible loss.  (An
      empty var NAME is still skipped — undeliverable, not empty.)
    """
    if self.reject_reason is not None or self.no_reader:
      return {}
    values: dict[str, str | None] = {}
    if self.endpoint:
      values["endpoint"] = self.endpoint
    if self.model is PERSONA_DEFAULT_MODEL:
      values["model"] = None
    elif isinstance(self.model, str):
      values["model"] = self.model
    if self.auth_env and self.token_path is not None:
      values[f"secret_path.{self.auth_env}"] = str(self.token_path)
    for name, val in self.env.items():
      if name:
        values[f"env.{name}"] = val
    return values


#: Schemes the delivered HTTP clients (urllib probe, Node harness runtimes) can act on.
_ENDPOINT_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class RegisteredPersona:
  """Live PG input; store_root is captured, never reconstructed from entry_path."""

  identity: AgentIdentity
  store_root: Path
  config: dict[str, object] = field(repr=False)
  auth_env: str | None
  keyless: bool


def read_agent_input(address: AgentIdentity) -> AgentResolution:
  """Classify sources; carry unavailable diagnoses."""
  from dataclasses import replace

  from kanibako.agent_ref import CANONICAL_SEP, with_harness
  from kanibako.errors import AgentNotInstalledError
  from kanibako.install_method import install_command
  from kanibako.settings.config import AgentResolution
  from kanibako.targets import discover_targets

  identity = address
  try:
    installed = set(discover_targets())
  except Exception as exc:
    return AgentResolution(identity, unavailable=exc)

  registered = None
  if (
    CANONICAL_SEP not in identity.node and find_identifier(address.target_name, installed) is None
  ):
    try:
      registered = read_registered_persona(address.target_name)
    except Exception as exc:
      return AgentResolution(identity, unavailable=exc)
    if registered is not None:
      identity = registered.identity

  found = find_identifier(identity.target_name, installed)
  if found is not None:
    if registered is not None:
      identity = replace(identity, harness=found, target_name=found)
      registered = replace(registered, identity=identity)
    else:
      node = with_harness(identity.node, found)
      identity = replace(
        identity, node=node, persona=persona_of(node), harness=found, target_name=found
      )
    return AgentResolution(identity, registered)

  if registered is not None:
    error = AgentNotInstalledError(
      f"PG agent '{identity.registered_name}' requires harness "
      f"'{identity.target_name}'. Install it with:\n"
      f"  {install_command(f'kanibako-agent-{identity.target_name}')}"
    )
  else:
    error = AgentNotInstalledError(
      f"Agent '{identity.target_name}' is not installed. Install it with:\n"
      f"  {install_command(f'kanibako-agent-{identity.target_name}')}\n"
      f"Or run 'kanibako agent list' to see installed agents."
    )
  return AgentResolution(identity, registered, error)


def read_registered_persona(name: str) -> RegisteredPersona | None:
  """Read identity before choosing a target, without fetching credentials or writing files."""
  from kanibako.settings.config_io import (
    load_persona_agent,
    persona_store_agents,
    persona_store_location,
  )

  root = persona_store_location()
  try:
    agents = persona_store_agents(root=root)
    found = find_identifier(name, agents)
    if found is None:
      return None
    if find_identifier(name, (other for other in agents if other != found)) is not None:
      raise ConfigError(f"persona store at {root}: agent name '{name}' is ambiguous by case")
    if (reason := reserved_pseudo_agent_reason(found)) is not None:
      raise ConfigError(reason)
    path, resolved = load_persona_agent(found, root=root)
    agent = resolved["agent"]
    persona = agent["persona"]["name"]
    harness = agent["harness"]
    harness_name = harness["name"]
    for component, spelling in (("persona", persona), ("harness", harness_name)):
      flaw = name_flaw(spelling) if isinstance(spelling, str) else "must be a string"
      if flaw is not None:
        from kanibako.launch.box_identity import name_spelling

        offer = name_spelling(spelling) if isinstance(spelling, str) else None
        cure = f"; try '{offer[0]}'" if offer is not None else ""
        raise ConfigError(f"PG agent '{found}' {component} name {spelling!r} {flaw}{cure}")
      if (reason := reserved_pseudo_agent_reason(spelling)) is not None:
        raise ConfigError(f"PG agent '{found}' {component}: {reason}")
    keyless = agent["secret"] is None
    config = harness["config_store"]
    if not isinstance(config, dict):
      raise ConfigError(f"PG agent '{found}' has no native harness configuration mapping")
    auth_env = harness["auth_var"]
    if not keyless and (not isinstance(auth_env, str) or not auth_env):
      raise ConfigError(f"PG agent '{found}' has no authentication variable")
    identity = AgentIdentity(
      node=agent_node_case(found),
      registered_name=found,
      persona=persona,
      harness=harness_name,
      target_name=agent_node_case(harness_name),
      entry_path=path,
    )
    return RegisteredPersona(
      identity=identity,
      store_root=root,
      config=config,
      auth_env=None if keyless else auth_env,
      keyless=keyless,
    )
  except ConfigError:
    raise
  except (OSError, ValueError, KeyError, TypeError) as exc:
    raise ConfigError(f"persona store at {root}: cannot read agent '{name}': {exc}") from exc


def project_registered_persona(
  persona: RegisteredPersona, target: PersonaInputTarget
) -> PersonaBundle:
  """Project native values and keyless names without I/O or mutation."""
  identity = persona.identity
  if find_identifier(target.name, (identity.target_name,)) is None:
    raise ConfigError(
      f"PG agent '{identity.registered_name}' uses harness '{identity.harness}', not '{target.name}'"
    )
  outcome = target.project_persona_settings(
    persona.config, source=f"{identity.entry_path}/agent.cfg", keyless=persona.keyless
  )
  if outcome.reject_reason is not None:
    raise ConfigError(outcome.reject_reason)
  if outcome.settings is None:
    raise ConfigError(f"the {target.name} harness has no native persona projection")
  settings = outcome.settings
  if settings.endpoint is not None:
    validate_endpoint(settings.endpoint)
  env = dict(settings.env)
  if persona.keyless:
    native_env = persona.config.get("env")
    if isinstance(native_env, dict) and settings.auth_env:
      placeholder = native_env.get(settings.auth_env)
      if isinstance(placeholder, str):
        env[settings.auth_env] = placeholder
  return PersonaBundle(
    endpoint=settings.endpoint,
    model=settings.model,
    auth_env=settings.auth_env if persona.keyless else persona.auth_env,
    env=MappingProxyType(env),
    env_dropped=settings.env_dropped,
  )


def validate_endpoint(endpoint: str) -> None:
  """Raise :class:`~kanibako.errors.ConfigError` unless *endpoint* is a well-formed
  ``http``/``https`` base URL — validate-only, like :func:`kanibako.agent_ref.parse_agent_ref`.

  Minimal well-formedness ONLY, by design: a recognized scheme and a non-empty host,
  nothing about path/port/query.  A persona endpoint is a base URL the harness appends
  its own routes to, so a stricter gate risks a FALSE REFUSAL — breaking a working box,
  the one outcome this check must never cause.  ⚑ The uncaught live incident it exists
  for: ``myhost:8080/v1`` reads as ``urlsplit``-scheme ``"myhost"``, sails past every
  truthiness check downstream, and dies inside Node with ``Invalid URL``.

  ⚑ Every message names the endpoint USERINFO-SCRUBBED (`_scrub_endpoint_userinfo`)
  and prints nothing the scrubbed form does not show: a malformed endpoint still
  carries the user's credential, and this refusal is where it gets printed.  The
  check runs on the raw endpoint; only the message is scrubbed.
  """
  shown = _scrub_endpoint_userinfo(endpoint)
  try:
    parsed = urlsplit(endpoint)
  except ValueError:
    # urllib quotes the raw netloc in some of these texts, so the fault is re-read
    # off *shown*; if *shown* splits, the fault lay in the span the scrub dropped.
    # ``from None``: the raw ValueError would carry that netloc along as __cause__.
    try:
      urlsplit(shown)
    except ValueError as exc:
      fault = str(exc)
    else:
      fault = "the userinfo before '@' is not well-formed"
    raise ConfigError(f"persona endpoint {shown!r} is not a well-formed URL ({fault})") from None
  if parsed.scheme.lower() not in _ENDPOINT_SCHEMES:
    # With no ``//`` the scrub can take a would-be scheme along with the userinfo
    # (``user:pw@host``); echo it only if it survived into *shown*.
    got = parsed.scheme if shown.lower().startswith(parsed.scheme) else _REDACTED
    raise ConfigError(
      f"persona endpoint {shown!r} has no recognized scheme "
      f"(got {got!r}; must start with 'http://' or 'https://')"
    )
  if not parsed.hostname:
    # The scheme is http/https by now, so it is safe to echo even where the scrub
    # took it (``https:tok@host``) — and it shows the reader the missing ``//``.
    raise ConfigError(
      f"persona endpoint {shown!r} names no host after the scheme "
      f"(expected '{parsed.scheme}://<host>')"
    )


def read_persona_bundle(ref: str, target: PersonaInputTarget) -> PersonaBundle | None:
  """Read the persona store ONCE and return the LIVE values for a launch.

  ``None`` means *ref* is NOT a store persona — a bare agent, or a
  ``<pid>+<hid>`` with no store entry.  It is the "nothing to do" signal, and
  it is DISTINCT from a located entry that yielded nothing, which comes back
  as a bundle carrying ``reject_reason`` (fatal to a launch) or ``no_reader``
  (which must keep launching); see :class:`PersonaBundle`.

  Pure read.  No probe, no network, no write; the token file itself is never
  opened (only its pointer is resolved — usability is the launch gate's job).

  NEVER RAISES, with ONE deliberate exception: a MALFORMED *ref* raises
  :class:`~kanibako.errors.ConfigError` out of ``parse_agent_address``, exactly as
  it does for every other ref consumer.  Everything downstream of a located
  entry is fail-soft, which is what makes this safe to call from the
  credential-lifecycle paths (``stop`` / creds-watch), where a raise would
  break an unrelated operation.
  """
  entry = locate_entry(ref)
  if entry is None:
    return None
  try:
    outcome = target.read_persona_settings(entry.config_dir)
  except Exception as exc:  # noqa: BLE001 - third-party plugin, see contract
    # The Target contract says this never raises; a THIRD-PARTY plugin can
    # still break it, and this seam rides paths that must not fail closed.
    # Reported, never swallowed silently.
    return PersonaBundle(
      reject_reason=(
        f"persona store entry for '{display_agent_ref(entry.node)}': the {entry.harness} "
        f"config reader failed ({exc.__class__.__name__}: {exc})"
      )
    )
  if outcome.settings is None:
    if outcome.reject_reason is None:
      # BOTH-``None``: the harness has no persona reader (the base no-op).
      # NOT a reject — nothing was read, so nothing was refused.
      return PersonaBundle(no_reader=True)
    return PersonaBundle(reject_reason=outcome.reject_reason)
  settings = outcome.settings
  if settings.endpoint is not None:
    try:
      validate_endpoint(settings.endpoint)
    except ConfigError as exc:
      display = display_agent_ref(entry.node)
      return PersonaBundle(
        reject_reason=(
          f"persona store entry for '{display}': the {entry.harness} config "
          f"at {entry.config_dir} names an endpoint that is not well-formed — "
          f"{exc}; fix the endpoint in that config, or override it via "
          f"`kanibako system set agent.{display}.endpoint=<url>`, then retry"
        )
      )
  token = resolve_secret_path(entry)
  return PersonaBundle(
    endpoint=settings.endpoint,
    model=settings.model,
    auth_env=settings.auth_env,
    env=settings.env,
    env_dropped=settings.env_dropped,
    token_path=token.path,
    token_error=token.error,
  )
