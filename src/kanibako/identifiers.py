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

It is also the ONE carrier of the NAME RULE's charset (§0): every kind of name
is checked against :func:`name_flaw`, and a derived name is :func:`derived_name`.

This module is the ONE carrier of the comparison.  It imports nothing from the
tree on purpose: ``settings/`` reaches it, and ``settings/paths.py`` →
``settings/agent_config.py`` → ``agent_ref`` is a live chain that an owner
inside ``settings/`` would close into a cycle.  ``tests/test_identifiers.py``
pins the leaf.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

# ---------------------------------------------------------------------------
# THE NAME RULE (keyspec §0, ⚑ NAMING RULES) — the ONE charset definition for all
# five kinds: box, workset, agent, persona, and harness.  A name is 1–64 characters,
# each an ASCII letter, digit, or ``_``, and does not start with a digit.  The rule
# is data here; each kind words its own refusal around :func:`name_flaw`.
# ---------------------------------------------------------------------------

#: The characters a name may hold, as a regex class body.
NAME_CHAR_CLASS = "A-Za-z0-9_"
NAME_MAX_LEN = 64
_NAME_CHAR_RE = re.compile(f"[{NAME_CHAR_CLASS}]")
_NOT_NAME_CHAR_RE = re.compile(f"[^{NAME_CHAR_CLASS}]")
#: The flaw :func:`name_flaw` reports for a leading digit — the one clause a GENERATED name
#: (a standalone box's kuid prefix, a nameless box's hash) may break.
LEADING_DIGIT_FLAW = "must not start with a digit"
#: What a DERIVED name writes ``_`` for (keyspec §0): nothing else is rewritten.
_DERIVED_SEPARATORS = "-."


def name_flaw(name: str) -> str | None:
    """Why *name* breaks the name rule, as a clause (``"must not contain '-'"``), or ``None``.

    The first offending character is quoted only when it is printable ASCII; anything
    else is shown as ``U+XXXX``, so a lookalike or a combining mark cannot hide.
    """
    if not name:
        return "must not be empty"
    if len(name) > NAME_MAX_LEN:
        return f"must be at most {NAME_MAX_LEN} characters"
    for ch in name:
        if not _NAME_CHAR_RE.fullmatch(ch):
            shown = f"'{ch}'" if " " < ch < "\x7f" else f"U+{ord(ch):04X}"
            return f"must not contain {shown}"
    if name[0].isdigit():
        return LEADING_DIGIT_FLAW
    return None


def is_name(name: str) -> bool:
    """True when *name* follows the name rule."""
    return name_flaw(name) is None


def derived_name(text: str) -> str:
    """The name kanibako DERIVES from *text* (a folder name): ``_`` for each ``-`` and ``.``.

    Nothing else is rewritten, so a derived name can still break the rule (a space, a
    leading digit, non-ASCII); the caller refuses it with :func:`underscore_spelling`.
    """
    for sep in _DERIVED_SEPARATORS:
        text = text.replace(sep, "_")
    return text


def first_free_name(base: str, taken: Callable[[str], bool]) -> str:
    """*base*, else the first of ``<base>2``, ``<base>3``, ... that *taken* does not hold.

    The ONE suffix rule: the name a box derives, and the ``--name`` a rename cure offers.
    """
    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}{n}"
        n += 1
    return candidate


def underscore_spelling(name: str) -> str | None:
    """The ``_`` spelling a refusal of ASCII *name* offers as its cure, or ``None``.

    Each character outside the rule becomes ``_``, and a leading digit gains a
    leading ``_``.  ``None`` when *name* is not ASCII (its cure is the ASCII spelling,
    ``launch.romanize.to_ascii``) or when no such spelling follows the rule.
    """
    if not name or not name.isascii():
        return None
    spelled = _NOT_NAME_CHAR_RE.sub("_", name)
    if spelled[0].isdigit():
        spelled = "_" + spelled
    return spelled if is_name(spelled) else None


def _fold(name: str) -> str:
    """The comparison form of an identifier.

    ``casefold()`` rather than ``lower()``: a COMPARISON wants the widest
    equivalence there is, and names stored before the name rule went ASCII may
    still hold any letter — ``STRASSE`` and ``straße`` are one name to a user.  ⚑ That argument is about comparing, and it does NOT carry over to
    :func:`agent_node_case`, which writes a directory; its docstring says why.

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
    only reason ``_fold`` stayed private.

    🛑 **``lower()``, NOT the comparison fold, and the two are not interchangeable
    here.**  The spec spells this ``%tolower(…)%`` (§2d, ``[R176]``) and §0 says a
    node is its name *in lowercase* — a CASE change.  ``casefold`` is a wider
    equivalence that changes LETTERS: ``"Straße".lower()`` is ``"straße"`` while
    ``"Straße".casefold()`` is ``"strasse"``, so a casefolded node would spell a
    plugin's store ``agents/strasse/`` where the spec says ``agents/straße/`` — a
    different word on disk, not a different case.
    ⚑ **Nothing is lost at the lookup, and that was the argument for reusing the
    comparison fold.**  :func:`find_identifier` casefolds BOTH sides, so a key built
    here is reached by any spelling of the query, ``STRASSE`` included.  The two
    folds answer different questions and neither has to be the other.
    """
    return name.lower()


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
