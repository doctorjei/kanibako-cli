"""Unit tests for kanibako.agent_ref — the persona+harness parse grammar (rider MVP)."""

from __future__ import annotations

import pytest

from kanibako.agent_ref import (
    CANONICAL_SEP,
    PLUS_SEP,
    canonicalize_agent_ref,
    display_agent_ref,
    harness_of,
    parse_agent_ref,
    with_harness,
)
from kanibako.errors import ConfigError

# The canonical separator literal, pinned so a source-level swap of the constant
# does not silently pass the round-trip tests.
assert CANONICAL_SEP == "℘"
assert PLUS_SEP == "+"


# ---------------------------------------------------------------------------
# parse_agent_ref — bare (backward-compat)
# ---------------------------------------------------------------------------


def test_parse_bare_node_equals_harness():
    # Bare name: node == harness == the whole name (byte-identical to pre-rider).
    assert parse_agent_ref("claude") == ("claude", "claude")


def test_parse_bare_strips_whitespace():
    assert parse_agent_ref("  claude  ") == ("claude", "claude")


def test_parse_bare_allows_safe_punctuation():
    # Alnum + '-' '.' '_' are all fs/key-safe in a bare name.
    assert parse_agent_ref("no_agent") == ("no_agent", "no_agent")
    assert parse_agent_ref("agent-1.2") == ("agent-1.2", "agent-1.2")


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
    node, harness = parse_agent_ref("gemma-4.test+claude")
    assert node == "gemma-4.test℘claude"
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


def test_with_harness_rider_as_requested():
    # Rider node, target resolved as requested -> node unchanged.
    assert with_harness("navigator℘claude", "claude") == "navigator℘claude"


def test_with_harness_rider_fallback_keeps_persona():
    # Rider node, target fell back -> persona kept, harness swapped.
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
