"""The shipped ``check-comms.sh`` PostToolUse hook — what it prints, and for whom.

The hook's stdout JSON is its only voice. ``systemMessage`` is shown to the user and
never reaches the model; ``hookSpecificOutput.additionalContext`` is what puts the alert
in the model's context. So one alert must carry both, with the same text, from the
``jq`` path and from the hand-escaped fallback alike.

Mail arrives by rename: a sender writes ``.name.tmp`` and moves it into place, so a
dot-file in the inbox is a message still being written and must not be announced.

Each test runs the real script with ``bash`` under a throwaway ``HOME`` and a unique
``KANIBAKO_NAME``, so its ``/tmp/kanibako-comms-<name>`` state dir starts fresh.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src/kanibako/data/templates/system/canon/handbook/general/scripts/behavior"
    / "check-comms.sh"
)

# Every external command the script runs (plus ``sed``, which lifts ``emit`` out),
# minus ``jq`` — the no-jq PATH gets these.
_TOOLS = (
    "bash", "cat", "mkdir", "touch", "find", "head", "wc",
    "sort", "md5sum", "cut", "sed",
)

# EVERY code point JSON forbids raw inside a string, 0x01-0x1F, as ONE list: the
# parametrized tests below sweep it instead of pasting 31 near-identical tests out.
# A mail file name can hold any of them, and the no-jq branch builds its JSON by
# hand, so each one has to survive the escaping AND still parse.  0x00 is absent
# because a bash string cannot hold NUL — see the comment in ``emit``.
_CONTROL_CHARS = [chr(c) for c in range(0x01, 0x20)]


@pytest.fixture
def box(tmp_path):
    """A throwaway HOME with ``~/channels/inbox/``, and a fresh state dir name."""
    home = tmp_path / "home"
    inbox = home / "channels" / "inbox"
    inbox.mkdir(parents=True)
    name = f"pytest-check-comms-{uuid.uuid4().hex}"
    yield home, inbox, name
    shutil.rmtree(f"/tmp/kanibako-comms-{name}", ignore_errors=True)


def _run(home: Path, name: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "KANIBAKO_NAME": name,
           "PATH": path if path is not None else os.environ["PATH"]}
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(SCRIPT)],
        input="{}", capture_output=True, text=True, env=env, timeout=30,
    )


def _no_jq_path(tmp_path: Path) -> str:
    shim = tmp_path / "bin-no-jq"
    shim.mkdir()
    for tool in _TOOLS:
        real = shutil.which(tool)
        assert real, f"{tool} is not installed"
        (shim / tool).symlink_to(real)
    return str(shim)


def _run_lifted_emit(msg: str, path: str) -> subprocess.CompletedProcess:
    """``emit`` lifted out of the script and called on an exact message.

    ``path`` decides which branch runs: the no-jq shim reaches the hand-escaped
    fallback, the inherited PATH reaches ``jq``.
    """
    snippet = (
        f"eval \"$(sed -n '/^emit() {{/,/^}}/p' '{SCRIPT}')\"\n"
        'emit "$1"\n'
    )
    return subprocess.run(["bash", "-c", snippet, "bash", msg], env={"PATH": path},
                          capture_output=True, text=True, timeout=30)


def _assert_one_alert(stdout: str, expected_in_msg: str) -> str:
    out = json.loads(stdout)  # raises on two objects or malformed JSON
    msg = out["systemMessage"]
    assert out["hookSpecificOutput"] == {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }
    assert out["continue"] is True
    assert expected_in_msg in msg
    return msg


def test_new_mail_reaches_user_and_model(box):
    home, inbox, name = box
    (inbox / "from-alice.md").write_text("hi\n")
    result = _run(home, name)
    assert result.returncode == 0, result.stderr
    msg = _assert_one_alert(result.stdout, "from-alice.md")
    assert msg.startswith("NEW MAIL (1):")


def test_dot_file_still_being_written_is_not_announced(box):
    home, inbox, name = box
    (inbox / ".from-x.md.tmp").write_text("half a mess")
    result = _run(home, name)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_fallback_without_jq_emits_the_same_valid_json(box, tmp_path):
    """The whole script, run with no ``jq`` reachable, emits the same JSON shape."""
    home, inbox, name = box
    path = _no_jq_path(tmp_path)
    probe = subprocess.run(["bash", "-c", "command -v jq"], env={"PATH": path},
                           capture_output=True)
    assert probe.returncode != 0, "jq is still reachable on the shim PATH"
    (inbox / "from-bob.md").write_text("hi\n")
    result = _run(home, name, path=path)
    assert result.returncode == 0, result.stderr
    msg = _assert_one_alert(result.stdout, "from-bob.md")
    assert msg == "NEW MAIL (1): from-bob.md"


def test_fallback_escapes_quote_and_trailing_backslash(tmp_path):
    """``emit``'s hand escaping, fed a quote and a trailing backslash directly.

    The function is lifted out of the script and called with ``jq`` hidden, so the
    fallback's escaping is checked on an exact message, apart from the mail scan.
    """
    path = _no_jq_path(tmp_path)
    msg = 'say "hi" \\'
    snippet = (
        f"eval \"$(sed -n '/^emit() {{/,/^}}/p' '{SCRIPT}')\"\n"
        'emit "$1"\n'
    )
    result = subprocess.run(["bash", "-c", snippet, "bash", msg], env={"PATH": path},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert msg.endswith("\\") and '"' in msg
    assert _assert_one_alert(result.stdout, msg) == msg


def test_awkward_file_names_are_reported_verbatim(box):
    """A quote, a trailing backslash, and a space survive into both keys unchanged.

    Names come straight from ``find -printf '%f'``; nothing between ``find`` and
    ``emit`` may reinterpret quotes or backslashes, as ``xargs`` did.
    """
    home, inbox, name = box
    names = ['from-"x"\\', "from a b.md"]
    for n in names:
        (inbox / n).write_text("hi\n")
    result = _run(home, name)
    assert result.returncode == 0, result.stderr
    # The script runs with no LANG/LC_* set, so `sort` orders bytes, as sorted() does.
    expected = f"NEW MAIL (2): {', '.join(sorted(names))}"
    assert _assert_one_alert(result.stdout, names[0]) == expected


def test_fallback_keeps_json_valid_for_a_tab_in_a_name(box, tmp_path):
    """With ``jq`` hidden, a tab in a mail file name still yields JSON that parses."""
    home, inbox, name = box
    filename = "t\tab.md"
    (inbox / filename).write_text("hi\n")
    result = _run(home, name, path=_no_jq_path(tmp_path))
    assert result.returncode == 0, result.stderr
    assert _assert_one_alert(result.stdout, filename) == f"NEW MAIL (1): {filename}"


def test_fallback_escapes_tab_carriage_return_and_newline(tmp_path):
    """``emit``'s hand escaping turns raw tab, CR, and LF into JSON escapes."""
    msg = "a\tb\rc\nd \\"
    snippet = (
        f"eval \"$(sed -n '/^emit() {{/,/^}}/p' '{SCRIPT}')\"\n"
        'emit "$1"\n'
    )
    result = subprocess.run(["bash", "-c", snippet, "bash", msg],
                            env={"PATH": _no_jq_path(tmp_path)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert _assert_one_alert(result.stdout, msg) == msg


@pytest.mark.parametrize("use_jq", [False, True], ids=["no-jq", "jq"])
@pytest.mark.parametrize("ch", _CONTROL_CHARS, ids=lambda c: f"U+{ord(c):04X}")
def test_every_json_control_character_survives_both_branches(tmp_path, ch, use_jq):
    """All 0x01-0x1F, on BOTH branches: valid JSON, and both fields unchanged.

    ``emit``'s comment used to claim it escaped "the raw control characters JSON
    forbids inside a string" while handling only tab, CR and LF — so ESC, BEL and
    the other 28 produced a stdout line that no JSON parser would accept, and the
    hook's only voice to both readers was lost.  The message carries a backslash and
    a quote too, because escaping backslash FIRST is what keeps the other escapes
    from being re-escaped.
    """
    if use_jq and shutil.which("jq") is None:
        pytest.skip("jq is not installed; the jq branch cannot be exercised here")
    path = os.environ["PATH"] if use_jq else _no_jq_path(tmp_path)
    # Prove which branch this case actually ran, instead of trusting the PATH.
    probe = subprocess.run(["bash", "-c", "command -v jq"], env={"PATH": path},
                           capture_output=True, text=True)
    assert (probe.returncode == 0) is use_jq, "the branch under test did not run"
    msg = f"a{ch}b \\ \"c\""
    result = _run_lifted_emit(msg, path)
    assert result.returncode == 0, result.stderr
    assert _assert_one_alert(result.stdout, msg) == msg


@pytest.mark.parametrize("use_jq", [False, True], ids=["no-jq", "jq"])
def test_control_character_in_a_mail_name_yields_parseable_json(box, tmp_path, use_jq):
    """The whole script, an ESC in a mail file name, on both branches.

    ``find -printf '%f'`` hands the name to ``emit`` unaltered, so the hook's alert
    is exactly where an unescaped ESC would land.  Same name and same expectation
    on the ``jq`` path, which is what proves the fallback fix did not regress it.
    """
    if use_jq and shutil.which("jq") is None:
        pytest.skip("jq is not installed; the jq branch cannot be exercised here")
    home, inbox, name = box
    path = os.environ["PATH"] if use_jq else _no_jq_path(tmp_path)
    filename = "e\x1b[31m-alert.md"
    (inbox / filename).write_text("hi\n")
    result = _run(home, name, path=path)
    assert result.returncode == 0, result.stderr
    assert _assert_one_alert(result.stdout, filename) == f"NEW MAIL (1): {filename}"


def test_del_is_left_alone_because_json_allows_it(tmp_path):
    """DEL (0x7F) is legal raw in a JSON string, so it is not escaped.

    A guard against over-escaping: the sweep is for 0x01-0x1F, and widening it
    would make the alert text differ from the mail name for no reason.
    """
    msg = "a\x7fb"
    result = _run_lifted_emit(msg, _no_jq_path(tmp_path))
    assert result.returncode == 0, result.stderr
    assert _assert_one_alert(result.stdout, msg) == msg
    assert "\x7f" in result.stdout and "\\u007f" not in result.stdout
