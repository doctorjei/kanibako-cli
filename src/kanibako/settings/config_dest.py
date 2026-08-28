"""Where a config key's value LIVES — the one destination rule.

Read, write and remove must answer WHICH FILE and which nested slot identically,
or a value written by ``set`` is invisible to ``get``.

⚑ THE ROUTE IS A DESTINATION, NOT A JUDGEMENT.  Whether a key EXISTS is spec
§0's closed keyspace (:mod:`kanibako.settings.settings_keyspace`); which FAMILY a
spelling belongs to is :mod:`kanibako.settings.config_keys`.  This module
consumes both and re-implements neither.

Layering: ``config_keys`` → ``config_dest`` → ``config_interface``.
Reference: ``llm-docs/kanibako/settings/config_dest.py.md``.
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

    The REASON, not a message: the recipe decides WHETHER a route exists, each
    caller decides what to say about it.
    """

    reason: str          # "reserved" | "malformed"
    detail: str = ""     # the ConfigError text, for "malformed"


def _agent_node_route(
    node: str, tail: str, agents_root: "Path | None",
) -> "AgentFileSlot | NodeRouteRefusal | None":
    """The per-node agent file route: an :class:`AgentFileSlot` for *node*/*tail*.

    ⚑ A SLOT, NOT AN ADDRESS — never hand a caller a ``self``-rooted file address;
    :mod:`kanibako.settings.agent_file` produces the address at read/write time.
    ONE HOME for the recipe every per-node resolution repeats; the four sites that
    copied it drifted, and ``set`` wrote node refs ``get`` refused to touch.

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
    routable: ``default`` is the RESERVED any-agent tier, and a ref must parse.

    ⚑ The caller this was split out for is GONE (R-9).  The split is KEPT: the
    guard pair is the rule, and a rule spelled once cannot drift back into the
    two-of-four-steps copy.
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

    An :class:`AgentFileSlot`, an ``"Error: ..."`` string for a refused node, or
    ``None`` when it is not a persona key / *agents_root* was not supplied.

    ⚑ The node is taken VERBATIM from *canonical* and only VALIDATED here, never
    re-swapped — canonicalisation happened once, at :func:`resolve_key`.
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

    ⚑ READ-ONLY since R-9, for every verb since S3 — but the key is still declared,
    hand-authored and delivered at launch, so the read must stay.

    ⚑⚑ The tail is ``bindings.<ro|rw>.<dest>`` with the DESTINATION WHOLE, never
    split on ``.``: :mod:`kanibako.settings.agent_file` places it at exactly the
    flat ``self: bindings: <arm>:`` table the launch reads, so this read and the
    cascade's are ONE address.  When they were two, a hand-authored dotted
    destination read back "(not set)" while the launch delivered it (D-4).

    ``None`` when it is not a node bind, *agents_root* was not threaded, or the
    node is refused.
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

    A slot on the node's own settings file with the tail ``secret_path.<VAR>``, or
    ``None`` under the same conditions as :func:`_node_bind_target`.
    """
    parsed = _parse_agent_node_secret_key(canonical)
    if parsed is None:
        return None
    node, _var = parsed
    route = _agent_node_route(node, f"secret_path.{_var}", agents_root)
    return route if isinstance(route, AgentFileSlot) else None


# ---------------------------------------------------------------------------
# The FILE-scope destination rule (H2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DestRoute:
    """WHERE a config value lives: a file plus the nested slot inside it.

    ``path`` may be ``None`` — the verbs treat a missing file as "nothing stored",
    so the route stays representable rather than raising.
    """

    path: "Path | None"
    sections: tuple[str, ...]
    leaf: str

    @property
    def file(self) -> Path:
        """The destination file, for the WRITE side.

        A write always has a file; a read may legitimately have none.  The
        invariant is asserted ONCE here, not at each of the ten write sites.
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

    ⚑ THE ONE OCCURRENCE OF THIS TEST — it was written out thirteen times, doing
    silent double duty as "am I the system scope", and two copies drifted.
    """
    return settings_path if settings_path is not None else config_path


#: Which FILE rule a family follows — a per-FAMILY fact, not a per-caller option.
#: ``NOUN`` = always the noun's settings file; ``SCOPED`` = the key's own scope
#: token picks; ``CATEGORY`` = the bind-shaped families, which follow ``SCOPED``.
#:
#: ⚑⚑ CATEGORY AND SCOPED NOW PICK THE SAME FILE, AND THAT IS THE REPAIR, NOT AN
#: OVERSIGHT — the arm that distinguished them was a SILENT NO-OP write, retired
#: by DS-BL1 = (a) and deleted by QA′.  ⚑ THE TERM IS KEPT DELIBERATELY AND IS NOT
#: DEAD DATA: it is the declared FAMILY of the key, bound for the KeyKind
#: descriptor.  Do not collapse it into ``SCOPED``.
#: ⚑⚑ A FOURTH RULE, ``BOOTSTRAP``, IS RETIRED (2026-08-26) AND MUST NOT COME BACK.
#: It named the Layer-1 ``kanibako_config.yaml`` itself and had exactly ONE member,
#: ``system.setup_completed`` — a rule that existed only because the marker's STORAGE
#: (the config file) disagreed with its DECLARATION (spec §2g: a Layer-2 ``system.*``
#: SETTINGS key).  Jei closed the delta by moving the storage: the marker is written to
#: and read from ``@config.settings`` like ``system.agent``, so it routes through
#: ``_KEY_ROUTES`` under ``SCOPED`` and the class has no members left.
#: 🛑 THERE IS NO "WRITE THE CONFIG FILE" RULE AND THERE MUST NOT BE ONE: ``config.*``
#: keys LOCATE the files everything else lives in and are refused from every scope
#: (spec §2a), so no key routes to Layer 1 at all.
_NOUN, _SCOPED, _CATEGORY = "noun", "scoped", "category"


# ⚑⚑ A DESTINATION IS DATA, NOT A KEY PATH — NEVER SPLIT IT ON A DOT. The key STOPS
# at the terminal category (spec §2a); everything after it is ONE destination.
# Splitting cut ``box.caches.~/.cache/uv`` into a section ``~/`` and a leaf
# ``cache/uv``, so a hand-authored entry read back "(not set)". FIVE known sites of
# this one root cause (`509592a`, `5958572`, `dacd9b7`, here, and ``agent_file``'s
# bindings arm until S3) — do not open a sixth, and do not rebuild a per-category
# destination carrier.
# ⚑ The WHOLE-key predicate, never the suffix one (QC): a scalar leaf that merely
# ends in a category token — ``system.channels.common`` — must not have its
# siblings' path cut apart.
# ⚑ PARSING THE STRING IS THE JOB HERE, so the P4 "re-parses a string we joined
# ourselves" objection does not apply — nothing was joined; the user TYPED this key.
# Reasoning: ``llm-docs/kanibako/settings/config_dest.py.md``.
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

    The per-node agent families are NOT here — their slot depends on the node's
    file shape and is resolved by :func:`_agent_node_route`.
    """
    from kanibako.settings.config_keys import (
        agent_default_tier_leaf,
        _is_agent_setting,
        _is_path_category_key,
        _is_pref_key,
        _is_scope_bind_key,
        _is_scope_env_key,
        _is_scope_secret_key,
        _KEY_ROUTES,
        _pref_sections_leaf,
    )
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    # ⚑ NO ``SETUP_MARKER_KEY`` BRANCH ANY MORE (2026-08-26) — the marker is an ordinary
    # ``system.*`` settings key now and falls through to ``_KEY_ROUTES`` below, exactly
    # like ``system.agent``.  set/get/reset still name ONE storage location; it is the
    # SETTINGS file, and it is the one ``config.read_setup_completed`` reads.
    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return sections, leaf, _NOUN
    if _is_scope_secret_key(canonical):
        parts = canonical.split(".")  # [<scope>, "secret_path", <VAR>]
        return (parts[0], "secret_path"), parts[2], _NOUN
    if _is_scope_env_key(canonical):
        # The SIBLING of the scope secret pointer above, and ``_NOUN`` for the same
        # reason: env is a SETTINGS category with no Layer-1 config-file form.
        parts = canonical.split(".")  # [<scope>, "env", <VAR>]
        return (parts[0], "env"), parts[2], _NOUN
    if _is_agent_setting(canonical):
        return ("agent", "default"), canonical, _NOUN
    # ⚑ THE SAME SLOT, THE OTHER SPELLING — ``agent.default.<leaf>`` written out in full is
    # the any-agent tier, so it addresses the value the bare leaf above addresses, in the
    # same table of the same file. It is a SEPARATE branch rather than a widened
    # ``_is_agent_setting`` because that predicate answers "may a verb write this bare
    # spelling", and the two questions have different answers: the write verbs refuse this
    # spelling in their preamble and must keep refusing it. Claiming the slot is what stops
    # a declared, SET value reading back "(not set)" (spec §0 forbids the fabricated answer).
    # ⚑ DECLARED, not SCALAR: ``agent.default.transform_settings`` is read-only and must
    # still read.
    default_leaf = agent_default_tier_leaf(canonical)
    if default_leaf is not None:
        return ("agent", "default"), default_leaf, _NOUN
    # THREE TERMS, ONE SLOT RULE — they are one storage shape: a category tuple at
    # the key's own nested path in the scope's settings file.
    #
    # ⚑⚑ ALL THREE ARE READ-ONLY (R-9; DS-BL1 = (a)). The write verbs refuse all six
    # bind-shaped categories in their preamble — but the keys are still DECLARED and
    # still authored in YAML, so ``config get`` must keep reading what the launch
    # uses. Dropping the slot makes a hand-authored key read back "(not set)": the
    # exact get/set-asymmetry bug this rule site exists to prevent.
    # ⚑ ``is_terminal_category_key`` is the term carrying the LIVE keys, and it is
    # the WHOLE-key predicate, not the suffix one (QC). ``_is_scope_bind_key`` is a
    # RETIRED spelling kept claimed. ⚑ ``_is_path_category_key`` now answers False
    # for EVERY key; it is left in place because deleting the predicate is a separate
    # ruled follow-up (QA′) with two other callers — not a live route.
    if (
        _is_path_category_key(canonical)
        or _is_scope_bind_key(canonical)
        or is_terminal_category_key(canonical)
    ):
        # ⚑ SEGMENTS, NOT A DOTTED SPLIT (see :func:`_category_segments`): the value
        # lives at the last addressing UNIT, whatever that unit is.
        tail = _category_segments(canonical)
        return tail[:-1], tail[-1], _CATEGORY
    # ⚑ THE ROUTING TABLE IS CONSULTED WITH THE KEY AS TYPED, and that is the whole
    # point: :func:`_dest` reads the scope token off the SAME string. When a
    # spelling normaliser sat here, the slot came from the canonical key while the
    # FILE came from the typed one, so ``box_image`` slotted at ``box: image:`` in
    # the Layer-1 config file while ``box.image`` slotted identically in the
    # settings file — a spelling silently choosing the precedence tier.
    route = _KEY_ROUTES.get(canonical)
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

    *command_scope* is deliberately UNUSED for the file choice; it is threaded so
    the rule site HAS the scope (the H2 explicit-scope requirement) for the refusal
    and descriptor work downstream.

    ⚑ Do not reintroduce a per-caller destination switch (the deleted
    ``agent_scope_to_config`` flag was one): "set writes where get cannot read" is
    the exact bug class this module exists to prevent, and a flag is how it got in.
    """
    slot = _key_slot(canonical)
    if slot is None:
        return None
    sections, leaf, rule = slot
    if rule == _NOUN:
        return DestRoute(noun_settings_file(config_path, settings_path), sections, leaf)
    key_scope = canonical.split(".", 1)[0]
    from kanibako.settings.config_keys import _SETTINGS_SCOPE_TOKENS

    # ⚑⚑ THE SCOPE TOKEN IS AN ASSERTION, NOT A FORK. Every family :func:`_key_slot`
    # answers a non-NOUN rule for is scope-rooted BY CONSTRUCTION — the routing
    # table, both bind recognisers and ``is_terminal_category_key`` all require a
    # head in ``SCOPE_CONTAINMENT``. This used to fall through to the Layer-1
    # ``config_path``, which is how the flat spelling ``box_image`` (first dotted
    # token = the whole string) wrote the bootstrap floor while ``box.image`` wrote
    # the settings tier. A slotted key whose head is NOT a scope is a broken route,
    # and it must SAY SO rather than quietly pick the other file.
    assert key_scope in _SETTINGS_SCOPE_TOKENS, (
        f"'{canonical}' has a {rule} slot but '{key_scope}' is not a settings "
        f"scope; a route may not let a SPELLING choose the destination file"
    )
    return DestRoute(noun_settings_file(config_path, settings_path), sections, leaf)


def _write_dest(
    canonical: str,
    *,
    command_scope: "object | None" = None,
    config_path: "Path | None",
    settings_path: "Path | None" = None,
) -> "DestRoute | None":
    """Where ``set`` writes and ``reset`` removes a FILE-scope key.

    ⚑ BYTE-IDENTICAL TO :func:`_read_dest` since QA′ deleted the one arm they
    disagreed on, AND BOTH NAMES ARE KEPT ON PURPOSE: the AGREEMENT of the write
    route and the read route is this module's whole reason to exist, and two names
    that provably resolve the same way state that at every call site.
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

    ⚑⚑ FOR THE BARE AGENT-SCOPE TERMINAL CATEGORY KEYS THE ROUTE IS WRONG,
    MEASURABLY — the half QA′ did not touch, and BOARDED, not fixed.  It answers the
    NOUN's settings file, while the agent tier is assembled from the FLAT tables
    under ``self:`` in ``agents/<node>/agent.yaml``, so a hand-authored
    ``self.caches`` reads back "(not set)".  ⚑ Until the repoint lands, NO message
    may promise that ``config get <agent terminal key>`` works (see
    ``config_keys.agent_node_bind_retired_error``).  ⚑ S3 did NOT close this — it
    added a second route, it did not repoint this one.
    """
    return _dest(
        canonical, command_scope=command_scope, config_path=config_path,
        settings_path=settings_path,
    )
