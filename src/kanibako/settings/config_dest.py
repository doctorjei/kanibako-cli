"""Where a config key's value LIVES — the one destination rule.

Every config verb has to answer the same question before it can act: given a
canonical key and the scope the command was issued at, WHICH FILE and which
nested slot does this value occupy?  Read, write and remove must answer it
identically or a value written by ``set`` is invisible to ``get`` — which is not
hypothetical: that exact divergence shipped, was found in an audit, and was
repaired by hand in `3b67e61` without removing the thirteen copies that made it
possible.  This module is where that question is answered once.

⚑ THE ROUTE IS A DESTINATION, NOT A JUDGEMENT.  Whether a key EXISTS is spec
§0's closed keyspace, owned by :mod:`kanibako.settings.settings_keyspace`; which
FAMILY a spelling belongs to is :mod:`kanibako.settings.config_keys`.  This
module consumes both and re-implements neither — it maps an already-classified
key to a path.  Keeping that line is what stops a routing layer from quietly
becoming a third opinion about what a key is.

Layering: ``config_keys`` → ``config_dest`` → ``config_interface``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import overload

from kanibako.agent_ref import parse_agent_ref
from kanibako.errors import ConfigError
from kanibako.settings.config_keys import (
    AGENT_DEFAULT_SUB,
    _parse_agent_node_secret_key,
    _parse_persona_agent_key,
    parse_agent_node_bind_key,
)


@dataclass(frozen=True)
class NodeRouteRefusal:
    """Why a per-node agent key has NO file route.

    The four call sites that resolve a node key refuse the SAME two conditions
    and then say four different things about them, because a persona ``set``
    owes the user a cure while a ``get`` of the same shape just reads back
    "(not set)".  Returning the REASON instead of a message keeps the rule in one
    place without pretending the callers want one voice: the recipe decides
    WHETHER a route exists, each caller decides what to say about it.
    """

    reason: str          # "reserved" | "malformed"
    detail: str = ""     # the ConfigError text, for "malformed"


def _agent_node_route(
    node: str, tail: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | NodeRouteRefusal | None":
    """The per-node agent file route: ``(path, sections, leaf)`` for *node*/*tail*.

    ONE HOME for the recipe every per-node key resolution repeats — the reserved
    any-agent tier refusal, the validate-only ref check, the store path, and the
    file-shape lookup.  The four sites that used to carry it copied steps two
    through four verbatim and differed only in the parse that produces *node* and
    *tail*, which is exactly the shape a rule takes just before one copy drifts:
    the inline copy in ``_set_category_value`` had already dropped BOTH guards,
    so ``set`` wrote node refs that ``get`` and ``reset`` then refused to touch.

    ``default`` is the RESERVED any-agent tier name (``read_agent_settings``: "no
    real agent may be named default") — the launch never reads an
    ``agents/default/`` dir as a node, so routing one would breach the
    keystore-maps-to-a-real-key rule and foot-gun a user who wants the any-agent
    default (that is the BARE key, e.g. ``system set model=…``).

    The node is used AS-IS for the dir and only VALIDATED here (via
    :func:`parse_agent_ref`), never re-swapped — canonicalisation happened once,
    at :func:`config_keys.resolve_key`.

    Returns ``None`` when *agents_root* was not threaded (the per-node store is
    global under ``config.agents``, so it is reachable only at the system scope).
    """
    if agents_root is None:
        return None
    refusal = check_agent_node(node)
    if refusal is not None:
        return refusal
    from kanibako.settings.agent_config import agent_file_route, agent_settings_path

    return (agent_settings_path(agents_root, node), *agent_file_route(tail, node))


def check_agent_node(node: str) -> "NodeRouteRefusal | None":
    """The GUARD PAIR every per-node route enforces, or ``None`` when *node* is
    routable.

    Split out from :func:`_agent_node_route` for the one caller that supplies its
    own destination file (the category repoint, whose node file the command
    handler already resolved) and therefore needs the guards WITHOUT the path
    lookup.  It takes the guards rather than re-deriving them, which is the
    entire point: this is the pair the inline copy dropped.
    """
    if node == AGENT_DEFAULT_SUB:
        return NodeRouteRefusal("reserved")
    try:
        parse_agent_ref(node)  # validate only (raises on a malformed ref)
    except ConfigError as exc:
        return NodeRouteRefusal("malformed", str(exc))
    return None


def _persona_agent_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | str | None":
    """Resolve a canonical persona key to its FILE write/read location.

    Returns one of:

    * ``(path, sections, leaf)`` — the route into ``agents/<node>/settings.yaml``
      (``path``), the nested file table (``("self",)`` for a flat state leaf,
      ``("self", "env")`` for an env pointer), and the leaf name;
    * an ``"Error: ..."`` string — a MALFORMED node ref (validated, never routed);
    * ``None`` — not a persona key, OR *agents_root* was not supplied (the per-
      persona store is global under ``config.agents`` and is only reachable when
      the caller threads its root — the system scope).

    The node is taken VERBATIM from *canonical* (already ``℘``-canonicalized by
    :func:`resolve_key`) and used AS-IS for the dir — it is only VALIDATED here
    (via :func:`parse_agent_ref`), never re-swapped.  So breaking the
    :func:`resolve_key` swap routes a ``+`` key to a ``agents/<node-with-+>/``
    dir the resolver never reads (the canonicalization mutation the gate proves).
    """
    parsed = _parse_persona_agent_key(canonical)
    if parsed is None:
        return None
    node, tail = parsed
    route = _agent_node_route(node, tail, agents_root)
    if isinstance(route, NodeRouteRefusal):
        if route.reason == "reserved":
            return (
                f"Error: 'default' is the reserved any-agent tier, not a persona "
                f"node; set the any-agent default with the bare key "
                f"(e.g. '{tail}') instead."
            )
        return f"Error: {route.detail}"
    return route


def _node_bind_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | None":
    """Resolve a canonical per-node DESCRIPTOR bind key
    ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0) to its FILE read/reset
    location — the get/reset symmetry twin of the set path (which routes through
    ``_set_category_value`` → ``repoint_host_src``).

    Returns ``(path, sections, leaf)`` via the file-shape SoT
    :func:`agent_config.agent_file_route`: the node's OWN settings file
    ``agents/<node>/settings.yaml`` (*path*), and the nested table the bind write
    targets — ``self.<node>.bindings.<ro|rw>.<name>`` split into ``(sections, leaf)``
    (the SAME route the set path passes to ``repoint_host_src`` as ``dest_parts``),
    so get/reset read/remove precisely where set wrote (the shape ``_agent_partial``
    reads back at launch). The node appears BOTH in the dir path AND in the nested
    key — that is the launch read shape, not a bug.

    Returns ``None`` when *canonical* is not a node bind, *agents_root* was not
    threaded (the per-node store is global under ``config.agents`` — only reachable
    at the SYSTEM scope, mirroring ``_persona_agent_target``), the node is the
    reserved any-agent tier, or the node ref is MALFORMED (validate-only via
    :func:`parse_agent_ref`, never re-swapped).
    """
    parsed = parse_agent_node_bind_key(canonical)
    if parsed is None:
        return None
    node, _cat, _name = parsed
    # ``_cat`` is the FULL ``bindings.ro`` / ``bindings.rw`` segment (not the bare
    # ``ro``/``rw``), so the tail is ``{cat}.{name}`` — no extra ``bindings.`` prefix.
    route = _agent_node_route(node, f"{_cat}.{_name}", agents_root)
    return route if isinstance(route, tuple) else None


def _node_secret_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | None":
    """Resolve a canonical ``agent.<node>.secret_path.<VAR>`` key (SECRET category)
    to its FILE write/read/reset location — the get/set/reset symmetry twin.

    Returns ``(path, sections, leaf)`` via the file-shape SoT
    :func:`agent_config.agent_file_route`: the node's OWN settings file
    ``agents/<node>/settings.yaml`` (*path*) and the DISCRIMINATED nested table
    ``self.<node>.secret_path`` (*sections*) with *leaf* = the VAR — EXACTLY the shape
    ``_agent_partial`` reads into the launch cascade and ``load_agent_config`` reads
    back into ``AgentConfig.secret_path``. The node appears BOTH in the dir path AND
    the nested key — that is the launch read shape, not a bug (same as
    ``_node_bind_target``).

    Returns ``None`` when *canonical* is not a node secret key, *agents_root* was not
    threaded (the per-node store is global under ``config.agents`` — only reachable at
    the SYSTEM scope, mirroring ``_node_bind_target``), the node is the reserved
    any-agent tier, or the node ref is MALFORMED (validate-only; never re-swapped).
    """
    parsed = _parse_agent_node_secret_key(canonical)
    if parsed is None:
        return None
    node, _var = parsed
    route = _agent_node_route(node, f"secret_path.{_var}", agents_root)
    return route if isinstance(route, tuple) else None


# ⚑ ``system.default_agent``'s four-site SPECIAL CASE is GONE (P7). The key is
# now ``system.agent`` (spec §2g) and routes like any other scope-prefixed
# settings key, through ``_KEY_ROUTES`` → the ``system:`` table of the settings
# file. The special case existed only because the old spelling was stored in the
# reserved ``agent.default`` table, a location that made it an undeclared key
# inside the AGENT tier of the real cascade.


# ---------------------------------------------------------------------------
# The FILE-scope destination rule (H2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DestRoute:
    """WHERE a config value lives: a file plus the nested slot inside it.

    ``path`` may be ``None`` when the caller supplied no file for the chosen
    arm — the verbs already treat a missing file as "nothing stored", so the
    route stays representable rather than raising.
    """

    path: "Path | None"
    sections: tuple[str, ...]
    leaf: str

    @property
    def file(self) -> Path:
        """The destination file, for the WRITE side.

        A write always has a file: the verbs that write take a required
        ``config_path``, so every arm of the rule resolves to a real path. Reads
        may legitimately have none (a scope with no settings file yet), which is
        why ``path`` is optional and this accessor exists — the invariant is
        asserted ONCE here instead of at each of the ten write sites.
        """
        assert self.path is not None, "a write route always names a file"
        return self.path


@overload
def noun_settings_file(config_path: Path, settings_path: "Path | None") -> Path: ...
@overload
def noun_settings_file(
    config_path: None, settings_path: "Path | None",
) -> "Path | None": ...
def noun_settings_file(
    config_path: "Path | None", settings_path: "Path | None",
) -> "Path | None":
    """The NOUN's settings file: *settings_path* when the noun keeps its settings
    apart from its config file (the system scope), else the noun's own file.

    ⚑ THE ONE OCCURRENCE OF THIS TEST.  It was written out thirteen times across
    the verbs, where it silently did double duty as "am I the system scope" — and
    two copies drifted into a user-reachable split between where ``set`` wrote and
    where ``get`` read (`3b67e61` re-synced them by hand and left the copies in
    place).  Here it answers only the question it can actually answer: does this
    noun keep its settings in a separate file?
    """
    return settings_path if settings_path is not None else config_path


#: Which FILE rule a family follows.  ``NOUN`` = always the noun's settings file;
#: ``SCOPED`` = the key's own scope token picks between the settings file and the
#: command's config file; ``CATEGORY`` = ``SCOPED`` plus the one arm below that is
#: deliberately broken.  This is a per-FAMILY fact, not a per-caller option: the
#: pref request, the non-agent secret pointer and the bare agent key are settings
#: by construction and have no config-file form, while a category or routed key
#: can land in either.  It reads as a field here and becomes a field on the
#: KeyKind descriptor later — the same fact, declared once.
#:
#: ⚑ CATEGORY is distinguished from SCOPED for exactly one reason: the broken
#: agent-scope destination belongs to the CATEGORY family alone.  Keying that arm
#: on the scope token by itself would silently extend it to any future routed
#: ``agent.*`` key (there are none today, so this is inert — which is precisely
#: what would make it a quiet surprise later).  The rule that is known-wrong is
#: the last one that should catch more than it names.
_NOUN, _SCOPED, _CATEGORY = "noun", "scoped", "category"


def _key_slot(canonical: str) -> "tuple[tuple[str, ...], str, str] | None":
    """``(sections, leaf, file_rule)`` for a FILE-scope key, or ``None``.

    Covers the five families whose value lives in a scope's own settings/config
    file. The per-node agent families are NOT here — their slot depends on the
    node's file shape and is resolved by :func:`_agent_node_route`. A key no
    family claims returns ``None``.
    """
    from kanibako.settings.config_keys import (
        _is_agent_setting,
        _is_path_category_key,
        _is_pref_key,
        _is_scope_secret_key,
        _KEY_ROUTES,
        _pref_sections_leaf,
        _route_key,
    )

    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return sections, leaf, _NOUN
    if _is_scope_secret_key(canonical):
        parts = canonical.split(".")  # [<scope>, "secret_path", <VAR>]
        return (parts[0], "secret_path"), parts[2], _NOUN
    if _is_agent_setting(canonical):
        return ("agent", "default"), canonical, _NOUN
    if _is_path_category_key(canonical):
        tail = canonical.split(".")
        return tuple(tail[:-1]), tail[-1], _CATEGORY
    route = _KEY_ROUTES.get(_route_key(canonical))
    if route is None:
        return None
    return route[0], route[1], _SCOPED


def _dest(
    canonical: str,
    *,
    command_scope: "object | None",
    config_path: "Path | None",
    settings_path: "Path | None",
    agent_scope_to_config: bool,
) -> "DestRoute | None":
    """The shared body of :func:`_write_dest` / :func:`_read_dest`.

    *command_scope* is accepted and deliberately unused for the file choice: the
    scope a command was issued at does not pick the file, the noun's own file
    layout does (see :func:`noun_settings_file`). It is threaded so the rule site
    HAS the scope — the H2 design's explicit-scope requirement — for the refusal
    and descriptor work that consumes this route, instead of inferring the scope
    from a path being non-``None`` the way the copies did.
    """
    slot = _key_slot(canonical)
    if slot is None:
        return None
    sections, leaf, rule = slot
    if rule == _NOUN:
        return DestRoute(noun_settings_file(config_path, settings_path), sections, leaf)
    key_scope = canonical.split(".", 1)[0]
    if agent_scope_to_config and rule == _CATEGORY and key_scope == "agent":
        return DestRoute(config_path, sections, leaf)
    from kanibako.settings.config_keys import _SETTINGS_SCOPE_TOKENS

    if key_scope in _SETTINGS_SCOPE_TOKENS:
        return DestRoute(noun_settings_file(config_path, settings_path), sections, leaf)
    return DestRoute(config_path, sections, leaf)


def _write_dest(
    canonical: str,
    *,
    command_scope: "object | None" = None,
    config_path: "Path | None",
    settings_path: "Path | None" = None,
) -> "DestRoute | None":
    """Where ``set`` writes and ``reset`` removes a FILE-scope key.

    ⚑ AGENT-SCOPE CATEGORIES GO TO A FILE NOTHING READS, ON PURPOSE.  A non-bind
    agent-scope category (``agent.<node>.common.*`` / ``caches`` / ``seeded`` /
    ``synced``) is routed by NO handler: the write lands in the command's own
    file, which is in no cascade level, so the set is a SILENT NO-OP.  That is
    the state `3b67e61` found, deliberately left alone, and recorded as its own
    future change — fixing it means routing the key to the node file, which is a
    behavior change with its own rationale to write.  Consolidating the copies
    must not smuggle that fix in, so the broken arm is reproduced here EXACTLY,
    named, and pinned by ``tests/test_settings/test_config_dest_parity.py``.
    """
    return _dest(
        canonical, command_scope=command_scope, config_path=config_path,
        settings_path=settings_path, agent_scope_to_config=True,
    )


def _read_dest(
    canonical: str,
    *,
    command_scope: "object | None" = None,
    config_path: "Path | None",
    settings_path: "Path | None" = None,
) -> "DestRoute | None":
    """Where a plain ``get`` reads a FILE-scope key: the value STORED at this noun.

    Identical to :func:`_write_dest` but for the one arm above: the read side has
    always used the noun's settings file for an agent-scope category, while the
    write side aims at the command's own file.  The two therefore disagree for
    exactly that family — which is the very asymmetry the broken destination
    consists of, so the honest consolidation keeps two functions with one shared
    body rather than one function that quietly picks a side.
    """
    return _dest(
        canonical, command_scope=command_scope, config_path=config_path,
        settings_path=settings_path, agent_scope_to_config=False,
    )
