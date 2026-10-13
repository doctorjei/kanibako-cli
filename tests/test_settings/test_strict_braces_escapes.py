"""Strict braces and escapes (keyspec § 0 "References"; braced-refs step 5, B1a).

"{{" is a literal "{" and "}}" a literal "}" everywhere; an unpaired brace, an empty or
malformed reference, and a backslash before anything but ``\\``, ``~`` (and, until the
sigils retire, ``@``/``$``) are refused by name. A trailing lone backslash stays literal.
The expander and the set-time scan read ONE tokenizer, so both refuse the same values.
"""

from __future__ import annotations

import pytest

from kanibako.settings.agent_config import is_self_resolving, is_unambiguous_path_value
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_configset import Error, scan_tokens, validate_config_set
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError, expand_expr

_KEYS = {"a.b": "x_x"}


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude", workset_name="ws", host_home="/h",
        xdg={"XDG_DATA_HOME": "/h/.local/share"}, term="xterm",
    )


def _lookup(ref: str, chain: tuple[str, ...]) -> str:
    return _KEYS[ref]


def _host(expr: str, *, defer: bool = False) -> str:
    return expand_expr(expr, space="host", ctx=_ctx(), lookup=_lookup, defer_env=defer)


# The refused values, each with the words its refusal must carry: what is wrong, and
# for an unpaired brace the doubled spelling that cures it.
_REFUSED = [
    ("a{b", ["Unpaired '{'", "'{{'"]),
    ("{", ["Unpaired '{'", "'{{'"]),
    ("x/{a.b", ["Unpaired '{'", "'{{'"]),
    ("a}b", ["Unpaired '}'", "'}}'"]),
    ("}", ["Unpaired '}'", "'}}'"]),
    ("{a.b}}", ["Unpaired '}'", "'}}'"]),
    ("{{a.b}", ["Unpaired '}'", "'}}'"]),
    ("{}", ["Empty reference '{}'"]),
    ("x/{}/y", ["Empty reference '{}'"]),
    ("{bad name}", ["Malformed reference '{bad name}'"]),
    ("{$}", ["Malformed reference '{$}'"]),
    ("{a.b.}", ["Malformed reference '{a.b.}'"]),
    ("\\x", ["Illegal escape '\\x'"]),
    ("\\/abs", ["Illegal escape '\\/'"]),
    ("a\\:b", ["Illegal escape '\\:'"]),
    ("\\{a.b}", ["Illegal escape '\\{'"]),
    ("\\}", ["Illegal escape '\\}'"]),
    ("a\\ b", ["Illegal escape '\\ '"]),
]


@pytest.mark.parametrize("defer", [False, True])
@pytest.mark.parametrize(("expr", "words"), _REFUSED)
def test_the_expander_refuses(expr, words, defer) -> None:
    with pytest.raises(SettingsError) as ei:
        _host(expr, defer=defer)
    for word in words:
        assert word in str(ei.value), (expr, str(ei.value))


@pytest.mark.parametrize(("expr", "words"), _REFUSED)
def test_the_set_time_scan_refuses_the_same_values(expr, words) -> None:
    with pytest.raises(ValueError) as ei:
        scan_tokens(expr)
    for word in words:
        assert word in str(ei.value), (expr, str(ei.value))


@pytest.mark.parametrize(("expr", "expected"), [
    ("{{ {a.b} }}", "{ x_x }"),                  # the spec's own example
    ("{{", "{"),
    ("}}", "}"),
    ("}}{{", "}{"),
    ("{{a.b}}", "{a.b}"),
    ("{a.b}}}", "x_x}"),
    ("a\\\\b", "a\\b"),
    ("\\\\", "\\"),
    ("\\~x", "~x"),
    ("a/\\~", "a/~"),
    ("tail\\", "tail\\"),                       # a trailing lone backslash stays literal
    ("\\", "\\"),
    ("\\@a.b", "@a.b"),                         # \@ and \$ escape until B1b
    ("\\$TERM", "$TERM"),
    ("\\@{a.b}", "@x_x"),                       # \@ escapes the @ only; {a.b} is a reference
])
def test_the_expander_reads(expr, expected) -> None:
    assert _host(expr) == expected


@pytest.mark.parametrize(("expr", "residue"), [
    ("{{x}}", "{{x}}"),
    ("\\\\x", "\\\\x"),
    ("\\~x", "\\~x"),
    ("\\$X", "\\$X"),
    ("\\@x", "@x"),
    ("x\\", "x\\"),
])
def test_deferral_carries_what_the_box_pass_reads(expr, residue) -> None:
    out = _host(expr, defer=True)
    assert out == residue
    # ...and the box pass reads that residue to the same text the host pass would.
    box = expand_expr(out.replace("@", "\\@"), space="guest", ctx=_ctx(), lookup=_lookup)
    assert box == _host(expr)


def test_a_leading_escaped_tilde_is_no_home() -> None:
    assert _host("\\~/x") == "~/x"
    assert _host("~/x") == "/h/x"


@pytest.mark.parametrize(("value", "refs", "variables"), [
    ("{{ {a.b} }}/{$TERM}", ["a.b"], ["TERM"]),
    ("\\@{a.b}", ["a.b"], []),
    ("\\@c.d/\\$X/tail\\", [], []),
])
def test_the_scan_reads_the_expanders_tokens(value, refs, variables) -> None:
    assert scan_tokens(value) == (refs, variables)


@pytest.mark.parametrize("value", ["a{b", "a}b", "{}", "{bad name}", "\\q"])
def test_config_set_names_the_key(value) -> None:
    verdict = validate_config_set("box.image", value, resolves=lambda _k, _v: None)
    assert isinstance(verdict, Error)
    assert verdict.message.startswith("'box.image': malformed value"), verdict.message


@pytest.mark.parametrize("value", ["a{b", "a}b", "{}", "{bad name}", "\\q"])
def test_the_launch_expansion_names_the_entry(value) -> None:
    with pytest.raises(SettingsError) as ei:
        expand(KeyStore({"box": {"image": value, "shell": "/x"}}), _ctx())
    assert str(ei.value).startswith("'box.image': "), str(ei.value)


@pytest.mark.parametrize("value", ["a{b", "a}b", "{}", "{bad name}", "\\q"])
def test_the_lenient_expansion_records_the_entry(value) -> None:
    expanded, errors = expand(
        KeyStore({"box": {"image": value, "shell": "/x"}}), _ctx(), collect_errors=True,
    )
    assert list(errors) == ["box.image"]
    assert expanded["box"]["shell"] == "/x"


@pytest.mark.parametrize("referrer_first", [True, False])
def test_the_key_holding_the_bad_value_is_named_once(referrer_first) -> None:
    # Named at the key that HOLDS the bad value, whatever the file order, and once.
    items = [("image", "{box.shell}"), ("shell", "a{b")]
    store = KeyStore({"box": dict(items if referrer_first else items[::-1])})
    with pytest.raises(SettingsError) as ei:
        expand(store, _ctx())
    message = str(ei.value)
    assert message.startswith("'box.shell': Unpaired '{'"), message
    assert "'box.image'" not in message
    if referrer_first:
        assert message.endswith(" (reached from box.image)."), message


def test_an_escaped_slash_is_no_absolute_path() -> None:
    assert is_self_resolving("\\/foo") is False
    assert is_unambiguous_path_value("\\/foo") is False
