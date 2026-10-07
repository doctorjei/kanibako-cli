"""Every name-, path- and ref-operand of a printed cure is a shell word.

The retained-box pair's quoting is pinned in :mod:`tests.test_cure_quoting`;
this covers the REST of the family: the ``proj.name`` cures in ``start`` and
``box info``, the ``box set --box`` shaping cure, the reserved-workset
``rm``/``mv``/``cd`` steps, the ``unrenderable_box_name_refusal`` convert and
move cures, the ``box convert``/``box move`` refusals in ``_lifecycle``, and
the two ``rig prep`` cures.

These operands are reachable with shell metacharacters in them.  A box
created from a PATH is registered under that path's basename VERBATIM —
``kanibako box create 'q$(touch pwned)'`` exits 0 and leaves a primary box
named ``q$(touch pwned)`` — and ``rig add --name`` stores whatever it was
handed, with no name validation at all.  So the metacharacter half is pinned
as an EXECUTION: each printed command is pasted into a real ``/bin/sh`` whose
leading program is a stub recording the argv the SHELL built, and the
assertion is on that argv plus the marker file the injected command would
leave.  A string match cannot say whether the second command ran.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import shlex
import subprocess
from pathlib import Path

import pytest

# ⚑ HOSTILE OPERANDS A REAL ``create``/``rig add`` PRODUCES.  ``q;>pwned`` needs
# the ``;``; the ``$( )`` form needs no separator, so the two fail differently.
_HOSTILE = ["q;>pwned", "q$(touch pwned)"]
_MARKER = "pwned"
_SAFE = "plainbox"


def _paste(command: str, scratch: Path, stub: str = "kanibako") -> "tuple[list[str], Path, Path, str]":
    """Paste *command*; return the argv it built, the cwd, the stub's own cwd, the HOME.

    *stub* shadows the pasted command's own leading program, so what is under
    test is the ARGV THE SHELL BUILT and not what kanibako then does with it.
    The stub's CWD is recorded because ``cd`` is a shell BUILTIN: a step that
    chains ``cd <path> && kanibako …`` runs no program for the ``cd``, so the
    directory the stub finds itself in is the only evidence that the path
    reached the shell as ONE word.  The child environment is passed whole, so it
    cannot inherit this process's HOME, and the recorded value says whether it
    did.  Both the paste cwd and every path the stub writes are under *scratch*,
    so a paste that DID inject leaves its marker where this test can see it and
    nowhere else.
    """
    stub_dir = scratch / "stubbin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    argv_dump = scratch / "argv.dump"
    home_dump = scratch / "home.dump"
    cwd_dump = scratch / "cwd.dump"
    argv_dump.write_bytes(b"")
    program = stub_dir / stub
    program.write_text(
        "#!/bin/sh\n"
        "for a in \"$@\"; do printf '%s\\0' \"$a\"; done > " + shlex.quote(str(argv_dump)) + "\n"
        "printf '%s' \"$PWD\" > " + shlex.quote(str(cwd_dump)) + "\n"
        "printf '%s' \"$HOME\" > " + shlex.quote(str(home_dump)) + "\n"
    )
    program.chmod(0o755)

    paste_cwd = scratch / "pastecwd"
    paste_cwd.mkdir(parents=True, exist_ok=True)
    child_home = scratch / "childhome"
    child_home.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=str(paste_cwd),
        env={"PATH": f"{stub_dir}:/usr/bin:/bin", "HOME": str(child_home)},
        capture_output=True,
        text=True,
        check=False,
    )
    stub_cwd = cwd_dump.read_text() if cwd_dump.exists() else ""
    return (
        [a for a in argv_dump.read_bytes().decode().split("\0") if a],
        paste_cwd,
        Path(stub_cwd),
        home_dump.read_text(),
    )


def _assert_inert(command: str, expected_argv: "list[str]", scratch: Path,
                  stub_cwd: Path | None = None) -> None:
    """Pasting *command* runs nothing of its own and reaches *expected_argv*.

    Asserted on the argv the shell built, so an operand that split into two
    words cannot pass by the interesting halves happening to exist.  *stub_cwd*,
    when given, is the directory the command is required to leave the shell in.
    """
    argv, paste_cwd, where, child_home = _paste(command, scratch)

    assert not (paste_cwd / _MARKER).exists(), f"the pasted cure ran injected text: {command}"
    assert argv == expected_argv, f"the shell built {argv} from {command}"
    if stub_cwd is not None:
        assert where == stub_cwd, f"the shell is in {where}, not {stub_cwd}, after {command}"
    assert child_home == str(scratch / "childhome")


def _run(argv: "list[str]") -> int:
    """Run *argv* through the SHIPPED parser and handler; return its rc."""
    from kanibako.cli import build_parser

    parsed = build_parser().parse_args(argv)
    return parsed.func(parsed)


def _printed(argv: "list[str]") -> str:
    """Run *argv* through the shipped CLI, returning everything it printed."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = _run(argv)
    assert rc == 0, f"{argv} refused the operand (rc={rc})"
    return out.getvalue()


def _line(message: str, needle: str) -> str:
    """The one printed line carrying *needle*."""
    return next(ln.strip() for ln in message.splitlines() if needle in ln)


_REPO_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> "dict[str, str]":
    """A child environment whose HOME and every XDG root is under *tmp_path*.

    ``kanibako box create`` roots at the CWD rather than at ``HOME``, so the CLI
    is run with ``cwd`` under *tmp_path* as well; both are asserted by the
    ``_paste`` helper for the shell it starts.
    """
    import os

    dirs = {
        key: tmp_path / key.lower()
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                    "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR")
    }
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)

    child = os.environ.copy()
    child.update({key: str(value) for key, value in dirs.items()})
    child["PYTHONPATH"] = str(_REPO_SRC)
    for key, value in dirs.items():
        monkeypatch.setenv(key, str(value))

    from kanibako.settings.config import write_global_config
    from tests.support.filenames import CONFIG_FILENAME

    write_global_config(dirs["XDG_CONFIG_HOME"] / CONFIG_FILENAME)
    return child


def _cli(env: "dict[str, str]", *args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the real CLI in a subprocess against the isolated *env*."""
    import sys

    return subprocess.run(
        [sys.executable, "-m", "kanibako", *args],
        env=env, cwd=str(cwd), capture_output=True, text=True, timeout=300, check=False,
    )


# ---------------------------------------------------------------------------
# Doors — the shipped CLI and the real handlers, driven as a user drives them.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", _HOSTILE)
class TestTheRigPrepCureIsQuoted:
    """``rig add`` prints a ``rig prep`` cure and stores the name VERBATIM.

    ``rig add`` has no name validation at all — the registry takes whatever
    ``--name`` was handed — so this operand is reachable with any metacharacter.
    """

    def test_the_printed_rig_cure_pastes_to_one_argv(self, name, tmp_home, config_file, tmp_path):
        printed = _printed(["rig", "add", "--name", name, "docker.io/library/alpine:3.19"])

        assert f"kanibako rig prep {shlex.quote(name)}" in printed
        # The message wraps the cure in prose single quotes, which are not part
        # of what a user copies, so the OPERAND is what lies inside them.
        operand = re.search(r"kanibako rig prep (.+?)' to pull it", printed).group(1)
        _assert_inert(f"kanibako rig prep {operand}", ["rig", "prep", name],
                      tmp_path / "paste")


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheBoxSetShapingCureIsQuoted:
    """The interrupted-create message cures the shaping flags with ``box set --box``."""

    def test_the_printed_box_set_cure_pastes_to_one_argv(self, name, tmp_path):
        """``_create_recovery_refusal`` is the real door, given the journal's record."""
        from kanibako.commands.box._parser import _create_recovery_refusal
        from kanibako.settings.paths import BoxMode

        root = Path(tmp_path) / name

        class Probe:
            """The non-materializing probe the door reads its designation off."""

            mode = BoxMode.primary
            name = root.name
            project_path = root
            metadata_path = root
            shell_path = root / "home"
            metadata = root / "metadata"

        args = argparse.Namespace(recover=False, name=None, standalone=None, no_vault=True)
        message = _create_recovery_refusal(
            args, None, Probe(), already=False, pending={"name": name},
        )
        assert message is not None, "the door owed no refusal for a pending create"

        line = _line(message, "box set --box")
        _assert_inert(
            line, ["box", "set", "--box", name, "box.enable_vault=false"], tmp_path / "paste",
        )


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheUnrenderableRefusalCuresAreQuoted:
    """A box whose name renders none is cured by its PATH, never by the name.

    ⚑ ``<new-path>``/``<new-name>`` are the message's own literal placeholders —
    the user fills them in — and ``<`` is a REDIRECT, so a paste substitutes them
    for a plain word first; what is under test is the PATH operand beside them.
    """

    def test_the_convert_cure_pastes_to_one_argv(self, name, tmp_path):
        from kanibako.utils import unrenderable_box_name_refusal

        path = Path("/w") / name
        text = unrenderable_box_name_refusal("-droste", "standalone", path)

        assert f"kanibako box convert {shlex.quote(str(path))}" in text
        cure = _line(text, "kanibako box convert").replace("<new-name>", "mynew")
        _assert_inert(
            cure,
            ["box", "convert", str(path), "--standalone", "--name", "mynew"],
            tmp_path / "paste",
        )

    def test_the_move_cure_pastes_to_one_argv(self, name, tmp_path):
        from kanibako.utils import unrenderable_box_name_refusal

        path = Path("/w") / name
        text = unrenderable_box_name_refusal("-droste", "primary", path)

        cure = (
            _line(text, "kanibako box move")
            .replace("<new-path>", "/w/moved")
            .replace("<new-name>", "mynew")
        )
        _assert_inert(
            cure,
            ["box", "move", str(path), "/w/moved", "--name", "mynew"],
            tmp_path / "paste",
        )


@pytest.mark.parametrize("name", _HOSTILE)
class TestTheReservedWorksetStepsAreQuoted:
    """The reserved-workset refusal prints ``rm``/``mv``/``cd`` steps to run.

    ``primary`` is reserved and NOT one of the two exempt identifiers, so it
    reaches the refusal.  The HOSTILE operand is the workset ROOT, which the
    ``mv`` and ``cd`` steps name and which is a PATH, so it carries a
    metacharacter with no validator in the way.  Driven through the REAL CLI in
    a subprocess against an isolated HOME, the way the refusal is reached.
    """

    def test_every_printed_step_pastes_to_one_argv_each(self, name, tmp_path, cli_env):
        from kanibako.project.names import read_names, register_name, unregister_name
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import load_std_paths

        home = Path(cli_env["HOME"])
        root = home / "ws" / name
        root.parent.mkdir(parents=True, exist_ok=True)
        made = _cli(cli_env, "workset", "create", str(root), "--name", "staging",
                    cwd=home)
        assert made.returncode == 0, made.stderr
        member = root.parent / "member"
        member.mkdir()
        connected = _cli(cli_env, "workset", "connect", "staging", str(member),
                         "--name", "ext", cwd=home)
        assert connected.returncode == 0, connected.stderr

        registry = load_std_paths(load_config(user_config_file())).registry
        unregister_name(registry, "staging", section="worksets")
        register_name(registry, "primary", str(root), section="worksets")
        assert "primary" in dict(read_names(registry)["worksets"])

        refused = _cli(cli_env, "box", "info", cwd=member)
        assert refused.returncode == 1, refused.stdout
        assert "Working set 'primary' is registered under a reserved name" in refused.stderr

        steps = [
            ln.strip() for ln in refused.stderr.splitlines()
            if ln.strip().startswith(("mv ", "cd "))
        ]
        assert steps, refused.stderr
        for index, step in enumerate(steps):
            verb, operand, *rest = shlex.split(step)
            if verb == "cd":
                # ``cd`` is a builtin, so the stub records the CHAINED ``kanibako``
                # — ``rest`` is ``['&&', 'kanibako', <its args>]`` — and the stub's
                # own cwd is what proves the path arrived as one word.
                assert rest[:2] == ["&&", "kanibako"], step
                _assert_inert(step, rest[2:], tmp_path / f"paste-{index}",
                              stub_cwd=Path(operand))
            else:
                _assert_inert(step, [verb, operand, *rest], tmp_path / f"paste-{index}")


def test_a_plain_operand_prints_the_same_bytes_it_did():
    """Quoting is a no-op for an operand that needs none."""
    from kanibako.utils import unrenderable_box_name_refusal

    text = unrenderable_box_name_refusal("-droste", "primary", Path("/home/u/plainbox"))
    assert "kanibako box move /home/u/plainbox <new-path> --name <new-name>" in text