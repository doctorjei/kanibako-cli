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
    unpack_bind,
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

    # The dotted ref stops before "/"; "-y" too.
    assert (
        expand_expr("@a.b.c-y", space="host", ctx=make_ctx(), lookup=lookup) == "/x-y"
    )
    assert seen == ["a.b.c"]


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
