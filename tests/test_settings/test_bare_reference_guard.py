"""``@`` and ``$`` are ordinary characters, under a one-release guard (keyspec § 0 "References").

A bare ``@a.b.c`` naming a declared key, a bare ``$NAME`` naming a known variable,
``@{a.b.c}``, ``${NAME}``, and a bare run whose leading dotted part is a declared key are
refused by name, and the cure names the braced spelling. Any other ``@`` or ``$`` is text.
``\\@`` and ``\\$`` are no longer escapes. Braced-refs step 5, B1b.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_configset import Error, scan_tokens, validate_config_set
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_resolve import (
    ResolveCtx,
    SettingsError,
    expand_expr,
    literal_expr,
)

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude", workset_name="ws", host_home="/h",
        xdg={"XDG_DATA_HOME": "/h/.local/share"}, term="xterm",
    )


def _lookup(ref: str, chain: tuple[str, ...]) -> str:
    return {"box.image": "img"}[ref]


def _host(expr: str, *, defer: bool = False) -> str:
    return expand_expr(expr, space="host", ctx=_ctx(), lookup=_lookup, defer_env=defer)


# Text that holds "@" or "$" but names no declared key and no known variable.
_ORDINARY = [
    "user@example.com",
    "https://user:key@host/v1",
    "cost $5",
    "x@y",
    "@foo.bar",
    "/srv/@foo.bar/x",
    "@box",                 # a namespace, not a key
    "$FOO/x",
    "$HOME/bin",
    "$TERMINAL",            # the name is TERMINAL, which is not a known variable
    "a$",
    "@",
    "$",
]


@pytest.mark.parametrize("text", _ORDINARY)
def test_at_and_dollar_are_ordinary_text(text) -> None:
    assert _host(text) == text
    assert scan_tokens(text) == ([], [])


@pytest.mark.parametrize("text", _ORDINARY)
def test_ordinary_text_survives_deferral_and_the_box_pass(text) -> None:
    residue = _host(text, defer=True)
    assert residue == text
    assert expand_expr(residue, space="guest", ctx=_ctx(), lookup=_lookup) == text


def test_a_dollar_before_a_brace_is_text_and_the_brace_opens_a_reference() -> None:
    assert _host("${box.image}") == "$img"
    assert _host("@{$TERM}") == "@xterm"
    assert scan_tokens("x@{$AGENT}") == ([], ["AGENT"])


@pytest.mark.parametrize("text", ["@foo.bar", "$FOO", "user@example.com", "cost $5"])
def test_a_whole_value_of_ordinary_text_is_delivered_as_written(text) -> None:
    expanded = expand(KeyStore({"box": {"image": text, "shell": "/x"}}), _ctx())
    assert expanded["box"]["image"] == text


@pytest.mark.parametrize("text", ["user@example.com", "cost $5", "@foo.bar", "$FOO"])
def test_config_set_accepts_ordinary_text(text) -> None:
    verdict = validate_config_set("box.image", text, resolves=lambda _k, _v: None)
    assert not isinstance(verdict, Error), verdict


@pytest.mark.parametrize("expr", ["\\@a.b", "\\$X", "x\\@", "\\$"])
def test_backslash_at_and_dollar_are_illegal_escapes(expr) -> None:
    with pytest.raises(SettingsError) as ei:
        _host(expr)
    assert f"Illegal escape '{expr[expr.index(chr(92)):][:2]}'" in str(ei.value)
    assert "only \\\\ and \\~ escape" in str(ei.value), str(ei.value)
    with pytest.raises(ValueError):
        scan_tokens(expr)


def test_literal_expr_escapes_only_backslash_and_tilde() -> None:
    text = "/p/@meta.box.name/$TERM/~/a\\b/{x}"
    assert literal_expr(text) == "/p/@meta.box.name/$TERM/\\~/a\\\\b/{{x}}"


# Each guard arm: the value, the old spelling the refusal names, and its braced cure.
_GUARDED = [
    ("@box.image", "@box.image", "{box.image}"),
    ("/x/@box.image/y", "@box.image", "{box.image}"),
    ("@meta.box.name", "@meta.box.name", "{meta.box.name}"),
    ("@{box.image}", "@{box.image}", "{box.image}"),
    ("a@{box.image}b", "@{box.image}", "{box.image}"),
    ("@meta.box.name.jsonl", "@meta.box.name.jsonl", "{meta.box.name}.jsonl"),
    ("/l/@meta.box.name.jsonl", "@meta.box.name.jsonl", "{meta.box.name}.jsonl"),
    ("$TERM", "$TERM", "{$TERM}"),
    ("a$AGENT/b", "$AGENT", "{$AGENT}"),
    ("$XDG_DATA_HOME/x", "$XDG_DATA_HOME", "{$XDG_DATA_HOME}"),
    ("${TERM}", "${TERM}", "{$TERM}"),
    ("x${COLORTERM}y", "${COLORTERM}", "{$COLORTERM}"),
]


@pytest.mark.parametrize("defer", [False, True])
@pytest.mark.parametrize(("expr", "old", "cure"), _GUARDED)
def test_the_guard_refuses_the_old_spelling_and_names_the_braced_one(
    expr, old, cure, defer,
) -> None:
    with pytest.raises(SettingsError) as ei:
        _host(expr, defer=defer)
    message = str(ei.value)
    assert f"'{old}'" in message and f"write '{cure}'" in message, message


@pytest.mark.parametrize(("expr", "old", "cure"), _GUARDED)
def test_the_set_time_scan_refuses_the_same_values(expr, old, cure) -> None:
    with pytest.raises(ValueError) as ei:
        scan_tokens(expr)
    assert f"'{old}'" in str(ei.value) and f"write '{cure}'" in str(ei.value)


@pytest.mark.parametrize("value", ["@box.shell", "x/@box.shell", "$TERM", "${TERM}",
                                   "@{box.shell}", "@meta.box.name.log"])
def test_config_set_names_the_key(value) -> None:
    verdict = validate_config_set("box.image", value, resolves=lambda _k, _v: None)
    assert isinstance(verdict, Error)
    assert verdict.message.startswith("'box.image': malformed value"), verdict.message


@pytest.mark.parametrize("value", ["@box.shell", "x/@box.shell", "$TERM", "${TERM}",
                                   "@{box.shell}"])
def test_the_launch_expansion_names_the_entry(value) -> None:
    with pytest.raises(SettingsError) as ei:
        expand(KeyStore({"box": {"image": value, "shell": "/x"}}), _ctx())
    message = str(ei.value)
    assert message.startswith("'box.image': "), message
    assert "write '{" in message, message


# Through the real CLI: a stored guarded value names its entry, and setting or resetting
# that key is the repair -- the guard never blocks its own fix.


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "KANIBAKO"))}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(tmp_path), PYTHONPATH=str(REPO_SRC))

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "kanibako", *args], env=env, cwd=home,
            capture_output=True, text=True, timeout=300, check=False,
        )

    run.home = home  # type: ignore[attr-defined]
    assert run("system", "get", "system.agent").returncode == 0  # bootstrap the store
    return run


def _system_file(cli) -> Path:
    path = cli.home / ".local/share/kanibako/global/settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_STORED = 'system:\n  agent: shell\n  env:\n    E: "/x/@system.agent"\n'


def test_set_refuses_the_old_spelling_naming_the_key(cli) -> None:
    proc = cli("system", "set", "system.env.E=@system.agent")
    assert proc.returncode == 1, proc.stderr
    assert "system.env.E" in proc.stderr and "write '{system.agent}'" in proc.stderr, proc.stderr


def test_a_stored_old_spelling_is_named_at_load(cli) -> None:
    _system_file(cli).write_text(_STORED)
    proc = cli("system", "show", "--effective")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "system.env.E" in proc.stderr and "write '{system.agent}'" in proc.stderr, proc.stderr


def test_setting_the_guarded_key_is_the_repair(cli) -> None:
    path = _system_file(cli)
    path.write_text(_STORED)
    proc = cli("system", "set", "system.env.E=/x/{system.agent}")
    assert proc.returncode == 0, proc.stderr
    assert "@system.agent" not in path.read_text()
    assert cli("system", "show", "--effective").returncode == 0


def test_resetting_the_guarded_key_is_the_repair(cli) -> None:
    path = _system_file(cli)
    path.write_text(_STORED)
    proc = cli("system", "reset", "system.env.E")
    assert proc.returncode == 0, proc.stderr
    assert "@system.agent" not in path.read_text()
    assert cli("system", "show", "--effective").returncode == 0
