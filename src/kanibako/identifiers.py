"""Case-blind comparison of kanibako identifiers — **fold to compare; never fold to store.**

Box, workset and agent names are compared without regard to case, and STORED in
the spelling their author typed (``specs/settings-keyspace-1.8.0.md`` §0, the
``⚑ NAMING RULES`` bullet).  Those are two rules, not one, and code that holds
only the first drifts into folding on the way in — which is the retired
``[R171]`` cure, not this one.

🛑 **A FILESYSTEM PATH IS NEVER CASE-FOLDED.**  A site that preserves case
because it is building a path is already conforming and must not be routed
through here.  Before calling :func:`find_identifier`, say which of the two you
are looking at.

Agents are the one kind with a second spelling: :func:`agent_node_case` derives
the lowercase NODE from a declared NAME (``[R173]``).  That is a derivation, not
an entry fold, and its docstring draws the line.

This module is the ONE carrier of the comparison.  It imports nothing from the
tree on purpose: ``settings/`` reaches it, and ``settings/paths.py`` →
``settings/agent_config.py`` → ``agent_ref`` is a live chain that an owner
inside ``settings/`` would close into a cycle.  ``tests/test_identifiers.py``
pins the leaf.
"""

from __future__ import annotations

from collections.abc import Iterable


def _fold(name: str) -> str:
    """The comparison form of an identifier.

    ``casefold()`` rather than ``lower()``: agent segments admit ``\\w`` in any
    language (``agent_ref.SEGMENT_CHAR_CLASS``), and keeping ``.lower()``
    unspoken here leaves any surviving ``.lower()``-on-a-name textually
    identifiable as the retired ``[R171]`` entry fold.

    ⚑ PRIVATE, and that is the design: with no exported way to fold a single
    name, there is no reachable way to fold one HALF of a comparison.  The
    half-folded lookup (``stored.get(query.lower())``) is the bug this module
    exists to make unsayable.
    """
    return name.casefold()


def agent_node_case(name: str) -> str:
    """The NODE spelling of an agent *name* — lowercase, always (keyspec §0).

    🛑 **NOT the retired entry fold, and the difference is that an agent has TWO
    spellings while a box or a workset has one** (``[R173]``).  The NAME keeps the
    plugin's declared case and lives in VALUES (``system.agent``,
    ``meta.agent.<agent>.name``); the NODE is the KEY segment — ``agent.<agent>.*``
    and everything derived from it, the ``agents/<node>/`` store dir included — and
    the spec says it is lowercase by construction.  So this DERIVES a second value;
    it does not fold the name on the way to storage.

    ⚑ **Call it where a node is DERIVED from a name**, never as one half of a
    comparison — that is :func:`find_identifier`, which folds both sides and is the
    only reason ``_fold`` stayed private.  A node built here and a registry key
    built here are byte-equal, which is what lets the two be compared at all.

    ⚑ ``_fold`` rather than ``.lower()``: the two differ only on names no agent-ref
    grammar realistically carries, and reaching for a SECOND fold here would give
    the tree two spellings of one rule.  The keyspec's ``%tolower(…)%`` in §2d is
    table prose for the same idea, not a resolver macro (``[R176]``).
    """
    return _fold(name)


def find_identifier(name: str, candidates: Iterable[str]) -> str | None:
    """The STORED spelling of *name* among *candidates*, compared case-blind — or ``None``.

    Returns the matched CANDIDATE rather than a bool, because the caller almost
    always indexes or prints the match afterwards.  Handed a bool it would write
    ``registry[name]``, which is correct only while storage is folded; handed the
    stored spelling it holds a key that works whatever case was typed, and no
    longer holds a query that needs folding.

    Contracts the signature cannot carry:

    * **Test ``is not None``, never truthiness.**  ``""`` is a legal candidate
      and a falsy return.
    * **Ambiguity does not raise.**  A registry already holding both ``Foo`` and
      ``foo`` resolves by iteration order.  Refusing here would be a claim about
      STORAGE, which this module deliberately makes none of; the refusal belongs
      at the write that would create the second entry.
    * *candidates* is consumed once — any iterable, including a ``dict`` (whose
      iteration yields its keys, which is the usual call).
    """
    folded = _fold(name)
    for candidate in candidates:
        if _fold(candidate) == folded:
            return candidate
    return None
