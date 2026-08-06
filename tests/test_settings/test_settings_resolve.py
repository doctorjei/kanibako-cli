"""Unit tests for the settings resolution engine (pure, no I/O)."""

from __future__ import annotations

import pytest

from kanibako.agent_ref import CANONICAL_SEP
from kanibako.settings.settings_resolve import (
    GUEST_HOME,
    MAX_REF_DEPTH,
    UNSET,
    LevelView,
    ResolveCtx,
    ResolvedValue,
    SettingsError,
    expand_expr,
    match_ref,
    match_var,
    resolve_value,
    split_bind,
    unpack_bind,
    unpack_bind_entry,
)

HOST_HOME = "/home/u"


def make_ctx(
    *,
    agent_name: str | None = "myagent",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/home/u/.local/share"},
    )


def no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    raise AssertionError(f"lookup should not be called (ref={ref!r})")


# ---------------------------------------------------------------------------
# split_bind — the CLI-INPUT edge ONLY (``config set k=h:b``); the category
# load/resolve path uses the structured ``unpack_bind`` below.
# ---------------------------------------------------------------------------


def test_split_bind_simple_pair() -> None:
    assert split_bind("a:b") == ("a", "b")


def test_split_bind_paths() -> None:
    assert split_bind("/host:/guest") == ("/host", "/guest")


def test_split_bind_plain_scalar() -> None:
    assert split_bind("/just/a/path") == ("/just/a/path", None)


def test_split_bind_escaped_colon_no_split() -> None:
    # "a\:b" -> literal colon, no split.
    assert split_bind("a\\:b") == ("a:b", None)


def test_split_bind_home_halves() -> None:
    assert split_bind("~/:~/host_home") == ("~/", "~/host_home")


def test_split_bind_first_colon_only() -> None:
    assert split_bind("a:b:c") == ("a", "b:c")


def test_split_bind_escaped_then_real_colon() -> None:
    # First colon is escaped; split on the second (real) one.
    assert split_bind("a\\:b:c") == ("a:b", "c")


def test_split_bind_escaped_backslash() -> None:
    assert split_bind("a\\\\b") == ("a\\b", None)


# ---------------------------------------------------------------------------
# unpack_bind — the STRUCTURED category-path unpacker (spec §2a). A binding
# value is a 2-/3-element list/tuple, never a colon-string. The optional 3rd
# element is the per-entry mount-options override.
# ---------------------------------------------------------------------------


def test_unpack_bind_two_tuple_list() -> None:
    assert unpack_bind(["/host", "/guest"]) == ("/host", "/guest", None)


def test_unpack_bind_two_tuple_tuple() -> None:
    assert unpack_bind(("/host", "/guest")) == ("/host", "/guest", None)


def test_unpack_bind_three_tuple_captures_options() -> None:
    assert unpack_bind(["/h/sock", "~/helper.sock", "z"]) == (
        "/h/sock",
        "~/helper.sock",
        "z",
    )


def test_unpack_bind_three_tuple_empty_options_preserved() -> None:
    # An explicit empty-string options slot (e.g. "no relabel" for a live socket)
    # is preserved distinct from the 2-element "no override" (None) form.
    assert unpack_bind(["/h/s", "~/s", ""]) == ("/h/s", "~/s", "")


def test_unpack_bind_colon_in_path_is_literal() -> None:
    # The structured form has no delimiter, so a path with a literal ':' is just
    # an element — no escaping (the colon-string failure mode the spec retires).
    assert unpack_bind(["/a:b", "/g"]) == ("/a:b", "/g", None)


def test_unpack_bind_coerces_non_str_elements() -> None:
    # YAML scalars may parse as int/etc.; each element is narrowed to str.
    assert unpack_bind([1, 2]) == ("1", "2", None)


def test_unpack_bind_rejects_scalar() -> None:
    # A bare scalar (the old colon-string form) is not a valid structured value.
    with pytest.raises(SettingsError):
        unpack_bind("/host:/guest")


def test_unpack_bind_rejects_wrong_arity() -> None:
    with pytest.raises(SettingsError):
        unpack_bind(["only-one"])
    with pytest.raises(SettingsError):
        unpack_bind(["a", "b", "c", "d"])


# ---------------------------------------------------------------------------
# expand_expr — host space
# ---------------------------------------------------------------------------


def test_expand_tilde_only_host() -> None:
    assert expand_expr("~", space="host", ctx=make_ctx(), lookup=no_lookup) == HOST_HOME


def test_expand_tilde_path_host() -> None:
    assert (
        expand_expr("~/x", space="host", ctx=make_ctx(), lookup=no_lookup)
        == f"{HOST_HOME}/x"
    )


def test_expand_xdg_var() -> None:
    assert (
        expand_expr(
            "$XDG_DATA_HOME/kanibako", space="host", ctx=make_ctx(), lookup=no_lookup
        )
        == "/home/u/.local/share/kanibako"
    )


def test_expand_agent_var() -> None:
    assert expand_expr("$AGENT", space="host", ctx=make_ctx(), lookup=no_lookup) == "myagent"


def test_expand_braced_workset_var() -> None:
    assert (
        expand_expr("${WORKSET}/p", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "myws/p"
    )


def test_expand_tilde_not_first_is_literal() -> None:
    assert (
        expand_expr("/a/~/b", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "/a/~/b"
    )


# ---------------------------------------------------------------------------
# expand_expr — guest space
# ---------------------------------------------------------------------------


def test_expand_tilde_guest() -> None:
    assert (
        expand_expr("~/.claude", space="guest", ctx=make_ctx(), lookup=no_lookup)
        == f"{GUEST_HOME}/.claude"
    )


# ---------------------------------------------------------------------------
# expand_expr — @-refs
# ---------------------------------------------------------------------------


def test_expand_ref_simple() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "system.path.data"
        return "/data"

    assert (
        expand_expr(
            "@system.path.data/crabs", space="host", ctx=make_ctx(), lookup=lookup
        )
        == "/data/crabs"
    )


def test_expand_ref_double_hop() -> None:
    # lookup for "a" itself expands "@b"; assert the double-hop resolves.
    calls: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        calls.append(ref)
        if ref == "a":
            # Re-enter resolution for "a"'s value, threading the chain.
            return expand_expr("@b", space="host", ctx=make_ctx(), lookup=lookup, chain=chain)
        if ref == "b":
            return "/leaf"
        raise AssertionError(ref)

    assert expand_expr("@a", space="host", ctx=make_ctx(), lookup=lookup) == "/leaf"
    assert calls == ["a", "b"]


def test_expand_escaped_at_is_literal() -> None:
    assert (
        expand_expr("\\@system.path.data", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "@system.path.data"
    )


def test_expand_ref_ends_at_nonname_char() -> None:
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    # The dotted ref stops before "/" (and before any other non-name char).
    assert (
        expand_expr("@a.b.c/y", space="host", ctx=make_ctx(), lookup=lookup) == "/x/y"
    )
    assert seen == ["a.b.c"]


def test_expand_bare_ref_swallows_hyphen_braces_are_the_escape() -> None:
    # ⚑ ``-`` is a NAME character (a node-name may contain one — see
    # ``_REF_SEG``), so it does NOT terminate a bare ref: ``@a.b.c-y`` is the
    # single name ``a.b.c-y``.  A LITERAL ``-`` suffix after a ref is spelled
    # with the braced form, which is exactly what PHASE R's ``@{...}`` exists for.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    assert expand_expr("@a.b.c-y", space="host", ctx=make_ctx(), lookup=lookup) == "/x"
    assert seen == ["a.b.c-y"]

    seen.clear()
    assert (
        expand_expr("@{a.b.c}-y", space="host", ctx=make_ctx(), lookup=lookup) == "/x-y"
    )
    assert seen == ["a.b.c"]


# ---------------------------------------------------------------------------
# expand_expr — BRACED @{name} refs (PHASE R)
#
# ``@{<name>}`` delimits the ref name explicitly so a LITERAL SUFFIX may follow.
# The bare form is unchanged and stays the normal spelling; braces are needed
# only where the next character would otherwise be eaten by the greedy dotted
# name match. Both forms resolve identically where both are expressible.
# ---------------------------------------------------------------------------


def test_expand_braced_ref_with_literal_suffix() -> None:
    # THE case the form exists for (spec §2c helper_log): a ``.jsonl`` suffix
    # directly after a dotted ref. Bare ``@meta.box.name.jsonl`` parses as the
    # single name ``meta.box.name.jsonl`` (asserted in the companion test below),
    # swallowing the extension; the braced form keeps them separate.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/ws/logs/mybox"

    assert (
        expand_expr("@{meta.box.name}.jsonl", space="host", ctx=make_ctx(), lookup=lookup)
        == "/ws/logs/mybox.jsonl"
    )
    assert seen == ["meta.box.name"]  # the suffix is NOT part of the ref name.


def test_expand_bare_ref_still_swallows_a_dotted_suffix() -> None:
    # The limitation the braced form works around, pinned so it cannot silently
    # change: bare ``@a.b.c`` is GREEDY over dot-separated segments.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    expand_expr("@meta.box.name.jsonl", space="host", ctx=make_ctx(), lookup=lookup)
    assert seen == ["meta.box.name.jsonl"]


def test_expand_braced_and_bare_agree_where_both_expressible() -> None:
    # Where the bare form CAN express it (a ``/`` ends the name), the two
    # spellings are the same expression.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b.c"
        return "/root"

    braced = expand_expr("@{a.b.c}/x", space="host", ctx=make_ctx(), lookup=lookup)
    bare = expand_expr("@a.b.c/x", space="host", ctx=make_ctx(), lookup=lookup)
    assert braced == bare == "/root/x"


def test_expand_braced_ref_alone_equals_bare_alone() -> None:
    # Nothing following the brace → identical to the bare form (the whole-value
    # position; its 3-state behaviour is covered in test_settings_expand.py).
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b.c"
        return "/root"

    braced = expand_expr("@{a.b.c}", space="host", ctx=make_ctx(), lookup=lookup)
    bare = expand_expr("@a.b.c", space="host", ctx=make_ctx(), lookup=lookup)
    assert braced == bare == "/root"


def test_expand_braced_ref_admits_persona_separator() -> None:
    # The braced form shares ``_REF_SEG`` with the bare one, so the persona node
    # separator ``℘`` stays ONE ref component inside braces too (it is not a
    # second char class that could drift).
    ref_name = f"meta.agent.navigator{CANONICAL_SEP}claude.auth.share_support"
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "true"

    assert (
        expand_expr(
            "@{" + ref_name + "}.x", space="host", ctx=make_ctx(), lookup=lookup
        )
        == "true.x"
    )
    assert seen == [ref_name]


@pytest.mark.parametrize("node", [f"navigator{CANONICAL_SEP}claude",
                                  f"kimi-k3{CANONICAL_SEP}claude"])
def test_ref_name_admits_hyphenated_node_name(node: str) -> None:
    # ``agent_ref._SAFE_EXTRA`` admits ``-`` inside a persona/harness segment, so
    # a node-name that is a KEY SEGMENT may contain one.  The ref grammar must
    # admit it too, or ``@meta.agent.kimi-k3℘claude.auth.share_support`` truncates
    # to the (absent) name ``meta.agent.kimi`` and leaves the rest as a literal
    # suffix — silent garbage in a path key, a crash in a bool key.
    ref_name = f"meta.agent.{node}.auth.share_support"
    assert match_ref("@" + ref_name, 0) == (ref_name, len(ref_name) + 1)
    # The braced spelling shares ``_REF_SEG``, so it must agree (before the fix
    # it did not truncate — it raised "Unterminated @{...}" instead).
    assert match_ref("@{" + ref_name + "}", 0) == (ref_name, len(ref_name) + 3)


@pytest.mark.parametrize("node", [f"navigator{CANONICAL_SEP}claude",
                                  f"kimi-k3{CANONICAL_SEP}claude"])
def test_expand_ref_admits_hyphenated_node_name(node: str) -> None:
    # The whole-expression route agrees with the raw grammar, bare and braced.
    ref_name = f"meta.agent.{node}.auth.share_support"
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "true"

    bare = expand_expr("@" + ref_name, space="host", ctx=make_ctx(), lookup=lookup)
    braced = expand_expr(
        "@{" + ref_name + "}", space="host", ctx=make_ctx(), lookup=lookup
    )
    assert bare == braced == "true"
    assert seen == [ref_name, ref_name]


@pytest.mark.parametrize(
    "node",
    [
        f"漢字{CANONICAL_SEP}claude",
        f"café{CANONICAL_SEP}claude",
        f"яндекс{CANONICAL_SEP}claude",
        f"über_k3{CANONICAL_SEP}claude",
    ],
)
def test_ref_name_admits_non_ascii_node_name(node: str) -> None:
    # A persona may be named in ANY language (Jei, 2026-08-04), so a node-name —
    # which is a KEY SEGMENT — may hold non-ASCII word characters.  ``_REF_SEG``
    # is built from ``\w`` for exactly this reason: an ASCII-only class truncated
    # ``@meta.agent.漢字℘claude.auth.share_support`` to the absent name
    # ``meta.agent.`` and left the rest as a literal suffix — the SAME failure as
    # the hyphen bug (silent ``""`` in a path key, ``expected bool, got str`` in
    # a bool key).  Leftover MUST be empty.
    ref_name = f"meta.agent.{node}.auth.share_support"
    assert match_ref("@" + ref_name, 0) == (ref_name, len(ref_name) + 1)
    assert match_ref("@{" + ref_name + "}", 0) == (ref_name, len(ref_name) + 3)


@pytest.mark.parametrize(
    "node",
    [
        f"漢字{CANONICAL_SEP}claude",
        f"café{CANONICAL_SEP}claude",
        f"яндекс{CANONICAL_SEP}claude",
    ],
)
def test_expand_ref_admits_non_ascii_node_name(node: str) -> None:
    # End-to-end through the expression route: the ref resolves WHOLE, with no
    # literal remainder glued onto the resolved value.
    ref_name = f"meta.agent.{node}.auth.share_support"
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "true"

    bare = expand_expr("@" + ref_name, space="host", ctx=make_ctx(), lookup=lookup)
    braced = expand_expr(
        "@{" + ref_name + "}", space="host", ctx=make_ctx(), lookup=lookup
    )
    assert bare == braced == "true"
    assert seen == [ref_name, ref_name]


def test_expand_brace_not_after_at_is_literal() -> None:
    # A ``{`` that does NOT immediately follow ``@`` is an ordinary literal: the
    # bare ref ends at it, and the braces pass through untouched.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    assert (
        expand_expr("@a.b{x}", space="host", ctx=make_ctx(), lookup=lookup) == "/x{x}"
    )
    assert seen == ["a.b"]


def test_expand_escaped_at_brace_is_literal() -> None:
    # The escape rule is unchanged and takes precedence — this is the way to
    # write a literal ``@{`` in a value.
    assert (
        expand_expr("\\@{a.b}", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "@{a.b}"
    )


def test_expand_braced_ref_in_guest_space() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b"
        return "/g"

    assert (
        expand_expr("~/x/@{a.b}.log", space="guest", ctx=make_ctx(), lookup=lookup)
        == f"{GUEST_HOME}/x//g.log"
    )


def test_expand_braced_ref_expands_under_defer_env() -> None:
    # defer_env defers ENVIRONMENT tokens only; ``@``-refs (CONFIG) still expand,
    # in either spelling.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b"
        return "/cfg"

    assert (
        expand_expr(
            "$XDG_STATE_HOME/@{a.b}.jsonl",
            space="host",
            ctx=make_ctx(),
            lookup=lookup,
            defer_env=True,
        )
        == "$XDG_STATE_HOME//cfg.jsonl"
    )


def test_expand_braced_ref_cycle_via_lookup_reentry_raises() -> None:
    # The cycle guard lives past the parse and is spelling-agnostic.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        return expand_expr(
            "@{a}", space="host", ctx=make_ctx(), lookup=lookup, chain=chain
        )

    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr("@{a}x", space="host", ctx=make_ctx(), lookup=lookup)


# ---------------------------------------------------------------------------
# match_ref — the shared parser's own contract (used by settings_expand +
# settings_configset, so the returned END INDEX is load-bearing, not internal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,name,end",
    [
        ("@a.b.c", "a.b.c", 6),
        ("@a.b.c/x", "a.b.c", 6),  # bare name ends at the non-name char.
        ("@{a.b.c}", "a.b.c", 8),  # end is PAST the closing brace.
        ("@{a.b.c}.jsonl", "a.b.c", 8),
        ("@{a}", "a", 4),
    ],
)
def test_match_ref_name_and_end(expr: str, name: str, end: int) -> None:
    assert match_ref(expr, 0) == (name, end)


def test_match_ref_at_an_offset() -> None:
    # Callers scan left-to-right and hand it the index OF the ``@``.
    assert match_ref("pre-@{a.b}-post", 4) == ("a.b", 10)


@pytest.mark.parametrize(
    "expr,name,end",
    [
        ("$AGENT", "AGENT", 6),
        ("$XDG_DATA_HOME/x", "XDG_DATA_HOME", 14),
        ("${XDG_DATA_HOME}", "XDG_DATA_HOME", 16),  # end is PAST the brace.
        ("${A}b", "A", 4),
    ],
)
def test_match_var_name_and_end(expr: str, name: str, end: int) -> None:
    assert match_var(expr, 0) == (name, end)


def test_match_var_is_the_one_parser_for_the_dollar_family() -> None:
    """``_expand_var`` / ``_scan_var_span`` / ``_scan_tokens`` share ONE parse.

    They carried three copies of the same ten lines. Agreement on token
    BOUNDARIES is what the ``defer_env`` deferral depends on (the span re-emitted
    box-side must be exactly the span the host-side expander would have consumed),
    so it is asserted structurally rather than left to three copies happening to
    match — the same argument that made ``match_ref`` public.
    """
    from kanibako.settings.settings_configset import _scan_tokens
    from kanibako.settings.settings_resolve import _scan_var_span

    for expr in ("$AGENT", "${XDG_DATA_HOME}", "$XDG_DATA_HOME/x"):
        name, end = match_var(expr, 0)
        span, span_end = _scan_var_span(expr, 0)
        assert span_end == end  # identical boundary...
        assert span == expr[:end]  # ...and the span IS that slice.
        assert name in span
        assert _scan_tokens(expr)[1] == [name]  # ...and the same name.


@pytest.mark.parametrize(
    "expr,match",
    [("$", "Malformed"), ("${", "Malformed"), ("$/x", "Malformed"), ("${X", "Unterminated")],
)
def test_match_var_malformed_raises(expr: str, match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        match_var(expr, 0)


def test_expand_substituted_value_is_leaf() -> None:
    # A returned value containing $ / @ is NOT re-scanned.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        return "$AGENT@b"

    assert expand_expr("@a", space="host", ctx=make_ctx(), lookup=lookup) == "$AGENT@b"


def test_expand_escaped_dollar_and_backslash() -> None:
    assert (
        expand_expr("\\$HOME\\\\x", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "$HOME\\x"
    )


# ---------------------------------------------------------------------------
# expand_expr — errors
# ---------------------------------------------------------------------------


def test_expand_unknown_var_raises() -> None:
    with pytest.raises(SettingsError, match="FOO"):
        expand_expr("$FOO", space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_agent_none_raises() -> None:
    with pytest.raises(SettingsError, match="AGENT"):
        expand_expr("$AGENT", space="host", ctx=make_ctx(agent_name=None), lookup=no_lookup)


def test_expand_workset_none_raises() -> None:
    with pytest.raises(SettingsError, match="WORKSET"):
        expand_expr(
            "$WORKSET", space="host", ctx=make_ctx(workset_name=None), lookup=no_lookup
        )


def test_expand_missing_xdg_raises() -> None:
    with pytest.raises(SettingsError, match="XDG_STATE_HOME"):
        expand_expr("$XDG_STATE_HOME", space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_direct_cycle_via_chain_raises() -> None:
    # Chain already contains the ref → cycle.
    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr(
            "@a", space="host", ctx=make_ctx(), lookup=no_lookup, chain=("a",)
        )


def test_expand_cycle_via_lookup_reentry_raises() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        # Re-reference the same ref → cycle caught on re-entry.
        return expand_expr("@a", space="host", ctx=make_ctx(), lookup=lookup, chain=chain)

    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr("@a", space="host", ctx=make_ctx(), lookup=lookup)


@pytest.mark.parametrize(
    "expr,match",
    [
        # No name at all after the brace.
        ("@{", "Malformed"),
        ("@{}", "Malformed"),
        # A name that never closes — the ``${...}`` unterminated case, mirrored.
        ("@{a.b.c", "Unterminated"),
        # The name stops at the offending char and the next char is not ``}``.
        ("@{a b}", "Unterminated"),
        ("@{a.b.}", "Unterminated"),
        # NESTING IS NOT SUPPORTED and must fail loudly: a substituted value is a
        # leaf and is never re-scanned, so a nested form would imply a second,
        # contradictory model of when expansion happens.
        ("@{a@{b}}", "Unterminated"),
        # Embedded position fails the same way (one parser, one grammar).
        ("pre-@{a b}-post", "Unterminated"),
    ],
)
def test_expand_malformed_braced_ref_raises(expr: str, match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        expand_expr(expr, space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_depth_cap_raises() -> None:
    deep_chain = tuple(f"n{i}" for i in range(MAX_REF_DEPTH))
    with pytest.raises(SettingsError, match="depth cap"):
        expand_expr(
            "@fresh", space="host", ctx=make_ctx(), lookup=no_lookup, chain=deep_chain
        )


# ---------------------------------------------------------------------------
# resolve_value — precedence
# ---------------------------------------------------------------------------


def _levels(box=None, workset=None, agent=None, system=None):
    """Build [box, workset, agent, system], each (values, defaults)."""
    def lv(name, spec):
        values, defaults = spec if spec else ({}, {})
        return LevelView(name=name, values=values, defaults=defaults)

    return [
        lv("box", box),
        lv("workset", workset),
        lv("agent", agent),
        lv("system", system),
    ]


def _rv(key, levels):
    return resolve_value(key, levels=levels, ctx=make_ctx(), lookup=no_lookup)


def test_resolve_box_beats_system() -> None:
    levels = _levels(box=({"k": "boxval"}, {}), system=({"k": "sysval"}, {}))
    res = _rv("k", levels)
    assert isinstance(res, ResolvedValue)
    assert res.value == "boxval"
    assert res.level == "box"
    assert res.is_default is False
    assert res.terminal is False


def test_resolve_set_value_beats_default_across_levels() -> None:
    # system SETS the value; box DECLARES a default. Pass-1 wins over Pass-2.
    levels = _levels(box=({}, {"k": "boxdefault"}), system=({"k": "sysset"}, {}))
    res = _rv("k", levels)
    assert isinstance(res, ResolvedValue)
    assert res.value == "sysset"
    assert res.level == "system"
    assert res.is_default is False


def test_resolve_terminal_empty_at_box() -> None:
    # box="" is terminal; does NOT fall to agent default.
    levels = _levels(box=({"k": ""}, {}), agent=({}, {"k": "agentdefault"}))
    res = _rv("k", levels)
    assert isinstance(res, ResolvedValue)
    assert res.value == ""
    assert res.level == "box"
    assert res.terminal is True
    assert res.is_default is False


def test_resolve_default_when_nothing_set() -> None:
    levels = _levels(system=({}, {"k": "sysdefault"}))
    res = _rv("k", levels)
    assert isinstance(res, ResolvedValue)
    assert res.value == "sysdefault"
    assert res.level == "system"
    assert res.is_default is True


def test_resolve_absent_no_default_is_unset() -> None:
    levels = _levels()
    assert _rv("k", levels) is UNSET


def test_resolve_most_specific_default_wins() -> None:
    # Two levels declare a default, none set a value → most-specific wins.
    levels = _levels(
        workset=({}, {"k": "wsdefault"}), system=({}, {"k": "sysdefault"})
    )
    res = _rv("k", levels)
    assert isinstance(res, ResolvedValue)
    assert res.value == "wsdefault"
    assert res.level == "workset"
    assert res.is_default is True


# --------------------------------------------------------------------------- #
# unpack_bind_entry — the DEST-KEYED entry unpacker (R-3/R-6)                  #
# --------------------------------------------------------------------------- #


def test_unpack_bind_entry_one_element_defaults_options() -> None:
    # 1 element = source only; the caller falls back to the category default.
    assert unpack_bind_entry(["/host/src"]) == ("/host/src", None)
    assert unpack_bind_entry(("/host/src",)) == ("/host/src", None)


def test_unpack_bind_entry_two_elements_carry_options() -> None:
    assert unpack_bind_entry(["/host/src", "ro"]) == ("/host/src", "ro")


def test_unpack_bind_entry_narrows_scalars_to_str() -> None:
    # A YAML scalar may parse as int/etc.; both halves are narrowed.
    assert unpack_bind_entry([123, 0]) == ("123", "0")


def test_unpack_bind_entry_refuses_a_bare_scalar() -> None:
    # ⚑ The ruled shape is 1-or-2 ELEMENTS — the exact transposition of the
    # name-keyed 2-or-3 rule. A bare ``{dest: src}`` would be a SECOND spelling of
    # the 1-element entry, which is the duplicate-form confusion CONVENTIONS §0
    # opens with, so it is refused rather than quietly accepted.
    with pytest.raises(SettingsError):
        unpack_bind_entry("/host/src")


def test_unpack_bind_entry_refuses_wrong_arity() -> None:
    with pytest.raises(SettingsError) as exc:
        unpack_bind_entry([])
    assert "1 or 2" in str(exc.value)
    with pytest.raises(SettingsError) as exc:
        # A stale NAME-keyed 3-tuple handed to the dest-keyed unpacker.
        unpack_bind_entry(["/host/src", "/box/dest", "ro"])
    assert "1 or 2" in str(exc.value)
    # The message must say WHERE the destination went, or the error reads as a
    # typo rather than a shape change.
    assert "DESTINATION is the map key" in str(exc.value)


def test_the_two_unpackers_read_one_2_element_list_oppositely() -> None:
    # ⚑⚑ THE ARITY TRAP, pinned. The SAME raw value is legal to BOTH unpackers and
    # means opposite things: name-keyed ``[a, b]`` is (host, box); dest-keyed
    # ``[a, b]`` is (src, opts). Nothing may pick the unpacker by looking at the
    # value — the CALLER picks it from the node the value came from.
    raw = ["/left", "/right"]
    assert unpack_bind(raw) == ("/left", "/right", None)
    assert unpack_bind_entry(raw) == ("/left", "/right")
    # i.e. "/right" is a DESTINATION to one and MOUNT OPTIONS to the other.
