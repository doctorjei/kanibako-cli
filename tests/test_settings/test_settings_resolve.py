"""Unit tests for the settings resolution engine (pure, no I/O)."""

from __future__ import annotations

import re

import pytest

from kanibako.agent_ref import CANONICAL_SEP
from kanibako.settings.settings_resolve import (
    DEFAULT_TERM,
    ExprSyntaxError,
    GUEST_HOME,
    MAX_REF_DEPTH,
    UNSET,
    LevelView,
    ResolveCtx,
    ResolvedValue,
    SettingsError,
    expand_expr,
    expand_guest_home,
    literal_expr,
    match_braced,
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
            "{$XDG_DATA_HOME}/kanibako", space="host", ctx=make_ctx(), lookup=no_lookup
        )
        == "/home/u/.local/share/kanibako"
    )


def test_expand_agent_var() -> None:
    assert expand_expr("{$AGENT}", space="host", ctx=make_ctx(), lookup=no_lookup) == "myagent"


# --------------------------------------------------------------------------- #
# {$TERM} — host-env twin of {$XDG_*}, with a LITERAL fallback and NO validation #
# --------------------------------------------------------------------------- #


def _expand_term(ctx: ResolveCtx) -> str:
    return expand_expr("{$TERM}", space="host", ctx=ctx, lookup=no_lookup)


@pytest.mark.parametrize("value", ["xterm-256color", "screen.linux"])
def test_expand_term_var_takes_the_host_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TERM", value)
    assert _expand_term(make_ctx()) == value


@pytest.mark.parametrize("set_empty", [True, False])
def test_expand_term_var_empty_or_unset_falls_back_to_xterm(
    monkeypatch: pytest.MonkeyPatch, set_empty: bool
) -> None:
    # [Jei:2026-09-08]: "if TERM is empty, we should fall back to xterm". UNSET is the same
    # nothing as EMPTY — the XDG twin reads both through one ``environ.get(var, "")``.
    if set_empty:
        monkeypatch.setenv("TERM", "")
    else:
        monkeypatch.delenv("TERM", raising=False)
    assert _expand_term(make_ctx()) == DEFAULT_TERM == "xterm"


@pytest.mark.parametrize("value", ["no-such-terminfo-entry-anywhere", "  ", "!!"])
def test_expand_term_var_set_value_is_never_validated(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A SET ``TERM`` reaches the box verbatim — ONLY an empty one falls back.

    ⚑ THIS TEST EXISTS TO STOP A FUTURE "missing symmetry" FIX. The ``{$XDG_*}`` twin ignores a
    value that fails ``isabs``; ``TERM`` has no such pure test (usability is a terminfo lookup,
    and the lookup that matters happens IN THE BOX, whose terminfo set this side cannot see).
    Adding a host-side check here is the defect, not the cure.
    """
    monkeypatch.setenv("TERM", value)
    assert _expand_term(make_ctx()) == value


def test_expand_term_var_honors_an_explicit_ctx_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    ctx = ResolveCtx(
        agent_name=None, workset_name=None, host_home=HOST_HOME, xdg={}, term="vt100"
    )
    assert _expand_term(ctx) == "vt100"


def test_expand_braced_workset_var() -> None:
    assert (
        expand_expr("{$WORKSET}/p", space="host", ctx=make_ctx(), lookup=no_lookup)
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
# expand_expr — {key}-refs
# ---------------------------------------------------------------------------


def test_expand_ref_simple() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "system.path.data"
        return "/data"

    assert (
        expand_expr(
            "{system.path.data}/crabs", space="host", ctx=make_ctx(), lookup=lookup
        )
        == "/data/crabs"
    )


def test_expand_ref_double_hop() -> None:
    # lookup for "a" itself expands "{b}"; assert the double-hop resolves.
    calls: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        calls.append(ref)
        if ref == "a":
            # Re-enter resolution for "a"'s value, threading the chain.
            return expand_expr("{b}", space="host", ctx=make_ctx(), lookup=lookup, chain=chain)
        if ref == "b":
            return "/leaf"
        raise AssertionError(ref)

    assert expand_expr("{a}", space="host", ctx=make_ctx(), lookup=lookup) == "/leaf"
    assert calls == ["a", "b"]


def test_expand_at_is_literal() -> None:
    # ``@`` is an ordinary character; ``nosuch.path`` names no declared key.
    assert (
        expand_expr("@nosuch.path.data", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "@nosuch.path.data"
    )


def test_expand_ref_ends_at_its_closing_brace() -> None:
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    assert (
        expand_expr("{a.b.c}/y", space="host", ctx=make_ctx(), lookup=lookup) == "/x/y"
    )
    assert expand_expr("{a.b.c}-y", space="host", ctx=make_ctx(), lookup=lookup) == "/x-y"
    assert seen == ["a.b.c", "a.b.c"]


def test_expand_braced_ref_with_literal_suffix() -> None:
    # THE case braces exist for (spec §2c helper-log bind): a ``.jsonl`` suffix
    # directly after a dotted ref.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/ws/logs/mybox"

    assert (
        expand_expr("{meta.box.name}.jsonl", space="host", ctx=make_ctx(), lookup=lookup)
        == "/ws/logs/mybox.jsonl"
    )
    assert seen == ["meta.box.name"]  # the suffix is NOT part of the ref name.


def test_expand_the_old_greedy_spelling_is_refused_with_the_braced_cure() -> None:
    # The old bare ``@meta.box.name.jsonl`` swallowed the suffix into the name; the
    # one-release guard refuses it and names the spelling that keeps them apart.
    with pytest.raises(SettingsError, match=r"write '\{meta\.box\.name\}\.jsonl'"):
        expand_expr("@meta.box.name.jsonl", space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_at_before_an_undeclared_name_is_text() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b.c"
        return "/root"

    assert expand_expr("@a.b.c/x", space="host", ctx=make_ctx(), lookup=lookup) == "@a.b.c/x"
    assert expand_expr("@{a.b.c}/x", space="host", ctx=make_ctx(), lookup=lookup) == "@/root/x"


def test_expand_braced_ref_admits_persona_separator() -> None:
    # ``_REF_SEG`` admits the persona node separator ``℘``, so it stays ONE ref
    # component inside braces.
    ref_name = f"meta.agent.navigator{CANONICAL_SEP}claude.auth.share_support"
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "true"

    assert (
        expand_expr("{" + ref_name + "}.x", space="host", ctx=make_ctx(), lookup=lookup)
        == "true.x"
    )
    assert seen == [ref_name]


@pytest.mark.parametrize("node", [f"navigator{CANONICAL_SEP}claude",
                                  f"kimi_k3{CANONICAL_SEP}claude"])
def test_ref_name_admits_an_underscored_node_name(node: str) -> None:
    # A node-name is a KEY SEGMENT, and the ref grammar is composed from the name
    # rule's charset (spec §0), so every node a persona/harness may spell is matched
    # whole.
    ref_name = f"meta.agent.{node}.auth.share_support"
    assert match_braced("{" + ref_name + "}", 0) == ("ref", ref_name, len(ref_name) + 2)
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "true"

    assert expand_expr("{" + ref_name + "}", space="host", ctx=make_ctx(), lookup=lookup) == "true"
    assert seen == [ref_name]


@pytest.mark.parametrize("node", ["漢字", "café", "яндекс"])
def test_a_non_ascii_character_is_no_ref_name(node: str) -> None:
    # A persona or harness name is ASCII (spec §0), and the ref grammar is composed
    # from the same charset, so a non-ASCII character opens no reference.
    assert match_braced(f"{{meta.agent.{node}{CANONICAL_SEP}claude.auth}}", 0) is None


def test_expand_brace_after_at_opens_a_braced_ref() -> None:
    # ``@`` is text, so a ``{`` after it opens a reference of its own, and
    # ``{{``/``}}`` are the literal braces.
    seen: list[str] = []

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        seen.append(ref)
        return "/x"

    assert expand_expr("@a.b{x}", space="host", ctx=make_ctx(), lookup=lookup) == "@a.b/x"
    assert expand_expr("@a.b{{x}}", space="host", ctx=make_ctx(), lookup=lookup) == "@a.b{x}"
    assert seen == ["x"]


def test_expand_at_brace_literal() -> None:
    # A literal ``@{`` doubles its brace (keyspec "References").
    assert (
        expand_expr("@{{a.b}}", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "@{a.b}"
    )
    assert (
        expand_expr("@{a.b}", space="host", ctx=make_ctx(), lookup=lambda r, c: "X")
        == "@X"
    )


def test_expand_braced_ref_in_guest_space() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b"
        return "/g"

    assert (
        expand_expr("~/x/{a.b}.log", space="guest", ctx=make_ctx(), lookup=lookup)
        == f"{GUEST_HOME}/x//g.log"
    )


def test_expand_braced_ref_expands_under_defer_env() -> None:
    # defer_env defers ENVIRONMENT tokens only; ``{key}``-refs (CONFIG) still expand.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        assert ref == "a.b"
        return "/cfg"

    assert (
        expand_expr(
            "{$XDG_STATE_HOME}/{a.b}.jsonl",
            space="host",
            ctx=make_ctx(),
            lookup=lookup,
            defer_env=True,
        )
        == "{$XDG_STATE_HOME}//cfg.jsonl"
    )


def test_expand_braced_ref_cycle_via_lookup_reentry_raises() -> None:
    # The cycle guard lives past the parse and is spelling-agnostic.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        return expand_expr(
            "{a}", space="host", ctx=make_ctx(), lookup=lookup, chain=chain
        )

    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr("{a}x", space="host", ctx=make_ctx(), lookup=lookup)


# ---------------------------------------------------------------------------
# match_braced — the shared parser's own contract (used by settings_expand +
# settings_configset, so the returned END INDEX is load-bearing, not internal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,name,end",
    [
        ("{a.b.c}", "a.b.c", 7),  # end is PAST the closing brace.
        ("{a.b.c}/x", "a.b.c", 7),
        ("{a.b.c}.jsonl", "a.b.c", 7),
        ("{a}", "a", 3),
    ],
)
def test_match_braced_name_and_end(expr: str, name: str, end: int) -> None:
    assert match_braced(expr, 0) == ("ref", name, end)


def test_match_braced_at_an_offset() -> None:
    # Callers scan left-to-right and hand it the index OF the ``{``.
    assert match_braced("pre-{a.b}-post", 4) == ("ref", "a.b", 9)


def test_the_tokenizer_reads_dollar_as_text() -> None:
    """``$`` is an ordinary character: no ``var`` token, and ``scan_tokens`` finds none."""
    from kanibako.settings.settings_configset import scan_tokens
    from kanibako.settings.settings_resolve import iter_expr_tokens

    for expr in ("$FOO", "a$FOO/x", "cost $5"):
        assert [(t.kind, t.value) for t in iter_expr_tokens(expr)] == [("text", expr)]
        assert scan_tokens(expr) == ([], [])


def test_expand_substituted_value_is_leaf() -> None:
    # A returned value containing a reference is NOT re-scanned.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        return "{$AGENT}{b}"

    assert expand_expr("{a}", space="host", ctx=make_ctx(), lookup=lookup) == "{$AGENT}{b}"


def test_expand_dollar_text_and_escaped_backslash() -> None:
    assert (
        expand_expr("$HOME\\\\x", space="host", ctx=make_ctx(), lookup=no_lookup)
        == "$HOME\\x"
    )


_LITERAL_CASES = [
    "/plain/path",
    "/x$HOME/cfg",
    "/x${FOO}/cfg",
    "/a@b/cfg",
    "/x@nosuch.key/cfg",
    "/b\\q/cfg",
    "/trailing\\",
    "~/t~/cfg",
]


@pytest.mark.parametrize("text", _LITERAL_CASES)
def test_literal_expr_expands_to_itself_host_side(text: str) -> None:
    """A host path made a :func:`literal_expr` comes back VERBATIM: no token in it is read."""
    assert expand_expr(
        literal_expr(text), space="host", ctx=make_ctx(), lookup=no_lookup,
    ) == text


@pytest.mark.parametrize("text", _LITERAL_CASES)
def test_literal_expr_has_no_refs_and_survives_deferral(text: str) -> None:
    """The deferred scan reads no reference in it, and keeps ``~ \\`` ESCAPED and braces
    DOUBLED for the later resolver (the deferral rule in :func:`expand_expr`)."""
    refs: list[str] = []

    def record(ref: str, chain: tuple[str, ...]) -> str:
        refs.append(ref)
        return ""

    deferred = expand_expr(
        literal_expr(text), space="host", ctx=make_ctx(), lookup=record, defer_env=True,
    )
    assert refs == []
    assert deferred == "".join(
        f"\\{c}" if c in "\\~" else c * 2 if c in "{}" else c for c in text
    )


# ---------------------------------------------------------------------------
# expand_expr — errors
# ---------------------------------------------------------------------------


def test_expand_unknown_var_raises() -> None:
    with pytest.raises(SettingsError, match="FOO"):
        expand_expr("{$FOO}", space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_agent_none_raises() -> None:
    with pytest.raises(SettingsError, match="AGENT"):
        expand_expr("{$AGENT}", space="host", ctx=make_ctx(agent_name=None), lookup=no_lookup)


def test_expand_workset_none_raises() -> None:
    with pytest.raises(SettingsError, match="WORKSET"):
        expand_expr(
            "{$WORKSET}", space="host", ctx=make_ctx(workset_name=None), lookup=no_lookup
        )


def test_expand_missing_xdg_raises() -> None:
    with pytest.raises(SettingsError, match="XDG_STATE_HOME"):
        expand_expr("{$XDG_STATE_HOME}", space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_direct_cycle_via_chain_raises() -> None:
    # Chain already contains the ref → cycle.
    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr(
            "{a}", space="host", ctx=make_ctx(), lookup=no_lookup, chain=("a",)
        )


def test_expand_cycle_via_lookup_reentry_raises() -> None:
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        # Re-reference the same ref → cycle caught on re-entry.
        return expand_expr("{a}", space="host", ctx=make_ctx(), lookup=lookup, chain=chain)

    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr("{a}", space="host", ctx=make_ctx(), lookup=lookup)


@pytest.mark.parametrize(
    "expr,match",
    [
        # ``@`` is text, so the brace after it is read on its own.
        ("@{", "Unpaired '{'"),
        ("@{}", "Empty reference"),
        ("@{a.b.c", "Unpaired '{'"),
        ("@{a b}", "Malformed reference"),
        ("@{a.b.}", "Malformed reference"),
        # NESTING IS NOT SUPPORTED and fails loudly: a substituted value is a leaf
        # and is never re-scanned.
        ("{a{b}}", "Malformed reference"),
        ("pre-@{a b}-post", "Malformed reference"),
    ],
)
def test_expand_malformed_braced_ref_raises(expr: str, match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        expand_expr(expr, space="host", ctx=make_ctx(), lookup=no_lookup)


def test_expand_depth_cap_raises() -> None:
    deep_chain = tuple(f"n{i}" for i in range(MAX_REF_DEPTH))
    with pytest.raises(SettingsError, match="depth cap"):
        expand_expr(
            "{fresh}", space="host", ctx=make_ctx(), lookup=no_lookup, chain=deep_chain
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


# --------------------------------------------------------------------------- #
# normalize_bind_dest — full canonicalization of a bind destination            #
# --------------------------------------------------------------------------- #


class TestADestinationIsFullyCanonicalized:
    """A bind dest is fully canonicalized.

    Before this change ``normalize_bind_dest`` only expanded ``~`` and dropped a
    trailing ``/``, so ``/opt/./kanibako/kanibako/../kanibako`` and
    ``/opt/kanibako//kanibako`` passed the internal-bind refusal and the
    peer-collision check as DIFFERENT paths from ``/opt/kanibako/kanibako``.
    """

    def test_dot_dot_under_home_is_one_destination(self) -> None:
        from kanibako.settings.settings_resolve import GUEST_HOME, normalize_bind_dest

        assert normalize_bind_dest("~/a/../b") == normalize_bind_dest("~/b") == f"{GUEST_HOME}/b"

    def test_leading_double_slash_collapses(self) -> None:
        from kanibako.settings.settings_resolve import normalize_bind_dest

        assert normalize_bind_dest("//x") == "/x"
        assert normalize_bind_dest("///x") == "/x"

    def test_dot_segment_and_inner_double_slash(self) -> None:
        from kanibako.settings.settings_resolve import normalize_bind_dest

        assert normalize_bind_dest("/a/./b") == "/a/b"
        assert normalize_bind_dest("/opt/kanibako//kanibako") == "/opt/kanibako/kanibako"

    def test_internal_bind_dest_spelled_with_dot_dot_is_refused(self) -> None:
        # Before this change: dotdot and doubleslash returned [] (the raw-text
        # comparison missed them); plain returned 1 refusal line.
        # ⚑ ``when="stored"`` is REQUIRED and has no default: these entries are ALREADY
        # IN a file, so this is the resolve's STORED-time judgment, not a verb's.
        from kanibako.settings.settings_launch import internal_bind_refusals

        plain = internal_bind_refusals(
            "box.bindings.ro", {"/opt/kanibako/kanibako": ["/x"]}, where="t.yaml", when="stored"
        )
        dotdot = internal_bind_refusals(
            "box.bindings.ro", {"/opt/./kanibako/kanibako/../kanibako": ["/x"]},
            where="t.yaml", when="stored",
        )
        doubleslash = internal_bind_refusals(
            "box.bindings.ro", {"/opt/kanibako//kanibako": ["/x"]},
            where="t.yaml", when="stored",
        )
        # All three now produce exactly one refusal line.
        assert len(plain) == 1
        assert len(dotdot) == 1
        assert len(doubleslash) == 1

    def test_mask_arm_carries_both_wordings_and_when_picks_one(self) -> None:
        """⚑ THE MASK ARM ANSWERS BOTH ``when`` VALUES, and this is the only place either
        one is pinned directly. ``share add`` cannot reach the write form — it writes
        only ``bindings``, and a mask is not a dest-keyed entry it can be handed — so a
        direct call is what keeps that arm from being untested text waiting for a verb
        that does not exist yet.

        The STORED text is pinned END-TO-END elsewhere, through the resolve rather than
        this carrier: ``tests/test_canon_delivery.py``
        ``TestInternalBindEntryRefusal::test_a_mask_above_an_internal_bind_refuses_naming_what_it_would_remove``
        drives ``_refuse_internal_bind_entries`` and asserts the same sentence. This test
        pins what that one cannot reach — the ``when="write"`` half, and the claim that
        ``when`` changes the WORDING and nothing else.
        """
        from kanibako.settings.settings_launch import internal_bind_refusals

        mask = {"/opt/kanibako": True}
        stored = internal_bind_refusals("box.masks", mask, where="t.yaml", when="stored")
        write = internal_bind_refusals("box.masks", mask, where="t.yaml", when="write")

        # ⚑ THE JUDGMENT IS IDENTICAL either way — one entry refuses, and it is the same
        # entry — so a change that made the write form refuse MORE (or less) is red here
        # even when the words still look right.
        assert len(stored) == len(write) == 1
        assert stored[0] == (
            "box.masks[/opt/kanibako] in t.yaml would remove the internal kanibako bind "
            "at /opt/kanibako/kanibako (spec §2c), which is not suppressible; mask a "
            "narrower path."
        )
        assert write[0] == (
            "box.masks[/opt/kanibako] cannot be added to t.yaml: it would remove the "
            "internal kanibako bind at /opt/kanibako/kanibako (spec §2c), which is not "
            "suppressible. Nothing was written; mask a narrower path."
        )

    def test_controls_unchanged(self) -> None:
        from kanibako.settings.settings_resolve import GUEST_HOME, normalize_bind_dest

        assert normalize_bind_dest("~") == GUEST_HOME
        assert normalize_bind_dest("~/") == GUEST_HOME
        assert normalize_bind_dest("/m/") == "/m"
        assert normalize_bind_dest("/") == "/"

    def test_tilde_expansion_with_an_at(self) -> None:
        # ``~`` is expanded when the rest of the dest holds an ``@``.
        from kanibako.settings.settings_resolve import GUEST_HOME, normalize_bind_dest

        assert normalize_bind_dest("~/x@y/") == f"{GUEST_HOME}/x@y"

    def test_ref_trailing_slash_stripped(self) -> None:
        # A reference dest still loses its trailing ``/``.
        from kanibako.settings.settings_resolve import normalize_bind_dest

        assert normalize_bind_dest("{meta.box.home}/") == "{meta.box.home}"

    def test_ref_and_var_unchanged_at_and_dollar_normalized(self) -> None:
        from kanibako.settings.settings_resolve import normalize_bind_dest

        assert normalize_bind_dest("{meta.box.home}/../x") == "{meta.box.home}/../x"
        assert normalize_bind_dest("{$XDG_DATA_HOME}/./x") == "{$XDG_DATA_HOME}/./x"
        # ``@`` and ``$`` are text: an absolute dest holding them is normalized.
        assert normalize_bind_dest("/a/@b/../x") == "/a/x"
        assert normalize_bind_dest("/a/$B/./x") == "/a/$B/x"

    def test_idempotent(self) -> None:
        from kanibako.settings.settings_resolve import normalize_bind_dest

        cases = [
            "~/a/../b",
            "~/b",
            "//x",
            "///x",
            "/a/./b",
            "/opt/kanibako//kanibako",
            "/opt/./kanibako/kanibako/../kanibako",
            "~",
            "~/",
            "/m/",
            "/",
            "{meta.box.home}/../x",
            "{$XDG_DATA_HOME}/./x",
            "/a/@b/../x",
            "///a@b",
        ]
        for c in cases:
            assert normalize_bind_dest(normalize_bind_dest(c)) == normalize_bind_dest(c), f"idempotence failed for {c!r}"


def test_the_two_unpackers_read_one_2_element_list_oppositely() -> None:
    # ⚑⚑ THE ARITY TRAP, pinned. The SAME raw value is legal to BOTH unpackers and
    # means opposite things: name-keyed ``[a, b]`` is (host, box); dest-keyed
    # ``[a, b]`` is (src, opts). Nothing may pick the unpacker by looking at the
    # value — the CALLER picks it from the node the value came from.
    raw = ["/left", "/right"]
    assert unpack_bind(raw) == ("/left", "/right", None)
    assert unpack_bind_entry(raw) == ("/left", "/right")
    # i.e. "/right" is a DESTINATION to one and MOUNT OPTIONS to the other.


# ---------------------------------------------------------------------------
# expand_guest_home — the one ``{$GUEST_HOME}`` prefix expander
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("{$GUEST_HOME}/x", f"{GUEST_HOME}/x"),
        ("{$GUEST_HOME}", GUEST_HOME),
        ("~/x", "~/x"),
        ("/etc/x", "/etc/x"),
        ("$TERM", "$TERM"),
        ("$GUEST_HOMEX/x", "$GUEST_HOMEX/x"),
        ("a/$GUEST_HOME/x", "a/$GUEST_HOME/x"),
        ("a/{$GUEST_HOME}/x", "a/{$GUEST_HOME}/x"),
        ("", ""),
    ],
)
def test_expand_guest_home_substitutes_only_a_leading_token(value: str, expected: str) -> None:
    assert expand_guest_home(value) == expected


@pytest.mark.parametrize("value", ["$GUEST_HOME/x", "$GUEST_HOME", "${GUEST_HOME}/x"])
def test_expand_guest_home_refuses_the_old_spelling_with_the_braced_cure(value: str) -> None:
    # BARE-REF-GUARD (keyspec "References"): refused for one release, naming the cure.
    with pytest.raises(ExprSyntaxError, match=re.escape("write '{$GUEST_HOME}'")):
        expand_guest_home(value)
