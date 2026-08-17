"""persona-grata store discovery and resolve (no box).

The persona-grata STANDARD lays down per-persona / per-harness harness-native
config under a fixed discovery root (design SOT
``plans/persona-grata-autoimport-DESIGN.md`` §1):

    $XDG_CONFIG_HOME/personas/          <- discovery root (FIXED, not configurable)
      <pid>/                            <- persona (identity segment)
        .secret_path                    <- ONE line: the path to the token file
        <hid>/                          <- harness (e.g. codex/, claude/)
          config.toml | settings.json   <- rendered harness-NATIVE config

kanibako CONSUMES the store: a ``<pid>+<hid>`` agent ref whose store entry
exists is a *persona agent*, and its endpoint/model/token-pointer/env are
re-read from the store at EVERY launch as a LIVE cascade level — never copied
into any settings file.  Store PRESENCE decides persona-vs-plain (DESIGN §4):
everything here returns a clean "not a persona" ``None`` on a miss so callers
fall through to normal agent handling.

Two parts live here:

* **discovery + resolve** (pure reads): locate an entry and resolve its
  ``.secret_path`` token pointer.  Harness-config extraction lives on the
  Target plugins (:meth:`kanibako.targets.base.Target.read_persona_settings`).
* **the LIVE bundle** (:class:`PersonaBundle` / :func:`read_persona_bundle`):
  one read of the store rendered into the harness-neutral values ONE launch
  resolves against, threaded into ``build_launch_snapshot`` as an in-memory
  level.  Nothing here is persisted — the store is a live resolution input,
  so a launch leaves ``agents/<node>/settings.yaml`` byte-identical.

⚑ There is deliberately NO import/sync half any more.  The store used to be
copied into ``agents/<node>/settings.yaml`` by a verified swap
(``build_candidate`` / ``persist_candidate`` / ``import_persona_entry``); that
route is GONE, because the agent settings file holds USER-INTENT values only
(the file-purity invariant) and a persisted copy of a live source can only go
stale.  Do not reintroduce one.

The start-flow / create wiring itself is NOT here.  Nothing in this module
reads the TOKEN file itself (the pointer is handled arm's-length, exactly like
the ``secret_path`` keyspace category it feeds).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from urllib.parse import urlsplit

from kanibako.agent_ref import display_agent_ref, harness_of, parse_agent_ref, persona_of
from kanibako.errors import ConfigError
from kanibako.settings.paths import xdg

if TYPE_CHECKING:
    from kanibako.targets.base import Target

#: Read cap for ``.secret_path`` (bytes).  The file holds one token path
#: (PATH_MAX-ish); anything larger is a malformed store — rejected WITHOUT
#: slurping it into memory (tolerance contract: fail warnable, never OOM).
_POINTER_CAP = 16 * 1024


@dataclass(frozen=True)
class PersonaEntry:
    """A located persona-grata store entry for one ``<pid>/<hid>`` pair."""

    node: str          # canonical node-name "<pid>℘<hid>"
    persona: str       # pid (identity segment)
    harness: str       # hid (harness segment)
    persona_dir: Path  # <root>/<pid>/  (anchors relative .secret_path values)
    config_dir: Path   # <root>/<pid>/<hid>/  (the rendered harness config dir)


class SecretPathResult(NamedTuple):
    """Outcome of resolving a persona's ``.secret_path`` token pointer.

    Exactly one of the fields is set: *path* (an ABSOLUTE host path) on
    success, else *error* (a caller-warnable reason).  Tolerant by contract —
    :func:`resolve_secret_path` never raises through.
    """

    path: Path | None
    error: str | None


def persona_store_root() -> Path:
    """The persona-grata discovery root: ``$XDG_CONFIG_HOME/personas/``.

    FIXED by design (DESIGN §5a — not configurable); the single builder for
    the store path.  Reuses the spec-backed XDG resolution in
    :func:`kanibako.settings.paths.xdg` (env var honored iff set AND absolute, else
    ``$HOME/.config``).
    """
    return xdg("XDG_CONFIG_HOME", ".config") / "personas"


def locate_entry(ref: str) -> PersonaEntry | None:
    """Locate the store entry for an agent *ref*, iff one exists.

    *ref* is any accepted agent ref (``navigator+codex``, ``navigator℘codex``,
    or an already-canonical node-name); it is normalised through
    :func:`kanibako.agent_ref.parse_agent_ref` like every other ref source.

    Returns ``None`` — a clean "not a persona" — when:

    * the ref is BARE (node == harness, e.g. ``claude``): a bare agent never
      has a persona store entry;
    * the store dir ``<root>/<pid>/<hid>/`` is absent (or not a directory):
      store PRESENCE decides persona-vs-plain (DESIGN §4), so the caller falls
      through to normal agent handling.

    A malformed ref raises :class:`~kanibako.errors.ConfigError` from
    ``parse_agent_ref`` — the same contract as every other ref consumer (a bad
    ref is a user error, not a store miss).

    ⚑ PATH TRAVERSAL is handled upstream, not here.  ``.`` stopped being a legal
    segment character on 2026-08-04, so ``..+claude`` (which would have resolved
    ``<root>/../claude``, an ordinary harness config dir) and ``navigator+..``
    (``<root>/navigator/..``, the store root) now RAISE from ``parse_agent_ref``
    on the first line below rather than being screened out afterwards.  There is
    deliberately no second dot-check here: one charset, enforced in one place.
    """
    node, harness = parse_agent_ref(ref)
    if harness_of(node) == node:
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

    The pointer file holds EXACTLY ONE line — the path to the token file
    (DESIGN §2).  Resolution rules, in order:

    * expand ``$VAR`` then ``~`` (``os.path.expandvars`` →
      ``Path.expanduser`` — the same order as the launch-side
      ``_secret_pointer_usable``);
    * an ABSOLUTE result is used as-is;
    * a RELATIVE result (``./token``, bare ``token``) is anchored to the
      directory ``.secret_path`` sits in — *entry.persona_dir* — so
      ``./token`` → ``<root>/<pid>/token``.

    An absolute HOST path is required because kanibako mounts the token
    arm's-length into the box, where a relative anchor is meaningless
    (DESIGN §3).  The token file itself is NEVER opened or stat'd here — the
    result is a pointer (it resolves even when the token does not exist yet;
    usability is the launch gate's job).

    Tolerant errors (never raises through): a missing / unreadable / non-text /
    oversized / empty / whitespace-only / MULTI-line ``.secret_path``, or a
    line that cannot expand to a usable path (``~nosuchuser``, an embedded NUL)
    yields ``SecretPathResult(None, <reason>)`` for the caller to warn on.  A
    single trailing newline is not a second line.
    """
    pointer = entry.persona_dir / ".secret_path"
    try:
        with pointer.open(encoding="utf-8") as fh:
            raw = fh.read(_POINTER_CAP + 1)  # capped: one path line, never a slurp
    except FileNotFoundError:
        return SecretPathResult(None, f"no .secret_path file at {pointer}")
    except UnicodeDecodeError:
        return SecretPathResult(None, f"{pointer} is not valid UTF-8 text")
    except OSError as exc:
        return SecretPathResult(None, f"cannot read {pointer}: {exc}")
    if len(raw) > _POINTER_CAP:
        return SecretPathResult(
            None, f"{pointer} is too large to be a token path (one line expected)",
        )

    # Exactly one line: strip ONE trailing newline (LF or CRLF), then any
    # remaining newline means a real second line (the file is malformed).
    body = raw[:-1] if raw.endswith("\n") else raw
    body = body[:-1] if body.endswith("\r") else body
    if "\n" in body or "\r" in body:
        return SecretPathResult(
            None, f"{pointer} must hold exactly one line (the token path)",
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
    read, in the form the launch consumes it: the behavior scalars, the
    passthrough env block, and the RESOLVED token pointer.  It is a VALUE for
    one launch, not a cached view — nothing here is written to any file, and
    a launch that reads it leaves ``agents/<node>/settings.yaml``
    byte-identical.  :meth:`to_persona_values` renders it into the mapping
    ``build_launch_snapshot(persona_values=…)`` takes.

    * *endpoint* / *model* — the harness config's alternate base URL and model
      id (``None`` when the config names none).
    * *auth_env* — the env var the bearer token is exported as; names the
      ``secret_path.<auth_env>`` entry the token pointer lands in.
    * *env* — the passthrough env block, already stripped of the two
      single-source vars (base URL / token) by the reader.
    * *env_dropped* — NAMES the reader skipped as undeliverable (a non-string
      config value), for a caller that wants to warn.  Never silent.
    * *token_path* — the resolved ABSOLUTE host token path, or ``None`` when
      the ``.secret_path`` pointer did not resolve.
    * *token_error* — why it did not resolve (set iff *token_path* is
      ``None``); a SOFT condition — the rest of the bundle is still usable, so
      a caller warns and carries on rather than refusing the launch.
    * *reject_reason* — set iff the located entry has a harness reader that
      REFUSED its config (present but unusable, naming the cause); the bundle
      then contributes NOTHING (:meth:`to_persona_values` returns ``{}``).
    * *no_reader* — set iff the harness has NO persona reader at all (today
      goose and ``NoAgentTarget``, which inherit the base no-op).  Also
      contributes nothing, and is likewise not a complaint about any file.

    ⚑ *reject_reason* and *no_reader* mirror the two non-success arms of
    :class:`~kanibako.targets.base.PersonaReadOutcome` ONE-FOR-ONE, and keeping
    them apart is load-bearing: a launch HARD-ERRORS on *reject_reason* (a
    config the harness could read and refused), but must NOT error on
    *no_reader* — a goose persona is configured entirely through the keyspace
    and merely happens to own a store directory, so refusing it would break
    every such launch.  Both render ``{}``; only the DIAGNOSIS differs.
    """

    endpoint: str | None = None
    model: str | None = None
    auth_env: str | None = None
    # Defaults are IMMUTABLE by construction: a bare ``{}`` on a NamedTuple
    # field is ONE object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})
    env_dropped: tuple[str, ...] = ()
    token_path: Path | None = None
    token_error: str | None = None
    reject_reason: str | None = None
    no_reader: bool = False

    def to_persona_values(self) -> dict[str, str]:
        """Render this bundle as the UN-DISCRIMINATED persona-values mapping.

        The shape ``build_launch_snapshot(persona_values=…)`` takes: the bare
        behavior names ``endpoint`` / ``model``, plus ``secret_path.<auth_env>``
        and one ``env.<VAR>`` per passthrough var.  The keys are deliberately
        NOT discriminated onto an agent — the store knows a persona, not a
        cascade; ``settings_launch._persona_partial`` wraps them under the
        active agent slot.  A bundle carrying *reject_reason* — or *no_reader*
        — renders ``{}``: neither yielded values, however differently a caller
        must DIAGNOSE them.

        ⚑ EMPTINESS IS HANDLED PER VALUE CLASS, and the asymmetry is the point:

        * *endpoint* / *model* / the token path are OMITTED when absent or
          empty.  This mapping is a cascade LEVEL, so emitting ``""`` would not
          mean "unset" — it would OVERRIDE ``agent.default`` and every rung
          below with emptiness, turning a store that names no model into a
          store that names the empty model.
        * an ``env.<VAR>`` value passes through VERBATIM, empty string
          INCLUDED.  ``"FOO": ""`` in a persona config is a user deliberately
          exporting an empty var, and the reader already ruled it deliverable
          by putting it in the passthrough set rather than in *env_dropped*.
          Dropping it here would be an invisible loss of exactly the kind this
          passthrough exists to end.  (An empty var NAME is still skipped: no
          env var can be named ``""``, so it is undeliverable, not empty.)
        """
        if self.reject_reason is not None or self.no_reader:
            return {}
        values: dict[str, str] = {}
        if self.endpoint:
            values["endpoint"] = self.endpoint
        if self.model:
            values["model"] = self.model
        if self.auth_env and self.token_path is not None:
            values[f"secret_path.{self.auth_env}"] = str(self.token_path)
        for name, val in self.env.items():
            if name:
                values[f"env.{name}"] = val
        return values


#: Schemes the delivered HTTP clients (urllib probe, Node harness runtimes) can act on.
_ENDPOINT_SCHEMES = frozenset({"http", "https"})


def validate_endpoint(endpoint: str) -> None:
    """Raise :class:`~kanibako.errors.ConfigError` unless *endpoint* is a well-formed
    ``http``/``https`` base URL — validate-only, exactly like :func:`kanibako.agent_ref.parse_agent_ref`.

    Minimal well-formedness ONLY, by design: a recognised scheme (``http``/``https``,
    case-insensitive) and a non-empty host. Nothing about path, port, or query is
    checked — a persona endpoint is a base URL the harness appends its own routes to,
    and a stricter gate risks refusing a shape that works today (a false refusal here
    breaks a working box, the one outcome this check must never cause). This is the
    boundary the live incident crossed uncaught: a scheme-less endpoint
    (``myhost:8080/v1``) reads as ``urlsplit``-scheme ``"myhost"``, sails past every
    truthiness check downstream, and dies inside Node with ``Invalid URL``.
    """
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise ConfigError(
            f"persona endpoint {endpoint!r} is not a well-formed URL ({exc})"
        ) from exc
    if parsed.scheme.lower() not in _ENDPOINT_SCHEMES:
        raise ConfigError(
            f"persona endpoint {endpoint!r} has no recognised scheme "
            f"(got {parsed.scheme!r}; must start with 'http://' or 'https://')"
        )
    if not parsed.hostname:
        raise ConfigError(f"persona endpoint {endpoint!r} names no host after the scheme")


def read_persona_bundle(ref: str, target: Target) -> PersonaBundle | None:
    """Read the persona store ONCE and return the LIVE values for a launch.

    ``None`` means *ref* is NOT a store persona — a bare agent, or a
    ``<pid>+<hid>`` with no store entry (:func:`locate_entry`'s clean miss,
    store PRESENCE deciding persona-vs-plain per DESIGN §4).  It is the
    "nothing to do" signal, and it is DISTINCT from a located entry that
    yielded nothing, which comes back as a bundle in one of two shapes:

    * ``reject_reason`` — the harness READ the config and refused it; an entry
      EXISTS and the user needs to hear why it did not take (a launch treats
      this as fatal);
    * ``no_reader`` — this harness has no persona reader at ALL, so there was
      never a config to refuse.  A goose persona is keyspace-configured and may
      own a store dir purely for its ``.secret_path``; that must keep launching,
      which is exactly why this is not folded into ``reject_reason``.

    Pure read.  No probe, no network, no write; the token file itself is never
    opened (only its pointer is resolved — usability is the launch gate's job).

    NEVER RAISES, with ONE deliberate exception: a MALFORMED *ref* raises
    :class:`~kanibako.errors.ConfigError` out of ``parse_agent_ref``, exactly as
    it does for every other ref consumer (a bad ref is a user error, not a
    store miss).  Everything downstream of a successfully located entry is
    fail-soft, so this is safe to call from the credential-lifecycle paths
    (``stop`` / creds-watch), where a raise would break an unrelated operation.
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
        return PersonaBundle(reject_reason=(
            f"persona store entry for '{entry.node}': the {entry.harness} "
            f"config reader failed ({exc.__class__.__name__}: {exc})"
        ))
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
            return PersonaBundle(reject_reason=(
                f"persona store entry for '{entry.node}': the {entry.harness} config "
                f"at {entry.config_dir} names an endpoint that is not well-formed — "
                f"{exc}; fix the endpoint in that config, or override it via "
                f"`kanibako system set agent.{display}.endpoint=<url>`, then retry"
            ))
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
