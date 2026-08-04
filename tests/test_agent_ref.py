"""Unit tests for kanibako.agent_ref — the persona+harness parse grammar (persona MVP)."""

from __future__ import annotations

import re

import pytest

from kanibako.agent_ref import (
    CANONICAL_SEP,
    PLUS_SEP,
    _is_segment_safe,
    canonicalize_agent_ref,
    display_agent_ref,
    harness_of,
    parse_agent_ref,
    persona_of,
    with_harness,
)
from kanibako.errors import ConfigError
from kanibako.settings.settings_resolve import _REF_SEG

# The canonical separator literal, pinned so a source-level swap of the constant
# does not silently pass the round-trip tests.
assert CANONICAL_SEP == "℘"
assert PLUS_SEP == "+"


# ---------------------------------------------------------------------------
# parse_agent_ref — bare (backward-compat)
# ---------------------------------------------------------------------------


def test_parse_bare_node_equals_harness():
    # Bare name: node == harness == the whole name (byte-identical to pre-persona).
    assert parse_agent_ref("claude") == ("claude", "claude")


def test_parse_bare_strips_whitespace():
    assert parse_agent_ref("  claude  ") == ("claude", "claude")


def test_parse_bare_allows_safe_punctuation():
    # Alnum + '-' '_' are fs/key-safe in a bare name.  ⚑ '.' was legal here
    # until 2026-08-04 (this line read "agent-1.2"); see _SAFE_EXTRA for why it
    # is not — it is the settings key-path separator.
    assert parse_agent_ref("no_agent") == ("no_agent", "no_agent")
    assert parse_agent_ref("agent-1_2") == ("agent-1_2", "agent-1_2")


# ---------------------------------------------------------------------------
# parse_agent_ref — composite (both separators)
# ---------------------------------------------------------------------------


def test_parse_plus_separator():
    node, harness = parse_agent_ref("navigator+claude")
    assert node == "navigator℘claude"
    assert harness == "claude"


def test_parse_canonical_separator():
    node, harness = parse_agent_ref("navigator℘claude")
    assert node == "navigator℘claude"
    assert harness == "claude"


def test_parse_plus_and_canonical_yield_same_node():
    # Either separator on input canonicalises to the SAME node-name.
    assert parse_agent_ref("navigator+claude")[0] == parse_agent_ref(
        "navigator℘claude"
    )[0]


def test_parse_persona_with_safe_punctuation():
    # ⚑ Was "gemma-4.test+claude" until 2026-08-04; '.' is no longer a legal
    # segment character (it is the key-path separator — see _SAFE_EXTRA).
    node, harness = parse_agent_ref("gemma-4_test+claude")
    assert node == "gemma-4_test℘claude"
    assert harness == "claude"


# ---------------------------------------------------------------------------
# parse_agent_ref — malformed (non-vacuous negative asserts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "   ",  # whitespace-only
        "+claude",  # empty persona
        "navigator+",  # empty harness
        "a+b+c",  # second separator lands in the harness segment
        "nav℘a℘b",  # double canonical separator
        "nav+cla ude",  # whitespace inside a segment
        "nav/igator+claude",  # path separator in persona
        "persona+harn/ess",  # path separator in harness
        "na$me+claude",  # illegal shell/fs char
    ],
)
def test_parse_malformed_raises(bad):
    with pytest.raises(ConfigError):
        parse_agent_ref(bad)


def test_parse_non_string_raises():
    with pytest.raises(ConfigError):
        parse_agent_ref(None)  # type: ignore[arg-type]


def test_parse_valid_does_not_raise():
    # Mutation guard: prove the malformed set above is not vacuously passing —
    # a well-formed ref MUST NOT raise.
    assert parse_agent_ref("navigator+claude") == ("navigator℘claude", "claude")


# ---------------------------------------------------------------------------
# Segment charset — the SUBSET invariant (this has broken twice; pin it)
#
# A node-name is a KEY SEGMENT (``agent.<node>.…``, ``meta.agent.<node>.…``) and
# an on-disk directory name.  Every character this module admits must therefore
# also be matchable by ``settings_resolve._REF_SEG``, or an ``@``-ref naming the
# node truncates mid-name: silent ``""`` garbage at the bind-path sites, a hard
# ``expected bool, got str`` at the auth site.
# ---------------------------------------------------------------------------

_REF_SEG_RE = re.compile(_REF_SEG)


def _admitted_chars() -> set[str]:
    """Every codepoint ``agent_ref`` admits in a persona/harness segment.

    Derived by EXHAUSTIVE sweep of the module's own predicate — not from a
    hand-typed literal — so the invariant below cannot rot when the predicate
    changes.
    """
    return {chr(cp) for cp in range(0x110000) if _is_segment_safe(chr(cp))}


def test_segment_charset_is_subset_of_ref_seg():
    # THE invariant.  Both sides are read out of the two modules' own
    # definitions; nothing here is a re-listed character literal.
    admitted = _admitted_chars()
    assert admitted, "predicate admits nothing — the sweep is vacuous"
    offenders = sorted(ch for ch in admitted if not _REF_SEG_RE.fullmatch(ch))
    # ⚑ Report a BOUNDED sample: the pre-hardening offender set was ~131k
    # characters (every non-ASCII alnum in Unicode) and dumping it produced a
    # 1 MB assertion message.
    assert not offenders, (
        f"agent_ref admits {len(offenders)} character(s) that "
        f"settings_resolve._REF_SEG ({_REF_SEG}) cannot match, e.g. "
        f"{offenders[:20]!r} — a node-name containing one truncates every "
        f"@-ref that names it"
    )


def test_segment_charset_admits_word_chars_in_any_language():
    # ⚑ Unicode letters and digits are DELIBERATELY legal (Jei, 2026-08-04:
    # "we dont want to disallow unicode any other languages").  When a name
    # character truncated an ``@``-ref, the remedy was to WIDEN the ref grammar
    # to match — never to narrow this one to ASCII.
    admitted = _admitted_chars()
    for ch in "漢字éßяعกひらがな½②":
        assert ch in admitted


def test_word_char_class_equals_isalnum_plus_underscore():
    # The load-bearing PROPERTY OF PYTHON that makes the two charsets compose:
    # ``\w`` (what ``_REF_SEG`` is built from) matches exactly ``str.isalnum()``
    # (what ``_is_segment_safe`` tests) plus ``_``.  Swept exhaustively — if a
    # future Python ever diverges, this fails here instead of silently
    # re-opening the truncation bug.
    word = re.compile(r"\w")
    mismatched = [
        cp
        for cp in range(0x110000)
        if (chr(cp).isalnum() or chr(cp) == "_") != bool(word.fullmatch(chr(cp)))
    ]
    assert mismatched == []


def test_segment_charset_excludes_the_separator():
    # The subset is STRICT and directional: ``℘`` is in ``_REF_SEG`` (it joins
    # the two halves of a node) but must never be admitted INSIDE a half.
    assert not _is_segment_safe(CANONICAL_SEP)
    assert _REF_SEG_RE.fullmatch(CANONICAL_SEP)


# ---------------------------------------------------------------------------
# Segment charset — the DOT rejection (the 2026-08-04 hardening)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "foo.bar+claude",  # dotted persona: AMBIGUOUS with a nested key path
        "claude+foo.bar",  # dotted harness: same
        "foo.bar",  # dotted BARE name: same
        "..",  # path traversal — agents/../
        ".",  # agents/./
        "..+claude",  # traversal in the persona half
        ".+claude",
        "navigator+..",  # traversal in the harness half
        "a.b℘claude",  # a dotted node in canonical spelling is refused too
    ],
)
def test_parse_rejects_dot_in_a_segment(bad):
    # ``.`` is the ref-name SEGMENT separator, so a dotted node-name is
    # indistinguishable from a genuine nested key path — ``agent.a.b℘claude.model``
    # can be read as agent ``a.b℘claude`` or as a nested ``a`` -> ``b℘claude``.
    # Denying it at the parser is the only place that ambiguity can be resolved.
    with pytest.raises(ConfigError):
        parse_agent_ref(bad)


@pytest.mark.parametrize(
    "good,expected",
    [
        ("navigator+claude", ("navigator℘claude", "claude")),
        ("kimi-k3+claude", ("kimi-k3℘claude", "claude")),  # the dash stays legal
        ("kimi_k3+claude", ("kimi_k3℘claude", "claude")),
        ("claude", ("claude", "claude")),  # bare: LOAD-BEARING back-compat
        ("no_agent", ("no_agent", "no_agent")),
        ("gemma-4+claude", ("gemma-4℘claude", "claude")),
        # Non-ASCII word characters are a MUST-WORK case, not a rejection.
        ("漢字+claude", ("漢字℘claude", "claude")),
        ("café+claude", ("café℘claude", "claude")),
        ("яндекс+claude", ("яндекс℘claude", "claude")),
        ("über_k3+claude", ("über_k3℘claude", "claude")),
        ("café", ("café", "café")),  # bare, non-ASCII
    ],
)
def test_parse_still_accepts_safe_refs(good, expected):
    # Companion to the rejection set: prove the hardening did not over-reach.
    # ⚑ The BARE rows are the backward-compat path (module docstring) — pinned.
    assert parse_agent_ref(good) == expected


# ---------------------------------------------------------------------------
# harness_of
# ---------------------------------------------------------------------------


def test_harness_of_bare():
    assert harness_of("claude") == "claude"


def test_harness_of_node():
    assert harness_of("navigator℘claude") == "claude"


def test_harness_of_only_splits_canonical():
    # harness_of works on NODE-names (canonical ℘). A stray '+' is NOT a separator
    # here (nodes are always canonicalised before reaching harness_of).
    assert harness_of("navigator+claude") == "navigator+claude"


def test_harness_of_multi_segment_takes_rightmost():
    # Defensive: rpartition takes the part right of the LAST ℘.
    assert harness_of("a℘b℘claude") == "claude"


# ---------------------------------------------------------------------------
# canonicalize_agent_ref  (round-trip with parse)
# ---------------------------------------------------------------------------


def test_canonicalize_plus_to_canonical():
    assert canonicalize_agent_ref("navigator+claude") == "navigator℘claude"


def test_canonicalize_bare_unchanged():
    assert canonicalize_agent_ref("claude") == "claude"


def test_canonicalize_idempotent():
    once = canonicalize_agent_ref("navigator+claude")
    assert canonicalize_agent_ref(once) == once


def test_canonicalize_accepts_canonical_literal():
    # A ref already using the ℘ literal round-trips unchanged.
    assert canonicalize_agent_ref("navigator℘claude") == "navigator℘claude"


def test_canonicalize_matches_parse_node():
    assert canonicalize_agent_ref("navigator+claude") == parse_agent_ref(
        "navigator+claude"
    )[0]


def test_canonicalize_malformed_raises():
    with pytest.raises(ConfigError):
        canonicalize_agent_ref("navigator+")


# ---------------------------------------------------------------------------
# with_harness  (swap the resolved target into the node; fallback-safe)
# ---------------------------------------------------------------------------


def test_with_harness_bare_as_requested():
    # Bare node, target resolved as requested -> unchanged.
    assert with_harness("claude", "claude") == "claude"


def test_with_harness_bare_fallback():
    # Bare node, target fell back (e.g. NoAgent) -> the fallback name.
    assert with_harness("claude", "no_agent") == "no_agent"


def test_with_harness_persona_as_requested():
    # Persona node, target resolved as requested -> node unchanged.
    assert with_harness("navigator℘claude", "claude") == "navigator℘claude"


def test_with_harness_persona_fallback_keeps_persona_name():
    # Persona node, target fell back -> persona name kept, harness swapped.
    assert with_harness("navigator℘claude", "no_agent") == "navigator℘no_agent"


# ---------------------------------------------------------------------------
# display_agent_ref  (presentation inverse; ℘ -> +)
# ---------------------------------------------------------------------------


def test_display_swaps_canonical_to_plus():
    assert display_agent_ref("navigator℘claude") == "navigator+claude"


def test_display_bare_unchanged():
    # Bare names have no ℘ — display is byte-identical to existing output.
    assert display_agent_ref("claude") == "claude"


def test_display_round_trips_with_canonicalize():
    node = canonicalize_agent_ref("navigator+claude")
    assert display_agent_ref(node) == "navigator+claude"
    # And back: display -> canonicalize returns the node.
    assert canonicalize_agent_ref(display_agent_ref(node)) == node


# ---------------------------------------------------------------------------
# persona_of  (inverse of harness_of; the segment LEFT of ℘)
# ---------------------------------------------------------------------------


def test_persona_of_node():
    assert persona_of("navigator℘claude") == "navigator"


def test_persona_of_bare_returns_node():
    # A bare node has no distinct persona segment.
    assert persona_of("claude") == "claude"


def test_persona_of_other_harness():
    assert persona_of("navigator℘goose") == "navigator"


def test_persona_of_only_splits_canonical():
    # Like harness_of, persona_of operates on canonical NODE-names; a stray '+'
    # is NOT a separator here (node-names are canonicalised before reaching it).
    assert persona_of("navigator+claude") == "navigator+claude"


def test_persona_of_inverse_of_harness_of():
    # persona_of / harness_of partition a persona node either side of ℘.
    node = "navigator℘claude"
    assert f"{persona_of(node)}{CANONICAL_SEP}{harness_of(node)}" == node
