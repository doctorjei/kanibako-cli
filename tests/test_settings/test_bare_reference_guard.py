"""``@`` and ``$`` are ordinary characters, under a one-release guard (keyspec § 0 "References").

A bare ``@a.b.c`` naming a declared key, a bare ``$NAME`` naming a known variable,
``@{a.b.c}``, ``${NAME}``, and a bare run whose leading dotted part is a declared key are
refused by name, and the cure names the braced spelling. Any other ``@`` or ``$`` is text.
``\\@`` and ``\\$`` are no longer escapes. The guard reads text a user wrote; data the code
feeds the expander (``literal_expr``) resolves as written. Braced-refs step 5, B1b.
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
from kanibako.settings.agent_config import is_self_resolving, is_unambiguous_path_value
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import BoxMode
from kanibako.settings.settings_launch import _persona_partial, resolve_box_dest
from kanibako.settings.settings_resolve import (
  ResolveCtx,
  SettingsError,
  expand_expr,
  literal_expr,
)
from kanibako.settings.workset_dirkeys import resolve_workset_dir_key
from tests.support.early import early_record

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _ctx() -> ResolveCtx:
  return ResolveCtx(
    agent_name="claude",
    workset_name="ws",
    host_home="/h",
    xdg={"XDG_DATA_HOME": "/h/.local/share"},
    term="xterm",
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
  "@box",  # a namespace, not a key
  "$FOO/x",
  "$HOME/bin",
  "$TERMINAL",  # the name is TERMINAL, which is not a known variable
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
  assert f"Illegal escape '{expr[expr.index(chr(92)) :][:2]}'" in str(ei.value)
  assert "only \\\\ and \\~ escape" in str(ei.value), str(ei.value)
  char = expr[expr.index(chr(92)) + 1]
  assert f"write '\\{char}' as '{char}'" in str(ei.value), str(ei.value)
  with pytest.raises(ValueError):
    scan_tokens(expr)


# BARE-REF-GUARD: an escaped OLD spelling gets no cure that walks into the guard.
@pytest.mark.parametrize(
  ("expr", "old", "cure", "noun"),
  [
    ("\\@meta.box.name", "@meta.box.name", "{meta.box.name}", "key"),
    ("x/\\@meta.box.name.jsonl", "@meta.box.name.jsonl", "{meta.box.name}.jsonl", "key"),
    ("\\$TERM/y", "$TERM", "{$TERM}", "variable"),
  ],
)
def test_an_escaped_old_spelling_is_not_cured_into_the_guard(expr, old, cure, noun) -> None:
  with pytest.raises(SettingsError) as ei:
    _host(expr)
  message = str(ei.value)
  assert (
    f"'{old}' is refused for this release as an old reference spelling: write '{cure}' "
    f"for the {noun}'s value; the literal text has no spelling until the guard is removed."
  ) in message, message
  assert "is an ordinary character" not in message, message


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
  expr,
  old,
  cure,
  defer,
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


@pytest.mark.parametrize(
  "value", ["@box.shell", "x/@box.shell", "$TERM", "${TERM}", "@{box.shell}", "@meta.box.name.log"]
)
def test_config_set_names_the_key(value) -> None:
  verdict = validate_config_set("box.image", value, resolves=lambda _k, _v: None)
  assert isinstance(verdict, Error)
  assert verdict.message.startswith("'box.image': malformed value"), verdict.message


@pytest.mark.parametrize(
  "value", ["@box.shell", "x/@box.shell", "$TERM", "${TERM}", "@{box.shell}"]
)
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
      [sys.executable, "-m", "kanibako", *args],
      env=env,
      cwd=home,
      capture_output=True,
      text=True,
      timeout=300,
      check=False,
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


@pytest.mark.parametrize(
  ("stored", "entry"),
  [
    (_STORED, "'system.env.E'"),
    (
      'system:\n  bindings:\n    ro:\n      /x/.y: ["/a/@system.agent"]\n',
      "'system.bindings.ro[/x/.y]'",
    ),
  ],
)
def test_a_stored_old_spelling_is_named_at_load(cli, stored, entry) -> None:
  path = _system_file(cli)
  path.write_text(stored)
  proc = cli("system", "show", "--effective")
  assert proc.returncode == 1, proc.stdout + proc.stderr
  assert entry in proc.stderr and "write '{system.agent}'" in proc.stderr, proc.stderr
  assert f"(in settings file {path})" in proc.stderr, proc.stderr


@pytest.mark.parametrize(
  ("rel", "text", "key"),
  [
    (
      ".local/share/kanibako/global/settings.yaml",
      'system:\n  canon: "/x/$XDG_DATA_HOME"\n',
      "system.canon",
    ),
    (".config/kanibako.cfg", 'config:\n  data: "$XDG_DATA_HOME/kb"\n', "config.data"),
  ],
)
def test_a_stored_old_spelling_in_the_path_tier_names_key_and_file(cli, rel, text, key) -> None:
  path = cli.home / rel
  path.write_text(text)
  proc = cli("system", "show", "--effective")
  assert proc.returncode == 1, proc.stdout + proc.stderr
  assert f"'{key}': Old reference spelling" in proc.stderr, proc.stderr
  assert "write '{$XDG_DATA_HOME}'" in proc.stderr and f"(in {path})" in proc.stderr, proc.stderr


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


# The guard reads text a USER wrote. A value the code builds from data -- a host path, a
# persona value, a probed source -- enters as ``literal_expr`` and resolves as written:
# never newly refuse a value that resolves today.
_DATA = [
  "/tmp/x/q@box.image",
  "/p/p$TERM",
  "$XDG_CONFIG_HOME/claude",
  "@{box.image}",
  "x${TERM}y",
  "/l/@meta.box.name.jsonl",
]


@pytest.mark.parametrize("text", _DATA)
def test_code_built_data_bypasses_the_guard(text) -> None:
  assert _host(literal_expr(text)) == text
  assert _host(str(literal_expr(text))) == text  # str() keeps the data mark


@pytest.mark.parametrize("text", _DATA)
def test_deferred_data_survives_the_box_pass(text) -> None:
  assert resolve_box_dest(_host(literal_expr(text), defer=True), _ctx()) == text


def test_a_spliced_referent_is_data_and_a_deferred_variable_still_expands() -> None:
  deferred = expand_expr(
    "/d/{$TERM}/{box.image}",
    space="host",
    ctx=_ctx(),
    defer_env=True,
    lookup=lambda ref, chain: literal_expr({"box.image": "q@box.image$TERM"}[ref]),
  )
  assert resolve_box_dest(deferred, _ctx()) == "/d/xterm/q@box.image$TERM"


@pytest.mark.parametrize("text", _DATA)
def test_the_same_text_typed_by_a_user_is_refused(text) -> None:
  with pytest.raises(SettingsError, match="Old reference spelling"):
    _host(text)


def test_a_persona_value_is_delivered_as_written() -> None:
  value = "$XDG_CONFIG_HOME/claude"
  out = expand(_persona_partial("claude", {"env.FOO": value, "model": value}), _ctx())
  assert dict(out["agent"]["claude"]["env"]) == {"FOO": value}
  assert out["agent"]["claude"]["model"] == value


def test_a_data_value_dumps_as_a_plain_string(tmp_path) -> None:
  path = tmp_path / "s.yaml"
  dump_doc(path, {"k": literal_expr("/x/@box.image")})
  assert load_doc(path) == {"k": "/x/@box.image"}


# A leading ``@`` or ``$`` is no sigil: ``@foo/x`` and ``$HOME/x`` are bare relative paths.
# A leading OLD spelling passes the predicates so the expander names its braced cure.
@pytest.mark.parametrize(
  ("value", "verdict"),
  [
    ("@foo/x", False),
    ("$HOME/x", False),
    ("$5", False),
    ("@", False),
    ("@box.image/x", True),
    ("$XDG_DATA_HOME/x", True),
    ("~/x", True),
    ("/x", True),
    ("{meta.box.path}/x", True),
  ],
)
def test_the_path_predicates_read_a_leading_at_and_dollar_as_text(value, verdict) -> None:
  assert is_self_resolving(value) is verdict
  assert is_unambiguous_path_value(value) is verdict


def test_a_workset_dir_key_at_a_leading_at_is_refused_as_bare_relative(tmp_path) -> None:
  early = early_record(tmp_path, mode=BoxMode.primary)
  with pytest.raises(SettingsError, match="BARE RELATIVE"):
    resolve_workset_dir_key(Path("/tmp/ws"), "@foo/logs", "logs", key="logs", early=early)


def test_create_takes_a_path_holding_an_old_spelling(cli, tmp_path) -> None:
  for name, leaf in (("qat", "q@box.image"), ("pat", "p$TERM")):
    project = tmp_path / leaf
    project.mkdir()
    proc = cli("create", str(project), "--name", name)
    assert proc.returncode == 0, proc.stderr
    info = cli("box", "info", name)
    assert info.returncode == 0, info.stderr


@pytest.mark.parametrize(
  "args",
  [
    ("box", "set", "qat", "box.canon=@foo/x"),
    ("box", "set", "qat", "box.canon=$HOME/x"),
    ("workset", "set", "default", "workset.logs=@foo/logs/{meta.workset.path}"),
  ],
)
def test_a_leading_at_or_dollar_path_is_refused_at_set(cli, tmp_path, args) -> None:
  project = tmp_path / "proj"
  project.mkdir()
  assert cli("create", str(project), "--name", "qat").returncode == 0
  proc = cli(*args)
  assert proc.returncode == 1, proc.stdout + proc.stderr
  assert "BARE RELATIVE" in proc.stderr, proc.stderr


@pytest.mark.parametrize(
  ("value", "cure"),
  [
    ("box.canon=$XDG_DATA_HOME/x", "write '{$XDG_DATA_HOME}'"),
    ("box.canon=@box.image/x", "write '{box.image}'"),
  ],
)
def test_a_leading_old_spelling_path_names_its_braced_cure(cli, tmp_path, value, cure) -> None:
  project = tmp_path / "proj"
  project.mkdir()
  assert cli("create", str(project), "--name", "qat").returncode == 0
  proc = cli("box", "set", "qat", value)
  assert proc.returncode == 1, proc.stdout + proc.stderr
  assert cure in proc.stderr, proc.stderr


@pytest.mark.parametrize(
  ("value", "cure"),
  [
    ("system.env.E=@box.image", "write '{box.image}'"),
    ("system.env.E=$TERM", "write '{$TERM}'"),
  ],
)
def test_a_typed_old_spelling_is_still_refused(cli, value, cure) -> None:
  proc = cli("system", "set", value)
  assert proc.returncode == 1, proc.stderr
  assert cure in proc.stderr, proc.stderr
