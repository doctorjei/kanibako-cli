"""Unit tests for the settings resolution engine (pure, no I/O)."""

from __future__ import annotations

import pytest

from kanibako.settings_resolve import (
    GUEST_HOME,
    MAX_REF_DEPTH,
    UNSET,
    LevelView,
    ResolveCtx,
    ResolvedValue,
    SettingsError,
    expand_expr,
    resolve_value,
    split_bind,
)

HOST_HOME = "/home/u"


def make_ctx(
    *,
    crab_name: str | None = "mycrab",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        crab_name=crab_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/home/u/.local/share"},
    )


def no_lookup(ref: str, chain: tuple[str, ...]) -> str:
    raise AssertionError(f"lookup should not be called (ref={ref!r})")


# ---------------------------------------------------------------------------
# split_bind
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


def test_expand_crab_var() -> None:
    assert expand_expr("$CRAB", space="host", ctx=make_ctx(), lookup=no_lookup) == "mycrab"


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

    # The dotted ref stops before "/"; "-y" too.
    assert (
        expand_expr("@a.b.c-y", space="host", ctx=make_ctx(), lookup=lookup) == "/x-y"
    )
    assert seen == ["a.b.c"]


def test_expand_substituted_value_is_leaf() -> None:
    # A returned value containing $ / @ is NOT re-scanned.
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        return "$CRAB@b"

    assert expand_expr("@a", space="host", ctx=make_ctx(), lookup=lookup) == "$CRAB@b"


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


def test_expand_crab_none_raises() -> None:
    with pytest.raises(SettingsError, match="CRAB"):
        expand_expr("$CRAB", space="host", ctx=make_ctx(crab_name=None), lookup=no_lookup)


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


def test_expand_depth_cap_raises() -> None:
    deep_chain = tuple(f"n{i}" for i in range(MAX_REF_DEPTH))
    with pytest.raises(SettingsError, match="depth cap"):
        expand_expr(
            "@fresh", space="host", ctx=make_ctx(), lookup=no_lookup, chain=deep_chain
        )


# ---------------------------------------------------------------------------
# resolve_value — precedence
# ---------------------------------------------------------------------------


def _levels(box=None, workset=None, crab=None, system=None):
    """Build [box, workset, crab, system], each (values, defaults)."""
    def lv(name, spec):
        values, defaults = spec if spec else ({}, {})
        return LevelView(name=name, values=values, defaults=defaults)

    return [
        lv("box", box),
        lv("workset", workset),
        lv("crab", crab),
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
    # box="" is terminal; does NOT fall to crab default.
    levels = _levels(box=({"k": ""}, {}), crab=({}, {"k": "crabdefault"}))
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
