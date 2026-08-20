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
from kanibako.settings.agent_file import AgentFileSlot, slot_for
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
) -> "AgentFileSlot | NodeRouteRefusal | None":
    """The per-node agent file route: an :class:`AgentFileSlot` for *node*/*tail*.

    ⚑ A SLOT, NOT AN ADDRESS.  It used to hand back ``(path, sections, leaf)``, which put a
    ``self``-rooted file address in every caller's hands — internal traffic in a FILE-SURFACE
    alias (spec §0, ``self`` … a FILE-SURFACE ALIAS substituting at the parse boundary).  The
    slot carries the node and the key TAIL; the address is produced
    inside :mod:`kanibako.settings.agent_file` when the value is actually read or written.

    ONE HOME for the recipe every per-node key resolution repeats — the reserved
    any-agent tier refusal, the validate-only ref check, the store path, and the
    file-shape lookup.  The four sites that used to carry it copied steps two
    through four verbatim and differed only in the parse that produces *node* and
    *tail*, which is exactly the shape a rule takes just before one copy drifts:
    the inline copy in ``_set_category_value`` had already dropped BOTH guards,
    so ``set`` wrote node refs that ``get`` and ``reset`` then refused to touch.
    (That inline copy is gone with the bind write route it served — R-9.)

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
    return slot_for(agents_root, node, tail)


def check_agent_node(node: str) -> "NodeRouteRefusal | None":
    """The GUARD PAIR every per-node route enforces, or ``None`` when *node* is
    routable.

    Split out from :func:`_agent_node_route` for a caller that supplied its own
    destination file — the ``agent.<node>.bindings.*`` category repoint, whose node
    file the command handler had already resolved — and therefore needed the guards
    WITHOUT the path lookup.  ⚑ That caller is GONE: R-9 retired the bind CLI write
    route, and the refusal now runs before any node is parsed.  The split is kept
    because the guard PAIR is the rule, and a rule spelled once cannot drift back
    into the two-of-four-steps copy that let ``set`` write node refs ``get`` and
    ``reset`` then refused to touch.
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
) -> "AgentFileSlot | str | None":
    """Resolve a canonical persona key to its FILE write/read location.

    Returns one of:

    * an :class:`AgentFileSlot` — the node's ``agents/<node>/settings.yaml`` plus the key TAIL
      (``model`` for a flat state leaf, ``env.<VAR>`` for an env pointer).  Where inside the file
      that lands is the boundary's business, not this module's;
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
) -> "AgentFileSlot | None":
    """Resolve a canonical per-node DESCRIPTOR bind key
    ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0) to its FILE READ location.

    ⚑ READ-ONLY since R-9. It was the get/reset twin of a ``config set`` repoint;
    that write route is retired and the verbs refuse the key by name, so the ONE
    caller left is ``config_interface.get_config_value``. The read survives because
    the key does: still declared, still hand-authored in this very file, still
    delivered at launch — and hand-editing it is the cure the refusal prescribes.
    ⚑ THE CLAIM IS TRUE OF EVERY VERB SINCE S3: the ``agent`` noun had its own writer and no
    gate, so ``agent set claude bindings.ro.x=…`` was a live write route past this one. It now
    takes the SAME retirement refusal, from the same recogniser.

    Returns an :class:`AgentFileSlot` on the node's OWN settings file
    ``agents/<node>/settings.yaml``, carrying the tail ``bindings.<ro|rw>.<dest>``.
    ⚑⚑ :mod:`kanibako.settings.agent_file` places it at EXACTLY the table the launch reads —
    ``self: bindings: <arm>:``, flat, with the DESTINATION whole (S2 flattened the read, S3 the
    address rule). The read this function serves and the read the cascade performs are therefore
    one address; before S3 they were two, and a hand-authored dotted destination read back
    "(not set)" from here while the launch delivered it (D-4).

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
    return route if isinstance(route, AgentFileSlot) else None


def _node_secret_target(
    canonical: str, agents_root: "Path | None",
) -> "AgentFileSlot | None":
    """Resolve a canonical ``agent.<node>.secret_path.<VAR>`` key (SECRET category)
    to its FILE write/read/reset location — the get/set/reset symmetry twin.

    Returns an :class:`AgentFileSlot` on the node's OWN settings file
    ``agents/<node>/settings.yaml``, carrying the tail ``secret_path.<VAR>``.
    :mod:`kanibako.settings.agent_file` places it at EXACTLY the table
    ``_agent_partial`` reads into the launch cascade and ``agent_file.load`` reads
    back into ``AgentConfig.secret_path``.

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
    return route if isinstance(route, AgentFileSlot) else None


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
#: command's config file; ``CATEGORY`` = the bind-shaped category families, which
#: follow the ``SCOPED`` rule.  This is a per-FAMILY fact, not a per-caller option:
#: the pref request, the non-agent secret pointer, the non-agent env var and the
#: bare agent key are settings
#: by construction and have no config-file form, while a category or routed key
#: can land in either.  It reads as a field here and becomes a field on the
#: KeyKind descriptor later — the same fact, declared once.
#:
#: ⚑⚑ CATEGORY AND SCOPED NOW PICK THE SAME FILE, AND THAT IS THE REPAIR, NOT AN
#: OVERSIGHT.  ``CATEGORY`` was distinguished for exactly one reason: it carried the
#: deliberately-broken agent-scope WRITE arm (an ``agent.<node>.<category>`` set
#: aimed at the command's own config file, which is in no cascade level — a SILENT
#: NO-OP write).  DS-BL1 = (a) retired the category write route, leaving the arm
#: unreachable from every verb, and QA′ (2026-08-08, on Jei's word) deleted it.
#:
#: ⚑ THE TERM IS KEPT ANYWAY, DELIBERATELY, AND IT IS NOT DEAD DATA.  ``_key_slot``
#: still answers ``CATEGORY`` for every TERMINAL category key at every scope and for
#: every FILE-scope per-entry spelling — it is the declared FAMILY of the key, which
#: is the fact this triple exists to carry into the KeyKind descriptor.  What it no
#: longer does is change the destination.  Collapsing it into ``SCOPED`` would throw
#: away a family distinction to save a string compare that no longer happens.
_NOUN, _SCOPED, _CATEGORY = "noun", "scoped", "category"


# ⚑⚑ A DESTINATION IS DATA, NOT A KEY PATH — the fourth known site of one root cause
# (`509592a`, `5958572`, `dacd9b7`), and NOT the last: a FIFTH lived in the per-agent file's
# own address rule (``agent_file``'s bindings arm did ``tail.split(".")``) until S3 replaced it
# with a partition rule. Splitting a per-entry spelling on
# ``.`` cut ``box.caches.~/.cache/uv`` into a section ``~/`` and a leaf ``cache/uv``,
# so the read landed on a slot no file has and a hand-authored entry read back
# "(not set)" — which is what made ``config_keys.scope_bind_retired_error``'s closing
# promise ("reading it back still works") false for exactly the destinations users
# have, now that destinations are guest-side paths.
#
# ⚑ PARSING THE STRING IS THE JOB HERE, and that is why the P4 objection that kept
# this predicate OUT of ``settings_categories``' derivations (`509592a`: it
# "re-parses a string we joined ourselves") does not apply. There we HELD the
# segments and threw them away by joining; here the input is a canonical key the
# user TYPED at the CLI, so nothing was joined and there is nothing to carry.
# ⚑ The key STOPS at the terminal category (spec §2a) and everything after it is ONE
# destination. The WHOLE-key predicate, never the suffix one (QC): a scalar leaf
# that merely ends in a category token — ``system.channels.common`` — must not have
# its siblings' path cut apart.
def _category_segments(canonical: str) -> tuple[str, ...]:
    """*canonical*'s addressing segments, with a per-entry DESTINATION kept WHOLE."""
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    parts = canonical.split(".")
    for cut in range(2, len(parts)):
        if is_terminal_category_key(".".join(parts[:cut])):
            return (*parts[:cut], ".".join(parts[cut:]))
    return tuple(parts)


def _key_slot(canonical: str) -> "tuple[tuple[str, ...], str, str] | None":
    """``(sections, leaf, file_rule)`` for a FILE-scope key, or ``None``.

    Covers the six families whose value lives in a scope's own settings/config
    file. The per-node agent families are NOT here — their slot depends on the
    node's file shape and is resolved by :func:`_agent_node_route`. A key no
    family claims returns ``None``.
    """
    from kanibako.settings.config_keys import (
        _is_agent_setting,
        _is_path_category_key,
        _is_pref_key,
        _is_scope_bind_key,
        _is_scope_env_key,
        _is_scope_secret_key,
        _KEY_ROUTES,
        _pref_sections_leaf,
        _route_key,
    )
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return sections, leaf, _NOUN
    if _is_scope_secret_key(canonical):
        parts = canonical.split(".")  # [<scope>, "secret_path", <VAR>]
        return (parts[0], "secret_path"), parts[2], _NOUN
    if _is_scope_env_key(canonical):
        # <scope>.env.<VAR> — the SIBLING of the scope secret pointer above: a
        # scalar in the noun's settings file at ``<scope>.env.<VAR>``, the shape
        # ``settings_assemble._file_partial`` reads into the cascade and
        # ``settings_launch._emit_scope_node`` emits as a ``category="env"``
        # entry.  ``_NOUN`` for the same reason: env is a SETTINGS category and
        # has no Layer-1 config-file form.
        parts = canonical.split(".")  # [<scope>, "env", <VAR>]
        return (parts[0], "env"), parts[2], _NOUN
    if _is_agent_setting(canonical):
        return ("agent", "default"), canonical, _NOUN
    # The two arms share ONE slot rule because they are one storage shape: a
    # category tuple at the nested dotted path in the scope's settings file.
    #
    # ⚑⚑ BOTH ARMS ARE NOW READ-ONLY. R-9 retired the CLI *write* route for
    # ``{system,workset,box}.bindings.{ro,rw}.<name>``; DS-BL1 = (a) (2026-08-07g)
    # retired it for ``caches`` / ``seeded`` / ``common`` / ``synced`` at every scope
    # as well. The write verbs refuse all six in their preamble before any
    # destination is resolved — but the keys are still DECLARED and still authored in
    # YAML, so ``config get`` must keep reading the value the launch actually uses.
    # Dropping the slot instead would make a hand-authored key read back "(not set)":
    # a silent lie, and the exact get/set-asymmetry class of bug this rule site
    # exists to prevent.
    # ⚑ CONSEQUENCE FOR :data:`_CATEGORY`, MEASURED: with no category WRITE left, no
    # ``_CATEGORY`` slot can ever reach :func:`_write_dest`, so the deliberately
    # broken agent-scope arm below is now UNREACHABLE — see its note there.
    #
    # ⚑⚑ THREE TERMS, AND THE THIRD IS THE ONE THAT CARRIES THE LIVE KEYS
    # (2026-08-08c). The slot RULE is the same for all of them — the value lives at
    # the key's own nested path — which is why one branch serves three questions:
    #
    # * ``is_terminal_category_key`` — the DECLARED keys: ``<scope>.masks``,
    #   ``<scope>.bindings.{ro,rw}`` and ``<scope>.{caches,seeded,common,synced}``,
    #   each holding a whole dest-keyed map. This term is what pays the debt the
    #   terminalization opened: since P6 a hand-authored ``box.bindings.ro`` read
    #   back "(not set)" because no term claimed the BARE key, and the four
    #   would have joined it here. A declared key must be readable (spec §0).
    #   ⚑ THE WHOLE-KEY PREDICATE, NOT THE SUFFIX ONE (QC). The suffix test claimed
    #   ``system.channels.common`` and ``workset.channels.common`` — CHANNEL
    #   type-roots, ordinary path SCALARS — while their siblings ``…channels.chat``
    #   / ``…channels.share`` fell through to ``_KEY_ROUTES``, so one family read by
    #   two rules. MEASURED, both keep their read: ``system.channels.*`` is a
    #   STRUCTURAL system path and ``get_config_value`` reads it from the config
    #   file before this rule is consulted (which is why ``chat`` already read
    #   fine with no slot at all), and ``workset.channels.common`` has a
    #   ``_KEY_ROUTES`` entry giving the IDENTICAL ``(sections, leaf)`` — only the
    #   family label changes, and CATEGORY and SCOPED pick the same file.
    # * ``_is_scope_bind_key`` — the RETIRED per-name FILE-scope spelling, kept
    #   claimed so the read lands somewhere explicable rather than falling to the
    #   unknown-key table.
    # * ``_is_path_category_key`` — ⚑ now answers False for EVERY key: it is
    #   ``BIND_KEY_RE``, whose non-terminal complement emptied in this same pass.
    #   The term is left in place rather than quietly dropped because deleting the
    #   predicate is a separate, ruled follow-up (QA′) with two other callers; it
    #   is named here so a reader does not take it for a live route.
    if (
        _is_path_category_key(canonical)
        or _is_scope_bind_key(canonical)
        or is_terminal_category_key(canonical)
    ):
        # ⚑ SEGMENTS, NOT A DOTTED SPLIT — a per-entry destination stays one segment
        # (see :func:`_category_segments`). For a TERMINAL key there is no entry and
        # the segments are the key's own, so one rule serves both: the value lives at
        # the last addressing unit, whatever that unit is.
        tail = _category_segments(canonical)
        return tail[:-1], tail[-1], _CATEGORY
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
) -> "DestRoute | None":
    """The destination rule — the shared body of :func:`_write_dest` /
    :func:`_read_dest`.

    *command_scope* is accepted and deliberately unused for the file choice: the
    scope a command was issued at does not pick the file, the noun's own file
    layout does (see :func:`noun_settings_file`). It is threaded so the rule site
    HAS the scope — the H2 design's explicit-scope requirement — for the refusal
    and descriptor work that consumes this route, instead of inferring the scope
    from a path being non-``None`` the way the copies did.

    ⚑ THERE IS NO ``agent_scope_to_config`` PARAMETER ANY MORE, AND ITS ABSENCE IS
    THE POINT: read and write now answer IDENTICALLY for every key. The flag was
    the deliberately-broken agent-scope category WRITE arm (see the note on
    :data:`_CATEGORY`), deleted in QA′ once DS-BL1 = (a) had made it unreachable
    from every verb. Do not reintroduce a per-caller destination switch here —
    "set writes where get cannot read" is the exact bug class this module exists
    to prevent, and a flag is how it got in.
    """
    slot = _key_slot(canonical)
    if slot is None:
        return None
    sections, leaf, rule = slot
    if rule == _NOUN:
        return DestRoute(noun_settings_file(config_path, settings_path), sections, leaf)
    key_scope = canonical.split(".", 1)[0]
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

    ⚑⚑ THE KNOWN-BROKEN AGENT-SCOPE CATEGORY ARM IS GONE (QA′, 2026-08-08).  It
    aimed a non-bind agent-scope category (``agent.<node>.{common,caches,seeded,
    synced}``) at the command's own file, which is in no cascade level, so the set
    was a SILENT NO-OP: the state `3b67e61` found, deliberately left alone and named
    rather than smuggled a fix into.  It died by its ROUTE being retired, not by
    being repaired — DS-BL1 = (a) retired the category write route, after which NO
    ``_CATEGORY`` slot could reach this function (MEASURED end-to-end: ``set`` and
    ``reset`` fall through to the routing table and answer "unknown config key" for
    every agent-scope terminal category key, and all six bind-shaped categories are
    refused BY NAME in the verb preamble at the file scopes).  With no reachable
    caller the arm was deleted rather than left as a flag that documents a bug.

    ⚑ SO THIS IS NOW BYTE-IDENTICAL TO :func:`_read_dest`, AND BOTH NAMES ARE KEPT
    ON PURPOSE.  Agreement between the write route and the read route is this
    module's whole reason to exist; two names that provably resolve the same way
    state that at every call site.  Merging them is a naming decision, not a
    behavioural one, and it is a separate pass.
    """
    return _dest(
        canonical, command_scope=command_scope, config_path=config_path,
        settings_path=settings_path,
    )


def _read_dest(
    canonical: str,
    *,
    command_scope: "object | None" = None,
    config_path: "Path | None",
    settings_path: "Path | None" = None,
) -> "DestRoute | None":
    """Where a plain ``get`` reads a FILE-scope key: the value STORED at this noun.

    ⚑ IDENTICAL TO :func:`_write_dest` SINCE QA′ — the one arm they disagreed on is
    deleted.  The read side had always used the noun's settings file for an
    agent-scope category while the write side aimed at the command's own file; that
    asymmetry WAS the broken destination, and removing the write half is what closed
    it.  The two names are kept because the AGREEMENT is the contract (see
    :func:`_write_dest`).

    ⚑ WHAT REACHES THE AGENT-SCOPE CATEGORY ROUTE NOW IS THE TERMINAL KEY, NOT AN
    ENTRY.  It used to be
    read for ``config get agent.<node>.common.<name>``; the 2026-08-08c shape flip
    made that spelling not a key at all (``_is_path_category_key`` answers False for
    every key), so the only agent-scope category keys that still route here are the
    bare terminal ones — ``agent.<node>.{caches,seeded,common,synced}``,
    ``agent.<node>.bindings.{ro,rw}``, ``agent.<node>.masks``.

    ⚑⚑ AND FOR THOSE THE ROUTE IS WRONG, MEASURABLY — THIS IS THE HALF QA′ DID NOT
    TOUCH.  It answers the NOUN's settings file, while the agent tier is assembled
    from ``agents/<node>/settings.yaml``'s FLAT category tables directly under
    ``self:`` (``settings_assemble._agent_partial``; the S2 flatten, and a nested
    ``self.<node>`` table is now REFUSED by name).  So a hand-authored ``self.caches``
    reads back "(not set)" while a stray ``agent.claude.caches`` in the system
    settings file reads back instead.  Re-pointing it is a STORAGE-SHAPE change that
    moves ``agent_file``'s address rule — the per-agent file-shape SoT shared
    with the ``agent`` noun's own verbs — and is a separately-boarded pass.  ⚑ Until
    it lands, NO message may promise that ``config get <agent terminal key>`` works
    (see ``config_keys.agent_node_bind_retired_error``).  ⚑ S3 did NOT close this: it
    gave the ``agent`` NOUN's own verbs the boundary read (``agent get <node> caches``
    answers off the agent file), which is a second route to the same value, not a
    repoint of this one.
    """
    return _dest(
        canonical, command_scope=command_scope, config_path=config_path,
        settings_path=settings_path,
    )
