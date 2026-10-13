"""The braced reference spelling ``{a.b.c}`` / ``{$NAME}``.

``{{`` / ``}}`` are literal braces; a brace that opens no reference is refused
(``test_strict_braces_escapes``); ``@`` and ``$`` are text, and their old reference
spellings are refused for one release (``test_bare_reference_guard``).
"""

from __future__ import annotations

import re

import pytest

from kanibako.settings.agent_config import is_self_resolving, is_unambiguous_path_value
from kanibako.settings.settings_configset import scan_tokens
from kanibako.settings.settings_expand import _is_whole_value_ref, _is_whole_value_var
from kanibako.settings.settings_resolve import (
    GUEST_HOME,
    ResolveCtx,
    SettingsError,
    expand_expr,
    expand_guest_home,
    literal_expr,
    match_braced,
    normalize_bind_dest,
)

_KEYS = {"a.b": "AB", "c": "C"}


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude", workset_name="ws", host_home="/h",
        xdg={"XDG_DATA_HOME": "/h/.local/share"}, term="xterm",
    )


def _lookup(ref: str, chain: tuple[str, ...]) -> str:
    return _KEYS[ref]


def _host(expr: str) -> str:
    return expand_expr(expr, space="host", ctx=_ctx(), lookup=_lookup)


@pytest.mark.parametrize(("expr", "kind", "name", "end"), [
    ("{a.b}", "ref", "a.b", 5),
    ("{$TERM}/x", "var", "TERM", 7),
    ("{a.b}}", "ref", "a.b", 5),
])
def test_match_braced_parses_a_reference(expr, kind, name, end) -> None:
    assert match_braced(expr, 0) == (kind, name, end)


@pytest.mark.parametrize("expr", ["{{a.b}}", "{", "{a.b", "{}", "{bad name}", "{$}", "{$1X}"])
def test_match_braced_answers_none_where_no_reference_starts(expr) -> None:
    assert match_braced(expr, 0) is None


@pytest.mark.parametrize(("expr", "expected"), [
    ("{a.b}", "AB"),
    ("x/{a.b}/y", "x/AB/y"),
    ("{$TERM}", "xterm"),
    ("{$XDG_DATA_HOME}/k", "/h/.local/share/k"),
    ("{{a.b}}", "{a.b}"),
    ("{{ {a.b} }}", "{ AB }"),
    ("@a/{c}", "@a/C"),
    ("$X-{$TERM}", "$X-xterm"),
    ("@{c}", "@C"),
    ("@{{a.b}}", "@{a.b}"),
])
def test_host_expansion_reads_the_braced_grammar(expr, expected) -> None:
    assert _host(expr) == expected


def test_a_braced_reference_keeps_the_cycle_guard() -> None:
    def cyclic(ref: str, chain: tuple[str, ...]) -> str:
        return expand_expr("{a.b}", space="host", ctx=_ctx(), lookup=cyclic, chain=chain)

    with pytest.raises(SettingsError, match="Cyclic"):
        expand_expr("{a.b}", space="host", ctx=_ctx(), lookup=cyclic)


@pytest.mark.parametrize(("expr", "residue"), [
    ("{$XDG_DATA_HOME}/k", "{$XDG_DATA_HOME}/k"),
    ("{{x}}", "{{x}}"),
    ("{a.b}/{{", "AB/{{"),
])
def test_deferral_carries_braces_for_the_box_pass(expr, residue) -> None:
    out = expand_expr(expr, space="host", ctx=_ctx(), lookup=_lookup, defer_env=True)
    assert out == residue


@pytest.mark.parametrize("text", ["/p/{a.b}/x", "/p/{{", "}", "/p/$X@y~\\z", "{$FOO}"])
def test_literal_expr_round_trips_through_the_expander(text) -> None:
    assert _host(literal_expr(text)) == text


@pytest.mark.parametrize("text", ["/p/{a.b}/x", "/p/}{", "/p/$X~\\z", "/p/a@y"])
def test_literal_expr_round_trips_through_the_box_pass(text) -> None:
    residue = literal_expr(text)
    assert expand_expr(residue, space="guest", ctx=_ctx(), lookup=_lookup) == text


def test_a_literal_at_brace_survives_both_passes() -> None:
    residue = expand_expr("@{{a.b}}", space="host", ctx=_ctx(), lookup=_lookup, defer_env=True)
    assert residue == "@{{a.b}}"
    box = expand_expr(residue, space="guest", ctx=_ctx(), lookup=_lookup)
    assert box == "@{a.b}"


def test_scan_tokens_reads_braced_names_and_skips_literal_braces() -> None:
    assert scan_tokens("{a.b}/{$TERM}/{{c}}/@d.e/{{/@{{f.g}}") == (["a.b"], ["TERM"])


@pytest.mark.parametrize(("value", "ref", "var"), [
    ("{a.b}", "a.b", None),
    ("{$COLORTERM}", None, "COLORTERM"),
    ("{a.b}/x", None, None),
    ("{{a.b}}", None, None),
])
def test_whole_value_predicates_read_the_braced_form(value, ref, var) -> None:
    assert _is_whole_value_ref(value) == ref
    assert _is_whole_value_var(value) == var


@pytest.mark.parametrize(("src", "expected"), [
    ("{a.b}/x", True), ("{$XDG_DATA_HOME}/x", True), ("{$AGENT}/x", True), ("{x", False),
])
def test_is_self_resolving_reads_the_braced_form(src, expected) -> None:
    assert is_self_resolving(src) is expected


@pytest.mark.parametrize(("value", "expected"), [
    ("{a.b}/x", True), ("{$XDG_DATA_HOME}/x", True), ("{$AGENT}/x", False), ("{x", False),
])
def test_is_unambiguous_path_value_reads_the_braced_form(value, expected) -> None:
    assert is_unambiguous_path_value(value) is expected


def test_expand_guest_home_takes_the_braced_token() -> None:
    assert expand_guest_home("{$GUEST_HOME}/.config") == GUEST_HOME + "/.config"
    with pytest.raises(SettingsError, match=re.escape("write '{$GUEST_HOME}'")):
        expand_guest_home("$GUEST_HOME/.config")


def test_normalize_bind_dest_carries_a_braced_reference_verbatim() -> None:
    assert normalize_bind_dest("/x/{a.b}/../y/") == "/x/{a.b}/../y"


def test_normalize_bind_dest_normalizes_a_dest_holding_at_or_dollar() -> None:
    # ``@`` and ``$`` are text, so only a ``{`` keeps a dest out of ``normpath``.
    assert normalize_bind_dest("/x/@a/../y/") == "/x/y"
    assert normalize_bind_dest("/x/$A/./y") == "/x/$A/y"
