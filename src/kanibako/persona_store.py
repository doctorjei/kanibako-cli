"""persona-grata store discovery and resolve (no box).

kanibako CONSUMES the persona-grata store at
``$XDG_CONFIG_HOME/personas/<pid>/<hid>/``: a ``<pid>+<hid>`` agent ref whose
store entry exists is a *persona agent*, and its endpoint/model/token-pointer/env
are re-read from the store at EVERY launch as a LIVE cascade level — never
copied into any settings file, so a launch leaves ``agents/<node>/agent.yaml``
byte-identical.  Store PRESENCE decides persona-vs-plain, so everything here
returns a clean "not a persona" ``None`` on a miss.

⚑ There is deliberately NO import/sync half any more.  The old copy-into-
``agents/<node>/agent.yaml`` route (``build_candidate`` /
``persist_candidate`` / ``import_persona_entry``) is GONE: that file holds
USER-INTENT values only, and a persisted copy of a live source can only go
stale.  Do not reintroduce one.

Nothing here reads the TOKEN file itself — only its pointer, arm's-length,
exactly like the ``secret_path`` keyspace category it feeds.

Design SOT ``designs/persona-grata-autoimport-DESIGN.md``; the layout, the two
parts of the module and the field-by-field notes are in
``llm-docs/kanibako/persona_store.py.md``.
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

#: Read cap for ``.secret_path`` (bytes).  Anything larger is a malformed store,
#: rejected WITHOUT slurping it into memory: fail warnable, never OOM.
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

    Exactly one field is set: *path* (ABSOLUTE host path) on success, else
    *error* — :func:`resolve_secret_path` never raises through.
    """

    path: Path | None
    error: str | None


def persona_store_root() -> Path:
    """The persona-grata discovery root: ``$XDG_CONFIG_HOME/personas/``.

    FIXED by design (DESIGN §5a — not configurable); the single builder for the
    store path, over the spec-backed :func:`kanibako.settings.paths.xdg`.
    """
    return xdg("XDG_CONFIG_HOME", ".config") / "personas"


def locate_entry(ref: str) -> PersonaEntry | None:
    """Locate the store entry for an agent *ref*, iff one exists.

    ``None`` is a clean "not a persona": the ref is BARE (node == harness), or
    the store dir ``<root>/<pid>/<hid>/`` is absent — store PRESENCE decides
    persona-vs-plain (DESIGN §4), so the caller falls through to normal agent
    handling.  A MALFORMED ref instead raises
    :class:`~kanibako.errors.ConfigError` from ``parse_agent_ref``: a bad ref is
    a user error, not a store miss.

    ⚑ PATH TRAVERSAL is handled upstream, not here.  ``.`` stopped being a legal
    segment character on 2026-08-04, so ``..+claude`` and ``navigator+..`` RAISE
    from ``parse_agent_ref`` on the first line below.  There is deliberately no
    second dot-check here: one charset, enforced in one place (llm-doc).
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

    endpoint: str | None = None   # the harness config's alternate base URL
    model: str | None = None      # the harness config's model id
    auth_env: str | None = None   # env var the token exports as; NAMES the secret_path.<auth_env>
    # Defaults are IMMUTABLE by construction: a bare ``{}`` on a NamedTuple
    # field is ONE object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})  # passthrough block, base-URL/token stripped
    env_dropped: tuple[str, ...] = ()  # names SKIPPED as undeliverable — never silent
    token_path: Path | None = None     # resolved ABSOLUTE host token path
    token_error: str | None = None     # why it did not resolve (set iff token_path is None)
    reject_reason: str | None = None   # the harness READ this config and REFUSED it
    no_reader: bool = False            # harness has NO persona reader (goose, NoAgentTarget)

    def to_persona_values(self) -> dict[str, str]:
        """Render this bundle as the UN-DISCRIMINATED persona-values mapping.

        The shape ``build_launch_snapshot(persona_values=…)`` takes.  The keys
        are deliberately NOT discriminated onto an agent — the store knows a
        persona, not a cascade; ``settings_launch._persona_partial`` wraps them
        under the active agent slot.  *reject_reason* and *no_reader* both
        render ``{}``.

        ⚑ EMPTINESS IS HANDLED PER VALUE CLASS, and the asymmetry is the point:

        * *endpoint* / *model* / the token path are OMITTED when absent or
          empty.  This mapping is a cascade LEVEL, so emitting ``""`` would not
          mean "unset" — it would OVERRIDE ``agent.default`` and every rung
          below with emptiness.
        * an ``env.<VAR>`` value passes through VERBATIM, empty string
          INCLUDED: the reader already ruled it deliverable by keeping it out of
          *env_dropped*, so dropping it here would be an invisible loss.  (An
          empty var NAME is still skipped — undeliverable, not empty.)
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
    ``http``/``https`` base URL — validate-only, like :func:`kanibako.agent_ref.parse_agent_ref`.

    Minimal well-formedness ONLY, by design: a recognised scheme and a non-empty host,
    nothing about path/port/query.  A persona endpoint is a base URL the harness appends
    its own routes to, so a stricter gate risks a FALSE REFUSAL — breaking a working box,
    the one outcome this check must never cause.  ⚑ The uncaught live incident it exists
    for: ``myhost:8080/v1`` reads as ``urlsplit``-scheme ``"myhost"``, sails past every
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
    ``<pid>+<hid>`` with no store entry.  It is the "nothing to do" signal, and
    it is DISTINCT from a located entry that yielded nothing, which comes back
    as a bundle carrying ``reject_reason`` (fatal to a launch) or ``no_reader``
    (which must keep launching); see :class:`PersonaBundle`.

    Pure read.  No probe, no network, no write; the token file itself is never
    opened (only its pointer is resolved — usability is the launch gate's job).

    NEVER RAISES, with ONE deliberate exception: a MALFORMED *ref* raises
    :class:`~kanibako.errors.ConfigError` out of ``parse_agent_ref``, exactly as
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
